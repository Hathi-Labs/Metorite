"""Thin OpenAI-compatible chat completions endpoint.

Routes /v1/chat/completions through the litellm SDK directly (no proxy).
The MAF OpenAIChatCompletionClient and Copilot SDK speak OpenAI API — this
route translates requests to our acb_llm module.

Serves both /v1/chat/completions (OpenAI standard) and /chat/completions (some SDKs).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from acb_auth import require_llm_api_auth
from acb_common import get_logger
from acb_llm.client import _ensure_keys_loaded
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

_log = get_logger("v1")

# Whether to honour a caller-supplied ``api_base``/``api_key`` in the request
# body (for Ollama / vLLM / self-hosted endpoints). OFF by default: an
# untrusted body could point the server at an arbitrary URL (SSRF/relay). No
# internal caller sends these in the body today — they configure the provider
# on their own client — so default-off is safe. Opt in per deployment.
_ALLOW_CALLER_ENDPOINT_OVERRIDE = os.environ.get(
    "V1_ALLOW_CALLER_ENDPOINT_OVERRIDE", "0"
).strip().lower() in ("1", "true", "yes", "on")

# ── Default output ceiling ────────────────────────────────────────────────
# This route is THE choke point every agent runtime POSTs through, so this
# default IS the real output cap for the whole platform whenever the caller
# omits max_tokens — and the Copilot SDK ALWAYS omits it: its wire protocol
# has no max_tokens field, so `COPILOT_MAX_OUTPUT_TOKENS` / the SessionConfig
# `max_tokens` never reach us. The old default of 4096 was therefore the
# effective ceiling for every Copilot agent, which truncated long tool-call
# arguments mid-JSON (the model emits a big file's content as a tool argument,
# gets cut at 4096, and the resulting malformed JSON fails the tool call).
#
# Measured on the live gateway (deepseek-v4-pro, 2026-07-17):
#   no max_tokens  → completion_tokens=4096,  finish_reason="length"  (cut off)
#   max_tokens=32k → completion_tokens=10940, finish_reason="stop"    (finished)
#
# Since max_tokens is a CEILING (the model stops when it's done, as the "stop"
# above shows), a generous default costs nothing and only removes an artificial
# cut. Verified accepted by the whole active fleet (deepseek-v4-pro /
# tier-powerful / tier-fast / auto all return 200 at 32000).
_DEFAULT_MAX_OUTPUT_TOKENS: int = int(
    os.environ.get("GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS", "32000")
)


def _clamp_max_tokens(requested: int, model: str) -> int:
    """Lower ``requested`` to ``model``'s real output cap, when one is known.

    The generous default above is what stops long tool-call arguments being
    truncated mid-JSON, but it cuts the other way too: a provider whose model
    caps below 32000 answers an over-large max_tokens with a 400 — a total
    failure, worse than the truncation this default exists to prevent. Nothing
    in the fleet does that today (all deepseek), which is exactly why the ceiling
    could be raised safely; this keeps that true when a smaller model is added.

    Only a limit we VOUCH for is allowed to lower the ceiling — a curated entry
    or an explicit env override. litellm's registry is excluded on purpose: it
    claims deepseek-v4-pro caps at 8192 while the live model emits 10940, and
    clamping to a stale-low number would re-create the exact truncation bug this
    module's default was raised to fix. Believing a guess is what broke this
    before; an unvetted model keeps the generous ceiling instead.
    """
    try:
        from acb_llm.model_limits import get_limits
        limits = get_limits(model)
    except Exception:
        return requested          # resolver unavailable — never block a call
    if limits.max_output_source not in ("curated", "env"):
        return requested
    if limits.max_output <= 0 or requested <= limits.max_output:
        return requested
    _log.info(
        "v1.max_tokens_clamped",
        model=model,
        requested=requested,
        clamped_to=limits.max_output,
        source=limits.max_output_source,
    )
    return limits.max_output


# ── Output-truncation visibility (audit CX4) ──────────────────────────────
# When the provider cuts generation at max_tokens (finish_reason="length"),
# the text just stops mid-sentence and the turn otherwise renders as a normal
# completed answer — nothing anywhere inspected finish_reason. Appending an
# explicit marker makes the cut visible to the user (and greppable in
# transcripts/traces). Disable with V1_SURFACE_TRUNCATION=0 for byte-exact
# passthrough.
_SURFACE_TRUNCATION = os.environ.get(
    "V1_SURFACE_TRUNCATION", "1"
).strip().lower() in ("1", "true", "yes", "on")
_TRUNCATION_NOTICE = (
    "\n\n[output truncated: the model hit its output-token limit]"
)


def _chunk_finish_reason(data: dict[str, Any]) -> str | None:
    """Extract choices[0].finish_reason from a streamed chunk dict."""
    try:
        choices = data.get("choices") or []
        return choices[0].get("finish_reason") if choices else None
    except Exception:  # noqa: BLE001
        return None


def _truncation_notice_chunk(model: str) -> dict[str, Any]:
    """A synthetic OpenAI-format chunk carrying the truncation notice."""
    return {
        "id": "cc-truncation-notice",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": _TRUNCATION_NOTICE},
            "finish_reason": None,
        }],
    }


def _mark_truncated_nonstream(choices: Any) -> bool:
    """Append the truncation notice to a non-streaming completion in place.

    Returns True when choices[0] was cut at the output limit and text content
    was available to annotate. Handles both dict and litellm-object shapes.
    """
    try:
        c0 = choices[0]
        fr = (
            c0.get("finish_reason") if isinstance(c0, dict)
            else getattr(c0, "finish_reason", None)
        )
        if fr != "length":
            return False
        msg = (
            c0.get("message") if isinstance(c0, dict)
            else getattr(c0, "message", None)
        )
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            msg["content"] += _TRUNCATION_NOTICE
            return True
        if msg is not None and isinstance(getattr(msg, "content", None), str):
            msg.content += _TRUNCATION_NOTICE
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


# ── Context-window guard ──────────────────────────────────────────────────
# Safety margin between what we count and what the provider counts (tokenizer
# divergence, per-message envelopes), and the smallest completion reservation
# worth keeping once the prompt is under pressure.
_CONTEXT_FIT_SAFETY_TOKENS: int = 512
_CONTEXT_FIT_MIN_OUTPUT: int = 1024


def _fit_context_window(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model: str,
    max_tokens: int,
) -> tuple[list[dict[str, Any]], int]:
    """Keep ``prompt + completion`` inside ``model``'s context window.

    This route is the choke point for ALL agent traffic — including Copilot-SDK
    sessions, whose backend compaction is disabled for BYOK models
    (orchestrator/_copilot_session.py) and whose session-accumulated history
    therefore arrives here unbounded. Nothing upstream fits that prompt, so
    without this guard a long chat session eventually reaches the provider as a
    hard 4xx ("context length exceeded") mid-conversation.

    No-op when the request already fits (the overwhelming majority). Otherwise
    recover in escalating steps — a trimmed request that works beats a faithful
    one the provider rejects:
      1. shrink ``max_tokens`` so prompt + completion fits (never below
         ``_CONTEXT_FIT_MIN_OUTPUT``);
      2. still over → drop oldest non-system messages, dropping any tool
         results orphaned by an evicted assistant turn;
      3. still over → char-trim the longest remaining message
         (``fit_messages_to_context``).
    """
    try:
        from acb_llm.context import (context_window_for, count_message_tokens,
                                     fit_messages_to_context)
        window = context_window_for(model)
        if window <= 0:
            return messages, max_tokens
        # Tool schemas ride in the same input window; count them roughly.
        tools_tokens = (len(json.dumps(tools, default=str)) // 4) if tools else 0
        prompt_tokens = count_message_tokens(messages, model) + tools_tokens
        if prompt_tokens + max_tokens + _CONTEXT_FIT_SAFETY_TOKENS <= window:
            return messages, max_tokens

        # 1. Shrink the output reservation before touching the prompt.
        room = window - prompt_tokens - _CONTEXT_FIT_SAFETY_TOKENS
        if room >= _CONTEXT_FIT_MIN_OUTPUT:
            _log.warning(
                "v1.context_fit_output_shrunk", model=model, window=window,
                prompt_tokens=prompt_tokens, requested=max_tokens,
                shrunk_to=room,
            )
            return messages, min(max_tokens, room)

        # 2. Evict oldest non-system turns until the floor reservation fits.
        out = [dict(m) for m in messages]
        budget = window - _CONTEXT_FIT_MIN_OUTPUT - _CONTEXT_FIT_SAFETY_TOKENS
        dropped = 0
        while count_message_tokens(out, model) + tools_tokens > budget:
            idx = next(
                (i for i, m in enumerate(out[:-1])
                 if m.get("role") not in ("system", "developer")),
                None,
            )
            if idx is None:
                break  # only system + the current turn left
            del out[idx]
            dropped += 1
            # A tool result whose assistant tool_call was just evicted is an
            # orphan some providers reject — evict it with its turn.
            while idx < len(out) - 1 and out[idx].get("role") == "tool":
                del out[idx]
                dropped += 1

        # 3. Last resort: char-trim the longest remaining message.
        out, _trimmed = fit_messages_to_context(
            out, model,
            max_output_tokens=_CONTEXT_FIT_MIN_OUTPUT,
            safety_margin=_CONTEXT_FIT_SAFETY_TOKENS,
        )
        final_prompt = count_message_tokens(out, model) + tools_tokens
        _log.warning(
            "v1.context_fit_messages_evicted", model=model, window=window,
            dropped=dropped, trimmed=bool(_trimmed),
            final_prompt_tokens=final_prompt,
        )
        return out, min(max_tokens, max(
            _CONTEXT_FIT_MIN_OUTPUT,
            window - final_prompt - _CONTEXT_FIT_SAFETY_TOKENS,
        ))
    except Exception:  # noqa: BLE001 — the guard must never break a working call
        _log.warning("v1.context_fit_failed", model=model, exc_info=True)
        return messages, max_tokens


# Mount at /v1 (OpenAI standard) and also at root for SDKs that omit the prefix.
router_v1 = APIRouter(prefix="/v1", tags=["openai-compat"])
router_root = APIRouter(tags=["openai-compat"])

# Tier name → tier id mapping. Single source of truth is
# acb_llm.client._TIER_ALIAS_MAP (also used by context.py); imported here rather
# than duplicated so the two can never drift. The orchestrator (agents.py,
# executor.py) passes these alias names as the "model" field — if an alias is
# missing, litellm rejects it with "BadRequestError: LLM Provider NOT provided".
from acb_llm.client import _TIER_ALIAS_MAP as _TIER_NAME_TO_ID  # noqa: E402


def _resolve_model(requested: str) -> str:
    """Map tier aliases to current model strings; pass unknown models through.

    Reads from acb_llm.client._TIER_MODEL at call time so runtime tier
    changes (via POST /settings/llm/tier) take effect immediately.
    """
    tier_id = _TIER_NAME_TO_ID.get(requested)
    if tier_id:
        from acb_llm.client import _TIER_MODEL as _live_tiers
        return _live_tiers.get(tier_id, requested)
    return requested


def _sanitize_messages_for_provider(
    messages: list[dict[str, Any]],
    provider: str,
) -> list[dict[str, Any]]:
    """Normalize messages so they pass provider-specific validation.

    DeepSeek (and some other providers) reject assistant messages where both
    ``content`` and ``tool_calls`` are null/missing.  The Copilot SDK may
    emit such messages when it serialises conversation history that includes
    tool-call-only assistant turns (content=null, tool_calls=[...]) or
    thinking-only turns that have neither field.

    Also handles camelCase ``toolCalls`` (some SDK variants), empty/whitespace
    content, and malformed tool-call items that lack a ``function`` key.

    Rules applied:
    1. Assistant messages with *neither* valid content nor valid tool_calls
       → dropped.
    2. Assistant messages with tool_calls but null/empty content → content
       set to "" (all providers — null content violates the OpenAI spec).
    3. Tool messages with null/empty content → content set to "[tool result]".
    4. Malformed tool-call items (missing ``function`` / ``name``) → stripped.
    """
    if not messages:
        return messages

    cleaned: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content")
        # Accept both snake_case and camelCase tool calls.
        tool_calls = m.get("tool_calls") or m.get("toolCalls")

        if role == "assistant":
            # ── Normalise tool_calls ──────────────────────────────
            _valid_tool_calls: list[dict[str, Any]] = []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        if isinstance(fn, dict) and fn.get("name"):
                            _valid_tool_calls.append(tc)
                            continue
                        # Some SDKs put name/arguments at top level.
                        if tc.get("name"):
                            _valid_tool_calls.append(tc)
                            continue
                if _valid_tool_calls:
                    m = dict(m)
                    m["tool_calls"] = _valid_tool_calls
                    tool_calls = _valid_tool_calls
                else:
                    tool_calls = None

            # ── Assess content ───────────────────────────────────
            has_content = bool(
                content is not None
                and str(content).strip()
            )

            # ── Assess tool_calls ────────────────────────────────
            has_tool_calls = bool(
                isinstance(tool_calls, list) and len(tool_calls) > 0
            )

            # ── Drop if neither is present ───────────────────────
            if not has_content and not has_tool_calls:
                _log.debug(
                    "v1.sanitize_dropped_assistant",
                    reason="no content or tool_calls",
                )
                continue

            # ── Always fix null/empty content when tool_calls set ─
            # The OpenAI spec says content SHOULD be null when
            # tool_calls is present, but many providers (DeepSeek,
            # Groq, Together) reject null content outright.
            if has_tool_calls and (
                content is None or str(content).strip() == ""
            ):
                if not isinstance(m, dict):
                    m = dict(m)
                m["content"] = ""

        elif role == "tool":
            # Tool messages must have non-empty string content.
            if content is None or str(content).strip() == "":
                m = dict(m)
                m["content"] = "[tool result]"

        cleaned.append(m)

    return cleaned


# ── Secret-stripping patterns for upstream error surfacing ─────────────────
# The provider's error string carries the ACTIONABLE reason (rate limit, bad
# key, context-length, invalid model) but may also embed the api_base URL, a
# Bearer token, or an ``sk-…`` key fragment. We surface the reason to the
# operator (it's THEIR Metorite) but redact anything secret first.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),          # OpenAI-style keys
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.I),  # bearer tokens
    re.compile(r"https?://\S+"),                     # endpoint URLs
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),      # GitHub tokens
)


def _sanitize_upstream_error(exc: Exception) -> tuple[int, str]:
    """Return ``(http_status, safe_message)`` for an upstream completion error.

    The status is taken from the litellm exception (``status_code``) when
    present so the caller — and the frontend's ``parseAgentError`` — can
    classify it (429 rate-limit, 401 auth, 400 bad-request, …) instead of
    dead-ending on an opaque "upstream completion error". The message keeps the
    provider's human-readable reason but with keys / tokens / URLs redacted.
    """
    status = getattr(exc, "status_code", None)
    try:
        status = int(status) if status is not None else 502
    except (TypeError, ValueError):
        status = 502
    if not (400 <= status <= 599):
        status = 502

    reason = str(getattr(exc, "message", "") or str(exc) or "").strip()
    for pat in _SECRET_PATTERNS:
        reason = pat.sub("[redacted]", reason)
    # Collapse whitespace and cap length so a giant provider payload can't bloat
    # the response (or the CLI/UI that renders it).
    reason = re.sub(r"\s+", " ", reason)[:300].strip()
    if not reason:
        reason = "upstream completion error"
    return status, f"upstream completion failed ({status}): {reason}"


# ── CP-11 slice 3: the serving hop ─────────────────────────────────────────
#
# ⚠️ **This file is the FIFTH importer of `acb_auth.console_resolve`**, where
# the fence in `tests/unit/test_console_dependency_boundary.py` allowed four.
# The argument is written beside the name there, and it is this: the property
# that fence protects is *nothing here allocates a seat*, and this call site
# allocates none. It spends AI CREDITS — a different resource, a different
# door (`cc_live_`, not `cc_depl_`) and a different table.


def _router_should_serve(body: dict[str, Any]) -> bool:
    """Whether THIS request goes to the Console Router. Two conditions, both.

    1. the flag is on;
    2. the box is wired for the Router (URL **and** the `cc_live_` org key).

    ⚠️ **A STREAM IS ROUTED TOO, as of CP-11 slice 5.** Slice 3 carried a third
    condition that sent every stream down the local path, because the Router
    answered 501 and CP-4b was unbuilt. That made streaming UNMETERED, and
    since every agent runtime streams it left most traffic unmetered. CP-4b
    landed on 2026-08-27 and the condition is gone with it.

    ⚠️ Do not re-add it as a "safety" measure. A stream on the local path is a
    stream nobody bills, which is the D57.7 failure reached by a different
    road.
    """
    from acb_common.settings import get_settings

    if not get_settings().router_serving_enabled:
        return False

    from acb_auth.console_resolve import router_is_wired

    if not router_is_wired():
        # Flag ON, box not wired. Say so: this state is indistinguishable from
        # "the flag is off" at the surface, and silence is how it stays that way.
        _log.warning("v1.router_enabled_but_unwired")
        return False

    return True


async def _serve_via_router_stream(
    request: Request, body: dict[str, Any]
) -> Any:
    """Stream one completion through the Console Router, on OUR account.

    ⚠️ **The first chunk is pulled HERE, before the response is returned.** That
    is the whole shape of this function. A streamed refusal has to become a
    status code, and the status line is fixed the moment a body starts. So the
    generator is advanced once while a `JSONResponse` is still possible, and
    only then handed to Starlette.

    ⚠️ **A failure FAILS (D57.7).** There is no `except -> local` arm here
    either. Slice 3's argument applies unchanged: a silent fallback would serve
    a stream on tenant-local keys, at tenant-local models, UNMETERED — which is
    precisely the hole this slice closes.
    """
    from acb_auth.console_resolve import (
        ConsoleRouterUnavailable,
        ConsoleRouterVerdict,
        stream_completion_on_console,
    )

    outbound = dict(body)
    outbound["messages"] = _sanitize_messages_for_provider(
        body.get("messages", []), ""
    )

    started = time.monotonic()
    frames = stream_completion_on_console(
        outbound,
        member=request.headers.get("x-cc-member") or None,
        agent=request.headers.get("x-cc-agent") or None,
        module_slug=request.headers.get("x-cc-module") or None,
        run_id=request.headers.get("x-cc-run") or None,
    )

    first: bytes | None = None
    try:
        first = await frames.__anext__()
    except StopAsyncIteration:
        # The Router answered 200 and sent nothing. Rare, and not an error we
        # can improve on: relay the empty stream.
        first = None
    except ConsoleRouterVerdict as verdict:
        # A VERDICT, relayed with its own status: 402 out of credits, 400 a
        # bare model id, 403 the breaker. Flattening these into one 502 would
        # tell a customer their Router is broken when they are out of credits.
        _log.info(
            "v1.router_stream_refused", status=verdict.status,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return JSONResponse(status_code=verdict.status, content=verdict.body)
    except ConsoleRouterUnavailable as exc:
        _log.error(
            "v1.router_unavailable", error=str(exc), stream=True,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return JSONResponse(
            status_code=502,
            content={"error": {
                "message": "the AI Router is unavailable",
                "type": "ConsoleRouterUnavailable",
                "code": 502,
            }},
        )

    async def _relay() -> Any:
        if first is not None:
            yield first
        async for chunk in frames:
            yield chunk
        _log.info(
            "v1.router_stream_served",
            elapsed_ms=round((time.monotonic() - started) * 1000),
            metered=True,
        )

    return StreamingResponse(
        _relay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _serve_via_router(
    request: Request, body: dict[str, Any]
) -> dict[str, Any] | JSONResponse:
    """Serve one completion through the Console Router, on OUR account.

    ⚠️ **The model string passes through UNTOUCHED.** `_resolve_model` maps a
    tier alias to a concrete model for the LOCAL path. The Router does its own
    resolution from `tier_binding` and 400s a bare model id rather than coercing
    it (D32.7), so resolving here would hand the Console a model name and turn
    every tier call into a refusal.

    ⚠️ **A failure FAILS (D57.7).** There is deliberately NO `except → local`
    arm. A silent fallback would serve traffic on tenant-local keys, at
    tenant-local models, UNMETERED — the "four deploys reported success while
    shipping nothing" shape applied to billing. BYOK is reached by
    CONFIGURATION (a `provider_credential` carrying an `organization_id`), never
    by a failure path, and the two must not share code.
    """
    from acb_auth.console_resolve import (ConsoleRouterUnavailable,
                                          chat_completion_on_console)

    # Spec-compliance normalisation still applies: it repairs OpenAI-spec
    # violations the Copilot SDK emits (null content beside tool_calls), which
    # most providers reject. It branches on no provider — the parameter is
    # unused — so it is safe before the Console has chosen one.
    outbound = dict(body)
    outbound["messages"] = _sanitize_messages_for_provider(
        body.get("messages", []), ""
    )

    started = time.monotonic()
    try:
        status, payload = await chat_completion_on_console(
            outbound,
            member=request.headers.get("x-cc-member") or None,
            agent=request.headers.get("x-cc-agent") or None,
            module_slug=request.headers.get("x-cc-module") or None,
            run_id=request.headers.get("x-cc-run") or None,
        )
    except ConsoleRouterUnavailable as exc:
        _log.error(
            "v1.router_unavailable", error=str(exc),
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return JSONResponse(
            status_code=502,
            content={"error": {
                "message": "the AI Router is unavailable",
                "type": "ConsoleRouterUnavailable",
                "code": 502,
            }},
        )

    # §6B.5 hazard 2: this adds one network hop to the interactive path. The
    # number is LOGGED rather than assumed. On a box where the Console is a
    # loopback (D47) it is negligible — but that is a measurement, and it stops
    # being true the day the Console moves off the box (D47 clause 4).
    _log.info(
        "v1.router_served", status=status,
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )

    if status != 200:
        # A VERDICT, relayed with its own status: 402 out of credits, 400 an
        # unknown tier, 403 the circuit breaker. Each must arrive as ITSELF.
        # Flattened into one 502 they all read as "the AI is down", which sends
        # a customer to support instead of to the top-up they need.
        return JSONResponse(status_code=status, content=payload)
    return payload


async def _handle_chat_completions(request: Request) -> StreamingResponse | dict[str, Any]:
    """OpenAI-compatible chat completions. Supports streaming and tools."""
    body = await request.json()

    # ── The serving hop (CP-11 slice 3, D57) ──────────────────────────────
    #
    # ⚠️ This branch is deliberately the FIRST thing after reading the body, and
    # in particular it is BEFORE `_ensure_keys_loaded()`. §6B.5 hazard 3: loading
    # tenant-local provider keys for a call our own account is about to serve is
    # the process-global credential injection (§6 (f)) doing work it does not
    # need to do. A routed call must touch none of it.
    if _router_should_serve(body):
        # One branch, not two: `_handle_chat_completions` already sits over
        # ruff's complexity ceiling (C901, 20 > 15) and this must not add to
        # it. The two hops are NOT interchangeable — the buffered one returns
        # a dict and would silently de-stream a chat UI.
        hop = (_serve_via_router_stream if body.get("stream", False)
               else _serve_via_router)
        return await hop(request, body)

    # H4: the provider credential this call sends is process-global and this
    # route has no tenant to scope it with — `_auth` is `require_llm_api_auth`,
    # the deployment-wide LITELLM_MASTER_KEY, so `current_tenant()` is None on
    # the agent-runtime path. Threading an org from `x-cc-agent` or the body
    # would be R5 / R11's forbidden move. The full argument, and what a real
    # fix requires (a tenant-scoped API key — owner gate (f)), is on
    # `acb_llm.client._ensure_keys_loaded`. MT-1j slice 5, recorded not fixed.
    await _ensure_keys_loaded()

    # Observability attribution (E2): the agent runtime stamps its identity on the
    # request via default_headers so we can tie this model call + cost back to the
    # specific agent (otherwise it's a bare request with no run context). Fail-soft
    # — absent headers just fall back to source="chat", no agent.
    _obs_agent = request.headers.get("x-cc-agent") or None
    _obs_source = request.headers.get("x-cc-source") or "chat"
    _requested_model = body.get("model", "tier-balanced")
    model = _resolve_model(_requested_model)
    # Preserve the tier alias (if the caller asked for one) so per-tier cost/
    # usage breakdowns populate — this is the choke point for all agent traffic,
    # so a blank tier here left the whole per-tier view empty (obs gap).
    _tier = _requested_model if _requested_model in _TIER_NAME_TO_ID else ""
    messages = body.get("messages", [])
    tools = body.get("tools")
    tool_choice = body.get("tool_choice", "auto")
    temperature = body.get("temperature", 0.2)
    max_tokens = _clamp_max_tokens(
        body.get("max_tokens") or _DEFAULT_MAX_OUTPUT_TOKENS, model)
    stream = body.get("stream", False)
    # Passthrough: custom api_base/api_key for Ollama / vLLM / self-hosted
    # endpoints — only when explicitly enabled (SSRF guard; see module top).
    if _ALLOW_CALLER_ENDPOINT_OVERRIDE:
        api_base = body.get("api_base") or None
        api_key = body.get("api_key") or None
    else:
        api_base = None
        api_key = None
        if body.get("api_base") or body.get("api_key"):
            _log.warning("v1.caller_endpoint_override_ignored")

    import litellm as _litellm  # type: ignore[import-untyped]
    from litellm import acompletion  # type: ignore[import-untyped]

    # Drop parameters the provider doesn't support (e.g. Copilot SDK may
    # send fields that DeepSeek / other providers reject).
    _litellm.drop_params = True
    _litellm.suppress_debug_info = True

    # Dynamically register model so new provider models route correctly.
    from acb_llm.client import ensure_model_registered
    provider = ensure_model_registered(model)

    # Sanitize messages for providers with strict validation (e.g. DeepSeek).
    messages = _sanitize_messages_for_provider(messages, provider or model)

    # Context-window guard (single-agent chat audit C1/CX1): fit prompt +
    # completion into the model's window BEFORE cache annotation, so an
    # unbounded Copilot-session history degrades gracefully (shrunk output /
    # evicted oldest turns) instead of a provider 4xx mid-conversation.
    messages, max_tokens = _fit_context_window(messages, tools, model, max_tokens)

    # Provider-aware prompt caching (specs/llm_caching_memory.md Phase 2/3).
    # This is THE choke point every agent runtime (native-MAF
    # OpenAIChatCompletionClient, Copilot SDK) POSTs through, so marking the
    # cache breakpoints here covers all agent traffic in one place:
    #   • Anthropic tiers → cache_control on the stable system block (split at
    #     the CACHE BREAK sentinel the executor injects) + the last tool schema.
    #   • OpenAI tiers → prompt_cache_key routing (agent name if present).
    #   • DeepSeek/others → sentinel stripped (automatic/no-op caching).
    # The sentinel never reaches the model in any case.
    from acb_llm.prompt_cache import apply_prompt_caching

    _cache_key = body.get("prompt_cache_key") or body.get("user") or None
    messages, tools, _cache_extra = apply_prompt_caching(
        model=model, messages=messages, tools=tools,
        cache_key=_cache_key, extra={},
    )

    common: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice if tools else None,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_cache_extra,
    }
    if api_base:
        common["api_base"] = api_base
    if api_key:
        common["api_key"] = api_key

    if stream:
        async def event_generator():
            # Keep the raw chunks so we can rebuild the full response (with usage)
            # AFTER the stream, WITHOUT changing the provider request — this is
            # THE choke point every agent runtime streams through, so it must stay
            # byte-identical for the client. Observability is derived, never
            # intrusive.
            _chunks: list[Any] = []
            _length_cut = False
            try:
                response = await acompletion(**common, stream=True)
                async for chunk in response:
                    _chunks.append(chunk)
                    # litellm returns Pydantic ModelResponseStream — convert to dict
                    data = chunk.model_dump() if hasattr(chunk, "model_dump") else dict(chunk)
                    # Never emit a chunk whose ``choices`` is null: the MAF/OpenAI
                    # client does ``len(chunk.choices)`` on every streamed chunk,
                    # so a null here crashes the whole run with an opaque
                    # "'NoneType' object has no len()". A missing/usage-only chunk
                    # must carry an empty list, which the client already skips.
                    if data.get("choices") is None:
                        data["choices"] = []
                    if _chunk_finish_reason(data) == "length":
                        _length_cut = True
                    yield f"data: {json.dumps(data)}\n\n"
                # Truncation visibility (audit CX4): the provider stopped at
                # max_tokens mid-generation. Append one synthetic delta chunk
                # so the cut is visible in the rendered turn instead of the
                # text just stopping mid-sentence with a "completed" status.
                if _length_cut and _SURFACE_TRUNCATION:
                    _log.warning(
                        "v1.output_truncated", model=model,
                        agent=_obs_agent, max_tokens=max_tokens,
                    )
                    yield (
                        "data: "
                        + json.dumps(_truncation_notice_chunk(model))
                        + "\n\n"
                    )
                yield "data: [DONE]\n\n"
            except Exception as exc:
                # Log the full detail server-side; surface a SANITIZED reason +
                # status to the caller (keys/URLs/tokens stripped) so the
                # provider's real cause — rate limit, bad key, context length —
                # reaches the operator instead of an opaque dead-end. The
                # OpenAI/Copilot client raises this as its error message.
                _log.exception("v1.stream_error")
                _status, _safe = _sanitize_upstream_error(exc)
                yield (
                    "data: "
                    + json.dumps({"error": {
                        "message": _safe,
                        "type": type(exc).__name__,
                        "code": _status,
                    }})
                    + "\n\n"
                )
            finally:
                # Observability (E2): rebuild usage from the streamed chunks
                # (litellm's own stream aggregator) → emit the model activation +
                # cost. This is what makes chat-agent model calls observable.
                # Best-effort; source="chat" (interactive agent traffic).
                if _chunks:
                    try:
                        from litellm import stream_chunk_builder  # noqa: PLC0415
                        rebuilt = stream_chunk_builder(_chunks, messages=messages)
                        if rebuilt is not None:
                            from acb_llm.client import _emit_usage  # noqa: PLC0415
                            _emit_usage(model, _tier, rebuilt,
                                        source=_obs_source, agent=_obs_agent)
                    except Exception:  # noqa: BLE001
                        pass

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # Non-streaming
    try:
        response = await acompletion(**common)
        # Observability (E2): emit the model activation + cost for this agent
        # completion. Best-effort; never affects the response.
        try:
            from acb_llm.client import _emit_usage  # noqa: PLC0415
            _emit_usage(model, _tier, response, source=_obs_source, agent=_obs_agent)
        except Exception:  # noqa: BLE001
            pass
        payload = dict(response) if hasattr(response, "items") else response
        # An OpenAI-compatible completion MUST carry a non-empty ``choices``
        # list. Some providers (and litellm on a soft error) return a 200 body
        # that has no ``choices`` — the MAF/OpenAI client then parses it into a
        # ChatCompletion whose ``choices`` is None and blows up iterating it with
        # "'NoneType' object is not iterable", masking the real cause. Surface it
        # as an upstream error (proper status) so the client raises cleanly.
        # ``payload`` may be a dict or a litellm ModelResponse object, so read
        # ``choices`` generically from either shape.
        _choices = (
            payload.get("choices") if isinstance(payload, dict)
            else getattr(payload, "choices", None)
        )
        if not _choices:
            _log.warning("v1.completion_missing_choices", model=model)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": "upstream completion returned no choices",
                        "type": "UpstreamResponseError",
                    }
                },
            )
        # Truncation visibility (audit CX4), non-streaming shape.
        if _SURFACE_TRUNCATION and _mark_truncated_nonstream(_choices):
            _log.warning(
                "v1.output_truncated", model=model,
                agent=_obs_agent, max_tokens=max_tokens,
            )
        return payload  # type: ignore[return-value]
    except Exception as exc:
        # Log full detail server-side; surface a SANITIZED reason + the upstream
        # status (keys/URLs/tokens stripped) so the provider's real cause reaches
        # the operator. Use a non-2xx status (never a 200 error body): a 200 with
        # no ``choices`` makes the MAF/OpenAI client iterate a None ``choices``
        # and fail with "'NoneType' object is not iterable", hiding the real
        # error. A non-2xx makes the client raise a clean APIStatusError carrying
        # this message, and the frontend can classify it by status/keyword.
        _log.exception("v1.completion_error")
        _status, _safe = _sanitize_upstream_error(exc)
        return JSONResponse(
            status_code=_status,
            content={
                "error": {
                    "message": _safe,
                    "type": type(exc).__name__,
                    "code": _status,
                }
            },
        )


# Register on both /v1/chat/completions and /chat/completions
# response_model=None because we return either StreamingResponse or dict.
# require_internal_auth 401s any caller without the internal Bearer token —
# this endpoint bills the server's stored provider keys, so it must not be
# world-reachable. Every internal caller already forwards the token.
_auth = [Depends(require_llm_api_auth)]
router_v1.post(
    "/chat/completions", response_model=None, dependencies=_auth,
)(_handle_chat_completions)
router_root.post(
    "/chat/completions", response_model=None, dependencies=_auth,
)(_handle_chat_completions)

# Export both routers — callers include both
routers = [router_v1, router_root]
