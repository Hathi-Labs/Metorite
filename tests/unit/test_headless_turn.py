"""WS-AUTO AUTO-8a, AUTO-8b-1 and AUTO-8b-2a: the headless turn, hermetic
(no database).

Spec: ``project-docs/specs/proactive_agent.md`` §4.3, §4.4, §7 and §11. Code:
``apps/services/gateway/gateway/jobs/headless_turn.py``. The R8 half is
``test_headless_turn_r8.py``: it runs the same handler on a real Postgres.

No test here calls a model. Each one passes a fake runner and a fake meter,
and the goal store, the queue settle and the departed check are fakes. The
fake goal store holds the real cap rule (``goal_store.cap_block``) and the
real compare-and-set, so a round reaches ``cap_reached`` as on the database.

**The fences (R7), by name.**

* no-candidate-no-call: ``test_no_candidate_makes_no_call_and_touches_no_goal``.
* stop at the cap and report once: ``test_a_turn_stops_at_the_cap_and_reports_once``.
* a second delivery reports nothing: ``test_a_second_delivery_reports_nothing``.
* the kill switch: ``test_the_kill_switch_stops_the_next_round``,
  ``test_the_flag_off_reads_nothing_and_calls_nothing``.
* the round cap: ``test_the_round_cap_stops_the_turn``.
* the time cap: ``test_the_time_cap_stops_the_turn``.
* AUTO-8b-2d, a cut or failed call charges the rest and reads no meter:
  ``test_the_time_cap_stops_the_turn`` (changed: a cut round no longer
  records the cost that the meter reads, because the Console can write the
  row of the cut call AFTER that read),
  ``test_a_cut_call_whose_row_lands_late_charges_the_rest`` and
  ``test_a_failed_round_charges_the_rest_and_gives_up``.
* read-only tools: ``test_only_read_only_tools_are_offered``,
  ``test_the_default_runner_holds_the_worker_to_a_read_only_job``.
* the default meter fails closed: ``test_the_default_meter_fails_closed``.
* the source scan: ``test_the_module_has_no_approve_path_no_usage_write_and_no_engine``.
* AUTO-8b-1, the settle in time:
  ``test_a_meter_that_hangs_after_the_call_charges_the_rest_inside_the_margin``,
  ``test_a_record_that_hangs_gives_up_inside_the_margin``,
  ``test_the_settle_never_swallows_a_cancel``,
  ``test_the_clock_of_the_turn_starts_before_recover``.
* AUTO-8b-1, the bound of the goal cap in each round:
  ``test_a_goal_raised_over_the_bound_between_rounds_gets_no_next_round``.
* AUTO-8b-1, no member from the payload, ``.pop(`` too:
  ``test_the_module_takes_no_member_from_the_payload``.
* AUTO-8b-2a, the mark (the P1 of the AUTO-8b-1 review):
  ``test_a_mark_is_written_before_each_call_and_the_record_clears_it``,
  ``test_a_goal_that_holds_a_mark_blocks_at_once_with_no_call`` (the mark of
  another job, and the mark of the same job id as the claim),
  ``test_a_lost_round_leaves_the_mark_and_the_next_turn_blocks``,
  ``test_a_failed_mark_write_makes_no_call``,
  ``test_a_stale_mark_write_makes_no_call``,
  ``test_no_time_after_the_mark_clears_it_and_makes_no_call``,
  ``test_a_mark_seen_before_a_later_round_blocks_the_goal``.
* AUTO-8b-2a, the six P2 notes, one test each:
  (1) ``test_the_settle_shares_follow_the_deadline`` and
  ``test_a_round_that_ends_early_gives_the_settle_the_time_left``,
  (2) ``test_a_meter_that_fails_after_a_call_gives_a_reason_of_its_own``,
  (3) ``test_an_unknown_cost_charges_the_rest_of_the_fresh_goal``,
  (4) ``test_a_meter_that_hangs_after_the_call_charges_the_rest_inside_the_margin``
  asserts an order, not a time,
  (5) ``test_a_driver_that_turns_a_cancel_into_an_error_charges_the_rest``,
  (6) ``test_a_report_that_hangs_is_cut_and_the_job_ends``.

**The mutations** (each was run on the branch, then restored):

* M1. ``HeadlessTurn.run``: drop the candidate check.
  ``test_no_candidate_makes_no_call_and_touches_no_goal`` goes red.
* M2. ``_rounds``: drop the stop at ``cap_reached``.
  ``test_a_turn_stops_at_the_cap_and_reports_once`` goes red.
* M3. ``_rounds``: report again when a round starts on a blocked goal.
  ``test_a_second_delivery_reports_nothing`` goes red.
* M4. ``_rounds``: drop the flag read before each round.
  ``test_the_kill_switch_stops_the_next_round`` goes red.
* M5. ``headless_tools``: keep each name. ``test_only_read_only_tools_are_offered``
  goes red.
* M6. ``console_meter``: answer 0 when the door fails.
  ``test_the_default_meter_fails_closed`` goes red, and so do the cases of
  ``test_headless_turn_meter.py`` (AUTO-8b-2d).
* M7 (AUTO-8b-1). ``_cost``: charge 0 when the meter runs out of time.
  ``test_a_meter_that_hangs_after_the_call_charges_the_rest_inside_the_margin``
  and the R8 case go red.
* M8 (AUTO-8b-1). ``_not_ready``: drop the check of ``MAX_GOAL_CREDITS``.
  ``test_a_goal_raised_over_the_bound_between_rounds_gets_no_next_round``
  goes red.
* M9 (AUTO-8b-1). ``run``: take the member with
  ``dict(payload).pop("member", ...)``.
  ``test_the_module_takes_no_member_from_the_payload`` goes red.
* M10 (AUTO-8b-1). Start the clock in ``_rounds`` again.
  ``test_the_clock_of_the_turn_starts_before_recover`` goes red.
* M11 (AUTO-8b-2a). ``_ready``: drop the mark write (``marked = goal`` in
  place of ``await self._mark(...)``).
  ``test_a_lost_round_leaves_the_mark_and_the_next_turn_blocks`` goes red,
  and so does the R8 case of the same name.
* M12 (AUTO-8b-2a). ``goal_store.record_round``: drop the clear (send
  ``"clear": False``). The fake store here does not run that SQL, so the R8
  cases go red: ``test_r8_a_normal_round_clears_the_mark`` of
  ``test_headless_turn_r8.py`` and ``test_r8_a_round_can_clear_the_mark_in_
  the_same_write`` of ``test_goal_record_r8.py``.

* M13 (AUTO-8b-2d). ``_rounds``: read the meter after a cut call again
  (``cost = await self._cost(...)`` for every round).
  ``test_a_cut_call_whose_row_lands_late_charges_the_rest`` goes red, and so
  do ``test_the_time_cap_stops_the_turn``, the cut case of
  ``test_headless_turn_meter.py`` and the R8 case
  ``test_r8_a_cut_call_whose_row_lands_late_charges_the_rest``.

Run::

    uv run pytest tests/unit/test_headless_turn.py -q -p no:cacheprovider -rfEs
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.jobs", reason="gateway not installed")

import structlog
from acb_common import get_settings, goal_store
from gateway.jobs import headless_turn as ht
from gateway.jobs import queue, unattended, worker

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "jobs" / "headless_turn.py"

ORG = "0d4b8f5e-0000-4000-8000-0000000000b8"
OTHER_ORG = "0d4b8f5e-0000-4000-8000-0000000000b9"
ANA = "ana@auto8a.test"
CHECK = "test.found"
NOW = datetime(2099, 3, 10, 9, 0, tzinfo=UTC)


# ── The fakes ───────────────────────────────────────────────────────────────


class FakeGoals:
    """The goal store in memory, with the real cap rule and the real
    compare-and-set. ``writes`` counts the rounds that it recorded."""

    def __init__(self) -> None:
        self.rows: dict[str, goal_store.Goal] = {}
        self.writes = 0
        self.reads = 0
        #: The changes of each ``update_goal`` call, in order (the marks).
        self.updates: list[dict[str, Any]] = []
        #: The ``clear_job_id`` of each recorded round, in order.
        self.cleared: list[bool] = []

    def add(self, *, member: str = ANA, kind: str = ht.GOAL_KIND, round_cap: int = 40,
            cost_cap: str = "10", state: str = "active", used: str = "0") -> goal_store.Goal:
        gid = str(uuid.uuid4())
        goal = goal_store.Goal(
            id=gid, member_email=member, kind=kind, objective="Watch the open deals.",
            state=state, version=1, round_count=0, round_cap=round_cap,
            cost_used_credits=Decimal(used), cost_cap_credits=Decimal(cost_cap),
            block_code=None, job_id=None, created_at=NOW, updated_at=NOW, finished_at=None)
        self.rows[gid] = goal
        return goal

    def bump(self, gid: str) -> None:
        """Another writer: the version moves, nothing else."""
        g = self.rows[gid]
        self.rows[gid] = dataclasses.replace(g, version=g.version + 1)

    def raise_cap(self, gid: str, cap: Decimal) -> None:
        """Another writer changes the credit cap, by the real rule of
        ``goal_store.update_goal`` (``_check_changes`` and ``_after``)."""
        g = self.rows[gid]
        after = goal_store._after(g, goal_store._check_changes({"cost_cap_credits": cap}))
        self.rows[gid] = dataclasses.replace(
            g, state=after["state"], block_code=after["block"], round_cap=after["round_cap"],
            cost_cap_credits=after["cost_cap"], version=g.version + 1)

    def lost(self, gid: str, job_id: str) -> None:
        """A call of an earlier run whose round has no record: its mark
        stays on the goal, and the version moved with the mark write."""
        g = self.rows[gid]
        self.rows[gid] = dataclasses.replace(g, job_id=job_id, version=g.version + 1)

    async def goal_member(self, db: Any, goal_id: str) -> str | None:
        self.reads += 1
        g = self.rows.get(goal_id)
        return None if g is None else g.member_email

    async def get_goal(self, db: Any, member: str, goal_id: str) -> goal_store.Goal | None:
        self.reads += 1
        g = self.rows.get(goal_id)
        return g if g is not None and g.member_email == member.strip().lower() else None

    async def update_goal(self, db: Any, member: str, goal_id: str, expected: int,
                          **changes: Any) -> goal_store.Goal | None:
        """The real rule of ``update_goal`` (``_check_changes`` and
        ``_after``), with the real compare-and-set."""
        checked = goal_store._check_changes(changes)
        g = await self.get_goal(db, member, goal_id)
        if g is None:
            return None
        if g.version != expected:
            raise goal_store.StaleGoal(g.version)
        if g.state in goal_store.FINAL_STATES:
            raise goal_store.GoalStateError("final")
        after = goal_store._after(g, checked)
        new = dataclasses.replace(
            g, state=after["state"], block_code=after["block"], round_cap=after["round_cap"],
            cost_cap_credits=after["cost_cap"], job_id=after["job"], version=g.version + 1)
        self.rows[goal_id] = new
        self.updates.append(dict(changes))
        return new

    async def record_round(self, db: Any, member: str, goal_id: str, expected: int,
                           cost: Any, *, clear_job_id: bool = False) -> goal_store.Goal | None:
        g = await self.get_goal(db, member, goal_id)
        if g is None:
            return None
        if g.version != expected:
            raise goal_store.StaleGoal(g.version)
        if g.state != "active":
            raise goal_store.GoalStateError("not active")
        amount = goal_store._credits(cost, "cost_credits", allow_zero=True)
        rounds, used = g.round_count + 1, g.cost_used_credits + amount
        block = goal_store.cap_block(rounds, g.round_cap, used, g.cost_cap_credits)
        new = dataclasses.replace(
            g, round_count=rounds, cost_used_credits=used, version=g.version + 1,
            state="active" if block is None else "blocked", block_code=block,
            job_id=None if clear_job_id else g.job_id)
        self.rows[goal_id] = new
        self.writes += 1
        self.cleared.append(clear_job_id)
        return new


class FakeJobs:
    """The settle of the queue: which claim was done, and which gave up."""

    def __init__(self) -> None:
        self.done: list[str] = []
        self.given_up: list[tuple[str, str]] = []

    async def hold(self, db: Any, claim: queue.Claim) -> bool:
        return True

    async def complete(self, db: Any, claim: queue.Claim, *, now: datetime) -> int:
        self.done.append(claim.id)
        return 1

    async def give_up(self, db: Any, claim: queue.Claim, error: str, *, now: datetime) -> bool:
        self.given_up.append((claim.id, error))
        return True


class FakeRunner:
    """One round of the assistant. Each round spends ``cost`` credits on the
    meter of its run id, then runs ``side`` if one is set."""

    def __init__(self, meter: FakeMeter, *, cost: str = "1", sleep: float = 0.0,
                 raises: Exception | None = None) -> None:
        self.meter = meter
        self.cost = Decimal(cost)
        self.sleep = sleep
        self.raises = raises
        self.calls: list[dict[str, Any]] = []
        self.side: Any = None
        self.cancelled = False

    async def __call__(self, message: str, *, run_id: str, org: str, member: str,
                       tools: tuple[str, ...]) -> dict[str, Any]:
        from acb_common.no_person import no_person_present

        self.calls.append({"message": message, "run_id": run_id, "org": org,
                           "member": member, "tools": tools, "marked": no_person_present()})
        self.meter.spent[run_id] = self.meter.spent.get(run_id, Decimal(0)) + self.cost
        if self.side is not None:
            self.side(len(self.calls))
        if self.sleep:
            try:
                await asyncio.sleep(self.sleep)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.raises is not None:
            raise self.raises
        return {"answer": f"answer {len(self.calls)}"}


class FakeMeter:
    """The credits of each run id. A read BEFORE the call sleeps
    ``sleep_before`` seconds, and a read AFTER it sleeps ``sleep_after``.
    ``cancelled`` is True when a sleep was cut."""

    def __init__(self) -> None:
        self.spent: dict[str, Decimal] = {}
        self.reads = 0
        self.fail_after: bool = False
        self.sleep_before = 0.0
        self.sleep_after = 0.0
        self.cancelled = False
        #: The member of each read, in order (AUTO-8b-2d).
        self.members: list[str] = []

    async def __call__(self, org: str, member: str, run_id: str) -> Decimal:
        self.reads += 1
        self.members.append(member)
        after = run_id in self.spent
        if self.fail_after and after:
            raise ConnectionError("the Console did not answer")
        wait = self.sleep_after if after else self.sleep_before
        if wait:
            try:
                await asyncio.sleep(wait)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return self.spent.get(run_id, Decimal(0))


class LateRunner:
    """A call that outlives the cut of the time cap (AUTO-8b-2d). The
    Console route is a sync route in a thread pool, so a cancel does not stop
    it: it writes the usage row of the call AFTER the cancel. Here a thread
    writes ``cost`` on the meter of the run when the test sets ``release``.
    So a read just after the cut sees the old value, and a later read sees
    the cost."""

    def __init__(self, meter: FakeMeter, *, cost: str = "4") -> None:
        self.meter = meter
        self.cost = Decimal(cost)
        self.calls: list[str] = []
        self.release = threading.Event()
        self.late: list[threading.Thread] = []

    async def __call__(self, message: str, *, run_id: str, org: str, member: str,
                       tools: tuple[str, ...]) -> dict[str, Any]:
        self.calls.append(run_id)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            def land() -> None:
                self.release.wait(30)
                self.meter.spent[run_id] = self.meter.spent.get(run_id, Decimal(0)) + self.cost

            thread = threading.Thread(target=land, daemon=True)
            thread.start()
            self.late.append(thread)
            raise
        return {"answer": "never"}

    def land(self) -> None:
        """Let the late rows land, and wait for them."""
        self.release.set()
        for thread in self.late:
            thread.join(30)


class Reports:
    """The report of a goal at its cap. It sleeps ``sleep`` seconds after
    it saw the goal, and ``cancelled`` is True when the sleep was cut."""

    def __init__(self, sleep: float = 0.0) -> None:
        self.seen: list[goal_store.Goal] = []
        self.sleep = sleep
        self.cancelled = False

    async def __call__(self, org: str, member: str, goal: goal_store.Goal) -> None:
        self.seen.append(goal)
        if self.sleep:
            try:
                await asyncio.sleep(self.sleep)
            except asyncio.CancelledError:
                self.cancelled = True
                raise


@contextlib.asynccontextmanager
async def _session(org: str | None = None):
    yield SimpleNamespace(org=org)


def make_world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The fakes of one test, patched in. ``test_no_person_present.py``
    reuses it for the mark of the handler."""
    goals, jobs, meter = FakeGoals(), FakeJobs(), FakeMeter()
    settings = get_settings()
    monkeypatch.setattr(settings, "headless_turn_orgs", ORG)
    monkeypatch.setattr(ht, "tenant_session", _session)
    monkeypatch.setattr(goal_store, "goal_member", goals.goal_member)
    monkeypatch.setattr(goal_store, "get_goal", goals.get_goal)
    monkeypatch.setattr(goal_store, "record_round", goals.record_round)
    monkeypatch.setattr(goal_store, "update_goal", goals.update_goal)
    monkeypatch.setattr(queue, "hold", jobs.hold)
    monkeypatch.setattr(queue, "complete", jobs.complete)
    monkeypatch.setattr(queue, "give_up", jobs.give_up)
    monkeypatch.setattr(unattended, "get_settings",
                        lambda: SimpleNamespace(unattended_ledger=False))
    departed: dict[str, str | None] = {}

    async def _departed(org: str, member: str) -> str | None:
        return departed.get(member)

    monkeypatch.setattr(ht, "_departed", _departed)
    found = {"value": True}

    async def _check(org: str, member: str, payload: dict[str, Any]) -> bool:
        return found["value"]

    return SimpleNamespace(goals=goals, jobs=jobs, meter=meter, departed=departed,
                           found=found, checks={CHECK: _check}, settings=settings)


