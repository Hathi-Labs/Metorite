"""One progress line while a slow tool runs (HRD-6, task 2985).

Spec: ``project-docs/specs/chat_failure_modes.md`` §4 row B6 and §6 HRD-6,
and ``chat_run_continuity.md`` §4.11.

The owner's rule (2026-11-13): a large read can take minutes, so the member
hears that it takes a while, and that they should wait. Before this module a
slow tool ran with no word, or it failed.

Rules that bind:

1. **One seam, one channel.** :func:`emit_progress` is the one writer. On the
   web chat it puts ONE ``CUSTOM`` event named :data:`EVENT` on the run's own
   queue (``executor.resolve_run_queue``), the channel that ``show_in_app``
   and ``emit_frontend_tool`` use. On WhatsApp it sends the line through the
   channel's early message (``whatsapp_ui.WhatsAppRun.notify``), at most once
   in a reply. No other channel gets a line, and this module builds none.
2. **A timer on each tool call.** :class:`ProgressNotice` is a per-run
   function middleware. It starts a timer when a call starts. A call that has
   not returned after :data:`NOTICE_AFTER_S` gets :data:`LONG_READ_TEXT`. The
   end of the call cancels the timer.
3. **At most one line for each call.** A tool may send its own line first
   with :func:`emit_progress`. The timer then sends nothing for that call.
4. **Parallel calls do not stack lines.** While the line of one call shows,
   the line of a parallel call waits. When the showing call ends, the oldest
   call that waits and still runs gets its line, once. A call that ends while
   it waits gets none. The marks live on the run's queue, as the mark of
   ``show_in_app`` does, so they die with the turn.
5. **Exempt:** the delegation tools (each tool of the delegated run has its
   own timer) and the tools that wait for the member, whose card is the sign.
6. **Dark.** ``CHAT_PROGRESS_NOTICE_ORGS`` (a comma list of organization ids,
   or ``*``) opens it for an org. Empty is the default. Then
   ``orchestrator._tool_injection._apply_progress_notice`` attaches nothing,
   :func:`emit_progress` sends nothing, and each run is byte for byte as
   before. The org is the bound tenant of the run, never request input (R5).

The event and the line hold no tool argument and no result text.
"""
from __future__ import annotations

import asyncio
import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Any

from acb_common import get_logger
from agent_framework import ContextProvider, FunctionMiddleware

from acb_skills.egress import is_delegation_tool
from acb_skills.tool_guard import tool_name

_log = get_logger("acb_skills.progress_notice")

SOURCE_ID = "metorite-progress-notice"

#: The name of the CUSTOM event on the run's stream.
EVENT = "progress"

#: The line of a call that is still running after :data:`NOTICE_AFTER_S`.
LONG_READ_TEXT = (
    "This is a large read. It can take a few minutes to get all the data. "
    "Please wait."
)

#: How long a call runs before the timer sends its line.
NOTICE_AFTER_S = 6.0

#: The longest line. One line, read at a glance.
TEXT_MAX = 300

#: The tools that wait for the member. Their card is the sign of the wait.
WAITS_FOR_MEMBER: frozenset[str] = frozenset({
    "ask_questions", "ask_user", "request_confirmation", "emit_generative_ui",
})

#: The mark on the run's queue: the keys of the calls whose line shows now.
_OPEN_ATTR = "_progress_notice_open"

#: The second mark on the run's queue: the calls whose line waits, oldest
#: first, while another call's line shows.
_WAIT_ATTR = "_progress_notice_waiting"

#: The notice tasks of the WhatsApp path, kept so none is lost to the GC.
_TASKS: set[asyncio.Task[Any]] = set()


# ── The flag ─────────────────────────────────────────────────────────────────

