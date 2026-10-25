"""WS-27bn RA-2 — the KPI strip and "Who did the work", on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B, D, E and F, and §8
RA-2.

``test_projects_report_kpis.py`` pins the Python rules. This file pins the
SQL of ``analytics_agents.py`` through the real report preview
(``POST /projects/reports/preview`` with ``kpis: true``, the path of the
Overview), as the non-privileged role of the phase-4 rehearsal (the
``promoted`` fixture), on a database with the whole ladder and two
organizations:

* **W1** ``actor_series``: each finish sits in the week of its finish, by
  label, and the weeks add up to ``actor_split`` and ``total_completed``.
* **C1** ``actor_cycle``: the median and the p90 of people and of agents.
  ``agent_assisted`` counts as agents. A task with no start stays out of the
  median and in the count. The Python twin agrees.
* **M1** ``agent_members``: each agent finish under its member, from the run
  or the agent act before the done move. The in-app agent names no member.
* **K1** ``kpis.runs``: working, silent (15 minutes with no agent act) and
  failed today. A run that waits on a person, a run on a done task and a
  failure before today do not count.
* **K2** ``kpis.merge``: merged over merged and closed, on pull requests of
  tasks that an agent worked before the close, in 14 days and in the 14
  days before. A draft, an open one, one with no agent work and one with
  agent work after the close do not count.
* **K3** ``kpis.time`` and ``kpis.cost``: the median agent run time of each
  finished task, start to end, from runs before the finish. Cost is unknown,
  and its share is zero.
* **K4** ``waiting.waiting_total`` and ``waiting.oldest_hours``.
* **V1** §7 rule 1 and §7.1 rule 3: a member who sees one task reads that
  task only, and the member row of another person is hidden and counted.
  The other organization reads zeros.
* **F1** with the runs flag off, ``kpis.runs`` reads nothing.

Run (a private base database, one R8 suite at a time)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_ra2 \\
        uv run pytest tests/unit/test_projects_report_kpis_r8.py -v -rs

The suite carries ``_DB_GATE`` through ``pytestmark``, so it skips where no
ladder URL is set, and its fixtures never start there.
"""

from __future__ import annotations

import json
import uuid
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

A_EMAIL = "ra2-a@kpis-r8.test"
PERSON = "ra2-p@kpis-r8.test"
M_EMAIL = "ra2-m@kpis-r8.test"
B_EMAIL = "ra2-b@kpis-r8.test"
IN_APP = "agent:coder"


def _admin(email: str, org: str) -> Any:
    """An admin of §7.1: the HR grant lets the reader see every member row."""
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


def _task(w: Any, category: str = "todo") -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, w.root, category=category)


def _assign(w: Any, tid: str, who: str) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
            "assigned_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :a, :e)"),
            {"o": w.p.org_a, "t": tid, "a": who, "e": A_EMAIL})


def _move(
    w: Any, tid: str, by: str, *, via: str | None = None, frm: str = "todo",
    to: str = "done", minutes_ago: int = 5,
) -> None:
    """One `status_change` row, as `apply_status_transition` writes it."""
    meta: dict[str, Any] = {"from": frm, "to": to, "from_category": frm, "to_category": to}
    if via is not None:
        meta["via"] = via
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, "
            "body, meta, created_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), "
            "'status_change', :by, :body, CAST(:m AS jsonb), "
            "now() - make_interval(mins => :ago))"),
            {"o": w.p.org_a, "t": tid, "by": by, "body": f"{frm} -> {to}",
             "m": json.dumps(meta), "ago": minutes_ago})


def _ago(minutes: int | None) -> str:
    return "NULL" if minutes is None else f"now() - make_interval(mins => {int(minutes)})"