@pytest.fixture
def world(monkeypatch):
    return make_world(monkeypatch)


def _claim(payload: dict[str, Any]) -> queue.Claim:
    return queue.Claim(id=str(uuid.uuid4()), organization_id=ORG, kind=ht.KIND,
                       payload=payload, attempts=1, max_attempts=1, token=str(uuid.uuid4()))


def _payload(goal: goal_store.Goal, check: str = CHECK) -> dict[str, Any]:
    return {"goal_id": goal.id, "candidate": check}


def _turn(world: Any, runner: Any = None, **kw: Any) -> ht.HeadlessTurn:
    kw.setdefault("meter", world.meter)
    kw.setdefault("candidates", world.checks)
    return ht.HeadlessTurn(runner=runner, **kw)


def _run(turn: ht.HeadlessTurn, payload: dict[str, Any],
         claim: queue.Claim | None = None) -> queue.Claim:
    claim = claim or _claim(payload)
    asyncio.run(turn.run(ORG, payload, claim))
    return claim


def _always(_result: Any) -> bool:
    return True


# ── The flag ────────────────────────────────────────────────────────────────


def test_the_flag_ships_empty_and_reads_each_call(monkeypatch) -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["headless_turn_orgs"].default == ""
    settings = get_settings()
    for raw, want in (("", False), (OTHER_ORG, False), (f" {ORG.upper()} ,x", True),
                      ("*", True), (f"{OTHER_ORG},{ORG}", True)):
        monkeypatch.setattr(settings, "headless_turn_orgs", raw)
        assert ht.headless_allowed(ORG) is want, raw
    monkeypatch.setattr(settings, "headless_turn_orgs", "*")
    for nobody in (None, "", "  "):
        assert ht.headless_allowed(nobody) is False


