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
  the run, and the arguments of each CRM tool call, in memory. A CRM tool
  result arms the check.
* :class:`CheckAnswers` (chat) does nothing until the check arms. Then, with
  the mode ``on``, it holds each model response until the response ends, and
  checks its text and the shown text of each display call (a card, a report,
  a document, a WhatsApp element) with ``acb_common.number_check.check``.

**The sources (item 4).** The tool results of the run (no delegated answer,
which a model wrote), the arguments of the run's CRM tool calls (a date gives
its count of days to today), and the member's own words of this turn
(:data:`MEMBER_MESSAGE` in the run context). Never the packed prompt string,
which holds the earlier answers of the model.

**The behaviour on a mismatch (item 7).** The first failed answer of a run
gets ONE more model request, which names each number that matched no source.
The check reads that answer too. A second failure, or a failure after the run
used its retry, gives :func:`fallback_text`: a line that says so, and the
figures of the CRM tools as they came. The failed draft and a failed card
never go out. A failed step that calls only tools that show nothing loses its
text and keeps its tool calls. So a run makes one extra model request at
most, and a passed check makes none.

**The hold and the watchdog (item 10).** While it holds a response, the check
puts a :data:`HOLD_EVENT` on the run queue at most each
:data:`HEARTBEAT_S` seconds, and only when the model sent an update. The
executor reads it as a sign of life, not as output, and shows nothing. A
model that stops sending still meets the idle watchdog.

**The mode.** ``acb_common.settings.crm_answer_check_mode`` is the one reader
of ``CRM_ANSWER_CHECK``: ``off`` (the default), ``shadow`` or ``on``.
``shadow`` checks each armed response at its end and logs, with no hold and
no retry.

No log line holds a number or a text. Fence: ``tests/unit/test_crm_answer_check.py``.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from acb_common import get_logger
from acb_common.number_check import CheckResult, Sources, check
from acb_common.settings import crm_answer_check_mode
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
from acb_skills.egress import DELEGATION_TOOLS, is_delegation_tool
from acb_skills.tool_guard import tool_name

_log = get_logger("acb_skills.answer_check")

SOURCE_ID = "crm_answer_check"

OFF = "off"
SHADOW = "shadow"
ON = "on"
MODES: tuple[str, ...] = (OFF, SHADOW, ON)

#: The one reader of ``CRM_ANSWER_CHECK`` lives in ``acb_common.settings``.
answer_check_mode = crm_answer_check_mode

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

#: The tools whose ARGUMENTS the member reads: a card, a report, a document,
#: a text file, a WhatsApp element (``whatsapp_ui``, fix round 3), and the
#: question and confirmation cards (``ask_questions``,
#: ``request_confirmation``, fix round 4). The check reads their shown text
#: as it reads the answer.
DISPLAY_TOOLS: frozenset[str] = frozenset({
    "emit_generative_ui", "make_report", "make_document", "write_artifact",
    "whatsapp_ui", "ask_questions", "request_confirmation",
})

#: The memory reads. They show nothing and send nothing.
RECALL_TOOLS: frozenset[str] = frozenset({
    "remember", "recall_timeline", "recall_agent", "recall_org", "recall_notes",
})

#: The ONLY tools that a failed step may call and still go out without its
#: text (fix round 4). Each one shows the member nothing and sends nothing:
#: the CRM reads and the memory reads. A failed step that calls any other
#: tool fails, so a card, a file, a message or a tool that this list does
#: not know never ships beside a failed text. A new quiet tool joins here.
QUIET_TOOLS: frozenset[str] = ARM_TOOLS | RECALL_TOOLS

#: A tool result that a model wrote, or a script that a model wrote printed.
#: It is no source (fix round 4): ``code_task`` runs a coding agent, as a
#: delegation does, and the others print what the model's own code prints.
#: So a number that only such a result holds fails the check.
MODEL_WRITTEN_TOOLS: frozenset[str] = DELEGATION_TOOLS | frozenset({
    "code_task", "run_script", "run_command", "code_task_run_command",
})

#: A result longer than this many characters is read on a worker thread, so
#: a result with 100,000 figures does not hold the event loop (fix round 4).
RECORD_INLINE_CHARS = 20_000

