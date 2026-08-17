"""CRM · import_zoho — the Zoho backfill, and the Zoho→native mapping itself.

Spec: ``project-docs/specs/crm_app.md`` §7.1 (the mapping table) · §4
(``import_zoho.py`` row) · ticket WS-26b done-when 2.

    POST /crm/import/zoho {dry_run: bool}   → per-module {fetched, created,
                                               updated, skipped, errors[]}

**Two jobs, deliberately in one module.** This is the bootstrap endpoint *and*
the canonical Zoho→native field mapping: ``sync_zoho.py``'s incremental pull
imports :func:`apply_module` from here rather than re-deriving the same field
names. A backfill and a pull that map ``Deal_Name`` differently is not a bug
anyone would notice until the numbers stopped matching.

**The permission floor is ``admin:access:manage``, not
``integrations:use:zoho-crm``.** ``131_integration_memory_permissions.sql``
grants ``member`` the whole ``integrations:use:*`` family, so under
``permission_matches`` every member would hold the integration slug and it
would gate nothing. Running this against the production tenant is separately an
OWNER-GATE (``work_plan.md`` §6) — the code floor is the floor, not the
authorization.

``dry_run`` fetches and reports and writes **nothing** — not a row, not a
status, not a cursor. It is the only way to see what a first import would do
against a live tenant before doing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger
from fastapi import Depends
from gateway.routes.crm.admin import _next_position
from gateway.routes.crm.core import (
    CONTACTS,
    DEALS,
    ENTITIES,
    LEADS,
    ORGANIZATIONS,
    Entity,
    _tenant_session,
    actor,
    bump_last_activity,
    compute_lead_name,
    load_by_zoho_id,
    load_default_status,
    router,
    savepoint,
    upsert_by_zoho_id,
)
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.crm.import_zoho")

#: Zoho module → the CRM entity it lands in. Ordered: Accounts before Contacts
#: and Deals, because the latter two carry a lookup at the former and the FK
#: has to exist. Leads have no FKs at all (§3.3) and could go anywhere.
RECORD_MODULES: tuple[tuple[str, Entity], ...] = (
    ("Accounts", ORGANIZATIONS),
    ("Contacts", CONTACTS),
    ("Leads", LEADS),
    ("Deals", DEALS),
)

#: The two activity modules, and the ``crm_activities.type`` each becomes.
ACTIVITY_MODULES: dict[str, str] = {"Notes": "note", "Tasks": "task"}

#: Every module a cycle or a backfill touches, in the order it touches them.
ALL_MODULES: tuple[str, ...] = tuple(
    [module for module, _ in RECORD_MODULES] + list(ACTIVITY_MODULES)
)


# ── The report ──────────────────────────────────────────────────────────────

class ModuleReport(BaseModel):
    """What one module's pass did. Counted honestly, including the failures."""

    fetched: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []


class ImportReport(BaseModel):
    dry_run: bool
    modules: dict[str, ModuleReport]
    #: Zoho stage/status names this run had to create natively (§7.1: the
    #: vocabulary flows DOWN, so an unseen stage becomes a lane here).
    statuses_created: int = 0
    #: Zoho owner ids that matched no Metorite address, and therefore fell
    #: back to the importing admin. Loud, because silently reassigning every
    #: record to whoever ran the import is a thing somebody must see.
    unmatched_owners: int = 0


class ImportRequest(BaseModel):
    dry_run: bool = False


# ── Seams ───────────────────────────────────────────────────────────────────

def _client() -> Any:
    """The Zoho read client. A function so tests can bind a fake in one place.

    Imported lazily: the gateway must not drag ``ingestion`` in at import time
    just because this module is registered.
    """
    from ingestion.sources.zoho import client

    return client


# ── Value coercion ──────────────────────────────────────────────────────────

