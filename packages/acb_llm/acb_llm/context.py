"""Context-window management + automatic model fallback (ADR-008 follow-up).

Most email-assistant inbox work runs on a cheap "assistant" tier model (e.g.
DeepSeek chat). Two failure modes have to be handled before a request reaches
the provider:

1. **Context overflow.** A prompt (long thread + knowledge base + every rule
   instruction) can exceed the assistant model's input window, so the provider
   rejects the call. We resolve the *underlying* model behind a tier alias, look
   up its real context window, and truncate/compress the input to fit *before*
   the call.

2. **The cheap model can't do the job.** Even within budget, a small model may
   mis-classify or hard-fail. The caller supplies a more powerful *fallback*
   model; on a context-length error (or any hard failure) we retry once on the
   fallback, re-fitting the input to that model's (usually larger) window.

This keeps the common path cheap while still getting an answer out of the hard
emails. The fitting + fallback logic lives here so every in-gateway LLM call
(rule classification, drafting, …) can share one implementation.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from acb_common import get_logger

from acb_llm.message_compress import compress_message_content
from acb_llm.model_limits import get_limits

# Stats from the most recent assemble_run_context() call in THIS async
# context (audit CX6). Eviction was entirely silent to the user — quality
# just degraded as oldest turns dropped. The executor reads this right after
# composing a run's input and surfaces a progress line when turns were
# dropped, without changing the assembler's return contract.
last_fit_stats: ContextVar[dict[str, Any] | None] = ContextVar(
    "acb_llm_last_fit_stats", default=None,
)

_log = get_logger("acb_llm.context")


def _infer_app_source(max_depth: int = 12) -> str | None:
    """Infer the originating app from the call stack, for activity attribution.

    Walks up the stack for the first frame whose module lives under
    ``gateway.routes.<app>`` and returns ``<app>`` (e.g. ``email``, ``tasks``).
    This is what makes every app observable WITHOUT touching its call sites —
    a new app under ``routes/<name>/`` is attributed automatically. Returns
    ``None`` when no such frame is found (e.g. an agent run, whose ``source``
    already rides the run context). Never raises.
    """
    marker = ".routes."
    try:
        frame = sys._getframe(2)  # skip this fn + acompletion_with_fallback
    except Exception:  # noqa: BLE001
        return None
    depth = 0
    while frame is not None and depth < max_depth:
        mod = frame.f_globals.get("__name__", "")
        idx = mod.find(marker)
        if idx != -1:
            rest = mod[idx + len(marker):]
            app = rest.split(".", 1)[0].strip()
            if app:
                return app
        frame = frame.f_back
        depth += 1
    return None

# Model limits (context window, max output) resolve through ONE place:
# acb_llm.model_limits.get_limits — imported at the top of this module, and the
# home of the fallback window table and the default window that used to sit
# here. Import them from there, not from this module.

# Substrings litellm / providers use when the prompt is too long for the model.
_CONTEXT_OVERFLOW_MARKERS = (
    "context length", "context_length", "context window", "maximum context",
    "context_length_exceeded", "too many tokens", "reduce the length",
    "input is too long", "prompt is too long", "string too long",
    "please reduce", "exceeds the maximum",
)

# Chars per token for the heuristic estimator (English prose ≈ 4).
_CHARS_PER_TOKEN = 4
# Trimmed-message marker so a truncated prompt reads as deliberately shortened.
_TRUNCATION_MARKER = "\n\n[… content truncated to fit the model context window …]\n\n"


def resolve_underlying_model(model: str) -> str:
    """Resolve a gateway tier alias (``tier-fast`` / ``-balanced`` / ``-powerful``)
    to the concrete litellm model string it currently routes to; pass an explicit
    ``provider/model`` (or any unknown value) through unchanged.

    Reads ``acb_llm.client._TIER_MODEL`` at call time so runtime tier changes
    (Settings UI) take effect immediately."""
    m = (model or "").strip()
    if not m:
        return m
    try:
        from acb_llm.client import _TIER_ALIAS_MAP, _TIER_MODEL
        tier_id = _TIER_ALIAS_MAP.get(m)
        if tier_id:
            return _TIER_MODEL.get(tier_id, m)
    except Exception:
        pass
    return m


def context_window_for(model: str) -> int:
    """Best-effort *input*-token budget for ``model`` (a tier alias or a
    concrete litellm model id).

    For tier aliases (``tier-fast`` / ``tier-balanced`` / ``tier-powerful``)
    the backing model is resolved dynamically via ``resolve_underlying_model``,
    which reads ``acb_llm.client._TIER_MODEL``.  ``_TIER_MODEL`` is populated
    from ``infra/litellm/config.yaml`` + ``tier_overrides.yaml`` at import time
    and updated immediately by :func:`acb_llm.client.set_tier_model` whenever
    the Settings UI changes a tier assignment.  No hardcoded context-window
    values for tier aliases — the budget always tracks the currently-configured
    model.

    Thin wrapper over :func:`acb_llm.model_limits.get_limits`, which owns the
    resolution order and the sources. Kept as the input-side entry point
    because callers and tests import it from here; use ``get_limits`` directly
    when you also need ``max_output`` or the provenance of a number.

    Always returns a positive integer.
    """
    return get_limits(model).context_window


def count_message_tokens(messages: list[dict[str, Any]], model: str = "") -> int:
    """Estimate the prompt token count of a chat ``messages`` list. Uses
    litellm's tokenizer for the resolved model when available, else a chars/4
    heuristic plus a small per-message envelope allowance."""
    resolved = resolve_underlying_model(model) if model else ""
    if resolved:
        try:
            from litellm import token_counter
            n = token_counter(model=resolved, messages=messages)
            if n:
                return int(n)
        except Exception:
            pass
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
    # +~8 tokens/message for role + delimiters (OpenAI chat-format overhead).
    return max(1, total // _CHARS_PER_TOKEN) + 8 * max(1, len(messages))


def fit_messages_to_context(
    messages: list[dict[str, Any]],
    model: str,
    *,
    max_output_tokens: int = 1024,
    safety_margin: int = 512,
) -> tuple[list[dict[str, Any]], bool]:
    """Shrink ``messages`` so they fit ``model``'s input window, reserving room
    for the completion (``max_output_tokens``) and a small ``safety_margin``.

    Returns ``(messages, truncated)``. When already within budget the original
    list is returned untouched. Otherwise the longest string ``content`` is
    repeatedly trimmed (keeping a head + tail around a marker) until the prompt
    fits. System/developer messages are protected: they are only trimmed when
    no other message has trimmable content left (a degraded persona beats a
    provider 4xx, but only as the last resort — audit CX5)."""
    budget = context_window_for(model) - max_output_tokens - safety_margin
    if budget < 1024:
        # Pathologically small window (or huge max_tokens): the requested
        # completion cannot coexist with a usable prompt, so budget against a
        # floor-sized completion instead. The old ``window * 3 // 4`` rescue
        # produced an unsatisfiable request by construction (prompt at 3/4
        # window + the untouched max_tokens > window — audit CX3). The /v1
        # choke point clamps the actual max_tokens to the room left after the
        # prompt (gateway ``_fit_context_window``), keeping this consistent.
        budget = max(1024, context_window_for(model) - 1024 - safety_margin)

    if count_message_tokens(messages, model) <= budget:
        return messages, False

    out: list[dict[str, Any]] = [dict(m) for m in messages]
    truncated = False
    # Bounded passes; each pass removes ~30% of the longest message, so the
    # prompt converges on the budget quickly without an unbounded loop.
    for _ in range(24):
        if count_message_tokens(out, model) <= budget:
            break
        # Prefer trimming non-system content (the big user payload / tool
        # results), keeping the persona/instructions intact (audit CX5).
        idx, longest = -1, 0
        for i, msg in enumerate(out):
            if msg.get("role") in ("system", "developer"):
                continue
            content = msg.get("content")
            if isinstance(content, str) and len(content) > longest:
                idx, longest = i, len(content)
        if idx < 0 or longest <= len(_TRUNCATION_MARKER) + 200:
            # No trimmable non-system content left — last resort: allow the
            # system block itself rather than ship an over-budget prompt.
            idx, longest = -1, 0
            for i, msg in enumerate(out):
                content = msg.get("content")
                if isinstance(content, str) and len(content) > longest:
                    idx, longest = i, len(content)
            if idx < 0 or longest <= len(_TRUNCATION_MARKER) + 200:
                break  # nothing meaningful left to trim
        content = out[idx]["content"]
        body_len = len(content) - (len(_TRUNCATION_MARKER) if _TRUNCATION_MARKER in content else 0)
        keep = max(200, int(body_len * 0.7))
        # Structure-aware: preserve an email thread's newest message / JSON
        # shape instead of slicing mid-structure; falls back to the prior
        # head+tail char-slice on anything unrecognized (never regresses).
        out[idx]["content"] = compress_message_content(
            content, keep, marker=_TRUNCATION_MARKER,
        )
        truncated = True

    if truncated:
        _log.info(
            "acb_llm.context_fitted",
            model=model,
            budget=budget,
            final_tokens=count_message_tokens(out, model),
        )
    return out, truncated


def is_context_overflow_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a provider rejecting an over-long prompt."""
    s = str(exc).lower()
    return any(mark in s for mark in _CONTEXT_OVERFLOW_MARKERS)