def _run(
    w: Any, tid: str, status: str, *, email: str = A_EMAIL, created: int = 0,
    started: int | None = None, ended: int | None = None,
    last_activity: int = 0, last_agent: int | None = 0, org: str | None = None,
) -> str:
    """One run. Each time is in minutes before now, or None for NULL."""
    rid = str(uuid.uuid4())
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (id, organization_id, task_id, member_email, "
            "client_via, status, started_by, created_at, started_at, ended_at, "
            "last_activity_at, last_agent_activity_at) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), CAST(:t AS uuid), :e, :via, :s, :e, "
            f"{_ago(created)}, {_ago(started)}, {_ago(ended)}, "
            f"{_ago(last_activity)}, {_ago(last_agent)})"),
            {"id": rid, "o": org or w.p.org_a, "t": tid, "e": email, "via": b.VIA,
             "s": status})
    return rid


async def _preview(
    w: Any, user: Any, *, sections: list[str] | None = None, project: bool = True,
) -> dict[str, Any]:
    """The body of the Overview's preview, with its KPI strip."""
    body: dict[str, Any] = {
        "config": {
            "sections": sections or ["finished", "throughput", "waiting"],
            "weeks": 12, "skip_current_week": False,
        },
        "kpis": True,
    }
    if project:
        body["project_id"] = w.root["id"]
    async with b._session(w.p) as call:
        got = await call(user, "POST", "/projects/reports/preview", body)
    assert got.status_code == 200, got.text
    return got.json()


def _week_of(w: Any, minutes_ago: int) -> str:
    """The ISO Monday, in UTC, of a time this many minutes ago."""
    with w.p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT CAST(date_trunc('week', (now() - make_interval(mins => :m))"
            " AT TIME ZONE 'UTC') AS date)"), {"m": minutes_ago}).scalar().isoformat()


#: A finish eight days ago: last week, or the week before it on a Monday.
_LAST_WEEK = 8 * 24 * 60


def _seed_finishes(w: Any) -> dict[str, str]:
    """Seven finishes with known labels and cycle times (minutes ago)."""
    t = {name: _task(w, "done") for name in (
        "mcp", "assisted", "no_start", "in_app", "person_48", "person_24", "old_person",
    )}
    # Agents: 5h through MCP, 1h after a run then a person, none measured.
    _move(w, t["mcp"], A_EMAIL, frm="todo", to="in_progress", minutes_ago=7 * 60)
    _move(w, t["mcp"], A_EMAIL, via=b.VIA, frm="in_progress", minutes_ago=2 * 60)
    _move(w, t["assisted"], PERSON, frm="todo", to="in_progress", minutes_ago=3 * 60)
    _run(w, t["assisted"], "completed", created=170, last_activity=150, last_agent=150)
    _move(w, t["assisted"], PERSON, frm="in_progress", minutes_ago=2 * 60)
    _move(w, t["no_start"], A_EMAIL, via=b.VIA, minutes_ago=2 * 60)
    _move(w, t["in_app"], IN_APP, minutes_ago=2 * 60)
    # People: 48h and 24h.
    _move(w, t["person_48"], PERSON, frm="todo", to="in_progress", minutes_ago=50 * 60)
    _move(w, t["person_48"], PERSON, frm="in_progress", minutes_ago=2 * 60)
    _move(w, t["person_24"], PERSON, frm="todo", to="in_progress", minutes_ago=26 * 60)
    _move(w, t["person_24"], PERSON, frm="in_progress", minutes_ago=2 * 60)
    _move(w, t["old_person"], PERSON, minutes_ago=_LAST_WEEK)
    return t


# ── W1 ───────────────────────────────────────────────────────────────────────