def _text(value: Any) -> str | None:
    """A Zoho scalar as text, with '' collapsed to NULL.

    Zoho returns empty strings for cleared fields; storing them makes
    ``WHERE email IS NULL`` and "no email" two different states.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


#: `NUMERIC(14,2)` holds 12 integer digits: |value| must stay under 10^12.
#: §3.1/§3.4 size `annual_revenue` and `amount` that way and Zoho does not —
#: its currency fields accept far larger numbers, and a tenant with one
#: fat-fingered ₹9,999,999,999,999 deal would otherwise raise
#: `numeric field overflow` at the driver. That is not a bad row, it is a
#: **poisoned transaction**: Postgres aborts the whole tx, every later
#: statement in the cycle fails with `current transaction is aborted`, and the
#: cursor write rolls back so the next cycle re-reads the same record and dies
#: identically — forever. Clamping to NULL here keeps the value out of the
#: statement entirely; the record still lands, missing one field.
NUMERIC_14_2_CEILING = 10 ** 12


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or abs(number) >= NUMERIC_14_2_CEILING:
        # NaN/inf included: neither is storable, and both abort the tx.
        _log.warning("crm.import.numeric_out_of_range", value=str(value)[:64])
        return None
    return number


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _lookup_id(value: Any) -> str | None:
    """The id inside a Zoho lookup field (``{"id": ..., "name": ...}``)."""
    if isinstance(value, dict):
        return _text(value.get("id"))
    return None


def parse_instant(value: Any) -> datetime | None:
    """A Zoho timestamp (``2026-01-15T12:34:56+05:30``) as an aware datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def modified_time(record: dict[str, Any]) -> datetime | None:
    """When Zoho last changed this record — the left side of LWW (§7.1)."""
    return parse_instant(record.get("Modified_Time") or record.get("Created_Time"))


# ── Owner mapping (§7.1, the Users row) ─────────────────────────────────────

def owner_index(users: list[dict[str, Any]]) -> dict[str, str]:
    """Zoho user id → email, from ``list_users``.

    Keyed on the id and not the name: names repeat and get edited, ids do not.
    """
    index: dict[str, str] = {}
    for user in users:
        user_id = _text(user.get("id"))
        email = _text(user.get("email"))
        if user_id and email:
            index[user_id] = email.lower()
    return index


def owner_email(
    record: dict[str, Any], owners: dict[str, str], fallback: str,
) -> tuple[str, bool]:
    """The native ``owner_email`` for a Zoho record, and whether we guessed.

    Zoho's ``Owner`` lookup carries an ``email`` of its own on most modules, so
    the id index is the first answer and the embedded address the second. An
    owner we cannot resolve falls back to the importing admin — §7.1's rule —
    and the caller counts it, because "every record is now owned by whoever ran
    the import" must not be a silent outcome.
    """
    owner = record.get("Owner")
    if isinstance(owner, dict):
        by_id = owners.get(_text(owner.get("id")) or "")
        if by_id:
            return by_id, False
        embedded = _text(owner.get("email"))
        if embedded:
            return embedded.lower(), False
    return fallback, True


# ── Field mappings (§7.1's table) ───────────────────────────────────────────

