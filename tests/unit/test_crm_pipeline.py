"""CRM · pipeline — status transitions, the lost gate, and the kanban board.

Spec: ``project-docs/specs/crm_app.md`` §3.6, §3.9, §4 · WS-26a done-when 4.

The headline claim under test: **a status transition has three effects, always.**
A ``PATCH`` that writes only the new ``status_id`` looks correct in the UI and
silently empties the funnel report that statuses-as-data exist to make free —
which is exactly the kind of defect no screenshot catches.

Hermetic: no Postgres, no network, no TestClient.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from gateway.routes.crm import activities as crm_activities
from gateway.routes.crm import admin as crm_admin
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import pipeline as crm_pipeline
from gateway.routes.crm import records as crm_records
from gateway.routes.crm.core import DEALS, LEADS

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(
        monkeypatch, fake,
        (crm_core, crm_records, crm_pipeline, crm_activities, crm_admin),
    )
    return fake


def _deal_pipeline(db: FakeCrmDB) -> dict:
    """The seeded deal stages, by name."""
    stages = {
        "Qualification": ("open", 10, 10, True),
        "Proposal": ("ongoing", 30, 50, False),
        "Closed Won": ("won", 50, 100, False),
        "Closed Lost": ("lost", 60, 0, False),
    }
    return {
        name: db.seed(
            "crm_deal_statuses", name=name, type=kind, position=pos,
            probability=prob, is_default=default,
        )
        for name, (kind, pos, prob, default) in stages.items()
    }


def _lead_pipeline(db: FakeCrmDB) -> dict:
    stages = {
        "New": ("open", 10, True),
        "Contacted": ("ongoing", 20, False),
        "Qualified": ("won", 40, False),
        "Lost": ("lost", 50, False),
    }
    return {
        name: db.seed(
            "crm_lead_statuses", name=name, type=kind, position=pos,
            is_default=default,
        )
        for name, (kind, pos, default) in stages.items()
    }


def _seed_deal(db: FakeCrmDB, status, **over):
    columns = {
        "name": "Printer order",
        "status_id": status.id,
        # Seeded three days into its current stage, so dwell is measurable.
        "status_changed_at": datetime.now(UTC) - timedelta(days=3),
        "probability": None,
        "closed_at": None,
        "lost_reason_id": None,
    }
    return db.seed(DEALS.table, **{**columns, **over})


# ── The three effects ───────────────────────────────────────────────────────

async def test_a_transition_writes_the_dwell_log(db: FakeCrmDB) -> None:
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    [entry] = db.rows("crm_status_changes")
    assert entry["entity_type"] == "deal"
    assert entry["entity_id"] == str(deal.id)
    assert entry["from_status"] == "Qualification"
    assert entry["to_status"] == "Proposal"
    assert entry["changed_by"] == USER.email


async def test_a_transition_records_time_spent_in_the_previous_status(
    db: FakeCrmDB,
) -> None:
    """§3.9's `dwell_seconds` — the reason the funnel report is free."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    [entry] = db.rows("crm_status_changes")
    # Seeded three days into Qualification, ±a minute of slack.
    assert 3 * 86_400 - 60 <= entry["dwell_seconds"] <= 3 * 86_400 + 60


async def test_a_transition_writes_a_timeline_activity(db: FakeCrmDB) -> None:
    """The move shows up beside the notes and calls that explain it."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    [entry] = db.rows("crm_activities")
    assert entry["type"] == "status_change"
    assert entry["deal_id"] == str(deal.id)
    assert entry["created_by"] == USER.email
    assert "Qualification" in entry["subject"] and "Proposal" in entry["subject"]


async def test_a_transition_restamps_the_stage_age_clock(db: FakeCrmDB) -> None:
    """§3.4 — without this, 'how long has this sat in Proposal' answers with
    the age of the move BEFORE it."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])
    before = deal.status_changed_at

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    [row] = db.rows(DEALS.table)
    assert row["status_changed_at"] > before


async def test_all_three_effects_land_in_one_patch(db: FakeCrmDB) -> None:
    """Counted together: each of the three has its own test above, and this is
    the one that fails if a refactor keeps two and drops the third."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    assert len(db.rows("crm_status_changes")) == 1
    assert len(db.rows("crm_activities")) == 1
    assert db.rows(DEALS.table)[0]["status_id"] == str(stages["Proposal"].id)


async def test_a_patch_that_does_not_move_the_status_writes_no_funnel_row(
    db: FakeCrmDB,
) -> None:
    """Re-sending the SAME status_id is not a transition. Logging it would put
    a zero-dwell entry in the funnel for every unrelated field edit."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(
            status_id=str(stages["Qualification"].id), next_step="Send quote",
        ), USER,
    )

    assert db.rows("crm_status_changes") == []
    assert db.rows("crm_activities") == []


async def test_a_field_only_patch_writes_no_funnel_row(db: FakeCrmDB) -> None:
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])
    await crm_records.patch_record(
        DEALS, str(deal.id), crm_core.DealIn(amount=250_000.0), USER,
    )
    assert db.rows("crm_status_changes") == []


# ── The lost gate ───────────────────────────────────────────────────────────

async def test_entering_a_lost_status_without_a_reason_is_422(
    db: FakeCrmDB,
) -> None:
    """'Why did we lose it' is the whole reason the lost vocabulary exists; a
    nullable column with no gate collects blanks."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Closed Lost"].id)), USER,
        )

    assert exc.value.status_code == 422
    assert "lost_reason_id" in str(exc.value.detail)


async def test_a_refused_lost_transition_writes_nothing_at_all(
    db: FakeCrmDB,
) -> None:
    """The refusal must come BEFORE the three effects, or a rejected move
    still leaves a funnel row saying it happened."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])

    with pytest.raises(HTTPException):
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Closed Lost"].id)), USER,
        )

    assert db.rows("crm_status_changes") == []
    assert db.rows("crm_activities") == []
    assert db.rows(DEALS.table)[0]["status_id"] == str(stages["Qualification"].id)
    assert db.committed == 0


async def test_a_lost_transition_with_a_reason_in_the_same_patch_succeeds(
    db: FakeCrmDB,
) -> None:
    """The reason may arrive WITH the move — demanding a prior write would make
    the UI do it in two requests and leave a window where it is unexplained."""
    stages = _deal_pipeline(db)
    reason = db.seed("crm_lost_reasons", label="Price", position=10)
    deal = _seed_deal(db, stages["Qualification"])

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(
            status_id=str(stages["Closed Lost"].id), lost_reason_id=str(reason.id),
        ), USER,
    )

    assert result["lost_reason_id"] == str(reason.id)
    assert len(db.rows("crm_status_changes")) == 1


async def test_a_lost_transition_uses_a_reason_already_on_the_record(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    reason = db.seed("crm_lost_reasons", label="Competitor", position=20)
    deal = _seed_deal(db, stages["Qualification"], lost_reason_id=str(reason.id))

    await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Closed Lost"].id)), USER,
    )
    assert len(db.rows("crm_status_changes")) == 1


async def test_the_lost_gate_applies_to_leads_too(db: FakeCrmDB) -> None:
    """The gate belongs to the STATUS TYPE, not to the entity — a rule spelled
    per-entity is a rule that gets added to three of four."""
    stages = _lead_pipeline(db)
    lead = db.seed(
        LEADS.table, lead_name="Anitha Kumar", status_id=stages["New"].id,
        lost_reason_id=None,
    )
    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            LEADS, str(lead.id),
            crm_core.LeadIn(status_id=str(stages["Lost"].id)), USER,
        )
    assert exc.value.status_code == 422


# ── WS-26h · stage entry requirements ───────────────────────────────────────
#
# The lost gate above, generalized: a stage may demand any of
# `core.STAGE_REQUIREABLE_FIELDS` before a deal is allowed to ENTER it. Every
# property the lost gate has is asserted again here, because "generalize
# exactly that" is the ticket and a generalisation that quietly dropped
# refuse-before-the-effects would be a regression wearing a new feature's name.


def _gate(db: FakeCrmDB, stage, *fields: str) -> None:
    """Put entry requirements on a seeded stage, as the settings grid would."""
    for row in db.rows("crm_deal_statuses"):
        if str(row["id"]) == str(stage.id):
            row["required_fields"] = list(fields)
            return
    raise AssertionError(f"no such seeded stage: {stage}")


async def test_entering_a_gated_stage_without_the_field_is_422(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=None)

    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
        )

    assert exc.value.status_code == 422
    # The refusal NAMES what is missing: "422" in a toast is not an answer to
    # "why can I not move this card".
    assert "amount" in str(exc.value.detail)
    assert "Proposal" in str(exc.value.detail)


