"""WS-28p — the company's working week.

Spec: `project-docs/specs/people_center_app.md` §3.4a, §5.11 · D-PC-16, D-PC-18.

Three layers, one direction, and the tests are grouped that way:

* **The model** — policy + override → effective schedule, and the derived
  contracted hours. Pure, so it is tested as arithmetic rather than through a
  database fake that would only agree with itself.
* **The read/write surface** — who may edit the policy, what a bad one is
  refused with, and the impact a change would have *before* it is applied.
* **The direction (D-PC-16)** — the calendar SEEDS from People and never
  mirrors it, and nothing in the tasks package writes back. That one has a
  **structural** fence: prose about a direction binds nobody, and the failure
  mode is two numbers that drift where nobody is looking.

The database's own half — that `org_settings` round-trips a JSONB policy, that
the seed reaches a real `gtd_settings` read — is `tests/live/live_ws28p.py`
per R8.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway import work_schedule as ws
from gateway.routes.people import core as people_core
from gateway.routes.people import schedule as people_schedule
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]
GATEWAY = REPO / "apps" / "services" / "gateway" / "gateway"


def run(coro):
    return asyncio.run(coro)


def _user(email: str, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


ADMIN = _user("admin@fracktal.in", "feature:people", "admin:members:manage")
MEMBER = _user("member@fracktal.in", "feature:people")


# ══════════════════════════════════════════════════════════════════════════
# 1. The model — normalise, validate, layer
# ══════════════════════════════════════════════════════════════════════════

def test_an_org_that_never_configured_anything_still_has_a_week() -> None:
    policy = ws.normalise_policy(None)
    assert policy["working_days"] == [1, 2, 3, 4, 5]
    assert policy["hours_per_day"] == 8.0


@pytest.mark.parametrize("junk", [None, "eight", 42, [], {"working_days": "mon"}])
def test_normalise_never_raises_on_stored_junk(junk: Any) -> None:
    """Read-side repair. A directory that 500s because a settings row holds
    "eight" is worse than one that falls back for that field — the strict half
    runs on the way IN, where somebody is still looking at what they typed."""
    assert ws.normalise_policy(junk)["hours_per_day"] == 8.0


def test_a_partial_policy_keeps_the_defaults_for_what_it_omits() -> None:
    policy = ws.normalise_policy({"hours_per_day": 6})
    assert policy["hours_per_day"] == 6.0
    assert policy["working_days"] == [1, 2, 3, 4, 5]


def test_days_are_deduplicated_and_sorted() -> None:
    assert ws.normalise_policy(
        {"working_days": [5, 1, 1, 3]})["working_days"] == [1, 3, 5]


@pytest.mark.parametrize("days", [[0], [8], [1.5], [True]])
def test_a_day_outside_1_to_7_is_dropped(days: Any) -> None:
    assert ws.normalise_policy(
        {"working_days": days})["working_days"] == [1, 2, 3, 4, 5]


def test_validate_refuses_an_empty_week_with_a_sentence() -> None:
    with pytest.raises(ws.ScheduleError) as exc:
        ws.validate_policy({"working_days": []})
    assert "at least one day" in str(exc.value)


@pytest.mark.parametrize("hours", [0, -1, 25, "eight", True])
def test_validate_refuses_impossible_hours(hours: Any) -> None:
    with pytest.raises(ws.ScheduleError):
        ws.validate_policy({"hours_per_day": hours})


def test_validate_refuses_a_shift_with_no_name() -> None:
    with pytest.raises(ws.ScheduleError) as exc:
        ws.validate_policy({"shifts": [{"start": "09:00"}]})
    assert "name" in str(exc.value)


@pytest.mark.parametrize("bad", ["9:00", "25:00", "09:60", "morning"])
def test_validate_refuses_a_time_that_is_not_hh_mm(bad: str) -> None:
    with pytest.raises(ws.ScheduleError) as exc:
        ws.validate_policy({"shifts": [{"name": "general", "start": bad}]})
    assert "24-hour" in str(exc.value)


def test_validate_refuses_a_holiday_that_is_not_a_date() -> None:
    with pytest.raises(ws.ScheduleError):
        ws.validate_policy({"holidays": ["next friday"]})


def test_validate_returns_the_normalised_policy_so_the_route_stores_that() -> None:
    """Otherwise the stored blob is whatever arrived, and the repair only
    happens on the way out — which means two shapes in the same column."""
    assert ws.validate_policy({"working_days": [3, 1]})["working_days"] == [1, 3]


# ── The layering ────────────────────────────────────────────────────────────

def test_no_override_is_the_org_policy() -> None:
    schedule = ws.effective_schedule({"working_days": [1, 2, 3], "hours_per_day": 6},
                                     None)
    assert schedule["days"] == [1, 2, 3]
    assert schedule["hours_per_day"] == 6.0
    assert schedule["source"]["days"] == "org"


def test_an_override_wins_field_by_field() -> None:
    """Field by field, not all or nothing: a person who works different HOURS
    should not have to restate the company's DAYS to say so."""
    schedule = ws.effective_schedule(
        {"working_days": [1, 2, 3, 4, 5], "hours_per_day": 8},
        {"hours_per_day": 4},
    )
    assert schedule["days"] == [1, 2, 3, 4, 5]
    assert schedule["hours_per_day"] == 4.0
    assert schedule["source"] == {"days": "org", "hours_per_day": "person",
                                  "start": "org", "end": "org", "timezone": "org"}
    assert schedule["start"] == "09:30"      # the company's standard day


