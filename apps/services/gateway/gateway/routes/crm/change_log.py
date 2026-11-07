"""CRM · change history — the ONE log of a change of the CRM set-up (WS-CRM CRM-M1).

Spec: ``project-docs/specs/crm_conversational.md`` §8 CRM-M1 and §4 (D164).

    GET   /crm/change-history                     the changes, newest first
    POST  /crm/change-history/{change_id}/undo    undo one change

**The one seam.** :func:`log_change` is the ONE writer of ``crm_change_log``
(migration 341). Each logged config route calls it with the session of its
own write, so the row commits or rolls back with the change. It is NOT
``acb_audit.record``: that writes best-effort and outside the transaction,
and an Undo needs a log that holds only changes that happened. A route that
writes a logged area and makes no call here is a defect.
``tests/unit/test_crm_change_history.py`` lists each write route of this
package: logged, pending for PR 2, or out with a reason.

**The row.** Who made the change (the session's member, never the body),
when, through what (:data:`VIAS`), who approved it, the level of the trust
ladder (:data:`LADDER`), the area (:data:`AREAS`), the action, a one-line
summary, the value before and after, the CRM mode at that time, and whether
an Undo can reverse it. ``approved_by`` is the actor in CRM-M1. The approval
card of the ladder is CRM-M2.

**Who reads.** One predicate, :data:`_VISIBLE_SQL`. Row level security binds
the org, and each statement names the org too. An admin
(``admin:access:manage``) reads each row that is not private to another
member. A member who is not an admin reads only the rows of their own
changes. ``private_to`` makes a row private: a change of a private saved
view, or of the business rule of one member. An Undo row has the same
readers as the row it undoes (:func:`log_change`).

**Undo.** ``POST /crm/change-history/{id}/undo`` writes the stored "before"
through the SAME code path as the change. Each feature module registers the
function of its areas with :func:`undoes`, so this module imports none of
them. That function calls the module's own write helper with ``undoes=``,
and the helper logs a new row that names the row it undoes. Then
:func:`log_change` sets ``undone_by`` on the old row, once. The refusals are
409s with a plain reason: a change that cannot be undone, a change that is
undone already, a CRM mode that changed since, a thing that changed again
since, a thing that is gone, a stage that now holds records, and an old
name that another stage or lost reason holds now (``name_taken``). Who may
undo: the permission of the route of the change. An admin area needs an
admin, and a view or a rule keeps its own owner check.

The organization comes from the bound tenant (R5, R11), never from the
request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user, require_internal_auth
from fastapi import Depends, HTTPException, Query, Request
from gateway.routes.crm.access import ADMIN_PERMISSION
from gateway.routes.crm.core import (
    MAX_PAGE_SIZE,
    _tenant_session,
    actor,
    checked_uuid,
    current_tenant,
    from_jsonb,
    router,
    wire,
)
from pydantic import BaseModel
from sqlalchemy import text

#: Through what a change came. CRM-M1 writes ``ui``, and the confirm of a
#: business rule writes ``chat`` or ``whatsapp``. The CHECK of migration 341
#: holds the same five words.
VIA_UI = "ui"
VIA_CHAT = "chat"
VIA_WHATSAPP = "whatsapp"
VIAS: tuple[str, ...] = (VIA_UI, VIA_CHAT, VIA_WHATSAPP, "ai_proposal", "system")

#: The header in which an in-product agent names itself (``chat:crm-assistant``).
#: The Projects skill sends the same header (``routes/projects/core.ACTOR_VIA``).
ACTOR_VIA_HEADER = "X-Actor-Via"

#: A chat stamp: ``chat:`` and an agent name. An ``mcp:`` stamp is an outside
#: agent (branch 0 of ``acb_auth.deps``), and it never reads as ``chat``.
_CHAT_STAMP = re.compile(r"^chat:[a-z0-9][a-z0-9_.-]{0,58}$")

#: The trust ladder (D164, ``crm_conversational.md`` §4).
OWN = "own"
TEAM = "team"
COMPANY = "company"
ALWAYS_ASK = "always_ask"
LADDER: tuple[str, ...] = (OWN, TEAM, COMPANY, ALWAYS_ASK)

#: The three actions. A create has no before, a delete has no after.
CREATE = "create"
CHANGE = "change"
DELETE = "delete"
ACTIONS: tuple[str, ...] = (CREATE, CHANGE, DELETE)

#: Each area of a change → the name a person reads. CRM-M1 PR 2 adds the
#: field studio, the conversion map and the rest (spec §8 CRM-M1).
AREAS: dict[str, str] = {
    "access": "Access",
    "setup": "CRM setup",
    "record_names": "Record names",
    "deal_stage": "Deal stage",
    "lead_status": "Lead status",
    "lost_reason": "Lost reason",
    "pipeline": "Pipeline",
    "pipeline_stage": "Pipeline stage",
    "view": "Saved view",
    "rule": "Business rule",
}

#: The areas whose routes need ``admin:access:manage``. Their Undo needs it too.
ADMIN_AREAS: frozenset[str] = frozenset({
    "access", "setup", "record_names", "deal_stage", "lead_status",
    "lost_reason", "pipeline", "pipeline_stage",
})

#: The longest summary. The CHECK of migration 341 holds the same number.
SUMMARY_MAX = 500


@dataclass(frozen=True)
class Change:
    """One change, as a write helper describes it to :func:`log_change`."""

    area: str
    action: str
    summary: str
    ladder: str
    entity_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    #: False for a change that no Undo can reverse (a change of the mode).
    reversible: bool = True
    #: The one member who may read the row, or None.
    private_to: str | None = None


@dataclass(frozen=True)
class Logged:
    """One row of ``crm_change_log``, as an Undo function reads it."""

    id: str
    area: str
    action: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    mode: str | None
    actor_email: str
    private_to: str | None


#: What an Undo function returns: None, or work to run after the commit.
AfterCommit = Callable[[], Awaitable[None]]
UndoFn = Callable[[Any, UserContext, Logged], Awaitable[AfterCommit | None]]

_UNDO: dict[str, UndoFn] = {}


def undoes(*areas: str) -> Callable[[UndoFn], UndoFn]:
    """Register the Undo function of *areas*. One function for each area."""
    def register(fn: UndoFn) -> UndoFn:
        for area in areas:
            if area not in AREAS:
                raise ValueError(f"unknown change area {area!r}")
            if area in _UNDO:
                raise ValueError(f"the area {area!r} has an Undo already")
            _UNDO[area] = fn
        return fn
    return register


def undo_areas() -> frozenset[str]:
    """The areas with an Undo function. The fence reads it."""
    return frozenset(_UNDO)


def plain(values: dict[str, Any] | None) -> dict[str, Any] | None:
    """*values* with each value in its JSON form (UUIDs and instants as text)."""
    if values is None:
        return None

    def one(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): one(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [one(v) for v in value]
        return wire(value)

    return {str(k): one(v) for k, v in values.items()}


def diff(
    before: dict[str, Any], after: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The keys whose value differs, as (before, after). Both empty: no change."""
    old, new = plain(before) or {}, plain(after) or {}
    keys = [k for k in new if old.get(k) != new.get(k)]
    return {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}


