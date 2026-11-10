"""CRM · the stage-metadata repair — WS-26f f1 + f4.

Spec: ``project-docs/specs/crm_app.md`` §5.1 (system 1) · ticket WS-26f
done-when 1-4 · decisions D-CRM-10 and D-CRM-11.

Hermetic: no Postgres, no network, no Zoho. The route function is called
directly with ``core._get_db`` monkeypatched onto each SUT submodule (the
``_crm_fakes`` convention) and ``import_zoho._client`` bound to
:class:`FakeTenant` — ONE seam, shared with the record importer, which is why
this file binds the same attribute ``test_crm_zoho_import.py`` does.

⚠️ **The dirty-bypass is pinned STATICALLY, against the statement's own text.**
``_crm_fakes.FakeCrmDB`` re-implements clauses in Python, so a behavioural
"nothing was dirtied" case there can only ever be the mirror agreeing with
itself about a column it never wrote. The claim that matters — f4's UPDATE
never travels through ``core.update_row`` and therefore never reaches
``mark_dirty_on_update`` — is a property of the SQL and of the module's call
graph, and is asserted against both. The behavioural cases below say what the
mirror CAN see: which rows changed, and which columns on them.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import import_zoho as crm_import
from gateway.routes.crm import records as crm_records
from gateway.routes.crm import stage_metadata as crm_stages
from ingestion.sources.zoho.client import (
    LAYOUTS_SCOPE,
    PIPELINE_SCOPE,
    ZohoApiVersionError,
    ZohoScopeError,
)

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()

CRM_MODULES = (crm_core, crm_records, crm_admin, crm_import, crm_stages)

WRITE_VERBS = ("INSERT", "UPDATE", "DELETE")


# ── The fake tenant ─────────────────────────────────────────────────────────

def stage(
    name: str, sequence: int, forecast: str = "Open", **over: Any,
) -> dict[str, Any]:
    """One entry of a Zoho pipeline's ``maps`` array."""
    return {
        "display_value": name,
        "actual_value": name,
        "sequence_number": sequence,
        "forecast_type": forecast,
        **over,
    }


def pipeline(name: str, *stages: dict[str, Any]) -> dict[str, Any]:
    return {"display_value": name, "actual_value": name, "maps": list(stages)}


def layout(
    layout_id: str = "lay-1", name: str = "Standard",
    picklist: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A Deals layout, optionally carrying the Stage field's picklist."""
    return {
        "id": layout_id,
        "name": name,
        "sections": [{
            "name": "Deal Information",
            "fields": [
                {"api_name": "Deal_Name"},
                {"api_name": "Stage", "pick_list_values": picklist or []},
            ],
        }],
    }


class FakeTenant:
    """The two settings readers ``stage_metadata`` calls, and nothing else.

    Shaped as the client's PUBLIC surface on purpose: if the route ever reaches
    past ``list_deal_layouts`` / ``list_deal_pipelines`` into the private
    ``_settings_json``, this fake stops answering and the test fails rather
    than silently exercising a different path.
    """

    def __init__(self) -> None:
        self.layouts: list[dict[str, Any]] = [layout()]
        self.pipelines: list[dict[str, Any]] = []
        self.layout_error: Exception | None = None
        self.pipeline_error: Exception | None = None
        #: Every read, in order — so a test can prove a refusal STOPPED the
        #: pull rather than being logged and walked past.
        self.reads: list[str] = []

    async def list_deal_layouts(self) -> list[dict[str, Any]]:
        self.reads.append("layouts")
        if self.layout_error is not None:
            raise self.layout_error
        return list(self.layouts)

    async def list_deal_pipelines(self, layout_id: str) -> list[dict[str, Any]]:
        self.reads.append(f"pipeline:{layout_id}")
        if self.pipeline_error is not None:
            raise self.pipeline_error
        return list(self.pipelines)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, CRM_MODULES)
    return fake