class TestWeeklySeries:
    async def test_w1_each_finish_sits_in_its_week_by_label(self, w):
        _seed_finishes(w)
        fin = (await _preview(w, w.a))["sections"]["finished"]
        series = {wk["week_start"]: wk for wk in fin["actor_series"]}
        assert len(fin["actor_series"]) == 12
        recent, old = series[_week_of(w, 2 * 60)], series[_week_of(w, _LAST_WEEK)]
        if recent is old:  # pragma: no cover — a run at 02:00 on a Monday
            pytest.skip("the two seeded weeks fell into one bucket")
        assert (recent["agent_finished"], recent["agent_assisted"], recent["person"]) == (
            3, 1, 2)
        assert (recent["agents"], recent["people"], recent["completed"]) == (4, 2, 6)
        assert (old["person"], old["completed"]) == (1, 1)
        # The weeks add up to the split and the total of the same window.
        for label in ("agent_finished", "agent_assisted", "person"):
            assert sum(wk[label] for wk in fin["actor_series"]) == fin["actor_split"][label]
        assert sum(wk["completed"] for wk in fin["actor_series"]) == fin["total_completed"] == 7
        assert fin["actor_summary"] == (
            "7 tasks finished in this period. Agents worked on 4, and people did 3."
            " A person finished 1 of the agent tasks."
        )


# ── C1 ───────────────────────────────────────────────────────────────────────


class TestCycleSplit:
    async def test_c1_each_group_has_its_median_and_p90(self, w):
        from gateway.routes.projects.analytics_waiting import percentile_cont

        _seed_finishes(w)
        thr = (await _preview(w, w.a))["sections"]["throughput"]
        cycle = thr["actor_cycle"]
        agents, people = cycle["agents"], cycle["people"]
        assert (agents["completed"], agents["assisted"], agents["measured"]) == (4, 1, 2)
        assert agents["median_hours"] == pytest.approx(3.0, abs=0.02)
        assert agents["p90_hours"] == pytest.approx(percentile_cont([5.0, 1.0], 0.9), abs=0.02)
        # `old_person` has no start, so it is in the count and not the median.
        assert (people["completed"], people["measured"]) == (3, 2)
        assert people["median_hours"] == pytest.approx(36.0, abs=0.02)
        assert people["p90_hours"] == pytest.approx(
            percentile_cont([48.0, 24.0], 0.9), abs=0.02)
        assert agents["completed"] + people["completed"] == thr["completed"]
        assert thr["actor_summary"] == (
            "At the median, agent tasks took 3 hours and tasks of people took 36 hours."
        )


# ── M1 ───────────────────────────────────────────────────────────────────────


class TestMembers:
    async def test_m1_each_agent_under_its_member(self, w):
        from acb_auth import agent_oauth

        _seed_finishes(w)
        fin = (await _preview(w, w.a))["sections"]["finished"]
        members = fin["agent_members"]
        assert [m["member"] for m in members] == [A_EMAIL, None]
        a = members[0]
        assert a["total"] == 3
        assert a["agents"] == [{
            "agent": agent_oauth.client_label(b.VIA), "client_via": b.VIA,
            "agent_finished": 2, "agent_assisted": 1, "total": 3,
        }]
        assert members[1]["agents"][0]["agent"] == "coder"
        assert fin["agent_members_total"] == 2
        # An admin reads every row, so no hidden count is sent.
        assert "hidden_people" not in fin


# ── K1 ───────────────────────────────────────────────────────────────────────


class TestRunsNow:
    async def test_k1_working_silent_and_failed_today(self, w):
        working = _task(w)
        _run(w, working, "in_progress", started=60, last_activity=0, last_agent=0)
        # A member prompt 0 minutes ago, the agent quiet for 20: silent.
        quiet = _task(w)
        _run(w, quiet, "in_progress", started=60, last_activity=0, last_agent=20)
        # The pill reads this one stale (10 minutes). It is not silent yet.
        idle = _task(w)
        _run(w, idle, "in_progress", email=PERSON, started=60, last_activity=10,
             last_agent=10)
        # A run with no agent act at all, started 30 minutes ago: silent.
        mute = _task(w)
        _run(w, mute, "in_progress", started=30, last_activity=30, last_agent=None)
        asking = _task(w)
        _run(w, asking, "awaiting", started=60)
        done = _task(w, "done")
        _run(w, done, "in_progress", started=60)
        failed = _task(w)
        _run(w, failed, "failed", started=30, ended=0, last_activity=0)
        before = _task(w, "done")
        _run(w, before, "failed", created=3 * 24 * 60, started=3 * 24 * 60,
             ended=2 * 24 * 60, last_activity=2 * 24 * 60, last_agent=2 * 24 * 60)

        runs = (await _preview(w, w.a))["kpis"]["runs"]
        assert runs["on"] is True
        assert (runs["working"], runs["silent"], runs["failed_today"]) == (2, 2, 1)
        assert runs["silent_after_minutes"] == 15
        assert runs["members"] == [
            {"member": A_EMAIL, "member_name": A_EMAIL, "working": 1, "silent": 2},
            {"member": PERSON, "member_name": PERSON, "working": 1, "silent": 0},
        ]