def test_the_flag_is_named_in_each_place_of_a_flag() -> None:
    from acb_common import env_guard
    from gateway.flag_reload import RELOADABLE

    env = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^HEADLESS_TURN_ORGS=$", env, re.MULTILINE)
    assert "HEADLESS_TURN_ORGS" in env_guard.PLATFORM_ENV_NAMES
    assert env_guard.is_platform_env("HEADLESS_TURN_ORGS") is True
    assert "HEADLESS_TURN_ORGS" in RELOADABLE


def test_headless_allowed_is_the_one_reader_of_the_flag() -> None:
    # env_guard names the key and reads no value.
    allowed = {"apps/services/gateway/gateway/jobs/headless_turn.py",
               "packages/acb_common/acb_common/env_guard.py"}
    found: set[str] = set()
    for base in ("apps", "packages"):
        for path in (_ROOT / base).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            body = path.read_text(encoding="utf-8", errors="replace")
            if "headless_turn_orgs" not in body.lower():
                continue
            for node in ast.walk(ast.parse(body)):
                hit = (isinstance(node, ast.Attribute) and node.attr == "headless_turn_orgs") or (
                    isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.strip().lower() == "headless_turn_orgs")
                if hit:
                    found.add(path.relative_to(_ROOT).as_posix())
    assert found == allowed, found ^ allowed


def test_the_kind_registers_only_with_the_job_queue_on(monkeypatch) -> None:
    monkeypatch.setattr(worker, "_HANDLERS", {})
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=False))
    assert ht.register_jobs() is False
    assert ht.KIND not in worker.kinds()
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(job_queue=True))
    assert ht.register_jobs() is True
    entry = worker._HANDLERS[ht.KIND]
    assert (entry.handler, entry.mode, entry.idempotent) == (ht.run_turn, "detached", False)


def test_the_default_turn_has_the_console_meter_no_candidate_and_reads_only() -> None:
    """With the flag on and nothing else, no turn makes a model call: no
    candidate check exists. The default meter is the Console reader of
    AUTO-8b-2d, and with no proven member it raises before any hop."""
    default = ht._DEFAULT
    assert default._meter is ht.console_meter
    assert default._runner is ht.run_worker
    assert default._go_on is ht.one_round
    assert default._candidates is None and ht._CANDIDATES == {}
    assert default._tools == ht.DEFAULT_TOOLS
    with pytest.raises(ht.CostReadFailed):
        asyncio.run(ht.console_meter(ORG, ANA, "run-1"))


# ── No candidate, no call ───────────────────────────────────────────────────