def quoted(value: Any, limit: int = 80) -> str:
    """A name inside a summary: one line, cut at *limit* characters."""
    one_line = " ".join(str(value if value is not None else "").split())
    if len(one_line) > limit:
        one_line = one_line[: limit - 1].rstrip() + "…"
    return f'"{one_line}"'


def _email(value: str | None) -> str | None:
    folded = (value or "").strip().lower()
    return folded or None


async def via_of(request: Request) -> str:
    """``chat`` when an in-product agent made this request, else ``ui``.

    The server decides it, and the body never does (WS-CRM CRM-M3a). Two
    facts must both hold:

    1. The request sends ONE ``X-Actor-Via`` header with a ``chat:`` stamp.
       The CRM assistant sends ``chat:crm-assistant`` on each call.
    2. Its ``Authorization`` is the internal bearer. Only an in-platform
       caller holds it: the agent runtime and the BFF. The BFF builds its
       upstream headers from ``gatewayHeaders()`` alone and forwards no
       header of the browser (``app/api/crm/[...path]/route.ts``). So a
       browser cannot put the stamp on a request that reaches the gateway.

    Each other request is ``ui``: no stamp, two stamps, a stamp of another
    shape, or a stamp with no internal bearer. A box with no internal token
    is a dev box, where ``get_current_user`` trusts a bare ``X-User-Email``
    too, so this check is never weaker than the identity of the row.
    Fence: ``tests/unit/test_crm_pipelines_native.py``.
    """
    stamps = request.headers.getlist(ACTOR_VIA_HEADER)
    if len(stamps) != 1 or not _CHAT_STAMP.fullmatch(stamps[0].strip().lower()):
        return VIA_UI
    authorization = request.headers.get("authorization") or ""
    if not authorization.startswith("Bearer ") or not authorization[7:].strip():
        return VIA_UI
    try:
        await require_internal_auth(authorization)
    except HTTPException:
        return VIA_UI
    return VIA_CHAT


