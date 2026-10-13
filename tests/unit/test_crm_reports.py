"""CRM · forecast & funnel reports — WS-26g.

Spec: ``project-docs/specs/crm_app.md`` §5.1 system 2 · ticket WS-26g.

Hermetic: no Postgres, no network. The route functions are called directly with
``core._get_db`` monkeypatched onto each SUT submodule (the ``_crm_fakes``
convention).

**How the aggregates are read, and why it is not a mirror agreeing with
itself.** ``reports.py`` emits no ``GROUP BY``: every aggregate is a per-key
query in the shape ``pipeline.get_pipeline`` already uses, which
``_crm_fakes.FakeCrmDB`` answers through ``_weighted`` — and ``_weighted``
reads the summed EXPRESSION out of the statement text (which column is summed,
which column falls back to which bound parameter, what it is divided by) rather
than reproducing it in Python. So perturbing ``core.WEIGHTED_SQL`` changes the
answer here. The set-shaped questions (visited sets, medians, orphan names) are
answered in Python by the route over rows the fake returns verbatim, so those
tests exercise the real code and nothing about them is restated in the fake.

⚠️ No aggregation answer is hard-coded into the fake, and none may be: that is
what would turn "fixture tests proving the math" into a mirror.
"""

from __future__ import annotations

import inspect
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import reports as crm_reports

from tests.unit._crm_fakes import FakeCrmDB, bind_db, crm_user

USER = crm_user()

CRM_MODULES = (crm_core, crm_reports)

#: The one fixture both runners read (`board.test.ts` reads the same path).
PARITY_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures"
    / "crm_weighted_parity.json"
)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> FakeCrmDB:
    fake = FakeCrmDB()
    bind_db(monkeypatch, fake, CRM_MODULES)
    return fake


# ── Seeding helpers ─────────────────────────────────────────────────────────

def stage(
    db: FakeCrmDB, name: str, position: int, *,
    kind: str = "open", probability: int | None = 0,
) -> Any:
    return db.seed(
        "crm_deal_statuses",
        name=name, position=position, type=kind, probability=probability,
    )


def deal(db: FakeCrmDB, lane: Any, **over: Any) -> Any:
    return db.seed("crm_deals", status_id=str(lane.id), **over)


def change(
    db: FakeCrmDB, record: Any, *,
    frm: str | None, to: str, dwell: int | None = None,
    entity_type: str = "deal",
) -> Any:
    return db.seed(
        "crm_status_changes",
        entity_type=entity_type,
        entity_id=str(record.id),
        from_status=frm,
        to_status=to,
        changed_by="vjvarada@fracktal.in",
        dwell_seconds=dwell,
    )


def seeded_board(db: FakeCrmDB) -> dict[str, Any]:
    """144's shape in miniature: four lanes across all the types that matter."""
    return {
        "qualification": stage(db, "Qualification", 10, probability=10),
        "proposal": stage(db, "Proposal", 30, kind="ongoing", probability=50),
        "won": stage(db, "Closed Won", 50, kind="won", probability=100),
        "lost": stage(db, "Closed Lost", 60, kind="lost", probability=0),
    }


# ── Block 1 · pipeline by stage ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_report_totals_each_open_lane_and_the_board(
    db: FakeCrmDB,
) -> None:
    lanes = seeded_board(db)
    deal(db, lanes["qualification"], amount=100000, probability=10)
    deal(db, lanes["qualification"], amount=200000, probability=None)
    deal(db, lanes["proposal"], amount=400000, probability=25)

    report = await crm_reports.pipeline_report(user=USER)

    by_name = {s.status.name: s for s in report.stages}
    assert set(by_name) == {"Qualification", "Proposal"}
    # 100000*10% + 200000*(inherited 10%) = 10000 + 20000
    assert by_name["Qualification"].count == 2
    assert by_name["Qualification"].amount == 300000
    assert by_name["Qualification"].weighted == 30000
    assert by_name["Proposal"].weighted == 100000
    assert report.count == 3
    assert report.amount == 700000
    assert report.weighted == 130000


@pytest.mark.asyncio
async def test_pipeline_report_omits_closed_and_on_hold_lanes(
    db: FakeCrmDB,
) -> None:
    """Won is booked revenue, lost is nothing, on_hold is nobody's forecast."""
    lanes = seeded_board(db)
    parked = stage(db, "Parked", 40, kind="on_hold", probability=40)
    deal(db, lanes["won"], amount=900000, probability=100)
    deal(db, lanes["lost"], amount=800000, probability=0)
    deal(db, parked, amount=700000, probability=40)

    report = await crm_reports.pipeline_report(user=USER)

    assert [s.status.name for s in report.stages] == ["Qualification", "Proposal"]
    assert report.weighted == 0.0
    # …and the ₹ on those lanes is not quietly folded into the pipeline either.
    assert report.amount == 0.0