async def acompletion_with_fallback(
    *,
    model: str,
    fallback_model: str = "",
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
    temperature: float = 0.2,
    source: str | None = None,
    **extra: Any,
) -> tuple[Any, str]:
    """Run a chat completion on ``model``, fitting the input to its context
    window first; on a context-overflow error (or any hard failure) retry once
    on ``fallback_model`` with the input re-fit to that model's window.

    The fallback is skipped when it is empty or resolves to the same underlying
    model as the primary. Returns ``(response, used_model)`` where
    ``used_model`` is the concrete litellm id that produced the answer. Raises
    the last error if every attempt fails.

    ``source`` overrides the stack-inferred attribution (see
    ``_infer_app_source``) for activity/cost tagging. Needed by callers whose
    real identity isn't recoverable from the calling module's dotted path —
    e.g. Custom Apps: every app's AI call runs through the SAME
    ``gateway.routes.apps.runtime`` module regardless of which app slug made
    it, so stack inspection can only ever resolve the shared package name
    (``"apps"``), never the individual app. Passing ``source=f"app:{slug}"``
    explicitly is the only way to get per-app cost attribution.
    """
    import litellm as _litellm
    from litellm import acompletion

    from acb_llm.client import (
        _emit_usage,
        _ensure_keys_loaded,
        ensure_model_registered,
    )

    _source = source if source is not None else _infer_app_source()

    # ── H-171: our own Router bills this, or nobody does ────────────────
    #
    # 🔴 **The product's own AI was never metered.** This function called
    # litellm directly, so the apps runtime, the email automation, the
    # assistants and the agents spent our vendor account and billed nobody.
    # `ROUTER_SERVING_ENABLED` did not change that, because the Router was not
    # on this path — only `/v1/chat/completions` was.
    #
    # ⚠️ **The tier goes through untouched and the caller's `fallback_model`
    # is IGNORED here.** Callers already pass `tier-fast` and friends, and the
    # Console has a ranked `tier_binding` chain an operator maintains. Running
    # this function's two-attempt loop as well would be a second opinion about
    # failover, decided by whichever call site was edited last.
    #
    # ⚠️ **D57.7 — a routed call that fails, FAILS.** Nothing below this
    # block runs when routing is on. Falling back to local litellm would
    # restore the unbilled path exactly when the Console is refusing to be
    # paid, which is the one moment it must not.
    from acb_llm.routed import completion_on_router, routing_is_on

    if routing_is_on():
        fitted_for_router, _ = fit_messages_to_context(
            messages, model, max_output_tokens=max_tokens,
        )
        return await completion_on_router(
            tier=model,
            messages=fitted_for_router,
            max_tokens=max_tokens,
            temperature=temperature,
            source=_source,
            extra=extra,
        )

    _litellm.drop_params = True
    _litellm.suppress_debug_info = True
    await _ensure_keys_loaded()

    attempts: list[tuple[str, bool]] = [(model, False)]
    fb = (fallback_model or "").strip()
    if fb and resolve_underlying_model(fb) != resolve_underlying_model(model):
        attempts.append((fb, True))

    last_exc: BaseException | None = None
    for attempt_model, is_fallback in attempts:
        resolved = resolve_underlying_model(attempt_model)
        ensure_model_registered(resolved)
        fitted, was_truncated = fit_messages_to_context(
            messages, attempt_model, max_output_tokens=max_tokens,
        )
        try:
            resp = await acompletion(
                model=resolved, messages=fitted,
                temperature=temperature, max_tokens=max_tokens, **extra,
            )
            if is_fallback:
                _log.info("acb_llm.fallback_succeeded",
                          model=resolved, truncated=was_truncated)
            # Usage + cost + live activation. This is the completion path email
            # and the task manager use (client.complete covers agent runs), so
            # emitting here is what makes those apps observable. Best-effort.
            try:
                _emit_usage(resolved, "", resp, source=_source)
            except Exception:  # noqa: BLE001
                pass
            return resp, resolved
        except Exception as exc:
            last_exc = exc
            _log.warning(
                "acb_llm.completion_failed",
                model=resolved,
                is_fallback=is_fallback,
                overflow=is_context_overflow_error(exc),
                will_fallback=(not is_fallback and len(attempts) > 1),
                error=str(exc)[:200],
            )
            continue

    raise last_exc or RuntimeError("LLM completion failed (no attempts made)")


