"""The one writer of ``brief_runs`` and ``brief_claims`` (WS-BRIEF BR-4).

Spec: ``project-docs/specs/core_brief.md`` §3. Table: migration
``brief_runs`` (315 at build). ``tests/unit/test_brief_store_r8.py`` runs
this SQL on a real Postgres.

**Tenancy (R5).** Every function takes a session that the caller opened with
``tenant_session(org)``, so RLS binds each statement. A jobs handler passes
the session that the worker gives it. No function takes an org. An INSERT
writes no ``organization_id``: the column DEFAULT reads the bound tenant, and
each read filters on ``_BOUND_ORG`` too.

**The member.** Every function takes the member as a required argument, and
every statement filters on it, so a member reads only their own runs. ⚠️ The
store does not authenticate. It only scopes. The caller resolved the member
from a trusted source, for example the member list of the org in a planner
job, and never from request input (R5, contract R11).

**The link.** ``href`` is a string that code built (``my_day_feed.md`` §12).
The store takes it as given from the caller, and never from a model. BR-2a-1
owns the builder.

**The source shape.** The CHECK ``brief_claims_source_id_shape`` is the one
fence of ``<app>:<ref>``. This module adds no second grammar. A reader splits
the id on the first colon only (``my_day_feed.md`` §4.1).

**Retention.** ``jobs.prune`` of BR-1b deletes runs older than 90 days
(``core_brief.md`` §9 Q6). This module deletes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

#: The two kinds, as the CHECK ``brief_runs_kind`` holds them.
KINDS: tuple[str, ...] = ("brief", "closeout")
#: The seven states, as the CHECK ``brief_runs_state`` holds them.
STATES: tuple[str, ...] = (
    "planned", "gathered", "phrasing", "ready", "delivered", "empty", "failed")
#: The six sections, as the CHECK ``brief_claims_section`` holds them.
SECTIONS: tuple[str, ...] = ("now", "needs_you", "numbers", "done", "moved", "acted")
#: The bound of the CHECK ``brief_claims_source_ids_max``.
MAX_SOURCE_IDS = 50

_BOUND_ORG = "CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)"

_RUN_COLUMNS = (
    "id::text, member_email, kind, local_date, slot_at, state, lead, lead_source, "
    "CAST(lead_claim_ids AS text[]), model_request_id, unattended_run_id::text, "
    "error, created_at, ready_at, delivered_at"
)

_CREATE_SQL = (
    "INSERT INTO brief_runs (member_email, kind, local_date, slot_at) "
    "VALUES (:m, :k, :d, :slot) "
    "ON CONFLICT (organization_id, member_email, kind, local_date) DO NOTHING "
    f"RETURNING {_RUN_COLUMNS}"
)

_READ_RUN_SQL = (
    f"SELECT {_RUN_COLUMNS} FROM brief_runs "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND kind = :k AND local_date = :d"
)

_SET_STATE_SQL = (
    "UPDATE brief_runs SET state = :st, error = CAST(:err AS text), "
    "ready_at = CASE WHEN CAST(:st AS text) = 'ready' THEN CAST(:now AS timestamptz) "
    "ELSE ready_at END, "
    "delivered_at = CASE WHEN CAST(:st AS text) = 'delivered' "
    "THEN CAST(:now AS timestamptz) ELSE delivered_at END "
    f"WHERE organization_id = {_BOUND_ORG} AND member_email = :m "
    "AND id = CAST(:id AS uuid)"
)

#: The run must be of the bound org and of the member, or no row goes in.
_ADD_CLAIM_SQL = (
    "INSERT INTO brief_claims (run_id, position, section, text, source_app, "
    "source_kind, source_id, source_ids, href, value, value_path, fingerprint) "
    "SELECT r.id, :pos, :sec, :txt, :app, :sk, :sid, CAST(:sids AS text[]), :href, "
    "CAST(:val AS numeric), :vp, :fp FROM brief_runs r "
    f"WHERE r.organization_id = {_BOUND_ORG} AND r.member_email = :m "
    "AND r.id = CAST(:run AS uuid) "
    "RETURNING id::text"
)

_READ_CLAIMS_SQL = (
    "SELECT c.id::text, c.run_id::text, c.position, c.section, c.text, "
    "c.source_app, c.source_kind, c.source_id, c.source_ids, c.href, c.value, "
    "c.value_path, c.fingerprint FROM brief_claims c "
    "JOIN brief_runs r ON r.organization_id = c.organization_id AND r.id = c.run_id "
    f"WHERE c.organization_id = {_BOUND_ORG} AND r.member_email = :m "
    "AND c.run_id = CAST(:run AS uuid) ORDER BY c.position, c.id"
)


class BriefStoreError(ValueError):
    """A write that the store refuses before it reaches the database."""


@dataclass(frozen=True)
class Run:
    """One row of ``brief_runs``."""

    id: str
    member_email: str
    kind: str
    local_date: date
    slot_at: datetime
    state: str
    lead: str | None
    lead_source: str | None
    lead_claim_ids: tuple[str, ...]
    model_request_id: str | None
    unattended_run_id: str | None
    error: str | None
    created_at: datetime
    ready_at: datetime | None
    delivered_at: datetime | None


@dataclass(frozen=True)
class Created:
    """The answer of :func:`create_run`. ``created`` is False when the run of
    the member, kind and date already existed. ``run`` is then that row."""

    run: Run
    created: bool


@dataclass(frozen=True)
class ClaimIn:
    """One claim to add. ``href`` is a link that code built, never a model
    value. ``source_id`` has the shape ``<app>:<ref>``."""

    position: int
    section: str
    text: str
    source_app: str
    source_kind: str
    source_id: str
    href: str
    source_ids: tuple[str, ...] = field(default_factory=tuple)
    value: Decimal | int | None = None
    value_path: str | None = None
    fingerprint: str | None = None


@dataclass(frozen=True)
class Claim:
    """One row of ``brief_claims``."""

    id: str
    run_id: str
    position: int
    section: str
    text: str
    source_app: str
    source_kind: str
    source_id: str
    source_ids: tuple[str, ...]
    href: str
    value: Decimal | None
    value_path: str | None
    fingerprint: str | None


def _member(member: object) -> str:
    """The member as the CHECK ``brief_runs_member_email`` holds it."""
    if not isinstance(member, str) or not member.strip():
        raise BriefStoreError("member must be a non-empty string")
    return member.strip().lower()


def _run(row: Any) -> Run:
    return Run(
        id=str(row[0]), member_email=str(row[1]), kind=str(row[2]), local_date=row[3],
        slot_at=row[4], state=str(row[5]), lead=row[6], lead_source=row[7],
        lead_claim_ids=tuple(str(x) for x in (row[8] or ())), model_request_id=row[9],
        unattended_run_id=row[10], error=row[11], created_at=row[12], ready_at=row[13],
        delivered_at=row[14])


async def create_run(db: Any, member: str, *, kind: str, local_date: date,
                     slot_at: datetime) -> Created:
    """Create the run of *member*, *kind* and *local_date*, or get it.

    A second call for the same slot makes no second row. It answers the row
    that exists, with ``created`` False. ``slot_at`` must carry a time zone.
    """
    m = _member(member)
    if kind not in KINDS:
        raise BriefStoreError(f"kind must be one of {KINDS}")
    if slot_at.tzinfo is None:
        raise BriefStoreError("slot_at must carry a time zone")
    params = {"m": m, "k": kind, "d": local_date, "slot": slot_at}
    row = (await db.execute(text(_CREATE_SQL), params)).first()
    if row is not None:
        return Created(run=_run(row), created=True)
    found = (await db.execute(text(_READ_RUN_SQL), params)).first()
    if found is None:
        # ON CONFLICT found a row that RLS or the member filter hides. That
        # cannot happen inside one bound tenant, so say so loudly.
        raise BriefStoreError("the run exists but this session cannot read it")
    return Created(run=_run(found), created=False)


async def read_run(db: Any, member: str, *, kind: str, local_date: date) -> Run | None:
    """The run of *member*, *kind* and *local_date*, or None."""
    row = (await db.execute(text(_READ_RUN_SQL), {
        "m": _member(member), "k": kind, "d": local_date})).first()
    return None if row is None else _run(row)


async def set_state(db: Any, member: str, run_id: str, state: str, *, now: datetime,
                    error: str | None = None) -> bool:
    """Move the run *run_id* of *member* to *state*.

    ``ready`` stamps ``ready_at`` and ``delivered`` stamps ``delivered_at``
    with *now*. *error* is the error type only, never a payload. Answers
    False when no run of the member has the id.
    """
    if state not in STATES:
        raise BriefStoreError(f"state must be one of {STATES}")
    result: Any = await db.execute(text(_SET_STATE_SQL), {
        "st": state, "err": error, "now": now, "m": _member(member), "id": run_id})
    return bool(getattr(result, "rowcount", 0))


async def add_claims(db: Any, member: str, run_id: str, claims: list[ClaimIn]) -> list[str]:
    """Add *claims* to the run *run_id* of *member*. Answers the new ids.

    Each ``href`` is a string that code built. The store never takes one from
    a model. A run of another member or of another org takes no claim, and
    the answer is then empty. A claim with no source fails its CHECK.
    """
    m = _member(member)
    ids: list[str] = []
    for c in claims:
        if len(c.source_ids) > MAX_SOURCE_IDS:
            raise BriefStoreError(f"source_ids holds at most {MAX_SOURCE_IDS} ids")
        row = (await db.execute(text(_ADD_CLAIM_SQL), {
            "m": m, "run": run_id, "pos": c.position, "sec": c.section, "txt": c.text,
            "app": c.source_app, "sk": c.source_kind, "sid": c.source_id,
            "sids": list(c.source_ids), "href": c.href,
            "val": None if c.value is None else str(c.value), "vp": c.value_path,
            "fp": c.fingerprint})).first()
        if row is None:
            return ids
        ids.append(str(row[0]))
    return ids


async def read_claims(db: Any, member: str, run_id: str) -> list[Claim]:
    """The claims of the run *run_id* of *member*, in order."""
    rows = (await db.execute(text(_READ_CLAIMS_SQL), {
        "m": _member(member), "run": run_id})).all()
    return [Claim(
        id=str(r[0]), run_id=str(r[1]), position=int(r[2]), section=str(r[3]),
        text=str(r[4]), source_app=str(r[5]), source_kind=str(r[6]),
        source_id=str(r[7]), source_ids=tuple(r[8] or ()), href=str(r[9]),
        value=r[10], value_path=r[11], fingerprint=r[12]) for r in rows]