def test_the_answer_says_which_layer_decided_each_field() -> None:
    """What lets a surface say "Mon-Fri (company), 10:00-16:00 (yours)" instead
    of four numbers a person cannot account for."""
    schedule = ws.effective_schedule({}, {"start": "10:00"})
    assert schedule["source"]["start"] == "person"
    assert schedule["source"]["days"] == "org"


def test_a_named_shift_pulls_its_times_from_the_policy() -> None:
    """So changing the general shift's hours moves everybody on it — which is
    the entire reason shifts are a list rather than per-person times."""
    policy = {"shifts": [{"name": "night", "start": "22:00", "end": "06:00",
                          "days": [1, 2, 3]}]}
    schedule = ws.effective_schedule(policy, {"shift": "night"})
    assert (schedule["start"], schedule["end"]) == ("22:00", "06:00")
    assert schedule["days"] == [1, 2, 3]
    assert schedule["shift"] == "night"


def test_a_shift_name_is_matched_case_insensitively() -> None:
    policy = {"shifts": [{"name": "General", "start": "09:00"}]}
    assert ws.effective_schedule(policy, {"shift": "general"})["start"] == "09:00"


def test_a_shift_that_no_longer_exists_is_ignored_not_fatal() -> None:
    """Shifts get renamed. A person should not lose their whole schedule to it."""
    schedule = ws.effective_schedule({"shifts": []}, {"shift": "retired"})
    assert schedule["days"] == [1, 2, 3, 4, 5]
    assert schedule["shift"] is None


def test_an_explicit_override_beats_the_shift_it_names() -> None:
    policy = {"shifts": [{"name": "general", "start": "09:00"}]}
    schedule = ws.effective_schedule(policy, {"shift": "general", "start": "11:00"})
    assert schedule["start"] == "11:00"


def test_a_broken_override_falls_through_rather_than_breaking_the_row() -> None:
    """"Absent" and "present and unusable" must reach the same place, or one
    bad stored value takes a person's schedule with it."""
    schedule = ws.effective_schedule({}, {"hours_per_day": "eight", "days": "mon"})
    assert schedule["hours_per_day"] == 8.0
    assert schedule["days"] == [1, 2, 3, 4, 5]


# ── The derived figure (D-PC-18) ────────────────────────────────────────────

def test_contracted_hours_is_days_times_hours() -> None:
    assert ws.contracted_hours_per_week(
        ws.effective_schedule({"working_days": [1, 2, 3, 4, 5],
                               "hours_per_day": 8}, None)) == 40.0


