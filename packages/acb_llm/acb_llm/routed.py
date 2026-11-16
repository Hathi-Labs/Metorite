"""Serve an in-product completion through OUR Router, so it bills. H-171.

Spec: ``launch_surface.md`` §4.1 (*"AI usage is metered separately in
credits"*) · **D57.7** (a routed call that fails, FAILS) · CP-6 (the balance
gate) · ``customer_console.md`` §6B.

🔴 **The product's own AI was never metered, and that made it free for ever.**
Measured 2026-09-23: ``acb_llm`` held ZERO references to the Console.
``acompletion_with_fallback`` imported litellm and called it directly, so the
apps runtime, the email automation, the assistants and the agents — 29 files —
spent our vendor account and billed nobody. Turning ``ROUTER_SERVING_ENABLED``
on did not change it, because the Router was not on that path.

⚠️ **It was OBSERVED and not BILLED, and the difference is the whole ticket.**
``_emit_usage`` already logs the vendor cost in USD and publishes a live
activity with D1's four-tuple. What never happened was a ``usage_event`` row
or a credit drawn down. So this is not new observability. It is the missing
half of the money loop.

## What routing changes, stated plainly

* **The tier decides, not the model.** Callers already pass ``tier-fast`` and
  friends, so the tier goes through untouched and the Router's own
  ``tier_binding`` chain picks the model. The caller's ``fallback_model`` is
  therefore IGNORED on this path — the Router has a better chain, ranked by an
  operator, and running a second one here would be two opinions about failover.
* **The Console's verdicts arrive.** Out of credits is a 402, an unknown tier
  is a 400, the per-run breaker is a 403. Each is an ANSWER and not an outage.
* **D57.7: no fallback to local litellm.** A routed call that fails, fails. A
  quiet fall back to the direct path would restore the very hole this closes,
  and it would do it exactly when the Console is refusing to be paid.

## Attribution costs the call sites nothing

``get_run_context()`` already carries ``user``, ``agent`` and ``source`` for
in-run code, which is the property ``_emit_usage``'s docstring records: *"an
in-run caller passes none of them"*. The routed call reads the same context, so
none of the 29 sites changes to gain per-member and per-app billing.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger

__all__ = [
    "MAX_STREAM_FRAME_BYTES",
    "OUT_OF_CREDITS",
    "RoutedRefusal",
    "RouterStreamBroken",
    "RouterStreamFrame",
    "RouterStreamParser",
    "completion_on_router",
    "router_wired",
    "routing_is_on",
    "run_attribution",
    "stream_on_router",
]

_log = get_logger("acb_llm.routed")

#: The ONE sentence for a member when the Router answers 402 (no credits).
#: The WhatsApp bot (``bot_run.REPLY_CREDITS``) and ``analyze_image``
#: (WS-VOX VOX-3) send it as it is. Fence: ``tests/unit/test_wac_bot_run.py``.
OUT_OF_CREDITS = (
    "Your organization is out of AI credits. An admin can add credits in "
    "Settings, Billing."
)

#: The OPTIONAL fields `customer_console.main.CompletionRequest` accepts.
#:
#: ⚠️ Named rather than derived, so widening it is an edit somebody
#: justifies. Deriving it from the Console would couple this package to that
#: service's imports, and `test_internal_ai_is_routed.py` checks the two agree
#: against the REAL model rather than against this list.
#:
#: 🔴 **`tools` IS here, and a first version wrongly said it was not.**
#: I read the model with a line-bounded grep, saw no `tools`, and concluded
#: tool-calling could not be routed. The field exists, along with
#: `tool_choice`, `parallel_tool_calls`, `reasoning_effort`, `seed` and
#: `thinking`. A test that asserts against the REAL model caught it.
#:
#: ⚠️ `model`, `messages`, `max_tokens` and `temperature` are set
#: explicitly above, and `stream` is deliberately absent — this path is the
#: non-streaming one, and sending `stream: true` here would ask the Console
#: for frames nothing reads. :func:`stream_on_router` sets `stream` itself
#: (task 3542), and only on its own payload.
_FORWARDABLE = (
    "task", "client_ref", "top_p", "n", "stop", "seed", "user",
    "presence_penalty", "frequency_penalty", "logit_bias", "response_format",
    "tools", "tool_choice", "parallel_tool_calls", "reasoning_effort",
    "thinking",
)


class RoutedRefusal(Exception):
    """The Router ANSWERED, and the answer was no.

    ⚠️ **Not an outage, and the distinction is load-bearing.** A 402 means the
    customer is out of credits, a 400 means the tier is unknown, a 403 is the
    per-run breaker. Flattening these into "the AI is down" would tell somebody
    to check the network when the real answer is that they must buy credits.

    Carries the Console's own status and detail so a caller can render the
    verdict rather than paraphrase it.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"router refused ({status}): {detail}")
        self.status = status
        self.detail = detail