@pytest.mark.asyncio
async def test_pipeline_report_is_zeros_on_an_empty_crm(db: FakeCrmDB) -> None:
    report = await crm_reports.pipeline_report(user=USER)
    assert report.stages == []
    assert (report.count, report.amount, report.weighted) == (0, 0, 0)


@pytest.mark.asyncio
async def test_pipeline_report_never_writes(db: FakeCrmDB) -> None:
    """The whole module is a read. A report that writes is a migration."""
    seeded_board(db)
    await crm_reports.pipeline_report(user=USER)
    await crm_reports.funnel_report(user=USER)
    await crm_reports.win_loss_report(user=USER)
    await crm_reports.owner_leaderboard(user=USER)
    # H2: `tenant_session` commits an EMPTY transaction on clean exit even for
    # reads, so "writes nothing" is asserted on the statements themselves.
    for statement in db.statements:
        assert statement.split(None, 1)[0].upper() == "SELECT", statement


# ── Block 1 · the cross-language parity fixture ─────────────────────────────

def parity_rows() -> list[dict[str, Any]]:
    return json.loads(PARITY_FIXTURE.read_text(encoding="utf-8"))["rows"]


def test_the_parity_fixture_exists_and_is_the_shape_both_runners_read() -> None:
    """A missing/renamed fixture must fail LOUDLY on both sides, not skip.

    ``board.test.ts`` reads the same path six directories up from itself; a
    fixture that quietly disappeared would leave one runner asserting nothing
    while the other stayed green, which is the failure mode a shared fixture
    exists to remove.
    """
    rows = parity_rows()
    assert len(rows) >= 10
    for row in rows:
        assert set(row) == {
            "name", "amount", "deal_probability", "stage_probability",
            "ui_probability", "stage_type", "expected_weighted",
        }
        assert row["stage_type"] in crm_core.STATUS_TYPES
    # CRM-C3 (§20.6 item 11): the two kinds of overlay case are present.
    overlaid = [r for r in rows if r["ui_probability"] is not None]
    assert any(r["deal_probability"] is None for r in overlaid)
    assert any(r["deal_probability"] is not None for r in overlaid)


@pytest.mark.asyncio
@pytest.mark.parametrize("row", parity_rows(), ids=lambda r: r["name"])
async def test_weighted_parity_against_the_emitted_sql(
    db: FakeCrmDB, row: dict[str, Any],
) -> None:
    """Every fixture row, through the real statement.

    This is the SQL half of the parity pair; ``board.test.ts`` runs the same
    rows through ``weightedDeal``/``weightedRows``. The number is read out of
    ``_lane_totals``, which is where the open/ongoing filter is applied, so a
    won/lost/on_hold row asserts the exclusion rather than skipping.
    """
    seeded = stage(
        db, "Fixture lane", 10,
        kind=row["stage_type"], probability=row["stage_probability"],
    )
    db.rows("crm_deal_statuses")[-1]["ui_probability"] = row["ui_probability"]
    deal(db, seeded, amount=row["amount"], probability=row["deal_probability"])
    # CRM-C3: the lane comes through the one effective SELECT list, as in
    # `reports._deal_stages`, so the overlay reaches `:stage_probability`.
    (lane,) = await crm_core.read_statuses(db, "crm_deal_statuses")

    totals = await crm_reports._lane_totals(db, lane)

    assert totals.weighted == pytest.approx(row["expected_weighted"])


def test_the_weighted_formula_has_exactly_one_definition() -> None:
    """``reports.py`` imports the expression; it must never retype it.

    A second copy would defeat ``_crm_fakes._WEIGHTED_SUM_RE``, which reads the
    formula out of the statement text precisely so a drifted copy changes the
    tests' answer instead of agreeing with itself.
    """
    source = inspect.getsource(crm_reports)
    # The plain ₹ total (`SUM(amount)`) is written out per statement like
    # everywhere else; the WEIGHTED expression — the one with the inheritance
    # COALESCE in it — is the one that must exist exactly once in the tree.
    assert "SUM(amount *" not in source
    assert "COALESCE(probability" not in source
    assert "WEIGHTED_SQL" in source
    # It lives beside WEIGHTED_TYPES in the leaf, and `pipeline` still re-exports
    # it, so nothing that imported it from there had to move.
    from gateway.routes.crm import pipeline as crm_pipeline

    assert crm_pipeline.WEIGHTED_SQL is crm_core.WEIGHTED_SQL
    assert "COALESCE(probability, :stage_probability)" in crm_core.WEIGHTED_SQL


