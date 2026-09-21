"""WS-28j1 — the people-management dashboard's rows and its five pills.

Spec: `project-docs/specs/people_center_app.md` §5.7.1, §5.7.2, §5.7.5 ·
**D-PC-14**.

Four claims, and the first one is the ticket:

* **The classification is arithmetic**, not a judgement — behind, at risk,
  overloaded, idle, on track, each computed from tasks and dates and each
  carrying a reason a person can check against the expanded row.
* **Missing estimates suppress the hours pills** rather than declaring somebody
  free. A row of thirty un-estimated tasks sums to zero hours, and a dashboard
  that reads that as spare capacity hands them a thirty-first.
* **Every figure is scoped by the VIEWER**, and the surface says so when it is
  partial. A rollup is not a licence to see work you could not open.
* **No ranking, score or leaderboard of people** (D-PC-14) — with a structural
  fence, because this is a surface where one would look natural.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from fastapi import HTTPException
from gateway import work_schedule as ws
from gateway import workload
from gateway.routes.people import core as people_core
from gateway.routes.people import dashboard as people_dashboard
from tests.unit._sql_match import hits

REPO = Path(__file__).resolve().parents[2]

WEEK = ws.effective_schedule({}, None)          # Mon-Fri, 8h/day → 40h

MON = date(2026, 8, 10)                          # a Monday
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
FRI = MON + timedelta(days=4)
SUN = MON + timedelta(days=6)


def run(coro):
    return asyncio.run(coro)


def task(*, due: date | None, mins: int | None, title: str = "A task",
         tid: str = "t1") -> dict[str, Any]:
    return {"id": tid, "title": title, "estimate_mins": mins,
            "due_at": (datetime.combine(due, datetime.min.time(),
                                        tzinfo=UTC) if due else None),
            "project_name": "Apollo", "_due": due}


def span(start: date, end: date, kind: str = "away",
         hours: float | None = None) -> dict[str, Any]:
    return {"starts_on": start, "ends_on": end, "kind": kind,
            "hours_per_day": hours}


# ══════════════════════════════════════════════════════════════════════════
# 1. "At risk" — the hours before the deadline, not the days until it
# ══════════════════════════════════════════════════════════════════════════

def test_a_task_that_fits_is_not_at_risk() -> None:
    # Mon → Fri inclusive is five working days, 40 hours. 16 fits.
    assert workload.at_risk_tasks(WEEK, [task(due=FRI, mins=16 * 60)],
                                  [], MON) == []


def test_a_task_that_does_not_fit_is() -> None:
    out = workload.at_risk_tasks(WEEK, [task(due=WED, mins=40 * 60)], [], MON)
    assert len(out) == 1
    assert out[0]["available_hours"] == 24.0      # Mon, Tue, Wed
    assert out[0]["shortfall_hours"] == 16.0


def test_a_holiday_is_exactly_the_difference() -> None:
    """The reason WS-28k had to exist before this ticket did: 24 hours of work
    due Wednesday is comfortable, and it is impossible if they are away."""
    fits = workload.at_risk_tasks(WEEK, [task(due=WED, mins=24 * 60)], [], MON)
    assert fits == []
    away = workload.at_risk_tasks(WEEK, [task(due=WED, mins=24 * 60)],
                                  [span(MON, TUE)], MON)
    assert len(away) == 1
    assert away[0]["available_hours"] == 8.0      # Wednesday alone


def test_the_estimate_is_cumulative_not_per_task() -> None:
    """Three four-hour tasks due Tuesday, with sixteen hours before Tuesday, is
    fine. Three TWELVE-hour ones are not — and per-task arithmetic says all
    three are, because none of them alone exceeds sixteen."""
    tasks = [task(due=TUE, mins=12 * 60, tid=f"t{i}", title=f"Task {i}")
             for i in range(3)]
    out = workload.at_risk_tasks(WEEK, tasks, [], MON)
    # The first fits (12 ≤ 16); the second and third do not.
    assert [r["needed_hours"] for r in out] == [24.0, 36.0]
    assert all(r["available_hours"] == 16.0 for r in out)


def test_overdue_work_is_carried_into_the_sum() -> None:
    """A task that missed Friday still has to be done. Dropping it makes every
    deadline after it look reachable — which is how a slipping week becomes a
    surprise."""
    late = task(due=MON - timedelta(days=3), mins=20 * 60, tid="late",
                title="Late")
    soon = task(due=TUE, mins=4 * 60, tid="soon", title="Soon")
    out = workload.at_risk_tasks(WEEK, [late, soon], [], MON)
    # The overdue row is not itself reported — the person is *behind*, which is
    # a stronger statement — but its hours are in the one that is.
    assert [r["task_id"] for r in out] == ["soon"]
    assert out[0]["needed_hours"] == 24.0
    assert out[0]["own_hours"] == 4.0


def test_an_undated_task_cannot_make_anybody_at_risk() -> None:
    """It carries no deadline to be late for, so it cannot force a sequence.
    Counting it here would put a pill on somebody whose backlog is merely
    large."""
    assert workload.at_risk_tasks(
        WEEK, [task(due=None, mins=400 * 60)], [], MON) == []


def test_a_task_beyond_the_horizon_is_not_judged_yet() -> None:
    far = MON + timedelta(days=workload.HORIZON_DAYS + 1)
    assert workload.at_risk_tasks(WEEK, [task(due=far, mins=999 * 60)],
                                  [], MON) == []


def test_an_unestimated_task_contributes_nothing_rather_than_a_guess() -> None:
    """A guess is a number nobody can check, on a surface people plan against.
    The suppression in `classify` is the honest answer instead."""
    assert workload.at_risk_tasks(WEEK, [task(due=TUE, mins=None)],
                                  [], MON) == []


def test_the_due_date_itself_is_a_working_day() -> None:
    """"Due Friday" means Friday is a day you can still work on it — and off by
    one here is off by eight hours on every row."""
    out = workload.at_risk_tasks(WEEK, [task(due=MON, mins=8 * 60)], [], MON)
    assert out == []


# ══════════════════════════════════════════════════════════════════════════
# 2. The five pills
# ══════════════════════════════════════════════════════════════════════════

def metrics(**over: Any) -> dict[str, Any]:
    base = {"open_tasks": 4, "unestimated": 0, "overdue": 0,
            "contracted_hours": 40.0, "committed_this_week": 20.0,
            "at_risk": []}
    base.update(over)
    return base


def test_behind_is_a_missed_date() -> None:
    out = workload.classify(metrics(overdue=3))
    assert out["pill"] == "behind"
    assert "3 open tasks past the due date" in out["reason"]


def test_at_risk_names_the_task_and_both_numbers() -> None:
    risky = workload.at_risk_tasks(WEEK, [task(due=WED, mins=40 * 60,
                                               title="Ship it")], [], MON)
    out = workload.classify(metrics(at_risk=risky))
    assert out["pill"] == "at_risk"
    assert "Ship it" in out["reason"]
    assert "40.0h" in out["reason"] and "24.0h" in out["reason"]


def test_overloaded_is_the_week_not_the_backlog() -> None:
    out = workload.classify(metrics(committed_this_week=52.0))
    assert out["pill"] == "overloaded"
    assert "52h" in out["reason"] and "40h" in out["reason"]


def test_idle_with_nothing_assigned_says_so_plainly() -> None:
    out = workload.classify(metrics(open_tasks=0, committed_this_week=0.0))
    assert out["pill"] == "idle"
    assert out["reason"] == "No open tasks assigned."


def test_idle_below_the_threshold_is_a_planning_signal() -> None:
    out = workload.classify(metrics(committed_this_week=4.0))
    assert out["pill"] == "idle"
    # A fact about the week, never about the person (D-PC-14).
    assert "work due this week" in out["reason"]


def test_on_track_is_what_is_left() -> None:
    out = workload.classify(metrics())
    assert out["pill"] == "on_track"
    assert out["flags"] == []


def test_behind_outranks_overloaded_but_both_are_reported() -> None:
    """One pill is what the row WEARS; the flags are what the row KNOWS.
    "Behind and overloaded" is a different conversation from "behind", and a
    single word cannot say so."""
    out = workload.classify(metrics(overdue=1, committed_this_week=60.0))
    assert out["pill"] == "behind"
    assert out["flags"] == ["behind", "overloaded"]


def test_the_precedence_is_the_documented_order() -> None:
    assert workload.PILLS == ("behind", "at_risk", "overloaded", "idle",
                              "on_track")


def test_every_pill_carries_a_reason() -> None:
    """A pill without its reason is a verdict, and this surface does not issue
    verdicts about people (D-PC-14)."""
    cases = [
        metrics(overdue=1),
        metrics(at_risk=[{"title": "X", "due_on": "2026-08-12",
                          "needed_hours": 40.0, "available_hours": 8.0}]),
        metrics(committed_this_week=99.0),
        metrics(open_tasks=0, committed_this_week=0.0),
        metrics(),
    ]
    for case in cases:
        out = workload.classify(case)
        assert out["reason"] and out["reason"].strip().endswith((".", "h."))


# ── The suppression that keeps a missing estimate from reading as free ──────

def test_nothing_estimated_turns_the_hours_pills_off() -> None:
    out = workload.classify(metrics(open_tasks=30, unestimated=30,
                                    committed_this_week=0.0))
    assert out["hours_basis"] is False
    assert out["pill"] == "on_track"          # NOT idle
    assert "no estimate" in (out["note"] or "")


def test_one_estimate_among_many_is_still_a_basis() -> None:
    out = workload.classify(metrics(open_tasks=30, unestimated=29,
                                    committed_this_week=2.0))
    assert out["hours_basis"] is True
    assert out["pill"] == "idle"


def test_an_overdue_task_still_shows_through_the_suppression() -> None:
    """`behind` is a statement about DATES, and a date is known whether or not
    anybody estimated the work."""
    out = workload.classify(metrics(open_tasks=5, unestimated=5, overdue=2))
    assert out["pill"] == "behind"
    assert out["hours_basis"] is False


def test_no_contracted_hours_also_suppresses_them() -> None:
    """Every hours comparison there divides against nothing."""
    out = workload.classify(metrics(contracted_hours=0.0,
                                    committed_this_week=80.0))
    assert out["hours_basis"] is False
    assert out["pill"] == "on_track"
    assert "contracted" in (out["note"] or "")


def test_no_open_tasks_is_idle_even_with_nothing_estimated() -> None:
    """A count, not an hours figure — so the suppression does not reach it."""
    out = workload.classify(metrics(open_tasks=0, unestimated=0,
                                    contracted_hours=0.0))
    assert out["pill"] == "idle"


# ══════════════════════════════════════════════════════════════════════════
# 2b. The department rollup (WS-28j2, §5.7.3)
# ══════════════════════════════════════════════════════════════════════════

def member(**over: Any) -> dict[str, Any]:
    base = {"person_id": "p1", "name": "Priya", "department": "Engineering",
            "kind": "person", "pill": "on_track", "open_tasks": 3,
            "contracted_hours": 40.0, "committed_this_week": 20.0,
            "hours_basis": True, "away_this_week": False}
    base.update(over)
    return base


def test_the_rollup_is_arithmetic_over_the_rows_it_is_handed() -> None:
    """The §5.9 guarantee, asserted as identity rather than trusted.

    A rollup that ran its own query would be a second answer to "how many
    people are behind", and the two would diverge the first time either
    changed. This one sums the same array, so the assertion is that the totals
    ARE the sum — not that they are close to it.
    """
    rows = [member(name="A", committed_this_week=10.0),
            member(name="B", committed_this_week=30.0, department="Sales"),
            member(name="C", committed_this_week=5.0, contracted_hours=20.0)]
    out = workload.rollup(rows)
    assert out["org"]["headcount"] == 3
    assert out["org"]["committed_hours"] == sum(
        r["committed_this_week"] for r in rows)
    assert out["org"]["contracted_hours"] == sum(
        r["contracted_hours"] for r in rows)
    assert sum(d["headcount"] for d in out["departments"]) == 3


def test_the_pill_counts_come_from_the_rows_pills() -> None:
    out = workload.rollup([
        member(name="A", pill="behind"), member(name="B", pill="behind"),
        member(name="C", pill="idle"), member(name="D", pill="on_track"),
    ])
    assert out["org"]["pills"] == {"behind": 2, "at_risk": 0, "overloaded": 0,
                                   "idle": 1, "on_track": 1}


def test_departments_are_sorted_by_strain_not_alphabetically() -> None:
    """"A rollup nobody can act on is a table" (§5.7.3). The order says where to
    look first — and it is an ordering of WORK, computed from pill counts."""
    rows = [
        member(name="A", department="Alpha", pill="on_track"),
        member(name="B", department="Alpha", pill="on_track"),
        member(name="C", department="Zulu", pill="behind"),
        member(name="D", department="Zulu", pill="on_track"),
    ]
    out = workload.rollup(rows)
    assert [d["department"] for d in out["departments"]] == ["Zulu", "Alpha"]
    assert out["departments"][0]["strain"] == 0.5


def test_strain_is_a_SHARE_not_a_count() -> None:
    """Three behind out of four is a different situation from three out of
    forty, and an absolute count cannot tell them apart."""
    small = workload.rollup(
        [member(name=f"s{i}", department="Small",
                pill="behind" if i < 3 else "on_track") for i in range(4)])
    large = workload.rollup(
        [member(name=f"l{i}", department="Large",
                pill="behind" if i < 3 else "on_track") for i in range(40)])
    assert small["departments"][0]["strain"] > large["departments"][0]["strain"]


def test_people_nobody_placed_are_rolled_up_not_dropped() -> None:
    out = workload.rollup([member(name="A", department=None),
                           member(name="B", department="   ")])
    assert [d["department"] for d in out["departments"]] == [workload.UNASSIGNED]
    assert out["departments"][0]["headcount"] == 2


def test_agents_are_excluded_and_the_exclusion_is_REPORTED() -> None:
    """Headcount is people. An agent has no contract and no pill, so counting
    it divides a department's strain by a denominator that is part process —
    but a silent omission is how a total quietly stops adding up."""
    out = workload.rollup([
        member(name="Priya", pill="behind"),
        member(name="agent:triage", kind="agent", pill=None,
               contracted_hours=0.0, committed_this_week=99.0),
    ])
    assert out["org"]["headcount"] == 1
    assert out["org"]["agents"] == 1
    assert out["org"]["committed_hours"] == 20.0     # the agent's 99h is out
    assert out["org"]["strain"] == 1.0


def test_who_is_away_is_named_not_counted() -> None:
    # "Two people are away" is true and useless when the question is whether to
    # hand somebody a deadline.
    out = workload.rollup([member(name="Priya", away_this_week=True),
                           member(name="Ravi")])
    assert out["org"]["away"] == ["Priya"]


def test_people_with_no_open_work_at_all_are_named() -> None:
    out = workload.rollup([member(name="Priya", open_tasks=0),
                           member(name="Ravi", open_tasks=4)])
    assert out["org"]["no_open_work"] == ["Priya"]


def test_the_spread_is_the_gap_that_starts_the_conversation() -> None:
    out = workload.rollup([
        member(name="Loaded", committed_this_week=46.0),
        member(name="Middle", committed_this_week=20.0),
        member(name="Free", committed_this_week=6.0),
    ])
    spread = out["org"]["spread"]
    assert spread["gap_hours"] == 40.0
    assert spread["most"]["name"] == "Loaded"
    assert spread["least"]["name"] == "Free"
    # Stated in HOURS, with the percentage beside them and never instead: a
    # bare percentage is the shape that reads as a score (D-PC-14).
    assert spread["most"]["committed_hours"] == 46.0
    assert spread["most"]["percent"] == 115


def test_the_spread_ignores_a_row_whose_hours_mean_nothing() -> None:
    """A row with nothing estimated would otherwise arrive at the bottom of the
    spread as though it were free — the exact misreading `hours_basis` exists to
    prevent."""
    out = workload.rollup([
        member(name="Loaded", committed_this_week=40.0),
        member(name="Busy", committed_this_week=30.0),
        member(name="Unknown", committed_this_week=0.0, hours_basis=False,
               open_tasks=30),
    ])
    assert out["org"]["spread"]["least"]["name"] == "Busy"
    assert out["org"]["spread"]["gap_hours"] == 10.0
    # …and the row is still counted, as a caveat on the totals.
    assert out["org"]["unestimated_people"] == 1


def test_a_spread_over_one_person_is_not_a_spread() -> None:
    # Rendering "0h" there would read as a balanced team.
    assert workload.rollup([member(name="Alone")])["org"]["spread"] is None


def test_an_empty_roster_does_not_divide_by_zero() -> None:
    out = workload.rollup([])
    assert out["departments"] == []
    assert out["org"]["headcount"] == 0 and out["org"]["strain"] == 0.0


@pytest.mark.parametrize("path", [
    "apps/services/gateway/gateway/workload.py",
])
def test_the_rollup_runs_no_query(path: str) -> None:
    """A second count is the failure mode §5.9 names, and the cheapest way to
    introduce one is a convenience `db` parameter on this module."""
    source = (REPO / path).read_text(encoding="utf-8")
    for forbidden in ("db.execute", "SELECT ", "await "):
        assert forbidden not in source, forbidden


# ══════════════════════════════════════════════════════════════════════════
# 3. The endpoint
# ══════════════════════════════════════════════════════════════════════════

def person(name: str, email: str | None, **over: Any) -> SimpleNamespace:
    base = dict(id=f"id-{name.lower()}", name=name, email=email,
                department="Engineering", team=None, avatar=None,
                working_hours=None, status="active")
    base.update(over)
    return SimpleNamespace(**base)


PRIYA = person("Priya", "priya@fracktal.in")
RAVI = person("Ravi", "ravi@fracktal.in", department="Sales")


class _Result:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    """Answers the six statements the endpoint issues, by shape.

    Hermetic on purpose for the *decisions* — who is gated, which rows appear,
    which pill is drawn. The SQL itself is verified against a real database in
    `tests/live/live_ws28j.py` (R8), because a fake agrees with whatever it is
    handed and five live bugs once shipped green that way.
    """

    def __init__(self, *, people=(PRIYA, RAVI), totals=(), dated=(),
                 projects=(), activity=(), absences=()):
        self.people = list(people)
        self.totals = list(totals)
        self.dated = list(dated)
        self.projects = list(projects)
        self.activity = list(activity)
        self.absences = list(absences)
        self.statements: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> _Result:
        statement = " ".join(str(sql).split())
        self.statements.append(statement)
        if "FROM people_absences" in statement:
            return _Result(self.absences)
        if "FROM org_settings" in statement:
            return _Result([])
        if hits(statement, "FROM people"):
            if "lower(email)" in statement:          # find_self_row
                return _Result([])
            return _Result(self.people)
        if "FROM pm_activities" in statement:
            return _Result(self.activity)
        if "JOIN pm_projects p" in statement and "DISTINCT" in statement:
            return _Result(self.projects)
        # ⚠️ Discriminated on the BIND, not on `t.due_at IS NOT NULL` — the
        # totals statement carries that phrase too, inside its `overdue`
        # FILTER, and the first version of this fake answered the totals query
        # with the (empty) dated rows. A fake that quietly routes one statement
        # to another's answer proves nothing about either.
        if "t.due_at < :until" in statement:
            return _Result(self.dated)
        if "FROM pm_tasks t" in statement:
            return _Result(self.totals)
        return _Result([])

    async def commit(self) -> None:
        return None

    def issued(self, fragment: str) -> bool:
        return any(fragment in s for s in self.statements)


class FakeVis:
    """The two clauses `Visibility` really answers, kept DISTINCT.

    ⚠️ A fake that returns one string for both arms cannot tell "the tenant
    subquery ran" from "the grant closure ran" — and the whole point of
    `data:org:read` being *unrestricted within a tenant* (WS-29b) is that those
    are different SQL.
    """

    def __init__(self, unrestricted: bool = True):
        self.unrestricted = unrestricted
        self.params = ({"vis_org": "org-1"} if unrestricted else
                       {"vis_email": "boss@fracktal.in", "vis_groups": [],
                        "vis_org": "org-1"})

    def project_clause(self, column: str = "id") -> str:
        if self.unrestricted:
            return f"{column} IN (SELECT id FROM tenant_projects)"
        return f"{column} IN (SELECT id FROM granted)"


def bind(monkeypatch, db: FakeDB, vis: FakeVis | None = None) -> None:
    @asynccontextmanager
    async def _tenant_session(organization_id: str | None = None):
        yield db
        await db.commit()

    for module in (people_core, people_dashboard):
        monkeypatch.setattr(module, "_tenant_session", _tenant_session,
                            raising=False)

    async def _visibility(_db, _user):
        return vis if vis is not None else FakeVis()

    monkeypatch.setattr(people_dashboard, "_visibility", _visibility)


def _user(email: str, *grants: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)))


BOSS = _user("boss@fracktal.in", "feature:people", "admin:members:read",
             "feature:projects")
MANAGER = _user("mgr@fracktal.in", "feature:people", "admin:members:read",
                "admin:members:manage", "feature:projects")
COLLEAGUE = _user("someone@fracktal.in", "feature:people")


def totals_row(who: str, **over: Any) -> SimpleNamespace:
    base = dict(who=who, open_tasks=0, mins=0, unestimated=0, overdue=0,
                next_due=None)
    base.update(over)
    return SimpleNamespace(**base)


def test_the_dashboard_needs_admin_members_read(monkeypatch) -> None:
    """It is skills, capacity and hours for everybody at once, so §4.2's oracle
    rule applies to the whole surface rather than to a clause."""
    bind(monkeypatch, FakeDB())
    with pytest.raises(HTTPException) as exc:
        run(people_dashboard.get_dashboard(user=COLLEAGUE))
    assert exc.value.status_code == 403
    assert "admin:members:read" in exc.value.detail


def test_it_returns_a_row_per_person(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert [r.name for r in out.rows] == ["Priya", "Ravi"]
    assert out.total == 2


def test_a_restricted_viewer_is_told_the_figures_are_partial(monkeypatch) -> None:
    bind(monkeypatch, FakeDB(), FakeVis(unrestricted=False))
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert out.partial is True


def test_an_unrestricted_viewer_is_not(monkeypatch) -> None:
    bind(monkeypatch, FakeDB(), FakeVis(unrestricted=True))
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert out.partial is False


def test_the_grant_closure_is_applied_to_every_work_statement(monkeypatch) -> None:
    """The scoping is the control, so it is asserted per statement rather than
    once: a figure that skipped it would be a number the viewer may not see,
    sitting beside four that they may."""
    db = FakeDB()
    bind(monkeypatch, db, FakeVis(unrestricted=False))
    run(people_dashboard.get_dashboard(user=BOSS))
    work = [s for s in db.statements
            if "FROM pm_tasks t" in s or "FROM pm_activities" in s]
    assert len(work) == 4
    assert all("SELECT id FROM granted" in s for s in work)


def test_even_the_unrestricted_viewer_is_scoped_to_their_tenant(
        monkeypatch) -> None:
    """`data:org:read` is unrestricted WITHIN a tenant, never across them.

    The tempting shortcut is `if vis.unrestricted: return "true"` and letting
    row-level security carry the tenant — which makes this endpoint's
    correctness depend on an enforcement flip that is the owner's act.
    `project_clause` already answers the tenant subquery for this caller (that
    is what WS-29b changed it for), and it costs one cheap `IN`.
    """
    db = FakeDB()
    bind(monkeypatch, db, FakeVis(unrestricted=True))
    run(people_dashboard.get_dashboard(user=BOSS))
    work = [s for s in db.statements
            if "FROM pm_tasks t" in s or "FROM pm_activities" in s]
    assert len(work) == 4
    assert all("SELECT id FROM tenant_projects" in s for s in work), work


def test_without_feature_projects_the_roster_still_renders(monkeypatch) -> None:
    """"This surface is not yours" and "nobody has any work" must not draw
    identically — the call `/people/{id}/work` already made."""
    db = FakeDB()
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(
        user=_user("hr@fracktal.in", "feature:people", "admin:members:read")))
    assert out.work_visible is False
    assert [r.name for r in out.rows] == ["Priya", "Ravi"]
    assert not db.issued("FROM pm_tasks t")


def test_the_numbers_land_on_the_right_person(monkeypatch) -> None:
    db = FakeDB(totals=[totals_row("priya@fracktal.in", open_tasks=6,
                                   mins=1200, unestimated=1, overdue=2)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    priya = next(r for r in out.rows if r.name == "Priya")
    ravi = next(r for r in out.rows if r.name == "Ravi")
    assert (priya.open_tasks, priya.committed_hours, priya.overdue) == (6, 20.0, 2)
    assert priya.pill == "behind"
    assert (ravi.open_tasks, ravi.pill) == (0, "idle")


def test_the_email_join_folds_case(monkeypatch) -> None:
    """`lower(email)` on both sides is the same join `has_login` and the self
    predicate use (D-PC-1). A row keyed by a capitalised address would be a
    person whose whole week silently reads as zero."""
    db = FakeDB(people=[person("Mixed", "Mixed.Case@Fracktal.IN")],
                totals=[totals_row("mixed.case@fracktal.in", open_tasks=3)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert out.rows[0].open_tasks == 3


def test_an_agent_appears_and_carries_no_pill(monkeypatch) -> None:
    """Agents hold tasks the same way people do (D-PM-4) and a report that
    omits half the workforce is wrong in the direction that matters — but
    "idle" and "behind" do not mean anything about a process (§5.7.5)."""
    db = FakeDB(totals=[totals_row("agent:triage", open_tasks=9, mins=600)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    agent = next(r for r in out.rows if r.name == "agent:triage")
    assert agent.kind == "agent"
    assert agent.open_tasks == 9 and agent.committed_hours == 10.0
    assert (agent.pill, agent.reason, agent.flags) == (None, None, [])
    assert agent.person_id is None


def test_work_assigned_to_somebody_off_the_roster_is_still_shown(
        monkeypatch) -> None:
    """A departed colleague still holding open tasks is invisible everywhere
    else in the product. One mechanism covers agents, alumni and addresses that
    were never in the directory."""
    db = FakeDB(totals=[totals_row("gone@fracktal.in", open_tasks=4)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    stray = next(r for r in out.rows if r.name == "gone@fracktal.in")
    assert stray.kind == "person" and stray.person_id is None
    # No directory row means no schedule, so no contracted hours to judge it
    # against — the suppression rather than a fabricated 40-hour week.
    assert stray.contracted_hours == 0.0
    assert stray.hours_basis is False


def test_this_weeks_commitment_ignores_work_due_later(monkeypatch) -> None:
    """§5.7.2 says "for the week". Comparing a whole backlog against one week of
    hours calls everybody overloaded."""
    later = date.today() + timedelta(days=12)
    db = FakeDB(
        totals=[totals_row("priya@fracktal.in", open_tasks=2, mins=60 * 60)],
        dated=[SimpleNamespace(who="priya@fracktal.in", id="t1", title="Later",
                               due_at=datetime.combine(
                                   later, datetime.min.time(),
                                   tzinfo=UTC),
                               estimate_mins=60 * 60, project_name="Apollo")])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    priya = next(r for r in out.rows if r.name == "Priya")
    assert priya.committed_hours == 60.0        # the backlog
    assert priya.committed_this_week == 0.0     # nothing due by Sunday
    assert priya.pill != "overloaded"


def test_the_projects_a_person_is_on_travel_on_the_row(monkeypatch) -> None:
    db = FakeDB(projects=[
        SimpleNamespace(who="priya@fracktal.in", id=f"p{i}", name=f"Project {i}")
        for i in range(9)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    priya = next(r for r in out.rows if r.name == "Priya")
    assert priya.projects_total == 9
    assert len(priya.projects) == people_dashboard.PROJECT_NAMES_SHOWN


def test_an_away_person_says_so_on_the_row(monkeypatch) -> None:
    today = date.today()
    db = FakeDB(absences=[SimpleNamespace(
        person_id="id-priya", starts_on=today - timedelta(days=1),
        ends_on=today + timedelta(days=3), kind="holiday",
        hours_per_day=None)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    priya = next(r for r in out.rows if r.name == "Priya")
    assert priya.away == {"kind": "holiday",
                          "until": (today + timedelta(days=3)).isoformat()}
    # And the hours they actually have left this week come down with it.
    assert priya.hours_available_this_week < 40.0


def test_a_database_without_migration_174_answers_present(monkeypatch) -> None:
    """Best-effort, the same call `away_today` makes: a dashboard that 500s
    because a table is one deploy behind is worse than one that shows everybody
    as present."""
    db = FakeDB()

    async def _boom(sql, params=None):
        if "people_absences" in str(sql):
            raise RuntimeError("relation does not exist")
        return await FakeDB.execute(db, sql, params)

    db.execute = _boom          # type: ignore[method-assign]
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert all(r.away is None for r in out.rows)


def test_the_rollup_arrives_beside_the_rows_and_agrees_with_them(
        monkeypatch) -> None:
    """WS-28j2 end to end. The endpoint computes it from `model_dump()` — the
    exact payload the client receives — so "the rollup matches the table" is a
    property of the code path rather than a claim about it."""
    db = FakeDB(totals=[totals_row("priya@fracktal.in", open_tasks=6,
                                   mins=1200, overdue=1)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert [d["department"] for d in out.departments] == ["Engineering", "Sales"]
    assert out.org["headcount"] == 2
    assert out.org["pills"]["behind"] == 1
    assert out.org["contracted_hours"] == sum(r.contracted_hours
                                              for r in out.rows)


def test_an_agent_row_is_kept_out_of_the_rollups_headcount(monkeypatch) -> None:
    db = FakeDB(totals=[totals_row("agent:triage", open_tasks=9, mins=600)])
    bind(monkeypatch, db)
    out = run(people_dashboard.get_dashboard(user=BOSS))
    assert len(out.rows) == 3            # Priya, Ravi, the agent
    assert out.org["headcount"] == 2     # …and the agent is not one of them
    assert out.org["agents"] == 1


def test_away_this_week_is_a_wider_window_than_away_today(monkeypatch) -> None:
    """Somebody back tomorrow and somebody leaving on Thursday are both answers
    to "can I give them a deadline this week", and neither is "away now"."""
    today = date.today()
    sunday = today + timedelta(days=7 - today.isoweekday())
    if sunday == today:                  # today IS Sunday; nothing later fits
        pytest.skip("no future day inside this week to place the absence on")
    db = FakeDB(absences=[SimpleNamespace(
        person_id="id-priya", starts_on=sunday, ends_on=sunday,
        kind="away", hours_per_day=None)])
    bind(monkeypatch, db)
    priya = next(r for r in run(
        people_dashboard.get_dashboard(user=BOSS)).rows if r.name == "Priya")
    assert priya.away is None            # not away right now
    assert priya.away_this_week is True  # …but do not promise their Friday


def test_can_manage_travels_so_the_ui_need_not_guess(monkeypatch) -> None:
    bind(monkeypatch, FakeDB())
    assert run(people_dashboard.get_dashboard(user=BOSS)).can_manage is False
    assert run(people_dashboard.get_dashboard(user=MANAGER)).can_manage is True


# ══════════════════════════════════════════════════════════════════════════
# 4. The fences
# ══════════════════════════════════════════════════════════════════════════

_ROUTE_ORDER_PROBE = """
import json, sys
from gateway.routes.people import router
print(json.dumps([[getattr(r, "path", ""), sorted(getattr(r, "methods", []) or [])]
                  for r in router.routes]))
