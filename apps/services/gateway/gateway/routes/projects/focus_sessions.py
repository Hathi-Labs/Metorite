"""Projects · the focus-session log (WS-MDC CC-5a, Metorite task 1972).

Spec: ``project-docs/specs/my_day_command_centre.md`` §9.3 · D188 (D-MD-5).

    POST  /projects/my/focus-sessions               → start a session on a task I can see
    PATCH /projects/my/focus-sessions/{session_id}  → pause, resume, extend or end MY session
    GET   /projects/my/focus-sessions?day=          → my sessions of one local day, with minutes

**One row for each session** (migration ``350_focus_sessions.sql``). Before
this module, actual time was one ``actual_start`` and ``actual_end`` pair on
``pm_task_personal``, and each session wrote over it. This module is the one
writer of ``focus_sessions``.

**Private to the member.** RLS binds the tenant. Every statement here also
names ``member_email = :me``, and ``:me`` comes from the session, never from
the request (R5, R11). A session of another member answers 404, never 403.

**One open session for each member.** A start ends the open session as
``replaced`` and inserts the new row, in one transaction. The partial unique
index ``focus_sessions_one_open`` keeps one open row when two starts race. The
loser meets the index, and its start runs once more in a new savepoint, where
it sees the winner's row and replaces it.

**The planned end.** A pomo ends at the start plus its work length. A Flow
session ends at the end of the member's block of the task when that block
covers now, else at the start plus :data:`FLOW_MINUTES`. So every new session
has a planned end, and the auto close applies to each one.

**The auto close.** No job runs. Each read, each start and each PATCH first
runs ONE statement, :data:`AUTO_CLOSE_SQL`, so the order of the reads never
changes the minutes. A running session whose ``planned_end`` is more than 15
minutes ago ends at ``planned_end``. While a pause runs, the timer stands
still: the effective planned end is ``planned_end + (now() - paused_at)``, and
the same 15-minute rule reads it. A pause of more than 60 minutes ends the
session at ``paused_at``. Each close writes ``auto_closed``. A second reader at
the same time waits on the row lock, then finds ``ended_at`` set and updates
no row. Never read first and then write.

**The check-in answer after an auto close.** An ``end`` with an answer on a
session that the auto close ended, with no answer of the member yet, records
the answer and keeps ``ended_at``. Every other act on an ended session answers
409 ``session_ended``.

**The overlay pair.** A start stamps ``actual_start`` (and clears
``actual_end``, as the focus room does). An end stamps ``actual_end``. A
replaced session stamps the end of its own task, and an auto-closed session
stamps its ``ended_at``. Each stamp goes through
``personal._upsert_personal``, the one writer of ``pm_task_personal``. Until
CC-5b no client calls these routes, so the room's own stamp stays the only
live writer of the pair.

**The pause.** ``paused_at`` holds the start of the pause that runs now. A
resume adds the pause to ``paused_seconds`` and moves ``planned_end`` by the
same length, so a pause stops the timer. An extend during a pause moves the
effective planned end, so the resume adds the pause once. An end or a replace
during a pause counts the pause up to the end. The minutes of a session are the
end (or now) minus the start minus the paused seconds.

**Dark by org.** :func:`require_focus_log` reads ``FOCUS_LOG_ORGS`` through
``flags.focus_log_allowed`` before the handler runs, so an org off the list
gets 404 and no database work.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from acb_auth import UserContext, get_current_user
from acb_common import get_logger
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    load_visible_task,
    now,
    resolve_visibility,
    router,
)
from gateway.routes.projects.due_day import day_start, local_date, noon_of, zone_name
from gateway.routes.projects.flags import focus_log_allowed
from gateway.routes.projects.personal import _upsert_personal, stored_zone
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

_log = get_logger("gateway.projects.focus_sessions")

#: Migration 350's mode CHECK. The focus room's three timers (`FocusMode.tsx`).
MODES: tuple[str, ...] = ("25/5", "50/10", "flow")

#: The work length of a timer, in minutes. Flow has no timer: the room counts
#: up against the block (`FocusMode.tsx`, `workTargetSec`).
TIMER_MINUTES: dict[str, int] = {"25/5": 25, "50/10": 50}

#: The planned length of a Flow session with no block that covers now. An
#: extend moves it, as for a pomo, and the auto close then applies.
FLOW_MINUTES = 90

#: The four answers to "Is it done?". A member ends a session with one of them.
END_OUTCOMES: tuple[str, ...] = ("done", "almost", "not_now", "blocked")

#: The outcomes that only the server writes. A body that names one gets 422.
SERVER_OUTCOMES: tuple[str, ...] = ("replaced", "auto_closed")

#: Migration 350's outcome CHECK.
OUTCOMES: tuple[str, ...] = END_OUTCOMES + SERVER_OUTCOMES

#: "+10 min moves planned_end" (§9.3).
EXTEND_MINUTES = 10

#: The rows of one day read. A member makes far fewer. `more` says when the
#: read stopped at the cap.
DAY_CAP = 500

#: The partial unique index of migration 350: one open session for each member.
OPEN_INDEX = "focus_sessions_one_open"

#: How many times a start tries its write: the first try and one more.
START_ATTEMPTS = 2

NOT_FOUND = "Focus session not found"
TASK_NOT_FOUND = "Task not found"
ENDED = {
    "code": "session_ended",
    "message": "This focus session has ended. Start a new session to go on.",
}
BUSY = {
    "code": "focus_busy",
    "message": "Another focus session started at the same time. Try again.",
}
BAD_DAY = (
    "The day is out of range. Send day as a date from 0001-01-02 to 9999-12-30, "
    "for example 2026-11-07."
)

# ── SQL ──────────────────────────────────────────────────────────────────────

#: The seconds of the pause that runs now, as a whole number. 0 with no pause.
_OPEN_PAUSE = (
    "CASE WHEN paused_at IS NULL THEN 0"
    " ELSE GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (now() - paused_at))))::int END"
)

#: The pause that runs now, as an interval of the same whole seconds.
_OPEN_PAUSE_INTERVAL = f"make_interval(secs => {_OPEN_PAUSE})"

#: The focused seconds of a row: the end (or now, while it runs) minus the
#: start, minus the paused seconds, minus the pause that runs now. Never below 0.
_FOCUSED_SECONDS = (
    "GREATEST(0,"
    " FLOOR(EXTRACT(EPOCH FROM (COALESCE(ended_at, now()) - started_at)))"
    " - paused_seconds"
    " - CASE WHEN ended_at IS NULL AND paused_at IS NOT NULL"
    "        THEN FLOOR(EXTRACT(EPOCH FROM (now() - paused_at))) ELSE 0 END"
    ")::bigint AS focused_seconds"
)

#: The columns of an answer row.
_COLUMNS = (
    "id, task_id, mode, started_at, ended_at, planned_end, paused_seconds,"
    " paused_at, outcome, " + _FOCUSED_SECONDS
)

#: The timer is stale: its effective planned end is more than 15 minutes ago.
#: While a pause runs, the effective planned end is planned_end + (now() -
#: paused_at), so a pause stops the timer.
_TIMER_STALE = (
    "((paused_at IS NULL AND planned_end < now() - interval '15 minutes')"
    " OR (paused_at IS NOT NULL"
    "     AND planned_end + (now() - paused_at) < now() - interval '15 minutes'))"
)

#: The pause is longer than 60 minutes.
_LONG_PAUSE = "(paused_at IS NOT NULL AND paused_at < now() - interval '60 minutes')"

#: The auto close (§9.3). ONE statement, never a read and then a write. A
#: stale timer ends at `planned_end`. A long pause with a live timer ends at
#: `paused_at`. In both cases the end is at or before the pause, so the pause
#: adds no seconds.
AUTO_CLOSE_SQL = (
    "UPDATE focus_sessions"
    f"   SET ended_at = GREATEST(started_at,"
    f"           CASE WHEN {_TIMER_STALE} THEN planned_end ELSE paused_at END),"
    "       outcome = 'auto_closed',"
    "       paused_at = NULL"
    " WHERE member_email = :me"
    "   AND ended_at IS NULL"
    f"   AND ({_TIMER_STALE} OR {_LONG_PAUSE})"
    " RETURNING id, task_id, ended_at"
)

#: A start ends the member's open session as `replaced`. `GREATEST` keeps the
#: end at or after the start when the other start began its transaction later.
_REPLACE_OPEN_SQL = (
    "UPDATE focus_sessions"
    "   SET ended_at = GREATEST(now(), started_at),"
    "       outcome = 'replaced',"
    f"      paused_seconds = paused_seconds + {_OPEN_PAUSE},"
    "       paused_at = NULL"
    " WHERE member_email = :me AND ended_at IS NULL"
    " RETURNING id, task_id, ended_at"
)

#: A timer session: the planned end is the start plus the work length.
_INSERT_TIMER_SQL = (
    "INSERT INTO focus_sessions (member_email, task_id, started_at, planned_end, mode)"
    " VALUES (:me, CAST(:task_id AS uuid), now(),"
    "         now() + make_interval(mins => CAST(:minutes AS integer)), :mode)"
    f" RETURNING {_COLUMNS}"
)

#: A Flow session: the planned end is the end of the member's block of this
#: task, when that block covers now. Else it is the start plus FLOW_MINUTES,
#: so the auto close applies to it too.
_INSERT_FLOW_SQL = (
    "INSERT INTO focus_sessions (member_email, task_id, started_at, planned_end, mode)"
    " VALUES (:me, CAST(:task_id AS uuid), now(),"
    "         COALESCE((SELECT p.scheduled_end FROM pm_task_personal p"
    "                    WHERE p.task_id = CAST(:task_id AS uuid)"
    "                      AND lower(p.member_email) = :me"
    "                      AND p.scheduled_start <= now() AND p.scheduled_end > now()"
    "                    LIMIT 1),"
    f"                  now() + interval '{FLOW_MINUTES} minutes'),"
    "         :mode)"
    f" RETURNING {_COLUMNS}"
)

#: Each act writes one row of the member that is still open.
_MINE_OPEN = "WHERE id = CAST(:id AS uuid) AND member_email = :me AND ended_at IS NULL"

#: A pause on a paused session keeps its first `paused_at`.
_PAUSE_SQL = (
    f"UPDATE focus_sessions SET paused_at = COALESCE(paused_at, now()) {_MINE_OPEN}"
    f" RETURNING {_COLUMNS}"
)

#: A resume adds the pause and moves the planned end by the same seconds. On a
#: session with no pause it changes nothing.
_RESUME_SQL = (
    "UPDATE focus_sessions"
    f"   SET paused_seconds = paused_seconds + {_OPEN_PAUSE},"
    "       planned_end = CASE WHEN planned_end IS NULL THEN NULL"
    f"           ELSE planned_end + {_OPEN_PAUSE_INTERVAL} END,"
    "       paused_at = NULL"
    f" {_MINE_OPEN}"
    f" RETURNING {_COLUMNS}"
)

#: "+10 min moves planned_end". The new effective planned end is 10 minutes
#: after the effective planned end, or after now when that has passed. The
#: stored planned end leaves out the pause that runs now, because the resume
#: adds that pause. So 09:00 a 25/5 start, a pause at 09:20, an extend at
#: 09:30 and a resume at 09:35 give 09:50.
_EXTEND_SQL = (
    "UPDATE focus_sessions"
    "   SET planned_end = GREATEST("
    f"           COALESCE(planned_end, now()) + {_OPEN_PAUSE_INTERVAL}, now())"
    f"       + interval '{EXTEND_MINUTES} minutes' - {_OPEN_PAUSE_INTERVAL}"
    f" {_MINE_OPEN}"
    f" RETURNING {_COLUMNS}"
)

#: An end with the member's answer. A pause that runs counts up to the end.
_END_SQL = (
    "UPDATE focus_sessions"
    "   SET ended_at = GREATEST(now(), started_at),"
    "       outcome = :outcome,"
    f"      paused_seconds = paused_seconds + {_OPEN_PAUSE},"
    "       paused_at = NULL"
    f" {_MINE_OPEN}"
    f" RETURNING {_COLUMNS}"
)

#: The member's answer on a session that the auto close ended. It keeps
#: `ended_at`. A session that holds an answer of the member is not
#: `auto_closed`, so a second answer changes nothing here.
_ANSWER_SQL = (
    "UPDATE focus_sessions SET outcome = :outcome"
    " WHERE id = CAST(:id AS uuid) AND member_email = :me"
    "   AND ended_at IS NOT NULL AND outcome = 'auto_closed'"
    f" RETURNING {_COLUMNS}"
)

_PATCH_SQL: dict[str, str] = {
    "pause": _PAUSE_SQL,
    "resume": _RESUME_SQL,
    "extend": _EXTEND_SQL,
    "end": _END_SQL,
}

#: Why a PATCH wrote no row: no row of the member (404) or an ended row (409).
_MINE_SQL = (
    "SELECT ended_at FROM focus_sessions"
    " WHERE id = CAST(:id AS uuid) AND member_email = :me"
)

#: The sessions of one member that started in one local day.
_DAY_SQL = (
    f"SELECT {_COLUMNS} FROM focus_sessions"
    " WHERE member_email = :me AND started_at >= :start AND started_at < :end"
    " ORDER BY started_at, id"
    " LIMIT :cap"
)


# ── The flag ─────────────────────────────────────────────────────────────────


async def require_focus_log(user: UserContext = Depends(get_current_user)) -> None:
    """404 while ``FOCUS_LOG_ORGS`` does not hold the caller's org. It runs
    before the handler, so a dark org gets no database work."""
    if not focus_log_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")


_ON = [Depends(require_focus_log)]


# ── The bodies ───────────────────────────────────────────────────────────────


class FocusStartIn(BaseModel):
    """A start. The member and the org come from the session, so a body that
    names either gets 422 (``extra="forbid"``)."""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    mode: Literal["25/5", "50/10", "flow"]


class FocusPatchIn(BaseModel):
    """One act on an open session. ``outcome`` goes with ``end`` only."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["pause", "resume", "extend", "end"]
    outcome: Literal["done", "almost", "not_now", "blocked"] | None = None