def test_a_half_timer_gets_half_the_week() -> None:
    assert ws.contracted_hours_per_week(
        ws.effective_schedule({}, {"fraction": 0.5})) == 20.0


def test_a_six_day_week_is_expressible() -> None:
    assert ws.contracted_hours_per_week(
        ws.effective_schedule({"working_days": [1, 2, 3, 4, 5, 6]}, None)) == 48.0


def test_the_figure_is_rounded_to_a_quarter_hour() -> None:
    """A bar labelled 37.33333h is a bar that looks broken."""
    hours = ws.contracted_hours_per_week(
        ws.effective_schedule({"hours_per_day": 7.4}, None))
    assert hours == round(hours * 4) / 4


@pytest.mark.parametrize("fraction,expected", [(1.5, 40.0), (-1, 0.0)])
def test_a_nonsense_fraction_is_clamped_not_multiplied(fraction, expected) -> None:
    """Inventing a 60-hour week from a stored 1.5 would put a wrong number on a
    dashboard people plan against."""
    assert ws.contracted_hours_per_week(
        ws.effective_schedule({}, {"fraction": fraction})) == expected


def test_the_typed_capacity_is_reported_when_it_disagrees() -> None:
    schedule = ws.effective_schedule({}, None)          # 40h
    assert ws.capacity_disagreement(schedule, 40) is None
    assert ws.capacity_disagreement(schedule, 20) == -20.0


def test_a_rounding_difference_is_not_a_disagreement() -> None:
    """Otherwise the data-quality panel fills with noise and stops being read."""
    assert ws.capacity_disagreement(ws.effective_schedule({}, None), 40) is None
    assert ws.capacity_disagreement(
        ws.effective_schedule({}, None), 41, tolerance=2) is None


def test_no_typed_capacity_is_not_a_disagreement() -> None:
    assert ws.capacity_disagreement(ws.effective_schedule({}, None), None) is None


# ══════════════════════════════════════════════════════════════════════════
# 2. The direction: People → Calendar, seeded once (D-PC-16)
# ══════════════════════════════════════════════════════════════════════════

def test_the_seed_widens_the_contracted_day_rather_than_copying_it() -> None:
    """The plannable window is not the contracted one — people start before and
    finish after, and a grid that refuses to show it is a grid they stop
    using."""
    seed = ws.calendar_seed(ws.effective_schedule(
        {"shifts": [{"name": "g", "start": "09:00", "end": "17:00"}]},
        {"shift": "g"}))
    assert seed["day_start_hour"] == 8
    assert seed["day_end_hour"] == 18


def test_the_seed_clamps_to_a_real_clock() -> None:
    seed = ws.calendar_seed(ws.effective_schedule(
        {"shifts": [{"name": "n", "start": "00:00", "end": "23:00"}]},
        {"shift": "n"}))
    assert seed["day_start_hour"] == 0
    assert seed["day_end_hour"] == 23


def test_focus_capacity_is_less_than_the_working_day() -> None:
    """Six hours of deep work in an eight-hour day — the ratio migration 77's
    own default encodes."""
    seed = ws.calendar_seed(ws.effective_schedule({"hours_per_day": 8}, None))
    assert seed["daily_capacity_mins"] == 360


def test_the_company_standard_day_is_enough_to_seed_a_calendar() -> None:
    """**Found by the live run, not by this file.** With times only inside
    shifts, a person who had named no shift had no start or end at all, and the
    seed silently fell back to migration 77's 07:00-22:00. Every hermetic
    fixture happened to name a shift, so nothing here could see it."""
    seed = ws.calendar_seed(ws.effective_schedule({}, None))
    assert seed["day_start_hour"] == 8      # 09:30 less an hour of margin
    assert seed["day_end_hour"] == 19       # 18:00 plus an hour


def test_an_org_with_no_fixed_clock_seeds_only_what_it_knows() -> None:
    """`None` is meaningful and different from absent: fully-async or field
    staff have hours without a start time, and nothing should invent one."""
    seed = ws.calendar_seed(
        ws.effective_schedule({"start": None, "end": None}, None))
    assert "day_start_hour" not in seed
    assert seed["daily_capacity_mins"] == 360