def is_router_outage(exc: BaseException) -> bool:
    """True when the Router produced no answer (``ConsoleRouterUnavailable``).

    Task 3541: the email draft names an outage to the member. It asks here,
    because ``console_resolve`` has a fixed set of callers
    (``test_console_dependency_boundary.py``), and this module is one of them.
    """
    try:
        from acb_auth.console_resolve import ConsoleRouterUnavailable
    except Exception:  # noqa: BLE001 — no auth package, so no Router outage
        return False
    return isinstance(exc, ConsoleRouterUnavailable)


def routing_is_on() -> bool:
    """Whether an in-product completion should go through the Console.

    BOTH halves, and neither implies the other. ``router_serving_enabled`` is
    the owner's decision that customers are billed for AI. ``router_is_wired``
    is whether this box can reach the Console at all. A box with the flag on
    and no address must NOT silently serve unbilled.

    ⚠️ Imported lazily. ``acb_llm`` is imported by agents and scripts that have
    no reason to load the auth package, and a module-level import here would
    put the Console client in every one of them.
    """
    try:
        from acb_auth.console_resolve import router_is_wired
        from acb_common.settings import get_settings
    except Exception:
        return False
    settings = get_settings()
    return bool(getattr(settings, "router_serving_enabled", False)) and router_is_wired()


def router_wired() -> bool:
    """Whether this box can reach the Console Router at all. Never raises.

    The ``router_is_wired`` half of :func:`routing_is_on`, without the owner's
    serving flag. It makes no call, and a wiring it cannot read is not wired.
    The email startup check reads it (EM-T5b-2 item 9), because ``decide``
    rides the same Router and needs no serving flag. That check imports this
    module, so the gateway adds no importer of the Console client
    (``tests/unit/test_console_dependency_boundary.py``).
    """
    try:
        from acb_auth.console_resolve import router_is_wired

        return bool(router_is_wired())
    except Exception:
        return False


def run_attribution(*, module_slug: str | None = None) -> dict[str, Any]:
    """Who to bill and what to blame, from the ambient run context.

    ``module_slug`` is an explicit app that beats the run context, as
    :func:`completion_on_router` passes its ``source``. The agent backstop
    then names that app too.

    Public since WS-17 EM-T5 (CP-13e). The email shadow helper in
    ``gateway/decide_features.py`` passes these fields to ``acb_llm.decide``.
    ``_attribution`` stays as an alias for older callers.

    ⚠️ ``user`` is the run context's name for the member, and ``app`` for the
    app. The two vocabularies meet here and nowhere else, so the mapping is
    written once rather than at 29 call sites.

    🔴 **``app`` first, and ``source`` only as the fallback.** ``source`` is
    the SURFACE that started the run — ``chat``, ``workflows`` — so an agent
    run that bills by it bills an app called "chat". The executor now binds
    ``app`` from the agent's own ``config.json``, and
    ``test_usage_attribution.py`` fails if an agent declares none. ``source``
    stays as the fallback for the code that runs outside any agent, where it
    names the app: an email automation binds ``source="email"`` and no agent.

    ⚠️ **A member without an ``@`` is dropped**, exactly as
    :mod:`acb_llm.attribution` drops it, so the two routed paths cannot
    disagree about who a person is.

    🔴 **The agent has a backstop** (AI-call attribution, 2026-10-10).
    ``acb_common.attributed_agent`` is the one rule. An explicit agent wins,
    then the bound agent, then ``<module>.automation``. Before it, every
    background call with no agent bound reached the Router with ``agent``
    NULL, and the dashboard showed it as "not attributed".
    """
    try:
        from acb_common._log import get_run_context

        ctx = get_run_context()
    except Exception:
        ctx = {}
    member = str(ctx.get("user") or "").strip()
    member = member if "@" in member else ""
    module = module_slug or ctx.get("app") or ctx.get("source") or None
    try:
        from acb_common._log import attributed_agent

        agent = attributed_agent(ctx, module)
    except Exception:
        agent = ctx.get("agent") or None
    return {
        "member": member or None,
        # H-73: only the SESSION's member may decide a cap. This call runs in
        # the gateway's own process, so there is no header to sign: the run
        # context itself is the server-side fact.
        "member_proven": bool(member) and ctx.get("member_verified") == "1",
        "agent": agent,
        "module_slug": module,
        "run_id": ctx.get("run_id") or None,
    }