#: The mode of the bound org. The text of ``core.is_mirror_mode``.
_MODE_SQL = (
    "SELECT mode FROM crm_settings WHERE organization_id = CAST(:organization_id AS uuid)"
)


def _bound_org() -> str | None:
    """The bound tenant. None makes each statement fail closed: no row reads,
    and the INSERT meets the NOT NULL of ``organization_id``."""
    org = current_tenant()
    return str(org) if org else None


async def current_mode(db: Any) -> str | None:
    """The CRM mode of the bound org: ``native``, ``mirror`` or None."""
    row = (await db.execute(
        text(_MODE_SQL), {"organization_id": _bound_org()},
    )).first()
    mode = getattr(row, "mode", None) if row is not None else None
    return mode if mode in ("native", "mirror") else None


_INSERT_SQL = (
    "INSERT INTO crm_change_log (organization_id, actor_email, via, approved_by, "
    "ladder_level, area, action, entity_id, summary, before, after, mode, "
    "reversible, private_to, undoes_id) VALUES (CAST(:organization_id AS uuid), "
    ":actor_email, :via, :approved_by, :ladder_level, :area, :action, :entity_id, "
    ":summary, CAST(:before AS jsonb), CAST(:after AS jsonb), :mode, :reversible, "
    ":private_to, CAST(:undoes_id AS uuid)) RETURNING id"
)

#: The readers of the row that an Undo undoes. The Undo route holds that row
#: FOR UPDATE already, in the same transaction.
_UNDONE_READERS_SQL = (
    "SELECT private_to FROM crm_change_log "
    "WHERE organization_id = CAST(:organization_id AS uuid) AND id = CAST(:id AS uuid)"
)

#: The old row gets the id of its Undo, once. No row back: undone already.
_MARK_UNDONE_SQL = (
    "UPDATE crm_change_log SET undone_by = CAST(:undone_by AS uuid) "
    "WHERE organization_id = CAST(:organization_id AS uuid) "
    "AND id = CAST(:id AS uuid) AND undone_by IS NULL RETURNING id"
)


