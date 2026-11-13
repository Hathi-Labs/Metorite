"""The headless turn: one capped turn with no person present (WS-AUTO AUTO-8a,
AUTO-8b-1, AUTO-8b-2a).

Spec: ``project-docs/specs/proactive_agent.md`` §3, §4.3, §4.4, §7 and the
§9 rows AUTO-8a, AUTO-8b-1 and AUTO-8b-2a. Queue: ``docs/job_queue.md`` §5a
and §9. Decision: D161.

**What it is.** The detached kind ``auto.turn`` (:data:`KIND`) runs one turn
of the assistant for one goal of kind ``headless.turn`` (:data:`GOAL_KIND`).
The job queue owns the turn, so it is one queue and not a second loop (§0).
The turn is DARK. :func:`register_jobs` registers the kind only when
``JOB_QUEUE`` is on, and no production code enqueues it. Tests prove it.

**The payload** is ``{goal_id, candidate}`` and nothing else. Any other key
gives up with ``BadPayload``. So no payload names the member, a tool or a
cap. The member is ``goal_records.member_email`` of the goal, read under the
RLS of the worker's org (``goal_store.goal_member``), never the payload (R5,
contract R11). ``candidate`` names a check of :data:`_CANDIDATES`.

**The order of the handler** (:meth:`HeadlessTurn.run`):

1. The payload shape. Another shape gives up with ``BadPayload``.
2. The clock of the turn starts (AUTO-8b-1). Then ``unattended.recover`` of
   the slot, before any check that can skip it (AUTO-2). The slot is the job
   id. So the steps below spend the time cap too.
3. :func:`headless_allowed`. Off, the job is ``done`` and nothing is read.
4. The goal, as its own member, in one ``tenant_session(org)``. A missing
   goal, a goal of another kind and a goal that is not active are ``done``,
   with no model call. A credit cap over :data:`MAX_GOAL_CREDITS` gives up
   with ``CapTooHigh``. A goal that holds a mark had a call whose round has
   no record (AUTO-8b-2a, see "The mark"). The turn charges the rest of its
   budget, so the goal blocks with ``cap_reached``, and reports once. It
   makes no model call, and it runs no candidate check. A block that does
   not land gives up with ``LostRound``.
5. ``my_day_loop.departed``, the one departed check. A member who left is
   ``done``. A failed read gives up with ``IdentityReadFailed``.
6. The candidate check of the payload (§4.3). An unknown name, or an answer
   that is not True, is ``done``: no model call, the goal untouched, silent.
   A check that raises gives up with ``CandidateFailed``.
7. ``unattended.once``: ``begin``, ``run_guarded`` and ``finish``. A taken
   slot is ``done``. The rounds run inside ``job_member_scope(member,
   app="auto", agent="auto.headless")`` and ``run_attribution_scope`` of
   the same app and agent (AUTO-8b-2c).
8. ``hold`` and ``complete``, or ``give_up`` with a named reason.

**One round** (:meth:`HeadlessTurn._rounds`):

a. :func:`headless_allowed` again. It is the kill switch, so an org that
   leaves the list stops before its next round, with no report.
b. The goal again: active, of :data:`GOAL_KIND`, and a credit cap at most
   :data:`MAX_GOAL_CREDITS` (:func:`_not_ready`). A goal that another write
   raised over that bound gets no next round (``cap_too_high``). An active
   goal is under both caps, because the CHECK
   ``goal_records_active_under_caps`` holds it. A goal that holds a mark
   gets the charge of step 4 and no call (``lost_round``).
c. The time left of the turn, from the clock of step 2. None left: the turn
   stops.
d. The meter for the new run id, BEFORE the call, inside
   :func:`_settle_limit`. The default meter (:func:`no_cost_reader`) raises
   :class:`NoCostReader`, and the turn raises it again as it is. So with no
   cost reader, the turn makes NO model call, gives up with ``NoCostReader``
   and logs ``headless_turn.no_cost_reader`` at error. A meter that does not
   answer in time raises it too. After an earlier round made a call, the
   same failure gives up with ``MeterFailed`` and logs
   ``headless_turn.meter_failed``, because a reader did answer before.
   AUTO-8b-2 builds the reader.
e. The mark (:meth:`HeadlessTurn._mark`): the job id in ``job_id`` of the
   goal, inside :func:`_settle_limit`. A write that fails or does not end in
   time gives up with ``MarkFailed``, and NO call runs. Another write that
   came first ends the turn (``stale``), with no call and no mark.
f. One call of the runner under ``asyncio.timeout`` of the time left, with a
   new run id. No time left after the meter: no call, and the turn stops.
   No time left after the mark: the turn clears the mark, makes no call and
   stops. A clear that fails keeps the mark, so a later turn blocks the goal.
g. The meter again. The cost of the round is the change. A meter that
   fails after the call, does not answer in time or answers no number
   charges the rest of the goal's budget (the cap minus the credits used).
   So the goal blocks with ``cap_reached``: fail closed for money.
h. ``goal_store.record_round(..., clear_job_id=True)``: the round and the
   end of the mark are ONE write. A ``StaleGoal`` reads the goal again and
   records the round once on the new version, so its cost is not lost. An
   unknown cost charges the rest of THAT new version. Then the turn stops,
   with no second round. A record that does not end in time logs
   ``headless_turn.round_unrecorded`` at error, and the turn raises
   :class:`TurnFailed`. The mark stays.
i. ``cap_reached``: the report, ONCE, and the turn stops. The report of
   record is the write of ``record_round`` on the goal row. A second delivery
   reads a blocked goal at step 4 and reports nothing.
j. ``go_on(result)``: False stops the turn. The default runs one round.

**The shares of the settle** (:meth:`HeadlessTurn._share`). The meter of
step g gets ``max(part, deadline + part - clock())``, and the record of step
h gets ``max(part, deadline + 2 * part - clock())``. ``part`` is
:func:`_settle_limit`. So a round that ends early may use the time that the
turn has left, and the record still ends by ``deadline + 2 * part``.

**A cancel.** Each step of the settle catches the timeout of its own scope
and the errors of its own step. A plain cancel from outside (a stop of the
worker) is ``asyncio.CancelledError``, which is not an ``Exception``. So it
goes through, and the mark stays on the goal. ⚠️ A database driver or an
HTTP client can turn an outer cancel into an error of its own. Then
:meth:`HeadlessTurn._cost` catches that error and charges the rest of the
budget, so it fails closed for money. :meth:`HeadlessTurn._record` raises
such an error again, so the mark stays.

**The mark** (AUTO-8b-2a). Three paths lose the cost of a round: a record
that does not end in time, a stop of the worker during the call, and a stop
during the settle. The mark keeps that debt on the goal row. The turn
writes it before each call, and the record of the round clears it in the
same UPDATE. So a goal that holds a mark had a call whose round has no
record, and the next turn charges the rest of the budget (step 4 and step
b). The mark is the queue claim id, ``Claim.id``: the id of the ``jobs``
row, the column that ``goal_records.job_id`` names.

⚠️ The reader never compares a mark with its own claim id. Each record of
a turn clears that turn's mark, and each path that keeps a mark ends the
turn. So a turn never reads its own live mark. A later try of the same job
reuses ``Claim.id``, with a new ``token``: a lost lease claims the row
again. The worker refuses a second try of this kind (``NOT_IDEMPOTENT``),
but a mark with the id of the current claim is still the mark of an
earlier try that died. So the turn reads it as a lost round, and a test
holds that. A mark write is a compare-and-set on the goal version, so a
second turn of one goal cannot write over a live mark.

**The report** (:meth:`HeadlessTurn._tell`) runs after the commit of the
round, so it moves no money. It takes at most :func:`_report_limit`, half of
the last part of the margin, so the ledger and the settle of the job keep
the other half. AUTO-6 must keep a limit when the report becomes a Reach
message.

**The caps.** ``goal_store.cap_block`` is the ONE cap rule, and this module
keeps no counter of its own.

* Rounds: :data:`DEFAULT_ROUNDS` in a turn, at most :data:`MAX_ROUNDS`. The
  goal's own round cap binds too.
* Time: :data:`DEFAULT_TIME_CAP_S` for the whole turn, at most
  :data:`MAX_TIME_CAP_S`. The clock starts in :meth:`HeadlessTurn.run`,
  before ``unattended.recover``. After the last call, the meter read ends by
  the deadline plus a third of :data:`SETTLE_MARGIN_S`, and the record of the
  round ends by the deadline plus two thirds ("The shares of the settle").
  The time bound plus the margin stays at or below
  ``worker.HANDLER_TIMEOUT`` (240 s), and that stays below ``queue.LEASE``
  (300 s). So the worker does not cancel a turn while it records a round.
  The goal record holds no clock, so the time cap stays in the turn (§9).
* Credits: the goal's cost cap, at most :data:`MAX_GOAL_CREDITS`, checked
  before each round.
* ADVISORY (R7), no fence: no credit stop inside one call. The cost of a
  round is known only at its end, so the cost cap stops the NEXT round, and
  the last round can go past the cap. The goal cap is the cap. The Router's
  ``customer_console.credits.RunCeiling`` is a fixed tripwire of 500 credits
  for each run, checked before each model call. It does not stop a turn at
  the goal cap.
* A round whose cost does not reach the goal row keeps its mark, so the
  next turn charges the rest of the budget (AUTO-8b-2a, "The mark"). A lost
  round never gives a later turn more room than the goal has left.

**The round limit inside one call.** One call of the default runner is one
``orchestrator.executor.run_agent`` run of the internal agent ``worker``.
The worker's client sets ``max_iterations`` 3 and ``max_duration_seconds``
90 (``subagent_contract.MAX_CHILD_ROUNDS`` is 4 model calls: 3 tool rounds,
then the one final call with the tools off, and ``CHILD_LOOP_BUDGET_S`` is
90). So one round makes at most 4 model calls. ``run_agent`` itself has no
round hook. An agent that sets no limit, such as ``orchestrator``, takes the
MAF default ``DEFAULT_MAX_ITERATIONS`` of 40 (``agent_framework`` 1.19.0,
``_tools.py``) with no time budget.

**The tools** (§7 item 1). The default runner binds one
``subagent_contract.Job`` that holds :func:`headless_tools` only, and runs
the worker. The executor injects the tools of the job, and the allow-list
pair ``subagent_contract.child_pin`` withholds and refuses every other tool,
the floor tools too. :func:`headless_tools` keeps a tool only when the risk
registry says ``read_only=True`` and ``open_world=False``, and
``subagent_contract.child_may_hold`` passes it: no member tool, no card, no
handoff and no extra model call. An unannotated tool fails closed. The run
also binds ``no_egress`` (H-236). Unattended means untrusted input, so the
turn reaches nothing outside. ``orchestrator/executor.py`` is not changed.

**No person present.** ``worker._call`` wraps each handler in
``no_person_scope`` (AUTO-7a), and the round task copies that context. This
module names neither the setter nor the reader of that no-person mark.

**Money.** The handler writes no ``usage_event`` row: the Router writes it.
The paying member is ``goal_records.member_email``, bound VERIFIED through
``job_member_scope``. AUTO-8b-2c: the rounds run inside
``acb_common.run_attribution_scope(app="auto", agent="auto.headless")``.
So ``run_agent`` binds ``auto.headless`` and ``auto`` in place of the
worker's ``worker`` and ``chat``, the worker's client sends no fixed
``X-CC-Agent``, and the usage row names the turn. This module is the one
binder of the scope.

**Tenancy (R5).** Each step opens ``tenant_session(org)`` with the org of
the worker. No statement names an org. The module builds no engine, opens
no connection of its own and holds no Redis key.

Fences: ``tests/unit/test_headless_turn.py`` (hermetic),
``tests/unit/test_headless_turn_r8.py`` (R8) and, for the clear of the mark,
``tests/unit/test_goal_record_r8.py`` (R8).
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from acb_common import (
    get_logger,
    get_settings,
    goal_store,
    job_member_scope,
    run_attribution_scope,
)
from acb_common.db import tenant_session

from gateway.jobs import queue, unattended, worker

_log = get_logger("jobs.headless_turn")

#: The kind of the job, and the job of its ledger row.
KIND = "auto.turn"
#: The kind of the goal that a turn runs. The CHECK ``goal_records_kind``
#: takes it, so it needs no migration.
GOAL_KIND = "headless.turn"
#: The app and the automation name of the model calls (``job_member_scope``).
APP = "auto"
AGENT = "auto.headless"

#: The rounds of one turn, and their bound.
DEFAULT_ROUNDS = 5
MAX_ROUNDS = 10
#: The seconds of one whole turn, and their bound. The clock starts in
#: :meth:`HeadlessTurn.run`, before ``unattended.recover``. The bound plus
#: :data:`SETTLE_MARGIN_S` stays at or below ``worker.HANDLER_TIMEOUT``, so the
#: turn records its last round and settles before the worker cancels it.
DEFAULT_TIME_CAP_S = 120.0
MAX_TIME_CAP_S = 180.0
#: The seconds of the settle after the deadline. It has :data:`SETTLE_PARTS`
#: parts. The meter read ends by the first part and the record of the round by
#: the second (:meth:`HeadlessTurn._share`). The report (half a part,
#: :func:`_report_limit`), the ledger and the settle of the job keep the last.
SETTLE_MARGIN_S = 60.0
SETTLE_PARTS = 3
#: The most credits that the goal of a turn may allow. A goal that allows more
#: gets no turn (``CapTooHigh``), and each round checks it again. It is a tenth
#: of the Router's fixed per-run tripwire (``RunCeiling``, 500 credits).
MAX_GOAL_CREDITS = Decimal("50")
#: The characters of the answer of a round that the next round reads.
PREVIOUS_CHARS = 4000

#: The tools that a turn may hold, before :func:`headless_tools` checks each
#: one. Each reads, as the member, what the member can read.
DEFAULT_TOOLS: tuple[str, ...] = (
    "remember",
    "recall_timeline",
    "query_history",
    "org_facts",
    "crm_list_records",
    "crm_get_record",
    "crm_list_pipelines",
    "crm_deal_contacts",
    "crm_aggregate",
    "crm_stage_dwell",
    "crm_list_owners",
    "crm_list_fields",
    "crm_search_notes",
)

#: The named reasons of a give-up (``docs/job_queue.md`` §5a). The log line
#: holds the text before the first colon only.
BAD_PAYLOAD = "BadPayload: the payload is not {goal_id, candidate}"
CAP_TOO_HIGH = "CapTooHigh: the credit cap of the goal is over the bound of a turn"
IDENTITY_FAILED = "IdentityReadFailed: the member check failed, so no model call ran"
CANDIDATE_FAILED = "CandidateFailed: the candidate check failed, so no model call ran"
NO_READER = "NoCostReader: no cost reader, so the turn made no model call"
METER_FAILED = "MeterFailed: the cost reader failed after an earlier round made a call"
MARK_FAILED = "MarkFailed: the mark before a call was not written, so no call ran"
LOST_ROUND = "LostRound: a round of the goal has no record, and its charge did not land"
TURN_FAILED = "TurnFailed: a round of the turn failed"
TURN_STALLED = "TurnStalled: the stall guard stopped the turn"

#: Why a goal takes no round: its credit cap is over :data:`MAX_GOAL_CREDITS`.
_CAP_TOO_HIGH = "cap_too_high"

#: The keys of a payload, exactly.
_PAYLOAD_KEYS = frozenset({"goal_id", "candidate"})
#: The shape of a candidate name: a code constant, never member text.
_NAME = re.compile(r"[a-z][a-z0-9_.]{0,63}")

#: ``await candidate(org, member, payload)``: True when the turn has work.
CandidateCheck = Callable[[str, str, dict[str, Any]], Awaitable[bool]]
#: ``await meter(org, run_id)``: the credits that the run *run_id* spent.
Meter = Callable[[str, str], Awaitable[Any]]
#: ``await runner(message, run_id=, org=, member=, tools=)``: one round.
Runner = Callable[..., Awaitable[Any]]
#: ``await report(org, member, goal)``: tell of a goal at its cap, once.
Report = Callable[[str, str, goal_store.Goal], Awaitable[None]]

#: The candidate checks, by name. Empty: no production check exists yet, so
#: each payload finds no candidate, and no model call runs.
_CANDIDATES: dict[str, CandidateCheck] = {}


class NoCostReader(RuntimeError):
    """No reader of the cost of a run exists, so no round may start."""


class TurnFailed(RuntimeError):
    """A round raised. Its cost is on the goal, and the turn stops."""


class MeterFailed(RuntimeError):
    """The meter failed BEFORE a round, after an earlier round of this turn
    made a call. The cost of each earlier round is on the goal."""


class MarkFailed(RuntimeError):
    """The mark before a call was not written, so the call does not run."""


class LostRound(RuntimeError):
    """A goal holds a mark, and the charge of the rest did not land. No call
    runs, and the mark stays for the next turn."""


@dataclass(frozen=True)
class TurnEnd:
    """How a turn ended, and the calls of the runner that it made."""

    outcome: str
    rounds: int


def _now() -> datetime:
    return datetime.now(UTC)


# ── The flag ────────────────────────────────────────────────────────────────


def headless_allowed(organization_id: str | None) -> bool:
    """True when ``HEADLESS_TURN_ORGS`` lists the org, or holds ``*``.
    Default OFF. The ONE reader of the flag.

    It is the on switch and the kill switch. The org is the worker's org,
    never request input (R5). It reads ``get_settings()`` on each call, so a
    flag reload applies a change before the next round."""
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "headless_turn_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


# ── The candidate checks ────────────────────────────────────────────────────


def register_candidate(name: str, check: CandidateCheck, *, replace: bool = False) -> None:
    """Add the candidate check *name*. A second check of one name is an
    error, unless *replace* is True."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("a candidate name must match [a-z][a-z0-9_.]{0,63}")
    if name in _CANDIDATES and not replace:
        raise ValueError(f"a candidate check named {name!r} exists")
    _CANDIDATES[name] = check


