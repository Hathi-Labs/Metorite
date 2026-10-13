"""CRM · reports — forecast, funnel, win/loss and the owner leaderboard.

Spec: ``project-docs/specs/crm_app.md`` §5.1 system 2 · ticket WS-26g.

    GET /crm/reports/pipeline    per open/ongoing stage: count, ₹, ₹ weighted
    GET /crm/reports/funnel      per stage: entered, conversion-forward %,
                                 median dwell days over DEPARTURES
    GET /crm/reports/win-loss    trailing 90d: win rate, cycle, lost reasons
    GET /crm/reports/owners      per owner: open deals, ₹ weighted, ₹ won 90d

**Read-only.** Nothing here writes a row, reaches Zoho, reads a flag or needs a
migration: every number is a query over what ``crm_status_changes``,
``crm_deals`` and the two status vocabularies have carried since WS-26a.

Four facts about the DATA decide the shape of every query below, and each was
established by reading the rows rather than the prose.

1. **``crm_status_changes`` logs TRANSITIONS only.** ``records.create_record``
   writes no row and ``import_zoho`` writes none either, so all 551 imported
   deals have zero log rows and a deal's FIRST stage never appears as a
   ``to_status``. The funnel's "entered" is therefore a VISITED-SET union —
   every ``from_status``, every ``to_status``, and the deal's CURRENT stage name
   — not a count of ``to_status`` rows, which would report an empty funnel for
   the entire live board.
2. **The log stores status NAMES, not ids**, deliberately (migration 144: "an
   analytics row that stops meaning anything when somebody tidies the pipeline
   is not a log"). So every funnel join is a name join — and
   ``PATCH /crm/statuses/{kind}/{id}``, which WS-26f's settings grid puts in
   front of the owner, RENAMES a lane and orphans that name's whole history.
   Orphans are tallied into :attr:`FunnelReport.unmatched`, never dropped: a
   number that silently shrank when somebody fixed a typo is worse than no
   number.
3. **``entity_type = 'deal'`` is not optional on those reads.** The lead and
   deal status vocabularies are independent tables and the Zoho importer mints
   names into both, so an unfiltered read pulls lead history into the deal
   funnel wherever two names collide.
4. **``closed_at`` is NULL on every imported closed deal** until the owner runs
   WS-26f f4's backfill — ``?apply=true``, an owner gate (`work_plan.md` §6)
   that has NOT been run. A NULL therefore falls OUTSIDE the trailing window:
   zeros, never an error, and never "closed today". The count of such deals is
   reported (:attr:`WinLossReport.closed_without_date`) so a zero win-rate on a
   board full of closed deals is explicable rather than mysterious.

**The weighted formula is imported, never retyped** (``core.WEIGHTED_SQL``).
``tests/unit/_crm_fakes.py::_WEIGHTED_SUM_RE`` reads the expression out of the
statement text so a drifted copy changes the tests' answer; a second copy here
is exactly what would defeat that.

**No ``GROUP BY`` is emitted, and that is a choice** (WS-26g asks for it to be
stated). Every aggregate is a per-key query in the shape
``pipeline.get_pipeline`` already uses, because the weighted expression binds
the LANE's own default as ``:stage_probability`` — a single grouped statement
would have to reach that value through a join and would no longer BE the
expression the parity fixture and the fake both read. The set-shaped questions
(visited sets, medians, orphan names) read their rows and are answered in
Python, where "a deal's visited set" is expressible at all. The cost is
O(stages) plus O(owners * stages) small aggregates over a 551-row table, and
the owner list is capped so the second term cannot run away on pathological
data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import median
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends
from gateway.routes.crm.core import (
    CLOSING_TYPES,
    WEIGHTED_SQL,
    WEIGHTED_TYPES,
    StatusModel,
    _tenant_session,
    now,
    read_statuses,
    router,
    status_wire,
    wire,
)
from pydantic import BaseModel
from sqlalchemy import text

#: ``crm_status_changes.entity_type`` for a deal — one of the two values the
#: CHECK constraint accepts. Named once so the filter cannot be spelled two
#: ways, and bound as a parameter like every other caller value here.
DEAL_ENTITY_TYPE = "deal"

#: The trailing window the win/loss block and the leaderboard's "won ₹" read.
#: A constant rather than a query parameter: §5.1 fixes it at 90 days, and a
#: caller-chosen window is a saved-view feature (WS-26i), not a report.
WINDOW_DAYS = 90

#: What the lost-reason breakdown calls deals carrying no reason.
#:
#: A NAMED bucket rather than dropped rows, because those rows are the majority
#: case and the spec's earlier claim that reason data is "complete by
#: construction" is FALSE: the gate lives on ``pipeline.apply_status_transition``
#: and ``records._resolve_status``, and ``import_zoho`` bypasses both — every
#: imported ``Closed Lost`` deal carries ``lost_reason_id = NULL``. On top of
#: that ``crm_deals.lost_reason_id`` is ``ON DELETE SET NULL``, so deleting a
#: reason strips the attribution from every deal that used it.
UNATTRIBUTED_LOST = "Not recorded"

#: How many owners the leaderboard will compute. The per-owner figures are
#: O(owners * stages) small aggregates, so the list is bounded — but bounded
#: *and reported* (:attr:`OwnerLeaderboard.omitted`), and ranked by deal count
#: first so the cut falls on the owners with the least in play rather than
#: wherever the rows happened to sort.
MAX_OWNERS = 25

SECONDS_PER_DAY = 86_400.0


# ── Models ──────────────────────────────────────────────────────────────────

class StageTotals(BaseModel):
    """One lane of the forecast: what is in it and what it is worth."""

    status: StatusModel
    count: int
    amount: float
    #: 0.0 on any lane whose type is not in ``WEIGHTED_TYPES`` — this block only
    #: renders open/ongoing lanes, so in practice it is never the zero.
    weighted: float


class PipelineReport(BaseModel):
    stages: list[StageTotals]
    count: int
    amount: float
    weighted: float


class FunnelStage(BaseModel):
    status: StatusModel
    #: Deals whose VISITED SET includes this stage — see the module docstring.
    entered: int
    #: Of those, how many ever reached a stage at a strictly greater position.
    advanced: int
    #: ``advanced / entered`` as a percentage; 0.0 when nothing entered.
    conversion_forward: float
    #: Median of ``dwell_seconds`` on rows LEAVING this stage, in days.
    #: ``None`` when no departure has ever been measured — which is not zero.
    median_dwell_days: float | None
    #: How many measured departures that median is over. A median of one is a
    #: number; presenting it without its sample size is how it gets believed.
    dwell_samples: int


class UnmatchedStage(BaseModel):
    """A status name in the log that matches no current deal stage."""

    name: str
    deals: int
    #: Log rows naming it, counted once per mention (a row can name it as both
    #: ``from_status`` and ``to_status`` only if it is a self-move, which the
    #: board refuses to issue).
    mentions: int


class FunnelReport(BaseModel):
    stages: list[FunnelStage]
    #: Deals the funnel is over — every deal, since every deal has a stage.
    deals: int
    #: Log rows read (``entity_type = 'deal'``, belonging to a live deal).
    transitions: int
    unmatched: list[UnmatchedStage]


class LostReasonShare(BaseModel):
    label: str
    deals: int
    amount: float
    #: False for :data:`UNATTRIBUTED_LOST` — the bucket is a report of missing
    #: data, not a reason somebody chose, and the two must not add up as one.
    attributed: bool


class WinLossReport(BaseModel):
    window_days: int
    #: The window's lower bound, so the UI can label the number it is showing.
    since: str
    won: int
    lost: int
    #: ``won / (won + lost)`` as a percentage; 0.0 when nothing closed.
    win_rate: float
    won_amount: float
    lost_amount: float
    #: Mean ``created_at`` → ``closed_at`` over the deals in the window, in
    #: days. ``None`` when none of them can be measured — never 0.0, which is
    #: a measurement.
    average_cycle_days: float | None
    #: Deals sitting in a won/lost lane with NO ``closed_at``. They are outside
    #: every window by construction (fact 4 above), and reporting the count is
    #: the difference between "nothing closed" and "we were never told when".
    closed_without_date: int
    lost_reasons: list[LostReasonShare]


class OwnerRow(BaseModel):
    #: ``None`` is the unassigned bucket — a deal nobody owns still forecasts
    #: money, and hiding it makes the leaderboard disagree with the board.
    owner_email: str | None
    open_deals: int
    weighted: float
    won_amount: float


class OwnerLeaderboard(BaseModel):
    owners: list[OwnerRow]
    window_days: int
    #: Owners beyond :data:`MAX_OWNERS`, reported rather than silently cut.
    omitted: int


# ── Shared reads ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Totals:
    count: int
    amount: float
    weighted: float


async def _deal_stages(db: Any) -> list[Any]:
    """The deal lanes in board order — the vocabulary every block joins to."""
    # CRM-C3: the effective values, so `stage_probability` binds the overlay.
    return await read_statuses(db, "crm_deal_statuses")


async def _lane_totals(
    db: Any,
    lane: Any,
    *,
    extra_where: tuple[str, ...] = (),
    params: dict[str, Any] | None = None,
) -> _Totals:
    """Count, ₹ and ₹ weighted over one WHOLE lane.

    The same statement shape ``get_pipeline`` emits, reusing the same
    :data:`~gateway.routes.crm.core.WEIGHTED_SQL`, so the two forecast surfaces
    cannot drift and the fake's expression reader sees the real formula.

    The open/ongoing restriction is applied to the RESULT rather than inside
    the SUM, exactly as ``get_pipeline`` does: a lane IS one status, so the
    filter is a property of the lane and expressing it in the predicate would
    be a WHERE clause that is either always true or always false. Applying it
    here (and not in each caller) is also what lets the cross-language parity
    fixture assert one function against ``board.ts::weightedRows``.

    Every fragment in ``extra_where`` is a literal written in this module;
    caller values are always bound parameters.
    """
    where = " AND ".join(("status_id = CAST(:status_id AS uuid)", *extra_where))
    scope = {
        **(params or {}),
        "status_id": str(lane.id),
        # The lane's own default is the inheritance fallback, bound rather than
        # interpolated like every other value here.
        "stage_probability": int(getattr(lane, "probability", 0) or 0),
    }
    row = (await db.execute(
        text(
            "SELECT count(*) AS count, COALESCE(SUM(amount), 0) AS amount, "
            f"{WEIGHTED_SQL} FROM crm_deals WHERE {where}"
        ),
        scope,
    )).fetchone()
    return _Totals(
        count=int(getattr(row, "count", 0) or 0),
        amount=float(getattr(row, "amount", 0) or 0),
        weighted=(
            float(getattr(row, "weighted", 0) or 0)
            if getattr(lane, "type", "open") in WEIGHTED_TYPES else 0.0
        ),
    )


def _percent(part: int, whole: int) -> float:
    """A share as a percentage, and 0.0 rather than an error at zero.

    "Nothing entered this stage" is a legitimate answer for an empty CRM and
    for every stage nobody has used yet; a ZeroDivisionError is not.
    """
    return round(100.0 * part / whole, 1) if whole else 0.0


# ── 1 · Pipeline by stage ───────────────────────────────────────────────────

@router.get("/reports/pipeline", response_model=PipelineReport)
async def pipeline_report(
    user: UserContext = Depends(get_current_user),
) -> PipelineReport:
    """The forecast, per open/ongoing stage, with grand totals.

    Closed lanes are absent by design: ``won`` is booked revenue and ``lost``
    is nothing, so including them at zero would put two rows on the report that
    can only ever say the same thing. ``on_hold`` is absent for the sharper
    reason — a deal nobody is working is not a forecast, and counting it at its
    stage's prior is how a pipeline number stays high while the quarter empties
    (``core.WEIGHTED_TYPES``).
    """
    async with _tenant_session() as db:
        stages: list[StageTotals] = []
        for lane in await _deal_stages(db):
            if getattr(lane, "type", "open") not in WEIGHTED_TYPES:
                continue
            totals = await _lane_totals(db, lane)
            stages.append(StageTotals(
                status=status_wire(lane),
                count=totals.count,
                amount=totals.amount,
                weighted=totals.weighted,
            ))
        return PipelineReport(
            stages=stages,
            count=sum(s.count for s in stages),
            amount=sum(s.amount for s in stages),
            weighted=sum(s.weighted for s in stages),
        )


# ── 2 · Funnel ──────────────────────────────────────────────────────────────

@router.get("/reports/funnel", response_model=FunnelReport)
async def funnel_report(
    user: UserContext = Depends(get_current_user),
) -> FunnelReport:
    """Entered / conversion-forward / median dwell, per stage.

    Read the module docstring's facts 1-3 before changing anything here: the
    three numbers are each defined against what the log ACTUALLY records, and
    the naive reading of each one is wrong in a way that looks right.
    """
    async with _tenant_session() as db:
        lanes = await _deal_stages(db)
        names = {str(lane.id): lane.name for lane in lanes}
        # `position` per stage NAME — the log's join key. A duplicate name
        # would collapse here, which is also what it does everywhere else in
        # the app: two lanes called "Proposal" are one lane to a reader.
        positions = {
            lane.name: int(getattr(lane, "position", 0) or 0) for lane in lanes
        }

        deals = (await db.execute(
            text("SELECT id, status_id FROM crm_deals"), {},
        )).fetchall()
        changes = (await db.execute(
            text(
                "SELECT entity_id, from_status, to_status, dwell_seconds "
                "FROM crm_status_changes WHERE entity_type = :entity_type"
            ),
            {"entity_type": DEAL_ENTITY_TYPE},
        )).fetchall()

        visited = _visited_sets(deals, names)
        # Log rows whose deal no longer exists are dropped here: the log has no
        # FK to `crm_deals` (it outlives the row on purpose), and a deleted
        # deal is not in anybody's funnel. Doing it once, before every
        # downstream pass, is what keeps "considered" one set rather than three.
        owned = [c for c in changes if str(c.entity_id) in visited]
        _union_transitions(owned, visited)
        dwell = _dwell_by_departure(owned)

        return FunnelReport(
            stages=[
                _funnel_stage(lane, visited, positions, dwell) for lane in lanes
            ],
            deals=len(visited),
            transitions=len(owned),
            unmatched=_unmatched(owned, set(positions)),
        )


def _visited_sets(deals: list[Any], names: dict[str, str]) -> dict[str, set[str]]:
    """Seed each deal's visited set with the stage it is sitting in NOW.

    ⚠️ This union term is the whole reason the funnel is not empty. The log
    records transitions, so the 551 imported deals — which have never moved —
    have no rows at all, and a deal's first stage is never a ``to_status`` even
    when it has moved since. Drop this and the report says zero for a board
    with 551 deals on it, which reads as "the instrument is broken" and is
    indistinguishable from it.
    """
    visited: dict[str, set[str]] = {}
    for deal in deals:
        stage = names.get(str(getattr(deal, "status_id", None)))
        visited[str(deal.id)] = {stage} if stage else set()
    return visited


def _union_transitions(changes: list[Any], visited: dict[str, set[str]]) -> None:
    """Fold both ENDS of every transition into the deal's visited set.

    ``from_status`` matters as much as ``to_status``: a deal imported into
    Qualification and moved once carries Qualification ONLY as a
    ``from_status``, so a to-only reading loses the stage every deal starts in.

    Visited stages only — a deal that jumped Qualification → Negotiation counts
    in those two and nothing is backfilled for the lanes it skipped. It did not
    go through them.
    """
    for change in changes:
        seen = visited[str(change.entity_id)]
        for name in (change.from_status, change.to_status):
            if name:
                seen.add(name)


def _dwell_by_departure(changes: list[Any]) -> dict[str, list[float]]:
    """Dwell samples keyed by ``from_status`` — the stage being LEFT.

    ⚠️ ``from_status`` and not ``to_status``. ``dwell_seconds`` is stamped for
    the status the record is LEAVING (``pipeline.apply_status_transition``), so
    keying by ``to_status`` would label every measurement with the wrong stage
    — plausibly, and off by exactly one lane.

    NULL ``dwell_seconds`` rows are excluded: they are first transitions, where
    "we have no earlier timestamp" is not a measurement of zero. So is a NULL
    ``from_status``, which names no stage to attribute the time to. A deal
    still sitting in a stage contributes nothing at all — which is why the
    number is labelled median-over-DEPARTURES, because that is what it is.
    """
    samples: dict[str, list[float]] = {}
    for change in changes:
        seconds = getattr(change, "dwell_seconds", None)
        name = getattr(change, "from_status", None)
        if not name or seconds is None:
            continue
        samples.setdefault(name, []).append(float(seconds))
    return samples


def _funnel_stage(
    lane: Any,
    visited: dict[str, set[str]],
    positions: dict[str, int],
    dwell: dict[str, list[float]],
) -> FunnelStage:
    name = lane.name
    position = int(getattr(lane, "position", 0) or 0)
    entered = [seen for seen in visited.values() if name in seen]
    advanced = [seen for seen in entered if _went_past(seen, position, positions)]
    samples = dwell.get(name, [])
    return FunnelStage(
        status=status_wire(lane),
        entered=len(entered),
        advanced=len(advanced),
        conversion_forward=_percent(len(advanced), len(entered)),
        median_dwell_days=(
            round(median(samples) / SECONDS_PER_DAY, 1) if samples else None
        ),
        dwell_samples=len(samples),
    )


def _went_past(seen: set[str], position: int, positions: dict[str, int]) -> bool:
    """Did this deal's visited set ever include a LATER stage?

    "Ever got past it", not "went to exactly N+1" — a deal that jumped
    Qualification → Negotiation got past Qualification, and a conversion rate
    that only counted the adjacent hop would under-report every real pipeline.

    Orphan names (a lane somebody renamed) carry no position and cannot answer
    the question, so they are skipped here — and reported under ``unmatched``
    rather than being quietly treated as "did not advance".
    """
    return any(positions[name] > position for name in seen if name in positions)


def _unmatched(changes: list[Any], known: set[str]) -> list[UnmatchedStage]:
    """Status names in the log that match no current deal stage.

    Reported, never dropped. ``PATCH /crm/statuses/deal/{id}`` renames a lane
    and WS-26f's settings grid puts that button in front of the owner, so this
    is a routine consequence of tidying the pipeline rather than corruption —
    but a funnel whose totals silently shrank the day somebody fixed a typo is
    a funnel nobody can trust again.

    Matching is EXACT: the log copies ``status.name`` verbatim at write time,
    so a case difference is a different name in the pipeline's own terms.
    """
    deals: dict[str, set[str]] = {}
    mentions: dict[str, int] = {}
    for change in changes:
        for name in (change.from_status, change.to_status):
            if not name or name in known:
                continue
            deals.setdefault(name, set()).add(str(change.entity_id))
            mentions[name] = mentions.get(name, 0) + 1
    return [
        UnmatchedStage(name=name, deals=len(deals[name]), mentions=mentions[name])
        for name in sorted(deals)
    ]


# ── 3 · Win / loss ──────────────────────────────────────────────────────────

@router.get("/reports/win-loss", response_model=WinLossReport)
async def win_loss_report(
    user: UserContext = Depends(get_current_user),
) -> WinLossReport:
    """Trailing-90d win rate, average cycle and the lost-reason breakdown.

    Built for two data truths rather than around them (module docstring facts
    3 and 4): NULL ``closed_at`` rows fall outside the window and are counted
    separately, and lost deals with no reason land in a named bucket.

    ⚠️ **The window is closed at BOTH ends**, and the upper bound is not
    decoration. WS-26f f4 backfills ``closed_at`` from Zoho's ``Closing_Date``
    as a *labelled proxy*, and a Closing_Date is a forecast — imported deals
    routinely carry one in the future. With a lower bound only, an imported
    ``Closed Lost`` deal dated next quarter counts as closed in the last 90
    days, lands in ``lost``, moves ``win_rate``, and inflates
    ``average_cycle_days`` by its entire forward span. Both bounds are
    INCLUSIVE: ``closed_at`` is an instant, so the endpoints are a measure-zero
    set, and stating the rule beats leaving it to be inferred from an operator.
    """
    async with _tenant_session() as db:
        lanes = await _deal_stages(db)
        types = {str(lane.id): getattr(lane, "type", "open") for lane in lanes}
        until = now()
        since = until - timedelta(days=WINDOW_DAYS)
        closed = (await db.execute(
            text(
                "SELECT id, status_id, amount, created_at, closed_at, "
                "lost_reason_id FROM crm_deals "
                "WHERE closed_at >= :since AND closed_at <= :until"
            ),
            {"since": since, "until": until},
        )).fetchall()

        won = [d for d in closed if types.get(str(d.status_id)) == "won"]
        lost = [d for d in closed if types.get(str(d.status_id)) == "lost"]
        cycles = [c for c in (_cycle_days(d) for d in won + lost) if c is not None]

        return WinLossReport(
            window_days=WINDOW_DAYS,
            since=str(wire(since)),
            won=len(won),
            lost=len(lost),
            win_rate=_percent(len(won), len(won) + len(lost)),
            won_amount=_amount(won),
            lost_amount=_amount(lost),
            average_cycle_days=(
                round(sum(cycles) / len(cycles), 1) if cycles else None
            ),
            closed_without_date=await _undated_closed(db, lanes),
            lost_reasons=await _lost_breakdown(db, lost),
        )


def _amount(deals: list[Any]) -> float:
    return float(sum(float(getattr(d, "amount", 0) or 0) for d in deals))


def _cycle_days(deal: Any) -> float | None:
    """``created_at`` → ``closed_at`` in days, or None when it cannot be said.

    None rather than 0.0 on a missing or incomparable timestamp, the same rule
    ``pipeline._dwell_seconds`` follows: zero is a measurement and "we have no
    pair of instants" is not one, and averaging invented zeros is how a cycle
    time halves overnight.
    """
    opened = getattr(deal, "created_at", None)
    shut = getattr(deal, "closed_at", None)
    if opened is None or shut is None:
        return None
    try:
        return max(0.0, (shut - opened).total_seconds() / SECONDS_PER_DAY)
    except TypeError:
        # A naive/aware mismatch — SQL would never produce one, a fixture can.
        return None


async def _undated_closed(db: Any, lanes: list[Any]) -> int:
    """Deals in a won/lost lane carrying no ``closed_at``.

    Counted lane by lane rather than with an ``IN`` over ids, because that is
    the shape the rest of this module (and ``get_pipeline``) already emits.
    """
    total = 0
    for lane in lanes:
        if getattr(lane, "type", "open") not in CLOSING_TYPES:
            continue
        count = (await db.execute(
            text(
                "SELECT count(*) FROM crm_deals "
                "WHERE status_id = CAST(:status_id AS uuid) "
                "AND closed_at IS NULL"
            ),
            {"status_id": str(lane.id)},
        )).scalar()
        total += int(count or 0)
    return total


async def _lost_breakdown(db: Any, lost: list[Any]) -> list[LostReasonShare]:
    """Lost deals in the window, by reason, with a named unattributed bucket.

    The bucket is not a rounding error: ``import_zoho`` bypasses both
    lost-reason gates, so every imported ``Closed Lost`` deal is in it, and
    ``ON DELETE SET NULL`` puts deals there retroactively when a reason is
    removed. Dropping those rows would make the breakdown's deal counts
    disagree with the ``lost`` figure printed directly above it.
    """
    reasons = (await db.execute(
        text("SELECT id, label FROM crm_lost_reasons ORDER BY position, label"),
        {},
    )).fetchall()
    labels = {str(r.id): r.label for r in reasons}

    grouped: dict[str | None, list[Any]] = {}
    for deal in lost:
        key = str(getattr(deal, "lost_reason_id", None) or "")
        grouped.setdefault(labels.get(key), []).append(deal)

    shares = [
        LostReasonShare(
            label=label, deals=len(deals), amount=_amount(deals), attributed=True,
        )
        for label, deals in grouped.items() if label is not None
    ]
    shares.sort(key=lambda s: (-s.deals, s.label))
    unattributed = grouped.get(None, [])
    if unattributed:
        shares.append(LostReasonShare(
            label=UNATTRIBUTED_LOST,
            deals=len(unattributed),
            amount=_amount(unattributed),
            attributed=False,
        ))
    return shares


# ── 4 · Owner leaderboard ───────────────────────────────────────────────────

@router.get("/reports/owners", response_model=OwnerLeaderboard)
async def owner_leaderboard(
    user: UserContext = Depends(get_current_user),
) -> OwnerLeaderboard:
    """Per owner: open deals, ₹ weighted, and ₹ won in the trailing window.

    ``owner_email`` is *assignment*, not an ACL (D-CRM-3) — this is a
    leaderboard over who is carrying what, not a permission surface, and every
    ``feature:crm`` holder sees all of it.
    """
    async with _tenant_session() as db:
        lanes = await _deal_stages(db)
        ranked, omitted = await _owners(db)
        until = now()
        since = until - timedelta(days=WINDOW_DAYS)
        rows = [
            await _owner_row(db, lanes, owner, since=since, until=until)
            for owner in ranked
        ]
        rows.sort(key=lambda r: (-r.weighted, -r.won_amount, r.owner_email or "~"))
        return OwnerLeaderboard(
            owners=rows, window_days=WINDOW_DAYS, omitted=omitted,
        )


async def _owners(db: Any) -> tuple[list[str | None], int]:
    """The owners to compute, ranked by TOTAL deal count, and how many were cut.

    Read as rows and counted in Python rather than as a ``GROUP BY``: it is one
    column off a 551-row table, and it keeps the module's single statement
    vocabulary (see the docstring's GROUP BY note). Addresses are lowered and
    stripped — R10, email comparisons are case-insensitive on both sides — and
    :func:`_owner_scope`'s predicate carries the matching ``lower(trim(…))`` so
    the two cannot group differently.

    ⚠️ The ranking counts **every** deal, closed ones included, so a departed
    rep holding nothing but won/lost history can occupy a capped slot showing
    zeros. That is the honest reading of "who has the most in this table" and
    it is what the cheap read supports; ranking by open deals would need each
    row's stage type, i.e. a second read of the whole table to order a list
    that is 2-3 long in practice. Said plainly here because the earlier wording
    ("least in play") described a rule this does not implement.

    ⚠️ A NULL is its own key and an empty string is a DIFFERENT one, on
    purpose. Collapsing the two here would be a tally SQL cannot reproduce:
    :func:`_owner_scope` can express "``owner_email IS NULL``" or
    "``lower(owner_email) = :owner``", and the fake refuses an unreadable
    predicate rather than quietly matching every row — so a combined bucket
    would have to be spelled as an OR the aggregate cannot bind, and would
    report a count for rows its own figures never summed. Both render as
    "Unassigned" in the UI, which is the right place for that judgement.
    """
    rows = (await db.execute(text("SELECT owner_email FROM crm_deals"), {})).fetchall()
    tally: dict[str | None, int] = {}
    for row in rows:
        stored = getattr(row, "owner_email", None)
        email = None if stored is None else str(stored).strip().lower()
        tally[email] = tally.get(email, 0) + 1
    ranked = sorted(tally, key=lambda e: (-tally[e], e is None, e or ""))
    return ranked[:MAX_OWNERS], max(0, len(ranked) - MAX_OWNERS)


def _owner_scope(owner: str | None) -> tuple[tuple[str, ...], dict[str, Any]]:
    """The WHERE fragment that scopes a lane aggregate to one owner.

    NULL is its own predicate rather than a parameter: ``lower(owner_email) =
    :owner`` can never match a NULL, so the unassigned bucket would silently
    report zeros for deals that are visibly on the board.

    ⚠️ **``trim()`` here is not tidiness — it is what makes this predicate agree
    with the bucket key.** :func:`_owners` groups on ``.strip().lower()``, so
    ``'vj@fracktal.in'`` and ``'vj@fracktal.in '`` are ONE owner in the tally.
    Without the matching ``trim()`` the aggregate matched only the unpadded
    row: the leaderboard reported that owner's figures short, the padded deal
    landed in no bucket at all, ``omitted`` still said 0, and "By owner"
    silently disagreed with "Forecast by stage" on the same screen. Any
    normalisation added to one side belongs on both, in the same order.
    """
    if owner is None:
        return ("owner_email IS NULL",), {}
    return ("lower(trim(owner_email)) = :owner",), {"owner": owner}


async def _owner_row(
    db: Any, lanes: list[Any], owner: str | None, *, since: Any, until: Any,
) -> OwnerRow:
    where, params = _owner_scope(owner)
    open_deals = 0
    weighted = 0.0
    won_amount = 0.0
    for lane in lanes:
        kind = getattr(lane, "type", "open")
        if kind in WEIGHTED_TYPES:
            totals = await _lane_totals(
                db, lane, extra_where=where, params=params,
            )
            open_deals += totals.count
            weighted += totals.weighted
        elif kind == "won":
            # Won ₹ is scoped to the SAME window the win/loss block uses —
            # BOTH bounds included — so the two reports cannot print two
            # different "won" numbers on one screen. NULL `closed_at` falls
            # outside it (every imported won deal, until the owner runs f4's
            # backfill) and so does a future one (f4's proxy is a forecast
            # date, so imported deals routinely carry them).
            totals = await _lane_totals(
                db, lane,
                extra_where=(
                    *where,
                    "closed_at >= :closed_since",
                    "closed_at <= :closed_until",
                ),
                params={**params, "closed_since": since, "closed_until": until},
            )
            won_amount += totals.amount
    return OwnerRow(
        owner_email=owner,
        open_deals=open_deals,
        weighted=weighted,
        won_amount=won_amount,
    )