# ── Block 2 · the funnel ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entered_is_the_visited_set_union_including_the_current_stage(
    db: FakeCrmDB,
) -> None:
    """The union rule, named (WS-26g done-when).

    Three deals, each in a different relationship to the log:

    * ``imported`` has NEVER moved — zero log rows, which is the state all 551
      imported deals are in. It is in the funnel ONLY through its current stage.
    * ``moved`` went Qualification → Proposal, so Qualification appears only as
      a ``from_status`` — a to-only reading loses it.
    * ``jumped`` went Qualification → Closed Won, skipping Proposal, and must
      NOT be backfilled into the lane it never entered.
    """
    lanes = seeded_board(db)
    imported = deal(db, lanes["qualification"], amount=100000)
    moved = deal(db, lanes["proposal"], amount=200000)
    change(db, moved, frm="Qualification", to="Proposal", dwell=86_400)
    jumped = deal(db, lanes["won"], amount=300000)
    change(db, jumped, frm="Qualification", to="Closed Won", dwell=172_800)

    report = await crm_reports.funnel_report(user=USER)
    entered = {s.status.name: s.entered for s in report.stages}

    assert entered["Qualification"] == 3   # all three, two of them via from_status
    assert entered["Proposal"] == 1        # `moved` only — no backfill for `jumped`
    assert entered["Closed Won"] == 1
    assert entered["Closed Lost"] == 0
    assert report.deals == 3
    assert report.transitions == 2
    assert str(imported.id)  # the imported deal is counted with no log row at all


@pytest.mark.asyncio
async def test_a_board_of_never_moved_deals_still_reports_a_funnel(
    db: FakeCrmDB,
) -> None:
    """The 551-imported-deals scenario, which a to_status reading answers 0 to."""
    lanes = seeded_board(db)
    for _ in range(5):
        deal(db, lanes["qualification"], amount=100000)

    report = await crm_reports.funnel_report(user=USER)

    assert report.transitions == 0
    assert {s.status.name: s.entered for s in report.stages}["Qualification"] == 5


@pytest.mark.asyncio
async def test_conversion_forward_counts_any_later_stage_not_the_next_one(
    db: FakeCrmDB,
) -> None:
    lanes = seeded_board(db)
    jumped = deal(db, lanes["won"], amount=300000)
    change(db, jumped, frm="Qualification", to="Closed Won")
    stuck = deal(db, lanes["qualification"], amount=100000)

    report = await crm_reports.funnel_report(user=USER)
    by_name = {s.status.name: s for s in report.stages}

    # Two deals visited Qualification; one of them got past it — to a stage two
    # lanes further on, which "went to exactly N+1" would have missed.
    assert by_name["Qualification"].entered == 2
    assert by_name["Qualification"].advanced == 1
    assert by_name["Qualification"].conversion_forward == 50.0
    # Nothing has ever got past the last lane, and that is 0%, not an error.
    assert by_name["Closed Lost"].conversion_forward == 0.0
    assert str(stuck.id)


@pytest.mark.asyncio
async def test_median_dwell_is_grouped_by_from_status_the_stage_being_left(
    db: FakeCrmDB,
) -> None:
    """The dwell key, named (WS-26g done-when).

    Every row below LEAVES Qualification for Proposal. The time belongs to
    Qualification — the stage that was occupied — and a ``to_status`` grouping
    would label all of it "Proposal", plausibly and off by exactly one lane.
    """
    lanes = seeded_board(db)
    for seconds in (86_400, 3 * 86_400, 9 * 86_400):
        moved = deal(db, lanes["proposal"], amount=100000)
        change(db, moved, frm="Qualification", to="Proposal", dwell=seconds)

    report = await crm_reports.funnel_report(user=USER)
    by_name = {s.status.name: s for s in report.stages}

    assert by_name["Qualification"].median_dwell_days == 3.0
    assert by_name["Qualification"].dwell_samples == 3
    # Proposal is where they all ARE; nobody has left it, so it has no
    # measured departure — and that is None, never 0.
    assert by_name["Proposal"].median_dwell_days is None
    assert by_name["Proposal"].dwell_samples == 0