@pytest.mark.parametrize("case", ["unknown_name", "false", "truthy_not_true"])
def test_no_candidate_makes_no_call_and_touches_no_goal(world, case) -> None:
    """Acceptance 2: no runner call, no meter read, no round, no usage."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    checks = dict(world.checks)

    async def truthy(org: str, member: str, payload: dict[str, Any]) -> Any:
        return "yes"

    if case == "false":
        world.found["value"] = False
    if case == "truthy_not_true":
        checks[CHECK] = truthy
    name = "test.missing" if case == "unknown_name" else CHECK
    claim = _run(_turn(world, runner, candidates=checks, go_on=_always),
                 _payload(goal, name))
    assert runner.calls == [] and world.meter.reads == 0 and world.goals.writes == 0
    assert world.goals.rows[goal.id] == goal  # untouched: version 1
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []


def test_a_candidate_check_that_raises_gives_up_with_no_call(world) -> None:
    goal = world.goals.add()
    runner = FakeRunner(world.meter)

    async def broken(org: str, member: str, payload: dict[str, Any]) -> bool:
        raise RuntimeError("the read failed")

    claim = _run(_turn(world, runner, candidates={CHECK: broken}), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.jobs.given_up == [(claim.id, ht.CANDIDATE_FAILED)]


def test_the_candidate_check_gets_the_member_of_the_goal(world) -> None:
    goal = world.goals.add()
    seen: list[tuple[str, str, dict[str, Any]]] = []

    async def check(org: str, member: str, payload: dict[str, Any]) -> bool:
        seen.append((org, member, payload))
        return False

    _run(_turn(world, FakeRunner(world.meter), candidates={CHECK: check}), _payload(goal))
    assert seen == [(ORG, ANA, _payload(goal))]


# ── The cap, and the one report ─────────────────────────────────────────────


def test_a_turn_stops_at_the_cap_and_reports_once(world) -> None:
    """Acceptance 1: a round past the credit cap blocks the goal with
    ``cap_reached`` in ONE write, the report runs once, and no second round
    starts, though the rule of the next round says go on."""
    goal = world.goals.add(cost_cap="1")
    runner, reports = FakeRunner(world.meter, cost="2"), Reports()
    claim = _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    # Version 2 is the mark, and version 3 the round that clears it.
    assert (after.state, after.block_code, after.version) == ("blocked", "cap_reached", 3)
    assert (after.round_count, after.cost_used_credits, after.job_id) == (1, Decimal("2"), None)
    assert world.goals.writes == 1
    assert [g.version for g in reports.seen] == [3]
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []


def test_a_second_delivery_reports_nothing(world) -> None:
    """Acceptance 1, the second half: the same job again, and a new job of
    the same payload, read a blocked goal. No call, no write, no report."""
    goal = world.goals.add(cost_cap="1")
    runner, reports = FakeRunner(world.meter, cost="2"), Reports()
    turn = _turn(world, runner, report=reports, go_on=_always)
    first = _run(turn, _payload(goal))
    _run(turn, _payload(goal), claim=first)
    _run(turn, _payload(goal))
    assert len(runner.calls) == 1 and world.goals.writes == 1
    assert len(reports.seen) == 1
    assert world.goals.rows[goal.id].version == 3


def test_the_round_cap_of_the_goal_binds_too(world) -> None:
    goal = world.goals.add(round_cap=2, cost_cap="50")
    runner, reports = FakeRunner(world.meter, cost="0.5"), Reports()
    _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 2
    assert (after.state, after.block_code, after.round_count) == ("blocked", "cap_reached", 2)
    assert len(reports.seen) == 1


def test_a_cap_over_the_bound_gets_no_turn(world) -> None:
    goal = world.goals.add(cost_cap=str(ht.MAX_GOAL_CREDITS + 1))
    runner = FakeRunner(world.meter)
    claim = _run(_turn(world, runner), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.jobs.given_up == [(claim.id, ht.CAP_TOO_HIGH)]


def test_a_goal_raised_over_the_bound_between_rounds_gets_no_next_round(world) -> None:
    """AUTO-8b-1 (b): another write raises the credit cap over
    ``MAX_GOAL_CREDITS`` after round 1. The goal stays active, because the
    rule of ``update_goal`` allows it. Round 2 reads the goal, and no call
    runs, though the rule of the next round says go on."""
    goal = world.goals.add(cost_cap="10")
    runner = FakeRunner(world.meter, cost="1")

    def raise_cap(_result: Any) -> bool:
        world.goals.raise_cap(goal.id, ht.MAX_GOAL_CREDITS + 1)
        return True

    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=raise_cap), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1 and world.goals.writes == 1
    assert (after.state, after.round_count) == ("active", 1)
    assert after.cost_cap_credits == ht.MAX_GOAL_CREDITS + 1
    ended = [c["outcome"] for c in caps if c["event"] == "headless_turn.ended"]
    assert ended == ["cap_too_high"]
    assert [c["log_level"] for c in caps if c["event"] == "headless_turn.cap_too_high"] == [
        "warning"]
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []


def test_a_meter_that_fails_after_the_call_charges_the_rest_of_the_budget(world) -> None:
    """Fail closed for money: a round whose cost nobody can read counts as
    the rest of the budget, so the goal blocks and no round follows."""
    goal = world.goals.add(cost_cap="10")
    world.meter.fail_after = True
    runner, reports = FakeRunner(world.meter, cost="1"), Reports()
    with structlog.testing.capture_logs() as caps:
        _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (after.block_code, after.cost_used_credits) == ("cap_reached", Decimal("10"))
    assert len(reports.seen) == 1
    assert [c["log_level"] for c in caps if c["event"] == "headless_turn.cost_unknown"] == [
        "error"]


def test_a_failed_round_charges_the_rest_and_gives_up(world) -> None:
    """AUTO-8b-2d: a call that raised can still write its usage row after a
    read. So the round reads no meter and charges the rest of the budget,
    the goal blocks with ``cap_reached``, the report runs once, and the job
    still gives up with ``TurnFailed``. (Before AUTO-8b-2d the round
    recorded the meter's 1 credit and the goal stayed active.)"""
    goal = world.goals.add()
    runner = FakeRunner(world.meter, cost="1", raises=RuntimeError("the model failed"))
    reports = Reports()
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1 and world.meter.reads == 1  # the read before only
    assert (after.round_count, after.cost_used_credits) == (1, Decimal("10"))
    assert (after.state, after.block_code, after.job_id) == ("blocked", "cap_reached", None)
    assert len(reports.seen) == 1
    assert world.jobs.given_up == [(claim.id, ht.TURN_FAILED)]
    lines = [(c["log_level"], c["error"]) for c in caps if c["event"] == "headless_turn.round_cut"]
    assert lines == [("warning", "RuntimeError")]


def test_a_stale_goal_records_the_round_once_more_and_stops(world) -> None:
    """Another write during the call moves the version. The round is
    recorded on the new version, so its cost is not lost, and no second
    round starts."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter, cost="1")
    runner.side = lambda _n: world.goals.bump(goal.id)
    claim = _run(_turn(world, runner, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    # 2 the mark, 3 the other write, 4 the round on the new version.
    assert (after.round_count, after.version, after.job_id) == (1, 4, None)
    assert world.jobs.done == [claim.id]


# ── The kill switch, the round cap and the time cap ─────────────────────────


def test_the_flag_off_reads_nothing_and_calls_nothing(world) -> None:
    goal = world.goals.add()
    world.settings.headless_turn_orgs = ""
    runner = FakeRunner(world.meter)
    claim = _run(_turn(world, runner), _payload(goal))
    assert runner.calls == [] and world.goals.reads == 0
    assert world.jobs.done == [claim.id]


@pytest.mark.parametrize("left", ["", OTHER_ORG])
def test_the_kill_switch_stops_the_next_round(world, left) -> None:
    """Acceptance 4: the flag goes off, or the org leaves the list, during
    round 1. Round 2 does not start, and the turn ends ok with no report."""
    world.settings.headless_turn_orgs = f"{OTHER_ORG},{ORG}"
    goal = world.goals.add()
    runner, reports = FakeRunner(world.meter), Reports()

    def _flip(_n: int) -> None:
        world.settings.headless_turn_orgs = left

    runner.side = _flip
    claim = _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    assert len(runner.calls) == 1 and reports.seen == []
    assert world.goals.rows[goal.id].round_count == 1
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []


def test_the_round_cap_stops_the_turn(world) -> None:
    goal = world.goals.add(round_cap=40, cost_cap="50")
    runner, reports = FakeRunner(world.meter, cost="0.1"), Reports()
    with structlog.testing.capture_logs() as caps:
        _run(_turn(world, runner, report=reports, go_on=_always, rounds=3), _payload(goal))
    assert len(runner.calls) == 3 and world.goals.writes == 3
    assert world.goals.rows[goal.id].state == "active" and reports.seen == []
    ended = [c for c in caps if c["event"] == "headless_turn.ended"]
    assert [(c["outcome"], c["rounds"]) for c in ended] == [("round_cap", 3)]
    # The answer of each round reaches the next one.
    assert "answer 1" in runner.calls[1]["message"]


def test_the_time_cap_stops_the_turn(world) -> None:
    """The time cap cancels the call at the limit, and no second round
    starts.

    ⚠️ Changed by AUTO-8b-2d. The round used to record the meter's 1 credit
    and end ``time_cap``. But the fake runner here writes its cost BEFORE it
    sleeps, so it could not show the real race: the Console writes the row of
    a cut call AFTER the cut. So a cut round now reads no meter and charges
    the rest of the budget. The goal blocks with ``cap_reached``, and the
    turn ends ``cap_reached``."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter, cost="1", sleep=30.0)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always, time_cap_s=0.2), _payload(goal))
    assert len(runner.calls) == 1 and runner.cancelled is True
    after = world.goals.rows[goal.id]
    assert (after.cost_used_credits, after.block_code) == (Decimal("10"), "cap_reached")
    assert world.meter.reads == 1  # the read before the call, and none after the cut
    ended = [c for c in caps if c["event"] == "headless_turn.ended"]
    assert [c["outcome"] for c in ended] == ["cap_reached"]
    lines = [(c["log_level"], c["error"]) for c in caps if c["event"] == "headless_turn.round_cut"]
    assert lines == [("warning", "TimeCap")]
    assert world.jobs.done == [claim.id]


def test_a_cut_call_whose_row_lands_late_charges_the_rest(world) -> None:
    """AUTO-8b-2d, the P1 of its review. The time cap cuts the call, and the
    usage row of the call lands AFTER the cut. A read at the cut would see 0
    and record 0, so the next turn would get room that the goal does not
    have. The round reads no meter and charges the rest of the budget (10
    minus 3). Then the late row lands, and a later read sees its 4 credits,
    which the charge of 7 covers."""
    goal = world.goals.add(cost_cap="10", used="3")
    runner, reports = LateRunner(world.meter, cost="4"), Reports()
    claim = _run(_turn(world, runner, report=reports, go_on=_always,
                       time_cap_s=0.3, clock=lambda: 0.0), _payload(goal))
    after = world.goals.rows[goal.id]
    [run_id] = runner.calls
    assert (after.block_code, after.cost_used_credits, after.job_id) == (
        "cap_reached", Decimal("10"), None)
    assert world.meter.reads == 1 and len(reports.seen) == 1
    assert world.jobs.done == [claim.id]
    runner.land()
    late = asyncio.run(world.meter(ORG, ANA, run_id))
    assert late == Decimal("4") <= after.cost_used_credits - goal.cost_used_credits


def test_one_round_is_the_default(world) -> None:
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    _run(_turn(world, runner), _payload(goal))
    assert len(runner.calls) == 1


def test_the_caps_are_bounded() -> None:
    assert ht.DEFAULT_ROUNDS == 5 and ht.DEFAULT_TIME_CAP_S == 120.0
    assert ht.DEFAULT_ROUNDS <= ht.MAX_ROUNDS
    assert ht.DEFAULT_TIME_CAP_S <= ht.MAX_TIME_CAP_S
    # The turn records its last round and settles before the worker cancels it.
    # The clock starts in run(), before unattended.recover, so the time cap
    # holds the checks too (test_the_clock_of_the_turn_starts_before_recover).
    assert ht.MAX_TIME_CAP_S + ht.SETTLE_MARGIN_S <= worker.HANDLER_TIMEOUT
    assert queue.LEASE.total_seconds() > worker.HANDLER_TIMEOUT
    # The meter read and the record take one part each, and the report, the
    # ledger and the settle of the job keep the last part.
    assert ht.SETTLE_PARTS >= 3
    assert 2 * ht._settle_limit() < ht.SETTLE_MARGIN_S
    # AUTO-8b-2a P2 (6): the report keeps to half of the last part.
    assert 0 < ht._report_limit() < ht.SETTLE_MARGIN_S - 2 * ht._settle_limit()
    assert unattended.STALL_AFTER.total_seconds() > ht.MAX_TIME_CAP_S
    assert Decimal(0) < ht.MAX_GOAL_CREDITS < Decimal(500)
    for bad in (0, ht.MAX_ROUNDS + 1, True, 2.0):
        with pytest.raises(ValueError):
            ht.HeadlessTurn(rounds=bad)
    for bad in (0, -1, ht.MAX_TIME_CAP_S + 1, True):
        with pytest.raises(ValueError):
            ht.HeadlessTurn(time_cap_s=bad)


# ── The settle of a round (AUTO-8b-1) ───────────────────────────────────────


def test_a_meter_that_hangs_after_the_call_charges_the_rest_inside_the_margin(
        world, monkeypatch) -> None:
    """AUTO-8b-1 (a), acceptance 1: the meter after the call sleeps past its
    share of ``SETTLE_MARGIN_S``. The settle cuts it, ``record_round`` runs,
    and the charge is the cap minus the credits used. So the goal blocks
    with ``cap_reached`` and the report runs once.

    AUTO-8b-2a P2 (4): the test asserts an ORDER (the call, the cut of the
    meter, the record), never a wall-clock time. The call ends at the
    deadline of the turn's clock, so the meter gets one part (P2 (1))."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.6)
    goal = world.goals.add(cost_cap="10", used="3")
    world.meter.sleep_after = 30.0
    runner, reports = FakeRunner(world.meter, cost="1"), Reports()
    now = [0.0]
    events: list[str] = []
    charged: list[Decimal] = []

    def at_the_deadline(_n: int) -> None:
        events.append("called")
        now[0] = ht.DEFAULT_TIME_CAP_S

    runner.side = at_the_deadline
    real = goal_store.record_round

    async def ordered(db: Any, member: str, goal_id: str, expected: int, cost: Any,
                      **kw: Any) -> Any:
        events.append("meter_cut" if world.meter.cancelled else "meter_answered")
        events.append("recorded")
        charged.append(Decimal(cost))
        return await real(db, member, goal_id, expected, cost, **kw)

    monkeypatch.setattr(goal_store, "record_round", ordered)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, report=reports, go_on=_always,
                           clock=lambda: now[0]), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1 and world.meter.cancelled is True
    assert events == ["called", "meter_cut", "recorded"]
    assert charged == [goal.cost_cap_credits - goal.cost_used_credits] == [Decimal("7")]
    assert (after.block_code, after.cost_used_credits, after.version) == (
        "cap_reached", Decimal("10"), 3)
    assert len(reports.seen) == 1
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []
    lines = [(c["log_level"], c["error"]) for c in caps if c["event"] == "headless_turn.cost_unknown"]
    assert lines == [("error", "MeterTimeout")]


def test_a_meter_that_hangs_before_the_call_makes_no_call(world, monkeypatch) -> None:
    """The meter BEFORE the call does not answer in time: no call, no
    write, and the job gives up with ``NoCostReader``."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.3)
    goal = world.goals.add()
    world.meter.sleep_before = 30.0
    runner = FakeRunner(world.meter)
    claim = _run(_turn(world, runner, go_on=_always), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0 and world.meter.cancelled is True
    assert world.goals.rows[goal.id] == goal
    assert world.jobs.given_up == [(claim.id, ht.NO_READER)]


def test_a_timeout_of_the_meter_itself_is_not_the_settle_timeout(world) -> None:
    """The settle catches the TimeoutError of its own scope only. A meter
    that raises a TimeoutError of its own before the call is a failed round,
    not ``NoCostReader``, and no call runs."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter)

    async def own_timeout(org: str, member: str, run_id: str) -> Any:
        raise TimeoutError("the meter's own HTTP timeout")

    claim = _run(_turn(world, runner, meter=own_timeout), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.jobs.given_up == [(claim.id, ht.TURN_FAILED)]


def test_a_record_that_hangs_gives_up_inside_the_margin(world, monkeypatch) -> None:
    """``record_round`` does not end in time. The turn logs the round that
    it could not record at error, and gives up with ``TurnFailed``."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.3)
    goal = world.goals.add()
    cut: list[bool] = []

    async def hangs(*_a: Any, **_kw: Any) -> Any:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cut.append(True)
            raise

    monkeypatch.setattr(goal_store, "record_round", hangs)
    runner = FakeRunner(world.meter)
    # The call ends at the deadline, so the record gets one part (P2 (1)).
    now = [0.0]
    runner.side = lambda _n: now.__setitem__(0, ht.DEFAULT_TIME_CAP_S)
    started = time.monotonic()
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always, clock=lambda: now[0]), _payload(goal))
    assert time.monotonic() - started < 10
    assert len(runner.calls) == 1 and cut == [True]
    assert world.jobs.given_up == [(claim.id, ht.TURN_FAILED)]
    lines = [(c["log_level"], c["error"]) for c in caps
             if c["event"] == "headless_turn.round_unrecorded"]
    assert lines == [("error", "RecordTimeout")]
    # AUTO-8b-2a: the round has no record, so the mark stays on the goal.
    assert world.goals.rows[goal.id].job_id == claim.id