#: ``write_artifact`` shows text only for these files. A script is code.
TEXT_FILE_SUFFIXES: tuple[str, ...] = (".md", ".txt", ".html", ".htm", ".csv")

#: The keys of a card or report spec whose number is layout, not a figure.
LAYOUT_KEYS: frozenset[str] = frozenset({
    "width", "height", "span", "col_span", "colspan", "rowspan", "cols",
    "size", "level", "order", "index", "position", "version", "gap", "padding",
    "margin", "radius", "opacity", "font_size", "fontsize", "decimals",
    "precision", "limit", "page", "page_size", "z_index", "zindex", "duration",
    "delay", "step", "max_items", "min_width", "max_width",
})

#: The keys of a spec whose text is not shown: a kind, an id, a style, a link.
HIDDEN_KEYS: frozenset[str] = frozenset({
    "type", "id", "key", "icon", "color", "colour", "variant", "surface",
    "template", "component", "classname", "class", "style", "href", "url",
    "src", "format", "path", "encoding", "hitl", "kind", "slug",
})

#: The key in the run context that holds the member's own words of this turn.
MEMBER_MESSAGE = "member_message"
#: The most characters of the member's words that the run context keeps.
MEMBER_MESSAGE_CHARS = 20_000

#: The CUSTOM event that says "the check holds an answer, the model is busy".
HOLD_EVENT = "answer_check.held"
#: The least time between two :data:`HOLD_EVENT` events of one run.
HEARTBEAT_S = 15.0

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

_ISO_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_TAG = re.compile(r"<[^>]*>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.I | re.S)
#: The shape of code, not a word of prose. "An expected return of ₹5 lakh"
#: and "import duties" are prose, so the check reads their numbers (fix
#: round 3). A string of this shape shows nothing the check can read.
_CODE_LIKE = re.compile(
    r"=>"
    r"|\bfunction\s*\w*\s*\("
    r"|^\s*import\s+[\w{*][^\n]*\bfrom\s+['\"]"
    r"|^\s*(?:import|from)\s+[\w.]+(?:\s+import\b|\s*$)"
    r"|\b(?:const|let|var)\s+\w+\s*="
    r"|\breturn\s*[(<{\[]"
    r"|\breturn\s+[\w.$\[\]]+\s*;",
    re.MULTILINE,
)


def holds_crm_tool(names: Iterable[str]) -> bool:
    """True when *names* holds a tool whose result arms the check."""
    return any(name in ARM_TOOLS for name in names)


def member_words(parent: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """The member's own words of a run, for :data:`MEMBER_MESSAGE`.

    A delegated run (its *parent* run context names a run) takes the
    parent's words. Its own payload is a message that the parent's model
    wrote, so it is never a source. Pass the context that the run READ
    before it set its own, because a run derives keys into its context
    early (H-236 ``no_egress``).
    """
    if parent.get("run_id"):
        return str(parent.get(MEMBER_MESSAGE) or "")
    words = payload.get("message") or payload.get("user_query") or ""
    return str(words)[:MEMBER_MESSAGE_CHARS]


# ── The text of a result, of the arguments, of a display call ──────────────

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


def _as_mapping(arguments: Any) -> Any:
    """The arguments of a call as plain data. A JSON string is parsed."""
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except ValueError:
            return arguments
    dump = getattr(arguments, "model_dump", None)
    if callable(dump):
        return dump()
    if isinstance(arguments, Mapping):
        return dict(arguments)
    return arguments


def _days_from(value: str, today: date) -> int | None:
    match = _ISO_DAY.match(value.strip())
    if not match:
        return None
    try:
        day = date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None
    return abs((today - day).days)


def argument_sources(arguments: Any, today: date | None = None) -> list[str]:
    """The numbers that the model chose as the arguments of a CRM call.

    "Top 5" after ``page_size=5`` and "the last 7 days" after
    ``updated_from=<a date 7 days ago>`` are true. So each value is a source,
    a date gives its count of days to *today*, and two dates give the days
    between them.
    """
    today = today or date.today()
    out: list[str] = []
    days: list[date] = []

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for value in node.values():
                _walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                _walk(value)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float)):
            out.append(str(node))
        elif isinstance(node, str):
            out.append(node)
            count = _days_from(node, today)
            if count is not None:
                out.append(str(count))
                match = _ISO_DAY.match(node.strip())
                if match:
                    days.append(date(int(match[1]), int(match[2]), int(match[3])))

    _walk(_as_mapping(arguments))
    for i, first in enumerate(days):
        for second in days[i + 1:]:
            out.append(str(abs((second - first).days)))
    return out


