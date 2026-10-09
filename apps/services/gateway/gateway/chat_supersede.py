"""An edited message SUPERSEDES the last one. It never forks the thread.

Owner ask, 2026-10-09: "Every time I send an edited message, ensure that the
AI knows that the previous message has been superseded by the edited message
... we don't unnecessarily create forks in our conversation."

Before this module, Edit re-sent the text as a NEW turn. The old turn and its
reply stayed in ``chat_message``, so the thread showed the same request twice,
and a run in flight took the edit as a steer instead of a replacement.

The contract, in four rules:

1. **Only the last user message may be superseded.** A later human turn in
   the thread refuses the edit (409 ``not_last``). Only its author may edit
   it (403 ``not_yours``).
2. **A run in flight on the thread stops first.** The member's own run is
   cancelled through ``stream_relay.cancel_run``, and the edit waits for it to
   settle. Somebody else's run refuses the edit (409 ``run_in_progress``).
3. **The superseded turn and every reply after it are deleted**, by id, in
   one tenant-bound transaction (R5 ``tenant_session``).
4. **The new run reads a server-composed note** that names the earlier text
   and the steps the earlier reply already completed that changed something.
   A runtime with its own session memory (the Copilot SDK session, a MAF
   native session) still holds the old turn, and the note is what tells the
   model that the turn was replaced. The note comes from stored tool events,
   never from client text, so a client cannot forge "already done" steps.

Fences: ``tests/unit/test_chat_supersede.py`` (the note and the plan, pure)
and ``tests/unit/test_chat_supersede_rls.py`` (R8, real database under FORCE
RLS: tenant-bound, owner-checked, last-only).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from acb_common import get_logger

_log = get_logger("gateway.chat_supersede")

#: The whole note never passes this many characters (token cost). The cap
#: holds for any number of steps and any length of the earlier text.
SUPERSEDE_NOTE_MAX_CHARS = 1200
#: How much of the earlier text the note quotes.
_OLD_TEXT_MAX = 300
#: How much of one step's result the note quotes.
_STEP_RESULT_MAX = 100
#: How many steps the note lists by name. The rest are counted.
_MAX_LISTED_STEPS = 8

#: The tag that opens the note. It says the platform wrote it, so the model
#: never reads it as the member's own words.
NOTE_TAG = "[Platform note: the member edited their last message]"

#: Tools that steer the conversation and change no data.
_CONTROL_TOOLS: frozenset[str] = frozenset({
    "ask_user", "ask_questions", "request_confirmation", "manage_todo_list",
    "emit_generative_ui", "report_intent", "think", "update_todos",
    "todo_write", "share_artifact",
})

#: A tool whose name starts with one of these only reads.
_READ_PREFIXES: tuple[str, ...] = (
    "get_", "list_", "search_", "read_", "find_", "fetch_", "query_",
    "view_", "show_", "lookup_", "describe_", "count_", "check_", "recall",
    "load_", "inspect_", "preview_", "explain_", "web_search", "remember",
)


class SupersedeRefused(Exception):
    """The edit may not supersede the turn. ``status`` is the HTTP answer."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}


@dataclass(frozen=True)
class Step:
    """One tool call of the superseded reply that changed something."""

    name: str
    result: str
    #: ``done``, or ``running`` when the cancel stopped it before its result.
    status: str


@dataclass(frozen=True)
class SupersedePlan:
    """What one edit removes, and what the new run must know about it."""

    old_text: str
    removed_ids: list[str]
    steps: list[Step] = field(default_factory=list)


def is_side_effect_tool(name: str) -> bool:
    """True when a call of *name* may have changed data.

    The annotation registry (``acb_skills.tool_annotations``) answers first,
    because it is the one source of truth for a tool it knows. An agent's own
    tool is often not registered, so the verb in its name answers next. An
    unknown verb counts as a write: a false "this changed data" costs a line,
    and a missed write makes the model do it twice.
    """
    n = (name or "").strip().lower()
    if not n or n in _CONTROL_TOOLS:
        return False
    try:
        from acb_skills.tool_annotations import get_annotations  # noqa: PLC0415

        hints = get_annotations(n)
    except Exception:  # noqa: BLE001 — no registry means the name decides
        hints = None
    if hints is not None:
        return not bool(hints.get("read_only", False))
    # An MCP tool name carries its server: `pm.create_task`, `crm__get_deal`.
    short = re.split(r"[./:]|__", n)[-1]
    return not short.startswith(_READ_PREFIXES)