@pytest.mark.parametrize("step", ["before", "cost", "record"])
def test_the_settle_never_swallows_a_cancel(world, monkeypatch, step) -> None:
    """A cancel from outside (a stop of the worker) during a step of the
    settle goes through. The step neither charges nor records."""
    goal = world.goals.add()
    turn = _turn(world, FakeRunner(world.meter))
    run_id = str(uuid.uuid4())
    world.meter.sleep_before = world.meter.sleep_after = 30.0

    async def hangs(*_a: Any, **_kw: Any) -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr(goal_store, "record_round", hangs)

    def call() -> Any:
        if step == "before":
            return turn._before(ORG, ANA, run_id)
        if step == "cost":
            world.meter.spent[run_id] = Decimal("1")
            return turn._cost(ORG, ANA, run_id, Decimal(0), goal, 0.0)
        return turn._record(ORG, ANA, goal, Decimal("1"), 0.0)

    async def main() -> None:
        task = asyncio.get_running_loop().create_task(call())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with structlog.testing.capture_logs() as caps:
        asyncio.run(main())
    assert world.goals.writes == 0
    assert [c for c in caps if c["event"] in (
        "headless_turn.cost_unknown", "headless_turn.round_unrecorded")] == []


def test_the_clock_of_the_turn_starts_before_recover(world, monkeypatch) -> None:
    """AUTO-8b-1 (a): the clock starts in ``run``, before
    ``unattended.recover``. A recover that spends the whole time cap leaves no
    time, so no meter read and no call run."""
    now = [1000.0]

    async def slow_recover(*_a: Any, **_kw: Any) -> bool:
        now[0] += ht.DEFAULT_TIME_CAP_S + 1
        return False

    monkeypatch.setattr(unattended, "recover", slow_recover)
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always, clock=lambda: now[0]), _payload(goal))
    assert runner.calls == [] and world.meter.reads == 0 and world.goals.writes == 0
    assert [c["outcome"] for c in caps if c["event"] == "headless_turn.ended"] == ["time_cap"]
    assert world.jobs.done == [claim.id]