def _shown_string(text: str) -> str:
    """The text that a string of a spec shows. HTML loses its tags, and
    code shows nothing that the check can read."""
    if "<" in text and ">" in text:
        return _TAG.sub(" ", _SCRIPT.sub(" ", text))
    if _CODE_LIKE.search(text):
        return ""
    return text


def _shown_text(node: Any, key: str = "") -> list[str]:
    """Each shown string and figure of a card or report spec."""
    name = key.lower()
    if isinstance(node, Mapping):
        out: list[str] = []
        for k, value in node.items():
            out.extend(_shown_text(value, str(k)))
        return out
    if isinstance(node, (list, tuple)):
        return [part for value in node for part in _shown_text(value, key)]
    if isinstance(node, bool) or node is None:
        return []
    if isinstance(node, (int, float)):
        return [] if name in LAYOUT_KEYS else [str(node)]
    if isinstance(node, str):
        if name in HIDDEN_KEYS:
            return []
        stripped = node.strip()
        if stripped[:1] in "{[":
            try:
                return _shown_text(json.loads(stripped), key)
            except ValueError:
                pass
        return [_shown_string(node)]
    return []


def display_text(name: str, arguments: Any) -> str:
    """The text that a display call shows the member, or "" (item 2)."""
    if name not in DISPLAY_TOOLS:
        return ""
    data = _as_mapping(arguments)
    if name == "write_artifact":
        if not isinstance(data, Mapping):
            return ""
        path = str(data.get("path") or "").lower()
        content = data.get("content")
        if not path.endswith(TEXT_FILE_SUFFIXES) or not isinstance(content, str):
            return ""
        return _shown_string(content) if path.endswith((".html", ".htm")) else content
    return "\n".join(part for part in _shown_text(data) if part)


def _calls(response: ChatResponse) -> list[Any]:
    return [
        c for m in response.messages for c in (m.contents or ())
        if getattr(c, "type", "") == "function_call"
    ]


def _card_text(response: ChatResponse) -> str:
    return "\n".join(
        part for c in _calls(response)
        if (part := display_text(str(getattr(c, "name", "") or ""),
                                 getattr(c, "arguments", None)))
    )


# ── The ledger of ONE run ───────────────────────────────────────────────────

@dataclass
class RunLedger:
    """What one run has read, in memory, for that run only (item 4).

    ``armed`` turns true at the first CRM tool result. ``retried`` turns true
    when the run spends its one retry. ``results`` holds the newest results
    of :data:`FALLBACK_TOOLS`, for the fallback. ``book`` holds the numbers
    the answer may state. It grows as each tool result arrives (fix round
    3), so a check never parses the run's results again.
    """

    results: list[tuple[str, str]] = field(default_factory=list)
    book: Sources = field(default_factory=Sources)
    armed: bool = False
    retried: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False,
                                  compare=False)

    def record(self, tool: str, text: str, *, arguments: Any = None,
               delegated: bool = False) -> None:
        """Keep one tool result. A CRM result arms the check.

        A delegated answer (*delegated*) or a result of
        :data:`MODEL_WRITTEN_TOOLS` is text that a model wrote, so it is no
        source. The arguments of a CRM call are sources. Only a tool
        result takes part in a derived figure (a sum, a difference or a
        percent of two), and ``acb_common.number_check`` caps the share of
        each result in that pool.
        """
        if tool in FALLBACK_TOOLS:
            # One character more than the fallback shows, so it still
            # knows that a long result was cut.
            self.results.append((tool, text[:FALLBACK_CHARS + 1]))
            del self.results[:-FALLBACK_RESULTS]
        with self._lock:
            if not delegated and tool not in MODEL_WRITTEN_TOOLS:
                self.book.add_text(text, derive=True)
            if tool in ARM_TOOLS:
                self.armed = True
                if arguments is not None:
                    for value in argument_sources(arguments):
                        self.book.add_text(value)

    def sources(self) -> Sources:
        """Each source of the run (item 4), the member's words included.

        The member's words of this check go on a layer over the run's book,
        so they are a source as they stand and change nothing in the book.
        """
        try:
            from acb_skills.write_artifact import artifact_context

            words = str(artifact_context().get(MEMBER_MESSAGE) or "")
        except Exception:  # no run context: no member words
            words = ""
        if not words:
            return self.book
        book = self.book.layer()
        book.add_text(words)
        return book

    def check(self, text: str) -> CheckResult:
        """Check *text* against the sources of the run."""
        return check(text, self.sources())