def _address(record: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    """Zoho's five flat address columns folded into the ``address`` JSONB."""
    parts = {
        "street": _text(record.get(f"{prefix}_Street")),
        "city": _text(record.get(f"{prefix}_City")),
        "state": _text(record.get(f"{prefix}_State")),
        "code": _text(record.get(f"{prefix}_Code")),
        "country": _text(record.get(f"{prefix}_Country")),
    }
    present = {k: v for k, v in parts.items() if v}
    return present or None


def map_account(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _text(record.get("Account_Name")) or f"Account {record.get('id')}",
        "website": _text(record.get("Website")),
        "industry": _text(record.get("Industry")),
        "no_of_employees": _integer(record.get("Employees")),
        "annual_revenue": _number(record.get("Annual_Revenue")),
        "phone": _text(record.get("Phone")),
        "email": _text(record.get("Email")),
        "address": _address(record, "Billing"),
        "description": _text(record.get("Description")),
    }


def map_contact(record: dict[str, Any]) -> dict[str, Any]:
    first = _text(record.get("First_Name"))
    last = _text(record.get("Last_Name"))
    return {
        # `first_name` is NOT NULL. A Zoho contact with only a surname is
        # common; inventing 'Unknown' is worse than promoting the name we have.
        "first_name": first or last or _text(record.get("Full_Name")) or "Contact",
        "last_name": last if first else None,
        "email": _text(record.get("Email")),
        "phone": _text(record.get("Phone")),
        "mobile": _text(record.get("Mobile")),
        "title": _text(record.get("Title")),
        "description": _text(record.get("Description")),
    }


def map_lead(record: dict[str, Any]) -> dict[str, Any]:
    """Zoho Lead → native columns — **without** ``lead_name``.

    ⚠️ The display name is deliberately NOT derived here. §3.3's fallback chain
    reads ``first_name``/``last_name``/``organization_name``, and on a pull two
    of those three are fields the push had to PAD because Zoho requires them
    (see ``PADDED_FROM``). Deriving the name from the padded values folds our
    own padding into it — a lead called "Asha" comes back as "Asha Asha", and
    ``lead_name`` IS on the upsert's conflict arm, so every cycle rewrites it.
    :func:`apply_record` derives it AFTER :func:`strip_padding_echo` instead.
    """
    return {
        "first_name": _text(record.get("First_Name")),
        "last_name": _text(record.get("Last_Name")),
        # §3.3 — Zoho's Company is free text here and becomes a real
        # organization row only at conversion.
        "organization_name": _text(record.get("Company")),
        "email": _text(record.get("Email")),
        "phone": _text(record.get("Phone")),
        "mobile": _text(record.get("Mobile")),
        "website": _text(record.get("Website")),
        "industry": _text(record.get("Industry")),
        "no_of_employees": _integer(record.get("No_of_Employees")),
        "annual_revenue": _number(record.get("Annual_Revenue")),
        "lead_source": _text(record.get("Lead_Source")),
        "description": _text(record.get("Description")),
    }


def map_deal(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _text(record.get("Deal_Name")) or f"Deal {record.get('id')}",
        "amount": _number(record.get("Amount")),
        "probability": _integer(record.get("Probability")),
        "expected_close_date": _text(record.get("Closing_Date")),
        "next_step": _text(record.get("Next_Step")),
        "description": _text(record.get("Description")),
    }


#: Zoho module → the mapper that turns one of its records into native values.
MAPPERS = {
    "Accounts": map_account,
    "Contacts": map_contact,
    "Leads": map_lead,
    "Deals": map_deal,
}


# ── Status auto-creation (§7.1: the vocabulary flows DOWN only) ─────────────

#: Substrings that decide a Zoho stage's machine-readable class. Checked in
#: this order because "Closed Lost" contains neither hint of the other, but a
#: future "Won back after loss" would, and losing is the safer read.
_LOST_HINTS: tuple[str, ...] = ("lost", "junk", "dead", "not qualified")
_WON_HINTS: tuple[str, ...] = ("won", "converted", "closed-won")


def guess_status_type(name: str) -> str:
    """§7.1 — "type guessed: name ~ won/lost, else open".

    A guess, and labelled one: the type drives the lost-reason gate and the
    ``closed_at`` stamp, so the owner can retype a lane in the admin UI
    afterwards. Guessing *open* for anything unrecognised is the conservative
    direction — it never auto-closes a deal.
    """
    blob = name.strip().lower()
    if any(hint in blob for hint in _LOST_HINTS):
        return "lost"
    if any(hint in blob for hint in _WON_HINTS):
        return "won"
    return "open"


async def ensure_status(
    db: Any, table: str, name: str, *, with_probability: bool,
) -> tuple[str, bool]:
    """The id of the status called ``name``, creating the lane if it is new.

    Returns ``(status_id, created)``. The INSERT carries
    ``ON CONFLICT (name) DO NOTHING`` because two modules (or two cycles) can
    reach the same unseen stage at once and the second one must not abort the
    import with a unique violation. **The test that this stays idempotent is
    static, against this statement's text** — the unit suite's fake DB models
    no ``ON CONFLICT``, so a behavioural "the second run created nothing" there
    would only be the mirror agreeing with itself.

    New lanes are APPENDED (``position`` past the last one), never inserted at
    0, which would silently reorder the owner's existing pipeline.
    """
    existing = (await db.execute(
        text(f"SELECT * FROM {table} WHERE name = :name"), {"name": name},
    )).fetchone()
    if existing is not None:
        return str(existing.id), False

    values: dict[str, Any] = {
        "name": name,
        "position": await _next_position(db, table),
        "type": guess_status_type(name),
    }
    if with_probability:
        values["probability"] = 0
    columns = ", ".join(values)
    binds = ", ".join(f":{c}" for c in values)
    await db.execute(
        text(
            f"INSERT INTO {table} ({columns}) VALUES ({binds}) "
            f"ON CONFLICT (name) DO NOTHING"
        ),
        values,
    )
    row = (await db.execute(
        text(f"SELECT * FROM {table} WHERE name = :name"), {"name": name},
    )).fetchone()
    if row is None:  # pragma: no cover — only reachable if the INSERT vanished
        raise RuntimeError(f"could not create or find status {name!r} in {table}")
    return str(row.id), True


async def resolve_status(
    db: Any, entity: Entity, record: dict[str, Any], report: ImportReport,
) -> str | None:
    """The native status for a Zoho record, auto-creating its stage if needed."""
    if not entity.status_table:
        return None
    field = "Stage" if entity is DEALS else "Lead_Status"
    name = _text(record.get(field))
    if not name:
        default = await load_default_status(db, entity.status_table)
        return str(default.id)
    status_id, created = await ensure_status(
        db, entity.status_table, name, with_probability=entity is DEALS,
    )
    if created:
        report.statuses_created += 1
    return status_id


# ── The push's padding, coming back (§7.1) ──────────────────────────────────
#
# Zoho makes fields NOT NULL that we allow to be blank: a Contact must have a
# Last_Name, a Lead must have a Last_Name and a Company. `sync_zoho`'s push
# therefore PADS them from a field that is always populated. The next pull sees
# that padding as real data and, without this, writes it back into the native
# NULL — the sync mutating native data purely by echoing itself.
#
# The guard is deliberately narrow and provable rather than clever: it fires
# only when the native column is NULL *and* the pulled value is exactly the
# value we would have padded it from. The one case it declines is a human in
# Zoho genuinely typing Last_Name = the first name onto a contact whose native
# last name is blank — which leaves the column as it already was.

#: entity slug → ((padded column, the native column it is padded FROM), …).
#: Must stay in step with ``sync_zoho.to_zoho_contact`` / ``to_zoho_lead``.
PADDED_FROM: dict[str, tuple[tuple[str, str], ...]] = {
    "contacts": (("last_name", "first_name"),),
    "leads": (("last_name", "lead_name"), ("organization_name", "lead_name")),
}


def strip_padding_echo(
    entity: Entity, values: dict[str, Any], existing: Any,
) -> dict[str, Any]:
    """Drop pulled values that are only our own push's padding coming home."""
    rules = PADDED_FROM.get(entity.slug)
    if existing is None or not rules:
        return values
    out = dict(values)
    for column, padded_from in rules:
        if getattr(existing, column, None) is not None:
            continue  # the native column has a real value; the pull may change it
        if out.get(column) and out[column] == getattr(existing, padded_from, None):
            out.pop(column)
    return out


# ── Applying one record ─────────────────────────────────────────────────────

async def apply_record(
    db: Any,
    module: str,
    entity: Entity,
    record: dict[str, Any],
    *,
    owners: dict[str, str],
    fallback_owner: str,
    report: ImportReport,
    module_report: ModuleReport,
    existing: Any = None,
) -> Any | None:
    """Map and write one Zoho record. Shared by the backfill and the pull.

    Returns the written row, or ``None`` when the record was skipped. Rows
    carry ``source='import'`` — §7.1: the existing CHECK vocabulary needs no
    new value and migration 144 needs no ALTER. On a row that already exists
    ``source`` is written but never applied: ``core.INSERT_ONLY_COLUMNS`` keeps
    it off the conflict arm, so a row typed into this app stays ``'manual'``
    after its first round trip through Zoho.

    ``existing`` is the native row this record already maps to, when there is
    one. It is what :func:`strip_padding_echo` needs to tell our own push's
    padding apart from a real Zoho value.
    """
    zoho_id = _text(record.get("id"))
    if not zoho_id:
        module_report.skipped += 1
        return None

    values = strip_padding_echo(entity, MAPPERS[module](record), existing)
    if entity is LEADS:
        # §3.3's fallback chain, derived AFTER the strip and never inside
        # `map_lead`. Two of the three fields it reads are ones the push had to
        # pad for Zoho; deriving first turns a lead called "Asha" into
        # "Asha Asha" on its first round trip, and `lead_name` is on the
        # conflict arm, so the corruption is written back every cycle.
        values["lead_name"] = compute_lead_name(values)
    owner, guessed = owner_email(record, owners, fallback_owner)
    values["owner_email"] = owner
    if guessed:
        report.unmatched_owners += 1
    values["source"] = "import"
    values["zoho_id"] = zoho_id
    values["zoho_synced_at"] = modified_time(record)

    status_id = await resolve_status(db, entity, record, report)
    if status_id:
        values["status_id"] = status_id
    if entity is CONTACTS:
        values["organization_id"] = await _linked_native_id(
            db, ORGANIZATIONS, record.get("Account_Name"),
        )
    if entity is DEALS:
        values["organization_id"] = await _linked_native_id(
            db, ORGANIZATIONS, record.get("Account_Name"),
        )

    row = await upsert_by_zoho_id(db, entity.table, values)
    if entity is DEALS:
        await _link_primary_contact(db, row, record)
    return row


async def _linked_native_id(db: Any, entity: Entity, lookup: Any) -> str | None:
    """The native id behind a Zoho lookup field, if we have imported it."""
    zoho_id = _lookup_id(lookup)
    if not zoho_id:
        return None
    row = await load_by_zoho_id(db, entity.table, zoho_id)
    return str(row.id) if row is not None else None


async def _link_primary_contact(
    db: Any, deal: Any, record: dict[str, Any],
) -> None:
    """Zoho's ``Contact_Name`` on a Deal becomes the primary deal-contact.

    ``ON CONFLICT DO NOTHING`` on the composite PK: re-importing a deal must
    not fail on a link it already made, and it must not demote a primary the
    team has since changed by hand either.
    """
    contact_id = await _linked_native_id(db, CONTACTS, record.get("Contact_Name"))
    if not contact_id:
        return
    # ⚠️ `is_primary` is COMPUTED, not the literal `true` the first draft sent:
    # a deal that already has a hand-set primary (contact A) whose Zoho record
    # names contact B would otherwise end up with TWO primaries, and "at most
    # one primary per deal" is enforced by code, not by a constraint. Deciding
    # it inside the INSERT rather than reading first also means two racing
    # cycles cannot both observe "no primary yet".
    await db.execute(
        text(
            "INSERT INTO crm_deal_contacts (deal_id, contact_id, is_primary) "
            "SELECT CAST(:deal_id AS uuid), CAST(:contact_id AS uuid), "
            "       NOT EXISTS (SELECT 1 FROM crm_deal_contacts "
            "                    WHERE deal_id = CAST(:deal_id AS uuid) "
            "                      AND is_primary) "
            "ON CONFLICT DO NOTHING"
        ),
        {"deal_id": str(deal.id), "contact_id": contact_id},
    )


# ── Activities (Notes and Tasks) ────────────────────────────────────────────

#: Zoho's ``$se_module`` value → the entity that owns the parent record.
_SE_MODULES: dict[str, Entity] = {
    "Accounts": ORGANIZATIONS, "Contacts": CONTACTS,
    "Leads": LEADS, "Deals": DEALS,
}


async def resolve_activity_target(
    db: Any, parent_zoho_id: str | None, se_module: str | None,
) -> tuple[str, str] | None:
    """``(activity_column, native_id)`` for a Zoho Note/Task's parent.

    ``$se_module`` names the parent's module, so the hinted table is tried
    first and the other three only as a fallback — Zoho Tasks carry the parent
    in ``What_Id``/``Who_Id`` with no module hint at all.
    """
    if not parent_zoho_id:
        return None
    hinted = _SE_MODULES.get(se_module or "")
    candidates = [hinted] if hinted else []
    candidates += [e for e in ENTITIES.values() if e is not hinted]
    for entity in candidates:
        row = await load_by_zoho_id(db, entity.table, parent_zoho_id)
        if row is not None:
            return entity.activity_column, str(row.id)
    return None


async def apply_activity(
    db: Any,
    module: str,
    record: dict[str, Any],
    *,
    created_by: str,
    module_report: ModuleReport,
) -> Any | None:
    """One Zoho Note or Task → one ``crm_activities`` row on its parent.

    An activity whose parent we have not imported is **skipped, not orphaned**:
    the CHECK requires at least one target, and a note attached to nothing is
    invisible in every timeline while still counting against the numbers.
    """
    zoho_id = _text(record.get("id"))
    if not zoho_id:
        module_report.skipped += 1
        return None

    # _lookup_id FIRST. Zoho sends Parent_Id as a lookup object
    # ({"name": ..., "id": ...}), and _text() stringifies any non-None value —
    # so with _text first the dict became "{'name': …}", a truthy garbage
    # string that made the _lookup_id fallback unreachable and skipped every
    # note (1,909/1,909 on the first real backfill, 2026-08-06). _text stays
    # as the fallback for a bare string id.
    parent = _lookup_id(record.get("Parent_Id")) or _text(record.get("Parent_Id"))
    if module == "Tasks":
        parent = (
            _lookup_id(record.get("What_Id"))
            or _lookup_id(record.get("Who_Id"))
            or parent
        )
    target = await resolve_activity_target(db, parent, _text(record.get("$se_module")))
    if target is None:
        module_report.skipped += 1
        return None
    column, target_id = target

    values: dict[str, Any] = {
        "type": ACTIVITY_MODULES[module],
        "subject": _text(record.get("Note_Title") or record.get("Subject")),
        "body": _text(record.get("Note_Content") or record.get("Description")),
        "created_by": created_by,
        "occurred_at": parse_instant(record.get("Created_Time")),
        "zoho_id": zoho_id,
        column: target_id,
    }
    if module == "Tasks":
        values["due_at"] = _text(record.get("Due_Date"))
        if str(record.get("Status") or "").strip().lower() == "completed":
            values["completed_at"] = parse_instant(record.get("Closed_Time")) or (
                parse_instant(record.get("Modified_Time"))
            )
    row = await upsert_by_zoho_id(db, "crm_activities", values)
    # §3.8's discipline: every write to a timeline bumps its target's recency,
    # or an imported record sorts as never-touched on the day it arrives.
    await bump_last_activity(db, _TABLE_BY_COLUMN[column], target_id)
    return row


_TABLE_BY_COLUMN: dict[str, str] = {
    e.activity_column: e.table for e in ENTITIES.values()
}


# ── One module's pass ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ModulePass:
    """One module's apply pass, and how far the pull cursor may move after it.

    The two instants are separate on purpose. ``newest_applied`` is the
    furthest the cursor could go if everything had worked; ``oldest_failed``
    is the ceiling that stops it stepping over a record it did not land.
    Keeping "the newest success" and "the oldest failure" apart is what makes
    a failure OLDER than a success visible — a single "watermark" value cannot
    express it, and the version that tried dropped those records silently.
    """

    report: ModuleReport
    #: Newest Zoho ``Modified_Time`` among the records that actually landed.
    newest_applied: datetime | None = None
    #: How many records raised. Counted separately from ``oldest_failed``
    #: because a record can fail while carrying no readable timestamp, and
    #: "something failed but we cannot place it in time" must not read as
    #: "nothing failed".
    failures: int = 0
    #: Oldest ``Modified_Time`` among the records that raised.
    oldest_failed: datetime | None = None


async def apply_module(
    db: Any,
    module: str,
    records: list[dict[str, Any]],
    *,
    owners: dict[str, str],
    fallback_owner: str,
    report: ImportReport,
) -> ModulePass:
    """Apply every fetched record of one module. The shared pull/backfill body.

    Returns a :class:`ModulePass` — the counts, plus the two instants the pull
    cursor needs to decide how far it may move.

    ``created`` vs ``updated`` is decided by a read BEFORE the upsert, because
    ``ON CONFLICT`` cannot tell the caller which arm it took without a second
    round trip, and reporting every row as "created" on a re-run would make the
    report useless exactly when somebody is checking whether the import is
    idempotent. That same read supplies ``apply_record``'s ``existing``.
    """
    module_report = ModuleReport(fetched=len(records))
    entity = dict(RECORD_MODULES).get(module)
    newest_applied: datetime | None = None
    oldest_failed: datetime | None = None
    failures = 0
    for record in records:
        zoho_id = _text(record.get("id"))
        table = entity.table if entity is not None else "crm_activities"
        try:
            # ⚠️ A SAVEPOINT per record, not just a try/except. Postgres aborts
            # the whole transaction on a statement error, so without this the
            # first bad row silently takes every row after it, the cursor
            # write and the commit with it — see `core.savepoint`.
            async with savepoint(db):
                existing = (
                    await load_by_zoho_id(db, table, zoho_id) if zoho_id else None
                )
                if entity is not None:
                    row = await apply_record(
                        db, module, entity, record, owners=owners,
                        fallback_owner=fallback_owner, report=report,
                        module_report=module_report, existing=existing,
                    )
                else:
                    row = await apply_activity(
                        db, module, record, created_by=fallback_owner,
                        module_report=module_report,
                    )
            if row is None:
                continue
            if existing is not None:
                module_report.updated += 1
            else:
                module_report.created += 1
            newest_applied = _newer(newest_applied, modified_time(record))
        except Exception as exc:  # one bad record must not lose the batch
            failures += 1
            oldest_failed = _older(oldest_failed, modified_time(record))
            module_report.errors.append(f"{zoho_id or '?'}: {str(exc)[:200]}")
            _log.warning(
                "crm.import.record_failed", module=module,
                zoho_id=zoho_id, error=str(exc)[:200],
            )
    return ModulePass(
        report=module_report, newest_applied=newest_applied,
        failures=failures, oldest_failed=oldest_failed,
    )


def _newer(left: datetime | None, right: datetime | None) -> datetime | None:
    """``max`` over two instants that may be missing or mutually incomparable."""
    if left is None:
        return right
    if right is None:
        return left
    try:
        return right if right > left else left
    except TypeError:
        # naive vs aware — refuse to guess, and keep the one we already had.
        return left


def _older(left: datetime | None, right: datetime | None) -> datetime | None:
    """``min`` over two instants that may be missing or mutually incomparable."""
    if left is None:
        return right
    if right is None:
        return left
    try:
        return right if right < left else left
    except TypeError:
        return left


# ── The endpoint ────────────────────────────────────────────────────────────

@router.post(
    "/import/zoho",
    response_model=ImportReport,
    dependencies=[require_permission("admin:access:manage")],
)
async def import_from_zoho(
    payload: ImportRequest | None = None,
    user: UserContext = Depends(get_current_user),
) -> ImportReport:
    """Backfill the native CRM from Zoho (§7.1's mapping table).

    Idempotent: every record is written ``ON CONFLICT (zoho_id)``, so running
    it twice converges rather than duplicating. Unresolvable owners fall back
    to the caller and are counted; stages Zoho has that we do not are created
    as new lanes at the end of the pipeline.
    """
    payload = payload or ImportRequest()
    who = actor(user)
    report = ImportReport(dry_run=payload.dry_run, modules={})
    zoho = _client()

    users = await zoho.list_users()
    owners = owner_index(users)
    report.modules["Users"] = ModuleReport(fetched=len(users))

    fetched: dict[str, list[dict[str, Any]]] = {}
    for module in ALL_MODULES:
        fetched[module] = await _fetch(zoho, module)

    if payload.dry_run:
        # Fetch and report, write nothing — no rows, no statuses, no cursors.
        # The one honest answer to "what would this do to the tenant".
        for module, records in fetched.items():
            report.modules[module] = ModuleReport(fetched=len(records))
        return report

    async with _tenant_session() as db:
        for module in ALL_MODULES:
            # The backfill has no cursor to advance, so the watermark half of
            # the pass is discarded here.
            report.modules[module] = (await apply_module(
                db, module, fetched[module],
                owners=owners, fallback_owner=who, report=report,
            )).report

    _log.info(
        "crm.import.completed",
        modules={m: r.fetched for m, r in report.modules.items()},
        statuses_created=report.statuses_created,
        unmatched_owners=report.unmatched_owners,
    )
    return report


async def _fetch(
    zoho: Any, module: str, *, modified_since: datetime | None = None,
) -> list[dict[str, Any]]:
    """One module's records from the client, by its own named function.

    Named functions rather than a generic ``_list_module(module)`` call because
    the client's public surface IS those six functions; reaching past them into
    the private helper would make the read surface unreviewable.
    """
    readers = {
        "Accounts": zoho.list_accounts,
        "Contacts": zoho.list_contacts,
        "Leads": zoho.list_leads,
        "Deals": zoho.list_deals,
        "Notes": zoho.list_notes,
        "Tasks": zoho.list_tasks,
    }
    return await readers[module](modified_since=modified_since)


__all__ = [
    "ACTIVITY_MODULES",
    "ALL_MODULES",
    "PADDED_FROM",
    "RECORD_MODULES",
    "ImportReport",
    "ImportRequest",
    "ModulePass",
    "ModuleReport",
    "apply_module",
    "guess_status_type",
    "import_from_zoho",
    "modified_time",
    "owner_index",
    "strip_padding_echo",
]