async def acompletion_stream_text(
    *,
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int = 1024,
    temperature: float = 0.2,
    on_delta: Any = None,
    **extra: Any,
) -> tuple[str, str]:
    """Streaming counterpart of :func:`acompletion_with_fallback` for callers
    that want the TEXT (not the response object) plus live deltas.

    Fits the input to ``model``'s window, streams the completion, and awaits
    ``on_delta(kind, text)`` for every chunk — ``kind`` is ``"reasoning"``
    (reasoning-tier models thinking out loud, e.g. deepseek-reasoner's
    ``reasoning_content``) or ``"content"`` (answer text). Returns
    ``(full_content, used_model)``.

    No model-fallback chain here: a streaming caller has usually already
    surfaced partial output, so silently restarting on another model would
    duplicate it. On failure the error propagates and the caller decides
    (drafting falls back to its non-streaming path / neutral template).

    Usage is emitted post-stream via ``litellm.stream_chunk_builder`` so
    streamed calls stay visible in observability like non-streamed ones.
    """
    import litellm as _litellm
    from litellm import acompletion

    from acb_llm.client import (
        _emit_usage,
        _ensure_keys_loaded,
        ensure_model_registered,
    )

    _source = _infer_app_source()
    _litellm.drop_params = True
    _litellm.suppress_debug_info = True
    await _ensure_keys_loaded()

    resolved = resolve_underlying_model(model)
    ensure_model_registered(resolved)
    fitted, _was_truncated = fit_messages_to_context(
        messages, model, max_output_tokens=max_tokens,
    )
    response = await acompletion(
        model=resolved, messages=fitted,
        temperature=temperature, max_tokens=max_tokens,
        stream=True, **extra,
    )
    content_parts: list[str] = []
    chunks: list[Any] = []
    async for chunk in response:
        chunks.append(chunk)
        try:
            delta = chunk.choices[0].delta
        except (AttributeError, IndexError):
            continue
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning and on_delta is not None:
            try:
                await on_delta("reasoning", str(reasoning))
            except Exception:  # noqa: BLE001 — progress is best-effort
                pass
        text_piece = getattr(delta, "content", None)
        if text_piece:
            content_parts.append(str(text_piece))
            if on_delta is not None:
                try:
                    await on_delta("content", str(text_piece))
                except Exception:  # noqa: BLE001
                    pass
    # Rebuild a response object from the chunks so usage/cost accounting fires
    # for streamed calls too. Best-effort — never fail the draft over it.
    try:
        rebuilt = _litellm.stream_chunk_builder(chunks, messages=fitted)
        if rebuilt is not None:
            _emit_usage(resolved, "", rebuilt, source=_source)
    except Exception:  # noqa: BLE001
        pass
    return "".join(content_parts), resolved


