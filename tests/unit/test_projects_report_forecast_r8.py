"""WS-27bn RA-6 — the forecast by count and "Needs an owner", on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B and F, the §5 rows
"Forecast by count", "Not converging", "Not enough history" and "Needs an
owner", and §8 RA-6.

``test_projects_report_forecast.py`` pins the Python twin of each rule. This
file pins the SQL through the real report preview (``POST
/projects/reports/preview``, the path of the Overview) and the real routes,
as the non-privileged role of the phase-4 rehearsal (the ``promoted``
fixture), on a database with the whole ladder and two organizations:

* **W1** the weekly series that feeds the forecast is Throughput's: the
  finishes of each closed week, on the one activity spine. The forecast
  counts the weeks with a finish, and its rate is their mean.
* **W2** the same scope and the same week give the same dates, in the
  section and in the route.
* **W3** a scope with finishes in one week of six says "Not enough history",
  and one with finishes in two weeks gets a date (fix round 1).
* **W4** the route takes ``weeks=3``, and three weeks of history give a date.
* **N1** "Needs an owner" is an open task with no assignee and no open run.
  An unassigned task whose open run went quiet is in ``none`` and does not
  need an owner. A closed run, a closed task and an archived task follow
  the rule.
* **N2** the route and the section give one answer, and it leads Open work
  when it is at least each holder.
* **V1** §7 rule 1: a member who sees one project reads its tasks only, and
  the other organization reads nothing.

Run (a private base database, one R8 suite at a time)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_ra6 \\
        uv run pytest tests/unit/test_projects_report_forecast_r8.py -v -rs

The suite carries ``_DB_GATE`` through ``pytestmark``, so it skips where no
ladder URL is set, and its fixtures never start there.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

A_EMAIL = "ra6-a@forecast-r8.test"
PERSON = "ra6-p@forecast-r8.test"
M_EMAIL = "ra6-m@forecast-r8.test"
B_EMAIL = "ra6-b@forecast-r8.test"


def _admin(email: str, org: str) -> Any:
    """An admin of §7.1, as the KPI suite reads the reports."""
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:projects", "data:org:read", "admin:members:read"]
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
                       organization_id=org)


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    ns = SimpleNamespace(p=promoted)
    ns.a = _admin(A_EMAIL, promoted.org_a)
    ns.m = b._principal(M_EMAIL, promoted.org_a, unrestricted=False)
    ns.b = b._principal(B_EMAIL, promoted.org_b)
    b.member(promoted, promoted.org_a, PERSON)
    b.member(promoted, promoted.org_a, M_EMAIL)
    b.member(promoted, promoted.org_b, B_EMAIL)
    return ns


@pytest.fixture
def w(world, monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    b.clear(world.p)
    world.root = b.project(world.p, world.p.org_a, A_EMAIL)
    return world


# ── Seeds ────────────────────────────────────────────────────────────────────


def _task(w: Any, category: str = "todo", *, root: Any = None,
          assignee: str | None = None, title: str = "Fix the extruder") -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, root or w.root, category=category,
                  assignee=assignee, title=title)


def _weeks_ago(n: int) -> str:
    """A moment inside the week ``n`` weeks before this one, in UTC."""
    return (
        f"((date_trunc('week', now() AT TIME ZONE 'UTC')"
        f" - make_interval(weeks => {int(n)}) + interval '2 days')"
        f" AT TIME ZONE 'UTC')"
    )


def _finish(w: Any, weeks_ago: int, *, root: Any = None) -> str:
    """A task that moved into done in the week ``weeks_ago`` weeks back."""
    tid = _task(w, "done", root=root)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, body, "
            f"meta, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), 'status_change', "
            f":e, 'todo -> done', CAST(:m AS jsonb), {_weeks_ago(weeks_ago)})"),
            {"o": w.p.org_a, "t": tid, "e": A_EMAIL,
             "m": json.dumps({"from_category": "todo", "to_category": "done"})})
    return tid


def _history(w: Any, per_week: dict[int, int], *, root: Any = None) -> None:
    """``{weeks_ago: finishes}`` over the closed weeks."""
    for weeks_ago, n in per_week.items():
        for _ in range(n):
            _finish(w, weeks_ago, root=root)


def _run(w: Any, tid: str, status: str, *, idle_minutes: int = 0) -> str:
    """One run on *tid*, with its last activity ``idle_minutes`` ago."""
    seen = f"now() - make_interval(mins => {int(idle_minutes)})"
    return b.run(w.p, w.p.org_a, tid, A_EMAIL, status, last_activity=seen, last_agent=seen)


async def _preview(w: Any, user: Any, sections: list[str], *, root: Any = None,
                   project: bool = True, weeks: int = 1) -> dict[str, Any]:
    body: dict[str, Any] = {
        "config": {"sections": sections, "weeks": weeks, "skip_current_week": True},
    }
    if project:
        body["project_id"] = (root or w.root)["id"]
    async with b._session(w.p) as call:
        got = await call(user, "POST", "/projects/reports/preview", body)
    assert got.status_code == 200, got.text
    return got.json()["sections"]


async def _route(w: Any, user: Any, path: str, *, root: Any = None) -> dict[str, Any]:
    async with b._session(w.p) as call:
        got = await call(user, "GET",
                         f"{path}?project_id={(root or w.root)['id']}&include_subtree=true")
    assert got.status_code == 200, got.text
    return got.json()


# ── W1 to W3: the forecast ──────────────────────────────────────────────────


class TestTheForecast:
    async def test_w1_the_forecast_reads_the_throughput_weeks(self, w):
        _history(w, {1: 3, 2: 1, 3: 2, 5: 4, 6: 1})
        for _ in range(9):
            _task(w)
        # Eight weeks of Throughput: the six the forecast reads, one more,
        # and this week. The forecast reads its own six whatever the period.
        got = await _preview(w, w.a, ["outlook", "throughput"], weeks=8)
        velocity, series = got["outlook"]["velocity"], got["throughput"]["series"]
        # Throughput's weeks hold this week at the end. The forecast reads
        # the six closed weeks before it.
        closed = [wk["completed"] for wk in series[:-1]][-6:]
        assert closed == [1, 4, 0, 2, 1, 3]
        assert velocity["weeks_sampled"] == 6
        assert velocity["weeks_with_finishes"] == sum(1 for n in closed if n > 0) == 5
        assert velocity["finished_per_week"] == round(sum(closed) / 6, 2)
        assert velocity["created_per_week"] == 0.0
        assert velocity["remaining_tasks"] == 9
        assert velocity["verdict"] == "converging", velocity
        assert velocity["p50_weeks"] <= velocity["p85_weeks"]
        assert velocity["finish_date"] == velocity["p50_date"]
        assert velocity["summary"].startswith("Half of 2,000 runs finish the 9 open tasks by")

    async def test_w2_the_same_scope_and_week_give_the_same_dates(self, w):
        _history(w, {1: 2, 2: 0, 3: 5, 4: 1, 5: 0, 6: 3})
        for _ in range(14):
            _task(w)
        first = (await _preview(w, w.a, ["outlook"]))["outlook"]["velocity"]
        again = (await _preview(w, w.a, ["outlook"]))["outlook"]["velocity"]
        route = (await _route(w, w.a, "/projects/analytics/outlook"))["velocity"]
        assert first["verdict"] == "converging", first
        assert (first["p50_date"], first["p85_date"]) == (again["p50_date"], again["p85_date"])
        assert first == again == route

    async def test_w3_finishes_in_one_week_are_not_enough_history(self, w):
        _history(w, {1: 6})
        for _ in range(5):
            _task(w)
        velocity = (await _preview(w, w.a, ["outlook"]))["outlook"]["velocity"]
        assert velocity["verdict"] == "no_history"
        assert velocity["weeks_with_finishes"] == 1
        assert velocity["finished_in_window"] == 6
        assert velocity["p50_date"] is None and velocity["finish_date"] is None
        assert velocity["summary"] == (
            "Not enough history to forecast: 6 tasks finished in 1 week of the"
            " last 6. A forecast needs 3 finished tasks, in 2 weeks or more."
        )
        # A second week with a finish gives a date (fix round 1).
        _history(w, {4: 2})
        again = (await _preview(w, w.a, ["outlook"]))["outlook"]["velocity"]
        assert again["verdict"] == "converging", again

    async def test_w4_the_route_takes_three_weeks(self, w):
        """Fix round 1, item 3. `GET /analytics/outlook` takes 2 to 26 weeks,
        and the history gate no longer needs four of them."""
        _history(w, {1: 2, 2: 0, 3: 3})
        for _ in range(6):
            _task(w)
        async with b._session(w.p) as call:
            got = await call(
                w.a, "GET",
                f"/projects/analytics/outlook?project_id={w.root['id']}&include_subtree=true&weeks=3",
            )
        assert got.status_code == 200, got.text
        velocity = got.json()["velocity"]
        assert got.json()["weeks"] == 3
        assert velocity["weeks_sampled"] == 3
        assert velocity["finished_in_window"] == 5
        assert velocity["verdict"] == "converging", velocity
        assert velocity["p50_date"] and velocity["p85_date"]


# ── N1 and N2: Needs an owner ───────────────────────────────────────────────


def _seed_owners(w: Any) -> dict[str, str]:
    """One task of each case of §6.8 F, by name."""
    made = {
        "nobody": _task(w, title="nobody"),
        "closed_run": _task(w, title="closed run"),
        "person": _task(w, assignee=PERSON, title="person"),
        "agent": _task(w, assignee="agent:summariser", title="agent"),
        "live_run": _task(w, title="live run"),
        "quiet_run": _task(w, title="quiet run"),
        "queued_run": _task(w, title="queued run"),
        "done": _task(w, "done", title="done"),
        "archived": _task(w, title="archived"),
    }
    _run(w, made["closed_run"], "completed", idle_minutes=30)
    _run(w, made["live_run"], "in_progress", idle_minutes=1)
    # Three hours with no act: past the two-hour lease, still open.
    _run(w, made["quiet_run"], "in_progress", idle_minutes=180)
    _run(w, made["queued_run"], "created", idle_minutes=1)
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_tasks SET archived_at = now() WHERE id = CAST(:t AS uuid)"),
                  {"t": made["archived"]})
    return made


class TestNeedsAnOwner:
    async def test_n1_no_assignee_and_no_open_run(self, w):
        made = _seed_owners(w)
        load = (await _preview(w, w.a, ["load"]))["load"]
        owner = load["needs_owner"]
        assert {t["id"] for t in owner["tasks"]} == {made["nobody"], made["closed_run"]}
        assert owner["total"] == 2
        # Each row carries what opens it, and the title it shows.
        assert {t["title"] for t in owner["tasks"]} == {"nobody", "closed run"}
        assert all(t["project_id"] == w.root["id"] for t in owner["tasks"])
        # RA-1's `none`: the quiet run lost its lease, so nobody holds that
        # task now. It has an owner that stopped, so it needs no new one.
        assert load["holder_split"]["none"] == owner["total"] + 1
        assert made["quiet_run"] not in {t["id"] for t in owner["tasks"]}

    async def test_n2_the_route_and_the_section_agree_and_it_leads(self, w):
        _seed_owners(w)
        section = (await _preview(w, w.a, ["load"]))["load"]["needs_owner"]
        route = (await _route(w, w.a, "/projects/analytics/load"))["needs_owner"]
        assert section == route
        # Two need an owner, and the person and the agent hold one each.
        assert section["leads"] is True
        for _ in range(3):
            _task(w, assignee=PERSON)
        later = (await _preview(w, w.a, ["load"]))["load"]["needs_owner"]
        assert later["total"] == 2 and later["leads"] is False

    async def test_n2_the_soonest_due_comes_first(self, w):
        late = _task(w, title="late")
        undated = _task(w, title="undated")
        soon = _task(w, title="soon")
        with w.p.admin_engine.begin() as c:
            c.execute(text("UPDATE pm_tasks SET due_at = now() - interval '2 days'"
                           " WHERE id = CAST(:t AS uuid)"), {"t": late})
            c.execute(text("UPDATE pm_tasks SET due_at = now() + interval '3 days'"
                           " WHERE id = CAST(:t AS uuid)"), {"t": soon})
        owner = (await _preview(w, w.a, ["load"]))["load"]["needs_owner"]
        assert [t["id"] for t in owner["tasks"]] == [late, soon, undated]


# ── V1: §7 rule 1 ───────────────────────────────────────────────────────────


class TestVisibility:
    async def test_v1_a_member_reads_only_what_they_see(self, w):
        """M is no admin and holds a grant on one project only. B is in the
        other organization."""
        _seed_owners(w)
        seen = b.project(w.p, w.p.org_a, A_EMAIL, grant=M_EMAIL)
        mine = _task(w, root=seen, title="mine to see")
        _history(w, {1: 1, 2: 1, 3: 1, 4: 1}, root=seen)

        load = (await _preview(w, w.m, ["load"], project=False))["load"]
        assert [t["id"] for t in load["needs_owner"]["tasks"]] == [mine]
        assert load["needs_owner"]["total"] == 1
        outlook = (await _preview(w, w.m, ["outlook"], project=False))["outlook"]
        assert outlook["velocity"]["remaining_tasks"] == 1
        assert outlook["velocity"]["weeks_with_finishes"] == 4

        other = (await _preview(w, w.b, ["load", "outlook"], project=False))
        assert other["load"]["needs_owner"] == {
            "total": 0, "max_rows": 20, "tasks": [], "leads": False,
        }
        assert other["outlook"]["velocity"]["remaining_tasks"] == 0
        assert other["outlook"]["velocity"]["verdict"] == "nothing_left"