def _one_line(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def steps_from_rows(rows: Iterable[dict[str, Any]]) -> list[Step]:
    """The side-effect steps of the stored replies, in order.

    A failed call changed nothing, so it is left out. A call still marked
    ``running`` was cut by the cancel, and its effect is unknown, so it is
    kept and marked. A sub-agent's own calls count, because they act for the
    same request.
    """
    out: list[Step] = []
    for row in rows:
        if row.get("role") == "user":
            continue
        for ev in row.get("tool_events") or []:
            if not isinstance(ev, dict):
                continue
            calls = [ev, *[t for t in (ev.get("subAgentTools") or []) if isinstance(t, dict)]]
            for call in calls:
                name = str(call.get("name") or "")
                status = str(call.get("status") or "done")
                if status == "error" or not is_side_effect_tool(name):
                    continue
                out.append(Step(
                    name=name,
                    result=_one_line(call.get("result"), _STEP_RESULT_MAX),
                    status="running" if status == "running" else "done",
                ))
    return out


def compose_supersede_note(plan: SupersedePlan) -> str:
    """The note the new run reads before the edited text.

    One sentence when the earlier reply changed nothing. Otherwise the steps
    it completed, then the rule: those steps are real and were not undone, so
    reconcile them and do not do them again. Never longer than
    :data:`SUPERSEDE_NOTE_MAX_CHARS`.
    """
    old = _one_line(plan.old_text, _OLD_TEXT_MAX)
    if not plan.steps:
        return _cap(
            f'{NOTE_TAG} The message below replaces their earlier message '
            f'("{old}"), and the earlier reply changed nothing, so answer '
            "only the message below."
        )
    head = (
        f"{NOTE_TAG} The message below replaces their earlier message "
        f'("{old}"). The reply to the earlier message already did these '
        "steps. They are real and nobody undid them:"
    )
    tail = (
        "Do not do these steps again. Make the answer agree with them: change "
        "or undo a step only when the member asks for it, or ask the member."
    )
    lines: list[str] = []
    budget = SUPERSEDE_NOTE_MAX_CHARS - len(head) - len(tail) - 40
    listed = 0
    for step in plan.steps:
        if listed >= _MAX_LISTED_STEPS:
            break
        mark = " (cut off by the edit, it may have finished)" if step.status == "running" else ""
        line = f"- {step.name}{mark}: {step.result}" if step.result else f"- {step.name}{mark}"
        if len(line) + 1 > budget:
            break
        lines.append(line)
        budget -= len(line) + 1
        listed += 1
    rest = len(plan.steps) - listed
    if rest:
        lines.append(f"- and {rest} more step{'s' if rest != 1 else ''}")
    return _cap("\n".join([head, *lines, tail]))


def _cap(note: str) -> str:
    if len(note) <= SUPERSEDE_NOTE_MAX_CHARS:
        return note
    return note[: SUPERSEDE_NOTE_MAX_CHARS - 1].rstrip() + "…"


def plan_supersede(
    rows: list[dict[str, Any]],
    superseded_id: str,
    *,
    actor: str,
    keep_ids: Iterable[str] = (),
    shared: bool = False,
) -> SupersedePlan:
    """Decide what one edit removes. Pure: *rows* are in transcript order.

    *rows* starts at the superseded row, or holds it somewhere. *keep_ids*
    are the rows of the NEW turn (its user row and its agent row), which a
    save of the browser can write before this runs.
    """
    keep = {k for k in keep_ids if k}
    idx = next((i for i, r in enumerate(rows) if r.get("id") == superseded_id), -1)
    if idx < 0:
        raise SupersedeRefused(
            "not_found", 404, "The message to edit is not in this conversation.",
        )
    target = rows[idx]
    kind = target.get("author_kind")
    author = (target.get("author_email") or "").strip().lower()
    me = (actor or "").strip().lower()
    human = target.get("role") == "user" and kind in (None, "human")
    # A row with no author predates rooms. In a solo thread the reader wrote
    # it, and in a shared room nobody can prove who did.
    mine = bool(me) and (author == me if author else not shared)
    if not human or not mine:
        raise SupersedeRefused(
            "not_yours", 403, "Only the author of a message can edit it.",
        )
    later = [r for r in rows[idx + 1:] if r.get("id") not in keep]
    if any(r.get("role") == "user" for r in later):
        raise SupersedeRefused(
            "not_last", 409, "Only the last message in a conversation can be edited.",
        )
    return SupersedePlan(
        old_text=str(target.get("content") or ""),
        removed_ids=[superseded_id, *[str(r["id"]) for r in later]],
        steps=steps_from_rows(later),
    )


# ---------------------------------------------------------------------------
# Database half — one tenant-bound transaction (R5)
# ---------------------------------------------------------------------------

_ROWS_FROM_SQL = (
    "SELECT m.id, m.role, m.content, m.timestamp_ms, m.tool_events, "
    "m.author_email, m.author_kind "
    "FROM chat_message m "
    "JOIN chat_message t ON t.session_id = m.session_id AND t.id = :mid "
    "WHERE m.session_id = :sid "
    # The transcript's own order (chat._get_messages), so "after it" means
    # what the member sees after it.
    "AND (m.timestamp_ms, m.id) >= (t.timestamp_ms, t.id) "
    "ORDER BY m.timestamp_ms ASC, m.id ASC "
    "FOR UPDATE OF m"
)

_DELETE_SQL = (
    "DELETE FROM chat_message "
    "WHERE session_id = :sid AND id = ANY(CAST(:ids AS text[]))"
)


def _row_dict(r: Any) -> dict[str, Any]:
    return {
        "id": r.id, "role": r.role, "content": r.content,
        "timestamp_ms": r.timestamp_ms, "tool_events": r.tool_events or [],
        "author_email": r.author_email, "author_kind": r.author_kind,
    }


def supersede_rows(
    session_id: str,
    superseded_id: str,
    *,
    actor: str,
    keep_ids: Iterable[str] = (),
    shared: bool = False,
    organization_id: str | None,
) -> SupersedePlan:
    """Read, decide and delete in ONE transaction, bound to the tenant.

    The rows are read ``FOR UPDATE``, so a second edit of the same turn waits
    for this one, then finds no row and answers ``not_found``.
    """
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with tenant_session(organization_id) as s:
        rows = [
            _row_dict(r) for r in s.execute(
                text(_ROWS_FROM_SQL), {"sid": session_id, "mid": superseded_id},
            ).fetchall()
        ]
        plan = plan_supersede(
            rows, superseded_id, actor=actor, keep_ids=keep_ids, shared=shared,
        )
        s.execute(text(_DELETE_SQL), {"sid": session_id, "ids": plan.removed_ids})
    return plan


def delete_rows(
    session_id: str, ids: list[str], *, organization_id: str | None,
) -> int:
    """Delete these rows again. Idempotent.

    The new run calls it at its own end. A cancelled run on another worker
    can still be folding when the edit deletes its row, and the fold inserts
    the row again. The second delete removes it.
    """
    if not ids:
        return 0
    from acb_graph import tenant_session  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    with tenant_session(organization_id) as s:
        return s.execute(
            text(_DELETE_SQL), {"sid": session_id, "ids": list(ids)},
        ).rowcount or 0


# ---------------------------------------------------------------------------
# The run half — stop the old run before the rows go
# ---------------------------------------------------------------------------

#: How long the edit waits for a cancelled run to settle.
_SETTLE_TIMEOUT_S = 6.0
_SETTLE_POLL_S = 0.2


async def settle_active_run(thread_id: str, actor: str) -> bool:
    """Stop the member's own run on *thread_id*, and wait for it to end.

    Returns True when a run was stopped. Refuses when the run is somebody
    else's: an edit must not destroy another person's turn.
    """
    try:
        from orchestrator.stream_relay import (  # noqa: PLC0415
            cancel_run, get_detached_task, get_run_actor, is_active,
        )
    except Exception:  # noqa: BLE001 — no relay means no run to stop
        return False
    try:
        if not await is_active(thread_id) and get_detached_task(thread_id) is None:
            return False
        owner = await get_run_actor(thread_id)
    except Exception:  # noqa: BLE001
        _log.warning("chat.supersede_probe_failed", thread_id=thread_id[:12])
        return False
    me = (actor or "").strip().lower()
    if owner and me and owner.strip().lower() != me:
        raise SupersedeRefused(
            "run_in_progress", 409,
            f"{owner} has a run in progress on this conversation. Wait for it "
            "to end, then edit your message.",
        )
    # cancel_run waits for a local task, its persist hook included.
    await cancel_run(thread_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETTLE_TIMEOUT_S
    while loop.time() < deadline:
        try:
            if not await is_active(thread_id) and get_detached_task(thread_id) is None:
                break
        except Exception:  # noqa: BLE001
            break
        await asyncio.sleep(_SETTLE_POLL_S)
    _log.info("chat.supersede_run_cancelled", thread_id=thread_id[:12])
    return True


async def supersede_turn(
    thread_id: str,
    superseded_id: str,
    *,
    actor: str,
    keep_ids: Iterable[str] = (),
    shared: bool = False,
    organization_id: str | None,
) -> SupersedePlan:
    """The whole edit: stop the old run, then remove the old turn.

    The caller has checked that *actor* may send in the room. Raises
    :class:`SupersedeRefused`.
    """
    await settle_active_run(thread_id, actor)
    plan = await asyncio.to_thread(
        supersede_rows, thread_id, superseded_id,
        actor=actor, keep_ids=list(keep_ids), shared=shared,
        organization_id=organization_id,
    )
    _log.info(
        "chat.supersede",
        thread_id=thread_id[:12], removed=len(plan.removed_ids),
        steps=len(plan.steps),
    )
    return plan