@pytest.mark.asyncio
async def test_null_dwell_seconds_are_excluded_rather_than_read_as_zero(
    db: FakeCrmDB,
) -> None:
    """A first transition stamps NULL by design: no earlier timestamp exists."""
    lanes = seeded_board(db)
    first = deal(db, lanes["proposal"], amount=100000)
    change(db, first, frm="Qualification", to="Proposal", dwell=None)
    second = deal(db, lanes["proposal"], amount=100000)
    change(db, second, frm="Qualification", to="Proposal", dwell=4 * 86_400)

    report = await crm_reports.funnel_report(user=USER)
    by_name = {s.status.name: s for s in report.stages}

    assert by_name["Qualification"].dwell_samples == 1
    assert by_name["Qualification"].median_dwell_days == 4.0


@pytest.mark.asyncio
async def test_a_null_from_status_names_no_stage_to_attribute_time_to(
    db: FakeCrmDB,
) -> None:
    lanes = seeded_board(db)
    born = deal(db, lanes["proposal"], amount=100000)
    change(db, born, frm=None, to="Proposal", dwell=5 * 86_400)

    report = await crm_reports.funnel_report(user=USER)

    assert all(s.dwell_samples == 0 for s in report.stages)


@pytest.mark.asyncio
async def test_the_funnel_reads_deal_history_only(db: FakeCrmDB) -> None:
    """``entity_type = 'deal'`` — the two vocabularies are independent tables.

    The lead pipeline here carries a status called ``Qualification`` too (the
    Zoho importer mints names into both), so an unfiltered read would count a
    LEAD's history in the deal funnel — under a name that happens to collide.
    """
    lanes = seeded_board(db)
    deal(db, lanes["qualification"], amount=100000)
    lead = db.seed("crm_leads", lead_name="Bosch", status_id=None)
    change(db, lead, frm="Qualification", to="Proposal", dwell=99 * 86_400,
           entity_type="lead")

    report = await crm_reports.funnel_report(user=USER)
    by_name = {s.status.name: s for s in report.stages}

    assert report.transitions == 0
    assert by_name["Qualification"].entered == 1
    assert by_name["Qualification"].advanced == 0
    assert by_name["Qualification"].dwell_samples == 0
    assert by_name["Proposal"].entered == 0


@pytest.mark.asyncio
async def test_a_row_stamped_lead_is_not_deal_history_whatever_id_it_carries(
    db: FakeCrmDB,
) -> None:
    """``entity_type`` is the column that says WHICH VOCABULARY the names are in.

    The test above passes for a second reason as well as the filter — a lead's
    ``entity_id`` is a lead's id, so keying the funnel through deal ids happens
    to exclude it too. That coincidence is not a guarantee:
    ``crm_status_changes.entity_id`` has **no foreign key** (migration 144, so
    the log outlives the row it describes), so nothing in the schema ties an id
    to a table and a mis-stamped write is a live possibility for any future
    logger.

    So the row below is stamped ``lead`` against a DEAL's id, which is exactly
    the state the filter exists to reject. With the filter, it is not read at
    all. Without it, the lead pipeline's ``Discovery`` — a stage no deal has
    ever been in — appears in the deal funnel's ``unmatched``, and its dwell is
    attributed to a deal stage.
    """
    lanes = seeded_board(db)
    subject = deal(db, lanes["proposal"], amount=100000)
    change(db, subject, frm="Discovery", to="Nurturing", dwell=77 * 86_400,
           entity_type="lead")

    report = await crm_reports.funnel_report(user=USER)

    assert report.transitions == 0
    assert report.unmatched == []
    assert {s.status.name: s.entered for s in report.stages}["Proposal"] == 1
    assert sum(s.dwell_samples for s in report.stages) == 0


def test_the_funnel_reads_the_change_log_filtered_by_entity_type() -> None:
    """The filter, pinned against the statement's own text.

    Belt to the behavioural case's braces, and cheap: the property is "this
    query names ``entity_type``", and a query that stopped naming it would be
    reading 1,516 leads' history on every page load even in the runs where the
    numbers happen to come out the same.
    """
    source = inspect.getsource(crm_reports.funnel_report)
    assert "FROM crm_status_changes WHERE entity_type = :entity_type" in source
    assert crm_reports.DEAL_ENTITY_TYPE == "deal"