# ── The mark of a call (AUTO-8b-2a, the P1) ─────────────────────────────────


def _run_cancelled(turn: ht.HeadlessTurn, payload: dict[str, Any], claim: queue.Claim,
                   runner: FakeRunner) -> None:
    """Run *turn*, and cancel it from outside while the runner is in its
    call, as ``worker.stop_job_worker`` cancels a tick."""
    async def main() -> None:
        called = asyncio.Event()
        runner.side = lambda _n: called.set()
        task = asyncio.get_running_loop().create_task(turn.run(ORG, payload, claim))
        await asyncio.wait_for(called.wait(), timeout=30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The rounds run in the task of the stall guard. Let its cancel land.
        for _ in range(3000):
            if runner.cancelled:
                return
            await asyncio.sleep(0.01)

    asyncio.run(main())


def test_a_mark_is_written_before_each_call_and_the_record_clears_it(world) -> None:
    """Before each call, the goal holds the claim id in ``job_id``. The
    record of the round clears it in the same write."""
    goal = world.goals.add(cost_cap="50")
    runner = FakeRunner(world.meter, cost="1")
    seen: list[str | None] = []
    runner.side = lambda _n: seen.append(world.goals.rows[goal.id].job_id)
    claim = _claim(_payload(goal))
    _run(_turn(world, runner, go_on=_always, rounds=2), _payload(goal), claim=claim)
    after = world.goals.rows[goal.id]
    assert seen == [claim.id, claim.id]
    assert world.goals.updates == [{"job_id": claim.id}, {"job_id": claim.id}]
    assert world.goals.cleared == [True, True]
    assert (after.job_id, after.round_count, after.version) == (None, 2, 5)
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []


@pytest.mark.parametrize("whose", ["another_job", "the_same_job_id_as_the_claim"])
def test_a_goal_that_holds_a_mark_blocks_at_once_with_no_call(world, whose) -> None:
    """Step 4: the goal holds a mark, so a call of an earlier run has no
    record. The turn charges the rest of the budget in one write that clears
    the mark. No meter read, no candidate check and no call.

    A mark with the same job id as the claim is the mark of an earlier try
    of this job (a lost lease reuses ``Claim.id``). So it is a lost round
    too: the turn never compares a mark with its own claim id."""
    goal = world.goals.add(cost_cap="10", used="2")
    claim = _claim(_payload(goal))
    world.goals.lost(goal.id, claim.id if whose != "another_job" else str(uuid.uuid4()))
    runner, reports = FakeRunner(world.meter), Reports()
    asked: list[str] = []

    async def check(org: str, member: str, payload: dict[str, Any]) -> bool:
        asked.append(member)
        return True

    turn = _turn(world, runner, report=reports, candidates={CHECK: check}, go_on=_always)
    with structlog.testing.capture_logs() as caps:
        _run(turn, _payload(goal), claim=claim)
    after = world.goals.rows[goal.id]
    assert runner.calls == [] and world.meter.reads == 0 and asked == []
    assert (after.state, after.block_code, after.job_id) == ("blocked", "cap_reached", None)
    assert (after.round_count, after.cost_used_credits, after.version) == (1, Decimal("10"), 3)
    assert world.goals.cleared == [True] and world.goals.updates == []
    assert [g.version for g in reports.seen] == [3]
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []
    assert [c["log_level"] for c in caps if c["event"] == "headless_turn.lost_round"] == ["error"]
    # A second delivery reads a blocked goal: no call, no write, no report.
    _run(turn, _payload(goal))
    assert runner.calls == [] and world.goals.writes == 1 and len(reports.seen) == 1


@pytest.mark.parametrize("how", ["cancel_during_the_call", "record_timeout"])
def test_a_lost_round_leaves_the_mark_and_the_next_turn_blocks(world, monkeypatch, how) -> None:
    """The P1 example: round 1 spends 8 of 10 credits, and its cost does not
    reach the goal row. A stop of the worker cancels the call, or the record
    does not end in time. The mark stays, so the next turn gets no room: it
    charges the rest (10) and makes no call. Mutation M11 turns it red."""
    goal = world.goals.add(cost_cap="10")
    runner = FakeRunner(world.meter, cost="8")
    first = _claim(_payload(goal))
    if how == "cancel_during_the_call":
        runner.sleep = 30.0
        _run_cancelled(_turn(world, runner, go_on=_always), _payload(goal), first, runner)
        assert runner.cancelled is True and world.jobs.done == []
    else:
        monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.3)
        real = goal_store.record_round

        async def hangs(*_a: Any, **_kw: Any) -> Any:
            await asyncio.sleep(30)

        monkeypatch.setattr(goal_store, "record_round", hangs)
        now = [0.0]
        runner.side = lambda _n: now.__setitem__(0, ht.DEFAULT_TIME_CAP_S)
        _run(_turn(world, runner, go_on=_always, clock=lambda: now[0]), _payload(goal),
             claim=first)
        assert world.jobs.given_up == [(first.id, ht.TURN_FAILED)]
        monkeypatch.setattr(goal_store, "record_round", real)
    lost = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (lost.job_id, lost.round_count, lost.state) == (first.id, 0, "active")
    assert lost.cost_used_credits == Decimal("0")
    # The next turn of the goal: a new job.
    following, reports = FakeRunner(world.meter, cost="1"), Reports()
    second = _run(_turn(world, following, report=reports, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert following.calls == []
    assert (after.state, after.block_code, after.job_id) == ("blocked", "cap_reached", None)
    assert after.cost_used_credits == Decimal("10") and len(reports.seen) == 1
    assert second.id in world.jobs.done


@pytest.mark.parametrize("how", ["raises", "hangs", "not_written"])
def test_a_failed_mark_write_makes_no_call(world, monkeypatch, how) -> None:
    """A FAILED mark write makes NO call. The turn gives up with
    ``MarkFailed`` and logs it at error: fail closed."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.3)
    goal = world.goals.add()
    runner = FakeRunner(world.meter)

    async def broken(db: Any, member: str, goal_id: str, expected: int,
                     **changes: Any) -> Any:
        if how == "raises":
            raise ConnectionError("the database did not answer")
        if how == "hangs":
            await asyncio.sleep(30)
        return world.goals.rows[goal_id]  # an answer, and no write

    monkeypatch.setattr(goal_store, "update_goal", broken)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.goals.rows[goal.id] == goal
    assert world.jobs.given_up == [(claim.id, ht.MARK_FAILED)]
    want = {"raises": "ConnectionError", "hangs": "MarkTimeout",
            "not_written": "MarkNotWritten"}[how]
    lines = [(c["log_level"], c["error"]) for c in caps if c["event"] == "headless_turn.mark_failed"]
    assert lines == [("error", want)]


def test_a_stale_mark_write_makes_no_call(world, monkeypatch) -> None:
    """Another write comes between the read of the goal and the mark. The
    compare-and-set refuses the mark, so no call runs and no mark stays."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    real = goal_store.update_goal

    async def other_first(db: Any, member: str, goal_id: str, expected: int,
                          **changes: Any) -> Any:
        world.goals.bump(goal_id)
        return await real(db, member, goal_id, expected, **changes)

    monkeypatch.setattr(goal_store, "update_goal", other_first)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert runner.calls == [] and world.goals.writes == 0
    assert (after.job_id, after.version) == (None, 2)
    assert [c["outcome"] for c in caps if c["event"] == "headless_turn.ended"] == ["stale"]
    assert world.jobs.done == [claim.id]


def test_no_time_after_the_mark_clears_it_and_makes_no_call(world, monkeypatch) -> None:
    """The time cap runs out during the mark write. No call runs, and the
    turn clears the mark, so the next turn charges no call that never ran."""
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    now = [0.0]
    real = goal_store.update_goal

    async def slow_mark(db: Any, member: str, goal_id: str, expected: int,
                        **changes: Any) -> Any:
        if changes.get("job_id") is not None:
            now[0] = ht.DEFAULT_TIME_CAP_S + 1
        return await real(db, member, goal_id, expected, **changes)

    monkeypatch.setattr(goal_store, "update_goal", slow_mark)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, go_on=_always, clock=lambda: now[0]), _payload(goal))
    after = world.goals.rows[goal.id]
    assert runner.calls == [] and world.goals.writes == 0
    assert world.goals.updates == [{"job_id": claim.id}, {"job_id": None}]
    assert (after.job_id, after.round_count, after.version) == (None, 0, 3)
    assert [c["outcome"] for c in caps if c["event"] == "headless_turn.ended"] == ["time_cap"]
    assert world.jobs.done == [claim.id]