# ── The helpers ──────────────────────────────────────────────────────────────


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def session_out(row: Any) -> dict[str, Any]:
    """One session as the wire shape. ``minutes`` is the whole minutes of
    ``seconds``."""
    seconds = int(getattr(row, "focused_seconds", 0) or 0)
    return {
        "id": str(row.id),
        "task_id": str(row.task_id),
        "mode": row.mode,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "planned_end": _iso(row.planned_end),
        "paused_seconds": int(row.paused_seconds or 0),
        "paused": row.paused_at is not None,
        "outcome": row.outcome,
        "seconds": seconds,
        "minutes": seconds // 60,
    }


async def _stamp_end(db: Any, me: str, rows: list[Any]) -> None:
    """Stamp ``actual_end`` on the overlay of each ended row's task."""
    for row in rows:
        await _upsert_personal(db, str(row.task_id), me, {"actual_end": row.ended_at})


async def auto_close(db: Any, me: str) -> int:
    """Run :data:`AUTO_CLOSE_SQL` for the member, and stamp each closed row's
    ``ended_at`` as the ``actual_end`` of its task. Returns the count it closed."""
    closed = (await db.execute(text(AUTO_CLOSE_SQL), {"me": me})).fetchall()
    await _stamp_end(db, me, closed)
    return len(closed)