async def log_change(
    db: Any, user: Any, change: Change, *, via: str = VIA_UI,
    undoes: str | None = None,
) -> str:
    """Write one row in the transaction of *db*. Returns its id.

    THE writer of ``crm_change_log``. The caller holds the session of the
    change itself, so a change that rolls back takes its row with it. The
    actor is the session's member. ``undoes`` names the row that this
    change undoes: the new row then cannot be undone, and the old row gets
    ``undone_by``. A second Undo of one row is a 409.

    **An Undo row has the same readers as the row it undoes** (fix round
    2). With ``undoes``, the new row takes the ``private_to`` of the undone
    row, and the value of the caller is ignored. So a row and its Undo live
    and die together: the member purge (``crm_private_changes``) deletes
    both or neither, and its count matches the rows that go. A view can
    change between shared and private between the two rows, so the state of
    the thing now is not the rule.
    """
    if change.area not in AREAS:
        raise ValueError(f"unknown change area {change.area!r}")
    if change.action not in ACTIONS or change.ladder not in LADDER or via not in VIAS:
        raise ValueError("unknown change action, ladder level or via")
    summary = " ".join(change.summary.split())
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 1].rstrip() + "…"
    who = _email(actor(user)) or "anonymous"
    org = _bound_org()
    before, after = plain(change.before), plain(change.after)
    private_to = _email(change.private_to)
    if undoes is not None:
        undone = (await db.execute(text(_UNDONE_READERS_SQL), {
            "organization_id": org, "id": undoes,
        })).first()
        if undone is None:
            raise _refuse(404, "no_change", "There is no such change.")
        private_to = _email(undone.private_to)
    row = (await db.execute(text(_INSERT_SQL), {
        "organization_id": org,
        "actor_email": who,
        "via": via,
        # CRM-M1: the actor approves their own change. CRM-M2 adds the card.
        "approved_by": who,
        "ladder_level": change.ladder,
        "area": change.area,
        "action": change.action,
        "entity_id": change.entity_id,
        "summary": summary,
        "before": json.dumps(before) if before is not None else None,
        "after": json.dumps(after) if after is not None else None,
        "mode": await current_mode(db),
        "reversible": bool(change.reversible and undoes is None),
        "private_to": private_to,
        "undoes_id": undoes,
    })).first()
    new_id = str(row.id)
    if undoes is not None:
        marked = (await db.execute(text(_MARK_UNDONE_SQL), {
            "organization_id": org, "undone_by": new_id, "id": undoes,
        })).first()
        if marked is None:
            raise _refuse(409, "already_undone", "This change is undone already.")
    return new_id


# ── Refusals ────────────────────────────────────────────────────────────────


