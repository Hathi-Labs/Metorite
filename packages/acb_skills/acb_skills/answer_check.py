"""The number check of a chat answer that read a CRM tool (WS-53 CRM-Q29).

Spec: ``project-docs/specs/crm_platform.md`` §18.11.2, CRM-Q29.

SQL gives each CRM number (CRM-Q25), but the model writes the sentence around
it. So the model can misquote a number, round it wrongly, add two results
itself or invent one. This module checks each number of an answer against the
numbers that the run's tools gave, before the member reads the answer.

**The seam.** ``orchestrator._tool_injection._inject_agent_tools`` puts
:class:`AnswerCheckProvider` on the per-run view of a native MAF agent that
holds a CRM tool. That covers the stream run, the batch run and the
sub-agent. The provider adds two middlewares to ONE run:

* :class:`RecordToolResults` (function) keeps the text of each tool result of
  the run, in memory. A CRM tool result arms the check.
* :class:`CheckAnswers` (chat) does nothing until the check arms. Then, with
  the mode ``on``, it holds each model response until the response ends, and
  checks its text with ``acb_common.number_check.check``.

**The behaviour on a mismatch (item 7).** The first failed answer of a run
gets ONE more model request, which names each number that matched no source.
The check reads that answer too. A second failure, or a failure after the run
used its retry, gives :func:`fallback_text`: a line that says so, and the
figures of the CRM tools as they came. The failed draft never goes out. A
failed step that also calls a tool loses its text and keeps its tool call. So
a run makes one extra model request at most, and a passed check makes none.

**The mode.** :func:`answer_check_mode` is the one reader of
``CRM_ANSWER_CHECK``: ``off`` (the default), ``shadow`` or ``on``. ``shadow``
checks each armed response at its end and logs, with no hold and no retry.

No log line holds a number or a text. Fence: ``tests/unit/test_crm_answer_check.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger
from acb_common.number_check import CheckResult, Sources, check
from agent_framework import (
    ChatMiddleware,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    ContextProvider,
    FunctionMiddleware,
    Message,
    ResponseStream,
)

from acb_skills.crm_tools import CRM_TOOL_NAMES
from acb_skills.tool_guard import tool_name

_log = get_logger("acb_skills.answer_check")

SOURCE_ID = "crm_answer_check"

OFF = "off"
SHADOW = "shadow"
ON = "on"
MODES: tuple[str, ...] = (OFF, SHADOW, ON)

#: The four reads of ``crm-assistant`` (``apps/agents/agent-crm``). They are
#: CRM tools too, so their results arm the check.
CRM_ASSISTANT_READS: frozenset[str] = frozenset({
    "search_crm", "get_pipeline", "get_record", "get_timeline",
})

#: A result of one of these tools arms the check (item 5).
ARM_TOOLS: frozenset[str] = CRM_TOOL_NAMES | CRM_ASSISTANT_READS

#: The tools whose results the fallback shows. Each is a figure tool, and
#: none of them returns text that a stranger wrote.
FALLBACK_TOOLS: frozenset[str] = frozenset({
    "crm_aggregate", "crm_report", "crm_stage_dwell", "get_pipeline",
})

#: The most numbers that the retry request names.
MAX_NAMED = 8
#: The newest results of :data:`FALLBACK_TOOLS` that the fallback shows.
FALLBACK_RESULTS = 3
#: The most characters of one result in the fallback.
FALLBACK_CHARS = 2000

#: What the member reads in place of an answer that failed the check.
FALLBACK_LEAD = (
    "I could not match each number of my answer to the CRM data, so I do not "
    "show that answer."
)
FALLBACK_FIGURES = "These are the CRM figures as the tools gave them:"
FALLBACK_NONE = "Ask again, or ask for one figure at a time."


def answer_check_mode() -> str:
    """The mode of the check, from ``CRM_ANSWER_CHECK``. Unknown reads ``off``."""
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "crm_answer_check", OFF) or OFF)
    except Exception:  # a broken settings load never turns the check on
        return OFF
    mode = raw.strip().lower()
    return mode if mode in MODES else OFF


def holds_crm_tool(names: Iterable[str]) -> bool:
    """True when *names* holds a tool whose result arms the check."""
    return any(name in ARM_TOOLS for name in names)


# ── The text of a result ────────────────────────────────────────────────────

def _result_text(result: Any) -> str:
    """The text of a tool result, in any shape MAF hands a middleware."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (list, tuple)):
        return "\n".join(_result_text(item) for item in result)
    text = getattr(result, "text", None)
    if isinstance(text, str) and text:
        return text
    inner = getattr(result, "result", None)
    if inner is not None and inner is not result:
        return _result_text(inner)
    return str(result)


def _role(message: Any) -> str:
    role = getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