def retry_note(result: CheckResult) -> str:
    """The text of the one retry request. It names each failed number."""
    named = ", ".join(f'"{f.text}"' for f in result.unmatched[:MAX_NAMED])
    return (
        "A check of your answer found numbers that no tool result of this turn "
        f"holds: {named}. Write the answer again, with any card again. Copy "
        "each number from a tool result, or round it and keep its unit. You "
        "may state the sum, the difference, the percent change or the percent "
        "share of TWO tool numbers, and then you must also state both of those "
        "numbers. For anything more, call crm_aggregate with "
        "the right group or filter, or leave the number out. Write each figure "
        "in digits."
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
    """Keeps each tool result of ONE run, and the arguments of a CRM call."""

    def __init__(self, ledger: RunLedger) -> None:
        self._ledger = ledger

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        await call_next()
        function = getattr(context, "function", None)
        name = tool_name(function) if function is not None else ""
        try:
            text = _result_text(getattr(context, "result", None))
            record = functools.partial(
                self._ledger.record, name, text,
                arguments=getattr(context, "arguments", None),
                delegated=function is not None and is_delegation_tool(function),
            )
            if len(text) > RECORD_INLINE_CHARS:
                await asyncio.to_thread(record)
            else:
                record()
        except Exception as exc:  # the record never breaks a tool call
            _log.warning("answer_check.record_failed", tool=name, error=type(exc).__name__)


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


#: The three answers of :meth:`CheckAnswers._verdict`.
PASS = "pass"
STRIP = "strip"
FAIL = "fail"


def _beat() -> None:
    """Put one :data:`HOLD_EVENT` on the run queue. Never raises."""
    executor = sys.modules.get("orchestrator.executor")
    if executor is None:  # no executor in this process: no run queue
        return
    try:
        from acb_skills.write_artifact import artifact_context

        queue = executor.resolve_run_queue(artifact_context().get("session_id"))
        if queue is not None:
            queue.put_nowait({"type": "CUSTOM", "name": HOLD_EVENT, "value": {}})
    except Exception:  # a sign of life never breaks a run
        pass


class CheckAnswers(ChatMiddleware):
    """Checks each model response of ONE armed run (items 2, 6, 7 and 10)."""

    def __init__(self, ledger: RunLedger, mode: str) -> None:
        self._ledger = ledger
        self._mode = mode
        self._last_beat = 0.0

    def _log(self, result: CheckResult, outcome: str) -> None:
        # The run id comes from the bound run context (``acb_common._log``).
        _log.info(
            "answer_check.checked", mode=self._mode,
            numbers=len(result.figures), mismatches=len(result.unmatched),
            outcome=outcome,
        )

    def _verdict(self, response: ChatResponse) -> tuple[str, CheckResult]:
        """``pass``, ``strip`` (the text failed, the calls may go) or ``fail``.

        A display call shows its arguments to the member, so a failed card
        fails the whole answer, and never ships. ``strip`` is only for a
        step whose calls are ALL in :data:`QUIET_TOOLS`. Any other call
        makes a failed step fail (fix round 4).
        """
        book = self._ledger.sources()
        card = _card_text(response)
        result = check("\n".join(p for p in (response.text, card) if p), book)
        if result.ok:
            return PASS, result
        calls = _calls(response)
        quiet = all(str(getattr(c, "name", "") or "") in QUIET_TOOLS for c in calls)
        return (STRIP if calls and quiet else FAIL), result

    async def _drain(self, stream: ResponseStream) -> tuple[list[ChatResponseUpdate], ChatResponse]:
        """Read the whole of *stream*, with a sign of life as it goes."""
        updates: list[ChatResponseUpdate] = []
        async for update in stream:
            updates.append(update)
            now = time.monotonic()
            if now - self._last_beat >= HEARTBEAT_S:
                self._last_beat = now
                _beat()
        return updates, await stream.get_final_response()

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
        def _look(response: ChatResponse) -> ChatResponse:
            try:
                verdict, result = self._verdict(response)
                self._log(result, "shadow_pass" if verdict == PASS else "shadow_mismatch")
            except Exception as exc:
                _log.warning("answer_check.shadow_failed", error=type(exc).__name__)
            return response

        if isinstance(context.result, ResponseStream):
            context.result.with_result_hook(_look)
        elif isinstance(context.result, ChatResponse):
            _look(context.result)

    # on: hold, check, retry once, then the fallback

    def _retry_messages(self, messages: list[Any], response: ChatResponse,
                        result: CheckResult) -> list[Any]:
        draft = response.text or "I drew a card with numbers."
        return [*messages, Message("assistant", [draft]),
                Message("user", [retry_note(result)])]

    async def _held(
        self, context: Any, call_next: Callable[[], Any], stream: ResponseStream,
    ) -> AsyncIterator[ChatResponseUpdate]:
        updates, response = await self._drain(stream)
        messages = list(context.messages)
        verdict, result = self._verdict(response)
        if verdict == PASS:
            self._log(result, "pass")
            for update in updates:
                yield update
            return
        if verdict == STRIP:
            self._log(result, "step_text_removed")
            for update in _strip_updates(updates):
                yield update
            return
        if not self._ledger.retried:
            self._ledger.retried = True
            self._log(result, "retry")
            context.messages = self._retry_messages(messages, response, result)
            await call_next()
            retry_updates, second = await self._drain(context.result)
            again, result2 = self._verdict(second)
            if again != FAIL:
                self._log(result2, "retry_pass" if again == PASS else "retry_call")
                kept = retry_updates if again == PASS else _strip_updates(retry_updates)
                for update in kept:
                    yield update
                return
            result, response = result2, second
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
        verdict, result = self._verdict(response)
        if verdict == PASS:
            self._log(result, "pass")
            return response
        if verdict == STRIP:
            self._log(result, "step_text_removed")
            return _without_text(response)
        if not self._ledger.retried:
            self._ledger.retried = True
            self._log(result, "retry")
            context.messages = self._retry_messages(messages, response, result)
            await call_next()
            second = context.result
            again, result2 = self._verdict(second)
            if again == PASS:
                self._log(result2, "retry_pass")
                return second
            if again == STRIP:
                self._log(result2, "retry_call")
                return _without_text(second)
            result, response = result2, second
        self._log(result, "fallback")
        return ChatResponse(
            messages=[Message("assistant", [fallback_text(self._ledger)])],
            response_id=response.response_id, model=response.model,
            finish_reason="stop",
        )


def _role(message: Any) -> str:
    role = getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


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
            [CheckAnswers(self.ledger, self.mode), RecordToolResults(self.ledger)],
        )


__all__ = [
    "ARM_TOOLS",
    "CRM_ASSISTANT_READS",
    "DISPLAY_TOOLS",
    "FALLBACK_TOOLS",
    "HEARTBEAT_S",
    "HOLD_EVENT",
    "MEMBER_MESSAGE",
    "MODEL_WRITTEN_TOOLS",
    "MODES",
    "OFF",
    "ON",
    "QUIET_TOOLS",
    "RECALL_TOOLS",
    "SHADOW",
    "AnswerCheckProvider",
    "CheckAnswers",
    "RecordToolResults",
    "RunLedger",
    "answer_check_mode",
    "argument_sources",
    "display_text",
    "fallback_text",
    "holds_crm_tool",
    "member_words",
    "retry_note",
]