def _refuse(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def gone(area: str) -> HTTPException:
    """The thing of a change is gone, so no Undo can reach it."""
    noun = AREAS.get(area, "item").lower()
    return _refuse(409, "gone", f"The {noun} is gone, so this change cannot be undone.")


def changed_since() -> HTTPException:
    """The thing changed again after this change."""
    return _refuse(
        409, "changed_since",
        "This changed again after this change. Undo the newer change first.",
    )


def in_use(message: str) -> HTTPException:
    """A thing that the Undo would remove now holds records."""
    return _refuse(409, "in_use", message)


def name_taken(noun: str, name: Any) -> HTTPException:
    """The old name of an Undo is the name of another row now (fix round 2).

    The names of the stages, the lead statuses and the lost reasons are
    unique in an org (migration 241). Without this, the Undo of a rename to
    a name that is taken again is a driver error and a 500."""
    return _refuse(409, "name_taken", (
        f"Another {noun} is named {quoted(name)} now, so the old name cannot "
        f"come back. Rename that {noun} first, and then undo this change."
    ))


def same_values(current: dict[str, Any], expected: dict[str, Any] | None) -> bool:
    """True when each key of *expected* has that value in *current*."""
    now, want = plain(current) or {}, plain(expected) or {}
    return all(now.get(k) == v for k, v in want.items())


# ── The read ────────────────────────────────────────────────────────────────

#: The ONE predicate of who reads a row, inside the org of the session.
_VISIBLE_SQL = (
    "organization_id = CAST(:organization_id AS uuid) "
    "AND (private_to IS NULL OR private_to = :me) "
    "AND (CAST(:admin AS boolean) OR actor_email = :me)"
)

_COLUMNS = (
    "id, at, actor_email, via, approved_by, ladder_level, area, action, entity_id, "
    "summary, before, after, mode, reversible, private_to, undoes_id, undone_by"
)


class ChangeOut(BaseModel):
    id: str
    at: str
    #: The member who made the change, and their name in the directory.
    actor_email: str
    actor_name: str | None = None
    via: str
    approved_by: str | None = None
    ladder_level: str
    area: str
    #: The name of the area for a person ("Deal stage").
    area_label: str
    action: str
    entity_id: str | None = None
    summary: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    mode: str | None = None
    reversible: bool
    undoes_id: str | None = None
    undone_by: str | None = None
    #: The caller may undo it now: it is reversible, not undone, the mode is
    #: the same, and the caller holds the permission of its area.
    can_undo: bool = False


class ChangesOut(BaseModel):
    """The list shape of the CRM (``core.ListResponse``), with typed rows."""

    rows: list[ChangeOut]
    total: int


class UndoOut(BaseModel):
    #: The row of the Undo itself.
    change: ChangeOut
    #: The id of the row that it undid.
    undid: str


def _me(user: Any) -> str:
    me = _email(getattr(user, "email", None))
    if me is None or "@" not in me:
        raise HTTPException(status_code=403, detail="Forbidden: no member for this call.")
    return me


def _is_admin(user: Any) -> bool:
    return bool(user.has_permission(ADMIN_PERMISSION))


def _organization(user: Any) -> str:
    if not getattr(user, "organization_id", None):
        raise HTTPException(status_code=403, detail="Forbidden: you are in no organization.")
    return str(user.organization_id)


def _instant(name: str, value: str | None, *, end: bool) -> datetime | None:
    """An ISO date or date-time as an instant. A date is a day in UTC, and
    an ``until`` date takes the whole of that day. A bad value is a 422."""
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            start = datetime(day.year, day.month, day.day, tzinfo=UTC)
            return start + timedelta(days=1) if end else start
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=(
            f"'{name}' is a date (YYYY-MM-DD) or a date and time in ISO 8601."
        )) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _row_out(row: Any, names: dict[str, str], *, admin: bool, me: str,
             mode: str | None) -> ChangeOut:
    area = str(row.area)
    allowed = admin if area in ADMIN_AREAS else (str(row.actor_email) == me or admin)
    undone = row.undone_by is not None
    return ChangeOut(
        id=str(row.id),
        at=(
            row.at.astimezone(UTC).isoformat() if isinstance(row.at, datetime)
            else str(row.at)
        ),
        actor_email=str(row.actor_email),
        actor_name=names.get(str(row.actor_email)),
        via=str(row.via),
        approved_by=row.approved_by,
        ladder_level=str(row.ladder_level),
        area=area,
        area_label=AREAS.get(area, area),
        action=str(row.action),
        entity_id=row.entity_id,
        summary=str(row.summary),
        before=from_jsonb(row.before),
        after=from_jsonb(row.after),
        mode=row.mode,
        reversible=bool(row.reversible),
        undoes_id=str(row.undoes_id) if row.undoes_id else None,
        undone_by=str(row.undone_by) if row.undone_by else None,
        can_undo=bool(
            row.reversible and not undone and row.mode == mode
            and area in _UNDO and allowed
        ),
    )


async def _names(db: Any, rows: list[Any]) -> dict[str, str]:
    from gateway.routes.people.core import names_of

    return await names_of(db, {str(r.actor_email) for r in rows})


@router.get("/change-history", response_model=ChangesOut)
async def list_changes(
    since: str | None = Query(None, description="From this date or instant."),
    until: str | None = Query(None, description="Up to this date or instant."),
    area: str | None = Query(None, description="One area of AREAS."),
    actor_email: str | None = Query(None, alias="actor", description="One member's email."),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    user: UserContext = Depends(get_current_user),
) -> ChangesOut:
    """The changes of the CRM set-up that the caller may read, newest first."""
    org = _organization(user)
    me = _me(user)
    admin = _is_admin(user)
    clauses = [_VISIBLE_SQL]
    params: dict[str, Any] = {"organization_id": org, "me": me, "admin": admin}
    start = _instant("since", since, end=False)
    stop = _instant("until", until, end=True)
    if start is not None:
        clauses.append("at >= :since")
        params["since"] = start
    if stop is not None:
        clauses.append("at < :until")
        params["until"] = stop
    if area is not None:
        if area not in AREAS:
            raise HTTPException(status_code=422, detail=(
                f"Unknown area '{area}'. One of: {list(AREAS)}."
            ))
        clauses.append("area = :area")
        params["area"] = area
    if actor_email is not None and actor_email.strip():
        clauses.append("actor_email = :actor")
        params["actor"] = actor_email.strip().lower()
    where = " AND ".join(clauses)
    async with _tenant_session() as db:
        total = int((await db.execute(
            text(f"SELECT count(*) FROM crm_change_log WHERE {where}"), params,
        )).scalar() or 0)
        rows = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM crm_change_log WHERE {where} "
                "ORDER BY at DESC, id DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": page_size, "offset": (page - 1) * page_size},
        )).fetchall()
        names = await _names(db, list(rows))
        mode = await current_mode(db)
    return ChangesOut(
        rows=[_row_out(r, names, admin=admin, me=me, mode=mode) for r in rows],
        total=total,
    )