#: The old private name. Kept so an older import does not break.
_attribution = run_attribution


def router_tier(tier: str) -> str:
    """The name the Router knows for *tier*.

    🔴 **Measured on production, 2026-09-24.** `complete` and
    `complete_with_tools` pass `LLMTier.value`, which is the LEGACY id
    (`tier1`, `tier2`, `tier3`, `stt`). The Console's `tier_catalog` knows
    only the slate names, so every `LLMTier` caller got 400 `tier_unknown`
    from the moment `ROUTER_SERVING_ENABLED` was flipped. The Tasks assistant
    was the first to show it.

    ⚠️ **One seam, not 80 call sites** (H-44). The map is the inverse of
    `client._TIER_ALIAS_MAP`, the one vocabulary every other path reads, so a
    renamed tier cannot drift here. A slate name or a model id passes through
    untouched.
    """
    from acb_llm.client import _TIER_ALIAS_MAP

    legacy = {v: k for k, v in _TIER_ALIAS_MAP.items()}
    return legacy.get(tier) or PICKER_WIRE_IDS.get(tier) or tier


#: The chat picker's three wire ids, and the slate name each one means.
#:
#: 🔴 **Measured on production, 2026-09-24:** a chat sent `tier3-opus` and the
#: Router refused it with `tier_unknown`. `ai_metering_and_analytics.md` keeps
#: these ids on purpose ("the three wire ids do not move"), because a member's
#: saved choice holds one. So the hop translates them and neither side renames.
#: The control plane mirrors this map as `CONSOLE_SLUG_BY_WIRE_ID`.
PICKER_WIRE_IDS: dict[str, str] = {
    "tier1-local-qwen3": "tier-fast",
    "tier2-sonnet": "tier-balanced",
    "tier3-opus": "tier-powerful",
}


async def completion_on_router(
    *,
    tier: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    source: str | None = None,
    extra: dict[str, Any] | None = None,
    job: str | None = None,
) -> tuple[Any, str]:
    """One completion, served and BILLED by the Console. Returns ``(resp, model)``.

    ``job`` names the kind of work (:mod:`acb_llm.jobs`, task 1128). It sets
    thinking and the cap floor. ``thinking`` goes to the Console, which drops
    it for a chain step whose model does not take it. JSON mode gets the word
    "json" (:func:`acb_llm.jobs.ensure_json_word`). An answer with no text
    and no tool call logs ``acb_llm.empty_answer`` and is still returned.

    Raises:
        RoutedRefusal: the Router answered and refused. A verdict, not an
            outage — see the class.
        ConsoleRouterUnavailable: no answer was produced. **D57.7 forbids
            falling back to local litellm here**, so this propagates.
    """
    from acb_auth.console_resolve import chat_completion_on_console

    from acb_llm.jobs import note_empty_answer

    payload, attribution, max_tokens = _router_call(
        tier=tier, messages=messages, max_tokens=max_tokens,
        temperature=temperature, source=source, extra=extra, job=job,
    )
    status, body = await chat_completion_on_console(payload, **attribution)

    if status >= 400:
        raise _refusal(tier, status, body)

    # ⚠️ Rebuilt as the type the 29 call sites already hold. They read
    # `resp.choices[0].message.content`, and handing them a dict would turn a
    # working feature into an AttributeError at the far end of a refactor.
    from litellm import ModelResponse

    resp = ModelResponse(**body) if isinstance(body, dict) else body
    used = str(getattr(resp, "model", "") or body.get("model", tier))
    note_empty_answer(resp, model=used, job=job, max_tokens=max_tokens)
    return resp, used