def unregister_candidate(name: str) -> None:
    """Forget the candidate check *name*. Safe when none exists."""
    _CANDIDATES.pop(name, None)


# ── The tools ───────────────────────────────────────────────────────────────


def headless_tools(names: Iterable[str]) -> tuple[str, ...]:
    """The names of *names* that a headless turn may hold, in order, once each.

    A tool stays only when the risk registry says ``read_only=True`` and
    ``open_world=False``, and ``subagent_contract.child_may_hold`` passes it.
    An unannotated tool, a web tool, a member tool, a card, a handoff and a
    tool that makes its own model call all go (fail closed)."""
    from acb_skills import subagent_contract as sc
    from acb_skills.tool_annotations import get_annotations

    kept: list[str] = []
    for raw in names:
        name = str(raw or "")
        if not name or name in kept or name in sc.WEB_TOOLS:
            continue
        notes = get_annotations(name) or {}
        if notes.get("read_only") is not True or notes.get("open_world") is not False:
            continue
        if sc.child_may_hold(name):
            kept.append(name)
    return tuple(kept)


async def run_worker(message: str, *, run_id: str, org: str, member: str,
                     tools: tuple[str, ...]) -> Any:
    """The default runner: one ``run_agent`` run of the internal agent
    ``worker``, held to *tools*.

    It binds one ``subagent_contract.Job`` and ``no_egress`` on a context of
    its own, so the run reads them as its parent, and the scope gives this
    frame its context back. The member is the payer of the goal, bound
    VERIFIED by ``job_member_scope``, so the claim in ``user_email`` keeps
    the proof (``executor._run_member``)."""
    from acb_skills import subagent_contract as sc
    from acb_skills.egress import NO_EGRESS_KEY
    from acb_skills.write_artifact import artifact_context_scope, derive_artifact_context
    from orchestrator.executor import run_agent

    job = sc.Job(number=1, job=message, tools=tuple(tools), tier=sc.DEFAULT_TIER)
    with artifact_context_scope():
        derive_artifact_context(**{NO_EGRESS_KEY: True})
        sc.bind_job(job)
        return await run_agent(
            sc.WORKER_NAME, {"message": message, "user_email": member},
            run_id=run_id, organization_id=org, solo_room=False,
        )