# ── Undo ────────────────────────────────────────────────────────────────────

_LOAD_SQL = f"SELECT {_COLUMNS} FROM crm_change_log WHERE id = CAST(:id AS uuid) AND "


@router.post("/change-history/{change_id}/undo", response_model=UndoOut)
async def undo_change(
    change_id: str, user: UserContext = Depends(get_current_user),
) -> UndoOut:
    """Undo one change through the code path of the change. See the module doc."""
    org = _organization(user)
    me = _me(user)
    admin = _is_admin(user)
    wanted = checked_uuid(change_id, "change_id")
    params = {"id": wanted, "organization_id": org, "me": me, "admin": admin}
    after_commit: AfterCommit | None = None
    async with _tenant_session() as db:
        # FOR UPDATE: a second Undo of the same row waits here, then reads
        # `undone_by` and gets its 409.
        row = (await db.execute(
            text(_LOAD_SQL + _VISIBLE_SQL + " FOR UPDATE"), params,
        )).first()
        if row is None:
            raise _refuse(404, "no_change", "There is no such change.")
        if row.undone_by is not None:
            raise _refuse(409, "already_undone", "This change is undone already.")
        if not row.reversible:
            raise _refuse(409, "not_reversible", "This change cannot be undone.")
        if row.mode != await current_mode(db):
            raise _refuse(409, "mode_changed", (
                "The CRM setup changed after this change, so it cannot be undone now."
            ))
        handler = _UNDO.get(str(row.area))
        if handler is None:
            raise _refuse(409, "not_reversible", "This change cannot be undone.")
        if str(row.area) in ADMIN_AREAS and not admin:
            raise _refuse(403, "admin_only", "Only a CRM admin can undo this change.")
        after_commit = await handler(db, user, Logged(
            id=str(row.id), area=str(row.area), action=str(row.action),
            entity_id=row.entity_id, before=from_jsonb(row.before),
            after=from_jsonb(row.after), mode=row.mode,
            actor_email=str(row.actor_email), private_to=row.private_to,
        ))
        # Read the new row back: an Undo that wrote no row is no Undo.
        undo = (await db.execute(
            text(
                f"SELECT {_COLUMNS} FROM crm_change_log WHERE "
                "organization_id = CAST(:organization_id AS uuid) "
                "AND undoes_id = CAST(:id AS uuid)"
            ),
            {"organization_id": org, "id": wanted},
        )).first()
        if undo is None:
            raise RuntimeError("an Undo function wrote no change row")
        names = await _names(db, [undo])
        mode = await current_mode(db)
        out = _row_out(undo, names, admin=admin, me=me, mode=mode)
    if after_commit is not None:
        await after_commit()
    return UndoOut(change=out, undid=wanted)


__all__ = [
    "ACTIONS",
    "ADMIN_AREAS",
    "ALWAYS_ASK",
    "AREAS",
    "CHANGE",
    "COMPANY",
    "CREATE",
    "DELETE",
    "LADDER",
    "OWN",
    "TEAM",
    "VIAS",
    "VIA_CHAT",
    "VIA_UI",
    "VIA_WHATSAPP",
    "Change",
    "ChangeOut",
    "ChangesOut",
    "Logged",
    "UndoOut",
    "changed_since",
    "current_mode",
    "diff",
    "gone",
    "in_use",
    "list_changes",
    "log_change",
    "name_taken",
    "plain",
    "quoted",
    "same_values",
    "undo_areas",
    "undo_change",
    "undoes",
    "via_of",
]