@pytest.fixture
def tenant(monkeypatch: pytest.MonkeyPatch) -> FakeTenant:
    fake = FakeTenant()
    # The ONE seam — `stage_metadata` reaches Zoho through `import_zoho`'s
    # client function rather than opening a second one of its own.
    monkeypatch.setattr(crm_import, "_client", lambda: fake)
    return fake


def seed_native_pipeline(db: FakeCrmDB) -> dict[str, Any]:
    """144's seed lanes: renamed variants of Zoho's defaults, which is exactly
    why name-match missed the tenant's real stages (§5.1's root cause)."""
    lanes = {
        "Qualification": ("open", 10, 10, True),
        "Proposal": ("ongoing", 30, 50, False),
        "Closed Won": ("won", 50, 100, False),
        "Closed Lost": ("lost", 60, 0, False),
    }
    return {
        name: db.seed(
            "crm_deal_statuses", name=name, type=kind, position=position,
            probability=probability, is_default=default,
        )
        for name, (kind, position, probability, default) in lanes.items()
    }


def writes(db: FakeCrmDB) -> list[str]:
    return [
        s for s in db.statements
        if s.split(None, 1)[0].upper() in WRITE_VERBS
    ]


async def run(apply: bool = False) -> Any:
    return await crm_stages.import_zoho_stages(apply, USER)


# ── The permission floor ────────────────────────────────────────────────────

def _route(path: str) -> Any:
    from gateway.routes.crm.core import router

    return next(r for r in router.routes if getattr(r, "path", None) == path)


def permission_checks(path: str) -> list[Any]:
    return [
        d.dependency for d in _route(path).dependencies
        if "require_permission" in getattr(d.dependency, "__qualname__", "")
    ]


async def test_the_repair_floor_is_the_admin_capability() -> None:
    """f1 — "same floor as the record import, same reasoning". Migration 131
    grants every member ``integrations:use:*``, so the integration slug would
    gate nothing, and this rewrites the PIPELINE rather than a record."""
    checks = permission_checks("/crm/import/zoho/stages")
    assert checks, "the stage repair carries no permission dependency at all"

    integration_holder = crm_user(features="integrations:use:*")
    for check in checks:
        with pytest.raises(HTTPException) as exc:
            await check(user=integration_holder)
        assert exc.value.status_code == 403
        assert "admin:access:manage" in str(exc.value.detail)


async def test_an_admin_capability_holder_passes_the_floor() -> None:
    admin = crm_user(features="admin:access:manage")
    checks = permission_checks("/crm/import/zoho/stages")
    assert checks
    for check in checks:
        assert await check(user=admin) is admin


# ── done-when 1 · dry-run writes nothing ────────────────────────────────────

async def test_dry_run_writes_nothing_at_all(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """Not "no rows" — no WRITE STATEMENTS and no commit. A dry run that
    renumbered one lane has already changed the thing it was asked to preview.
    """
    seed_native_pipeline(db)
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Qualification", 1),
        stage("Proposal/Price Quote", 2),
        stage("Closed Won", 5, "Closed Won"),
    )]

    report = await run(apply=False)

    assert report.outcome == "dry_run"
    assert report.applied is False
    # H2: `tenant_session` commits an empty transaction on clean exit, so the
    # honest "a dry run writes nothing" assertion is the statement list.
    assert writes(db) == []
    # …and it still says what it would do.
    assert report.changed > 0
    assert {s.name for s in report.stages} == {
        "Qualification", "Proposal/Price Quote", "Closed Won",
    }