# ── K2 ───────────────────────────────────────────────────────────────────────


class TestMergeRate:
    async def test_k2_merged_over_merged_and_closed(self, w):
        day = 24 * 60

        def worked(minutes_ago: int) -> str:
            tid = _task(w, "done")
            _run(w, tid, "completed", created=minutes_ago, started=minutes_ago,
                 ended=minutes_ago - 10, last_activity=minutes_ago - 10,
                 last_agent=minutes_ago - 10)
            return tid

        merged = worked(3 * day)
        b.ref(w.p, w.p.org_a, merged, 1, "merged", merged="now() - interval '2 days'")
        closed = worked(3 * day)
        b.ref(w.p, w.p.org_a, closed, 2, "closed", updated="now() - interval '1 day'")
        merged_before = worked(25 * day)
        b.ref(w.p, w.p.org_a, merged_before, 3, "merged",
              merged="now() - interval '20 days'")
        no_agent = _task(w, "done")
        b.ref(w.p, w.p.org_a, no_agent, 4, "merged", merged="now() - interval '1 day'")
        still_open = worked(3 * day)
        b.ref(w.p, w.p.org_a, still_open, 5, "open")
        draft = worked(3 * day)
        b.ref(w.p, w.p.org_a, draft, 6, "draft")
        after = _task(w, "done")
        b.ref(w.p, w.p.org_a, after, 7, "merged", merged="now() - interval '1 day'")
        _run(w, after, "completed", created=0, started=0, ended=0)
        ancient = worked(60 * day)
        b.ref(w.p, w.p.org_a, ancient, 8, "closed", updated="now() - interval '40 days'")

        merge = (await _preview(w, w.a))["kpis"]["merge"]
        assert (merge["merged"], merge["closed"], merge["rate"]) == (1, 1, 0.5)
        assert (merge["merged_before"], merge["closed_before"]) == (1, 0)
        assert merge["rate_before"] == 1.0
        assert merge["trend"] == "down"
        assert merge["days"] == 14


# ── K3 ───────────────────────────────────────────────────────────────────────


class TestRunTimeAndCost:
    async def test_k3_median_run_time_of_each_finished_task(self, w):
        two_runs = _task(w, "done")
        _run(w, two_runs, "completed", created=300, started=300, ended=180)
        _run(w, two_runs, "completed", created=150, started=150, ended=120)
        _move(w, two_runs, PERSON, minutes_ago=60)
        one_run = _task(w, "done")
        _run(w, one_run, "completed", created=240, started=240, ended=180)
        _move(w, one_run, A_EMAIL, via=b.VIA, minutes_ago=60)
        # A run after the finish did not help that finish.
        late = _task(w, "done")
        _move(w, late, PERSON, minutes_ago=60)
        _run(w, late, "completed", created=30, started=30, ended=10)
        # A queued run that never started has no time.
        queued = _task(w, "done")
        _run(w, queued, "abandoned", created=200, started=None, ended=100)
        _move(w, queued, PERSON, minutes_ago=60)
        _move(w, _task(w, "done"), PERSON, minutes_ago=60)

        kpis = (await _preview(w, w.a))["kpis"]
        time = kpis["time"]
        assert (time["finished"], time["run_tasks"]) == (5, 2)
        assert time["median_hours"] == pytest.approx(1.75, abs=0.02)
        assert time["total_hours"] == pytest.approx(3.5, abs=0.02)
        assert time["basis"] == "run_start_to_end"
        cost = kpis["cost"]
        assert cost["per_task"] is None and cost["known_tasks"] == 0
        assert cost["finished"] == 5 and cost["share"] == 0.0