def test_the_tasks_package_never_writes_the_people_schedule() -> None:
    """**The structural fence (R7).** Prose about a direction binds nobody: the
    next agent has not read the paragraph. The failure mode is two numbers that
    drift where nobody is looking, so this asserts over the whole package
    rather than over one function.
    """
    offenders: list[str] = []
    for path in (GATEWAY / "routes" / "tasks").rglob("*.py"):
        body = path.read_text(encoding="utf-8")
        # A write to the People Center's half of the schedule. Reading it is
        # the point of the seam; writing it would make the seed a mirror.
        if re.search(r"UPDATE\s+people[\s\S]{0,400}?working_hours", body) \
                or re.search(r"working_hours\s*=\s*:", body):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} writes people.working_hours from the tasks package — "
        "the calendar SEEDS from the work schedule and must never write back "
        "(D-PC-16). A seeded default that diverges is somebody changing their "
        "mind; a mirror that diverges is a bug."
    )


def test_the_seed_is_read_time_and_writes_nothing() -> None:
    """It is a default, not a migration of somebody's preferences into a row.
    A write would make the schedule authoritative over a choice the person has
    not made yet — and would have to be kept in sync forever after."""
    body = (GATEWAY / "routes" / "tasks" / "settings.py").read_text(encoding="utf-8")
    seed_fn = body[body.index("async def _seed_from_work_schedule"):]
    seed_fn = seed_fn[:seed_fn.index("\ndef ")]
    assert "INSERT" not in seed_fn.upper()
    assert "UPDATE" not in seed_fn.upper()


# ══════════════════════════════════════════════════════════════════════════
# 3. The surface
# ══════════════════════════════════════════════════════════════════════════

class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, policy: Any = None, people: list[Any] | None = None):
        self.policy = policy
        self.people = people or []
        self.statements: list[str] = []
        self.params: list[dict] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        self.params.append(dict(params or {}))
        if "FROM org_settings" in statement:
            if self.policy is None:
                return _Result([])
            return _Result([SimpleNamespace(
                value=json.dumps(self.policy), updated_by="admin@fracktal.in",
                updated_at=None)])
        if hits(statement, "FROM people"):
            return _Result(self.people)
        return _Result([])

    async def commit(self) -> None:
        return None

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)


def bind(monkeypatch, db: FakeDB) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    for module in (people_schedule, people_core):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)


def person(name: str, override: Any = None) -> SimpleNamespace:
    return SimpleNamespace(id=f"id-{name}", name=name, working_hours=override)


def test_the_policy_read_answers_defaults_when_nothing_is_stored(monkeypatch) -> None:
    bind(monkeypatch, FakeDB(policy=None))
    got = run(people_schedule.get_policy(user=MEMBER))
    assert got.policy["working_days"] == [1, 2, 3, 4, 5]
    assert got.contracted_hours_per_week == 40.0
    assert got.can_manage is False


def test_a_member_may_read_the_companys_week(monkeypatch) -> None:
    """Directory-tier information: a person cannot understand their own
    effective schedule without seeing the layer underneath it."""
    bind(monkeypatch, FakeDB(policy={"hours_per_day": 6}))
    assert run(people_schedule.get_policy(user=MEMBER)).policy["hours_per_day"] == 6.0


def test_the_read_carries_the_defaults_so_reset_needs_no_second_copy(
        monkeypatch) -> None:
    bind(monkeypatch, FakeDB(policy={"hours_per_day": 6}))
    assert run(people_schedule.get_policy(user=ADMIN)).defaults == ws.DEFAULT_POLICY