@pytest.mark.asyncio
async def test_a_renamed_lane_is_reported_as_unmatched_never_dropped(
    db: FakeCrmDB,
) -> None:
    """f2's settings grid renames lanes; the log keeps the old name forever."""
    lanes = seeded_board(db)
    moved = deal(db, lanes["proposal"], amount=200000)
    change(db, moved, frm="Price Quote", to="Proposal", dwell=2 * 86_400)
    again = deal(db, lanes["proposal"], amount=100000)
    change(db, again, frm="Price Quote", to="Proposal", dwell=6 * 86_400)

    report = await crm_reports.funnel_report(user=USER)

    assert [(u.name, u.deals, u.mentions) for u in report.unmatched] == [
        ("Price Quote", 2, 2),
    ]
    # The orphan carries no position, so it cannot answer "did this deal get
    # past stage N" and is not silently counted as "did not advance" either.
    by_name = {s.status.name: s for s in report.stages}
    assert by_name["Proposal"].entered == 2
    assert sum(s.dwell_samples for s in report.stages) == 0


@pytest.mark.asyncio
async def test_the_funnel_is_zeros_on_an_empty_crm(db: FakeCrmDB) -> None:
    report = await crm_reports.funnel_report(user=USER)
    assert report.stages == []
    assert (report.deals, report.transitions, report.unmatched) == (0, 0, [])


@pytest.mark.asyncio
async def test_log_rows_for_a_deleted_deal_are_not_in_anybodys_funnel(
    db: FakeCrmDB,
) -> None:
    """``crm_status_changes`` has no FK to ``crm_deals`` — the log outlives it."""
    lanes = seeded_board(db)
    ghost = db.seed("crm_ghosts", id="00000000-0000-0000-0000-0000000000ff")
    change(db, ghost, frm="Qualification", to="Proposal", dwell=42 * 86_400)
    deal(db, lanes["qualification"], amount=100000)

    report = await crm_reports.funnel_report(user=USER)

    assert report.transitions == 0
    assert report.deals == 1
    assert {s.status.name: s.entered for s in report.stages}["Proposal"] == 0