def _message_sources(messages: Sequence[Any]) -> list[str]:
    """The member's messages and the tool results of a request (item 4).

    A text that the model wrote (an ``assistant`` message) is never a source.
    """
    texts: list[str] = []
    for message in messages or ():
        role = _role(message)
        if role == "user":
            texts.append(str(getattr(message, "text", "") or ""))
        elif role == "tool":
            for content in getattr(message, "contents", None) or ():
                if getattr(content, "type", "") == "function_result":
                    texts.append(_result_text(getattr(content, "result", None)))
    return texts


# ── The ledger of ONE run ───────────────────────────────────────────────────

@dataclass
class RunLedger:
    """What one run has read, in memory, for that run only (item 4).

    ``armed`` turns true at the first CRM tool result. ``retried`` turns true
    when the run spends its one retry.
    """

    results: list[tuple[str, str]] = field(default_factory=list)
    armed: bool = False
    retried: bool = False

    def record(self, tool: str, text: str) -> None:
        """Keep the text of one tool result. A CRM result arms the check."""
        self.results.append((tool, text))
        if tool in ARM_TOOLS:
            self.armed = True

    def sources(self, messages: Sequence[Any]) -> Sources:
        """Each source of the run: its tool results and the request (item 4)."""
        book = Sources()
        for _tool, text in self.results:
            book.add_text(text)
        for text in _message_sources(messages):
            book.add_text(text)
        return book

    def check(self, text: str, messages: Sequence[Any]) -> CheckResult:
        """Check *text* against the sources of the run."""
        return check(text, self.sources(messages))


def retry_note(result: CheckResult) -> str:
    """The text of the one retry request. It names each failed number."""
    named = ", ".join(f'"{f.text}"' for f in result.unmatched[:MAX_NAMED])
    return (
        "A check of your answer found numbers that no tool result of this turn "
        f"holds: {named}. Write the answer again. Copy each number from a tool "
        "result, or round it and keep its unit. Do not add, subtract or divide "
        "tool numbers yourself. For a new total, call crm_aggregate with the "
        "right group or filter, or leave the number out. Write each figure in "
        "digits."
    )


def fallback_text(ledger: RunLedger) -> str:
    """What the member reads when the answer failed the check (item 7)."""
    shown = [(t, x) for t, x in ledger.results if t in FALLBACK_TOOLS and x.strip()]
    shown = shown[-FALLBACK_RESULTS:]
    if not shown:
        return f"{FALLBACK_LEAD} {FALLBACK_NONE}"
    blocks = []
    for _tool, text in shown:
        body = text.strip()
        if len(body) > FALLBACK_CHARS:
            body = body[:FALLBACK_CHARS].rstrip() + "\n…"
        blocks.append(body)
    return f"{FALLBACK_LEAD} {FALLBACK_FIGURES}\n\n" + "\n\n".join(blocks)


# ── The two middlewares ─────────────────────────────────────────────────────

class RecordToolResults(FunctionMiddleware):
    """Keeps the text of each tool result of ONE run in its ledger."""

    def __init__(self, ledger: RunLedger) -> None:
        self._ledger = ledger

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        await call_next()
        function = getattr(context, "function", None)
        name = tool_name(function) if function is not None else ""
        try:
            self._ledger.record(name, _result_text(getattr(context, "result", None)))
        except Exception as exc:  # the record never breaks a tool call
            _log.warning("answer_check.record_failed", tool=name, error=type(exc).__name__)


def _has_call(response: ChatResponse) -> bool:
    return any(
        getattr(c, "type", "") == "function_call"
        for m in response.messages for c in (m.contents or ())
    )


def _no_text(contents: Sequence[Any]) -> list[Any]:
    return [c for c in contents if getattr(c, "type", "") != "text"]


def _strip_updates(updates: Sequence[ChatResponseUpdate]) -> list[ChatResponseUpdate]:
    """*updates* with each text taken out. The tool calls and the rest stay."""
    out: list[ChatResponseUpdate] = []
    for update in updates:
        kept = _no_text(update.contents)
        if kept:
            out.append(ChatResponseUpdate(
                contents=kept, role=update.role, response_id=update.response_id,
                message_id=update.message_id, model=update.model,
                finish_reason=update.finish_reason,
            ))
    return out