async def test_the_refusal_names_every_missing_field_at_once(
    db: FakeCrmDB,
) -> None:
    """Not the first one — a modal that asks for `amount`, is refused again for
    `owner_email` and again for the close date is three round trips."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount", "expected_close_date", "owner_email")
    deal = _seed_deal(
        db, stages["Qualification"],
        amount=None, expected_close_date=None, owner_email=None,
    )

    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
        )

    detail = str(exc.value.detail)
    for field in ("amount", "expected_close_date", "owner_email"):
        assert field in detail


async def test_a_refused_entry_writes_nothing_at_all(db: FakeCrmDB) -> None:
    """Refused BEFORE the transition's three effects, exactly as the lost gate
    is — otherwise a rejected move still leaves a funnel row saying it
    happened."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=None)

    with pytest.raises(HTTPException):
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
        )

    assert db.rows("crm_status_changes") == []
    assert db.rows("crm_activities") == []
    assert db.rows(DEALS.table)[0]["status_id"] == str(stages["Qualification"].id)
    assert db.committed == 0


async def test_the_same_patch_may_carry_the_missing_field(db: FakeCrmDB) -> None:
    """The ticket's whole shape: ONE PATCH with fields + status together. Two
    requests could half-apply, and the half that lands is the one that moved
    the deal."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=None)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id), amount=400000),
        USER,
    )

    assert result["status_id"] == str(stages["Proposal"].id)
    assert result["amount"] == 400000
    assert len(db.rows("crm_status_changes")) == 1


async def test_a_field_already_on_the_deal_satisfies_the_requirement(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=250000)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    assert result["status_id"] == str(stages["Proposal"].id)


async def test_a_non_move_patch_is_never_blocked_by_requirements(
    db: FakeCrmDB,
) -> None:
    """Entry-only. §5.1 chose visibility over locks: a deal already sitting in a
    stage it no longer satisfies must stay editable — including editable
    TOWARDS satisfying it."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount", "owner_email")
    deal = _seed_deal(
        db, stages["Proposal"], amount=None, owner_email=None,
    )

    result = await crm_records.patch_record(
        DEALS, str(deal.id), crm_core.DealIn(next_step="Send the quote"), USER,
    )

    assert result["next_step"] == "Send the quote"
    assert db.rows("crm_status_changes") == []


async def test_a_patch_restating_the_current_status_is_not_an_entry(
    db: FakeCrmDB,
) -> None:
    """`records.patch_record` only treats a body as a transition when the
    status actually MOVES, and the gate must inherit that — a card re-saved in
    place is not an entry."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Proposal"], amount=None)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    assert result["status_id"] == str(stages["Proposal"].id)
    assert db.rows("crm_status_changes") == []


async def test_a_zero_amount_satisfies_an_amount_requirement(
    db: FakeCrmDB,
) -> None:
    """0 is a number somebody typed, not a blank box. A plain falsiness test
    here would refuse a genuine ₹0 deal, which a pipeline really does hold."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=0)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    assert result["status_id"] == str(stages["Proposal"].id)


async def test_a_blank_string_does_not_satisfy_a_requirement(
    db: FakeCrmDB,
) -> None:
    """An empty text box is not a filled one — the opposite end of the same
    rule as the zero above."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "owner_email")
    deal = _seed_deal(db, stages["Qualification"], owner_email="   ")

    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
        )

    assert exc.value.status_code == 422
    assert "owner_email" in str(exc.value.detail)


async def test_clearing_a_field_in_the_same_patch_that_moves_is_refused(
    db: FakeCrmDB,
) -> None:
    """What matters is the state the write LEAVES, not what was stored a moment
    ago: a PATCH that nulls `amount` while entering a stage that requires it
    must not be waved through on the strength of the old value."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=400000)

    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Proposal"].id), amount=None),
            USER,
        )

    assert exc.value.status_code == 422
    assert db.rows("crm_status_changes") == []


async def test_an_ungated_stage_requires_nothing(db: FakeCrmDB) -> None:
    """Every stage until somebody sets a requirement — i.e. the whole live
    board on the day this ships."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(
        db, stages["Qualification"],
        amount=None, expected_close_date=None, owner_email=None,
    )

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )

    assert result["status_id"] == str(stages["Proposal"].id)


async def test_leads_have_no_entry_requirements(db: FakeCrmDB) -> None:
    """`crm_lead_statuses` has no such column, so the gate must read absent as
    "nothing required" rather than raising an AttributeError on every lead
    move — the same asymmetry `probability` and `status_changed_at` have."""
    stages = _lead_pipeline(db)
    lead = db.seed(LEADS.table, lead_name="Asha", status_id=stages["New"].id)

    result = await crm_records.patch_record(
        LEADS, str(lead.id),
        crm_core.LeadIn(status_id=str(stages["Contacted"].id)), USER,
    )

    assert result["status_id"] == str(stages["Contacted"].id)


async def test_the_lost_gate_still_fires_first_on_a_gated_lost_stage(
    db: FakeCrmDB,
) -> None:
    """Both gates guard the same move. The lost reason is the more specific
    refusal and stays the one the caller is told about first, unchanged — the
    ticket says generalize the mechanism, not renumber the existing rule."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Closed Lost"], "amount")
    deal = _seed_deal(db, stages["Qualification"], amount=None)

    with pytest.raises(HTTPException) as exc:
        await crm_records.patch_record(
            DEALS, str(deal.id),
            crm_core.DealIn(status_id=str(stages["Closed Lost"].id)), USER,
        )

    assert "lost_reason_id" in str(exc.value.detail)


# ── WS-26h · what the settings grid may write ───────────────────────────────

async def test_an_unknown_required_field_name_is_422(db: FakeCrmDB) -> None:
    """The allowlist is checked on the way IN. A name no deal can ever carry
    would make its lane refuse every move with a message about a field that
    does not exist — a lane bricked by a typo."""
    stages = _deal_pipeline(db)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(stages["Proposal"].id),
            crm_admin.StatusIn(required_fields=["amont"]), USER,
        )

    assert exc.value.status_code == 422
    assert "amont" in str(exc.value.detail)
    assert db.rows("crm_deal_statuses")[1].get("required_fields") is None


@pytest.mark.parametrize(
    "field", ["status_id", "probability", "name", "zoho_id", "id"],
)
async def test_only_requirable_columns_may_be_required(
    db: FakeCrmDB, field: str,
) -> None:
    """Real deal columns that are still not requirable: what the move sets,
    what the platform supplies, what is already NOT NULL, what is provenance."""
    stages = _deal_pipeline(db)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(stages["Proposal"].id),
            crm_admin.StatusIn(required_fields=[field]), USER,
        )

    assert exc.value.status_code == 422


async def test_a_repeated_required_field_is_422(db: FakeCrmDB) -> None:
    """The blocked-move 422 lists what is missing, and a duplicate would name
    the same field twice in it."""
    stages = _deal_pipeline(db)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(stages["Proposal"].id),
            crm_admin.StatusIn(required_fields=["amount", "amount"]), USER,
        )

    assert exc.value.status_code == 422


async def test_the_settings_grid_can_set_both_columns(db: FakeCrmDB) -> None:
    stages = _deal_pipeline(db)

    result = await crm_admin.patch_status(
        "deal", str(stages["Proposal"].id),
        crm_admin.StatusIn(
            required_fields=["amount", "expected_close_date"], max_dwell_days=14,
        ),
        USER,
    )

    assert result.required_fields == ["amount", "expected_close_date"]
    assert result.max_dwell_days == 14


async def test_the_requirements_can_be_cleared(db: FakeCrmDB) -> None:
    """An empty list is how a stage stops demanding things — and it must be a
    legal write, or a requirement set by mistake is permanent."""
    stages = _deal_pipeline(db)
    _gate(db, stages["Proposal"], "amount")

    result = await crm_admin.patch_status(
        "deal", str(stages["Proposal"].id),
        crm_admin.StatusIn(required_fields=[]), USER,
    )

    assert result.required_fields == []


@pytest.mark.parametrize("days", [0, -1, 40000])
async def test_a_rot_threshold_outside_the_column_is_422(
    db: FakeCrmDB, days: int,
) -> None:
    """0 would paint every card in the stage amber the moment it arrived, which
    reads as a bug rather than as a policy; NULL is how you say "never"."""
    stages = _deal_pipeline(db)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "deal", str(stages["Proposal"].id),
            crm_admin.StatusIn(max_dwell_days=days), USER,
        )

    assert exc.value.status_code == 422


async def test_a_rot_threshold_may_be_cleared_to_never(db: FakeCrmDB) -> None:
    stages = _deal_pipeline(db)

    result = await crm_admin.patch_status(
        "deal", str(stages["Proposal"].id),
        crm_admin.StatusIn(max_dwell_days=None), USER,
    )

    assert result.max_dwell_days is None