def _error_chain(exc: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    node: BaseException | None = exc
    while node is not None and node not in seen:
        seen.append(node)
        node = getattr(node, "orig", None) or node.__cause__
    return seen


def _sqlstates(chain: list[BaseException]) -> set[str]:
    return {
        str(getattr(e, "sqlstate", None) or getattr(e, "pgcode", None) or "")
        for e in chain
    }


def open_session_taken(exc: BaseException) -> bool:
    """True when *exc* is a unique violation of :data:`OPEN_INDEX`, the race of
    two starts. Any other integrity error is not a retry case."""
    chain = _error_chain(exc)
    if "23505" not in _sqlstates(chain):
        return False
    names = {str(getattr(e, "constraint_name", "") or "") for e in chain}
    return OPEN_INDEX in names or any(OPEN_INDEX in str(e) for e in chain)


def task_gone(exc: BaseException) -> bool:
    """True when *exc* is a foreign key violation (23503): the task went
    between the visibility read and the write, so the start answers 404."""
    return "23503" in _sqlstates(_error_chain(exc))


async def _start_in(db: Any, me: str, task_id: str, mode: str) -> Any:
    """End the open session as ``replaced``, then insert the new row, in one
    savepoint. A race with another start meets :data:`OPEN_INDEX`, and the
    savepoint runs once more: its UPDATE then sees the other start's row. A
    task deleted during the start answers 404, and the savepoint keeps the old
    session open."""
    if mode in TIMER_MINUTES:
        sql = _INSERT_TIMER_SQL
        binds = {"me": me, "task_id": task_id, "mode": mode,
                 "minutes": TIMER_MINUTES[mode]}
    else:
        sql = _INSERT_FLOW_SQL
        binds = {"me": me, "task_id": task_id, "mode": mode}
    for attempt in range(1, START_ATTEMPTS + 1):
        try:
            async with db.begin_nested():
                replaced = (await db.execute(text(_REPLACE_OPEN_SQL), {"me": me})).fetchall()
                await _stamp_end(db, me, replaced)
                row = (await db.execute(text(sql), binds)).fetchone()
                await _upsert_personal(db, task_id, me, {
                    "actual_start": row.started_at, "actual_end": None})
            return row
        except IntegrityError as exc:
            if task_gone(exc):
                raise HTTPException(status_code=404, detail=TASK_NOT_FOUND) from None
            if not open_session_taken(exc):
                raise
            _log.info("focus_sessions.start_retry", attempt=attempt)
    raise HTTPException(status_code=409, detail=BUSY)


def day_bounds(the_day: date, zone: str) -> tuple[Any, Any]:
    """The start of *the_day* and of the next day, in *zone*. Noon is inside
    its day in every zone, so ``day_start`` of noon is the start of that day,
    also on a day with a clock change. A day at either end of the calendar
    answers 422, because the bounds and their UTC form must exist."""
    if not (date.min < the_day < date.max):
        raise HTTPException(status_code=422, detail=BAD_DAY)
    try:
        start = day_start(zone, noon_of(the_day, zone))
        end = day_start(zone, noon_of(the_day + timedelta(days=1), zone))
    except (OverflowError, ValueError):
        raise HTTPException(status_code=422, detail=BAD_DAY) from None
    return start, end


# ── The routes ───────────────────────────────────────────────────────────────


@router.post("/my/focus-sessions", status_code=201, dependencies=_ON)
async def start_focus_session(
    payload: FocusStartIn, user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Start a focus session on a task that I can see. It ends my open session
    as ``replaced``. A task that I cannot see answers 404."""
    me = actor(user).lower()
    task_id = str(payload.task_id)
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_task(db, vis, task_id)
        await auto_close(db, me)
        row = await _start_in(db, me, task_id, payload.mode)
        out = session_out(row)
    return out


@router.patch("/my/focus-sessions/{session_id}", dependencies=_ON)
async def update_focus_session(
    session_id: UUID, payload: FocusPatchIn,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Pause, resume, extend (+10 min) or end MY open session.

    The auto close runs first. ``end`` needs one of the four answers to "Is it
    done?", and another act takes no outcome. An answer on a session that the
    auto close ended, with no answer of the member yet, records the answer and
    keeps ``ended_at``. Every other act on an ended session answers 409
    ``session_ended``. A session of another member answers 404. A pause of a
    paused session and a resume of a running one change nothing."""
    if payload.action == "end" and payload.outcome is None:
        raise HTTPException(
            status_code=422,
            detail=f"An end needs an outcome. One of: {list(END_OUTCOMES)}.",
        )
    if payload.action != "end" and payload.outcome is not None:
        raise HTTPException(
            status_code=422, detail="Only an end takes an outcome.",
        )
    me = actor(user).lower()
    binds: dict[str, Any] = {"id": str(session_id), "me": me}
    if payload.action == "end":
        binds["outcome"] = payload.outcome
    async with _tenant_session() as db:
        await auto_close(db, me)
        row = (await db.execute(text(_PATCH_SQL[payload.action]), binds)).fetchone()
        if row is not None and payload.action == "end":
            await _stamp_end(db, me, [row])
        if row is None and payload.action == "end":
            row = (await db.execute(text(_ANSWER_SQL), binds)).fetchone()
        if row is None:
            mine = (await db.execute(
                text(_MINE_SQL), {"id": str(session_id), "me": me},
            )).fetchone()
            if mine is None:
                raise HTTPException(status_code=404, detail=NOT_FOUND)
            raise HTTPException(status_code=409, detail=ENDED)
        out = session_out(row)
    return out


@router.get("/my/focus-sessions", dependencies=_ON)
async def list_focus_sessions(
    day: date | None = Query(default=None),
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """My sessions that started in one local day, with the minutes of each and
    the total. No ``day`` is my today. The day is my own, in the zone of
    ``user_settings.timezone`` (``personal.stored_zone``), the zone of the
    Today rule. The read runs the auto close first."""
    me = actor(user).lower()
    async with _tenant_session() as db:
        await auto_close(db, me)
        zone = zone_name(await stored_zone(db, me))
        the_day = day or local_date(zone, now())
        start, end = day_bounds(the_day, zone)
        rows = (await db.execute(
            text(_DAY_SQL), {"me": me, "start": start, "end": end, "cap": DAY_CAP + 1},
        )).fetchall()
    more = len(rows) > DAY_CAP
    sessions = [session_out(r) for r in rows[:DAY_CAP]]
    total_seconds = sum(s["seconds"] for s in sessions)
    return {
        "day": the_day.isoformat(),
        "timezone": zone,
        "sessions": sessions,
        "total_seconds": total_seconds,
        # One rule for minutes: the whole minutes of the seconds.
        "total_minutes": total_seconds // 60,
        "more": more,
    }


__all__ = [
    "AUTO_CLOSE_SQL",
    "END_OUTCOMES",
    "FLOW_MINUTES",
    "MODES",
    "OPEN_INDEX",
    "OUTCOMES",
    "SERVER_OUTCOMES",
    "TIMER_MINUTES",
    "auto_close",
    "day_bounds",
    "open_session_taken",
    "require_focus_log",
    "session_out",
    "task_gone",
]
