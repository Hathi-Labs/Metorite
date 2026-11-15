"""A card stores the call that it guards (RESUME-1a, task 3260).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.4 item 1 and the
RESUME-1 row of §4.12.7, with the corrections C1 to C9 of the audit of
2026-11-15. Dark behind ``CHAT_RESUME_ORGS`` (empty, off). Off, nothing here
stores a call, so each card and each ``chat_pending_ask`` row is as before.

**Why.** A late Approve of a parked card started a new run, which planned
again and drew a new card. RESUME-2 will run the stored call of the card once,
with no model. This slice only stores the call, at the card. No route reads it.

**Which card stores one** (D-RES-2, C1, C2). A plain confirmation card only:
no rows, no reply form and no held send id (``ask_tools.PlainCard``). Then
``late_approve.act_of`` is the ONE resumable test. It refuses a cut card, a
tool that fails the three tests of ``egress.is_card_write_tool`` (the name is
on ``HELD_CARD_WRITE_TOOLS``, the annotation says ``card_write``, and the
parameters are the pinned set), and act keys that do not read. So an email
card, an egress tool and an MCP tool with a borrowed name store none. A
question card is RESUME-1b, and a generative UI stores none.

**The shape, version 1** (:func:`stored_call_of`)::

    {"v": 1, "kind": "confirmation", "tool", "module", "agent", "arguments",
     "call_id", "card_text", "act_keys", "digest"}

* ``arguments`` are the bound arguments of the call as JSON gives them back
  (``whatsapp_acts._stored_arguments``). A call that JSON cannot hold stores
  none, and so does a call above 64 KB (``pending_ask.bind_call``).
* ``module`` reads a module that the loader made for one run as
  ``agent:<name>``, as the act keys do (C7). ``act_keys`` are the
  ``late_approve.keys_wire`` strings, and ``digest`` is their digest with the
  card text.
* ``agent`` is the agent of the run, so RESUME-2 can refuse a card of a
  delegated child run (C8). A run with no agent stores none.
* ``call_id`` is the MAF call id of the tool call (C4), or null.
  :class:`CallIdMiddleware` reads it from ``context.metadata["call_id"]``.
* No org, member or thread: the columns of the row hold them (C6).

The org is the run's own (``executor._current_run_org``), never request
input (R5, R11).

**The yes** (RESUME-2a). :func:`resuming` opens the scope of ONE stored
call in the continuation run, and :func:`resume_answer` is its one test. Only
the first card of the scope asks, and only an equal card (the card text and
the act keys) takes the yes.

Fences (R7): ``tests/unit/test_resume_stored_call.py``,
``tests/unit/test_resume_stored_call_r8.py`` (R8),
``tests/unit/test_resume_continuation.py`` and
``tests/unit/test_resume_continuation_r8.py`` (R8).
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from acb_common import get_logger
from agent_framework import ContextProvider, FunctionMiddleware

from acb_skills.tool_guard import tool_name

_log = get_logger("acb_skills.stored_call")

#: The source id of :class:`CallIdProvider` on a run.
SOURCE_ID = "metorite-resume-call-id"

#: The version of the stored shape. RESUME-2 reads only this version.
VERSION = 1

#: The one kind that stores a call in RESUME-1a.
KIND = "confirmation"

#: The call id of the tool call that runs on this frame, with the name of its
#: tool: ``(name, call_id)``. :class:`CallIdMiddleware` sets it around each
#: call. ⚠️ The default is None, never a mutable object.
_CALL_ID: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "acb_resume_call_id", default=None)


# ── The flag ─────────────────────────────────────────────────────────────────

def chat_resume_on(org: object = None) -> bool:
    """True when ``CHAT_RESUME_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant. A comma list of org ids, or ``*`` for
    each one. Empty means off, the default. It reads the settings on each
    call, so a flag reload (``docs/flag_reload.md``) applies a change with no
    restart. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_resume_orgs", "") or "")
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


# ── The call id (C4) ─────────────────────────────────────────────────────────

def current_call_id(name: str) -> str | None:
    """The MAF call id of the tool call *name* that runs on this frame, or
    None. A call of another tool gives None, so a card of a child run never
    takes the call id of the call that started the child."""
    held = _CALL_ID.get()
    if held is None or not name or held[0] != name:
        return None
    return held[1]


class CallIdMiddleware(FunctionMiddleware):
    """Holds the call id of each tool call of ONE run while the call runs.

    MAF puts the call id in ``context.metadata["call_id"]`` before the
    function middleware runs. A call with no id holds nothing."""

    async def process(self, context: Any, call_next: Any) -> None:
        token = None
        try:
            meta = getattr(context, "metadata", None)
            call_id = meta.get("call_id") if isinstance(meta, dict) else None
            name = tool_name(getattr(context, "function", None))
            if call_id and name:
                token = _CALL_ID.set((name, str(call_id)))
        except Exception:
            token = None
        try:
            await call_next()
        finally:
            if token is not None:
                try:
                    _CALL_ID.reset(token)
                except ValueError:
                    _CALL_ID.set(None)


class CallIdProvider(ContextProvider):
    """Adds :class:`CallIdMiddleware` to ONE run of a native MAF agent.

    ``orchestrator._tool_injection._apply_resume_call_id`` attaches it while
    ``CHAT_RESUME_ORGS`` names the org of the run. It adds no tool and no
    instruction."""

    def __init__(self) -> None:
        super().__init__(SOURCE_ID)

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(SOURCE_ID, [CallIdMiddleware()])


# ── The shape ────────────────────────────────────────────────────────────────