# ── The meter, the report and the next round ────────────────────────────────


async def no_cost_reader(org: str, run_id: str) -> Decimal:
    """The default meter. It raises, so a turn with no cost reader makes no
    model call. AUTO-8b-2 gives the reader of the Console."""
    raise NoCostReader("no cost reader: AUTO-8b-2 builds the per-run reader")


def _settle_limit() -> float:
    """The seconds of one part of the settle: the least time of one meter
    read or of the record of one round, and the most time of the meter read
    before a call and of the mark. It reads :data:`SETTLE_MARGIN_S` on each
    call."""
    return SETTLE_MARGIN_S / SETTLE_PARTS


def _report_limit() -> float:
    """The seconds of the report: half of the last part of the settle, so
    the ledger and the settle of the job keep the other half."""
    return _settle_limit() / 2


def _rest(goal: goal_store.Goal) -> Decimal:
    """The rest of the budget of *goal*: the cap minus the credits used."""
    return max(goal.cost_cap_credits - goal.cost_used_credits, Decimal(0))


def _charge(goal: goal_store.Goal, cost: Decimal | None) -> Decimal:
    """The charge of a round on *goal*. An unknown cost (None) charges the
    rest of THIS goal, so a retry on a new version reads the new caps."""
    return _rest(goal) if cost is None else cost


