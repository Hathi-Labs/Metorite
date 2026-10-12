"""CRM · stage_metadata — repairing the pipeline from Zoho's own settings.

Spec: ``project-docs/specs/crm_app.md`` §5.1 (system 1) · ticket WS-26f
f1 + f4 · decisions D-CRM-10 (probability) and D-CRM-11 (one pipeline).

    POST /crm/import/zoho/stages            → dry run: the full report, no writes
    POST /crm/import/zoho/stages?apply=true → the same report, applied

**Why this module exists.** ``import_zoho.ensure_status`` appends a stage Zoho
has and we do not past the last ``position`` with ``probability = 0`` — the
right refusal at import time, the wrong place to leave the data forever. 144's
six seed lanes are *renamed* variants of Zoho's defaults, so name-match missed
the tenant's real stages and the board reads as seed lanes (some empty)
followed by whatever order the importer first encountered. The tenant's true
lane order and forecast semantics live in the settings API; this reads them.

**The three properties that make it safe to hand to an owner:**

1. **Dry-run by default.** ``?apply=true`` is the write, and that exact string
   is the registered owner gate (``work_plan.md`` §6). A dry run issues no
   INSERT, UPDATE or DELETE and never commits — not "no rows", no *statements*.
2. **More than one pipeline STOPS the run, applied or not** (D-CRM-11). A
   name-match repair against the wrong pipeline scrambles the board it was
   meant to fix, so the decision goes back to the owner first. The stop returns
   before the database is opened at all.
3. **No deal row is touched by the stage repair.** Statuses are native config
   and are absent from ``core.ZOHO_TRACKED_TABLES``, so the repair queues zero
   Zoho pushes. The one deal-row write in this module is f4's ``closed_at``
   proxy, which is a **direct UPDATE** precisely so it does not travel through
   ``core.update_row`` → ``mark_dirty_on_update``: routing 500+ imported deals
   through the dirty-marking path would queue that many no-op pushes into the
   live Zoho tenant on the next sync cycle.

**The Zoho read is reached through ``import_zoho._client()``** rather than a
second seam of its own, so a test binds ONE fake tenant for the importer and
for this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger
from fastapi import Depends, Query
from gateway.routes.crm import import_zoho
from gateway.routes.crm.core import (
    CLOSING_TYPES,
    STATUS_TYPES,
    _tenant_session,
    actor,
    insert_row,
    refuse_in_mirror_mode,
    router,
    update_row,
)
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.crm.stage_metadata")

#: The statuses table this repair reshapes. Named once: it is not a caller
#: value and never will be — leads have no forecast semantics to import.
STATUS_TABLE = "crm_deal_statuses"

#: Zoho's ``forecast_type`` → our ``type``. Anything else keeps the type the
#: lane already has (D-CRM-10: "type stays king", and a guess that re-opened a
#: won lane would un-close every deal in it on the next transition).
FORECAST_TYPES: dict[str, str] = {
    "closed won": "won",
    "closed lost": "lost",
}

#: f4's UPDATE, one lane at a time. Written out as a module constant because
#: its SAFETY is a property of the statement text and is asserted against it:
#: it names ``closed_at`` and does not name ``zoho_dirty``, and no caller of it
#: goes through ``core.update_row``. ``expected_close_date`` is a DATE and
#: ``closed_at`` a TIMESTAMPTZ; Postgres's implicit date→timestamptz cast lands
#: it at midnight, which is the honest resolution of a date-only proxy.
CLOSED_AT_BACKFILL_SQL = (
    "UPDATE crm_deals SET closed_at = expected_close_date "
    "WHERE status_id = CAST(:status_id AS uuid) "
    "AND closed_at IS NULL AND expected_close_date IS NOT NULL "
    "RETURNING id"
)

#: The dry-run twin of the statement above — the same predicate, counted.
CLOSED_AT_ELIGIBLE_SQL = (
    "SELECT count(*) FROM crm_deals "
    "WHERE status_id = CAST(:status_id AS uuid) "
    "AND closed_at IS NULL AND expected_close_date IS NOT NULL"
)

#: Won/lost rows the tenant gave us no date for. Counted and reported, never
#: invented: "we do not know when this closed" is not the same as "today".
CLOSED_AT_UNDATED_SQL = (
    "SELECT count(*) FROM crm_deals "
    "WHERE status_id = CAST(:status_id AS uuid) "
    "AND closed_at IS NULL AND expected_close_date IS NULL"
)


# ── The report ──────────────────────────────────────────────────────────────

class StageChange(BaseModel):
    """One lane, before and after. Reported whether or not it moved."""

    name: str
    action: str  # "matched" | "created"
    changed: bool = False
    position_before: int | None = None
    position_after: int
    type_before: str | None = None
    type_after: str
    probability_before: int | None = None
    probability_after: int | None = None


class OrphanLane(BaseModel):
    """A native lane the tenant has no counterpart for — reported, never deleted.

    Deletion is the owner's call in the f2 settings grid, and ``ON DELETE
    RESTRICT`` 409s a lane still holding deals anyway — which is why the deal
    count travels with the name.
    """

    name: str
    position: int
    deals: int


class ProbabilityProbe(BaseModel):
    """Whether the per-stage probability could be read, and if not, why not.

    Four outcomes, and the distinctions are the point — each sends the owner
    somewhere different:

    * ``read`` — values came back; the repair applies them.
    * ``no_scope`` — re-mint the token with the named ``scope``.
    * ``no_data`` — the layout WAS read and carries no probability; set them by
      hand in the settings tab. This is the only one that is a statement ABOUT
      the layout, so it must never be the answer when no layout was read.
    * ``unavailable`` — we never got far enough to look (the layout read was
      refused, or the tenant returned no Deals layout). Points at the API
      version constant, not at f2.

    Collapsing any of these into one "unavailable" is the failure this field
    exists to prevent; so is letting the default speak for a read that never
    happened, which is why :class:`Metadata` starts at ``unavailable`` and only
    a code path that actually held the layout overwrites it.
    """

    outcome: str  # "read" | "no_scope" | "no_data" | "unavailable"
    scope: str | None = None
    detail: str | None = None
    values: int = 0


class ClosedAtBackfill(BaseModel):
    """f4 — the ``closed_at`` proxy, reported as its own section."""

    eligible: int = 0
    stamped: int = 0
    missing_close_date: int = 0


class StageMetadataReport(BaseModel):
    #: "dry_run" | "applied" | "stopped" | "unavailable".
    outcome: str
    applied: bool = False
    layout_id: str | None = None
    layout_name: str | None = None
    #: Every pipeline the layout returned. More than one is the D-CRM-11 stop.
    pipelines: list[str] = []
    stop_reason: str | None = None
    #: The exact OAuth scope an owner would have to add, when that is what
    #: stopped the read. Named rather than described — it goes into a console.
    missing_scope: str | None = None
    probability: ProbabilityProbe
    stages: list[StageChange] = []
    orphans: list[OrphanLane] = []
    #: ⚠️ **None means the backfill never ran**, not "it ran and found nothing".
    #: On the D-CRM-11 stop and on every unavailable outcome the database is
    #: never opened, so a zero here would be a MEASUREMENT this endpoint never
    #: took — and "0 close dates missing" reads as reassurance about data
    #: nobody looked at. Only the dry-run and applied paths set it.
    closed_at: ClosedAtBackfill | None = None
    #: How many lanes this run would change (or did). Zero on a second apply —
    #: that is done-when 2's idempotency, measured rather than asserted.
    changed: int = 0
    notes: list[str] = []
    errors: list[str] = []


# ── Reading the tenant's metadata (pure) ────────────────────────────────────

@dataclass
class TenantStage:
    """One stage as the tenant's pipeline describes it."""

    name: str
    sequence_number: int
    forecast_type: str = ""
    forecast_category: str = ""