def _module_of(tool: Any, agent: str) -> str:
    """The module of *tool*. A module that the loader made for one run reads
    as ``agent:<name>`` (C7), as ``late_approve.keys_wire`` reads it."""
    from acb_skills.late_approve import _PER_RUN_MODULE

    module = str(getattr(getattr(tool, "func", tool), "__module__", "") or "")
    if _PER_RUN_MODULE.match(module):
        return f"agent:{agent}"
    return module


# ── The stored-call yes (RESUME-2a) ──────────────────────────────────────────

@dataclass
class Resuming:
    """The state of ONE stored-call yes (:func:`resuming`).

    *stored* is the stored call that the member approved. *taken* is True
    once a card of the scope asked :func:`resume_answer`, so only the FIRST
    card takes the yes. *outcome* says what that card did: ``approved`` (the
    equal card, no card shows), ``changed`` (a card that is not equal, which
    shows as normal) or ``""`` (no card asked). *card_shown* is True when a
    card of the scope goes on to the member."""

    stored: dict[str, Any]
    taken: bool = False
    outcome: str = ""
    card_shown: bool = False


#: The yes of the stored call that runs on this frame. ⚠️ The default is
#: None, never a mutable object.
_RESUMING: contextvars.ContextVar[Resuming | None] = contextvars.ContextVar(
    "acb_resuming", default=None)


@contextlib.contextmanager
def resuming(stored: Mapping[str, Any]) -> Iterator[Resuming]:
    """The scope of ONE stored-call yes (RESUME-2a).

    ``orchestrator.executor`` opens it around the one call of the stored
    tool in the continuation run. Inside it, the first card of the call asks
    :func:`resume_answer`. Outside it, no card ever takes the yes."""
    state = Resuming(dict(stored))
    token = _RESUMING.set(state)
    try:
        yield state
    finally:
        try:
            _RESUMING.reset(token)
        except ValueError:
            _RESUMING.set(None)


async def resume_answer(card: Any) -> bool:
    """True when *card* is the card that the member approved, so it shows
    no card and its tool writes (RESUME-2a). ``ask_tools.request_confirmation``
    calls it just before the late-approve grant.

    Only the FIRST card in the scope of :func:`resuming` asks. A card that is
    not plain (*card* None) takes no yes. A plain card takes it only when
    ``late_approve.act_of(card, agent)`` gives the stored ``card_text`` and the
    stored ``act_keys``, and the tool has the stored name. So a changed record
    gives another card text, and the card shows (D-RES-3). A raw call of the
    tool function opens no ``card_call``, so its card has no act keys and
    never takes the yes. The yes takes the act with ONE ``SET … GET``
    (``late_approve.claim_act``): an act that is SPENT already (a twin row,
    or a twin approved at the same moment) takes no yes, so it never writes
    twice. A run with no late-approve scope takes no yes either (fail
    closed). Never raises, and a fault shows the card."""
    state = _RESUMING.get()
    if state is None or state.taken:
        return False
    state.taken = True
    state.card_shown = True
    state.outcome = "changed"
    try:
        from acb_skills.late_approve import _run_agent, act_of, claim_act

        if card is None:
            return False
        stored = state.stored
        act = act_of(card, _run_agent())
        if act is None:
            return False
        if tool_name(getattr(card, "tool", None)) != str(stored.get("tool") or ""):
            return False
        if act.text != stored.get("card_text") or list(act.keys) != list(
                stored.get("act_keys") or []):
            _log.info("stored_call.card_changed", tool=str(stored.get("tool") or "")[:60])
            return False
        # The LAST test: it marks the act SPENT, so nothing may refuse after it.
        if not await claim_act(card):
            state.outcome = "spent"
            _log.info("stored_call.act_spent", tool=str(stored.get("tool") or "")[:60])
            return False
    except Exception as exc:
        _log.warning("stored_call.resume_check_failed", error_type=type(exc).__name__)
        return False
    state.outcome = "approved"
    state.card_shown = False
    return True


def stored_call_of(card: Any, arguments: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The stored call of the plain card *card* (``ask_tools.PlainCard``), or
    None when it stores none.

    *arguments* are the bound arguments of the call (``ask_tools.card_call``).
    None unless each of these holds: ``CHAT_RESUME_ORGS`` names the org of the
    run (C5), the run has an agent, ``late_approve.act_of`` gives an act (C1),
    and JSON holds the arguments. The size cap is ``pending_ask.bind_call``'s.
    Never raises."""
    try:
        from acb_skills.late_approve import _run_agent, _run_org, act_of
        from acb_skills.whatsapp_acts import _stored_arguments

        if card is None or arguments is None:
            return None
        org = _run_org()
        if not org or not chat_resume_on(org):
            return None
        agent = _run_agent()
        if not agent:
            return None
        act = act_of(card, agent)
        if act is None:
            return None
        stored_args = _stored_arguments(dict(arguments))
        if stored_args is None:
            return None
        tool = getattr(card, "tool", None)
        name = tool_name(tool)
        if not name:
            return None
        return {
            "v": VERSION,
            "kind": KIND,
            "tool": name,
            "module": _module_of(tool, agent),
            "agent": agent,
            "arguments": stored_args,
            "call_id": current_call_id(name),
            "card_text": act.text,
            "act_keys": list(act.keys),
            "digest": act.digest,
        }
    except Exception as exc:
        _log.warning("stored_call.build_failed", error_type=type(exc).__name__)
        return None


__all__ = [
    "KIND",
    "SOURCE_ID",
    "VERSION",
    "CallIdMiddleware",
    "CallIdProvider",
    "Resuming",
    "chat_resume_on",
    "current_call_id",
    "resume_answer",
    "resuming",
    "stored_call_of",
]