@pytest.mark.parametrize(
    "payload",
    [
        {"required_fields": ["amount"]},
        {"max_dwell_days": 14},
    ],
)
async def test_a_lead_status_refuses_both_deal_only_columns(
    db: FakeCrmDB, payload: dict,
) -> None:
    """`crm_lead_statuses` has neither column, so an INSERT/UPDATE naming one
    would surface as a driver error rather than as the 422 this rule already
    knows how to say — WS-26c dw 3, one ticket later."""
    stages = _lead_pipeline(db)

    with pytest.raises(HTTPException) as exc:
        await crm_admin.patch_status(
            "lead", str(stages["New"].id), crm_admin.StatusIn(**payload), USER,
        )

    assert exc.value.status_code == 422
    assert "deal-only" in str(exc.value.detail)


async def test_a_position_only_patch_is_still_not_a_forecast_decision(
    db: FakeCrmDB,
) -> None:
    """The WS-26f regression, re-asserted because WS-26h adds two validators
    ABOVE the clamp's early return: a reorder must not start answering 422
    about a probability the caller never mentioned, or the settings grid's
    reorder loop aborts partway and leaves duplicate positions behind."""
    stages = _deal_pipeline(db)
    # A lane already contradicting D-CRM-10, the shape the Zoho importer mints.
    contradictory = db.seed(
        "crm_deal_statuses", name="Imported Won", type="won", position=70,
        probability=0,
    )

    result = await crm_admin.patch_status(
        "deal", str(contradictory.id), crm_admin.StatusIn(position=15), USER,
    )

    assert result.position == 15


# ── closed_at and probability ───────────────────────────────────────────────

@pytest.mark.parametrize("stage", ["Closed Won", "Closed Lost"])
async def test_entering_a_terminal_status_stamps_closed_at(
    db: FakeCrmDB, stage: str,
) -> None:
    stages = _deal_pipeline(db)
    reason = db.seed("crm_lost_reasons", label="Price", position=10)
    deal = _seed_deal(db, stages["Qualification"], lost_reason_id=str(reason.id))

    result = await crm_records.patch_record(
        DEALS, str(deal.id), crm_core.DealIn(status_id=str(stages[stage].id)), USER,
    )
    assert result["closed_at"] is not None


async def test_a_non_terminal_transition_leaves_closed_at_alone(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"])
    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )
    assert result["closed_at"] is None


async def test_a_transition_fills_a_null_probability_from_the_stage(
    db: FakeCrmDB,
) -> None:
    """§3.4's auto-fill — a forecast column nobody types into stays empty."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"], probability=None)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )
    assert result["probability"] == 50


async def test_a_transition_never_overwrites_a_stated_probability(
    db: FakeCrmDB,
) -> None:
    """"Auto-filled when NULL" is the whole rule; overwriting a number a human
    typed would make the field unusable."""
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"], probability=85)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id)), USER,
    )
    assert result["probability"] == 85


async def test_a_probability_sent_with_the_move_wins(db: FakeCrmDB) -> None:
    stages = _deal_pipeline(db)
    deal = _seed_deal(db, stages["Qualification"], probability=None)

    result = await crm_records.patch_record(
        DEALS, str(deal.id),
        crm_core.DealIn(status_id=str(stages["Proposal"].id), probability=20), USER,
    )
    assert result["probability"] == 20


# ── Leads have no stage-age column ──────────────────────────────────────────

async def test_a_lead_transition_reads_dwell_from_the_change_log(
    db: FakeCrmDB,
) -> None:
    """Leads carry no `status_changed_at` by design (§3.3), so dwell comes from
    the log itself — falling back to `created_at` on the first move. Both mean
    time-in-status, not time-since-creation."""
    stages = _lead_pipeline(db)
    lead = db.seed(
        LEADS.table, lead_name="Anitha Kumar", status_id=stages["New"].id,
        created_at=datetime.now(UTC) - timedelta(days=5),
    )

    await crm_records.patch_record(
        LEADS, str(lead.id),
        crm_core.LeadIn(status_id=str(stages["Contacted"].id)), USER,
    )

    [entry] = db.rows("crm_status_changes")
    assert entry["entity_type"] == "lead"
    assert 5 * 86_400 - 60 <= entry["dwell_seconds"] <= 5 * 86_400 + 60
    assert any("max(changed_at)" in s for s in db.statements)


async def test_a_lead_transition_writes_no_stage_age_column(
    db: FakeCrmDB,
) -> None:
    """`crm_leads` has no `status_changed_at`; writing one would be a 500."""
    stages = _lead_pipeline(db)
    lead = db.seed(
        LEADS.table, lead_name="Anitha Kumar", status_id=stages["New"].id,
    )
    await crm_records.patch_record(
        LEADS, str(lead.id),
        crm_core.LeadIn(status_id=str(stages["Contacted"].id)), USER,
    )
    # The transition emits two UPDATEs on this table — the record write and
    # `bump_last_activity` — so name the one under test rather than assuming
    # there is only one.
    [update] = [
        s for s in db.statements_touching("UPDATE crm_leads SET")
        if "status_id" in s
    ]
    assert "status_changed_at" not in update


# ── The board ───────────────────────────────────────────────────────────────

async def test_the_pipeline_renders_every_lane_including_empty_ones(
    db: FakeCrmDB,
) -> None:
    """A kanban that hides its empty columns is a list."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Qualification"], amount=100_000.0)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)

    assert [lane.status.name for lane in board.lanes] == [
        "Qualification", "Proposal", "Closed Won", "Closed Lost",
    ]
    assert board.lanes[1].rows == []
    assert board.lanes[1].count == 0


async def test_each_lane_carries_its_count_and_rupee_total(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=100_000.0)
    _seed_deal(db, stages["Proposal"], amount=250_000.0)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.count == 2
    assert proposal.amount == 350_000.0


async def test_lane_totals_cover_the_whole_lane_not_the_returned_page(
    db: FakeCrmDB,
) -> None:
    """A header that counts only the page it returned lies about a busy lane —
    and the busy lane is the one somebody is looking at."""
    stages = _deal_pipeline(db)
    for _ in range(4):
        _seed_deal(db, stages["Proposal"], amount=50_000.0)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=2, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert len(proposal.rows) == 2
    assert proposal.count == 4
    assert proposal.amount == 200_000.0


async def test_the_board_can_be_scoped_to_one_owner_case_insensitively(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], owner_email="VJVarada@Fracktal.in")
    _seed_deal(db, stages["Proposal"], owner_email="someone@fracktal.in")

    board = await crm_pipeline.get_pipeline(
        owner="vjvarada@FRACKTAL.IN", per_lane=50, user=USER,
    )
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.count == 1


# ── WS-26f f3 · weighted ₹ per lane (done-when 6, the SQL half) ────────────
#
# The lane number binds the SQL and `board.ts`'s pure function binds the same
# formula for the header rollup (C4). It cannot be derived from `rows`: those
# are capped at `per_lane` and the browser sends no cap at all, so a
# rows-derived total would under-report exactly the busy lane on screen.

async def test_a_lane_weights_by_the_deals_own_probability(
    db: FakeCrmDB,
) -> None:
    """D-CRM-10: forecast math reads the DEAL, never the stage. A rep who knows
    the champion just left marks a Proposal-stage deal at 20 without moving
    it, and the forecast has to believe them."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=100_000.0, probability=20)
    _seed_deal(db, stages["Proposal"], amount=200_000.0, probability=75)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.amount == 300_000.0
    assert proposal.weighted == 20_000.0 + 150_000.0


async def test_a_null_probability_inherits_the_stage_default(
    db: FakeCrmDB,
) -> None:
    """The inheritance materializes on entry, so a NULL survives only on rows
    that predate a move — the imported ones, which is most of the board today.
    Reading them as 0% would zero the forecast on exactly those rows."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=100_000.0, probability=None)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.status.probability == 50
    assert proposal.weighted == 50_000.0


async def test_a_zero_probability_is_a_measurement_not_a_missing_value(
    db: FakeCrmDB,
) -> None:
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=100_000.0, probability=0)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.weighted == 0.0
    assert proposal.amount == 100_000.0


@pytest.mark.parametrize("lane", ["Closed Won", "Closed Lost"])
async def test_a_terminal_lane_forecasts_nothing(
    db: FakeCrmDB, lane: str,
) -> None:
    """Won revenue is CLOSED, not pipeline, and a lost deal forecasts nothing.
    Both lanes still carry their ₹ total — WS-26g's win/loss block reads it."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages[lane], amount=500_000.0, probability=100)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    terminal = next(x for x in board.lanes if x.status.name == lane)

    assert terminal.amount == 500_000.0
    assert terminal.weighted == 0.0


async def test_an_on_hold_lane_is_excluded_from_the_weighted_total(
    db: FakeCrmDB,
) -> None:
    """``on_hold`` is a fifth status type §5.1's prose never mentions, and it
    is deliberately NOT weighted: a deal nobody is working is not a forecast,
    and counting it at its stage's prior is how a pipeline number stays high
    while the quarter empties."""
    parked = db.seed(
        "crm_deal_statuses", name="Parked", type="on_hold", position=40,
        probability=40, is_default=False,
    )
    _seed_deal(db, parked, amount=1_000_000.0, probability=40)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    lane = next(x for x in board.lanes if x.status.name == "Parked")

    assert lane.amount == 1_000_000.0
    assert lane.weighted == 0.0
    assert set(crm_core.WEIGHTED_TYPES) == {"open", "ongoing"}


async def test_the_weighted_total_covers_the_whole_lane_not_the_page(
    db: FakeCrmDB,
) -> None:
    """The reason it is SQL and not `rows.reduce(...)`: the browser asks for no
    per-lane cap, and the gateway's default is 50."""
    stages = _deal_pipeline(db)
    for _ in range(4):
        _seed_deal(db, stages["Proposal"], amount=100_000.0, probability=50)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=2, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert len(proposal.rows) == 2
    assert proposal.weighted == 200_000.0