async def log_report(org: str, member: str, goal: goal_store.Goal) -> None:
    """The default report. The write of ``record_round`` on the goal row is
    the report of record. This logs it once, with no member text. AUTO-6
    gives the mouth (``tell_me``)."""
    _log.info("headless_turn.cap_reached", goal_id=goal.id, rounds=goal.round_count,
              credits=str(goal.cost_used_credits))


def one_round(result: Any) -> bool:
    """The default rule of the next round: none. A later slice passes the
    rule of its template."""
    return False


def _credits_of(value: Any) -> Decimal | None:
    """*value* as a number of credits from 0, or None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    try:
        amount = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def _answer(result: Any) -> str:
    if isinstance(result, Mapping):
        return str(result.get("answer") or "")[:PREVIOUS_CHARS]
    return ""


def _message(goal: goal_store.Goal, number: int, previous: str) -> str:
    """The job text of round *number*: the objective, and the answer of the
    round before, if one exists."""
    if not previous:
        return goal.objective
    return (f"{goal.objective}\n\nThis is round {number}. "
            f"Your answer of the round before:\n{previous}")


def _not_ready(goal: goal_store.Goal | None) -> str | None:
    """Why *goal* takes no round, or None."""
    if goal is None:
        return "goal_missing"
    if goal.kind != GOAL_KIND:
        return "goal_other_kind"
    if goal.state != "active":
        return "goal_not_active"
    if goal.cost_cap_credits > MAX_GOAL_CREDITS:
        return _CAP_TOO_HIGH
    return None


def _payload(payload: Any) -> tuple[str, str] | None:
    """The goal id and the candidate name of *payload*, or None."""
    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
        return None
    goal_id, name = payload.get("goal_id"), payload.get("candidate")
    if not isinstance(goal_id, str) or not isinstance(name, str):
        return None
    if not _NAME.fullmatch(name):
        return None
    try:
        return str(uuid.UUID(goal_id)), name
    except ValueError:
        return None


# ── The settle ──────────────────────────────────────────────────────────────


async def _settle_done(org: str, claim: queue.Claim) -> None:
    async with tenant_session(org) as db:
        if await queue.hold(db, claim):
            await queue.complete(db, claim, now=_now())


async def _give_up(org: str, claim: queue.Claim, reason: str) -> None:
    async with tenant_session(org) as db:
        await queue.give_up(db, claim, reason, now=_now())


async def _skip(org: str, claim: queue.Claim, reason: str) -> None:
    """End a job that makes no model call, with no write but the settle."""
    _log.info("headless_turn.skipped", reason=reason)
    await _settle_done(org, claim)


async def _read_goal(org: str, goal_id: str) -> goal_store.Goal | None:
    """The goal *goal_id*, read as its own member, in the bound org."""
    async with tenant_session(org) as db:
        member = await goal_store.goal_member(db, goal_id)
        if member is None:
            return None
        return await goal_store.get_goal(db, member, goal_id)


async def _departed(org: str, member: str) -> str | None:
    from gateway import my_day_loop

    return await my_day_loop.departed(org, member)


# ── The turn ────────────────────────────────────────────────────────────────


class HeadlessTurn:
    """One headless turn, with its seams. :func:`run_turn` uses the defaults.

    A test passes a fake *runner* and a fake *meter*. No default makes a
    model call that a cost reader did not allow first."""

    def __init__(
        self, *, runner: Runner | None = None, meter: Meter | None = None,
        report: Report | None = None, go_on: Callable[[Any], bool] | None = None,
        candidates: Mapping[str, CandidateCheck] | None = None,
        tools: Iterable[str] = DEFAULT_TOOLS, rounds: int = DEFAULT_ROUNDS,
        time_cap_s: float = DEFAULT_TIME_CAP_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(rounds, bool) or not isinstance(rounds, int) or not (
                1 <= rounds <= MAX_ROUNDS):
            raise ValueError(f"rounds must be a whole number from 1 to {MAX_ROUNDS}")
        if isinstance(time_cap_s, bool) or not isinstance(time_cap_s, int | float) or not (
                0 < time_cap_s <= MAX_TIME_CAP_S):
            raise ValueError(f"time_cap_s must be more than 0 and at most {MAX_TIME_CAP_S:g}")
        self._runner: Runner = runner or run_worker
        self._meter: Meter = meter or no_cost_reader
        self._report: Report = report or log_report
        self._go_on = go_on or one_round
        self._candidates = candidates
        self._tools = tuple(tools)
        self._rounds_cap = rounds
        self._time_cap = float(time_cap_s)
        self._clock = clock

    def _check_of(self, name: str) -> CandidateCheck | None:
        source = _CANDIDATES if self._candidates is None else self._candidates
        return source.get(name)

    async def run(self, org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
        """The detached handler of :data:`KIND`."""
        parsed = _payload(payload)
        if parsed is None:
            _log.warning("headless_turn.bad_payload")
            await _give_up(org, claim, BAD_PAYLOAD)
            return
        goal_id, name = parsed
        slot = claim.id
        # The clock of the turn starts here, so the checks below spend the
        # time cap too, and the settle stays inside HANDLER_TIMEOUT.
        deadline = self._clock() + self._time_cap
        await unattended.recover(org, KIND, slot)
        if not headless_allowed(org):
            await _skip(org, claim, "switched_off")
            return
        goal = await _read_goal(org, goal_id)
        why = _not_ready(goal)
        if why == _CAP_TOO_HIGH:
            _log.warning("headless_turn.cap_too_high", goal_id=goal_id)
            await _give_up(org, claim, CAP_TOO_HIGH)
            return
        if goal is None or why is not None:
            await _skip(org, claim, why or "goal_missing")
            return
        if goal.job_id is not None:
            # A call of an earlier run has no record: charge the rest, no call.
            if await self._settle_lost(org, goal.member_email, goal, deadline) is None:
                await _give_up(org, claim, LOST_ROUND)
                return
            _log.info("headless_turn.ended", outcome="lost_round", rounds=0)
            await _settle_done(org, claim)
            return
        member = goal.member_email
        try:
            gone = await _departed(org, member)
        except Exception as exc:
            _log.warning("headless_turn.member_check_failed", error=type(exc).__name__)
            await _give_up(org, claim, IDENTITY_FAILED)
            return
        if gone is not None:
            await _skip(org, claim, "member_departed")
            return
        check = self._check_of(name)
        try:
            found = check is not None and await check(org, member, dict(payload)) is True
        except Exception as exc:
            _log.warning("headless_turn.candidate_failed", error=type(exc).__name__)
            await _give_up(org, claim, CANDIDATE_FAILED)
            return
        if not found:
            await _skip(org, claim, "no_candidate")
            return
        await self._run_once(org, member, goal.id, slot, claim, deadline)

    async def _run_once(self, org: str, member: str, goal_id: str, slot: str,
                        claim: queue.Claim, deadline: float) -> None:
        async def work(guard: unattended.StallGuard) -> TurnEnd:
            # AUTO-8b-2c: the one binder of the attribution scope.
            with (job_member_scope(member, app=APP, agent=AGENT),
                  run_attribution_scope(app=APP, agent=AGENT)):
                # The mark of each call is the claim id (the slot).
                return await self._rounds(org, member, goal_id, slot, guard, deadline)

        try:
            ledger, end = await unattended.once(org, KIND, slot, work)
        except NoCostReader:
            _log.error("headless_turn.no_cost_reader")
            await _give_up(org, claim, NO_READER)
            return
        except MeterFailed:
            _log.error("headless_turn.meter_failed")
            await _give_up(org, claim, METER_FAILED)
            return
        except MarkFailed:
            await _give_up(org, claim, MARK_FAILED)
            return
        except LostRound:
            await _give_up(org, claim, LOST_ROUND)
            return
        except Exception as exc:
            _log.warning("headless_turn.failed", error=type(exc).__name__)
            await _give_up(org, claim, TURN_FAILED)
            return
        if end is None:
            if ledger.fresh:
                await _give_up(org, claim, TURN_STALLED)
            else:
                await _skip(org, claim, "slot_taken")
            return
        _log.info("headless_turn.ended", outcome=end.outcome, rounds=end.rounds)
        await _settle_done(org, claim)

    async def _rounds(self, org: str, member: str, goal_id: str, mark: str,
                      guard: unattended.StallGuard, deadline: float) -> TurnEnd:
        previous = ""
        calls = 0
        for number in range(1, self._rounds_cap + 1):
            guard.progress()
            ready = await self._ready(org, member, goal_id, mark, calls, deadline)
            if isinstance(ready, TurnEnd):
                return ready
            goal, run_id, before, left = ready
            result: Any = None
            failure: Exception | None = None
            timed_out = False
            scope = asyncio.timeout(left)
            calls += 1
            try:
                async with scope:
                    result = await self._runner(
                        _message(goal, number, previous), run_id=run_id, org=org,
                        member=member, tools=headless_tools(self._tools))
            except Exception as exc:
                if scope.expired():
                    timed_out = True
                else:
                    failure = exc
            guard.progress()
            cost = await self._cost(org, run_id, before, goal, deadline)
            recorded, stale = await self._record(org, member, goal, cost, deadline)
            if recorded is not None and recorded.block_code == "cap_reached":
                await self._tell(org, member, recorded)
                return TurnEnd("cap_reached", calls)
            if failure is not None:
                raise TurnFailed(type(failure).__name__) from failure
            if stale:
                return TurnEnd("stale", calls)
            if recorded is None:
                return TurnEnd("goal_missing", calls)
            if timed_out:
                return TurnEnd("time_cap", calls)
            if not self._go_on(result):
                return TurnEnd("done", calls)
            previous = _answer(result)
        return TurnEnd("round_cap", calls)

    async def _ready(self, org: str, member: str, goal_id: str, mark: str, calls: int,
                     deadline: float) -> TurnEnd | tuple[goal_store.Goal, str, Decimal, float]:
        """Steps a to f of one round, up to the call. Answers how the turn
        ends with no call, or the marked goal, the new run id, the meter
        before the call and the seconds left for the call."""
        if not headless_allowed(org):
            return TurnEnd("switched_off", calls)
        async with tenant_session(org) as db:
            goal = await goal_store.get_goal(db, member, goal_id)
        why = _not_ready(goal)
        if goal is None or why is not None:
            if why == _CAP_TOO_HIGH:
                _log.warning("headless_turn.cap_too_high", goal_id=goal_id)
            return TurnEnd(why or "goal_missing", calls)
        if goal.job_id is not None:
            # Not this turn's mark: each record of this turn cleared its own.
            if await self._settle_lost(org, member, goal, deadline) is None:
                raise LostRound("the charge of a lost round did not land")
            return TurnEnd("lost_round", calls)
        if deadline - self._clock() <= 0:
            return TurnEnd("time_cap", calls)
        run_id = str(uuid.uuid4())
        # BEFORE the call: the default meter raises here, so no call runs.
        try:
            before = await self._before(org, run_id)
        except NoCostReader as exc:
            if calls:
                raise MeterFailed(str(exc)) from None
            raise
        if deadline - self._clock() <= 0:
            return TurnEnd("time_cap", calls)
        # The mark, AFTER the meter: a meter that fails leaves no mark.
        marked = await self._mark(org, member, goal, mark)
        if marked is None:
            return TurnEnd("stale", calls)
        left = deadline - self._clock()
        if left <= 0:
            await self._unmark(org, member, marked)
            return TurnEnd("time_cap", calls)
        return marked, run_id, before, left

    async def _before(self, org: str, run_id: str) -> Decimal:
        """The meter of *run_id* BEFORE the call, inside :func:`_settle_limit`.

        A :class:`NoCostReader` of the meter is raised again as it is. A read
        that does not answer in time, or answers no number, raises it too, so
        no call runs. Another error of the meter is raised again as it is,
        and a TimeoutError of the meter's own is such an error."""
        scope = asyncio.timeout(_settle_limit())
        try:
            async with scope:
                value = await self._meter(org, run_id)
        except Exception:
            if scope.expired():
                raise NoCostReader("the meter did not answer in time") from None
            raise
        before = _credits_of(value)
        if before is None:
            raise NoCostReader("the meter answered no number of credits")
        return before

    def _share(self, deadline: float, parts: int) -> float:
        """The seconds of a step of the settle that must end by part *parts*
        of the margin: ``max(part, deadline + parts * part - clock())``.

        ``part`` is :func:`_settle_limit`. The meter after a call takes
        *parts* 1 and the record takes 2. A round that ends early may use the
        time that the turn has left. A round that ends at the deadline gets
        one part. The bound inside ``worker.HANDLER_TIMEOUT`` does not
        change, because each step still ends by ``deadline + parts * part``
        when it starts by the deadline."""
        part = _settle_limit()
        return max(part, deadline + parts * part - self._clock())

    async def _mark(self, org: str, member: str, goal: goal_store.Goal,
                    mark: str) -> goal_store.Goal | None:
        """Write *mark* (the claim id) in ``job_id`` of *goal* BEFORE a call,
        inside :func:`_settle_limit`. Answers the goal after the write.

        None when another write came first or the goal is gone: no call, and
        no mark. A write that fails, or does not end in time, logs
        ``headless_turn.mark_failed`` at error and raises :class:`MarkFailed`,
        so no call runs (fail closed). A write that timed out can still have
        landed. Then the next turn charges the rest, which also fails closed."""
        scope = asyncio.timeout(_settle_limit())
        try:
            async with scope, tenant_session(org) as db:
                marked = await goal_store.update_goal(
                    db, member, goal.id, goal.version, job_id=mark)
        except (goal_store.StaleGoal, goal_store.GoalStateError):
            _log.info("headless_turn.mark_stale", goal_id=goal.id)
            return None
        except Exception as exc:
            error = "MarkTimeout" if scope.expired() else type(exc).__name__
            _log.error("headless_turn.mark_failed", goal_id=goal.id, error=error)
            raise MarkFailed(error) from None
        if marked is None:
            return None
        if marked.job_id != str(uuid.UUID(mark)) or marked.state != "active":
            _log.error("headless_turn.mark_failed", goal_id=goal.id, error="MarkNotWritten")
            raise MarkFailed("MarkNotWritten")
        return marked

    async def _unmark(self, org: str, member: str, goal: goal_store.Goal) -> None:
        """Clear the mark of *goal* when no call ran after it, inside
        :func:`_settle_limit`. A clear that fails keeps the mark and logs
        ``headless_turn.mark_kept``. Then the next turn charges the rest of
        the budget: fail closed."""
        scope = asyncio.timeout(_settle_limit())
        try:
            async with scope, tenant_session(org) as db:
                await goal_store.update_goal(db, member, goal.id, goal.version, job_id=None)
        except Exception as exc:
            error = "UnmarkTimeout" if scope.expired() else type(exc).__name__
            _log.warning("headless_turn.mark_kept", goal_id=goal.id, error=error)

    async def _settle_lost(self, org: str, member: str, goal: goal_store.Goal,
                           deadline: float) -> goal_store.Goal | None:
        """*goal* holds a mark: a call whose round has no record. Charge the
        rest of the budget in one round that clears the mark, so the goal
        blocks with ``cap_reached``, and report once. No call runs.

        Logs ``headless_turn.lost_round`` at error. Answers the goal after
        the write, or None when the charge did not land. Then the mark
        stays, and the next turn tries again."""
        _log.error("headless_turn.lost_round", goal_id=goal.id, job_id=goal.job_id)
        try:
            recorded, _stale = await self._record(org, member, goal, None, deadline)
        except Exception as exc:
            _log.error("headless_turn.lost_round_unrecorded", goal_id=goal.id,
                       error=type(exc).__name__)
            return None
        if recorded is not None and recorded.block_code == "cap_reached":
            await self._tell(org, member, recorded)
        return recorded

    async def _cost(self, org: str, run_id: str, before: Decimal,
                    goal: goal_store.Goal, deadline: float) -> Decimal | None:
        """The credits of one round, read inside ``_share(deadline, 1)``.
        None when the cost is unknown: a meter that fails, does not answer in
        time or answers no number. The record then charges the rest of the
        budget of the goal that it writes, so the goal blocks.

        A plain cancel is not an ``Exception``, so it goes through. ⚠️ A
        driver that turns an outer cancel into an error of its own is caught
        here, and the round charges the rest: fail closed for money."""
        scope = asyncio.timeout(self._share(deadline, 1))
        try:
            async with scope:
                after = _credits_of(await self._meter(org, run_id))
        except Exception as exc:
            error = "MeterTimeout" if scope.expired() else type(exc).__name__
            _log.error("headless_turn.cost_unknown", goal_id=goal.id, error=error)
            return None
        cost = None if after is None else after - before
        if cost is None or cost < 0 or cost > goal_store.MAX_CREDITS:
            if after is not None:
                _log.error("headless_turn.cost_unknown", goal_id=goal.id, error="BadCost")
            return None
        return cost

    async def _record(self, org: str, member: str, goal: goal_store.Goal,
                      cost: Decimal | None,
                      deadline: float) -> tuple[goal_store.Goal | None, bool]:
        """:meth:`_write_round` inside ``_share(deadline, 2)``. A record that
        does not end in time logs ``headless_turn.round_unrecorded`` at error
        and raises :class:`TurnFailed`. Another error is raised again as it
        is, and a cancel goes through. In each of those cases the mark stays
        on the goal."""
        scope = asyncio.timeout(self._share(deadline, 2))
        try:
            async with scope:
                return await self._write_round(org, member, goal, cost)
        except Exception:
            if not scope.expired():
                raise
        _log.error("headless_turn.round_unrecorded", goal_id=goal.id,
                   credits=str(_charge(goal, cost)), error="RecordTimeout")
        raise TurnFailed("RecordTimeout")

    async def _write_round(self, org: str, member: str, goal: goal_store.Goal,
                           cost: Decimal | None) -> tuple[goal_store.Goal | None, bool]:
        """Record one round on *goal* and clear its mark, in one write.
        Answers the goal after the write, and True when another write came
        first (``StaleGoal``). Then the round is recorded once on the new
        version, so its cost is not lost. An unknown cost (None) charges the
        rest of the version that the write names (:func:`_charge`)."""
        try:
            async with tenant_session(org) as db:
                return await goal_store.record_round(
                    db, member, goal.id, goal.version, _charge(goal, cost),
                    clear_job_id=True), False
        except goal_store.StaleGoal:
            pass
        except goal_store.GoalStateError:
            _log.warning("headless_turn.round_unrecorded", goal_id=goal.id,
                         credits=str(_charge(goal, cost)))
            return None, True
        try:
            async with tenant_session(org) as db:
                fresh = await goal_store.get_goal(db, member, goal.id)
                if fresh is not None and fresh.state == "active":
                    return await goal_store.record_round(
                        db, member, fresh.id, fresh.version, _charge(fresh, cost),
                        clear_job_id=True), True
        except goal_store.GoalStoreError:
            pass
        _log.warning("headless_turn.round_unrecorded", goal_id=goal.id,
                     credits=str(_charge(goal, cost)))
        return None, True

    async def _tell(self, org: str, member: str, goal: goal_store.Goal) -> None:
        """The one report of a goal that this turn moved to its cap.

        It runs after the commit of the round, so it moves no money. It takes
        at most :func:`_report_limit`, so the ledger and the settle of the
        job keep the rest of the margin. A report that fails or does not end
        in time logs ``headless_turn.report_failed``. AUTO-6 must keep a
        limit when the report becomes a Reach message."""
        scope = asyncio.timeout(_report_limit())
        try:
            async with scope:
                await self._report(org, member, goal)
        except Exception as exc:
            error = "ReportTimeout" if scope.expired() else type(exc).__name__
            _log.warning("headless_turn.report_failed", error=error)


#: The turn of the worker. A test replaces it, never a production caller.
_DEFAULT = HeadlessTurn()


async def run_turn(org: str, payload: dict[str, Any], claim: queue.Claim) -> None:
    """The detached handler of :data:`KIND` (``docs/job_queue.md`` §5a)."""
    await _DEFAULT.run(org, payload, claim)


def register_jobs(*, replace: bool = False) -> bool:
    """Register :data:`KIND` as a detached kind that is not idempotent, only
    when ``JOB_QUEUE`` is on. True when it registered. A second try of a turn
    could spend twice, so the worker gives each job one try.

    The effect is ``own_rows`` (BR-7a): a turn writes only the ledger and its
    own goal row, and its tools are read-only. The report is a log line. When
    AUTO-6 sends it through Reach, the kind becomes ``send`` with a policy."""
    if not worker.worker_enabled():
        return False
    worker.register(KIND, run_turn, mode="detached", idempotent=False, replace=replace,
                    effect="own_rows")
    return True


register_jobs()