"""


def _package_route_order() -> list[tuple[str, list[str]]]:
    """The route order a FRESH process builds, with each route's methods.

    ⚠️ **The method is half the fact.** Starlette matches on path AND method, so
    `PATCH /people/{person_id}` sitting in front of `GET /people/dashboard` is
    not a collision at all — and the first version of this fence compared bare
    paths, found profile.py's PATCH first, and failed on a package that was
    correct. A fence that fires on a non-violation is worse than none: the fix
    for it is to weaken it.

    ⚠️ It cannot be read off the router inside this suite. `router` is a
    module-level singleton and the decorators run at import time, so by the time
    any one test imports the package, sibling test modules have already imported
    `directory` and `absences` directly in pytest's collection order — the
    observed order there is the TEST SESSION's, not the application's. The first
    version of this fence asserted against it and failed on a package whose
    import list was correct.
    """
    import json
    import os
    import subprocess
    import sys

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    out = subprocess.run([sys.executable, "-c", _ROUTE_ORDER_PROBE],
                         capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _shadowed(routes: list[tuple[str, list[str]]]) -> list[str]:
    """Literal `/people/<word>` routes a `/people/{person_id}` route would eat.

    One rule for the whole package, not one assertion per ticket, so the next
    literal path added here inherits the fence instead of rediscovering it in
    production. A shadowed GET reaches `get_person`, which answers 404 for a
    person called "dashboard" — a permissions-shaped mystery on a page whose
    permissions are fine.
    """
    out: list[str] = []
    for index, (path, methods) in enumerate(routes):
        if "{" in path or path.count("/") != 2:
            continue
        for other, other_methods in routes[:index]:
            if other == "/people/{person_id}" and set(methods) & set(other_methods):
                out.append(f"{'/'.join(methods)} {path}")
    return out


def test_dashboard_is_registered_before_the_person_id_pattern() -> None:
    routes = _package_route_order()
    assert ["/people/dashboard", ["GET"]] in [list(r) for r in routes], routes
    assert "GET /people/dashboard" not in _shadowed(routes)


def test_no_literal_people_path_is_shadowed_by_the_pattern() -> None:
    assert _shadowed(_package_route_order()) == []


def test_directory_is_imported_last_in_the_package() -> None:
    """The cheap half of the same fact, and the one that names the fix.

    `directory.py` is the module that registers `/people/{person_id}`, so its
    import has to come after every other module's. This is what `ruff --fix`
    would undo by alphabetising, which is why `__init__.py` carries
    `# ruff: noqa: I001` — and why a test says so rather than a comment.
    """
    source = (REPO / "apps/services/gateway/gateway/routes/people/__init__.py"
              ).read_text(encoding="utf-8")
    imports = re.findall(r"^from gateway\.routes\.people import (\w+)",
                         source, re.MULTILINE)
    assert imports[-1] == "directory", imports


#: The words that would turn a measurement surface into a performance one.
#: Matched as whole words on the SOURCE, because the way D-PC-14 gets broken is
#: not a decision anybody records — it is one reasonable-looking column called
#: `score` on a surface that already has every number it would need.
_PERFORMANCE = re.compile(
    r"\b(leaderboard|rank(?:ing|ed)?_people|performance_score|"
    r"productivity|utilisation_rank|utilization_rank|percentile)\b",
    re.IGNORECASE)


def _strip_prose(source: str) -> str:
    """Comments and docstrings out; identifiers, literals and rendered text in.

    ⚠️ **A fence over raw source would forbid explaining the refusal.** The
    first version of this one matched the whole file and failed on the very
    docstrings that say *"there is no leaderboard, no score, no percentile"* —
    punishing the explanation instead of the defect, and the cheapest way to
    make it pass would have been to delete the reasoning. What must stay clean
    is the part that RUNS: a variable called `score`, a string rendered as
    "Leaderboard", a `percentile()` call. Those all survive this stripping.
    """
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    without_line = re.sub(r"//[^\n]*", "", without_block)
    without_doc = re.sub(r'"""(?:.|\n)*?"""', "", without_line)
    return re.sub(r"#[^\n]*", "", without_doc)


@pytest.mark.parametrize("path", [
    "apps/services/gateway/gateway/workload.py",
    "apps/services/gateway/gateway/routes/people/dashboard.py",
    "workbench/control_plane/src/app/people/dashboard/page.tsx",
    "workbench/control_plane/src/app/people/lib/dashboard.ts",
])
def test_no_ranking_of_people_anywhere_on_this_surface(path: str) -> None:
    """D-PC-14: ranking TASKS by risk is the product; ranking PEOPLE by output
    is not. Every figure here is trivially gamed and trivially misread, and a
    leaderboard is one `sort()` away from a table that already holds the
    numbers — so the refusal is structural rather than a note in a spec."""
    source = _strip_prose((REPO / path).read_text(encoding="utf-8"))
    found = _PERFORMANCE.findall(source)
    assert not found, f"{path} reads as a performance surface: {found}"


def test_the_no_ranking_fence_would_actually_catch_one() -> None:
    """The fence's own fence. A refusal that cannot fire is a comment.

    Each of these is a plausible way this surface becomes a performance one —
    and each survives the prose stripping, because each is code rather than an
    explanation of why the code is not written that way.
    """
    for guilty in (
        'const heading = "Leaderboard";',
        "def performance_score(row): ...",
        "rows.sort(key=lambda r: r.percentile)",
        "export const productivity = 1;",
    ):
        assert _PERFORMANCE.findall(_strip_prose(guilty)), guilty
    # …and the explanation of the refusal does not fire it.
    assert not _PERFORMANCE.findall(_strip_prose(
        "# no leaderboard, no score, no percentile, no productivity metric"))


#: A judgement about a person, as opposed to a fact about work.
_JUDGEMENT = re.compile(
    r"\b(underperform\w*|slow|lazy|poor|weak|top performer|behind schedule "
    r"again|not pulling)\b", re.IGNORECASE)


def test_the_reason_strings_talk_about_tasks_not_about_people() -> None:
    """The distinction that makes the owner's ask and D-PC-14 compatible.

    ⚠️ Driven over `classify` rather than grepped over the module. The first
    version of this fence searched the SOURCE — and failed on the docstring
    that quotes *"Priya is underperforming"* in order to explain why the
    product never says it. A fence that forbids naming the thing you are
    refusing punishes the explanation and not the defect; what has to be clean
    is the OUTPUT.
    """
    emitted = {workload.classify(case)["reason"] for case in (
        metrics(overdue=1),
        metrics(overdue=4),
        metrics(at_risk=workload.at_risk_tasks(
            WEEK, [task(due=WED, mins=40 * 60)], [], MON)),
        metrics(committed_this_week=99.0),
        metrics(open_tasks=0, committed_this_week=0.0),
        metrics(committed_this_week=1.0),
        metrics(),
        metrics(open_tasks=1, unestimated=1, committed_this_week=0.0),
        metrics(contracted_hours=0.0),
    )}
    for reason in emitted:
        assert not _JUDGEMENT.search(reason), reason
        # Each one names a countable thing — a task, an hour or a date.
        assert re.search(r"\d", reason) or "No open tasks" in reason, reason