def test_a_mark_seen_before_a_later_round_blocks_the_goal(world) -> None:
    """Step b: after round 1, the mark of another run lands on the goal (a
    second live turn of one goal, against the producer rule). Round 2 reads
    it, charges the rest and makes no call. A turn never reads its own live
    mark, because each of its records clears its own."""
    goal = world.goals.add(cost_cap="10")
    runner, reports = FakeRunner(world.meter, cost="1"), Reports()
    other = str(uuid.uuid4())

    def go_on(_result: Any) -> bool:
        world.goals.lost(goal.id, other)
        return True

    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, report=reports, go_on=go_on), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (after.block_code, after.cost_used_credits, after.job_id, after.round_count) == (
        "cap_reached", Decimal("10"), None, 2)
    assert len(reports.seen) == 1
    assert [c["outcome"] for c in caps if c["event"] == "headless_turn.ended"] == ["lost_round"]
    assert world.jobs.done == [claim.id]


# ── The six P2 notes of the AUTO-8b-1 review (AUTO-8b-2a) ───────────────────


def test_the_settle_shares_follow_the_deadline(monkeypatch) -> None:
    """P2 (1): the meter after a call gets max(part, deadline + part -
    clock()), and the record gets max(part, deadline + 2 * part - clock())."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 60.0)
    now = [0.0]
    turn = ht.HeadlessTurn(clock=lambda: now[0])
    assert ht._settle_limit() == 20.0
    for at, meter, record in ((50.0, 70.0, 90.0), (100.0, 20.0, 40.0),
                              (110.0, 20.0, 30.0), (150.0, 20.0, 20.0)):
        now[0] = at
        assert (turn._share(100.0, 1), turn._share(100.0, 2)) == (meter, record), at


def test_a_round_that_ends_early_gives_the_settle_the_time_left(world, monkeypatch) -> None:
    """P2 (1), the behaviour: the call ends long before the deadline. A meter
    and a record that each take longer than one part still answer, so the
    round charges its real cost, not the rest of the budget. With fixed
    shares of one part, both would be cut."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.15)
    goal = world.goals.add(cost_cap="10")
    world.meter.sleep_after = 0.3
    runner = FakeRunner(world.meter, cost="1")
    real = goal_store.record_round

    async def slow(*a: Any, **kw: Any) -> Any:
        await asyncio.sleep(0.3)
        return await real(*a, **kw)

    monkeypatch.setattr(goal_store, "record_round", slow)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner), _payload(goal))
    after = world.goals.rows[goal.id]
    assert world.meter.cancelled is False
    assert (after.cost_used_credits, after.state, after.job_id) == (Decimal("1"), "active", None)
    assert [c for c in caps if c["event"] in (
        "headless_turn.cost_unknown", "headless_turn.round_unrecorded")] == []
    assert world.jobs.done == [claim.id]