class CheckAnswers(ChatMiddleware):
    """Checks each model response of ONE armed run (items 6, 7 and 10)."""

    def __init__(self, ledger: RunLedger, mode: str) -> None:
        self._ledger = ledger
        self._mode = mode

    def _log(self, result: CheckResult, outcome: str) -> None:
        # The run id comes from the bound run context (``acb_common._log``).
        _log.info(
            "answer_check.checked", mode=self._mode,
            numbers=len(result.figures), mismatches=len(result.unmatched),
            outcome=outcome,
        )

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        if not self._ledger.armed:
            await call_next()
            return
        if self._mode == SHADOW:
            await call_next()
            self._shadow(context)
            return
        await call_next()
        if isinstance(context.result, ResponseStream):
            # Take the inner stream now: the next line puts the held one in
            # its place, and the held one reads the inner one.
            context.result = ResponseStream(
                self._held(context, call_next, context.result),
                finalizer=ChatResponse.from_updates,
            )
        elif isinstance(context.result, ChatResponse):
            context.result = await self._checked(context, call_next, context.result)

    # shadow: check at the end, change nothing

    def _shadow(self, context: Any) -> None:
        messages = list(context.messages)

        def _look(response: ChatResponse) -> ChatResponse:
            try:
                result = self._ledger.check(response.text, messages)
                self._log(result, "shadow_pass" if result.ok else "shadow_mismatch")
            except Exception as exc:
                _log.warning("answer_check.shadow_failed", error=type(exc).__name__)
            return response

        if isinstance(context.result, ResponseStream):
            context.result.with_result_hook(_look)
        elif isinstance(context.result, ChatResponse):
            _look(context.result)

    # on: hold, check, retry once, then the fallback

    async def _held(
        self, context: Any, call_next: Callable[[], Any], stream: ResponseStream,
    ) -> AsyncIterator[ChatResponseUpdate]:
        updates = [u async for u in stream]
        response = await stream.get_final_response()
        messages = list(context.messages)
        result = self._ledger.check(response.text, messages)
        if result.ok:
            self._log(result, "pass")
            for update in updates:
                yield update
            return
        if _has_call(response):
            self._log(result, "step_text_removed")
            for update in _strip_updates(updates):
                yield update
            return
        if not self._ledger.retried:
            self._ledger.retried = True
            self._log(result, "retry")
            context.messages = [
                *messages,
                Message("assistant", [response.text]),
                Message("user", [retry_note(result)]),
            ]
            await call_next()
            retry = context.result
            retry_updates = [u async for u in retry]
            second = await retry.get_final_response()
            again = self._ledger.check(second.text, messages)
            if again.ok or _has_call(second):
                self._log(again, "retry_pass" if again.ok else "retry_call")
                for update in retry_updates if again.ok else _strip_updates(retry_updates):
                    yield update
                return
            result, response = again, second
        self._log(result, "fallback")
        last = updates[-1] if updates else None
        yield ChatResponseUpdate(
            contents=[Content.from_text(fallback_text(self._ledger))],
            role="assistant",
            response_id=getattr(last, "response_id", None) or response.response_id,
            message_id=getattr(last, "message_id", None),
            model=getattr(last, "model", None),
            finish_reason="stop",
        )

    async def _checked(
        self, context: Any, call_next: Callable[[], Any], response: ChatResponse,
    ) -> ChatResponse:
        messages = list(context.messages)
        result = self._ledger.check(response.text, messages)
        if result.ok:
            self._log(result, "pass")
            return response
        if _has_call(response):
            self._log(result, "step_text_removed")
            return _without_text(response)
        if not self._ledger.retried:
            self._ledger.retried = True
            self._log(result, "retry")
            context.messages = [
                *messages,
                Message("assistant", [response.text]),
                Message("user", [retry_note(result)]),
            ]
            await call_next()
            second = context.result
            again = self._ledger.check(second.text, messages)
            if again.ok:
                self._log(again, "retry_pass")
                return second
            if _has_call(second):
                self._log(again, "retry_call")
                return _without_text(second)
            result, response = again, second
        self._log(result, "fallback")
        return ChatResponse(
            messages=[Message("assistant", [fallback_text(self._ledger)])],
            response_id=response.response_id, model=response.model,
            finish_reason="stop",
        )


def _without_text(response: ChatResponse) -> ChatResponse:
    """*response* with its text taken out and its tool calls kept."""
    messages = [
        Message(
            _role(m) or "assistant", _no_text(m.contents or ()),
            author_name=getattr(m, "author_name", None),
            message_id=getattr(m, "message_id", None),
        )
        for m in response.messages
    ]
    return ChatResponse(
        messages=messages, response_id=response.response_id,
        model=response.model, finish_reason=response.finish_reason,
        usage_details=response.usage_details,
    )


class AnswerCheckProvider(ContextProvider):
    """Adds the two middlewares above to ONE run of a native MAF agent.

    ``_tool_injection._inject_agent_tools`` attaches it to the run's own view
    of the agent (``agent_with_providers``), never to a shared agent object.
    Each ``run`` call gets a new ledger, so nothing carries from one run to
    the next.
    """

    def __init__(self, mode: str) -> None:
        super().__init__(SOURCE_ID)
        self.mode = mode
        self.ledger = RunLedger()

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        self.ledger = RunLedger()
        context.extend_middleware(
            SOURCE_ID,
            [CheckAnswers(self.ledger, self.mode),
             RecordToolResults(self.ledger)],
        )


__all__ = [
    "ARM_TOOLS", "CRM_ASSISTANT_READS", "FALLBACK_TOOLS", "MODES", "OFF", "ON",
    "SHADOW", "AnswerCheckProvider", "CheckAnswers", "RecordToolResults",
    "RunLedger", "answer_check_mode", "fallback_text", "holds_crm_tool",
    "retry_note",
]