async def test_dry_run_reports_position_type_and_probability_before_and_after(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """The report is the thing an owner reads before approving the apply, so
    every column the repair would move is in it, both sides."""
    seed_native_pipeline(db)
    tenant.layouts = [layout(picklist=[
        {"display_value": "Qualification", "probability": 20},
    ])]
    tenant.pipelines = [pipeline("Standard", stage("Qualification", 4))]

    report = await run(apply=False)
    qualification = next(s for s in report.stages if s.name == "Qualification")

    assert qualification.action == "matched"
    assert (qualification.position_before, qualification.position_after) == (10, 40)
    assert (qualification.type_before, qualification.type_after) == ("open", "open")
    assert (
        qualification.probability_before, qualification.probability_after,
    ) == (10, 20)
    assert qualification.changed is True


async def test_an_unmatched_native_lane_is_reported_with_its_deal_count(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """"native-only lane → reported, never deleted". The deal count travels
    with it because ``ON DELETE RESTRICT`` 409s a lane still holding deals, so
    "delete this" is not always even an offer the settings tab can make."""
    lanes = seed_native_pipeline(db)
    db.seed("crm_deals", name="Bosch printer", status_id=lanes["Proposal"].id)
    tenant.pipelines = [pipeline("Standard", stage("Qualification", 1))]

    report = await run(apply=True)

    orphans = {o.name: o for o in report.orphans}
    assert orphans["Proposal"].deals == 1
    assert orphans["Closed Won"].deals == 0
    assert not db.statements_touching("DELETE FROM crm_deal_statuses")


# ── done-when 1 · the D-CRM-11 stop ─────────────────────────────────────────

@pytest.mark.parametrize("apply", [False, True])
async def test_more_than_one_pipeline_stops_before_anything_is_written(
    db: FakeCrmDB, tenant: FakeTenant, apply: bool,
) -> None:
    """D-CRM-11 goes back to the owner BEFORE the repair, applied or not: a
    name-match repair against the wrong pipeline scrambles the board it was
    meant to fix. Parametrized over ``apply`` because "stops the dry run" is
    the easy half — the half that matters is that ``?apply=true`` stops too."""
    seed_native_pipeline(db)
    tenant.pipelines = [
        pipeline("Standard", stage("Qualification", 1)),
        pipeline("Renewals", stage("Renewal Due", 1)),
    ]

    report = await run(apply=apply)

    assert report.outcome == "stopped"
    assert report.applied is False
    assert report.pipelines == ["Standard", "Renewals"]
    assert "D-CRM-11" in (report.stop_reason or "")
    # Not merely "no writes": the database is never opened, so there is no
    # window in which a half-applied repair could exist. The one short session
    # is the Mirror-mode check (CRM-Z6): a single read of crm_settings.
    assert len(db.statements) == 1
    assert db.statements[0].startswith("SELECT mode FROM crm_settings")
    assert report.stages == []


@pytest.mark.parametrize(
    "outcome",
    ["stopped", "unavailable"],
)
async def test_a_run_that_never_opened_the_database_reports_no_close_dates(
    db: FakeCrmDB, tenant: FakeTenant, outcome: str,
) -> None:
    """``closed_at`` is **absent**, not zeroed, when the backfill never ran.

    The model's default used to be ``ClosedAtBackfill()``, so a stop and a
    scope refusal both answered "0 stamped, 0 missing a close date" — a
    MEASUREMENT this endpoint never took, printed next to a banner saying
    nothing was read. "0 close dates missing" reads as reassurance about data
    nobody looked at, which is the exact species of quiet lie f4 exists to
    avoid on the deal rows themselves.
    """
    seed_native_pipeline(db)
    db.seed(
        "crm_deals", name="Won deal", status_id=next(
            r["id"] for r in db.rows("crm_deal_statuses") if r["name"] == "Closed Won"
        ),
        closed_at=None, expected_close_date=None,
    )
    if outcome == "stopped":
        tenant.pipelines = [
            pipeline("Standard", stage("Qualification", 1)),
            pipeline("Renewals", stage("Renewal Due", 1)),
        ]
    else:
        tenant.layout_error = ZohoScopeError(LAYOUTS_SCOPE, "invalid oauth scope")

    report = await run(apply=True)

    assert report.outcome == outcome
    assert report.closed_at is None
    assert writes(db) == []


@pytest.mark.parametrize("apply", [False, True])
async def test_a_run_that_did_open_the_database_always_reports_a_section(
    db: FakeCrmDB, tenant: FakeTenant, apply: bool,
) -> None:
    """The other half of the sentinel: absent means "not evaluated", so a real
    run must never leave it absent — including one that found nothing to do."""
    seed_native_pipeline(db)
    tenant.pipelines = [pipeline("Standard", stage("Qualification", 1))]

    report = await run(apply=apply)

    assert report.closed_at is not None
    assert report.closed_at.stamped == 0


# ── done-when 2 · idempotency ───────────────────────────────────────────────

async def test_apply_renumbers_retypes_and_creates_the_missing_lanes(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    lanes = seed_native_pipeline(db)
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Qualification", 1),
        stage("Proposal/Price Quote", 2),
        stage("Negotiation/Review", 3),
        stage("Closed Won", 4, "Closed Won"),
        stage("Closed Lost", 5, "Closed Lost"),
    )]

    report = await run(apply=True)

    assert report.outcome == "applied"
    assert db.committed == 2  # the Mirror-mode check, then the repair
    by_name = {row["name"]: row for row in db.rows("crm_deal_statuses")}
    assert by_name["Qualification"]["position"] == 10
    assert by_name["Closed Won"]["position"] == 40
    assert by_name["Closed Lost"]["position"] == 50
    # The tenant's real stages arrive in sequence rather than appended at the
    # end in first-encounter order, which is the whole defect (§5.1).
    assert by_name["Proposal/Price Quote"]["position"] == 20
    assert by_name["Negotiation/Review"]["position"] == 30
    # …and 144's renamed seeds are left standing, reported as orphans.
    assert {o.name for o in report.orphans} == {"Proposal"}
    assert str(lanes["Proposal"].id) in {
        str(r["id"]) for r in db.rows("crm_deal_statuses")
    }


async def test_a_second_apply_reports_zero_changes(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """done-when 2, measured rather than asserted: a repair that keeps finding
    work to do is a repair nobody can tell apart from a broken one."""
    seed_native_pipeline(db)
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Qualification", 1),
        stage("Proposal/Price Quote", 2),
        stage("Closed Won", 3, "Closed Won"),
    )]

    first = await run(apply=True)
    assert first.changed > 0

    before = len(db.statements)
    second = await run(apply=True)

    assert second.changed == 0
    assert second.closed_at.stamped == 0
    # Not one status row is rewritten. The backfill statement is still ISSUED
    # (its predicate is what decides, and re-deciding it costs one indexed
    # scan) — but it matches nothing, which is the honest reading of "the
    # second run reports zero changes".
    assert [
        s for s in db.statements[before:]
        if s.split(None, 1)[0].upper() in ("INSERT", "UPDATE")
        and "crm_deal_statuses" in s
    ] == []