# ── Block 3 · win / loss ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_win_loss_reads_the_trailing_window(db: FakeCrmDB) -> None:
    lanes = seeded_board(db)
    recent = crm_core.now() - timedelta(days=10)
    stale = crm_core.now() - timedelta(days=200)
    deal(db, lanes["won"], amount=500000,
         created_at=recent - timedelta(days=20), closed_at=recent)
    deal(db, lanes["won"], amount=300000,
         created_at=recent - timedelta(days=40), closed_at=recent)
    deal(db, lanes["lost"], amount=100000,
         created_at=recent - timedelta(days=30), closed_at=recent)
    deal(db, lanes["won"], amount=9_000_000,
         created_at=stale - timedelta(days=5), closed_at=stale)

    report = await crm_reports.win_loss_report(user=USER)

    assert report.window_days == crm_reports.WINDOW_DAYS == 90
    assert (report.won, report.lost) == (2, 1)
    assert report.won_amount == 800000
    assert report.lost_amount == 100000
    assert report.win_rate == pytest.approx(66.7)
    assert report.average_cycle_days == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_the_window_is_closed_at_both_ends_inclusive(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FUTURE ``closed_at`` is not a close — WS-26f f4's proxy makes them real.

    f4 backfills ``closed_at`` from Zoho's ``Closing_Date``, which is a
    *forecast* date, so imported deals routinely carry one in the future. With
    a lower bound only, the deal below counts as lost in the last 90 days,
    moves ``win_rate``, and adds its whole forward span to the cycle average —
    a report that gets worse the moment the owner runs the repair that was
    supposed to make it work.

    Both bounds are INCLUSIVE, and the boundary rows below say so rather than
    leaving it to be read off an operator. The clock is frozen because the
    route reads it AFTER the fixture does: a row seeded exactly on the boundary
    is otherwise a few hundred microseconds outside the window the route then
    computes, and the test would be asserting clock skew rather than the rule.
    """
    until = crm_core.now()
    monkeypatch.setattr(crm_reports, "now", lambda: until)
    lanes = seeded_board(db)
    deal(db, lanes["won"], amount=500000,
         created_at=until - timedelta(days=40), closed_at=until)
    deal(db, lanes["won"], amount=200000,
         created_at=until - timedelta(days=100),
         closed_at=until - timedelta(days=crm_reports.WINDOW_DAYS))
    # The one the missing upper bound let in: dated a quarter from now.
    deal(db, lanes["lost"], amount=9_000_000,
         created_at=until - timedelta(days=5),
         closed_at=until + timedelta(days=90))

    report = await crm_reports.win_loss_report(user=USER)

    assert report.won == 2          # both boundary rows are inside
    assert report.lost == 0         # the future-dated deal is not a close
    assert report.win_rate == 100.0
    assert report.won_amount == 700000
    assert report.lost_amount == 0.0
    # The two cycles are 40d (created -40d, closed today) and 10d (created
    # -100d, closed on the boundary at -90d) → 25. The excluded deal's cycle
    # would be +95d, so the missing upper bound would have reported 48.3.
    assert report.average_cycle_days == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_the_leaderboards_won_window_is_closed_at_both_ends_too(
    db: FakeCrmDB,
) -> None:
    """The two blocks sit on one screen and must not print two "won" numbers."""
    lanes = seeded_board(db)
    until = crm_core.now()
    deal(db, lanes["won"], amount=400000, owner_email="vj@fracktal.in",
         closed_at=until - timedelta(days=5))
    deal(db, lanes["won"], amount=9_000_000, owner_email="vj@fracktal.in",
         closed_at=until + timedelta(days=90))

    leaderboard = await crm_reports.owner_leaderboard(user=USER)
    win_loss = await crm_reports.win_loss_report(user=USER)

    assert leaderboard.owners[0].won_amount == 400000
    assert leaderboard.owners[0].won_amount == win_loss.won_amount


@pytest.mark.asyncio
async def test_a_null_closed_at_falls_outside_the_window_and_is_counted(
    db: FakeCrmDB,
) -> None:
    """The imported-deals truth: f4's backfill is an owner gate and has not run.

    NULL must never be read as "closed today" and must never raise — but a
    board with closed deals on it and a 0% win rate needs to say WHY, so the
    undated ones are counted rather than merely dropped.
    """
    lanes = seeded_board(db)
    deal(db, lanes["won"], amount=500000, created_at=None, closed_at=None)
    deal(db, lanes["lost"], amount=200000, created_at=None, closed_at=None)
    deal(db, lanes["qualification"], amount=100000, closed_at=None)

    report = await crm_reports.win_loss_report(user=USER)

    assert (report.won, report.lost) == (0, 0)
    assert report.win_rate == 0.0
    assert report.won_amount == 0.0
    assert report.average_cycle_days is None
    # The two closed lanes only — an open deal has no close date to be missing.
    assert report.closed_without_date == 2


@pytest.mark.asyncio
async def test_a_closed_deal_with_no_created_at_never_invents_a_cycle(
    db: FakeCrmDB,
) -> None:
    lanes = seeded_board(db)
    closed = crm_core.now() - timedelta(days=5)
    deal(db, lanes["won"], amount=500000, created_at=None, closed_at=closed)

    report = await crm_reports.win_loss_report(user=USER)

    assert report.won == 1
    assert report.average_cycle_days is None


@pytest.mark.asyncio
async def test_lost_deals_with_no_reason_land_in_a_named_bucket(
    db: FakeCrmDB,
) -> None:
    """"Complete by construction" is FALSE: the importer bypasses both gates."""
    lanes = seeded_board(db)
    budget = db.seed("crm_lost_reasons", label="Budget", position=10)
    db.seed("crm_lost_reasons", label="Timing", position=20)
    closed = crm_core.now() - timedelta(days=3)
    deal(db, lanes["lost"], amount=100000, closed_at=closed,
         lost_reason_id=str(budget.id))
    deal(db, lanes["lost"], amount=250000, closed_at=closed,
         lost_reason_id=str(budget.id))
    deal(db, lanes["lost"], amount=400000, closed_at=closed, lost_reason_id=None)

    report = await crm_reports.win_loss_report(user=USER)

    assert [(r.label, r.deals, r.amount, r.attributed) for r in report.lost_reasons] == [
        ("Budget", 2, 350000, True),
        (crm_reports.UNATTRIBUTED_LOST, 1, 400000, False),
    ]
    # The breakdown adds up to the `lost` figure printed above it — which is
    # exactly what dropping the unattributed rows would break.
    assert sum(r.deals for r in report.lost_reasons) == report.lost == 3


@pytest.mark.asyncio
async def test_a_reason_deleted_out_from_under_a_deal_is_unattributed(
    db: FakeCrmDB,
) -> None:
    """``lost_reason_id`` is ``ON DELETE SET NULL`` — a misclick strips it."""
    lanes = seeded_board(db)
    closed = crm_core.now() - timedelta(days=3)
    deal(db, lanes["lost"], amount=100000, closed_at=closed,
         lost_reason_id="00000000-0000-0000-0000-00000000dead")

    report = await crm_reports.win_loss_report(user=USER)

    assert [r.label for r in report.lost_reasons] == [crm_reports.UNATTRIBUTED_LOST]


@pytest.mark.asyncio
async def test_win_loss_is_zeros_on_an_empty_crm(db: FakeCrmDB) -> None:
    report = await crm_reports.win_loss_report(user=USER)
    assert (report.won, report.lost, report.win_rate) == (0, 0, 0.0)
    assert report.average_cycle_days is None
    assert report.closed_without_date == 0
    assert report.lost_reasons == []


# ── Block 4 · owner leaderboard ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_leaderboard_ranks_owners_by_weighted_pipeline(
    db: FakeCrmDB,
) -> None:
    lanes = seeded_board(db)
    closed = crm_core.now() - timedelta(days=7)
    deal(db, lanes["proposal"], amount=400000, probability=50,
         owner_email="priya@fracktal.in")
    deal(db, lanes["qualification"], amount=100000, probability=None,
         owner_email="priya@fracktal.in")
    deal(db, lanes["proposal"], amount=100000, probability=50,
         owner_email="vj@fracktal.in")
    deal(db, lanes["won"], amount=900000, owner_email="vj@fracktal.in",
         closed_at=closed)

    report = await crm_reports.owner_leaderboard(user=USER)

    assert [r.owner_email for r in report.owners] == [
        "priya@fracktal.in", "vj@fracktal.in",
    ]
    priya, vj = report.owners
    assert priya.open_deals == 2
    assert priya.weighted == 210000     # 400000*50% + 100000*(inherited 10%)
    assert priya.won_amount == 0.0
    assert vj.open_deals == 1
    assert vj.weighted == 50000
    assert vj.won_amount == 900000
    assert report.omitted == 0


@pytest.mark.asyncio
async def test_owner_matching_is_case_insensitive_on_both_sides(
    db: FakeCrmDB,
) -> None:
    """R10 — the same address in two casings is one person, not two rows."""
    lanes = seeded_board(db)
    deal(db, lanes["proposal"], amount=100000, probability=50,
         owner_email="VJ@Fracktal.in")
    deal(db, lanes["proposal"], amount=300000, probability=50,
         owner_email="vj@fracktal.in")

    report = await crm_reports.owner_leaderboard(user=USER)

    assert [r.owner_email for r in report.owners] == ["vj@fracktal.in"]
    assert report.owners[0].open_deals == 2
    assert report.owners[0].weighted == 200000


@pytest.mark.asyncio
async def test_a_padded_address_is_the_same_owner_in_the_sql_and_in_the_tally(
    db: FakeCrmDB,
) -> None:
    """The bucket key and the aggregate predicate must normalise identically.

    ``_owners`` groups on ``.strip().lower()``, so both rows below are ONE
    owner in the tally. If the predicate is only ``lower(owner_email)``, the
    aggregate matches the unpadded row alone: the row under-reports, the padded
    deal lands in NO bucket, ``omitted`` still says 0, and "By owner" quietly
    disagrees with "Forecast by stage" about the same board.

    The invariant, asserted rather than the intermediate figures: every deal is
    counted exactly once across the leaderboard, and the leaderboard's weighted
    total equals the forecast's.
    """
    lanes = seeded_board(db)
    deal(db, lanes["proposal"], amount=200000, probability=50,
         owner_email="vj@fracktal.in")
    deal(db, lanes["proposal"], amount=100000, probability=50,
         owner_email="  vj@fracktal.in ")
    deal(db, lanes["proposal"], amount=400000, probability=50,
         owner_email="VJ@FRACKTAL.IN")

    report = await crm_reports.owner_leaderboard(user=USER)
    forecast = await crm_reports.pipeline_report(user=USER)

    assert [r.owner_email for r in report.owners] == ["vj@fracktal.in"]
    assert report.owners[0].open_deals == 3
    assert report.owners[0].weighted == 350000
    assert report.omitted == 0
    assert sum(r.open_deals for r in report.owners) == forecast.count
    assert sum(r.weighted for r in report.owners) == forecast.weighted


@pytest.mark.asyncio
async def test_deals_nobody_owns_get_their_own_row(db: FakeCrmDB) -> None:
    """A NULL owner cannot match ``lower(owner_email) = :owner``.

    Without its own predicate the bucket reports zeros for deals that are
    visibly on the board, and the leaderboard's total stops matching the
    forecast's.
    """
    lanes = seeded_board(db)
    deal(db, lanes["proposal"], amount=200000, probability=50, owner_email=None)
    deal(db, lanes["proposal"], amount=300000, probability=50, owner_email=None)
    deal(db, lanes["proposal"], amount=100000, probability=50,
         owner_email="vj@fracktal.in")

    report = await crm_reports.owner_leaderboard(user=USER)

    unassigned = next(r for r in report.owners if r.owner_email is None)
    assert unassigned.open_deals == 2
    assert unassigned.weighted == 250000


@pytest.mark.asyncio
async def test_a_blank_owner_is_its_own_key_because_sql_cannot_fold_it(
    db: FakeCrmDB,
) -> None:
    """NULL and '' are two buckets on purpose — see ``_owners``' warning.

    Folding them would be a tally the per-owner aggregate cannot reproduce (it
    can bind ``IS NULL`` or ``lower(owner_email) = :owner``, not an OR), so the
    row's count would come from one rule and its ₹ from another. The UI is
    where they both become the word "Unassigned".
    """
    lanes = seeded_board(db)
    deal(db, lanes["proposal"], amount=200000, probability=50, owner_email=None)
    deal(db, lanes["proposal"], amount=100000, probability=50, owner_email="")

    report = await crm_reports.owner_leaderboard(user=USER)

    assert sorted(str(r.owner_email) for r in report.owners) == ["", "None"]
    # Whichever way they are labelled, every deal is counted exactly once.
    assert sum(r.open_deals for r in report.owners) == 2
    assert sum(r.weighted for r in report.owners) == 150000


@pytest.mark.asyncio
async def test_won_amount_uses_the_same_window_the_win_loss_block_does(
    db: FakeCrmDB,
) -> None:
    lanes = seeded_board(db)
    deal(db, lanes["won"], amount=500000, owner_email="vj@fracktal.in",
         closed_at=crm_core.now() - timedelta(days=200))
    deal(db, lanes["won"], amount=700000, owner_email="vj@fracktal.in",
         closed_at=None)

    report = await crm_reports.owner_leaderboard(user=USER)

    assert report.owners[0].won_amount == 0.0


@pytest.mark.asyncio
async def test_the_leaderboard_is_empty_rather_than_erroring_on_an_empty_crm(
    db: FakeCrmDB,
) -> None:
    report = await crm_reports.owner_leaderboard(user=USER)
    assert report.owners == []
    assert report.omitted == 0
    assert report.window_days == 90


@pytest.mark.asyncio
async def test_the_owner_list_is_bounded_and_says_how_many_it_cut(
    db: FakeCrmDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-owner figures are O(owners * stages) aggregates.

    Capped rather than unbounded — and the cap is REPORTED, because a
    leaderboard that silently stops at 25 is a leaderboard somebody trusts to
    be complete.
    """
    monkeypatch.setattr(crm_reports, "MAX_OWNERS", 2)
    lanes = seeded_board(db)
    for index, count in enumerate((3, 2, 1)):
        for _ in range(count):
            deal(db, lanes["proposal"], amount=100000, probability=50,
                 owner_email=f"rep{index}@fracktal.in")

    report = await crm_reports.owner_leaderboard(user=USER)

    # Ranked by deal count before the cut, so the cut lands on the owner with
    # the least in play rather than wherever the rows happened to sort.
    assert [r.owner_email for r in report.owners] == [
        "rep0@fracktal.in", "rep1@fracktal.in",
    ]
    assert report.omitted == 1


# ── Registration ────────────────────────────────────────────────────────────

def test_the_reports_are_registered_on_the_shared_gated_router() -> None:
    """Same router, same ``feature:crm`` gate, no second app.

    Imported from the package rather than from the module, because the failure
    this catches is the module never being imported from ``__init__`` at all —
    at which point the routes exist in source and answer 404.
    """
    from gateway.routes.crm import router

    paths = {route.path for route in router.routes}
    assert {
        "/crm/reports/pipeline", "/crm/reports/funnel",
        "/crm/reports/win-loss", "/crm/reports/owners",
    } <= paths
    assert all(
        "GET" in getattr(route, "methods", set())
        for route in router.routes if route.path.startswith("/crm/reports/")
    )


def test_reports_imports_only_the_leaf() -> None:
    """core.py is the leaf: a sibling importing a sibling is how a cycle starts.

    ``records.py`` is the package's one sanctioned exception (it needs
    ``pipeline.apply_status_transition``); a read-only report needs nothing
    from anybody.
    """
    source = inspect.getsource(crm_reports)
    for sibling in (
        "pipeline", "records", "activities", "admin", "deal_contacts",
        "import_zoho", "stage_metadata", "sync_zoho", "broker_handlers",
    ):
        assert f"from gateway.routes.crm.{sibling} import" not in source
        assert f"from gateway.routes.crm import {sibling}" not in source