# ── Server-side run-context assembler (C2) ──────────────────────────────────
# The single INPUT-side context builder. Before this, each orchestrator path
# re-sliced the client-sent history with its own COUNT cap (12/16/20/50 msgs)
# and char-truncation, none token-aware, and non-chat callers (API/webhook) got
# no history at all because nothing rebuilt it server-side. This routes them all
# through one token-budgeted assembler. See specs/context_assembly_c2.md.


def _dedupe_current_turn(
    history: list[dict[str, Any]], current_message: str,
) -> list[dict[str, Any]]:
    """Drop a trailing history entry equal to the current user turn.

    Server-side equivalent of route.ts's ``withoutCurrentTurn`` — so the model
    never sees the prompt twice, regardless of whether the caller already
    excluded it. Only strips a *trailing* user turn (the just-sent one).
    """
    if not history or not current_message.strip():
        return history
    last = history[-1]
    if (
        str(last.get("role")) == "user"
        and str(last.get("content") or "").strip() == current_message.strip()
    ):
        return history[:-1]
    return history


def assemble_run_context(
    *,
    system_context: str = "",
    history: list[dict[str, Any]] | None = None,
    current_message: str = "",
    model: str = "",
    max_output_tokens: int = 1024,
    history_loader: Callable[[], list[dict[str, Any]]] | None = None,
    max_turns: int = 50,
) -> list[dict[str, Any]]:
    """Assemble a token-budgeted OpenAI-format ``messages`` list for a run.

    One server-side assembler for every chat / agent-run path, replacing the
    six divergent client-history slicers in the executor (C2). Steps:

    1. **Source of history.** Use ``history`` when it has content. When empty
       AND ``history_loader`` is supplied (the server holds a ``thread_id``),
       rebuild it from the store via the loader — this is what gives non-chat
       callers (API/webhook/external) the SAME history as the browser client,
       which previously got none.
    2. **Dedupe the current turn** (server-side ``withoutCurrentTurn``).
    3. **Assemble** ``[system?] + history[-max_turns:] + current`` in OpenAI
       shape; only ``user``/``assistant``/``system`` roles with content survive.
    4. **Token-budget fit** via :func:`fit_messages_to_context` — token-aware
       fitting REPLACES the old blind count caps (``max_turns`` remains only as
       a cheap upper bound applied before the token pass).

    Pure function: DB access is injected via ``history_loader`` so ``acb_llm``
    keeps no dependency on the gateway. Never raises for empty inputs — returns
    at least the current user turn (and system, if any).
    """
    src = list(history or [])
    if not src and history_loader is not None:
        try:
            loaded = history_loader() or []
            if isinstance(loaded, list):
                src = loaded
        except Exception as exc:
            _log.warning("acb_llm.history_loader_failed", error=str(exc))

    src = _dedupe_current_turn(src, current_message)

    messages: list[dict[str, Any]] = []
    if system_context.strip():
        messages.append({"role": "system", "content": system_context.strip()})

    hist_turns: list[dict[str, Any]] = []
    for m in src[-max_turns:]:
        role = str(m.get("role") or "user")
        content = str(m.get("content") or "").strip()
        if content and role in ("user", "assistant", "system"):
            hist_turns.append({"role": role, "content": content})

    current_turn = (
        {"role": "user", "content": current_message.strip()}
        if current_message.strip() else None
    )

    # Reset per-call so a caller can never read a PREVIOUS run's pressure
    # stats from this async context (both exits below overwrite as needed).
    last_fit_stats.set({"dropped_turns": 0, "char_truncated": False})

    if not model:
        # No window to fit to — return the full assembly (count cap only).
        out = messages + hist_turns
        if current_turn:
            out.append(current_turn)
        return out

    # Token-budget windowing. Two-stage, because fit_messages_to_context only
    # TRIMS the single longest message per pass — great for the email shape
    # (one huge body) but it can't converge on a many-medium-turn chat
    # transcript. So first DROP whole oldest history turns until the assembly
    # fits, THEN let fit_messages_to_context handle any remaining oversized
    # single message (e.g. one giant pasted block). System context + the
    # current turn are never dropped — only historical turns.
    budget = context_window_for(model) - max_output_tokens - 512
    budget = max(1024, budget)
    dropped = 0
    while hist_turns:
        trial = messages + hist_turns + ([current_turn] if current_turn else [])
        if count_message_tokens(trial, model) <= budget:
            break
        hist_turns.pop(0)  # drop the oldest historical turn
        dropped += 1

    assembled = messages + hist_turns + ([current_turn] if current_turn else [])
    fitted, truncated = fit_messages_to_context(
        assembled, model, max_output_tokens=max_output_tokens,
    )
    last_fit_stats.set({"dropped_turns": dropped, "char_truncated": truncated})
    if truncated or dropped:
        _log.info(
            "acb_llm.run_context_fitted",
            model=model,
            turns=len(assembled),
            dropped_turns=dropped,
            char_truncated=truncated,
            from_loader=(not history and history_loader is not None),
        )
    return fitted