def progress_notice_on(org: object = None) -> bool:
    """True when ``CHAT_PROGRESS_NOTICE_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant of the run. A comma list of org ids, or
    ``*`` for each one. Empty means off, the default. It reads the settings on
    each call, so a flag reload (``docs/flag_reload.md``) applies a change
    with no restart. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_progress_notice_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return str(org or "").strip().lower() in entries


# ── The state of one call ────────────────────────────────────────────────────

@dataclass(eq=False)
class _Call:
    """One tool call, from its start to its end."""

    #: The provider's call id. The client puts the line under this step.
    call_id: str | None
    #: The run's own queue, read at the start of the call, or None.
    queue: Any = None
    #: The open WhatsApp run, read at the start of the call, or None.
    channel: Any = None
    #: True once this call has its line: it went, or it waits (rule 4).
    sent: bool = False
    #: True once the call ended. A late timer then sends nothing.
    ended: bool = False
    #: True while this call's line shows on the web chat.
    shown: bool = False
    #: True when :class:`ProgressNotice` made this state, and so closes it.
    managed: bool = False
    #: The line that waits while another call's line shows, or None.
    due_text: str | None = None
    key: str = field(default_factory=lambda: uuid.uuid4().hex)


_CALL: contextvars.ContextVar[_Call | None] = contextvars.ContextVar(
    "progress_notice_call", default=None,
)


def _run_queue() -> Any:
    """The run's own event queue, or None. H-201: never another run's queue."""
    try:
        from orchestrator.executor import resolve_run_queue

        from acb_skills.write_artifact import artifact_context

        return resolve_run_queue((artifact_context() or {}).get("session_id"))
    except Exception:
        return None


def _channel_run() -> Any:
    """The open WhatsApp run that can send an early message, or None."""
    try:
        from acb_skills.whatsapp_ui import current_run

        run = current_run()
    except Exception:
        return None
    if run is None or getattr(run, "notify", None) is None:
        return None
    return run


def _new_call(call_id: str | None, *, managed: bool) -> _Call:
    return _Call(call_id=call_id, queue=_run_queue(), channel=_channel_run(), managed=managed)


def _clean(text: Any) -> str:
    line = " ".join(str(text or "").split())
    return line[:TEXT_MAX]


# ── The writer ───────────────────────────────────────────────────────────────

async def _notify(run: Any, text: str) -> bool:
    """Send *text* through the channel's early message. Best effort."""
    try:
        sent = bool(await run.notify(text))
    except asyncio.CancelledError:
        raise
    except Exception:  # a nicety: never cost the member the answer
        sent = False
    # A line that did not go leaves the channel's own timer free to send.
    run.notified = sent
    _log.info("progress_notice.channel", sent=sent)
    return sent


def _put(call: _Call, text: str, showing: set[str]) -> None:
    """Put the line of *call* on its run's queue, and mark it as showing."""
    value: dict[str, Any] = {"text": text}
    if call.call_id:
        value["tool_call_id"] = call.call_id
    call.queue.put_nowait({"type": "CUSTOM", "name": EVENT, "value": value})
    if call.managed:
        showing.add(call.key)
        setattr(call.queue, _OPEN_ATTR, showing)
        call.shown = True


def _to_queue(call: _Call, text: str) -> bool:
    """Put the line of *call* on the run's queue now. While another call's
    line shows, a managed call waits instead (rule 4), and this returns False."""
    queue = call.queue
    if queue is None:
        return False
    try:
        showing: set[str] = getattr(queue, _OPEN_ATTR, None) or set()
        if showing:
            # Another call's line shows now. Parallel calls do not stack, so
            # this line waits until that call ends (:func:`_close`).
            if call.managed and not call.ended:
                call.due_text = text
                waiting: list[_Call] = getattr(queue, _WAIT_ATTR, None) or []
                if call not in waiting:
                    waiting.append(call)
                setattr(queue, _WAIT_ATTR, waiting)
            return False
        _put(call, text, showing)
    except Exception as exc:  # a notice never fails the call
        _log.warning("progress_notice.queue_failed", error=type(exc).__name__)
        return False
    return True


def _send_soon(call: _Call, text: str) -> bool:
    """Send the line of *call* now, from a frame that cannot await. The timer
    of :class:`ProgressNotice` calls it."""
    if call.sent or call.ended:
        return False
    call.sent = True
    channel = call.channel
    if channel is not None:
        if getattr(channel, "notified", False):
            return False
        channel.notified = True  # before the send, so a second line waits
        task = asyncio.get_running_loop().create_task(_notify(channel, text))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return True
    return _to_queue(call, text)


