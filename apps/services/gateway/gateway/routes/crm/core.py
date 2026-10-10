"""CRM routes — the shared kernel.

The leaf module: it imports nothing from its siblings except ``flags``, the
kill switch, which is a leaf too (WS-53 CRM-0). It owns the shared
``router``, the entity registry, the Pydantic models, the row→model mapper, the
list contract, and the small set of SQL helpers every feature module builds on.
Spec: ``project-docs/specs/crm_app.md`` sections 3 and 4 (WS-26a).

Two things here are load-bearing and worth stating once:

**The engine.** This package makes **zero** ``create_async_engine`` calls. It
consumes ``gateway.db`` — the shared seam BO-10 asked for and D-CRM-4 records —
and ``routes/tasks/core.py`` was converted to the same seam in the same change
as the proof it works.

**Sort keys are an allowlist, never interpolation.** Every identifier that
reaches an f-string in this module comes from a literal we wrote (a table name,
an entry in :attr:`Entity.sorts`, a model field name); every value the caller
supplies is a bound parameter. An unknown sort key is a 422, not a slower query
— trycompai's ``resolveOrderBy`` lesson, and the one place a list endpoint
usually grows an injection.

**Visibility.** CRM data is org-visible to ``feature:crm`` holders in v1 and
``owner_email`` is *assignment*, not an ACL (D-CRM-3, a departure recorded per
``user_management_contract.md`` §7). So there is deliberately no owner
predicate here and no 404-not-403 scoping — unlike ``routes/notes``, where the
opposite decision is equally deliberate. Do not "fix" one to look like the
other without changing the decision first.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from acb_auth import require_feature_router
from acb_common import get_logger
from fastapi import APIRouter, Depends, HTTPException

# The shared seam (BO-10 / D-CRM-4 → MT-1c/H2). `_tenant_session` IS
# `acb_common.db.tenant_session`, aliased per-package for the same reason
# `_get_db` was: every request-handler submodule imports it from here BY NAME,
# which is the seam `tests/unit/_crm_fakes.bind_db` patches per module. The
# tenant comes from the request context — bound centrally in
# `_with_resolved_access` — so no call site passes one (H2). A call outside a
# bound request raises `TenantUnbound` rather than defaulting: fail closed,
# never "the usual org".
#
# `_get_db` stays exported for the package's three NON-request leaves only —
# `sync_zoho` (the scheduled sync engine), `auto_lead` (the email scheduler's
# hook) and `broker_handlers` (the approval-time push handler). Those run
# outside a member's request, so inheriting the ambient tenant is exactly what
# H4 forbids; they stay unbound until H4 threads an explicit tenant through.
from gateway.db import get_db as _get_db  # noqa: F401
from gateway.db import tenant_session as _tenant_session  # noqa: F401
from gateway.routes.crm.flags import require_crm_enabled
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.crm")

#: The kill switch goes FIRST (WS-53 CRM-0). FastAPI runs router dependencies
#: in list order, so an organization outside ``CRM_ORGS`` gets 404 before the
#: feature gate can answer 403. Fence: ``tests/unit/test_crm_kill_switch.py``.
router = APIRouter(
    prefix="/crm", tags=["crm"],
    dependencies=[Depends(require_crm_enabled), require_feature_router("crm")],
)

#: The `source` vocabulary, mirrored from the CHECK constraint in migration 144.
#: Kept here so a bad value is a 422 at the boundary rather than an IntegrityError
#: 500 from the driver.
SOURCES: tuple[str, ...] = ("manual", "import", "email", "agent")

#: `crm_*_statuses.type` — the machine-readable class the pipeline rules key off.
STATUS_TYPES: tuple[str, ...] = ("open", "ongoing", "on_hold", "won", "lost")

#: Status types that close a record (§3.6: entering one stamps `closed_at`).
CLOSING_TYPES: tuple[str, ...] = ("won", "lost")

#: Status types whose deals are IN the pipeline, and therefore the only ones
#: weighted ₹ is summed over (D-CRM-10 / §5.1). `won` is *closed* revenue and
#: never pipeline; `lost` is nothing; and `on_hold` — the fifth type §5.1's
#: prose never mentions — is deliberately excluded too: a deal nobody is
#: working is not a forecast, and counting it at its stage's prior is how a
#: pipeline number stays high while the quarter empties.
WEIGHTED_TYPES: tuple[str, ...] = ("open", "ongoing")

#: The weighted-₹ aggregate — ONE expression, shared by every forecast surface.
#:
#: It lives here rather than beside its first caller because it now has two
#: (``pipeline.get_pipeline`` and ``reports``), and a second copy would defeat
#: ``tests/unit/_crm_fakes.py::_WEIGHTED_SUM_RE``, which reads the formula OUT
#: of the statement text precisely so a drifted copy changes the test's answer
#: rather than agreeing with itself. Import it; never retype it (WS-26g).
#:
#: ``COALESCE(probability, :stage_probability)`` is §5.1's inheritance rule read
#: at query time: the deal's own probability is what forecast math reads
#: (D-CRM-10), and a NULL only survives on rows that predate a move — which is
#: every imported deal, i.e. most of the live board. Reading those as 0% would
#: zero the forecast on exactly the rows the forecast is about.
#:
#: It also cannot be derived from a page of rows: ``get_pipeline`` caps ``rows``
#: at ``per_lane`` and the browser sends no cap, so a rows-derived figure would
#: silently under-report exactly the busy lane somebody is looking at.
#:
#: Cross-language twin: ``lib/board.ts::weightedDeal``/``weightedRows``. The one
#: fixture both sides read is ``tests/fixtures/crm_weighted_parity.json`` — two
#: independently typed tables are not parity.
WEIGHTED_SQL = (
    "COALESCE(SUM(amount * COALESCE(probability, :stage_probability) / 100.0), 0) "
    "AS weighted"
)

#: The deal columns a stage may demand before a deal is allowed to ENTER it
#: (WS-26h, §5.1 system 3) — an allowlist, validated when `required_fields` is
#: written rather than when it is read.
#:
#: An allowlist and not free text because the column is user-managed through
#: the settings grid: a typo'd name would name a column no deal can ever carry,
#: and the lane it guards would refuse every move with a message about a field
#: that does not exist. Validating on the way IN means the bad value never
#: reaches the row, so no lane can be bricked by a save.
#:
#: All four are nullable columns on `crm_deals` that a human fills in — which
#: is the whole population a requirement can sensibly name. `status_id` is not
#: here (it is what the move sets), nor is any NOT NULL column (already
#: guaranteed), nor `probability` (D-CRM-10 inherits it from the stage, so
#: demanding it would demand something the platform is about to supply).
STAGE_REQUIREABLE_FIELDS: tuple[str, ...] = (
    "amount", "expected_close_date", "organization_id", "owner_email",
)

#: `crm_activities.type`, mirrored from the same migration.
ACTIVITY_TYPES: tuple[str, ...] = (
    "note", "call", "meeting", "task", "status_change", "system",
)

MAX_PAGE_SIZE = 100


# ── Models ──────────────────────────────────────────────────────────────────
#
# Output model field names are the table's column names, 1:1, so `row_to_model`
# can map any row generically. Input models are all-optional: the same model
# serves POST and PATCH, with the create-time requirements declared once on the
# Entity (`required`) and checked in `records.py`. Two near-identical models per
# entity is the shape that drifts.

class OrganizationModel(BaseModel):
    id: str
    name: str
    website: str | None = None
    industry: str | None = None
    no_of_employees: int | None = None
    annual_revenue: float | None = None
    phone: str | None = None
    email: str | None = None
    address: dict | None = None
    description: str | None = None
    linkedin_url: str | None = None
    owner_email: str | None = None
    source: str = "manual"
    zoho_id: str | None = None
    last_activity_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class OrganizationIn(BaseModel):
    name: str | None = None
    website: str | None = None
    industry: str | None = None
    no_of_employees: int | None = None
    annual_revenue: float | None = None
    phone: str | None = None
    email: str | None = None
    address: dict | None = None
    description: str | None = None
    linkedin_url: str | None = None
    owner_email: str | None = None
    source: str | None = None


class ContactModel(BaseModel):
    id: str
    first_name: str
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    title: str | None = None
    organization_id: str | None = None
    description: str | None = None
    linkedin_url: str | None = None
    owner_email: str | None = None
    source: str = "manual"
    zoho_id: str | None = None
    last_activity_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ContactIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    title: str | None = None
    organization_id: str | None = None
    description: str | None = None
    linkedin_url: str | None = None
    owner_email: str | None = None
    source: str | None = None


class LeadModel(BaseModel):
    id: str
    first_name: str | None = None
    last_name: str | None = None
    lead_name: str
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    organization_name: str | None = None
    website: str | None = None
    industry: str | None = None
    no_of_employees: int | None = None
    annual_revenue: float | None = None
    status_id: str | None = None
    lead_source: str | None = None
    owner_email: str | None = None
    description: str | None = None
    lost_reason_id: str | None = None
    lost_note: str | None = None
    converted_at: str | None = None
    converted_contact_id: str | None = None
    converted_organization_id: str | None = None
    converted_deal_id: str | None = None
    source: str = "manual"
    zoho_id: str | None = None
    last_activity_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class LeadIn(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    lead_name: str | None = None
    email: str | None = None
    phone: str | None = None
    mobile: str | None = None
    organization_name: str | None = None
    website: str | None = None
    industry: str | None = None
    no_of_employees: int | None = None
    annual_revenue: float | None = None
    status_id: str | None = None
    lead_source: str | None = None
    owner_email: str | None = None
    description: str | None = None
    lost_reason_id: str | None = None
    lost_note: str | None = None
    source: str | None = None


class DealModel(BaseModel):
    id: str
    name: str
    organization_id: str | None = None
    #: NOT a column — projected by the LEFT JOIN in :func:`project_joined` so a
    #: kanban card can print the account name without the browser client-side
    #: joining a paged organization list against it (WS-26c done-when 2). A row
    #: read straight from ``crm_deals`` leaves it None, which is the honest
    #: answer for a deal with no organization.
    organization_name: str | None = None
    status_id: str | None = None
    status_changed_at: str | None = None
    amount: float | None = None
    currency: str = "INR"
    probability: int | None = None
    expected_close_date: str | None = None
    closed_at: str | None = None
    lost_reason_id: str | None = None
    lost_note: str | None = None
    next_step: str | None = None
    lead_id: str | None = None
    owner_email: str | None = None
    description: str | None = None
    source: str = "manual"
    zoho_id: str | None = None
    last_activity_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DealIn(BaseModel):
    name: str | None = None
    organization_id: str | None = None
    status_id: str | None = None
    amount: float | None = None
    currency: str | None = None
    probability: int | None = None
    expected_close_date: str | None = None
    lost_reason_id: str | None = None
    lost_note: str | None = None
    next_step: str | None = None
    lead_id: str | None = None
    owner_email: str | None = None
    description: str | None = None
    source: str | None = None


class ActivityModel(BaseModel):
    id: str
    type: str
    subject: str | None = None
    body: str | None = None
    occurred_at: str | None = None
    due_at: str | None = None
    completed_at: str | None = None
    lead_id: str | None = None
    deal_id: str | None = None
    contact_id: str | None = None
    organization_id: str | None = None
    created_by: str | None = None
    meta: dict | None = None
    zoho_id: str | None = None
    created_at: str | None = None


class StatusModel(BaseModel):
    id: str
    name: str
    color: str = "gray"
    position: int = 0
    type: str = "open"
    is_default: bool = False
    #: Deal statuses only — a lead status has no win probability.
    probability: int | None = None
    #: Deal statuses only (WS-26h) — the deal columns a deal must carry before
    #: it may ENTER this stage, drawn from :data:`STAGE_REQUIREABLE_FIELDS`.
    #: Empty on a lead status, which is the honest answer rather than a
    #: convenient one: leads have no entry requirements.
    required_fields: list[str] = []
    #: Deal statuses only (WS-26h) — days a deal may sit here before its card
    #: wears an age badge. ``None`` = this stage never rots.
    max_dwell_days: int | None = None


class LostReasonModel(BaseModel):
    id: str
    label: str
    position: int = 0


class ListResponse(BaseModel):
    """The one list shape every collection endpoint returns."""

    rows: list[dict]
    total: int


# ── Entity registry ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entity:
    """One CRM record type, and everything generic code needs to know about it."""

    slug: str
    table: str
    #: Singular, capitalised — what a 404 calls this record ("Lead not found").
    label: str
    #: The column in ``crm_activities`` that points at this entity.
    activity_column: str
    model: type[BaseModel]
    payload: type[BaseModel]
    #: Fields a POST must supply. PATCH never re-checks them.
    required: tuple[str, ...]
    #: Wire sort key → the SQL expression it is allowed to order by. This dict
    #: IS the allowlist: anything not a key here is a 422.
    sorts: dict[str, str]
    default_sort: str
    #: Columns ``?q=`` matches with ILIKE.
    search: tuple[str, ...] = ()
    #: The statuses table this entity's ``status_id`` points at, if any.
    status_table: str | None = None
    #: Leads only: default lists hide converted rows (§3.3). ``?include_converted``
    #: brings them back rather than a second endpoint.
    hides_converted: bool = False
    #: Child tables a DELETE takes with it, as ``(table, fk_column)``. Reported
    #: in the delete response — R7/R8: a destructive route names what cascaded.
    cascades: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: The Zoho module this entity's rows live in (WS-26b, §7.1's mapping
    #: table). Declared HERE rather than in a second dict beside the sync
    #: engine so "which Zoho module is a deal" has one answer that the
    #: importer, the sync engine and the delete path all read.
    zoho_module: str | None = None
    #: What ``crm_zoho_tombstones.entity_type`` calls this entity — singular,
    #: matching ``crm_status_changes``' vocabulary rather than the URL slug.
    zoho_entity_type: str | None = None
    #: Display columns that live on a *related* table and are projected onto
    #: every listed row by :func:`project_joined`, as
    #: ``(output_name, "<alias>.<column>")``. Empty for entities that need no
    #: join — the statement is then byte-identical to the un-joined one.
    joined_columns: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: The ``LEFT JOIN`` that supplies :attr:`joined_columns`. Written against
    #: the derived table ``base`` (see :func:`project_joined`) so the join can
    #: never make an unqualified column in a WHERE clause ambiguous.
    join_sql: str = ""


_TIMESTAMP_SORTS = {
    "created_at": "created_at",
    "updated_at": "updated_at",
    "last_activity_at": "last_activity_at",
}

ORGANIZATIONS = Entity(
    slug="organizations",
    table="crm_organizations",
    label="Organization",
    activity_column="organization_id",
    model=OrganizationModel,
    payload=OrganizationIn,
    required=("name",),
    sorts={"name": "name", "owner_email": "owner_email", **_TIMESTAMP_SORTS},
    default_sort="last_activity_at",
    search=("name", "email", "website"),
    cascades=(("crm_activities", "organization_id"),),
    # Zoho calls an organization an Account — the one place that rename lives.
    zoho_module="Accounts",
    zoho_entity_type="organization",
)

CONTACTS = Entity(
    slug="contacts",
    table="crm_contacts",
    label="Contact",
    activity_column="contact_id",
    model=ContactModel,
    payload=ContactIn,
    required=("first_name",),
    sorts={
        "first_name": "first_name", "last_name": "last_name", "email": "email",
        "owner_email": "owner_email", **_TIMESTAMP_SORTS,
    },
    default_sort="last_activity_at",
    search=("first_name", "last_name", "email"),
    cascades=(
        ("crm_activities", "contact_id"),
        ("crm_deal_contacts", "contact_id"),
    ),
    zoho_module="Contacts",
    zoho_entity_type="contact",
)

LEADS = Entity(
    slug="leads",
    table="crm_leads",
    label="Lead",
    activity_column="lead_id",
    model=LeadModel,
    payload=LeadIn,
    required=(),  # lead_name is computed, never demanded — see compute_lead_name
    sorts={
        "lead_name": "lead_name", "email": "email", "owner_email": "owner_email",
        "status_id": "status_id", "annual_revenue": "annual_revenue",
        "converted_at": "converted_at", **_TIMESTAMP_SORTS,
    },
    default_sort="created_at",
    search=("lead_name", "email", "organization_name"),
    status_table="crm_lead_statuses",
    hides_converted=True,
    cascades=(("crm_activities", "lead_id"),),
    zoho_module="Leads",
    zoho_entity_type="lead",
)

DEALS = Entity(
    slug="deals",
    table="crm_deals",
    label="Deal",
    activity_column="deal_id",
    model=DealModel,
    payload=DealIn,
    required=("name",),
    sorts={
        "name": "name", "amount": "amount", "probability": "probability",
        "expected_close_date": "expected_close_date", "closed_at": "closed_at",
        "status_changed_at": "status_changed_at", "owner_email": "owner_email",
        **_TIMESTAMP_SORTS,
    },
    default_sort="last_activity_at",
    search=("name", "next_step"),
    status_table="crm_deal_statuses",
    cascades=(
        ("crm_activities", "deal_id"),
        ("crm_deal_contacts", "deal_id"),
    ),
    zoho_module="Deals",
    zoho_entity_type="deal",
    # A kanban card prints the account name. Serving it here rather than in the
    # browser is not an optimisation: the org list the client would join
    # against is paged at 100, so a board with the 101st organization on it
    # would render blank labels that look like missing data (WS-26c dw 2).
    joined_columns=(("organization_name", "org.name"),),
    join_sql=(
        " LEFT JOIN crm_organizations org ON org.id = base.organization_id"
    ),
)

#: URL segment → entity. The segment is matched against this dict, never
#: interpolated, so `/crm/{entity}/…` cannot name a table we did not choose.
ENTITIES: dict[str, Entity] = {
    e.slug: e for e in (LEADS, DEALS, CONTACTS, ORGANIZATIONS)
}

#: Activity target column → the table whose recency that target drives.
#: :func:`record_activity` bumps through this, so no caller can forget to.
TABLE_BY_ACTIVITY_TARGET: dict[str, str] = {
    e.activity_column: e.table for e in ENTITIES.values()
}


# ── Wire conversion ─────────────────────────────────────────────────────────

def wire(value: Any) -> Any:
    """One DB value → its JSON-safe form. UUIDs and instants become strings."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def from_jsonb(value: Any) -> Any:
    """A ``JSONB`` column as the driver hands it back.

    Raw ``text()`` over asyncpg returns jsonb as a **string** — there is no
    declared column type to decode against — so a model field typed ``dict``
    would reject it. Same rule, same reason, as
    ``routes/tasks/core.py::_parse_jsonb``.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def row_to_model(row: Any, model: type[BaseModel]) -> Any:
    """Map a DB row onto ``model`` by field name.

    Generic on purpose: the output models' fields ARE the table's columns, so a
    column added to a table and its model needs no mapper edit — and a column
    added to only one of the two shows up as a missing field rather than as a
    silently dropped value.
    """
    data = {
        name: (
            from_jsonb(getattr(row, name, None)) if name in JSONB_COLUMNS
            else wire(getattr(row, name, None))
        )
        for name in model.model_fields
    }
    return model(**data)


def row_to_dict(row: Any, model: type[BaseModel]) -> dict[str, Any]:
    return row_to_model(row, model).model_dump()


def status_wire(row: Any) -> StatusModel:
    """A ``crm_{lead,deal}_statuses`` row → its wire model.

    Not :func:`row_to_model`: every reader wants the same defensive defaults
    (a NULL ``color`` renders as grey, a missing ``position`` sorts first),
    and ``probability`` exists on deal statuses only.

    It lives here rather than beside a caller because there are now THREE —
    ``admin``, ``pipeline`` and ``reports`` — and three hand-kept copies of one
    projection is how one of them quietly stops reporting ``probability``,
    which is the number every forecast surface reads.
    """
    return StatusModel(
        id=str(row.id),
        name=row.name,
        color=getattr(row, "color", "gray") or "gray",
        position=int(getattr(row, "position", 0) or 0),
        type=getattr(row, "type", "open"),
        is_default=bool(getattr(row, "is_default", False)),
        probability=getattr(row, "probability", None),
        # `or []` covers both a lead-status row (no such column) and the window
        # R6 opens on deploy, where the running code meets a table that has not
        # got the column yet. Copied rather than aliased so a caller cannot
        # mutate the row's own list.
        required_fields=list(getattr(row, "required_fields", None) or []),
        max_dwell_days=getattr(row, "max_dwell_days", None),
    )


def actor(user: Any) -> str:
    """The acting identity, from the authenticated context only (R3/R4).

    Never from a query parameter or a body field: ``created_by`` is what the
    timeline attributes an action to, and a client-supplied one would let a
    caller write history in somebody else's name.
    """
    email = (getattr(user, "email", None) or "").strip()
    return email or "anonymous"


def compute_lead_name(values: dict[str, Any]) -> str:
    """§3.3's fallback chain — a lead always renders as *something*.

    names → organization_name → email local-part → 'Unnamed lead'. A NOT NULL
    column with no computed fallback is how "Untitled" ends up in a pipeline.
    """
    explicit = (values.get("lead_name") or "").strip()
    if explicit:
        return explicit
    person = " ".join(
        part for part in (values.get("first_name"), values.get("last_name"))
        if (part or "").strip()
    ).strip()
    if person:
        return person
    org = (values.get("organization_name") or "").strip()
    if org:
        return org
    email = (values.get("email") or "").strip()
    if email and "@" in email:
        return email.split("@", 1)[0]
    return email or "Unnamed lead"


def lead_name_is_derived(values: dict[str, Any]) -> bool:
    """Does this lead's stored ``lead_name`` still equal its computed one?

    The question a PATCH has to answer before re-deriving: a name the fallback
    chain produced is the chain's to update, and a name somebody typed is not.
    Answered by recomputing rather than by a ``lead_name_is_custom`` column,
    because a flag has to be maintained by every writer — the importer, the
    agent tools, the sync engine — and the one that forgets it silently turns
    a hand-edited name back into "Unnamed lead" (WS-26c dw 3).

    The one accepted cost: hand-typing exactly what the chain would have
    produced leaves the name derived, and a later name change re-derives it.
    That produces the same string, so nobody can tell.
    """
    stored = (values.get("lead_name") or "").strip()
    return stored == compute_lead_name({**values, "lead_name": None})


# ── The list contract ───────────────────────────────────────────────────────

DIRECTIONS: dict[str, str] = {"asc": "ASC", "desc": "DESC"}


@dataclass(frozen=True)
class ListQuery:
    """A validated list request, rendered as SQL fragments + bound parameters."""

    where: str
    order_by: str
    limit: int
    offset: int
    params: dict[str, Any]


def list_contract(
    entity: Entity,
    *,
    q: str | None = None,
    sort: str | None = None,
    direction: str = "desc",
    page: int = 1,
    page_size: int = 50,
    status_id: str | None = None,
    owner: str | None = None,
    source: str | None = None,
    extra_where: tuple[str, ...] = (),
    extra_params: dict[str, Any] | None = None,
) -> ListQuery:
    """Validate one list request and render it. The same contract for all four.

    Raises 422 for an unknown sort key or direction. That is deliberately not a
    silent fall back to the default: a client sorting by a column it thinks
    exists and quietly getting `created_at` is a bug that survives review.
    """
    column = entity.sorts.get(sort or entity.default_sort)
    if column is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown sort key '{sort}' for {entity.slug}. "
                f"One of: {sorted(entity.sorts)}."
            ),
        )
    order = DIRECTIONS.get((direction or "").lower())
    if order is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort direction '{direction}'. One of: asc, desc.",
        )

    clauses: list[str] = list(extra_where)
    params: dict[str, Any] = dict(extra_params or {})

    if q and entity.search:
        params["q"] = f"%{q.strip()}%"
        matches = " OR ".join(f"{col} ILIKE :q" for col in entity.search)
        clauses.append(f"({matches})")
    if status_id:
        if not entity.status_table:
            # Contacts and organizations have no pipeline. Ignoring the filter
            # would answer 200 with the WHOLE table for a caller who asked for
            # one lane — the same class of silent-wrong-answer as falling back
            # to the default sort key, and the one a UI reuses the shared list
            # component into (WS-26c dw 3).
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{entity.slug} have no status pipeline, so '?status_id' "
                    "cannot filter them. Drop the parameter."
                ),
            )
        clauses.append("status_id = CAST(:status_id AS uuid)")
        params["status_id"] = status_id
    if owner:
        # R10 — email comparisons are case-insensitive on both sides.
        clauses.append("lower(owner_email) = :owner")
        params["owner"] = owner.strip().lower()
    if source:
        clauses.append("source = :source")
        params["source"] = source

    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    return ListQuery(
        where=(" WHERE " + " AND ".join(clauses)) if clauses else "",
        # `column` is a value from the entity's own allowlist and `order` is one
        # of two literals — neither is caller text.
        order_by=f"{column} {order} NULLS LAST, id {order}",
        limit=page_size,
        offset=(page - 1) * page_size,
        params=params,
    )


def project_joined(entity: Entity, inner: str, order_by: str) -> str:
    """Wrap a base-table SELECT so its rows carry their joined display names.

    The join is applied to a **derived table** rather than inlined into the
    base ``FROM``:

    .. code-block:: sql

        SELECT base.*, org.name AS organization_name
        FROM (SELECT * FROM crm_deals WHERE … ORDER BY … LIMIT …) base
        LEFT JOIN crm_organizations org ON org.id = base.organization_id
        ORDER BY base.…

    Inlining it would put two relations in scope for the WHERE clause, and
    ``crm_organizations`` carries ``owner_email``, ``source``, ``name`` and the
    timestamp trio too — so every unqualified predicate
    :func:`list_contract` renders would become ambiguous, and the fix would be
    to qualify all of them, in all four entities, for one entity's benefit.
    Filtering first also means the join runs over one page of deals, not the
    whole table.

    The outer ``ORDER BY`` is not decoration: a join over an ordered subquery
    does not preserve its order.
    """
    if not entity.joined_columns:
        return inner
    projected = ", ".join(f"{expr} AS {name}" for name, expr in entity.joined_columns)
    return (
        f"SELECT base.*, {projected} FROM ({inner}) base{entity.join_sql} "
        f"ORDER BY {order_by}"
    )


def qualify(order_by: str, prefix: str) -> str:
    """``created_at DESC NULLS LAST, id DESC`` → the same, prefixed.

    Only the column names :func:`list_contract` itself emitted are rewritten —
    each is an allowlist value or the literal ``id``, never caller text.
    """
    return ", ".join(f"{prefix}.{term.strip()}" for term in order_by.split(","))


async def run_list(db: Any, entity: Entity, query: ListQuery) -> ListResponse:
    """Execute a :class:`ListQuery` and return the ``{rows, total}`` shape."""
    total = (await db.execute(
        text(f"SELECT count(*) FROM {entity.table}{query.where}"), query.params,
    )).scalar() or 0
    inner = (
        f"SELECT * FROM {entity.table}{query.where} "
        f"ORDER BY {query.order_by} LIMIT :limit OFFSET :offset"
    )
    rows = (await db.execute(
        text(project_joined(entity, inner, qualify(query.order_by, "base"))),
        {**query.params, "limit": query.limit, "offset": query.offset},
    )).fetchall()
    return ListResponse(
        rows=[row_to_dict(r, entity.model) for r in rows], total=int(total),
    )


# ── SQL helpers ─────────────────────────────────────────────────────────────
#
# Every identifier reaching an f-string below is one of ours: a table name from
# the registry, or a key of a dict this module built from a Pydantic model's
# declared fields. Caller values are always bound parameters.

@asynccontextmanager
async def savepoint(db: Any) -> AsyncIterator[None]:
    """A nested transaction around ONE unit of work (WS-26b).

    ⚠️ **Postgres aborts the entire transaction on any statement error.** Not
    the statement — the transaction. After one `numeric field overflow` every
    later statement answers `current transaction is aborted, commands ignored
    until end of transaction block`, so a loop with a per-record
    ``try/except`` looks like it survived one bad row while in fact it lost
    every row after it, plus the cursor write, plus the commit. The next cycle
    then reads the same data and dies identically — forever.

    A ``SAVEPOINT`` is the only thing that makes "one bad record must not lose
    the batch" true against a real database. The test fakes have no nested
    transactions, so this degrades to a pass-through there and the seam stays
    unit-testable; that is also why the tests that matter assert the savepoint
    was *taken*, not that the fake rolled anything back.
    """
    begin_nested = getattr(db, "begin_nested", None)
    if begin_nested is None:  # pragma: no cover — only the fakes lack it
        yield
        return
    async with begin_nested():
        yield


async def load_row(db: Any, table: str, record_id: str) -> Any | None:
    return (await db.execute(
        text(f"SELECT * FROM {table} WHERE id = CAST(:id AS uuid)"),
        {"id": record_id},
    )).fetchone()


async def require_row(db: Any, table: str, record_id: str, what: str) -> Any:
    row = await load_row(db, table, record_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return row


#: Columns declared ``JSONB`` in migration 144. asyncpg has no codec for a bare
#: Python dict, so these are serialized here and cast in the statement.
JSONB_COLUMNS: frozenset[str] = frozenset({"address", "meta"})

#: ``TIMESTAMPTZ`` / ``DATE`` columns a request body may set as an ISO string.
#: asyncpg binds real ``datetime``/``date`` objects, so the string is parsed
#: here rather than cast in SQL — a ``CAST(:col AS timestamptz)`` would force
#: the parameter to text and then break the paths that bind a real datetime.
TIMESTAMP_COLUMNS: frozenset[str] = frozenset({
    "occurred_at", "due_at", "completed_at", "converted_at", "closed_at",
    "status_changed_at", "changed_at", "last_activity_at",
})
DATE_COLUMNS: frozenset[str] = frozenset({"expected_close_date"})


def coerce_write_values(values: dict[str, Any]) -> dict[str, Any]:
    """Request-shaped values → driver-shaped values. ONE choke point.

    Every write in this package goes through :func:`insert_row` or
    :func:`update_row`, so this runs on all of them and a new endpoint inherits
    it. A malformed instant answers **422 naming the column** rather than
    surfacing as a driver error a caller cannot act on.
    """
    out = dict(values)
    for column, value in values.items():
        if value is None or not isinstance(value, str):
            continue
        if column in TIMESTAMP_COLUMNS:
            out[column] = _parse(datetime.fromisoformat, value, column, "instant")
        elif column in DATE_COLUMNS:
            out[column] = _parse(date.fromisoformat, value, column, "date")
    return out


#: Table → the columns migration 144 declares ``NOT NULL DEFAULT <x>``.
#:
#: These are the columns where **omitting** the field and **sending it as
#: ``null``** are two different requests and only one of them is legal:
#: omitting asks for the default, ``null`` asks Postgres to violate a NOT NULL
#: constraint. ``clean_payload`` keeps an explicit ``null`` on purpose (it is
#: how a client clears a nullable field), so without this the driver answers
#: with an IntegrityError the caller reads as a 500 — a client bug reported as
#: a server fault (WS-26c dw 3).
#:
#: Hand-kept against the migration and pinned against it by
#: ``test_crm_migration.py``. Only NOT NULL columns **with a default** belong
#: here: a NOT NULL column without one is a missing *required* field, which
#: ``Entity.required`` and the route-level checks already answer.
NOT_NULL_DEFAULTED: dict[str, frozenset[str]] = {
    "crm_organizations": frozenset({"source"}),
    "crm_contacts": frozenset({"source"}),
    "crm_leads": frozenset({"source"}),
    "crm_deals": frozenset({"source", "currency", "status_changed_at"}),
    "crm_lead_statuses": frozenset({"color", "is_default"}),
    # `required_fields` arrives by ALTER in the WS-26h migration rather than in
    # 144's CREATE TABLE, and it is here for the same reason the others are: the
    # settings grid PATCHes it, so an explicit `null` is a request a client can
    # actually make and must answer 422 rather than a driver 500.
    "crm_deal_statuses": frozenset({
        "color", "is_default", "probability", "required_fields",
    }),
    "crm_lost_reasons": frozenset({"position"}),
    "crm_deal_contacts": frozenset({"is_primary"}),
    # Platform-written only (pipeline.apply_status_transition), and named here
    # anyway: the map is keyed by table, so a table left out is silently
    # unguarded rather than loudly wrong.
    "crm_status_changes": frozenset({"changed_at"}),
}


def reject_null_on_defaulted(table: str, values: dict[str, Any]) -> None:
    """422 for an explicit ``null`` on a defaulted NOT NULL column.

    Called from :func:`insert_row` and :func:`update_row` — the package's only
    two write paths — so a route added later inherits it rather than having to
    remember it.
    """
    nulled = sorted(
        column for column in NOT_NULL_DEFAULTED.get(table, frozenset())
        if column in values and values[column] is None
    )
    if nulled:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{nulled} cannot be set to null on {table} — the column is "
                "NOT NULL with a default. Omit the field to take the default."
            ),
        )


def _parse(parser: Any, value: str, column: str, what: str) -> Any:
    try:
        return parser(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"'{column}' is not a valid ISO-8601 {what}: {value!r}.",
        ) from exc


def _placeholder(column: str) -> str:
    """The bind expression for one column — jsonb needs the cast, nothing else."""
    return f"CAST(:{column} AS jsonb)" if column in JSONB_COLUMNS else f":{column}"


def _bindable(values: dict[str, Any]) -> dict[str, Any]:
    """Coerce, then serialize the jsonb columns to the text their cast expects."""
    out = coerce_write_values(values)
    for column in JSONB_COLUMNS & out.keys():
        if out[column] is not None and not isinstance(out[column], str):
            out[column] = json.dumps(out[column])
    return out


#: The four tables the Zoho sync dirty-tracks (§7.1, migration 145). Statuses,
#: activities and the change log are deliberately absent: pipeline vocabulary
#: flows DOWN only, and an activity carries its own push signal (a NULL
#: ``zoho_id``), so a dirty column there would be a second answer to one
#: question.
ZOHO_TRACKED_TABLES: frozenset[str] = frozenset({
    "crm_organizations", "crm_contacts", "crm_leads", "crm_deals",
})


def mark_dirty_on_insert(table: str, values: dict[str, Any]) -> dict[str, Any]:
    """A new tracked row starts dirty **unless it came from Zoho** (§7.1).

    "Came from Zoho" is read off the values themselves: the importer and the
    pull both carry a ``zoho_id``, a native create never does. That is the
    create half of echo suppression, and keying it on the payload rather than
    on a flag the caller must remember means a pull path added later cannot
    accidentally push everything it just read back up.
    """
    if table not in ZOHO_TRACKED_TABLES or "zoho_dirty" in values:
        return values
    return {**values, "zoho_dirty": values.get("zoho_id") is None}


def mark_dirty_on_update(
    table: str, values: dict[str, Any], *, touch: bool,
) -> dict[str, Any]:
    """A native update dirties a tracked row; a pull-applied one never does.

    ``touch`` is the seam, and it is the seam on purpose: it already means
    "this is a real edit, bump ``updated_at``". A pull applies its writes with
    ``touch=False`` (so LWW keeps comparing Zoho's ``Modified_Time`` against a
    timestamp Zoho itself produced), and the same flag therefore answers
    "should this row be pushed back". One switch, not two that can disagree.

    An explicit ``zoho_dirty`` in *values* wins — that is how the push phase
    clears the flag and how a conflict resolved in Zoho's favour discards the
    native edit.
    """
    if not touch or table not in ZOHO_TRACKED_TABLES or "zoho_dirty" in values:
        return values
    return {**values, "zoho_dirty": True}


async def insert_row(db: Any, table: str, values: dict[str, Any]) -> Any:
    reject_null_on_defaulted(table, values)
    values = mark_dirty_on_insert(table, values)
    columns = list(values)
    placeholders = ", ".join(_placeholder(c) for c in columns)
    return (await db.execute(
        text(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) RETURNING *"
        ),
        _bindable(values),
    )).fetchone()


async def update_row(
    db: Any, table: str, record_id: str, values: dict[str, Any],
    *, touch: bool = True,
) -> Any:
    reject_null_on_defaulted(table, values)
    values = mark_dirty_on_update(table, values, touch=touch)
    assignments = [f"{c} = {_placeholder(c)}" for c in values]
    if touch:
        assignments.append("updated_at = now()")
    return (await db.execute(
        text(
            f"UPDATE {table} SET {', '.join(assignments)} "
            f"WHERE id = CAST(:record_id AS uuid) RETURNING *"
        ),
        {**_bindable(values), "record_id": record_id},
    )).fetchone()


#: Columns an ``ON CONFLICT (zoho_id)`` upsert writes on INSERT and never
#: rewrites on the conflict arm.
#:
#: ``zoho_id`` is the key it matched on. ``source`` is **provenance**: a row
#: typed into this app is ``'manual'`` forever, even after the sync pushes it
#: to Zoho and pulls it back. Rewriting it would flip every native-origin row
#: to ``'import'`` on its first echo — a silent, one-way rewrite of the column
#: the `source` filter and every "where did this come from" question read.
INSERT_ONLY_COLUMNS: frozenset[str] = frozenset({"zoho_id", "source"})


async def upsert_by_zoho_id(db: Any, table: str, values: dict[str, Any]) -> Any:
    """Write a row keyed on its Zoho provenance — §7.1's ``ON CONFLICT (zoho_id)``.

    One statement, so a backfill replayed after a partial failure (or two
    cycles racing) cannot produce a second copy of the same Zoho record. The
    ``UNIQUE`` on every ``zoho_id`` column is what makes the arm reachable.

    ⚠️ It deliberately does **not** touch ``updated_at`` on the conflict arm.
    ``updated_at`` is the timestamp last-writer-wins compares against Zoho's
    ``Modified_Time``, so it has to keep meaning "when a HUMAN last changed
    this here". Bumping it on every pull would make the native side look
    perpetually newer and turn LWW into always-native-wins. (It also could not
    be applied generically: ``crm_activities`` has no such column.)

    ⚠️ Nor does it rewrite :data:`INSERT_ONLY_COLUMNS` — see that constant.
    """
    values = mark_dirty_on_insert(table, values)
    columns = list(values)
    placeholders = ", ".join(_placeholder(c) for c in columns)
    assignments = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in columns if c not in INSERT_ONLY_COLUMNS
    )
    return (await db.execute(
        text(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (zoho_id) DO UPDATE SET {assignments} "
            f"RETURNING *"
        ),
        _bindable(values),
    )).fetchone()


async def load_by_zoho_id(db: Any, table: str, zoho_id: str) -> Any | None:
    """The native row a Zoho record already maps to, if there is one."""
    return (await db.execute(
        text(f"SELECT * FROM {table} WHERE zoho_id = :zoho_id"),
        {"zoho_id": zoho_id},
    )).fetchone()


async def record_zoho_tombstone(
    db: Any, entity: Entity, record: Any, *, deleted_by: str,
) -> bool:
    """Remember a zoho-linked record we are about to delete (§7.1).

    Called from inside the delete transaction, **before** the ``DELETE``, and
    returns whether a tombstone was written. A tombstone cannot be a column on
    a row that no longer exists, and it cannot be written afterwards either:
    the ``zoho_id`` it needs disappears with the row.

    Returns ``False`` — writing nothing — for a record Zoho has never seen.
    There is nothing upstream to delete, and a tombstone with no ``zoho_id``
    would be a permanent un-pushable row in the backlog.
    """
    zoho_id = (getattr(record, "zoho_id", None) or "").strip()
    if not zoho_id or not entity.zoho_module:
        return False
    await insert_row(db, "crm_zoho_tombstones", {
        "module": entity.zoho_module,
        "zoho_id": zoho_id,
        "entity_type": entity.zoho_entity_type,
        "deleted_by": deleted_by,
    })
    return True


async def load_default_status(db: Any, table: str) -> Any:
    """The status a new record lands in: the flagged default, else the first lane.

    Falling back to the lowest ``position`` rather than failing is deliberate —
    an owner who deletes the row that happened to carry ``is_default`` must not
    discover it by being unable to create a deal.
    """
    for clause in ("WHERE is_default ", ""):
        row = (await db.execute(
            text(f"SELECT * FROM {table} {clause}ORDER BY position, name LIMIT 1"),
            {},
        )).fetchone()
        if row is not None:
            return row
    raise HTTPException(
        status_code=422,
        detail=f"No statuses are configured in {table}; create one first.",
    )


def has_column(entity: Entity, name: str) -> bool:
    """Does this entity's table carry ``name``?

    Answered from the output model, which mirrors the columns 1:1 — so
    "deals have a stage-age clock and leads do not" is read from the schema's
    own projection rather than from a second hand-kept list.
    """
    return name in entity.model.model_fields


async def count_where(db: Any, table: str, column: str, value: str) -> int:
    total = (await db.execute(
        text(f"SELECT count(*) FROM {table} WHERE {column} = CAST(:value AS uuid)"),
        {"value": value},
    )).scalar()
    return int(total or 0)


async def bump_last_activity(db: Any, table: str, record_id: str) -> None:
    """Denormalized recency, maintained by every write to a record's timeline.

    trycompai's discipline, and the reason "sort by last touched" is an index
    scan rather than a correlated subquery over the activity spine.
    """
    await db.execute(
        text(
            f"UPDATE {table} SET last_activity_at = now() "
            f"WHERE id = CAST(:id AS uuid)"
        ),
        {"id": record_id},
    )


async def record_activity(
    db: Any,
    *,
    activity_type: str,
    created_by: str,
    target_column: str,
    target_id: str,
    subject: str | None = None,
    body: str | None = None,
    occurred_at_now: bool = True,
    extra: dict[str, Any] | None = None,
) -> Any:
    """Write one timeline row and bump its target's ``last_activity_at``.

    ``target_column`` is an entity's ``activity_column`` — one of four literals
    from the registry — so the CHECK constraint requiring at least one target
    can never be reached with all four NULL through this path.
    """
    values: dict[str, Any] = {
        "type": activity_type,
        "subject": subject,
        "body": body,
        "created_by": created_by,
        target_column: target_id,
        **(extra or {}),
    }
    if occurred_at_now:
        values.setdefault("occurred_at", _now())
    row = await insert_row(db, "crm_activities", values)
    # The docstring's promise, kept HERE so the next caller (the WS-26b
    # importer, WS-26d's agent tools) cannot ship a record that sorts as
    # never-touched by trusting it.
    await bump_last_activity(db, TABLE_BY_ACTIVITY_TARGET[target_column], target_id)
    return row


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


def now() -> datetime:
    """The one clock this package reads, so tests can freeze it in one place."""
    return _now()


async def link_deal_contact(
    db: Any,
    deal_id: str,
    contact_id: str,
    *,
    role: str | None = None,
    is_primary: bool | None = None,
) -> Any:
    """Attach a contact to a deal, keeping "one primary per deal" true.

    §3.5 says the rule is enforced *in code* — the migration deliberately has
    no partial unique index, because the WS-26b importer sets primaries in a
    pass that would fight one. "In code" was WS-26a's convention and nothing
    else: the only writer was the convert path, and it wrote ``is_primary``
    straight into an INSERT. This function is the enforcement WS-26c owes
    (dw 2), and it is on the **shared** seam rather than on the route, so the
    convert path and the deal-contacts endpoints cannot disagree about it.

    Promoting a contact demotes the incumbent in the same transaction, before
    the promotion — the intermediate state is "no primary", never "two".

    ⚠️ **Both optional fields are tri-state: ``None`` means "leave it alone".**
    ``role`` was written that way from the start and ``is_primary`` was not,
    so ``PATCH``-ing a contact's role demoted the deal's primary as a side
    effect — the caller said nothing about the flag and a default of ``False``
    said "no". A field the caller did not mention must not be a decision.
    """
    if is_primary:
        await db.execute(
            text(
                "UPDATE crm_deal_contacts SET is_primary = false "
                "WHERE deal_id = CAST(:deal_id AS uuid)"
            ),
            {"deal_id": deal_id},
        )
    existing = (await db.execute(
        text(
            "SELECT * FROM crm_deal_contacts "
            "WHERE deal_id = CAST(:deal_id AS uuid) "
            "AND contact_id = CAST(:contact_id AS uuid)"
        ),
        {"deal_id": deal_id, "contact_id": contact_id},
    )).fetchone()
    if existing is None:
        values: dict[str, Any] = {
            "deal_id": deal_id,
            "contact_id": contact_id,
            "role": role,
        }
        # Unstated on a NEW row means the column's own default (false) —
        # omitted rather than sent as null, which the NOT NULL guard refuses.
        if is_primary is not None:
            values["is_primary"] = is_primary
        return await insert_row(db, "crm_deal_contacts", values)
    # The PK is (deal_id, contact_id), so re-adding a linked contact is an
    # update of its role/primary flag rather than a 409: "make Priya the
    # primary" is the same request whether or not she is already on the deal.
    return (await db.execute(
        text(
            "UPDATE crm_deal_contacts SET role = :role, is_primary = :is_primary "
            "WHERE deal_id = CAST(:deal_id AS uuid) "
            "AND contact_id = CAST(:contact_id AS uuid) RETURNING *"
        ),
        {
            "deal_id": deal_id, "contact_id": contact_id,
            "role": role if role is not None else getattr(existing, "role", None),
            "is_primary": (
                is_primary if is_primary is not None
                else bool(getattr(existing, "is_primary", False))
            ),
        },
    )).fetchone()


def clean_payload(payload: BaseModel) -> dict[str, Any]:
    """A PATCH body → only the fields the caller actually sent.

    ``exclude_unset`` and not ``exclude_none``: sending ``null`` is how a client
    clears a field, and collapsing the two would make "unset" and "clear" the
    same request.
    """
    return payload.model_dump(exclude_unset=True)


def validate_source(values: dict[str, Any]) -> None:
    source = values.get("source")
    if source is not None and source not in SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown source '{source}'. One of: {list(SOURCES)}.",
        )