# ── done-when 3 · no-scope vs no-data ───────────────────────────────────────

async def test_a_missing_scope_is_reported_and_names_the_scope_string(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """The existing token was not minted with ``settings.*``, so this is the
    EXPECTED first outcome — and re-minting it is the owner's act, which is
    only actionable if the response prints the exact string to add."""
    seed_native_pipeline(db)
    tenant.layout_error = ZohoScopeError(LAYOUTS_SCOPE, "invalid oauth scope")

    report = await run(apply=True)

    assert report.outcome == "unavailable"
    assert report.missing_scope == "ZohoCRM.settings.layouts.READ"
    assert report.probability.outcome == "no_scope"
    assert report.probability.scope == "ZohoCRM.settings.layouts.READ"
    assert writes(db) == []
    # A refusal STOPS the pull; it is not logged and walked past.
    assert tenant.reads == ["layouts"]


async def test_the_pipeline_scope_is_named_separately_from_the_layout_scope(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """Two reads, two scopes. Naming the layout scope when the pipeline read is
    the one that was refused sends the owner to change the wrong thing."""
    seed_native_pipeline(db)
    tenant.pipeline_error = ZohoScopeError(PIPELINE_SCOPE, "invalid oauth scope")

    report = await run(apply=True)

    assert report.missing_scope == "ZohoCRM.settings.pipeline.READ"
    assert writes(db) == []


async def test_no_data_is_a_different_answer_from_no_scope(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """"metadata present, field absent" means "set them by hand in the settings
    tab", not "re-mint the token". Collapsing the two into one "unavailable"
    is the failure the probe field exists to prevent."""
    seed_native_pipeline(db)
    tenant.layouts = [layout(picklist=[{"display_value": "Qualification"}])]
    tenant.pipelines = [pipeline("Standard", stage("Qualification", 2))]

    report = await run(apply=True)

    assert report.probability.outcome == "no_data"
    assert report.missing_scope is None
    # The rest of the repair still lands: f2 is the probability editor, and a
    # board in the right ORDER is most of what f1 was for.
    assert report.outcome == "applied"
    matched = next(s for s in report.stages if s.name == "Qualification")
    assert matched.position_after == 20
    assert matched.probability_after == 10  # untouched, not invented


async def test_a_probability_that_was_read_is_reported_as_read(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    seed_native_pipeline(db)
    tenant.layouts = [layout(picklist=[
        {"display_value": "Qualification", "probability": 15},
    ])]
    tenant.pipelines = [pipeline("Standard", stage("Qualification", 1))]

    report = await run(apply=False)

    assert report.probability.outcome == "read"
    assert report.probability.values == 1


async def test_a_refused_layout_read_never_claims_the_probability_was_absent(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """The fourth outcome, and the one a default was quietly answering for.

    ``no_data`` is a statement ABOUT a layout — "Zoho carries none for this
    one, set them by hand". When the layout read itself is refused there is no
    layout to say it about, and saying it anyway points the owner at the
    settings grid when the thing to change is the API version constant.
    """
    seed_native_pipeline(db)
    tenant.layout_error = ZohoApiVersionError("v8", "settings/layouts", "nope")

    report = await run(apply=True)

    assert report.probability.outcome == "unavailable"
    assert report.probability.outcome != "no_data"
    assert "v8" in (report.probability.detail or "")
    assert report.missing_scope is None
    assert writes(db) == []


async def test_a_tenant_with_no_deals_layout_is_also_unavailable_not_no_data(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    seed_native_pipeline(db)
    tenant.layouts = []

    report = await run(apply=True)

    assert report.outcome == "unavailable"
    assert report.probability.outcome == "unavailable"
    assert writes(db) == []


async def test_a_refused_api_version_is_reported_not_walked_downward(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """C2: the settings endpoints live on a newer API version than the record
    readers. A tenant that refuses it produces ONE reported outcome — the
    endpoint that answers on an older version is a different endpoint."""
    seed_native_pipeline(db)
    tenant.pipeline_error = ZohoApiVersionError("v8", "settings/pipeline", "no")

    report = await run(apply=True)

    assert report.outcome == "unavailable"
    assert any("v8" in e for e in report.errors)
    assert report.missing_scope is None
    assert writes(db) == []
    assert tenant.reads == ["layouts", "pipeline:lay-1"]


# ── done-when 4 · f4, and what it must not touch ────────────────────────────

async def test_the_closed_at_proxy_stamps_only_won_and_lost_rows(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """The importer never stamps ``closed_at`` (only native transitions do), so
    every imported won/lost deal is invisible to win/loss and cycle-time math.
    Zoho's ``Closing_Date`` is the only date the tenant gave us."""
    lanes = seed_native_pipeline(db)
    won = db.seed(
        "crm_deals", name="Won deal", status_id=lanes["Closed Won"].id,
        closed_at=None, expected_close_date="2026-03-31",
    )
    open_deal = db.seed(
        "crm_deals", name="Live deal", status_id=lanes["Qualification"].id,
        closed_at=None, expected_close_date="2026-09-30",
    )
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Qualification", 1),
        stage("Closed Won", 2, "Closed Won"),
    )]

    report = await run(apply=True)

    rows = {str(r["id"]): r for r in db.rows("crm_deals")}
    assert rows[str(won.id)]["closed_at"] == "2026-03-31"
    # An OPEN deal has an expected close date too, and it is a forecast rather
    # than an event: stamping it would invent a close that never happened.
    assert rows[str(open_deal.id)]["closed_at"] is None
    assert report.closed_at.stamped == 1


async def test_a_won_row_with_no_close_date_is_counted_not_invented(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """"NULL-date rows counted in the report, not invented" — "we do not know
    when this closed" and "it closed today" are different facts."""
    lanes = seed_native_pipeline(db)
    undated = db.seed(
        "crm_deals", name="Old win", status_id=lanes["Closed Won"].id,
        closed_at=None, expected_close_date=None,
    )
    tenant.pipelines = [pipeline("Standard", stage("Closed Won", 1, "Closed Won"))]

    report = await run(apply=True)

    assert report.closed_at.missing_close_date == 1
    assert report.closed_at.stamped == 0
    assert {str(r["id"]): r for r in db.rows("crm_deals")}[
        str(undated.id)
    ]["closed_at"] is None


async def test_an_already_stamped_close_is_never_overwritten(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """A native transition stamped a REAL time; the proxy is a fallback for
    rows that have none, not a correction of the ones that do."""
    lanes = seed_native_pipeline(db)
    native = db.seed(
        "crm_deals", name="Closed here", status_id=lanes["Closed Won"].id,
        closed_at="2026-01-05T09:00:00+00:00", expected_close_date="2026-03-31",
    )
    tenant.pipelines = [pipeline("Standard", stage("Closed Won", 1, "Closed Won"))]

    report = await run(apply=True)

    assert report.closed_at.stamped == 0
    assert {str(r["id"]): r for r in db.rows("crm_deals")}[
        str(native.id)
    ]["closed_at"] == "2026-01-05T09:00:00+00:00"


async def test_a_lane_retyped_to_won_in_the_same_apply_backfills_its_deals(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """f4 reads the type the repair is LEAVING on the lane, not the one it
    found: a stage the tenant calls Closed Won and we had typed open would
    otherwise need a second run to become countable."""
    lanes = seed_native_pipeline(db)
    db.seed(
        "crm_deal_statuses", name="Order Received", type="open", position=70,
        probability=0,
    )
    order_received = next(
        r for r in db.rows("crm_deal_statuses") if r["name"] == "Order Received"
    )
    deal = db.seed(
        "crm_deals", name="Retyped", status_id=order_received["id"],
        closed_at=None, expected_close_date="2026-02-14",
    )
    assert lanes["Qualification"] is not None
    tenant.pipelines = [pipeline(
        "Standard", stage("Order Received", 1, "Closed Won"),
    )]

    report = await run(apply=True)

    assert report.closed_at.stamped == 1
    assert {str(r["id"]): r for r in db.rows("crm_deals")}[
        str(deal.id)
    ]["closed_at"] == "2026-02-14"


async def test_the_dry_run_counts_the_backfill_without_performing_it(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    lanes = seed_native_pipeline(db)
    deal = db.seed(
        "crm_deals", name="Won deal", status_id=lanes["Closed Won"].id,
        closed_at=None, expected_close_date="2026-03-31",
    )
    tenant.pipelines = [pipeline("Standard", stage("Closed Won", 1, "Closed Won"))]

    report = await run(apply=False)

    assert report.closed_at.eligible == 1
    assert report.closed_at.stamped == 0
    assert {str(r["id"]): r for r in db.rows("crm_deals")}[
        str(deal.id)
    ]["closed_at"] is None
    assert writes(db) == []


async def test_the_only_deal_row_change_is_closed_at_and_nothing_is_dirtied(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """done-when 4 in one case: after an apply, every deal row is byte-identical
    except for ``closed_at``, and no row is ``zoho_dirty`` that was not already.

    ``zoho_dirty`` is what the sync's push phase selects on, so a repair that
    dirtied 551 imported deals would queue 551 no-op writes into the live Zoho
    tenant on the next cycle — against a tenant nothing has ever written."""
    lanes = seed_native_pipeline(db)
    db.seed(
        "crm_deals", name="Won deal", status_id=lanes["Closed Won"].id,
        closed_at=None, expected_close_date="2026-03-31",
    )
    db.seed(
        "crm_deals", name="Live deal", status_id=lanes["Proposal"].id,
        closed_at=None, expected_close_date="2026-09-30", amount=100_000.0,
    )
    # One row that WAS already dirty — the invariant is "nothing that wasn't",
    # not "nothing is".
    db.seed(
        "crm_deals", name="Edited here", status_id=lanes["Proposal"].id,
        closed_at=None, zoho_dirty=True,
    )
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Proposal", 1),
        stage("Closed Won", 2, "Closed Won"),
    )]
    before = {str(r["id"]): dict(r) for r in db.rows("crm_deals")}

    await run(apply=True)

    after = {str(r["id"]): dict(r) for r in db.rows("crm_deals")}
    assert set(after) == set(before)
    for deal_id, row in after.items():
        changed = {
            column for column, value in row.items()
            if before[deal_id].get(column) != value
        }
        assert changed <= {"closed_at"}, (deal_id, changed)
        assert row["zoho_dirty"] == before[deal_id]["zoho_dirty"]


def test_the_backfill_statement_names_closed_at_and_not_the_dirty_column() -> None:
    """Static, because the fake writes only the columns a SET clause names and
    would therefore agree that nothing was dirtied even if the statement said
    ``zoho_dirty = true``. The safety property IS the statement text."""
    sql = crm_stages.CLOSED_AT_BACKFILL_SQL

    assert sql.startswith("UPDATE crm_deals SET closed_at = expected_close_date")
    assert "zoho_dirty" not in sql
    assert "updated_at" not in sql
    # It must also not widen: only rows with nothing stamped and a date to
    # adopt, scoped to one lane at a time.
    assert "closed_at IS NULL" in sql
    assert "expected_close_date IS NOT NULL" in sql
    assert "status_id = CAST(:status_id AS uuid)" in sql


def test_the_backfill_never_goes_through_the_dirty_marking_write_path() -> None:
    """The other half, and the one a reviewer cannot eyeball: ``update_row``
    calls ``mark_dirty_on_update``, so a "tidy-up" that routed 500+ imported
    deals through it would queue that many pushes. This module's only
    ``update_row`` caller is the STATUSES repair, which is not a tracked table.
    """
    source = Path(crm_stages.__file__).read_text(encoding="utf-8")
    callers = [
        name for name, value in vars(crm_stages).items()
        if inspect.isfunction(value)
        and value.__module__ == crm_stages.__name__
        and "update_row(" in inspect.getsource(value)
    ]
    assert callers == ["apply_repair"], callers
    assert "update_row(db, STATUS_TABLE" in source

    backfill = inspect.getsource(crm_stages.backfill_closed_at)
    for forbidden in ("update_row(", "insert_row(", "upsert_by_zoho_id("):
        assert forbidden not in backfill, forbidden


def test_the_statuses_tables_are_not_zoho_tracked() -> None:
    """"the repair queues zero pushes" is structural, not behavioural: the
    vocabulary flows DOWN only, so no statuses table is dirty-tracked at all."""
    assert "crm_deal_statuses" not in crm_core.ZOHO_TRACKED_TABLES
    assert "crm_lead_statuses" not in crm_core.ZOHO_TRACKED_TABLES


async def test_the_repair_writes_no_deal_row_other_than_the_backfill(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """f1 point 5 — "no deal row is touched". Every statement in the whole
    apply that writes ``crm_deals`` is f4's, once per won/lost lane, and f4 is
    the exception that was argued for."""
    lanes = seed_native_pipeline(db)
    db.seed(
        "crm_deals", name="Won deal", status_id=lanes["Closed Won"].id,
        closed_at=None, expected_close_date="2026-03-31",
    )
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Qualification", 1),
        stage("Closed Won", 2, "Closed Won"),
    )]

    await run(apply=True)

    deal_writes = {s for s in writes(db) if "crm_deals" in s}
    assert deal_writes == {" ".join(crm_stages.CLOSED_AT_BACKFILL_SQL.split())}


# ── D-CRM-10 · the repair obeys the clamp it enforces ───────────────────────

async def test_a_created_won_lane_lands_at_a_hundred_percent(
    db: FakeCrmDB, tenant: FakeTenant,
) -> None:
    """The importer creates an unseen stage at probability 0 — including one
    called "Closed Won". Creating that lane here at 0 would mint a row the f2
    settings grid then refuses to save (D-CRM-10), so the repair applies the
    same rule the admin API now enforces."""
    seed_native_pipeline(db)
    tenant.pipelines = [pipeline(
        "Standard",
        stage("Order Delivered", 1, "Closed Won"),
        stage("Dropped", 2, "Closed Lost"),
    )]

    await run(apply=True)

    by_name = {row["name"]: row for row in db.rows("crm_deal_statuses")}
    assert by_name["Order Delivered"]["type"] == "won"
    assert by_name["Order Delivered"]["probability"] == 100
    assert by_name["Dropped"]["type"] == "lost"
    assert by_name["Dropped"]["probability"] == 0


# ── The pure half ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("forecast", "current", "expected"),
    [
        ("Closed Won", "open", "won"),
        ("closed lost", "ongoing", "lost"),
        ("Open", "ongoing", "ongoing"),
        ("", "on_hold", "on_hold"),
        ("Something New", None, "open"),
    ],
)
def test_forecast_type_maps_onto_type_and_never_invents_one(
    forecast: str, current: str | None, expected: str,
) -> None:
    """D-CRM-10: no parallel forecast-category enum. An unrecognised value
    leaves the existing type alone, which is the direction that never
    auto-closes a deal (or re-opens a closed one)."""
    assert crm_stages.resolve_type(forecast, current) == expected


@pytest.mark.parametrize(
    ("type_after", "probed", "current", "expected"),
    [
        ("won", None, 0, 100),
        ("lost", 90, 90, 0),
        ("open", 35, 10, 35),
        ("open", None, 10, 10),
        ("ongoing", 140, 50, 100),
        ("ongoing", -5, 50, 0),
    ],
)
def test_the_probability_rule(
    type_after: str, probed: int | None, current: int | None, expected: int,
) -> None:
    assert crm_stages.resolve_probability(type_after, probed, current) == expected


def test_stages_come_back_in_the_tenants_stated_sequence() -> None:
    """``sequence_number`` is the tenant's real lane order — the whole reason
    this endpoint exists — so it is sorted on rather than trusted to arrive."""
    stages = crm_stages.pipeline_stages(pipeline(
        "Standard",
        stage("Negotiation", 3),
        stage("Qualification", 1),
        stage("Proposal", 2),
    ))
    assert [s.name for s in stages] == ["Qualification", "Proposal", "Negotiation"]


def test_a_stage_with_no_sequence_keeps_its_arrival_order() -> None:
    """Defaulting to 0 would collapse every unsequenced stage onto one
    position, which is a different board from the one Zoho describes."""
    stages = crm_stages.pipeline_stages(
        {"maps": [stage("A", 1), {"display_value": "B"}, {"display_value": "C"}]}
    )
    assert [(s.name, s.sequence_number) for s in stages] == [
        ("A", 1), ("B", 2), ("C", 3),
    ]


def test_the_picklist_probe_returns_nothing_rather_than_zero() -> None:
    """An absent probability is "we were not told", not "0%" — and 0% on an
    open stage would silently zero the weighted pipeline."""
    assert crm_stages.stage_probabilities(layout(picklist=[
        {"display_value": "Qualification"},
    ])) == {}
    assert crm_stages.stage_probabilities(layout(picklist=[
        {"display_value": "Qualification", "probability": 20},
    ])) == {"qualification": 20}


def test_name_matching_ignores_case_and_spacing_and_nothing_else() -> None:
    """A fuzzy match would fold "Proposal" into "Proposal/Price Quote", which
    is exactly the rename that made the board wrong."""
    assert crm_stages.normalize("  Closed   Won ") == crm_stages.normalize("closed won")
    assert crm_stages.normalize("Proposal") != crm_stages.normalize(
        "Proposal/Price Quote"
    )