async def emit_progress(text: str) -> bool:
    """Tell the member, now, what a slow tool does. THE one writer.

    A tool calls it before a long wait, for example "Adding up 31 totals.
    This can take two minutes." The line replaces the timer's line for this
    call, because each call gets at most one line. Returns True when the line
    went now. While another call's line shows, the line waits and goes when
    that call ends, and this returns False. Never raises.
    """
    if not progress_notice_on():
        return False
    line = _clean(text)
    if not line:
        return False
    call = _CALL.get()
    if call is None:
        # No middleware on this run (a Copilot SDK agent, a direct call): a
        # call of its own, which no timer follows.
        call = _new_call(None, managed=False)
    if call.ended:
        return False
    if call.sent:
        if call.due_text and not call.shown:
            # The timer's line waits behind another call's line. It has not
            # shown, so the tool's own words take its place.
            call.due_text = line
        return False
    channel = call.channel
    if channel is not None:
        call.sent = True
        if getattr(channel, "notified", False):
            return False
        channel.notified = True
        return await _notify(channel, line)
    call.sent = True
    return _to_queue(call, line)


def _close(call: _Call) -> None:
    """End *call*. A call that waits gives up its place, and gets no line.

    When the line of *call* showed and no other line shows now, the oldest
    call that waits and still runs gets its line, once (rule 4).
    """
    call.ended = True
    queue = call.queue
    if queue is None:
        return
    try:
        waiting: list[_Call] = getattr(queue, _WAIT_ATTR, None) or []
        if call in waiting:
            waiting.remove(call)
        if not call.shown:
            return
        showing: set[str] = getattr(queue, _OPEN_ATTR, None) or set()
        showing.discard(call.key)
        if showing:
            return
        while waiting:
            nxt = waiting.pop(0)
            if nxt.ended or nxt.shown or not nxt.due_text:
                continue
            _put(nxt, nxt.due_text, showing)
            break
    except Exception as exc:  # a notice never fails the call
        _log.warning("progress_notice.close_failed", error=type(exc).__name__)


# ── The middleware ───────────────────────────────────────────────────────────

def _exempt(function: Any) -> bool:
    name = tool_name(function)
    if name in WAITS_FOR_MEMBER:
        return True
    inner = getattr(function, "func", None)
    return is_delegation_tool(function) or (inner is not None and is_delegation_tool(inner))


class ProgressNotice(FunctionMiddleware):
    """The timer of each tool call of ONE run (rule 2 of the module)."""

    def __init__(self, after_s: float = NOTICE_AFTER_S) -> None:
        self.after_s = after_s

    async def process(self, context: Any, call_next: Any) -> None:
        function = getattr(context, "function", None)
        try:
            skip = function is None or _exempt(function)
        except Exception:
            skip = True
        if skip:
            await call_next()
            return
        meta = getattr(context, "metadata", None)
        call_id = meta.get("call_id") if isinstance(meta, dict) else None
        call = _new_call(str(call_id) if call_id else None, managed=True)
        if call.queue is None and call.channel is None:
            # Nowhere to show a line: a batch run with no channel, a test.
            await call_next()
            return
        token = _CALL.set(call)
        timer = asyncio.get_running_loop().call_later(
            self.after_s, _send_soon, call, LONG_READ_TEXT,
        )
        try:
            await call_next()
        finally:
            timer.cancel()
            _close(call)
            _CALL.reset(token)


class ProgressNoticeProvider(ContextProvider):
    """Adds :class:`ProgressNotice` to ONE run of a native MAF agent.

    ``orchestrator._tool_injection._apply_progress_notice`` attaches it last,
    so its timer measures the tool and not the other middleware. It adds no
    tool and no instruction.
    """

    def __init__(self, after_s: float = NOTICE_AFTER_S) -> None:
        super().__init__(SOURCE_ID)
        self.after_s = after_s

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(SOURCE_ID, [ProgressNotice(self.after_s)])


__all__ = [
    "EVENT",
    "LONG_READ_TEXT",
    "NOTICE_AFTER_S",
    "SOURCE_ID",
    "TEXT_MAX",
    "WAITS_FOR_MEMBER",
    "ProgressNotice",
    "ProgressNoticeProvider",
    "emit_progress",
    "progress_notice_on",
]
