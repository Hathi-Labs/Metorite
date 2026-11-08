"""The headless turn: one capped turn with no person present (WS-AUTO AUTO-8a).

Spec: ``project-docs/specs/proactive_agent.md`` §3, §4.3, §4.4, §7 and the
§9 row AUTO-8a. Queue: ``docs/job_queue.md`` §5a and §9. Decision: D161.

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
2. ``unattended.recover`` of the slot, before any check that can skip it
   (AUTO-2). The slot is the job id.
3. :func:`headless_allowed`. Off, the job is ``done`` and nothing is read.
4. The goal, as its own member, in one ``tenant_session(org)``. A missing
   goal, a goal of another kind and a goal that is not active are ``done``,
   with no model call. A credit cap over :data:`MAX_GOAL_CREDITS` gives up
   with ``CapTooHigh``.
5. ``my_day_loop.departed``, the one departed check. A member who left is
   ``done``. A failed read gives up with ``IdentityReadFailed``.
6. The candidate check of the payload (§4.3). An unknown name, or an answer
   that is not True, is ``done``: no model call, the goal untouched, silent.
   A check that raises gives up with ``CandidateFailed``.
7. ``unattended.once``: ``begin``, ``run_guarded`` and ``finish``. A taken
   slot is ``done``. The rounds run inside ``job_member_scope(member,
   app="auto", agent="auto.headless")``.
8. ``hold`` and ``complete``, or ``give_up`` with a named reason.

**One round** (:meth:`HeadlessTurn._rounds`):

a. :func:`headless_allowed` again. It is the kill switch, so an org that
   leaves the list stops before its next round, with no report.
b. The goal again: active, and of :data:`GOAL_KIND`. An active goal is under
   both caps, because the CHECK ``goal_records_active_under_caps`` holds it.
c. The time left of the turn. None left: the turn stops.
d. The meter for the new run id, BEFORE the call. The default meter
   (:func:`no_cost_reader`) raises :class:`NoCostReader`. So with no cost
   reader, the turn makes NO model call, gives up with ``NoCostReader`` and
   logs ``headless_turn.no_cost_reader`` at error. AUTO-8b builds the reader.
e. One call of the runner under ``asyncio.timeout`` of the time left, with a
   new run id.
f. The meter again. The cost of the round is the change. A meter that fails
   after the call, or answers no number, charges the rest of the goal's
   budget. So the goal blocks with ``cap_reached``: fail closed for money.
g. ``goal_store.record_round``. A ``StaleGoal`` reads the goal again and
   records the round once on the new version, so its cost is not lost. Then
   the turn stops, with no second round.
h. ``cap_reached``: the report, ONCE, and the turn stops. The report of
   record is the write of ``record_round`` on the goal row. A second delivery
   reads a blocked goal at step 4 and reports nothing.
i. ``go_on(result)``: False stops the turn. The default runs one round.

**The caps.** ``goal_store.cap_block`` is the ONE cap rule, and this module
keeps no counter of its own.

* Rounds: :data:`DEFAULT_ROUNDS` in a turn, at most :data:`MAX_ROUNDS`. The
  goal's own round cap binds too.
* Time: :data:`DEFAULT_TIME_CAP_S` for the whole turn, at most
  :data:`MAX_TIME_CAP_S`. That bound and :data:`SETTLE_MARGIN_S` stay below
  ``worker.HANDLER_TIMEOUT``. The goal record holds no clock, so the time
  cap stays in the turn (§9).
* Credits: the goal's cost cap, at most :data:`MAX_GOAL_CREDITS`.
* ADVISORY (R7), no fence: no credit stop inside one call. The cost of a
  round is known only at its end, so the cost cap stops the NEXT round, and
  the last round can go past the cap. The Router's per-run tripwire
  (``customer_console.credits.RunCeiling``, 500 credits) is the backstop.

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
module names neither the setter nor the reader of the mark.

**Money.** The handler writes no ``usage_event`` row: the Router writes it.
The paying member is ``goal_records.member_email``, bound VERIFIED through
``job_member_scope``. ⚠️ Inside ``run_agent``, the run binds its own agent
name (``worker``) and the app of the worker's config (``chat``). So the
usage row names those, not ``auto.headless``. AUTO-8b decides that
attribution.

**Tenancy (R5).** Each step opens ``tenant_session(org)`` with the org of
the worker. No statement names an org. The module builds no engine, opens
no connection of its own and holds no Redis key.

Fences: ``tests/unit/test_headless_turn.py`` (hermetic) and
``tests/unit/test_headless_turn_r8.py`` (R8).
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

from acb_common import get_logger, get_settings, goal_store, job_member_scope
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
#: The seconds of one whole turn, and their bound. The bound plus
#: :data:`SETTLE_MARGIN_S` stays at or below ``worker.HANDLER_TIMEOUT``, so the
#: turn records its last round and settles before the worker cancels it.
DEFAULT_TIME_CAP_S = 120.0
MAX_TIME_CAP_S = 180.0
SETTLE_MARGIN_S = 60.0
#: The most credits that the goal of a turn may allow. A goal that allows more
#: gets no turn (``CapTooHigh``). It is a tenth of the Router's per-run
#: tripwire (``RunCeiling``, 500 credits), so the goal cap binds first.
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
TURN_FAILED = "TurnFailed: a round of the turn failed"
TURN_STALLED = "TurnStalled: the stall guard stopped the turn"

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
    model call. AUTO-8b gives the reader of the Console."""
    raise NoCostReader("no cost reader: AUTO-8b builds the per-run reader")


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
        await unattended.recover(org, KIND, slot)
        if not headless_allowed(org):
            await _skip(org, claim, "switched_off")
            return
        goal = await _read_goal(org, goal_id)
        why = _not_ready(goal)
        if goal is None or why is not None:
            await _skip(org, claim, why or "goal_missing")
            return
        if goal.cost_cap_credits > MAX_GOAL_CREDITS:
            _log.warning("headless_turn.cap_too_high", goal_id=goal.id)
            await _give_up(org, claim, CAP_TOO_HIGH)
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
        await self._run_once(org, member, goal.id, slot, claim)

    async def _run_once(self, org: str, member: str, goal_id: str, slot: str,
                        claim: queue.Claim) -> None:
        async def work(guard: unattended.StallGuard) -> TurnEnd:
            with job_member_scope(member, app=APP, agent=AGENT):
                return await self._rounds(org, member, goal_id, guard)

        try:
            ledger, end = await unattended.once(org, KIND, slot, work)
        except NoCostReader:
            _log.error("headless_turn.no_cost_reader")
            await _give_up(org, claim, NO_READER)
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

    async def _rounds(self, org: str, member: str, goal_id: str,
                      guard: unattended.StallGuard) -> TurnEnd:
        deadline = self._clock() + self._time_cap
        previous = ""
        calls = 0
        for number in range(1, self._rounds_cap + 1):
            guard.progress()
            if not headless_allowed(org):
                return TurnEnd("switched_off", calls)
            async with tenant_session(org) as db:
                goal = await goal_store.get_goal(db, member, goal_id)
            why = _not_ready(goal)
            if goal is None or why is not None:
                return TurnEnd(why or "goal_missing", calls)
            left = deadline - self._clock()
            if left <= 0:
                return TurnEnd("time_cap", calls)
            run_id = str(uuid.uuid4())
            # BEFORE the call: the default meter raises here, so no call runs.
            before = _credits_of(await self._meter(org, run_id))
            if before is None:
                raise NoCostReader("the meter answered no number of credits")
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
            cost = await self._cost(org, run_id, before, goal)
            recorded, stale = await self._record(org, member, goal, cost)
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

    async def _cost(self, org: str, run_id: str, before: Decimal,
                    goal: goal_store.Goal) -> Decimal:
        """The credits of one round. A meter that fails, or answers no number,
        charges the rest of the goal's budget, so the goal blocks."""
        try:
            after = _credits_of(await self._meter(org, run_id))
        except Exception as exc:
            _log.error("headless_turn.cost_unknown", goal_id=goal.id, error=type(exc).__name__)
            after = None
        cost = None if after is None else after - before
        if cost is None or cost < 0 or cost > goal_store.MAX_CREDITS:
            if after is not None:
                _log.error("headless_turn.cost_unknown", goal_id=goal.id, error="BadCost")
            return max(goal.cost_cap_credits - goal.cost_used_credits, Decimal(0))
        return cost

    async def _record(self, org: str, member: str, goal: goal_store.Goal,
                      cost: Decimal) -> tuple[goal_store.Goal | None, bool]:
        """Record one round on *goal*. Answers the goal after the write, and
        True when another write came first (``StaleGoal``). Then the round is
        recorded once on the new version, so its cost is not lost."""
        try:
            async with tenant_session(org) as db:
                return await goal_store.record_round(db, member, goal.id, goal.version, cost), False
        except goal_store.StaleGoal:
            pass
        except goal_store.GoalStateError:
            _log.warning("headless_turn.round_unrecorded", goal_id=goal.id, credits=str(cost))
            return None, True
        try:
            async with tenant_session(org) as db:
                fresh = await goal_store.get_goal(db, member, goal.id)
                if fresh is not None and fresh.state == "active":
                    return await goal_store.record_round(
                        db, member, fresh.id, fresh.version, cost), True
        except goal_store.GoalStoreError:
            pass
        _log.warning("headless_turn.round_unrecorded", goal_id=goal.id, credits=str(cost))
        return None, True

    async def _tell(self, org: str, member: str, goal: goal_store.Goal) -> None:
        """The one report of a goal that this turn moved to its cap."""
        try:
            await self._report(org, member, goal)
        except Exception as exc:
            _log.warning("headless_turn.report_failed", error=type(exc).__name__)


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