@pytest.mark.parametrize("how", ["raises", "hangs"])
def test_a_meter_that_fails_after_a_call_gives_a_reason_of_its_own(
        world, monkeypatch, how) -> None:
    """P2 (2): round 1 made a call. The meter before round 2 fails, so no
    second call runs. The reason is ``MeterFailed``, not ``NoCostReader``,
    because a reader did answer in round 1."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.3)
    goal = world.goals.add(cost_cap="10")
    reads = [0]

    async def meter(org: str, member: str, run_id: str) -> Any:
        reads[0] += 1
        if reads[0] == 3:  # the read before round 2
            if how == "raises":
                raise ht.NoCostReader("the reader went away")
            await asyncio.sleep(30)
        return world.meter.spent.get(run_id, Decimal(0))

    runner = FakeRunner(world.meter, cost="1")
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, meter=meter, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (after.round_count, after.cost_used_credits, after.job_id) == (1, Decimal("1"), None)
    assert world.jobs.given_up == [(claim.id, ht.METER_FAILED)]
    assert [c["log_level"] for c in caps if c["event"] == "headless_turn.meter_failed"] == [
        "error"]
    assert [c for c in caps if c["event"] == "headless_turn.no_cost_reader"] == []


def test_an_unknown_cost_charges_the_rest_of_the_fresh_goal(world) -> None:
    """P2 (3): during the call another write raises the credit cap from 10
    to 20, and the meter after the call fails. The record meets
    ``StaleGoal`` and records on the new version. The charge is the rest of
    THAT version (20), so the goal blocks. The rest of the old version (10)
    would leave it active with 10 credits of room."""
    goal = world.goals.add(cost_cap="10")
    world.meter.fail_after = True
    runner, reports = FakeRunner(world.meter, cost="1"), Reports()
    runner.side = lambda _n: world.goals.raise_cap(goal.id, Decimal("20"))
    claim = _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    after = world.goals.rows[goal.id]
    assert len(runner.calls) == 1
    assert (after.state, after.block_code, after.cost_cap_credits) == (
        "blocked", "cap_reached", Decimal("20"))
    assert (after.cost_used_credits, after.job_id) == (Decimal("20"), None)
    assert len(reports.seen) == 1 and world.jobs.done == [claim.id]


def test_a_driver_that_turns_a_cancel_into_an_error_charges_the_rest(world) -> None:
    """P2 (5): a driver can turn an outer cancel into an error of its own.
    ``_cost`` then catches it, and the cost is unknown, so the record charges
    the rest of the budget: fail closed for money. The module text says so,
    and no longer says that the settle never catches a cancel."""
    goal = world.goals.add(cost_cap="10", used="4")
    run_id = str(uuid.uuid4())
    entered = asyncio.Event()

    async def converts(org: str, member: str, run: str) -> Any:
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise ConnectionError("the driver turned the cancel into an error") from None
        return Decimal(0)

    turn = _turn(world, FakeRunner(world.meter), meter=converts)

    async def main() -> Any:
        task = asyncio.get_running_loop().create_task(
            turn._cost(ORG, ANA, run_id, Decimal(0), goal, 0.0))
        await asyncio.wait_for(entered.wait(), timeout=30)
        task.cancel()
        return await task

    with structlog.testing.capture_logs() as caps:
        cost = asyncio.run(main())
    assert cost is None
    lines = [(c["log_level"], c["error"]) for c in caps if c["event"] == "headless_turn.cost_unknown"]
    assert lines == [("error", "ConnectionError")]
    assert ht._charge(goal, cost) == Decimal("6")
    text = _MODULE.read_text(encoding="utf-8")
    assert "never catches a cancel" not in text
    assert "can turn an outer cancel into an error" in text


def test_a_report_that_hangs_is_cut_and_the_job_ends(world, monkeypatch) -> None:
    """P2 (6): the report runs after the commit of the round, so it moves no
    money. It has a time limit of its own, half of the last part. A report
    that hangs is cut and logged, and the job still ends ``done``."""
    monkeypatch.setattr(ht, "SETTLE_MARGIN_S", 0.3)
    goal = world.goals.add(cost_cap="1")
    runner, reports = FakeRunner(world.meter, cost="2"), Reports(sleep=30.0)
    with structlog.testing.capture_logs() as caps:
        claim = _run(_turn(world, runner, report=reports, go_on=_always), _payload(goal))
    assert len(reports.seen) == 1 and reports.cancelled is True
    assert world.goals.rows[goal.id].block_code == "cap_reached"
    lines = [(c["log_level"], c["error"]) for c in caps if c["event"] == "headless_turn.report_failed"]
    assert lines == [("warning", "ReportTimeout")]
    assert world.jobs.done == [claim.id] and world.jobs.given_up == []


# ── The member, the goal and the payload ────────────────────────────────────


@pytest.mark.parametrize("extra", ["member", "user_email", "tools", "rounds"])
def test_the_payload_names_no_member_and_no_cap(world, extra) -> None:
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    payload = {**_payload(goal), extra: "eve@evil.test"}
    claim = _run(_turn(world, runner), payload)
    assert runner.calls == [] and world.goals.reads == 0
    assert world.jobs.given_up == [(claim.id, ht.BAD_PAYLOAD)]


def test_the_runner_acts_as_the_member_of_the_goal(world) -> None:
    goal = world.goals.add(member="ana@auto8a.test")
    runner = FakeRunner(world.meter)
    _run(_turn(world, runner), _payload(goal))
    assert [(c["member"], c["org"]) for c in runner.calls] == [(ANA, ORG)]
    assert runner.calls[0]["message"] == goal.objective


@pytest.mark.parametrize("case", ["missing", "other_kind", "blocked"])
def test_a_goal_that_takes_no_round_gets_no_call(world, case) -> None:
    if case == "missing":
        payload = {"goal_id": str(uuid.uuid4()), "candidate": CHECK}
    else:
        goal = world.goals.add(kind="research") if case == "other_kind" else world.goals.add(
            state="blocked")
        payload = _payload(goal)
    runner = FakeRunner(world.meter)
    claim = _run(_turn(world, runner), payload)
    assert runner.calls == [] and world.goals.writes == 0
    assert world.jobs.done == [claim.id]


def test_a_member_who_left_gets_no_call(world) -> None:
    goal = world.goals.add()
    world.departed[ANA] = "status"
    runner = FakeRunner(world.meter)
    claim = _run(_turn(world, runner), _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.jobs.done == [claim.id]


# ── The tools ───────────────────────────────────────────────────────────────

#: Tools that a headless turn must never hold: writes, sends, cards, member
#: questions, handoffs, an extra model call, the web, and an unknown name.
_NEVER = ("save_memory", "save_episode", "write_artifact", "make_report", "crm_propose_rule",
          "crm_answer_question", "ask_questions", "request_confirmation", "share_artifact",
          "emit_generative_ui", "call_agent", "call_agents_parallel", "decide",
          "web_search", "fetch_page", "github_search", "run_script", "code_task",
          "no_such_tool",
          # AUTO-8b-2d: a billed embed call with no run id (UNMETERED_TOOLS).
          "crm_search_notes")


def test_only_read_only_tools_are_offered() -> None:
    from acb_skills.tool_annotations import get_annotations

    got = ht.headless_tools((*_NEVER, *ht.DEFAULT_TOOLS, "remember"))
    assert got == ht.DEFAULT_TOOLS  # in order, once each, and nothing of _NEVER
    for name in got:
        notes = get_annotations(name) or {}
        assert notes.get("read_only") is True and notes.get("open_world") is False, name
    assert ht.headless_tools(_NEVER) == ()


def test_the_turn_offers_the_runner_the_read_only_tools_only(world) -> None:
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    _run(_turn(world, runner, tools=(*_NEVER, "remember")), _payload(goal))
    assert [c["tools"] for c in runner.calls] == [("remember",)]


def test_the_default_runner_holds_the_worker_to_a_read_only_job(monkeypatch) -> None:
    """The one path to ``run_agent``: the worker, with a bound job of the
    read tools and ``no_egress`` on. The pin of the worker is the job."""
    from acb_skills import subagent_contract as sc
    from acb_skills.egress import NO_EGRESS_KEY
    from acb_skills.write_artifact import artifact_context
    from orchestrator import executor

    seen: dict[str, Any] = {}

    async def fake_run_agent(agent_name: str, event_payload: dict[str, Any], **kw: Any) -> Any:
        job = sc.bound_job()
        seen.update(agent=agent_name, payload=event_payload, kw=kw, job=job,
                    pin=sc.child_allowed(job), no_egress=artifact_context().get(NO_EGRESS_KEY),
                    scope=sc.child_tool_scope(agent_name, None))
        return {"answer": "ok"}

    monkeypatch.setattr(executor, "run_agent", fake_run_agent)
    tools = ht.headless_tools((*ht.DEFAULT_TOOLS, *_NEVER))
    out = asyncio.run(ht.run_worker("Watch the deals.", run_id="run-1", org=ORG,
                                    member=ANA, tools=tools))
    assert out == {"answer": "ok"}
    assert seen["agent"] == sc.WORKER_NAME
    assert seen["payload"] == {"message": "Watch the deals.", "user_email": ANA}
    assert seen["kw"] == {"run_id": "run-1", "organization_id": ORG, "solo_room": False}
    assert seen["job"].tools == ht.DEFAULT_TOOLS and seen["job"].pack_tools == ()
    assert seen["pin"] == frozenset(ht.DEFAULT_TOOLS)
    assert seen["scope"] == list(ht.DEFAULT_TOOLS)
    assert seen["no_egress"] is True
    # The scope gave the frame its context back.
    assert sc.bound_job() is None and artifact_context().get(NO_EGRESS_KEY) is None


# ── The meter fails closed ──────────────────────────────────────────────────


def test_the_default_meter_fails_closed(world, monkeypatch) -> None:
    """With the flag on, a candidate and a box with no Console wiring, the
    default meter cannot read. The turn makes NO model call, gives up with
    a named reason and logs it at error (AUTO-8b-2d)."""
    monkeypatch.setattr(world.settings, "customer_console_url", "")
    monkeypatch.setattr(world.settings, "customer_console_deployment_key", "")
    goal = world.goals.add()
    runner = FakeRunner(world.meter)
    turn = ht.HeadlessTurn(runner=runner, candidates=world.checks, go_on=_always)
    with structlog.testing.capture_logs() as caps:
        claim = _run(turn, _payload(goal))
    assert runner.calls == [] and world.goals.writes == 0
    assert world.goals.rows[goal.id] == goal
    assert world.jobs.given_up == [(claim.id, ht.COST_READ_FAILED)]
    lines = [(c["log_level"], c["error"], c["status_class"]) for c in caps
             if c["event"] == "headless_turn.cost_read_failed"]
    assert lines == [("error", "Unwired", "none")]


def test_a_meter_that_answers_no_number_fails_closed(world) -> None:
    goal = world.goals.add()
    runner = FakeRunner(world.meter)

    async def odd(org: str, member: str, run_id: str) -> Any:
        return "zero"

    claim = _run(_turn(world, runner, meter=odd), _payload(goal))
    assert runner.calls == []
    assert world.jobs.given_up == [(claim.id, ht.NO_READER)]


# ── The source scan ─────────────────────────────────────────────────────────


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.add(id(first.value))
    return out


def test_the_module_has_no_approve_path_no_usage_write_and_no_engine() -> None:
    """R5 and §7: the module asks nobody, approves nothing, writes no usage
    row, and opens a session only through the seam."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    names: set[str] = set()
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            strings.append(node.value)
    banned_names = {
        "request_confirmation", "ask_questions", "approve", "reject", "answer_decision",
        "resolve_user_input", "resume_run", "no_person_scope", "no_person_present",
        "create_task", "create_async_engine", "create_engine", "get_db",
        "get_session_factory", "get_session", "get_tenant_redis", "text", "execute",
    }
    assert names & banned_names == set()
    for value in strings:
        for fragment in ("usage_event", "INSERT", "UPDATE ", "DELETE", "/approve",
                         "/answer", "/actions/pending", "/respond-input", "redis"):
            assert fragment not in value, (fragment, value)
    # The one session opener is the seam.
    assert "tenant_session" in names


#: A read of a member name from the payload: ``.get(``, ``.pop(`` or a
#: subscript, on the payload or on a copy such as ``dict(payload)``.
_PAYLOAD_MEMBER = re.compile(
    r"payload\)?\s*(\.(get|pop)\(|\[)\s*[\"'](member|user_email|user_id)")


def test_the_module_takes_no_member_from_the_payload() -> None:
    # AUTO-8b-1 (c): the fence sees each form of the read, a `.pop(` too.
    for mutant in ('member = payload.get("member")', "member = payload['user_email']",
                   'member = dict(payload).pop("member", None)',
                   "member = payload.pop('user_id')"):
        assert _PAYLOAD_MEMBER.search(mutant), mutant
    for fine in ('payload.get("goal_id")', 'payload.pop("candidate")', "dict(payload)) is True"):
        assert not _PAYLOAD_MEMBER.search(fine), fine
    src = _MODULE.read_text(encoding="utf-8")
    assert not _PAYLOAD_MEMBER.search(src)