@dataclass
class RepairPlan:
    """What an apply would do, computed before anything is written."""

    stages: list[StageChange] = field(default_factory=list)
    orphans: list[tuple[str, Any]] = field(default_factory=list)
    #: status id → the values to UPDATE, for the matched lanes only.
    updates: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The rows to INSERT, in sequence order.
    creates: list[dict[str, Any]] = field(default_factory=list)
    #: status id → the type it will hold after the repair. f4 reads this, not
    #: the stored type, so a lane retyped to won in the same apply has its
    #: deals backfilled in the same run.
    types_after: dict[str, str] = field(default_factory=dict)


def _text_of(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalize(name: str) -> str:
    """The key a tenant stage and a native lane are matched on.

    Case- and whitespace-insensitive, and nothing cleverer: a fuzzy match would
    silently fold "Proposal" into "Proposal/Price Quote", which is exactly the
    rename that made the board wrong in the first place. An unmatched tenant
    stage becomes a new lane; an unmatched native lane is reported.
    """
    return " ".join(name.split()).casefold()


def pick_deals_layout(layouts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Deals layout the pipeline read is scoped to.

    A tenant may have several; the first is taken and the count is reported,
    because picking one silently is the same class of mistake as picking one of
    several pipelines silently — the difference is that layouts do not change
    what a *deal* means, so this is a note rather than a stop.
    """
    return layouts[0] if layouts else None


def stage_probabilities(layout: dict[str, Any]) -> dict[str, int]:
    """Per-stage win probability out of the layout's Stage picklist.

    Zoho documents this inconsistently and this tenant already withholds
    record-level fields (§7.1), so an absent value is the expected answer and
    produces an empty dict — never a zero, which would look like a read.
    """
    out: dict[str, int] = {}
    for section in layout.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for stage_field in section.get("fields") or []:
            if not isinstance(stage_field, dict):
                continue
            if stage_field.get("api_name") != "Stage":
                continue
            for value in stage_field.get("pick_list_values") or []:
                if not isinstance(value, dict):
                    continue
                name = _text_of(value.get("display_value") or value.get("actual_value"))
                probability = value.get("probability")
                if name and isinstance(probability, int | float):
                    out[normalize(name)] = int(probability)
    return out


def pipeline_name(pipeline: dict[str, Any]) -> str:
    return (
        _text_of(pipeline.get("display_value"))
        or _text_of(pipeline.get("actual_value"))
        or _text_of(pipeline.get("name"))
        or "(unnamed)"
    )


def pipeline_stages(pipeline: dict[str, Any]) -> list[TenantStage]:
    """The stages of one pipeline, in the tenant's stated sequence.

    ``maps`` is what the pipeline API documents; ``stages`` is read too for the
    same reason ``list_deal_pipelines`` reads two response keys — misreading it
    is indistinguishable from "this tenant configured no stages", and both
    write nothing.
    """
    raw: list[Any] = []
    for key in ("maps", "stages"):
        found = pipeline.get(key)
        if isinstance(found, list):
            raw = found
            break

    out: list[TenantStage] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        name = _text_of(entry.get("display_value") or entry.get("actual_value"))
        if not name:
            continue
        sequence = entry.get("sequence_number")
        out.append(TenantStage(
            name=name,
            # A stage with no stated sequence keeps the order it arrived in
            # rather than collapsing onto position 0 with its neighbours.
            sequence_number=int(sequence) if isinstance(sequence, int | float)
            else index + 1,
            forecast_type=_text_of(entry.get("forecast_type")),
            forecast_category=_text_of(
                (entry.get("forecast_category") or {}).get("display_value")
                if isinstance(entry.get("forecast_category"), dict)
                else entry.get("forecast_category")
            ),
        ))
    return sorted(out, key=lambda s: s.sequence_number)


def resolve_type(forecast_type: str, current: str | None) -> str:
    """``forecast_type`` → ``type``, keeping what we have when Zoho is vague.

    D-CRM-10: no parallel forecast-category enum. "Open" and every unrecognised
    value leave the existing type alone (``open`` for a brand-new lane), which
    is the direction that never auto-closes a deal.
    """
    mapped = FORECAST_TYPES.get(forecast_type.casefold())
    if mapped:
        return mapped
    if current in STATUS_TYPES:
        return str(current)
    return "open"


def resolve_probability(
    type_after: str, probed: int | None, current: int | None,
) -> int | None:
    """The probability a lane should carry after the repair.

    D-CRM-10 pins won-type lanes to 100 and lost-type to 0 — the same rule the
    admin API now refuses to break — so the repair applies it rather than
    creating rows its own settings grid would reject. Everything else takes the
    probe's value when there is one and is left exactly as it was when there is
    not: f2 is the editor, and inventing a prior is worse than admitting none.
    """
    if type_after == "won":
        return 100
    if type_after == "lost":
        return 0
    if probed is not None:
        return max(0, min(100, probed))
    return current


def plan_repair(
    rows: list[Any], stages: list[TenantStage], probabilities: dict[str, int],
) -> RepairPlan:
    """What the repair would do — computed once, reported and then applied."""
    plan = RepairPlan()
    by_name = {normalize(str(r.name)): r for r in rows}
    seen: set[str] = set()

    for stage in stages:
        key = normalize(stage.name)
        seen.add(key)
        existing = by_name.get(key)
        position_after = stage.sequence_number * 10
        type_before = _text_of(getattr(existing, "type", "")) or None
        type_after = resolve_type(stage.forecast_type, type_before)
        probability_before = getattr(existing, "probability", None)
        probability_after = resolve_probability(
            type_after, probabilities.get(key),
            probability_before if existing is not None else 0,
        )

        if existing is None:
            plan.creates.append({
                "name": stage.name,
                "position": position_after,
                "type": type_after,
                "probability": probability_after if probability_after is not None else 0,
            })
            plan.stages.append(StageChange(
                name=stage.name, action="created", changed=True,
                position_after=position_after, type_after=type_after,
                probability_after=probability_after,
            ))
            continue

        values: dict[str, Any] = {}
        if int(getattr(existing, "position", 0) or 0) != position_after:
            values["position"] = position_after
        if type_before != type_after:
            values["type"] = type_after
        if probability_after is not None and probability_before != probability_after:
            values["probability"] = probability_after
        if values:
            plan.updates[str(existing.id)] = values
        plan.types_after[str(existing.id)] = type_after
        plan.stages.append(StageChange(
            name=str(existing.name), action="matched", changed=bool(values),
            position_before=int(getattr(existing, "position", 0) or 0),
            position_after=position_after,
            type_before=type_before, type_after=type_after,
            probability_before=probability_before,
            probability_after=probability_after,
        ))

    for key, row in by_name.items():
        if key not in seen:
            plan.orphans.append((key, row))
            plan.types_after[str(row.id)] = _text_of(getattr(row, "type", "open"))
    return plan


# ── Reading the tenant ──────────────────────────────────────────────────────

@dataclass
class Metadata:
    """What one round trip to the tenant produced, refusals included."""

    layout: dict[str, Any] | None = None
    layouts_seen: int = 0
    pipelines: list[dict[str, Any]] = field(default_factory=list)
    probabilities: dict[str, int] = field(default_factory=dict)
    #: Starts at ``unavailable`` — "we never got far enough to look" — and is
    #: overwritten ONLY by a path that actually held the layout. Defaulting it
    #: to ``no_data`` made a refused layout read answer "Zoho carries no
    #: per-stage probability for this layout", a statement about a layout
    #: nobody had, pointing the owner at the settings grid when the thing to
    #: change was the API version constant.
    probe: ProbabilityProbe = field(
        default_factory=lambda: ProbabilityProbe(outcome="unavailable"),
    )
    missing_scope: str | None = None
    errors: list[str] = field(default_factory=list)


async def fetch_metadata(zoho: Any) -> Metadata:
    """Layouts, then pipelines. Every refusal becomes a reported outcome.

    Deliberately not a retry ladder: an API version the tenant refuses is
    reported once, with the version named, rather than walked downward until
    something answers — the endpoint that answers on an older version is a
    different endpoint with different fields.
    """
    from ingestion.sources.zoho.client import (
        LAYOUTS_SCOPE,
        PIPELINE_SCOPE,
        ZohoApiVersionError,
        ZohoScopeError,
    )

    meta = Metadata()
    try:
        layouts = await zoho.list_deal_layouts()
    except ZohoScopeError as exc:
        meta.missing_scope = exc.scope or LAYOUTS_SCOPE
        meta.probe = ProbabilityProbe(
            outcome="no_scope", scope=meta.missing_scope, detail=exc.detail or None,
        )
        meta.errors.append(f"layout metadata unavailable: {exc}")
        return meta
    except ZohoApiVersionError as exc:
        # The probe stays `unavailable`: nothing was read, so "this layout
        # carries no probability" would be a claim about a layout we never saw.
        meta.probe = ProbabilityProbe(outcome="unavailable", detail=str(exc)[:200])
        meta.errors.append(f"layout metadata unavailable: {exc}")
        return meta

    meta.layouts_seen = len(layouts)
    meta.layout = pick_deals_layout(layouts)
    if meta.layout is None:
        meta.probe = ProbabilityProbe(
            outcome="unavailable",
            detail="Zoho returned no Deals layout to read the Stage picklist from.",
        )
        meta.errors.append("Zoho returned no Deals layout to read a pipeline from.")
        return meta

    meta.probabilities = stage_probabilities(meta.layout)
    meta.probe = (
        ProbabilityProbe(outcome="read", values=len(meta.probabilities))
        if meta.probabilities
        else ProbabilityProbe(
            outcome="no_data",
            detail=(
                "the Deals layout carries no per-stage probability — set them "
                "in the pipeline settings tab"
            ),
        )
    )

    try:
        meta.pipelines = await zoho.list_deal_pipelines(_text_of(meta.layout.get("id")))
    except ZohoScopeError as exc:
        meta.missing_scope = exc.scope or PIPELINE_SCOPE
        meta.errors.append(f"pipeline metadata unavailable: {exc}")
    except ZohoApiVersionError as exc:
        meta.errors.append(f"pipeline metadata unavailable: {exc}")
    return meta


# ── Applying ────────────────────────────────────────────────────────────────

async def apply_repair(db: Any, plan: RepairPlan) -> None:
    """Write the plan. Statuses only — not one deal row is named here."""
    for status_id, values in plan.updates.items():
        # `touch=False` like every other statuses write: `crm_deal_statuses`
        # has no `updated_at`, and it is absent from ZOHO_TRACKED_TABLES, so
        # this cannot dirty anything even if it wanted to.
        await update_row(db, STATUS_TABLE, status_id, values, touch=False)
    for values in plan.creates:
        row = await insert_row(db, STATUS_TABLE, values)
        plan.types_after[str(row.id)] = str(values["type"])


async def backfill_closed_at(
    db: Any, types_after: dict[str, str], *, apply: bool,
) -> ClosedAtBackfill:
    """f4 — imported won/lost deals adopt ``expected_close_date`` as a proxy.

    The importer never stamps ``closed_at`` (only native transitions do), so
    every imported won/lost deal is invisible to win/loss, cycle-time and won-₹
    math. Zoho's ``Closing_Date`` is the only date the tenant gave us, so it is
    adopted as a **labeled** proxy and rows where that is also NULL stay NULL
    and are counted.

    ⚠️ The write is :data:`CLOSED_AT_BACKFILL_SQL` executed directly. It must
    NOT travel through ``core.update_row``: that path calls
    ``mark_dirty_on_update``, and dirtying 500+ imported deals would queue that
    many no-op pushes into the live Zoho tenant on the next sync cycle.
    """
    report = ClosedAtBackfill()
    for status_id, status_type in sorted(types_after.items()):
        if status_type not in CLOSING_TYPES:
            continue
        params = {"status_id": status_id}
        report.missing_close_date += int(
            (await db.execute(text(CLOSED_AT_UNDATED_SQL), params)).scalar() or 0
        )
        if not apply:
            report.eligible += int(
                (await db.execute(text(CLOSED_AT_ELIGIBLE_SQL), params)).scalar() or 0
            )
            continue
        stamped = (await db.execute(text(CLOSED_AT_BACKFILL_SQL), params)).fetchall()
        report.eligible += len(stamped)
        report.stamped += len(stamped)
    return report


async def count_deals_in(db: Any, status_id: str) -> int:
    return int((await db.execute(
        text(
            "SELECT count(*) FROM crm_deals "
            "WHERE status_id = CAST(:status_id AS uuid)"
        ),
        {"status_id": status_id},
    )).scalar() or 0)


# ── The endpoint ────────────────────────────────────────────────────────────

@router.post(
    "/import/zoho/stages",
    response_model=StageMetadataReport,
    dependencies=[require_permission("admin:access:manage")],
)
async def import_zoho_stages(
    apply: bool = Query(
        False,
        description=(
            "Write the repair. Absent or false runs a dry run that issues no "
            "writes at all. Running this against the production tenant is "
            "OWNER-GATE (work_plan.md §6)."
        ),
    ),
    user: UserContext = Depends(get_current_user),
) -> StageMetadataReport:
    """Repair the deal pipeline from the tenant's own metadata (§5.1 system 1).

    Same floor as the record importer — ``admin:access:manage``, never
    ``integrations:use:zoho-crm``, because migration 131 grants every member
    the whole ``integrations:use:*`` family and the slug would gate nothing.
    This rewrites the pipeline rather than a record, which is the same class of
    act for the same reason.
    """
    who = actor(user)
    # Mirror mode: Zoho owns the stages. Refuse BEFORE the Zoho call, so a
    # Mirror org sends nothing through the global legacy credential. It also
    # covers the closed_at backfill below.
    async with _tenant_session() as db:
        await refuse_in_mirror_mode(
            db, "mirror_mode_settings",
            "The stages come from Zoho. Change them in Zoho.",
        )
    meta = await fetch_metadata(import_zoho._client())

    if meta.layout is None or not meta.pipelines:
        return _unavailable(meta)

    names = [pipeline_name(p) for p in meta.pipelines]
    if len(meta.pipelines) > 1:
        # D-CRM-11 goes back to the owner BEFORE anything is written: a
        # name-match repair against the wrong pipeline scrambles the board it
        # was meant to fix. Returning here means the database is never opened,
        # which is also what makes "the stop writes nothing" cheap to pin.
        _log.warning("crm.stages.multiple_pipelines", pipelines=names, actor=who)
        return StageMetadataReport(
            outcome="stopped",
            layout_id=_text_of(meta.layout.get("id")) or None,
            layout_name=_text_of(meta.layout.get("name")) or None,
            pipelines=names,
            stop_reason=(
                f"This layout has {len(names)} pipelines. The native CRM models "
                "one pipeline (D-CRM-11) and a name-match repair against the "
                "wrong one would scramble the board — nothing was written. "
                "Adding a second pipeline is an owner decision, not a repair."
            ),
            probability=meta.probe,
            missing_scope=meta.missing_scope,
            errors=meta.errors,
        )

    report = StageMetadataReport(
        outcome="applied" if apply else "dry_run",
        applied=apply,
        layout_id=_text_of(meta.layout.get("id")) or None,
        layout_name=_text_of(meta.layout.get("name")) or None,
        pipelines=names,
        probability=meta.probe,
        missing_scope=meta.missing_scope,
        errors=list(meta.errors),
    )
    if meta.layouts_seen > 1:
        report.notes.append(
            f"{meta.layouts_seen} Deals layouts exist; read the first "
            f"({report.layout_name or report.layout_id})."
        )

    async with _tenant_session() as db:
        rows = (await db.execute(
            text(f"SELECT * FROM {STATUS_TABLE} ORDER BY position, name"), {},
        )).fetchall()
        plan = plan_repair(rows, pipeline_stages(meta.pipelines[0]), meta.probabilities)
        report.stages = plan.stages
        report.changed = sum(1 for change in plan.stages if change.changed)
        for _key, row in plan.orphans:
            report.orphans.append(OrphanLane(
                name=str(row.name),
                position=int(getattr(row, "position", 0) or 0),
                deals=await count_deals_in(db, str(row.id)),
            ))

        if apply:
            await apply_repair(db, plan)
        # This is the ONLY place `closed_at` is set, and that is the point: the
        # stop and the unavailable branches leave it None, so the report can
        # never print a count for a computation that did not happen.
        backfill = await backfill_closed_at(db, plan.types_after, apply=apply)
        report.closed_at = backfill

    _log.info(
        "crm.stages.repair",
        applied=apply, actor=who, changed=report.changed,
        orphans=len(report.orphans), closed_at=backfill.stamped,
        probability=report.probability.outcome,
    )
    return report


def _unavailable(meta: Metadata) -> StageMetadataReport:
    """The tenant did not answer — reported, and nothing is written."""
    return StageMetadataReport(
        outcome="unavailable",
        layout_id=_text_of((meta.layout or {}).get("id")) or None,
        layout_name=_text_of((meta.layout or {}).get("name")) or None,
        probability=meta.probe,
        missing_scope=meta.missing_scope,
        errors=meta.errors or [
            "Zoho returned no pipeline for the Deals layout — set the stages "
            "by hand in the pipeline settings tab."
        ],
    )


__all__ = [
    "CLOSED_AT_BACKFILL_SQL",
    "FORECAST_TYPES",
    "STATUS_TABLE",
    "ClosedAtBackfill",
    "OrphanLane",
    "ProbabilityProbe",
    "RepairPlan",
    "StageChange",
    "StageMetadataReport",
    "TenantStage",
    "backfill_closed_at",
    "fetch_metadata",
    "import_zoho_stages",
    "normalize",
    "pipeline_name",
    "pipeline_stages",
    "plan_repair",
    "resolve_probability",
    "resolve_type",
    "stage_probabilities",
]