def _router_call(
    *,
    tier: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    source: str | None,
    extra: dict[str, Any] | None,
    job: str | None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """The payload, the attribution and the cap of ONE Router call.

    :func:`completion_on_router` and :func:`stream_on_router` both build their
    call here, so the two cannot send different fields or bill a different
    member. Task 3542 moved these lines out of ``completion_on_router``, in
    the same order, and changed none of them.
    """
    from acb_llm.jobs import apply_job, ensure_json_word

    # Idempotent, so a seam that applied the job already changes nothing here.
    max_tokens, extra = apply_job(job, max_tokens=max_tokens, extra=dict(extra or {}))
    # JSON mode needs the word "json" in a message, or the vendor answers 400.
    # A direct caller of this seam (`shell/intent._fill`) gets the guard too.
    messages = ensure_json_word(messages, extra.get("response_format"))

    # An explicit `source` beats the inferred one: Custom Apps all run through
    # one module, so only the caller knows which app it is. It goes IN, so the
    # agent backstop names the same app as `module_slug`.
    attribution = run_attribution(module_slug=source or None)

    # 🔴 **Built against `CompletionRequest`, which is `extra="forbid"`.**
    # A first version sent `{"tier": ...}` and spread the caller's `**extra`.
    # Both are 422s: the field is called `model` and it carries the TIER ALIAS,
    # and an unknown key is refused rather than ignored. Nothing caught it,
    # because the test stubbed the client — a fake agrees with whatever it is
    # handed, which is exactly what R8 exists to say.
    #
    # ⚠️ `model` holds `tier-fast`, not a resolved model id. The Console reads
    # its own `tier_binding` chain, which an operator ordered and which fails
    # over. Resolving here would take that away.
    payload: dict[str, Any] = {
        "model": router_tier(tier),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # ⚠️ **An allowlist, never a spread.** The caller's `extra` carries
    # litellm transport options — `timeout`, `cache`, `prompt_cache_key` — and
    # forwarding those to a `forbid` model refuses the whole call. Only the
    # keys `CompletionRequest` names may travel, and the rest stay local.
    for key in _FORWARDABLE:
        if extra and key in extra and extra[key] is not None:
            payload[key] = extra[key]
    return payload, attribution, max_tokens


def _refusal(tier: str, status: int, body: Any, *, stream: bool = False) -> RoutedRefusal:
    """The :class:`RoutedRefusal` of a Router verdict, logged once.

    ``detail`` is the Console's own text, as the caller may render it. The
    streamed call adds ``stream=True`` to the line, and the buffered call
    logs the line it always did.
    """
    detail = ""
    if isinstance(body, dict):
        raw = body.get("detail")
        detail = raw if isinstance(raw, str) else str(raw or "")
    _log.warning(
        "acb_llm.router_refused",
        router_tier=tier, router_status=status, router_detail=detail[:200],
        **({"stream": True} if stream else {}),
    )
    return RoutedRefusal(status, detail or "no reason given")


# ── The streamed call (task 3542) ───────────────────────────────────────────
#
# The Router streams (CP-4b, 2026-08-27). `console_resolve.
# stream_completion_on_console` yields its raw SSE bytes, and the gateway's
# `/v1` relay hands them on unread. This is the one reader of those bytes in
# `acb_llm`. `context._routed_text` drives it, with the three stream bounds.
#
# ⚠️ **The Console meters the stream, and this side meters nothing.** The
# Console asks the provider for the usage frame itself
# (`main._stream_kwargs_for`, `stream_options.include_usage`), and
# `router.relay_stream` writes one usage row in its `finally`, on a clean end
# and on a client that left. `_streamed_completion` gives the hold back in its
# own `finally`. So this client sends `stream: true` and nothing more, as the
# `/v1` relay does, and it calls no `_emit_usage`. The buffered
# `completion_on_router` emits none either.
#
# ⚠️ **A stream that ends early meters only what the Console saw.** The usage
# frame comes last, just before `[DONE]`. When the caller cancels, or a bound
# ends the stream, this client closes it, and the Console closes the provider
# stream. Its row then holds the counts seen before the close, which can be
# zero. The buffered call, as read in the code and not run, goes on after its
# caller leaves: `main.chat_completions` is a sync route that runs
# `call_chain` to its end, so the Console meters all of it. The early-end
# rule is the Console's (CP-4b done-when 3), and the `/v1` relay meets it too.

#: The largest SSE line, or the data of one event, that the parser holds. A
#: chunk frame of a draft is under 1 KB, and a whole answer of 3,000 tokens is
#: about 12 KB. A stream past this cap is broken, and it ends the call rather
#: than grow the buffer without end.
MAX_STREAM_FRAME_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class RouterStreamFrame:
    """One ``data:`` frame of a routed stream, cut to what the arm reads.

    ``reasoning`` and ``content`` are the two deltas of the first choice, as
    the direct arm reads them from a litellm chunk. A frame with neither (the
    role frame, the ``finish_reason`` frame, the usage frame) is still a frame,
    because any frame is a sign of life for the idle bound.
    """

    reasoning: str = ""
    content: str = ""
    model: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


class RouterStreamBroken(Exception):
    """The Router's bytes cannot be read as one whole answer.

    ``code`` is one of a closed set of our own words, and it is the whole
    message. It never holds text of the frame: a provider error can quote the
    request, and each caller logs ``str(exc)``.

    * ``stream_error_frame``: a frame carried an ``error`` object.
    * ``stream_malformed_frame``: the data of a frame is not a JSON object.
    * ``stream_frame_too_large``: a line or an event passed
      :data:`MAX_STREAM_FRAME_BYTES`.
    * ``stream_truncated``: the bytes ended before ``[DONE]``.
    * ``stream_never_opened``: ``[DONE]`` came before any data frame.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RouterStreamParser:
    """Turn the Router's SSE bytes into frames. Pure: no I/O and no clock.

    ⚠️ **The bytes do not align with frames.** ``stream_completion_on_console``
    relays them as ``aiter_bytes`` gives them. So the parser keeps the bytes
    of a part line, splits on ``\\n`` (a ``\\r`` before it goes too), and
    decodes a whole frame only. A UTF-8 character split across two chunks is
    then whole when it is read.

    The SSE rules it keeps: a blank line ends an event, the ``data:`` lines of
    one event join with ``\\n``, one space after the colon goes, a line that
    starts with ``:`` is a comment (a keepalive), and each other field
    (``event``, ``id``, ``retry``) is not read. A comment makes no frame, so
    it never counts as a sign of life for the idle bound.
    """

    def __init__(self, *, max_frame_bytes: int = MAX_STREAM_FRAME_BYTES) -> None:
        self._buf = bytearray()
        self._data: list[bytes] = []
        self._data_bytes = 0
        self._max = max_frame_bytes
        #: ``[DONE]`` arrived. The parser reads nothing after it.
        self.done = False
        #: The data frames read, ``[DONE]`` not counted.
        self.frames = 0

    def feed(self, chunk: bytes) -> list[RouterStreamFrame]:
        """The frames that *chunk* completes. Raises :class:`RouterStreamBroken`."""
        if self.done:
            return []
        self._buf.extend(chunk)
        out: list[RouterStreamFrame] = []
        if b"\n" in chunk:
            *lines, rest = bytes(self._buf).split(b"\n")
            self._buf = bytearray(rest)
            for line in lines:
                frame = self._line(line[:-1] if line.endswith(b"\r") else line)
                if frame is not None:
                    out.append(frame)
                if self.done:
                    self._buf = bytearray()
                    return out
        if len(self._buf) + self._data_bytes > self._max:
            raise RouterStreamBroken("stream_frame_too_large")
        return out

    def end(self) -> None:
        """The bytes ended. Raise unless they held a data frame, then ``[DONE]``.

        ⚠️ A lone ``[DONE]`` is not an empty answer. The Console sends exactly
        that when every step of the chain failed before a frame existed
        (``main._stream_closed``), where the buffered call gets a 502.
        """
        if not self.done:
            raise RouterStreamBroken("stream_truncated")
        if self.frames == 0:
            raise RouterStreamBroken("stream_never_opened")

    def _line(self, line: bytes) -> RouterStreamFrame | None:
        if not line:
            return self._dispatch()
        if line.startswith(b":"):
            return None
        name, _sep, value = line.partition(b":")
        if name != b"data":
            return None
        if value.startswith(b" "):
            value = value[1:]
        self._data.append(value)
        self._data_bytes += len(value)
        if self._data_bytes > self._max:
            raise RouterStreamBroken("stream_frame_too_large")
        return None

    def _dispatch(self) -> RouterStreamFrame | None:
        if not self._data:
            return None
        data = b"\n".join(self._data)
        self._data, self._data_bytes = [], 0
        if data.strip() == b"[DONE]":
            self.done = True
            return None
        try:
            body = json.loads(data)
        except (ValueError, RecursionError):  # bad UTF-8 is a ValueError too
            raise RouterStreamBroken("stream_malformed_frame") from None
        if not isinstance(body, dict):
            raise RouterStreamBroken("stream_malformed_frame")
        if body.get("error"):
            # The OpenAI client's rule for a frame: a truthy `error` fails it.
            raise RouterStreamBroken("stream_error_frame")
        self.frames += 1
        return _frame_of(body)


def _frame_of(body: dict[str, Any]) -> RouterStreamFrame:
    """The deltas of the first choice, read as ``context._stream_text`` reads a
    litellm chunk: each one only when it is truthy, and as ``str``."""
    reasoning = content = ""
    finish: str | None = None
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first = choices[0]
        raw_finish = first.get("finish_reason")
        finish = raw_finish if isinstance(raw_finish, str) else None
        delta = first.get("delta")
        if isinstance(delta, dict):
            piece = delta.get("reasoning_content")
            reasoning = str(piece) if piece else ""
            piece = delta.get("content")
            content = str(piece) if piece else ""
    model = body.get("model")
    usage = body.get("usage")
    return RouterStreamFrame(
        reasoning=reasoning,
        content=content,
        model=model if isinstance(model, str) else "",
        finish_reason=finish,
        usage=usage if isinstance(usage, dict) else None,
    )


async def stream_on_router(
    *,
    tier: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    source: str | None = None,
    extra: dict[str, Any] | None = None,
    job: str | None = None,
) -> AsyncIterator[RouterStreamFrame]:
    """One completion, STREAMED and billed by the Console. Yields each frame.

    The call is :func:`completion_on_router`'s: the same payload, the same
    attribution and the same job rules (:func:`_router_call`), with
    ``stream: true``. The generator ends at ``[DONE]``, and the caller reads
    the text from the frames. It holds no clock: ``context._routed_text``
    bounds it.

    Raises:
        RoutedRefusal: the Router answered with a status that is not 200,
            before the first byte. 402 is out of credits, 400 an unknown tier,
            403 the breaker, as on the buffered call.
        ConsoleRouterUnavailable: no whole answer came. The Router was not
            reached, the connection broke, or the bytes broke
            (:class:`RouterStreamBroken`, whose code is the message).
            ``stream_completion_on_console`` raises this class for a break
            in the middle of a stream, so a broken frame takes it too.

    ⚠️ **D57.7.** Nothing here falls back to litellm or to the buffered call.
    A second call would be a second bill for one answer.

    ⚠️ **The Console stream is always closed**, in the ``finally``: at
    ``[DONE]``, on an error, on a bound, and when the caller cancels or closes
    this generator. A cancel that comes while this waits for bytes unwinds the
    Console generator itself. The close covers the rest.
    """
    from acb_auth.console_resolve import (
        ConsoleRouterUnavailable,
        ConsoleRouterVerdict,
        stream_completion_on_console,
    )

    payload, attribution, _max_tokens = _router_call(
        tier=tier, messages=messages, max_tokens=max_tokens,
        temperature=temperature, source=source, extra=extra, job=job,
    )
    payload["stream"] = True
    raw = stream_completion_on_console(payload, **attribution)
    parser = RouterStreamParser()
    try:
        try:
            async for chunk in raw:
                for frame in parser.feed(chunk):
                    yield frame
                if parser.done:
                    break
            parser.end()
        except ConsoleRouterVerdict as verdict:
            raise _refusal(tier, verdict.status, verdict.body, stream=True) from None
        except RouterStreamBroken as broken:
            _log.warning(
                "acb_llm.router_stream_broken",
                router_tier=tier, reason=broken.code, frames=parser.frames,
            )
            raise ConsoleRouterUnavailable(broken.code) from broken
    finally:
        await _aclose_quietly(raw)


async def _aclose_quietly(stream: Any) -> None:
    """Close the Console stream. Bounded, and a failed close changes nothing."""
    with contextlib.suppress(Exception):
        await asyncio.wait_for(stream.aclose(), timeout=1.0)