def test_the_write_is_admin_gated() -> None:
    """The route's OWN dependency is executed, not inspected — so a route that
    loses its gate fails here rather than in production. The same shape
    `test_people_write.py` uses for the other three write routes."""
    [route] = [r for r in people_core.router.routes
               if getattr(r, "path", "") == "/people/schedule"
               and "PUT" in getattr(r, "methods", set())]
    # `route.dependencies` carries the ROUTER's gate too (the
    # `require_feature_router("people")` every route here inherits), and that
    # one needs a Request. The permission gate is the one that takes a user.
    gates = [d.dependency for d in route.dependencies
             if "user" in inspect.signature(d.dependency).parameters
             and "request" not in inspect.signature(d.dependency).parameters]
    assert gates, "PUT /people/schedule carries no permission dependency"
    for gate in gates:
        # `_check` is async — awaited, not called, or the coroutine is never
        # run and the test passes by never asking the question.
        with pytest.raises(HTTPException) as exc:
            run(gate(user=MEMBER))
        assert exc.value.status_code == 403
        run(gate(user=ADMIN))     # the admin passes the same gate


def test_a_bad_policy_is_refused_with_its_reason(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    with pytest.raises(HTTPException) as exc:
        run(people_schedule.put_policy(
            people_schedule.PolicyWrite(policy={"hours_per_day": 99}), user=ADMIN))
    assert exc.value.status_code == 400
    assert "hours_per_day" in exc.value.detail


def test_a_dry_run_writes_nothing(monkeypatch) -> None:
    db = FakeDB(people=[person("Priya")])
    bind(monkeypatch, db)
    out = run(people_schedule.put_policy(
        people_schedule.PolicyWrite(policy={"hours_per_day": 6}, dry_run=True),
        user=ADMIN))
    assert out.saved is False
    assert not db.issued("INSERT INTO org_settings")


def test_the_impact_counts_who_actually_moves(monkeypatch) -> None:
    """Not the roster size — that would be true and useless. A settings page
    that silently re-baselines every load bar is one nobody trusts twice."""
    db = FakeDB(people=[person("Priya"),
                        person("Ravi", {"hours_per_day": 4})])
    bind(monkeypatch, db)
    out = run(people_schedule.put_policy(
        people_schedule.PolicyWrite(policy={"hours_per_day": 6}, dry_run=True),
        user=ADMIN))
    # Priya rides the policy and moves 40 → 30; Ravi overrides hours and does not.
    assert out.impact.changed == 1
    assert out.impact.unchanged == 1
    assert out.impact.examples[0]["name"] == "Priya"
    assert out.impact.hours_before == 40.0
    assert out.impact.hours_after == 30.0


def test_the_impact_examples_are_bounded(monkeypatch) -> None:
    db = FakeDB(people=[person(f"P{i}") for i in range(40)])
    bind(monkeypatch, db)
    out = run(people_schedule.put_policy(
        people_schedule.PolicyWrite(policy={"hours_per_day": 6}, dry_run=True),
        user=ADMIN))
    assert out.impact.changed == 40
    assert len(out.impact.examples) == 10


def test_a_real_save_stores_the_normalised_policy(monkeypatch) -> None:
    db = FakeDB()
    bind(monkeypatch, db)
    out = run(people_schedule.put_policy(
        people_schedule.PolicyWrite(policy={"working_days": [5, 1]}), user=ADMIN))
    assert out.saved is True
    assert db.issued("INSERT INTO org_settings")
    stored = json.loads(
        next(p for s, p in zip(db.statements, db.params, strict=True)
             if "INSERT INTO org_settings" in s)["value"])
    assert stored["working_days"] == [1, 5]


def test_alumni_are_not_counted_in_the_impact(monkeypatch) -> None:
    """A change to the working week does not move somebody who left."""
    db = FakeDB()
    bind(monkeypatch, db)
    run(people_schedule.put_policy(
        people_schedule.PolicyWrite(policy={"hours_per_day": 6}, dry_run=True),
        user=ADMIN))
    assert db.issued("status <> 'alumni'")


def test_the_policy_is_read_through_the_session_not_a_new_connection() -> None:
    """R5b. `acb_common.org_settings` opens its own psycopg connection per call
    — fine for the appearance blob nothing reads on a hot path, wrong for a
    value read on every person read."""
    body = (GATEWAY / "work_schedule.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith(("#", "*", '"""')))
    assert "import psycopg" not in code
    assert "load_org_setting(" not in code
    # And it acquires no session of its own — every reader takes one.
    assert "tenant_session" not in code