async def test_the_weighted_aggregate_binds_the_stage_default(
    db: FakeCrmDB,
) -> None:
    """Structural: the fallback is the lane's OWN probability, bound like every
    other caller value in this package rather than interpolated — and dropping
    the COALESCE is what would silently read every pre-move NULL as 0%."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=100_000.0)

    await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    aggregates = db.statements_touching("AS weighted")

    assert aggregates, "the lane aggregate lost its weighted total"
    assert "COALESCE(probability, :stage_probability)" in aggregates[0]
    proposal_call = next(
        params for statement, params in db.calls
        if "AS weighted" in statement
        and params.get("status_id") == str(stages["Proposal"].id)
    )
    assert proposal_call["stage_probability"] == 50


# ── WS-26c · kanban cards carry the account name (done-when 2) ─────────────

async def test_a_board_card_carries_its_organization_name(
    db: FakeCrmDB,
) -> None:
    """The board is the surface that most needs it: a card shows
    name/org/amount/owner/stage-age, and the browser cannot client-side join
    the account list because that list is paged at 100."""
    stages = _deal_pipeline(db)
    org = db.seed("crm_companies", name="Bosch India")
    _seed_deal(db, stages["Proposal"], amount=100_000.0, organization_id=org.id)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.rows[0]["organization_name"] == "Bosch India"


async def test_a_board_card_without_an_organization_still_renders(
    db: FakeCrmDB,
) -> None:
    """LEFT, not INNER — a walk-in filament order has no account and must not
    disappear from the lane it is sitting in."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=8_000.0)

    board = await crm_pipeline.get_pipeline(owner=None, per_lane=50, user=USER)
    proposal = next(x for x in board.lanes if x.status.name == "Proposal")

    assert proposal.count == 1
    assert proposal.rows[0]["organization_name"] is None


async def test_the_lane_query_joins_after_its_own_limit(
    db: FakeCrmDB,
) -> None:
    """Structural: per_lane caps the rows the join runs over. A join outside
    the limit would resolve an account name for every deal in the stage to
    return the first fifty."""
    stages = _deal_pipeline(db)
    _seed_deal(db, stages["Proposal"], amount=1.0)

    await crm_pipeline.get_pipeline(owner=None, per_lane=2, user=USER)
    joined = db.statements_touching("LEFT JOIN crm_companies")

    assert joined, "the board lost its organization-name projection"
    assert "LIMIT :limit) base LEFT JOIN" in joined[0]
    assert joined[0].rstrip().endswith("base.id DESC")


# ── Structural fences ───────────────────────────────────────────────────────

