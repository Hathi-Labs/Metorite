"""Projects · reminders — the private reminder store of one member.

Spec: ``project-docs/specs/calendar_events_reminders.md`` §5.1 to §5.3, §5.8
and §10.3 · **D114** · board WS-66 RM-1.

    GET   /projects/my/reminders                          → my reminders, by state and window
    POST  /projects/my/reminders                          → set one, in state ``scheduled``
    PATCH /projects/my/reminders/{reminder_id}            → change its text, kind, link or time
    POST  /projects/my/reminders/{reminder_id}/resolve    → done or drop, which reopen undoes
    POST  /projects/my/reminders/{reminder_id}/reopen     → undo a done or a drop
    POST  /projects/my/reminders/{reminder_id}/promote    → make it a task, with no undo
    POST  /projects/my/reminders/{reminder_id}/snooze     → a member move to a named time
    POST  /projects/my/reminders/{reminder_id}/reschedule → a member move to a named time

**Undo (D-CF-5).** A chip acts at once and offers Undo, so each answer that
it gives needs a way back. ``/reopen`` undoes a done or a drop: it puts back
the state that the answer's ledger row recorded as ``from_state``. A promote
makes a task, so it has no undo, and it is its own route, ``/promote``.

**A reminder is a nudge, not work** (D114.1), so it lives in its own table,
``reminders`` (migration 307), and never in ``pm_tasks``. "Make it a task"
promotes one into the member's personal project when it is work.

**Private to one member.** RLS binds the ORGANIZATION only. The member is a
second axis, so every statement here also filters on ``member_email``, and
the value comes from the session (:func:`_member`), never from a request
body or a query parameter (R5e). A colleague and an admin of the same org
read nothing, change nothing and count nothing. A miss is 404, never 403, so
the code does not tell a caller that a reminder of somebody else exists.

**One transaction for each change.** Each write and its ``reminder_events``
row run in one ``async with _tenant_session() as db:`` block. The session
commits on a clean exit and rolls back on any exception, so a 409 after a
partial write leaves nothing behind.

**One ledger row for each change**: ``created``, each move of the time
(``snoozed``, ``rescheduled``), each answer (``done``, ``dropped``,
``promoted``) and each edit (``edited``). An edit changes the title, the
notes, the kind, the link, the zone, the estimate or ``important``. It moves
no state and no time, and its ``detail`` lists the NAMES of the fields that
changed, never their values. A PATCH of ``fire_at`` writes ``rescheduled``,
and a PATCH of both writes both. A field sent with its stored value is not
a change. ``edited`` is the fifteenth event of migration 307: an engineering
decision of 2026-10-25, taken before 307 shipped.

**Who counts what** (§5.4). A move that the member asks for adds 1 to
``snoozes``: a snooze, a reschedule to a named time or to the smart slot,
and a PATCH of ``fire_at``. Only the loop (``gateway/reminders/loop.py``,
RM-2) moves a reminder with no answer, and only that adds to ``cycles``.
Every member move restarts the 72-hour window: ``first_fired_at`` clears
(RM-2, decision of 2026-10-26). A member move out of ``unconfirmed`` is a
fresh start: ``cycles`` goes back to 0 too, so the give-up limit does not
trip again at once. A move that the loop makes keeps both.

**Time.** A time with no UTC offset is a 422, because a naive instant reads
in the zone of the database session and fires at the wrong minute. ``tz``
is the IANA zone the member was in, stored as given once ``zoneinfo`` knows
it. ``/reschedule`` also takes ``"smart"``: the slot of §5.5
(``gateway/reminders/slot.py``), read in the session of the move. Every
other route answers 422 to it.

**The feed.** :func:`my_open_cards` is the read of the ``reminders`` source
of My Day (``routes/shell/feed.py``): the member's ``due`` and
``unconfirmed`` rows, each with its last step of the ladder.

**The open of a link (RM-4, §5.7).** :func:`open_reminder` is the ONE open.
It writes the ``opened`` row and closes a ``notice``. It is a function and
not a route here: ``POST /chat/sessions/{id}/reminder-open``
(``routes/chat_reminder.py``) calls it, and then posts the first message.

**The gate.** The ``/projects`` router gives ``feature:projects``. No second
gate applies. ``CALENDAR_REMINDERS`` off makes every route here answer 404
before the shape of the body is checked (:func:`_require_on`, a route
dependency). A body that is not JSON still answers 422, because FastAPI
decodes the JSON before any dependency runs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from acb_auth import UserContext, get_current_user
from acb_common import get_settings
from fastapi import Depends, HTTPException, Query
from gateway.routes.projects.core import (
    ACTOR_VIA,
    ListResponse,
    _tenant_session,
    actor,
    clean_payload,
    load_default_status,
    load_visible_project,
    load_visible_task,
    resolve_visibility,
    router,
    wire,
)
from gateway.routes.projects.personal import (
    create_personal_task,
    ensure_personal_project,
)
from gateway.routes.projects.text_links import link_from_text
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

#: Migration 307's state CHECK, in the order of §5.3.
STATES: tuple[str, ...] = (
    "scheduled", "due", "unconfirmed", "done", "dropped", "cancelled",
)
#: The states that still wait on the member or on the loop (§5.3, "Open?").
OPEN_STATES: tuple[str, ...] = ("scheduled", "due", "unconfirmed")
KINDS: tuple[str, ...] = ("action", "notice")
#: §5.1. The link is a pointer, and it copies nothing from the object.
SUBJECT_KINDS: tuple[str, ...] = (
    "task", "email_thread", "crm_contact", "wa_chat", "project",
)
#: The link kinds whose id is a row UUID. A thread id and a chat id are text.
_UUID_SUBJECTS = frozenset({"task", "crm_contact", "project"})
_SUBJECT_ID_MAX = 200
TITLE_MAX = 200
#: The longest act a reminder sizes its grace for (§5.4): one week, in
#: minutes. A larger guess is a mistake, not an estimate.
ESTIMATE_MAX = 7 * 24 * 60
#: How far ahead a snooze ("Later") may move a reminder: a time after now,
#: within this. A time past it is a new time, which `/reschedule` takes.
SNOOZE_MAX = timedelta(days=366)

#: The two answers of ``/resolve`` (§5.4) -> (state, resolution, ledger
#: event). ``/reopen`` undoes either. A promote is ``/promote``.
RESOLUTIONS: dict[str, tuple[str, str, str]] = {
    "done": ("done", "done", "done"),
    "drop": ("dropped", "dropped", "dropped"),
}


# ── The flag ───────────────────────────────────────────────────────────────


def reminders_enabled() -> bool:
    """The switch of the whole store (``calendar_reminders``, default off).

    The ONE reader of the setting. ``/version`` calls it, and so does
    :func:`_require_on`.
    """
    return bool(getattr(get_settings(), "calendar_reminders", False))


async def _require_on() -> None:
    """404 while the flag is off, and nothing is written.

    A route dependency, so it runs before FastAPI checks the SHAPE of the
    body: with the flag off, valid JSON of the wrong shape answers 404, not
    422. FastAPI decodes the JSON before any dependency runs, so a body that
    is not JSON at all answers 422 with the flag off. Neither writes a row.
    """
    if not reminders_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


_ON = [Depends(_require_on)]


# ── Models ─────────────────────────────────────────────────────────────────


class ReminderIn(BaseModel):
    """A new reminder. ``fire_at`` is ISO 8601 WITH an offset."""

    title: str
    fire_at: str
    tz: str | None = None
    kind: Literal["action", "notice"] = "action"
    notes: str | None = None
    subject_kind: str | None = None
    subject_id: str | None = None
    estimate_minutes: int | None = Field(None, ge=1, le=ESTIMATE_MAX)
    important: bool = False


class ReminderPatch(BaseModel):
    """A change to an open reminder. Only the fields sent change. ``null``
    clears ``notes``, ``tz``, ``estimate_minutes`` or the link."""

    title: str | None = None
    fire_at: str | None = None
    tz: str | None = None
    kind: Literal["action", "notice"] | None = None
    notes: str | None = None
    subject_kind: str | None = None
    subject_id: str | None = None
    estimate_minutes: int | None = Field(None, ge=1, le=ESTIMATE_MAX)
    important: bool | None = None


class MoveIn(BaseModel):
    """A member move to a named time (§5.4). ``tz`` replaces the stored zone
    when it is sent."""

    fire_at: str
    tz: str | None = None


class ResolveIn(BaseModel):
    """The member's answer: done or drop. ``/reopen`` undoes either. A
    promote is ``/promote``, so ``promote`` here is a 422."""

    action: Literal["done", "drop"]


class ReminderModel(BaseModel):
    """One reminder on the wire. It carries no tenant and no owner: the caller
    is the owner, and a response never shows the tenant."""

    id: str
    title: str
    notes: str | None = None
    kind: str
    subject_kind: str | None = None
    subject_id: str | None = None
    fire_at: str | None = None
    tz: str | None = None
    state: str
    next_check_at: str | None = None
    first_fired_at: str | None = None
    cycles: int = 0
    snoozes: int = 0
    estimate_minutes: int | None = None
    important: bool = False
    resolution: str | None = None
    resolved_at: str | None = None
    promoted_task_id: str | None = None
    source: str
    created_at: str | None = None
    updated_at: str | None = None


def _out(row: Any) -> dict[str, Any]:
    data = {name: wire(getattr(row, name, None)) for name in ReminderModel.model_fields}
    return ReminderModel(**data).model_dump()


# ── Inputs, checked before any write ─────────────────────────────────────


def _member(user: Any) -> str:
    """The owner, from the authenticated session only (R5e), lower case.

    A caller with no address would share one bucket with every other caller
    with no address, so it is refused rather than keyed as ``anonymous``.
    """
    email = actor(user).lower()
    if "@" not in email:
        raise HTTPException(
            status_code=403, detail="A reminder needs a signed-in member.",
        )
    return email


#: The value of ``fire_at`` that asks ``/reschedule`` for the smart slot.
SMART = "smart"


def _is_smart(value: Any) -> bool:
    return str(value or "").strip().lower() == SMART


def _instant(value: Any, field: str) -> datetime:
    """An ISO 8601 instant WITH an offset, as UTC. A naive time is a 422."""
    raw = str(value or "").strip()
    if _is_smart(raw):
        raise HTTPException(
            status_code=422,
            detail=f"{field}: only /reschedule takes the smart slot. Name a time.",
        )
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"{field} is not an ISO 8601 time: {raw!r}.",
        ) from None
    if when.tzinfo is None or when.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} needs a UTC offset, such as 2026-10-31T09:00:00+05:30.",
        )
    try:
        # A time at the edge of the calendar, such as year 1 with a positive
        # offset or year 9999 with a negative one, has no UTC form.
        return when.astimezone(UTC)
    except OverflowError:
        raise HTTPException(
            status_code=422, detail=f"{field} is out of range: {raw!r}.",
        ) from None


def _zone(value: str | None) -> str | None:
    """An IANA zone, stored as given, or None. An unknown zone is a 422."""
    clean = (value or "").strip()
    if not clean:
        return None
    try:
        ZoneInfo(clean)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: a key that names a directory of the zone database, such as
        # "America", fails to open as a file, and is not a zone.
        raise HTTPException(
            status_code=422, detail=f"tz is not an IANA zone: {clean!r}.",
        ) from None
    return clean


def _title(value: str | None) -> str:
    clean = (value or "").strip()
    if not clean:
        raise HTTPException(status_code=422, detail="A reminder needs a title.")
    if len(clean) > TITLE_MAX:
        raise HTTPException(
            status_code=422, detail=f"A title is at most {TITLE_MAX} characters.",
        )
    return clean


def _subject(kind: str | None, subject_id: str | None) -> tuple[str | None, str | None]:
    """The link of §5.1: both parts or neither. Migration 307 holds the pair."""
    kind = (kind or "").strip() or None
    subject_id = (subject_id or "").strip() or None
    if (kind is None) != (subject_id is None):
        raise HTTPException(
            status_code=422,
            detail="A link needs both subject_kind and subject_id, or neither.",
        )
    if kind is None:
        return None, None
    if kind not in SUBJECT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"subject_kind must be one of {', '.join(SUBJECT_KINDS)}.",
        )
    if kind in _UUID_SUBJECTS:
        try:
            subject_id = str(UUID(subject_id))
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"subject_id of a {kind} is a UUID.",
            ) from None
    elif len(subject_id) > _SUBJECT_ID_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"subject_id is at most {_SUBJECT_ID_MAX} characters.",
        )
    return kind, subject_id


def _source(user: Any) -> str:
    """Where the reminder came from (§5.2 ``source``), from the request's own
    provenance, never from a body field. ``X-Actor-Via`` is bound by the
    router (``core.capture_actor_via``)."""
    via = ACTOR_VIA.get("")
    if getattr(user, "agent", None) is not None or via.startswith("mcp:"):
        return "api"
    if via.startswith("chat:"):
        return "chat"
    # No caller sends a WhatsApp stamp yet. RM-9 adds that arm, and the
    # CHECK of migration 307 already holds `whatsapp` for it.
    return "app"


# ── SQL ────────────────────────────────────────────────────────────────────
#
# Every statement names `member_email = :me`. RLS adds the organization.

_MINE_FOR_UPDATE_SQL = (
    "SELECT * FROM reminders "
    "WHERE id = CAST(:id AS uuid) AND member_email = :me FOR UPDATE"
)


async def _load_mine(db: Any, reminder_id: UUID, me: str) -> Any:
    """My reminder, locked for this transaction, or 404.

    The lock serialises two answers to one reminder: the second waits, then
    reads the closed state and gets 409. So two promotes make one task.
    """
    row = (await db.execute(
        text(_MINE_FOR_UPDATE_SQL), {"id": str(reminder_id), "me": me},
    )).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Reminder not found")
    return row


def _require_open(row: Any) -> None:
    if row.state not in OPEN_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"This reminder is closed ({row.state}). It cannot change.",
        )


async def _ledger(
    db: Any, reminder_id: Any, event: str, detail: dict[str, Any] | None = None,
) -> None:
    """One ``reminder_events`` row, in the caller's transaction.

    ``organization_id`` comes from the column default, the bound tenant. The
    ledger has no member column: a read of it must join ``reminders`` on
    ``member_email``.

    ``at`` is ``clock_timestamp()``, not the column default ``now()``. One
    PATCH can write two rows, and ``now()`` is the same instant for each row
    of one transaction, so the order of the two would be lost.
    """
    await db.execute(
        text(
            "INSERT INTO reminder_events (reminder_id, event, detail, at) "
            "VALUES (CAST(:rid AS uuid), :event, CAST(:detail AS jsonb), "
            "clock_timestamp())"
        ),
        {"rid": str(reminder_id), "event": event, "detail": json.dumps(detail or {})},
    )


async def _bump(user: Any, me: str) -> None:
    """After the commit of a member write that changes the card: move the
    member's My Day version, so the feed reads the new state (RM-2). The org
    is the bound tenant that the write went to, which the session set
    (R11). It waits 0.5 s at most and never raises
    (``cache.bump_member_safe``), so the write stands."""
    from acb_common.db import current_tenant
    from gateway.routes.shell import cache as feed_cache

    org = current_tenant() or getattr(user, "organization_id", None)
    await feed_cache.bump_member_safe(org, me)


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


#: The columns a PATCH may write, in a fixed order. Identifiers come from
#: this tuple only. Every value is a bound parameter.
_PATCH_COLUMNS: tuple[str, ...] = (
    "title", "notes", "kind", "subject_kind", "subject_id", "fire_at", "tz",
    "estimate_minutes", "important",
)
#: The columns whose change is an ``edited`` row. Every PATCH column except
#: ``fire_at``, which is a move. ``tz`` is here: a new zone moves no instant.
_EDIT_COLUMNS: tuple[str, ...] = tuple(c for c in _PATCH_COLUMNS if c != "fire_at")


# ── Routes ─────────────────────────────────────────────────────────────────


@router.get("/my/reminders", dependencies=_ON)
async def list_reminders(
    state: str | None = Query(None),
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    user: UserContext = Depends(get_current_user),
) -> ListResponse:
    """My reminders, by ``fire_at``.

    With no ``state``, the open ones (§5.3). ``state=unconfirmed`` is the
    Unconfirmed list. ``start`` and ``end`` bound ``fire_at`` as
    ``start <= fire_at < end``, each with an offset. ``total`` counts every
    match, past ``limit`` too, and only mine.
    """
    me = _member(user)
    if state is None:
        states = list(OPEN_STATES)
    elif state in STATES:
        states = [state]
    else:
        raise HTTPException(
            status_code=422, detail=f"state must be one of {', '.join(STATES)}.",
        )
    where = ["member_email = :me", "state = ANY(:states)"]
    params: dict[str, Any] = {"me": me, "states": states}
    lower = _instant(start, "start") if start is not None else None
    upper = _instant(end, "end") if end is not None else None
    if lower is not None and upper is not None and upper <= lower:
        raise HTTPException(status_code=422, detail="end must be after start.")
    if lower is not None:
        where.append("fire_at >= :start")
        params["start"] = lower
    if upper is not None:
        where.append("fire_at < :end")
        params["end"] = upper
    clause = " AND ".join(where)
    async with _tenant_session() as db:
        rows = (await db.execute(
            text(
                f"SELECT * FROM reminders WHERE {clause} "
                "ORDER BY fire_at, created_at, id LIMIT :limit"
            ),
            {**params, "limit": limit},
        )).fetchall()
        total = (await db.execute(
            text(f"SELECT count(*) AS n FROM reminders WHERE {clause}"), params,
        )).scalar_one()
    return ListResponse(rows=[_out(r) for r in rows], total=int(total))


@router.post("/my/reminders", status_code=201, dependencies=_ON)
async def create_reminder(
    payload: ReminderIn, user: UserContext = Depends(get_current_user),
) -> dict:
    """Set one reminder. It starts ``scheduled`` and writes a ``created`` row."""
    me = _member(user)
    title = _title(payload.title)
    fire_at = _instant(payload.fire_at, "fire_at")
    tz = _zone(payload.tz)
    subject_kind, subject_id = _subject(payload.subject_kind, payload.subject_id)
    source = _source(user)
    async with _tenant_session() as db:
        row = (await db.execute(
            text(
                "INSERT INTO reminders (member_email, title, notes, kind, "
                "subject_kind, subject_id, fire_at, tz, estimate_minutes, "
                "important, source) "
                "VALUES (:me, :title, :notes, :kind, :subject_kind, :subject_id, "
                ":fire_at, :tz, :estimate_minutes, :important, :source) "
                "RETURNING *"
            ),
            {
                "me": me, "title": title, "notes": payload.notes,
                "kind": payload.kind, "subject_kind": subject_kind,
                "subject_id": subject_id, "fire_at": fire_at, "tz": tz,
                "estimate_minutes": payload.estimate_minutes,
                "important": payload.important, "source": source,
            },
        )).fetchone()
        await _ledger(db, row.id, "created", {"fire_at": _iso(fire_at), "tz": tz})
        return _out(row)


@router.patch("/my/reminders/{reminder_id}", dependencies=_ON)
async def update_reminder(
    reminder_id: UUID, payload: ReminderPatch,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Change an open reminder. A closed reminder is 409.

    A new ``fire_at`` is a member move: the reminder goes back to
    ``scheduled``, ``snoozes`` gains 1, and the ledger gains a
    ``rescheduled`` row. A change to any other field writes one ``edited``
    row that names the fields. A PATCH of both writes both rows.

    Only a REAL change counts. A ``fire_at`` that names the stored instant
    is not a move, in any offset. A field sent with its stored value is not
    an edit. A PATCH with no real change writes nothing, not even
    ``updated_at``, and answers with the row as it is."""
    me = _member(user)
    changes = clean_payload(payload)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to change.")
    for field in ("title", "fire_at", "kind", "important"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null.")
    values: dict[str, Any] = {}
    if "title" in changes:
        values["title"] = _title(changes["title"])
    if "fire_at" in changes:
        values["fire_at"] = _instant(changes["fire_at"], "fire_at")
    if "tz" in changes:
        values["tz"] = _zone(changes["tz"])
    for field in ("notes", "kind", "estimate_minutes", "important"):
        if field in changes:
            values[field] = changes[field]
    async with _tenant_session() as db:
        row = await _load_mine(db, reminder_id, me)
        _require_open(row)
        if "subject_kind" in changes or "subject_id" in changes:
            kind, subject_id = _subject(
                changes.get("subject_kind", row.subject_kind),
                changes.get("subject_id", row.subject_id),
            )
            values["subject_kind"], values["subject_id"] = kind, subject_id
        # Two aware datetimes compare as instants, so +05:30 and Z agree.
        moved = "fire_at" in values and values["fire_at"] != row.fire_at
        edited = [
            c for c in _EDIT_COLUMNS
            if c in values and values[c] != getattr(row, c, None)
        ]
        if not moved and not edited:
            return _out(row)
        changed = [*(["fire_at"] if moved else []), *edited]
        assignments = [f"{c} = :{c}" for c in _PATCH_COLUMNS if c in changed]
        if moved:
            assignments += [
                "state = 'scheduled'", "next_check_at = NULL",
                "snoozes = snoozes + 1", *_fresh_start(row),
            ]
        assignments.append("updated_at = now()")
        updated = (await db.execute(
            text(
                f"UPDATE reminders SET {', '.join(assignments)} "
                "WHERE id = CAST(:id AS uuid) AND member_email = :me RETURNING *"
            ),
            {**{c: values[c] for c in changed}, "id": str(reminder_id), "me": me},
        )).fetchone()
        if moved:
            await _ledger(db, row.id, "rescheduled", {
                "from": _iso(row.fire_at), "to": _iso(values["fire_at"]),
                "from_state": row.state, "by": "member", **_fresh_detail(row),
            })
        if edited:
            await _ledger(db, row.id, "edited", {"fields": edited, "by": "member"})
        result = _out(updated)
    await _bump(user, me)
    return result


def _fresh_start(row: Any) -> list[str]:
    """The assignments of a member move (§5.4, "Who counts what").

    Every member move restarts the 72-hour window: ``first_fired_at`` clears,
    so the limit counts from the next fire (decision of 2026-10-26). A move
    out of ``unconfirmed`` also sets ``cycles`` back to 0 (RM-1 review). The
    member is engaged again in both cases. A move that Metorite makes keeps
    both."""
    if row.state != "unconfirmed":
        return ["first_fired_at = NULL"]
    return ["cycles = 0", "first_fired_at = NULL"]


def _fresh_detail(row: Any) -> dict[str, Any]:
    """The ledger fact of :func:`_fresh_start`: what it cleared."""
    out: dict[str, Any] = {}
    if row.first_fired_at is not None:
        out["from_first_fired_at"] = _iso(row.first_fired_at)
    if row.state == "unconfirmed":
        out |= {"fresh_start": True, "from_cycles": int(row.cycles or 0)}
    return out


async def _smart_slot(db: Any, row: Any, me: str) -> datetime:
    """The smart slot of §5.5 for *row*, after now: ``slot.next_slot`` on the
    member's day and busy blocks, read on *db*, the reads of the loop."""
    from gateway.reminders import slot
    from gateway.reminders.loop import member_busy, member_day

    now = datetime.now(UTC)
    day = await member_day(db, me)
    busy = await member_busy(db, me, now, row.fire_at)
    return slot.next_slot(slot.MemberDay(day, busy), row, now)


async def _move(
    reminder_id: UUID, payload: MoveIn, user: Any, event: str, *,
    smart: bool = False,
) -> dict:
    """A member move: snooze and reschedule share it.

    The reminder goes back to ``scheduled`` at the new time, ``snoozes``
    gains 1, ``next_check_at`` clears, and the ledger gains ``event``. A move
    out of ``unconfirmed`` is a fresh start (:func:`_fresh_start`). With
    *smart*, the new time is the smart slot of §5.5, read in the session of
    the move.
    """
    me = _member(user)
    fire_at = None if smart else _instant(payload.fire_at, "fire_at")
    if event == "snoozed" and fire_at is not None:
        now = datetime.now(UTC)
        if not now < fire_at <= now + SNOOZE_MAX:
            raise HTTPException(
                status_code=422,
                detail=(
                    "A snooze moves a reminder to a time after now and within "
                    f"{SNOOZE_MAX.days} days. Use reschedule for another time."
                ),
            )
    tz_sent = "tz" in payload.model_fields_set
    tz = _zone(payload.tz) if tz_sent else None
    async with _tenant_session() as db:
        row = await _load_mine(db, reminder_id, me)
        _require_open(row)
        if smart:
            fire_at = await _smart_slot(db, row, me)
        fresh = "".join(f", {a}" for a in _fresh_start(row))
        updated = (await db.execute(
            text(
                "UPDATE reminders SET fire_at = :fire_at, "
                + ("tz = :tz, " if tz_sent else "")
                + "state = 'scheduled', next_check_at = NULL, "
                "snoozes = snoozes + 1" + fresh + ", updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND member_email = :me RETURNING *"
            ),
            {"fire_at": fire_at, "tz": tz, "id": str(reminder_id), "me": me},
        )).fetchone()
        await _ledger(db, row.id, event, {
            "from": _iso(row.fire_at), "to": _iso(fire_at),
            "from_state": row.state, "by": "member",
            **({"slot": SMART} if smart else {}), **_fresh_detail(row),
        })
        result = _out(updated)
    await _bump(user, me)
    return result


@router.post("/my/reminders/{reminder_id}/snooze", dependencies=_ON)
async def snooze_reminder(
    reminder_id: UUID, payload: MoveIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """"Later", or a named time: a ``snoozed`` ledger row."""
    return await _move(reminder_id, payload, user, "snoozed")


@router.post("/my/reminders/{reminder_id}/reschedule", dependencies=_ON)
async def reschedule_reminder(
    reminder_id: UUID, payload: MoveIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """A named time, or ``"smart"`` for the smart slot of §5.5 (RM-2). A
    ``rescheduled`` ledger row. "Not yet" with no time is the smart one."""
    return await _move(reminder_id, payload, user, "rescheduled",
                       smart=_is_smart(payload.fire_at))


@router.post("/my/reminders/{reminder_id}/resolve", dependencies=_ON)
async def resolve_reminder(
    reminder_id: UUID, payload: ResolveIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """The member's answer, done or drop. A closed reminder is 409.

    The ledger row records ``from_state`` and ``from_next_check_at``, the
    state and the check time before the answer, so ``/reopen`` can put both
    back. That is the Undo of the chip (D-CF-5).
    """
    me = _member(user)
    state, resolution, event = RESOLUTIONS[payload.action]
    async with _tenant_session() as db:
        row = await _load_mine(db, reminder_id, me)
        _require_open(row)
        updated = await _close(db, reminder_id, me, state, resolution)
        await _ledger(db, row.id, event, {
            "from_state": row.state,
            "from_next_check_at": _iso(row.next_check_at),
            "by": "member",
        })
        result = _out(updated)
    await _bump(user, me)
    return result


async def _close(
    db: Any, reminder_id: UUID, me: str, state: str, resolution: str,
    task_id: str | None = None,
) -> Any:
    """End an open reminder in ``state`` with ``resolution``."""
    return (await db.execute(
        text(
            "UPDATE reminders SET state = :state, resolution = :resolution, "
            "resolved_at = now(), next_check_at = NULL, "
            "promoted_task_id = COALESCE(CAST(:task AS uuid), promoted_task_id), "
            "updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND member_email = :me RETURNING *"
        ),
        {
            "state": state, "resolution": resolution, "task": task_id,
            "id": str(reminder_id), "me": me,
        },
    )).fetchone()


#: What ``/reopen`` may undo: (state, resolution). A promote made a task, and
#: evidence (RM-6) or a cleared condition (RM-7) is not the member's answer.
_REOPENABLE: frozenset[tuple[str, str]] = frozenset({
    ("done", "done"), ("dropped", "dropped"),
})

#: The answer's own ledger row, the newest one, for the state to put back.
_LAST_ANSWER_SQL = (
    "SELECT detail FROM reminder_events "
    "WHERE reminder_id = CAST(:id AS uuid) AND event IN ('done', 'dropped') "
    "ORDER BY at DESC, id DESC LIMIT 1"
)


@router.post("/my/reminders/{reminder_id}/reopen", dependencies=_ON)
async def reopen_reminder(
    reminder_id: UUID, user: UserContext = Depends(get_current_user),
) -> dict:
    """Undo a done or a drop (D-CF-5): the reminder is open again.

    It puts back the state and the check time that the answer's ledger row
    recorded, clears ``resolution`` and ``resolved_at``, and writes a
    ``reopened`` row. Only state ``done`` with resolution ``done``, or state
    ``dropped``, may reopen. A promoted reminder, an open one, or any other
    end is 409. A row with no usable record reopens as ``scheduled``.
    """
    me = _member(user)
    async with _tenant_session() as db:
        row = await _load_mine(db, reminder_id, me)
        if (row.state, row.resolution) not in _REOPENABLE:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Only a reminder that is done or dropped can reopen. "
                    f"This one is {row.state}"
                    + (f" ({row.resolution})." if row.resolution else ".")
                ),
            )
        # The ledger has no member column. The row above is mine, and the
        # read names only its id, so it reads only my ledger.
        found = (await db.execute(
            text(_LAST_ANSWER_SQL), {"id": str(reminder_id)},
        )).scalar()
        record = found if isinstance(found, dict) else json.loads(found or "{}")
        to_state = record.get("from_state")
        if to_state not in OPEN_STATES:
            to_state = "scheduled"
        check_at: datetime | None = None
        raw_check = record.get("from_next_check_at") if to_state == "due" else None
        if isinstance(raw_check, str):
            try:
                check_at = datetime.fromisoformat(raw_check)
            except ValueError:
                check_at = None
        updated = (await db.execute(
            text(
                "UPDATE reminders SET state = :state, resolution = NULL, "
                "resolved_at = NULL, next_check_at = :check, "
                "updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND member_email = :me RETURNING *"
            ),
            {"state": to_state, "check": check_at, "id": str(reminder_id), "me": me},
        )).fetchone()
        await _ledger(db, row.id, "reopened", {
            "from_state": row.state, "to_state": to_state, "by": "member",
        })
        result = _out(updated)
    await _bump(user, me)
    return result


@router.post("/my/reminders/{reminder_id}/promote", dependencies=_ON)
async def promote_reminder(
    reminder_id: UUID, user: UserContext = Depends(get_current_user),
) -> dict:
    """"Make it a task". A closed reminder is 409, and there is no undo.

    It makes exactly one private task in the member's personal project
    through the capture path (``ensure_personal_project``,
    ``load_default_status``, ``create_personal_task``, as
    ``POST /projects/my/tasks`` does), in this same session. Then it ends
    the reminder in ``done`` with resolution ``promoted``. A failure
    anywhere rolls the task back with the reminder.
    """
    me = _member(user)
    async with _tenant_session() as db:
        row = await _load_mine(db, reminder_id, me)
        _require_open(row)
        project = await ensure_personal_project(db, me)
        project_id = str(project.id)
        status = await load_default_status(db, project_id)
        task = await create_personal_task(
            db, me, project_id, project_id, str(status.id), {
                "title": row.title,
                "description": row.notes,
                "source": "manual",
            })
        task_id = str(task.id)
        updated = await _close(db, reminder_id, me, "done", "promoted", task_id)
        await _ledger(db, row.id, "promoted", {
            "from_state": row.state, "task_id": task_id, "by": "member",
        })
        result = _out(updated)
    # WS-57 TR-5 — the new task's title and notes, after the commit, as the
    # capture route does. It runs in its own transaction and never fails the
    # promote.
    await link_from_text(user, [(task_id, [(row.title, False, None), (row.notes, True, None)])])
    await _bump(user, me)
    return result


# ── The open of a link (RM-4) ──────────────────────────────────────────────

#: The ledger tail of the context block, newest first. The ledger has no
#: member column, so :func:`open_reminder` runs this only after
#: :func:`_load_mine` found the row, and it names only that row's id.
_LEDGER_TAIL_SQL = (
    "SELECT event, at FROM reminder_events "
    "WHERE reminder_id = CAST(:id AS uuid) "
    "ORDER BY at DESC, id DESC LIMIT :n"
)

#: The status name of a task the member may see. RLS binds the organization.
_STATUS_NAME_SQL = "SELECT name FROM pm_task_statuses WHERE id = CAST(:id AS uuid)"


async def _subject_of(db: Any, user: Any, row: Any) -> Any:
    """The linked object of *row*, as the member may see it (§5.7 item 2).

    A task and a project go through the one visible read of the Projects API
    (``resolve_visibility``, then ``load_visible_task`` or
    ``load_visible_project``). A miss is ``hidden``, so no word of the object
    shows. The read runs in a SAVEPOINT, so a refusal or a failure there
    never ends the open. RM-4 reads no other kind: an email thread, a CRM
    contact or a WhatsApp chat shows its kind only.
    """
    from gateway.reminders.context import READ_SUBJECTS, Subject

    kind, subject_id = row.subject_kind, row.subject_id
    if not kind or not subject_id:
        return None
    if kind not in READ_SUBJECTS:
        return Subject(kind, subject_id=subject_id)
    try:
        async with db.begin_nested():
            vis = await resolve_visibility(db, user)
            if kind == "task":
                task = await load_visible_task(db, vis, subject_id)
                status = (await db.execute(
                    text(_STATUS_NAME_SQL), {"id": str(task.status_id)},
                )).scalar()
                return Subject(kind, subject_id=subject_id, title=task.title, status=status)
            project = await load_visible_project(db, vis, subject_id)
            return Subject(kind, subject_id=subject_id, title=project.name)
    except (HTTPException, SQLAlchemyError):
        return Subject(kind, hidden=True)


async def _zone_of(db: Any, row: Any, me: str) -> ZoneInfo:
    """The zone of the words: the row's ``tz``, else the member's saved zone
    (``user_settings.timezone``, through the loop's read), else UTC."""
    from gateway.routes.shell.state import zone_of

    if row.tz:
        return zone_of(row.tz)
    from gateway.reminders.loop import member_day

    return (await member_day(db, me)).tz


async def open_reminder(
    user: Any, reminder_id: UUID, *, record: bool = True,
) -> dict[str, Any]:
    """A tap on a reminder link (§5.7, RM-4). The ONE open.

    It loads the member's own row, locked (:func:`_load_mine`). Any other
    row is 404, and nothing is written.

    With *record*, an open reminder gains one ``opened`` ledger row (§5.7
    item 5). A ``notice`` in state ``due`` then closes as ``done``. Its
    ``done`` row records ``from_state`` and ``from_next_check_at``, as
    ``/resolve`` writes them, so ``/reopen`` still undoes it. An ``action``
    reminder stays as it is, because an open is not an answer (item 6). A
    closed reminder writes nothing and answers with its state, never 409,
    so a second tap is safe. Without *record* (the same chat asks again),
    nothing is written.

    In the same transaction it reads the linked object as the member may see
    it, the zone, and the ledger tail of THIS row. The ledger has no member
    column, so the tail comes after the load and names only this row's id.

    It returns the row, the first message, the card data and the context
    block. ``gateway/reminders/context.py`` builds the words, with no model.
    """
    from gateway.reminders import context

    me = _member(user)
    now = datetime.now(UTC)
    closed = False
    async with _tenant_session() as db:
        row = await _load_mine(db, reminder_id, me)
        if record and row.state in OPEN_STATES:
            await _ledger(db, row.id, "opened", {"by": "member"})
            if row.kind == "notice" and row.state == "due":
                before = row
                row = await _close(db, reminder_id, me, "done", "done")
                await _ledger(db, row.id, "done", {
                    "from_state": before.state,
                    "from_next_check_at": _iso(before.next_check_at),
                    "by": "member", "via": "open",
                })
                closed = True
        subject = await _subject_of(db, user, row)
        zone = await _zone_of(db, row, me)
        ledger = (await db.execute(
            text(_LEDGER_TAIL_SQL), {"id": str(row.id), "n": context.LEDGER_TAIL},
        )).fetchall()
        result = {
            "reminder": _out(row),
            "message": context.first_message(row, zone=zone, now=now, subject=subject),
            "card": context.card_data(row, zone=zone, now=now, subject=subject),
            "context": {
                "label": context.label(row),
                "facts": context.context_facts(
                    row, zone=zone, now=now, subject=subject, ledger=ledger,
                ),
            },
        }
    if closed:
        await _bump(user, me)
    return result


# ── The read of My Day (RM-2) ──────────────────────────────────────────────

#: The ladder events that say which card a row makes: the last of them since
#: the row was made. ``fired`` gives ``reminder_due``, ``check_back`` gives
#: ``reminder_check``, and ``gave_up`` comes before ``unconfirmed``.
CARD_STEPS: tuple[str, ...] = ("fired", "check_back", "gave_up")

#: My rows that make a card, each with its last ladder step. The ledger has
#: no member column, so the read joins it through my own rows only.
#:
#: ⚠️ The order decides which cards survive the LIMIT (review of RM-2, P1).
#: `due` rows come first, then the newest step, so a reminder that just
#: fired is never cut by old `unconfirmed` ones. A row with no ladder step
#: takes its `fire_at`.
_CARDS_SQL = (
    "SELECT r.id, r.title, r.kind, r.state, r.fire_at, r.next_check_at, "
    "r.cycles, r.important, s.step, s.step_at "
    "FROM reminders r LEFT JOIN LATERAL ("
    "  SELECT e.event AS step, e.at AS step_at FROM reminder_events e "
    "   WHERE e.reminder_id = r.id AND e.event = ANY(CAST(:steps AS text[])) "
    "   ORDER BY e.at DESC, e.id DESC LIMIT 1) s ON true "
    "WHERE r.member_email = :me AND r.state IN ('due', 'unconfirmed') "
    "ORDER BY (r.state = 'due') DESC, COALESCE(s.step_at, r.fire_at) DESC, r.id "
    "LIMIT :limit"
)

#: The next instant at which the loop changes one of my rows: a fire, or the
#: next step of a `due` row.
_EDGE_SQL = (
    "SELECT min(CASE WHEN state = 'scheduled' THEN fire_at ELSE next_check_at END) "
    "FROM reminders WHERE member_email = :me "
    "AND ((state = 'scheduled' AND fire_at > :now) "
    "  OR (state = 'due' AND kind = 'action' AND next_check_at > :now))"
)


async def my_open_cards(
    user: Any, *, now: datetime, limit: int,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """My reminders that make a My Day card, and the next edge (RM-2).

    The rows are the ``due`` and ``unconfirmed`` ones, ``due`` first and
    then the newest step first, at most *limit*. The edge is the next ``fire_at`` or
    ``next_check_at`` after *now*, or None. The member comes from the
    session (:func:`_member`), and one tenant session reads both. The
    caller checks the flag: the feed provider raises ``SourceAbsent`` first.
    """
    me = _member(user)
    async with _tenant_session() as db:
        rows = (await db.execute(text(_CARDS_SQL), {
            "me": me, "steps": list(CARD_STEPS), "limit": limit,
        })).fetchall()
        edge = (await db.execute(text(_EDGE_SQL), {"me": me, "now": now})).scalar()
    return [dict(r._mapping) for r in rows], edge