# ── K4 ───────────────────────────────────────────────────────────────────────


class TestWaitingTile:
    async def test_k4_the_waiting_total_and_the_oldest_wait(self, w):
        older = _task(w)
        rid = _run(w, older, "awaiting", started=600)
        with w.p.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, "
                "type, content, actor, actor_via, created_at) VALUES (CAST(:o AS uuid), "
                "CAST(:r AS uuid), CAST(:t AS uuid), 'elicitation', "
                "CAST('{\"body\": \"which?\"}' AS jsonb), :e, :via, "
                "now() - interval '3 hours')"),
                {"o": w.p.org_a, "r": rid, "t": older, "e": A_EMAIL, "via": b.VIA})
        waiting = (await _preview(w, w.a))["sections"]["waiting"]
        assert waiting["waiting_total"] == (
            waiting["questions_total"] + waiting["reviews_total"] + waiting["handoffs_total"]
        ) == 1
        assert waiting["oldest_hours"] == pytest.approx(3.0, abs=0.05)
        assert waiting["oldest_hours"] == waiting["questions"][0]["age_hours"]


# ── V1 and F1 ────────────────────────────────────────────────────────────────


class TestVisibility:
    async def test_v1_a_member_reads_only_what_they_see(self, w):
        """M is no admin and holds no grant, so M sees the tasks M holds and
        nothing else. B is in the other organization and sees nothing."""
        mine_done = _task(w, "done")
        _assign(w, mine_done, M_EMAIL)
        _move(w, mine_done, A_EMAIL, via=b.VIA, minutes_ago=60)
        mine_open = _task(w)
        _assign(w, mine_open, M_EMAIL)
        _run(w, mine_open, "in_progress", started=10, last_activity=0, last_agent=0)
        _seed_finishes(w)
        _run(w, _task(w), "in_progress", started=10)

        body = await _preview(w, w.m, project=False)
        fin = body["sections"]["finished"]
        assert fin["total_completed"] == 1
        assert sum(wk["completed"] for wk in fin["actor_series"]) == 1
        # The agent worked for A, and M may not report on A (§7.1 rule 3).
        assert fin["agent_members"] == [] and fin["hidden_people"] == 1
        runs = body["kpis"]["runs"]
        assert (runs["working"], runs["silent"]) == (1, 0)
        assert runs["members"] == [] and runs["hidden_people"] == 1
        assert body["sections"]["throughput"]["actor_cycle"]["agents"]["completed"] == 1

        other = await _preview(w, w.b, project=False)
        ofin = other["sections"]["finished"]
        assert ofin["total_completed"] == 0
        assert sum(wk["completed"] for wk in ofin["actor_series"]) == 0
        assert ofin["agent_members"] == []
        okpis = other["kpis"]
        assert (okpis["runs"]["working"], okpis["runs"]["silent"]) == (0, 0)
        assert okpis["merge"]["merged"] == 0 and okpis["time"]["finished"] == 0

    async def test_f1_the_runs_flag_off_reads_no_run(self, w, monkeypatch):
        from acb_common import get_settings

        _run(w, _task(w), "in_progress", started=10)
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        runs = (await _preview(w, w.a))["kpis"]["runs"]
        assert runs["on"] is False
        assert (runs["working"], runs["silent"], runs["failed_today"]) == (0, 0, 0)

    async def test_a_preview_without_the_key_has_no_strip(self, w):
        body: dict[str, Any] = {"project_id": w.root["id"], "config": {
            "sections": ["finished"], "weeks": 12, "skip_current_week": False,
        }}
        async with b._session(w.p) as call:
            got = await call(w.a, "POST", "/projects/reports/preview", body)
        assert got.status_code == 200, got.text
        assert "kpis" not in got.json()