def test_the_entity_type_vocabulary_matches_the_migration_check() -> None:
    """`crm_status_changes.entity_type` has a CHECK; a slug this map invents
    would be an IntegrityError on the first transition of that kind."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[2] / "infra" / "postgres" / "144_crm.sql"
    ).read_text(encoding="utf-8")

    assert "entity_type IN ('lead', 'deal')" in migration
    assert set(crm_pipeline.ENTITY_TYPES.values()) == {"lead", "deal"}
    # And every entity WITH a status pipeline has an entry, or its transitions
    # would raise a KeyError instead of logging.
    for entity in (LEADS, DEALS):
        assert entity.slug in crm_pipeline.ENTITY_TYPES


def test_the_transition_is_the_only_writer_of_status_change_rows() -> None:
    """One writer, so the three effects cannot be produced separately. If a
    second module starts inserting into crm_status_changes, the funnel can
    disagree with the timeline and nothing will say so."""
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    writers = sorted(
        path.name for path in package.glob("*.py")
        if "crm_status_changes" in path.read_text(encoding="utf-8")
        and "INSERT" not in path.name
        and 'insert_row(db, "crm_status_changes"' in path.read_text(encoding="utf-8")
    )
    assert writers == ["pipeline.py"], writers


# ── WS-26h + WS-26h2 · where the two stage gates are allowed to be ─────────
#
# ONE mechanism, TWO gates. `routes/crm/` has two places a status write can be
# refused, and the same live-system property guards both: the ENABLED 600s Zoho
# pull must reach NEITHER. A pulled record carries whatever stage Zoho has it
# in and no obligation to satisfy a requirement somebody set here afterwards,
# so a gate on that path turns one settings-grid save into a whole Zoho module
# failing every cycle — fail-closed in the wrong direction, on a loop nobody is
# watching, and changing that loop is OWNER-GATE (`work_plan.md` §6 WS-26 (a)).
#
#   * `_MOVE_GATE`  — `pipeline.apply_status_transition` (WS-26h), the status
#     TRANSITION and its three effects.
#   * `_ENTRY_GATE` — `pipeline._require_entry_fields` (WS-26h2), the entry
#     requirements, reached unconditionally from the move gate and directly
#     from `records._resolve_status` on the create path.
#
# Each gate gets TWO fences, because one assertion cannot back the claim:
#
#   * `_gate_call_files` answers "which files CALL the gate" — the direct case.
#   * `_gate_reached_from` answers "can the enabled 600s pull REACH it" — the
#     indirect case, which is the one a call-site set is blind to.
#
# Both gates' fences were TEXT MATCHES first and both were wrong in the same
# two directions. Measured against the real package (h2's repair round 1 for
# the entry gate, this ticket's conversion round for the move gate): an ALIASED
# import in `import_zoho.py` left the fence GREEN, and a COMMENT in
# `import_zoho.py` saying the path must never call the gate turned it RED —
# making deletion of that comment the cheapest way back to green. The move
# gate's text fence was blind to the indirect route as well
# (`import_zoho.apply_record` → `records.patch_record`). AST call nodes fix all
# three. Do not reintroduce a third mechanism: `_GATE` was a module constant
# until WS-26h-fence parameterised it, and one set of helpers now answers for
# both gates.
#
# Known limit, stated because the docstrings must not outrun it — and restated
# in repair round 1, where the limit block itself was the overclaim.
#
# This is a STATIC call graph over `routes/crm/*.py`. It READS: direct calls;
# aliased imports; module-attribute calls; function-body imports; **relative
# imports at any level** (`from .pipeline import …`, `from . import pipeline`,
# `from ..crm.pipeline import …`); **star imports**; **symbols re-exported
# through the package `__init__`**; a name bound to the package itself
# (`from .. import crm` → `crm.pipeline.f()`); and **calls written at module
# level**, outside any `def`.
#
# It is BLIND to the shapes below. Each was measured against a copy of the real
# package rather than reasoned about, and they are split by ONE question —
# **is the gate's own name written immediately before a `(`?** — because that,
# and not "expression versus name", is what the deleted substring scan keyed on,
# and therefore what decides whether a hole is a REGRESSION against it.
#
#   A. The name is NEVER written before a `(`, so the substring scan was blind
#      too. **Not regressions**; holes this fence never claimed to close:
#        · dispatch through a value — `_MOVE = apply_status_transition` then
#          `_MOVE(…)`, `functools.partial`, a callback handed across the
#          package boundary;
#        · a registry or object holding the gate under ANOTHER name —
#          `_REGISTRY["move"](…)`, `Registry.move(…)`;
#        · `getattr(pipeline, "apply_status_transition")(…)` — measured green
#          on the old scan too, because `")("` intervenes and the literal
#          `apply_status_transition(` never appears.
#   B. The name IS written before a `(`, so the substring scan went RED.
#      **Residual REGRESSIONS — two shapes, both left open deliberately.** Both
#      are a call qualified by something the graph cannot tie to a package
#      module:
#        · `importlib.import_module("…pipeline").apply_status_transition(…)`
#        · `_GATES.apply_status_transition(…)`, where `_GATES` is a local
#          object or class rather than a module binding.
#      ⚠️ **Why they stay open — the reason recorded in repair round 1 was
#      FALSE and is corrected here.** It said closing them meant resolving
#      unbound attributes against every top-level name, letting an innocent
#      `db.close()` fabricate a chain. A reviewer disproved that by building
#      it: a narrow resolver reading only STRING CONSTANTS adds no edges at all
#      to the real package, reddens both forms, and never looks at `db.close()`.
#      The real reason is reach, not risk. `importlib.import_module` appears
#      ONCE in all of `apps/` + `packages/` (`orchestrator/declarative.py:100`,
#      a plugin loader) and ZERO times in the gateway. Neither shape is the
#      plausible refactor this fence exists to catch ("make the importer use
#      the shared seam"); both are deliberate evasion, and a fence cannot be
#      built against someone willing to edit the fence file.
#   C. Reachability enters at `_SYNC_ENTRY_POINTS` only. Module-level code is a
#      call SITE (so a module-level call TO a gate is caught) but is not an
#      entry POINT, so an indirect route that begins at import time —
#      `_PRIMED = patch_record(…)` at module level — is seen by neither
#      reachability fence.
#   D. Anything outside `routes/crm/*.py`.
#
# Nothing in this package reaches either gate by any of these today.

#: WS-26h — the status transition itself, and its three effects.
_MOVE_GATE = ("pipeline", "apply_status_transition")

#: WS-26h2 — the entry requirements on the stage a caller CHOSE (D-CRM-13).
_ENTRY_GATE = ("pipeline", "_require_entry_fields")

#: Where the ENABLED 600s Zoho sync enters this package. Anything these can
#: reach runs against the live upstream tenant every cycle.
#:
#: ⚠️ `_run_cycle_locked` and not just `pull_phase`, added by repair round 1:
#: one cycle PULLS **and then PUSHES** — `pull_phase` at `sync_zoho.py:1172`,
#: then `apply_zoho_deletes`, `push_records`, `push_activities`,
#: `push_tombstones` (and below them `apply_push_result` / `_settle` / `_fail`).
#: A gate reached only from the push half runs against the live tenant every
#: cycle just as surely, and entering at `pull_phase` alone reported `[]` for it.
#: The loop's own three frames are entered so a rename inside the chain cannot
#: quietly narrow the fence.
_SYNC_ENTRY_POINTS = (
    ("sync_zoho", "_sync_loop"),
    ("sync_zoho", "run_cycle"),
    ("sync_zoho", "_run_cycle_locked"),
    ("sync_zoho", "pull_phase"),
    ("import_zoho", "apply_module"),
)

#: Calls written at module level rather than inside a `def`. They run at IMPORT
#: time, so they are call sites like any other; they are not entry points.
_MODULE_FRAME = "<module>"


def _dotted(node: object) -> str | None:
    """``a``/``a.b.c`` as a string, or None for anything else."""
    import ast

    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _crm_target(node, package: str) -> str | None:
    """What an ``ImportFrom`` imports FROM, as a path inside the CRM package.

    ``""`` is the package itself, ``"pipeline"`` is that submodule, ``None`` is
    "not this package".

    ⚠️ **Relative forms are resolved, and that is the whole point of this
    helper** (repair round 1). ``from .pipeline import apply_status_transition``
    is the same import as the absolute spelling — same call, same live-system
    consequence — but its ``node.module`` is ``"pipeline"`` with ``level=1``,
    and ``from . import pipeline`` has ``node.module is None``. Gating on
    ``node.module.startswith("gateway.routes.crm.")`` saw neither, so the fence
    could be respelled around in one line: a fence with a spelling it does not
    know is a fence with a hole, and the substring scan this replaced had no
    such hole.
    """
    absolute = "gateway.routes." + package
    module = node.module or ""
    if node.level == 0:
        if module == absolute:
            return ""
        if module.startswith(absolute + "."):
            return module[len(absolute) + 1:]
        return None
    if node.level == 1:
        # `from . import x` → "", `from .pipeline import x` → "pipeline".
        return module
    # `level >= 2` walks up out of the package, so it has to be named again to
    # land back inside it: `from ..crm.pipeline import x`.
    if module == package:
        return ""
    if module.startswith(package + "."):
        return module[len(package) + 1:]
    return None


def _package_alias(root: str, stems: frozenset[str]) -> dict[str, str]:
    """A local name bound to the PACKAGE — so ``root.pipeline.f()`` resolves."""
    return {f"{root}.{stem}": stem for stem in stems}


def _from_submodule(
    stem: str, aliases, exports: dict[str, frozenset[str]],
) -> dict[str, frozenset[tuple[str, str]]]:
    """``from .pipeline import X`` / ``import *`` → local name -> candidates."""
    names: dict[str, frozenset[tuple[str, str]]] = {}
    for alias in aliases:
        if alias.name == "*":
            names.update({
                fn: frozenset({(stem, fn)}) for fn in exports.get(stem, frozenset())
            })
        else:
            names[alias.asname or alias.name] = frozenset({(stem, alias.name)})
    return names


def _from_package(
    aliases, stems: frozenset[str], exports: dict[str, frozenset[str]],
) -> tuple[dict[str, frozenset[tuple[str, str]]], dict[str, str]]:
    """``from . import pipeline`` / ``from . import a_reexported_symbol``.

    A submodule binds a MODULE; anything else is a symbol re-exported through
    the package ``__init__``, which names its defining module only up to
    ambiguity — so every module that DEFINES the name is a candidate.
    """
    names: dict[str, frozenset[tuple[str, str]]] = {}
    modules: dict[str, str] = {}
    for alias in aliases:
        if alias.name in stems:
            modules[alias.asname or alias.name] = alias.name
        elif alias.name == "*":
            for stem in stems:
                _merge_names(names, _from_submodule(stem, aliases, exports))
        else:
            found = frozenset(
                (stem, alias.name) for stem in stems
                if alias.name in exports.get(stem, frozenset())
            )
            if found:
                names[alias.asname or alias.name] = found
    return names, modules


def _merge_names(into: dict, more: dict) -> None:
    """Union, never overwrite: two imports can bind the same local name."""
    for key, value in more.items():
        into[key] = into.get(key, frozenset()) | value


def _crm_imports(
    tree, *, package: str, stems: frozenset[str], exports: dict[str, frozenset[str]],
) -> tuple[dict[str, frozenset[tuple[str, str]]], dict[str, str]]:
    """``(local name -> {(module, function), …}, local alias -> module stem)``.

    Read from the WHOLE tree, so an import inside a function body counts —
    that is how the "one seam" mutant reached the gate from ``core.py``.

    A local name maps to a SET, not one pair, because a symbol re-exported
    through the package ``__init__`` names its defining module only up to
    ambiguity. Over-approximating there is the safe direction.
    """
    import ast

    absolute = "gateway.routes." + package
    names: dict[str, frozenset[tuple[str, str]]] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            rest = _crm_target(node, package)
            if rest in stems:
                _merge_names(names, _from_submodule(rest, node.names, exports))
            elif rest == "":
                more, more_modules = _from_package(node.names, stems, exports)
                _merge_names(names, more)
                modules.update(more_modules)
            elif rest is None and (node.module or "") in ("gateway.routes", ""):
                # `from gateway.routes import crm` / `from .. import crm` bind
                # the package itself under a plain name.
                for alias in node.names:
                    if alias.name == package:
                        modules.update(
                            _package_alias(alias.asname or alias.name, stems),
                        )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(absolute + "."):
                    modules[alias.asname or alias.name] = alias.name.rsplit(".", 1)[1]
                elif alias.name == absolute:
                    modules.update(
                        _package_alias(alias.asname or alias.name, stems),
                    )
    return names, modules


def _resolved_calls(
    call, *, module: str, names: dict, modules: dict, own: set[str],
) -> frozenset[tuple[str, str]]:
    """One ``ast.Call`` → the ``(module, function)`` pairs it may name."""
    target = _dotted(call.func)
    if target is None:
        return frozenset()
    if "." in target:
        prefix, attr = target.rsplit(".", 1)
        return frozenset({(modules[prefix], attr)}) if prefix in modules else frozenset()
    if target in names:
        return names[target]
    return frozenset({(module, target)}) if target in own else frozenset()


def _crm_call_graph(package) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """``(module, function) -> the (module, function) pairs it calls``.

    Over-approximates on purpose — nested and class-scoped defs merge into
    their bare name, and an import inside one function is treated as visible to
    the whole module. A fence guarding a live loop should answer "maybe" as
    "yes"; the failure it must never produce is a quiet green.

    Module-level calls are collected under ``_MODULE_FRAME`` rather than
    dropped: they run at import time, and leaving them out made a call written
    outside any ``def`` invisible to the call-site fence.
    """
    import ast

    stems = frozenset(path.stem for path in package.glob("*.py"))
    trees: dict[str, object] = {}
    exports: dict[str, frozenset[str]] = {}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        trees[path.stem] = tree
        # Top-level defs only: what a `*` or an `__init__` re-export can carry.
        exports[path.stem] = frozenset(
            node.name for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        )

    graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for module, tree in sorted(trees.items()):
        names, modules = _crm_imports(
            tree, package=package.name, stems=stems, exports=exports,
        )
        defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        own = {node.name for node in defs}
        scopes: list[tuple[str, list]] = [
            (node.name, [c for c in ast.walk(node) if isinstance(c, ast.Call)])
            for node in defs
        ]
        nested = {id(call) for _fn, calls in scopes for call in calls}
        scopes.append((_MODULE_FRAME, [
            call for call in ast.walk(tree)
            if isinstance(call, ast.Call) and id(call) not in nested
        ]))
        for fn, calls in scopes:
            edges = graph.setdefault((module, fn), set())
            for sub in calls:
                edges |= _resolved_calls(
                    sub, module=module, names=names, modules=modules, own=own,
                )
    return graph


def _gate_call_files(package, gate: tuple[str, str]) -> list[str]:
    """The files containing a real CALL to ``gate``, under any local name.

    ``gate`` is an argument rather than a module constant so the move gate and
    the entry gate are expressed through ONE mechanism — a second copy of this
    walk keyed on a different name is the defect, not the feature.
    """
    graph = _crm_call_graph(package)
    return sorted({
        f"{module}.py" for (module, _fn), calls in graph.items() if gate in calls
    })


def _gate_reached_from(package, entries, gate: tuple[str, str]) -> list[str]:
    """The call chain by which ``entries`` reaches ``gate``, or ``[]``."""
    graph = _crm_call_graph(package)
    parents: dict[tuple[str, str], tuple[str, str] | None] = {}
    queue = []
    for entry in entries:
        parents[entry] = None
        queue.append(entry)
    while queue:
        node = queue.pop(0)
        for target in sorted(graph.get(node, ())):
            if target in parents:
                continue
            parents[target] = node
            if target == gate:
                chain, cursor = [], target
                while cursor is not None:
                    chain.append(f"{cursor[0]}.{cursor[1]}")
                    cursor = parents[cursor]
                return list(reversed(chain))
            queue.append(target)
    return []


def test_the_move_gate_is_called_from_exactly_two_files() -> None:
    """WS-26h, half one — the DIRECT case.

    Two FILES may call :func:`apply_status_transition`: ``pipeline.py``, which
    defines it, and ``records.py``, the one request path that moves a status.
    Nothing else — in particular not import_zoho / sync_zoho / broker_handlers
    / auto_lead, the four that run outside a member request.

    ⚠️ **FILE-level, and deliberately not function-level.** The assertion is
    over ``f"{module}.py"``, so moving the call to another function *inside*
    ``records.py`` keeps it green. That is not an oversight: WS-26i-bulk
    done-when 1 extracts ``patch_record``'s body into ``apply_record_patch`` in
    the same file, and a function-level assertion would turn that sanctioned
    change red and contradict its "passes with zero edits" clause. The property
    this fence owns is *which file the seam lives in*; **which function inside
    it is not claimed here, and nothing asserts it.** (It was claimed in this
    docstring until repair round 1, which is the same overclaim-by-docstring
    defect the whole ticket exists to remove.)

    ⚠️ **The seam's LOCATION is what this pins, and it is load-bearing for
    WS-26i-bulk done-when 1**, which decides the seam **stays in
    ``records.py``** — so this fence passes there with zero edits. Measured:
    extracting the call into a new ``routes/crm/bulk_seam.py`` reports
    ``['bulk_seam.py', 'pipeline.py']`` and goes red, with ``records.py``
    dropping out, so the failure names the relocation rather than merely
    growing the list.

    Converted from a literal ``"apply_status_transition(" in path.read_text()``
    scan by WS-26h-fence. Measured on the real package before the conversion:
    an aliased import in ``import_zoho.py`` left it GREEN and a COMMENT saying
    the path must never call the gate turned it RED. Calls are AST nodes now,
    so both are answered the right way round.
    """
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    assert _gate_call_files(package, _MOVE_GATE) == ["pipeline.py", "records.py"]


def test_the_zoho_pull_never_enters_the_stage_gate() -> None:
    """WS-26h, half two — the INDIRECT case, and the load-bearing one.

    The importer and the sync engine must not route their status writes
    through :func:`apply_status_transition`, and "must not" is a REACHABILITY
    claim: ``import_zoho.apply_record`` calling ``records.patch_record`` puts
    the transition on the enabled pull without either Zoho module ever naming
    it. The old text-match fence stayed green on exactly that shape.

    A pulled deal carries whatever stage Zoho has it in and no obligation to
    satisfy an entry requirement somebody set here afterwards — and the move
    gate reaches the entry gate unconditionally, so this is also the fence that
    keeps the create-side refusal off the loop. Changing that loop is
    OWNER-GATE (``work_plan.md`` §6 WS-26 (a)), so this must fail in CI rather
    than be fixed forward.

    ⚠️ **The whole cycle, not the pull half** (repair round 1). Entering only
    at ``pull_phase`` left the PUSH half — ``push_records`` /
    ``push_activities`` / ``push_tombstones`` and below them
    ``apply_push_result`` / ``_settle`` / ``_fail`` — reporting ``[]`` for a
    gate reached from it, while it would run against the live tenant every
    cycle just the same. Measured: a gate reached from ``apply_push_result``
    was green at ``pull_phase`` and is red at ``_run_cycle_locked``.
    """
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    chain = _gate_reached_from(package, _SYNC_ENTRY_POINTS, _MOVE_GATE)
    assert chain == [], " -> ".join(chain)


def test_the_entry_gate_is_called_from_exactly_two_files() -> None:
    """WS-26h2 done-when 8, half one — the DIRECT case.

    ``pipeline.py`` defines the gate and calls it from
    :func:`apply_status_transition`; ``records.py`` calls it from
    ``_resolve_status``, the one create path where a caller may choose the
    stage. Nothing else may call it at all.

    ``core.py`` is the one to watch: ``insert_row`` keyed on
    ``table == "crm_deals"`` is the tempting "one seam" and misses the Zoho
    pull **today only because ``upsert_by_zoho_id`` duplicates the statement
    rather than delegating**. Measured: with the gate moved there, both Zoho
    suites stay green (136) and only this fence and the two D-CRM-13 cases go
    red. Do not build on that accident.

    Calls are read as AST nodes, so a comment or docstring **mentioning** the
    gate — including this file's — is not a call, and an aliased import is.
    """
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    assert _gate_call_files(package, _ENTRY_GATE) == ["pipeline.py", "records.py"]


def test_no_zoho_pull_path_can_reach_the_entry_gate() -> None:
    """WS-26h2 done-when 8, half two — the INDIRECT case, and the load-bearing
    one.

    A call-site set cannot see this: ``import_zoho.apply_record`` calling
    ``records._resolve_status`` puts the gate on the pull without either Zoho
    module ever naming it. That refactor ("make the importer use the shared
    create seam") is the one this repo's own doctrine encourages, and
    ``apply_record`` **already sets ``values["status_id"]`` server-side**, so
    ``chosen`` would be truthy for every pulled deal.

    What happens then is not a test failure, it is an incident: the sync loop
    is ENABLED and pulls every 600s, so the first settings-grid save on a
    Zoho-named lane starts 422-ing rows from the live upstream tenant, on a
    loop nobody is watching. Changing that loop is OWNER-GATE
    (``work_plan.md`` §6 WS-26 (a)), so this must fail in CI rather than be
    fixed forward. ⚠️ Neither Zoho suite would catch it either — ``grep
    required_fields`` over both returns nothing, so the gate is a silent no-op
    against their fixtures, exactly as it was for the ``core.insert_row``
    mutant.
    """
    from pathlib import Path

    package = Path(crm_core.__file__).parent
    chain = _gate_reached_from(package, _SYNC_ENTRY_POINTS, _ENTRY_GATE)
    assert chain == [], " -> ".join(chain)


# The shapes the four fences above claim to tell apart, run against
# synthetic packages so "the fence went blind" is a red test rather than a
# silent gap (the `test_crm_agent.py` path-guard convention).

#: Where BOTH gates are allowed to be called from — the real package's answer
#: for `_MOVE_GATE` and for `_ENTRY_GATE` alike.
_SITED = ["pipeline.py", "records.py"]

_FENCE_SOURCES = {
    "pipeline.py": (
        "def _require_entry_fields(status, record, patch):\n"
        "    pass\n"
        "def apply_status_transition(db):\n"
        "    _require_entry_fields(1, 2, {})\n"
        # The conversion's won-status move — why `pipeline.py` is in the move
        # gate's own call-file set, exactly as in the real package.
        "async def _stamp_converted(db, lead):\n"
        "    await apply_status_transition(db)\n"
    ),
    "records.py": (
        "from gateway.routes.crm.pipeline import _require_entry_fields\n"
        "from gateway.routes.crm.pipeline import apply_status_transition\n"
        "def _resolve_status(db, values):\n"
        "    _require_entry_fields(1, 2, values)\n"
        "def create_record(db):\n"
        "    _resolve_status(db, {})\n"
        # The one request path that MOVES a status. Its body is what
        # WS-26i-bulk would extract, which is why the move gate's call-file
        # fence is what announces that extraction.
        "async def patch_record(db, record, values):\n"
        "    await apply_status_transition(db)\n"
    ),
    "core.py": "async def insert_row(db, table, values):\n    pass\n",
    "import_zoho.py": (
        "from gateway.routes.crm.core import insert_row\n"
        "async def apply_record(db):\n"
        "    await insert_row(db, 'crm_deals', {})\n"
        "async def apply_module(db):\n"
        "    await apply_record(db)\n"
    ),
    # The cycle's own chain, not just its pull half: `_run_cycle_locked` calls
    # `pull_phase` AND the three push phases, and `_SYNC_ENTRY_POINTS` enters at
    # the loop. Without these frames the widened entry points would be fenced by
    # nothing and the push-half case below could not go red.
    "sync_zoho.py": (
        "from gateway.routes.crm.import_zoho import apply_module\n"
        "async def pull_phase(db):\n"
        "    await apply_module(db)\n"
        "async def apply_push_result(db, row):\n"
        "    pass\n"
        "async def push_records(db):\n"
        "    await apply_push_result(db, None)\n"
        "async def _run_cycle_locked(db):\n"
        "    await pull_phase(db)\n"
        "    await push_records(db)\n"
        "async def run_cycle(db):\n"
        "    await _run_cycle_locked(db)\n"
        "async def _sync_loop(db):\n"
        "    await run_cycle(db)\n"
    ),
}

#: ``(name, file, extra source, entry-gate answer, move-gate answer)`` where an
#: answer is ``(expected call files, the gate is reachable from the pull)``.
#: BOTH gates are asserted on EVERY case, so a shape aimed at one of them
#: cannot quietly move the other's answer.
_FENCE_CASES = [
    ("baseline", None, "", (_SITED, False), (_SITED, False)),
    # ── the ENTRY gate's shapes (WS-26h2) ──────────────────────────────────
    (
        "a comment naming the entry gate is not a call",
        "import_zoho.py",
        "# never calls _require_entry_fields(...) — the pull must stay ungated\n"
        "GATE_DOC = 'see _require_entry_fields(status, record, patch)'\n",
        (_SITED, False),
        (_SITED, False),
    ),
    (
        "a direct entry-gate call added to import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm.pipeline import _require_entry_fields\n"
        "def gated(status, values):\n"
        "    _require_entry_fields(status, None, values)\n",
        (["import_zoho.py", "pipeline.py", "records.py"], False),
        (_SITED, False),
    ),
    (
        "a direct entry-gate call added to sync_zoho",
        "sync_zoho.py",
        "from gateway.routes.crm.pipeline import _require_entry_fields\n"
        "def gated(status, values):\n"
        "    _require_entry_fields(status, None, values)\n",
        (["pipeline.py", "records.py", "sync_zoho.py"], False),
        (_SITED, False),
    ),
    (
        # ⚠️ The import is INSIDE the function on purpose, and this is the case
        # that pins `_crm_imports`' whole-tree walk. The real `core.py` CANNOT
        # import `pipeline` at top level — `pipeline` imports `CLOSING_TYPES`
        # from `core`, so a module-level import raises `ImportError: cannot
        # import name 'CLOSING_TYPES' from partially initialized module`. The
        # "one seam" mis-siting this package is most likely to grow can
        # therefore ONLY be written with a function-body import, and it is also
        # the one the reachability fence cannot help with (`core.insert_row` is
        # not reached from the pull entry points). A fixture with a top-level
        # import would model a siting that cannot exist.
        "a direct entry-gate call added to core, imported inside the function",
        "core.py",
        "async def insert_row_gated(db, table, values):\n"
        "    from gateway.routes.crm.pipeline import _require_entry_fields\n"
        "    _require_entry_fields(None, None, values)\n",
        (["core.py", "pipeline.py", "records.py"], False),
        (_SITED, False),
    ),
    (
        "an aliased entry-gate import in import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm.pipeline import _require_entry_fields as _gate\n"
        "def gated(status, values):\n"
        "    _gate(status, None, values)\n",
        (["import_zoho.py", "pipeline.py", "records.py"], False),
        (_SITED, False),
    ),
    (
        "a module-attribute entry-gate call in import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm import pipeline\n"
        "def gated(status, values):\n"
        "    pipeline._require_entry_fields(status, None, values)\n",
        (["import_zoho.py", "pipeline.py", "records.py"], False),
        (_SITED, False),
    ),
    (
        "the importer routed through the shared create seam",
        "import_zoho.py",
        # `apply_record` is the function the pull already runs, and the one
        # that already sets `values["status_id"]` — so this is the refactor as
        # it would actually be written, not a spare function nothing calls.
        "from gateway.routes.crm.records import _resolve_status\n"
        "async def apply_record(db, values):\n"
        "    await _resolve_status(db, values)\n",
        (_SITED, True),
        (_SITED, False),
    ),
    # ── the MOVE gate's shapes (WS-26h), pinned the same way ───────────────
    (
        # The case the TEXT fence got backwards: it went RED on this comment,
        # making deletion of the comment the cheapest way back to green.
        "a comment naming the move gate is not a call",
        "import_zoho.py",
        "# never calls apply_status_transition(...) — the pull carries whatever\n"
        "# stage Zoho has, and the gate would refuse live upstream rows\n"
        "GATE_DOC = 'see apply_status_transition(db, entity, record, ...)'\n",
        (_SITED, False),
        (_SITED, False),
    ),
    (
        "a direct move-gate call added to import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm.pipeline import apply_status_transition\n"
        "async def moved(db, record):\n"
        "    await apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        "a direct move-gate call added to sync_zoho",
        "sync_zoho.py",
        "from gateway.routes.crm.pipeline import apply_status_transition\n"
        "async def moved(db, record):\n"
        "    await apply_status_transition(db)\n",
        (_SITED, False),
        (["pipeline.py", "records.py", "sync_zoho.py"], False),
    ),
    (
        # ⚠️ Function-body import for the same reason as the entry gate's core
        # case — a top-level `from …pipeline import …` in the real `core.py`
        # raises `ImportError` on the circular `CLOSING_TYPES`. A fixture with
        # a top-level import would pin nothing about `_crm_imports`' whole-tree
        # walk, which is the F1 gap repair round 2 closed for the entry gate.
        "a direct move-gate call added to core, imported inside the function",
        "core.py",
        "async def insert_row_moved(db, table, values):\n"
        "    from gateway.routes.crm.pipeline import apply_status_transition\n"
        "    await apply_status_transition(db)\n",
        (_SITED, False),
        (["core.py", "pipeline.py", "records.py"], False),
    ),
    (
        # The case the TEXT fence was BLIND to: measured green on the real
        # package before this conversion.
        "an aliased move-gate import in import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm.pipeline import apply_status_transition as _move\n"
        "async def moved(db, record):\n"
        "    await _move(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        "a module-attribute move-gate call in import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm import pipeline\n"
        "async def moved(db, record):\n"
        "    await pipeline.apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        # The decisive one, and the second shape the TEXT fence was blind to:
        # `apply_record` is the function the pull already runs, so routing it
        # at the shared PATCH seam adds no call site anywhere and lands the
        # transition — and, through it, the entry gate — on the enabled loop.
        "the importer routed through the shared patch seam",
        "import_zoho.py",
        "from gateway.routes.crm.records import patch_record\n"
        "async def apply_record(db, values):\n"
        "    await patch_record(db, None, values)\n",
        (_SITED, True),
        (_SITED, True),
    ),
    # ── repair round 1: the spellings the first AST cut could not read ─────
    #
    # Every one of these was measured **green on the AST fence and RED on the
    # substring scan it replaced** — i.e. a capability the conversion removed.
    # The reviewer found the first; the rest share its cause. Each is a
    # one-line respelling of a case above, which is exactly what makes them
    # dangerous: `from .pipeline import …` is the same import, the same call
    # and the same live-system consequence as the absolute spelling, and
    # `pyproject.toml` selects no `TID` rules, so nothing else refuses one.
    (
        "a RELATIVE-import move-gate call in import_zoho",
        "import_zoho.py",
        "from .pipeline import apply_status_transition\n"
        "async def moved(db, record):\n"
        "    await apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        "a RELATIVE-import entry-gate call in import_zoho",
        "import_zoho.py",
        "from .pipeline import _require_entry_fields\n"
        "def gated(status, values):\n"
        "    _require_entry_fields(status, None, values)\n",
        (["import_zoho.py", "pipeline.py", "records.py"], False),
        (_SITED, False),
    ),
    (
        "a `from . import pipeline` move-gate call in import_zoho",
        "import_zoho.py",
        "from . import pipeline as _p\n"
        "async def moved(db, record):\n"
        "    await _p.apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        # ⚠️ Over-approximates on purpose: `exports` is every top-level def, so
        # a `*` is treated as binding underscore-prefixed names too, which a
        # real `import *` would not. "Maybe" answered as "yes" is the safe
        # direction for a fence guarding a running loop.
        "a STAR-import move-gate call in import_zoho",
        "import_zoho.py",
        "from .pipeline import *\n"
        "async def moved(db, record):\n"
        "    await apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        # A symbol re-exported through the package `__init__`. The first cut
        # read this as a MODULE import and resolved the call to nothing.
        "a package RE-EXPORT move-gate call in import_zoho",
        "import_zoho.py",
        "from gateway.routes.crm import apply_status_transition\n"
        "async def moved(db, record):\n"
        "    await apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        # Outside any `def`, so it runs at IMPORT time — and the first cut
        # walked only function bodies.
        "a MODULE-LEVEL move-gate call in import_zoho",
        "import_zoho.py",
        "from .pipeline import apply_status_transition\n"
        "_PRIMED = apply_status_transition(None)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        "a parent-package move-gate call in import_zoho",
        "import_zoho.py",
        "from .. import crm\n"
        "async def moved(db, record):\n"
        "    await crm.pipeline.apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
    (
        # The decisive case, respelled relatively. This is the one that makes
        # the regression an EXPOSURE rather than a wart: the shape the whole
        # reachability fence exists to catch, written in one line the fence
        # could not read.
        "the importer routed through the patch seam, RELATIVE import",
        "import_zoho.py",
        "from .records import patch_record\n"
        "async def apply_record(db, values):\n"
        "    await patch_record(db, None, values)\n",
        (_SITED, True),
        (_SITED, True),
    ),
    (
        # ⚠️ Reachable through the PUSH half only. This case is green unless
        # `_SYNC_ENTRY_POINTS` enters at the cycle rather than at `pull_phase`
        # — measured both ways.
        "the PUSH half routed through the patch seam",
        "sync_zoho.py",
        "from .records import patch_record\n"
        "async def apply_push_result(db, row):\n"
        "    await patch_record(db, None, {})\n",
        (_SITED, True),
        (_SITED, True),
    ),
    (
        # ── repair round 2 ────────────────────────────────────────────────
        # The case that pins `_merge_names`' UNION. Two imports bind the same
        # local name — the `TYPE_CHECKING` / `try: … except ImportError:`
        # fallback, a real shape and not a contrivance — and only one branch
        # names the gate. Union sees it; last-one-wins does not, and which
        # branch "wins" is an accident of `ast.walk` order.
        #
        # ⚠️ This was the ONE helper mutant that survived repair round 1:
        # `_merge_names` documented union semantics and nothing asserted them,
        # so `into[key] = value` left the whole file green.
        "the same local name bound by two imports (TYPE_CHECKING fallback)",
        "import_zoho.py",
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .pipeline import apply_status_transition\n"
        "else:\n"
        "    from .core import apply_status_transition\n"
        "async def _mv(db):\n"
        "    return await apply_status_transition(db)\n",
        (_SITED, False),
        (["import_zoho.py", "pipeline.py", "records.py"], False),
    ),
]


@pytest.mark.parametrize(
    ("name", "target", "extra", "entry", "move"),
    _FENCE_CASES,
    ids=[case[0] for case in _FENCE_CASES],
)
def test_the_siting_fences_see_the_shapes_they_claim_to_see(
    tmp_path, name: str, target: str | None, extra: str,
    entry: tuple[list[str], bool], move: tuple[list[str], bool],
) -> None:
    """Each gate's last case is the whole reason it has two fences: the
    importer routed through ``records._resolve_status`` (entry) or
    ``records.patch_record`` (move) adds NO call site — the file set is
    unchanged and green — and is caught only by reachability.

    The two "a comment naming the gate is not a call" cases and the two
    "aliased import" cases are the ones the superseded text-match fences got
    exactly backwards, in both directions.
    """
    package = tmp_path / "crm"
    package.mkdir()
    for filename, source in _FENCE_SOURCES.items():
        body = source + (extra if filename == target else "")
        (package / filename).write_text(body, encoding="utf-8")

    for gate, (expected_files, reachable) in (
        (_ENTRY_GATE, entry), (_MOVE_GATE, move),
    ):
        label = f"{name} · {gate[1]}"
        assert _gate_call_files(package, gate) == expected_files, label
        chain = _gate_reached_from(package, _SYNC_ENTRY_POINTS, gate)
        assert bool(chain) is reachable, (
            f"{label}: {' -> '.join(chain) or 'unreachable'}"
        )


def test_the_spec_quotes_the_real_number_of_fence_cases() -> None:
    """The fence's own fence, added in repair round 2 (R7).

    Two consecutive rounds found a **stale case count in `crm_app.md`** and
    nothing failed — including in the §10 block an agent runs at dispatch,
    where the number annotates a command whose real output disagreed with it.
    A count nobody checks is a comment, so this checks it.

    The spec writes the count in one canonical form, ``N-case synthetic
    self-test``, everywhere it states it; every occurrence must equal the
    length of :data:`_FENCE_CASES`.
    """
    import re
    from pathlib import Path

    spec = (
        Path(__file__).resolve().parents[2]
        / "project-docs" / "specs" / "crm_app.md"
    ).read_text(encoding="utf-8")
    quoted = re.findall(r"(\d+)-case synthetic self-test", spec)

    # Its own precondition, because a regex that matches nothing agrees with
    # everything — the WS-26i-export vacuous-fence lesson, applied here.
    assert quoted, (
        "crm_app.md no longer states the count in the pinned form "
        "'N-case synthetic self-test' — restore it or this fence is vacuous"
    )
    assert [int(n) for n in quoted] == [len(_FENCE_CASES)] * len(quoted), (
        f"crm_app.md quotes {quoted}; the suite has {len(_FENCE_CASES)} cases"
    )


# ── WS-26h2 · the create gate's own shape ───────────────────────────────────
#
# The route-level behaviour is in `test_crm_routes.py` (and the convert half in
# `test_crm_convert.py`). What is asserted here is the shape `records.py` hands
# the gate: "there is no existing row" as a first-class argument rather than a
# `None` that happens to make `getattr` answer the same way.


def _stage(name: str = "Proposal", *fields: str):
    """A status row as `require_row` would return it, carrying requirements."""
    from types import SimpleNamespace

    return SimpleNamespace(name=name, type="ongoing", required_fields=list(fields))


def test_with_no_existing_row_only_the_payload_can_satisfy_a_requirement() -> None:
    with pytest.raises(HTTPException) as exc:
        crm_pipeline._require_entry_fields(
            _stage("Proposal", "amount"),
            crm_pipeline.NO_EXISTING_RECORD,
            {"name": "Printer order"},
        )

    assert exc.value.status_code == 422
    assert "amount" in str(exc.value.detail)

    # …and the same call with the field present is silent.
    crm_pipeline._require_entry_fields(
        _stage("Proposal", "amount"),
        crm_pipeline.NO_EXISTING_RECORD,
        {"name": "Printer order", "amount": 400000},
    )


def test_the_no_row_shape_is_a_distinct_object_not_none() -> None:
    """Passing `None` would work by coincidence — `getattr(None, "amount",
    None)` is also `None` — and would read forever after as somebody having
    forgotten the record. The create case is something the gate is TOLD."""
    assert crm_pipeline.NO_EXISTING_RECORD is not None
    assert repr(crm_pipeline.NO_EXISTING_RECORD) == "NO_EXISTING_RECORD"


def test_none_is_refused_rather_than_read_as_no_row() -> None:
    """The fence that makes "first-class shape" more than a comment.

    `getattr(None, field, None)` answers None for every field, so a caller that
    lost the record on the way here would silently refuse a move it should have
    allowed — the sentinel and a bug would be indistinguishable. They are not.
    """
    with pytest.raises(TypeError):
        crm_pipeline._require_entry_fields(_stage("Proposal", "amount"), None, {})


def test_the_no_row_sentinel_can_never_be_given_a_field() -> None:
    """It is a module-level singleton; one that could carry an attribute would
    be a global that could be made to satisfy somebody's requirement."""
    with pytest.raises(AttributeError):
        crm_pipeline.NO_EXISTING_RECORD.amount = 400000  # type: ignore[attr-defined]


def test_a_decimal_zero_is_a_value_on_the_create_path_too() -> None:
    """WS-26h2 done-when 6, the half `DealIn` cannot express: `amount` is typed
    `float | None`, so a `Decimal` never survives the model — but `_is_blank`
    is shared with the move path, where a real `NUMERIC` column returns
    `Decimal('0.00')`, and "same semantics" is asserted rather than assumed."""
    from decimal import Decimal

    crm_pipeline._require_entry_fields(
        _stage("Proposal", "amount"),
        crm_pipeline.NO_EXISTING_RECORD,
        {"amount": Decimal("0.00")},
    )


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_text_is_absent_on_the_create_path_too(blank: str) -> None:
    with pytest.raises(HTTPException) as exc:
        crm_pipeline._require_entry_fields(
            _stage("Proposal", "owner_email"),
            crm_pipeline.NO_EXISTING_RECORD,
            {"owner_email": blank},
        )

    assert exc.value.status_code == 422
