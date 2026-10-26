"""WS-27bn RA-4 — "Agent runs", on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B, E and F, the §5
rows "Run outcome", "Success rate", "Silent run", "Run size" and "Long run",
and §8 RA-4.

``test_projects_report_agent_runs.py`` pins the Python twin of each rule.
This file pins the SQL of ``analytics_agent_runs.py`` through the real
report preview (``POST /projects/reports/preview``, the path of the
Overview), as the non-privileged role of the phase-4 rehearsal (the
``promoted`` fixture), on a database with the whole ladder and two
organizations:

* **O1** the outcomes: each run that ended in the last 14 UTC days counts by
  its outcome, on the day it ended. A run that ended before the window, and
  an open run, do not. The days add up to the outcomes.
* **R1** the success rate: completed over completed, failed and abandoned.
  A stop and a release stay out. The trend compares the 14 days before.
* **S1** the silent runs: an open run in ``in_progress`` with no agent act
  for 15 minutes or more, on an open task, the longest silence first. Each
  row names the agent and its member. The total equals the silent count of
  the KPI tile.
* **Z1** the run sizes: the completed runs of the window by agent time,
  which ends at the agent's last act and leaves out each wait on a person.
  The long runs come the longest first, with the flag. The Python twin
  agrees.
* **T1** the size rule is one rule in Python and in SQL.
* **V1** §7 rule 1: a member who sees two tasks reads those tasks only, and
  the other organization reads nothing and sees no run.
* **H1** a scope with no agent run says so (``agent_runs_seen`` false), so
  the Overview hides the panel.
* **F1** with the runs flag off, the section reads nothing.

Run (a private base database, one R8 suite at a time)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_ra4 \\
        uv run pytest tests/unit/test_projects_report_agent_runs_r8.py -v -rs

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

A_EMAIL = "ra4-a@runs-r8.test"
PERSON = "ra4-p@runs-r8.test"
M_EMAIL = "ra4-m@runs-r8.test"
B_EMAIL = "ra4-b@runs-r8.test"

#: Minutes in a day.
DAY = 24 * 60


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


def _task(w: Any, category: str = "todo", *, assignee: str | None = None) -> str:
    return b.task(w.p, w.p.org_a, A_EMAIL, w.root, category=category, assignee=assignee)


def _ago(minutes: int | None) -> str:
    return "NULL" if minutes is None else f"now() - make_interval(mins => {int(minutes)})"


def _run(
    w: Any, tid: str, status: str, *, email: str = A_EMAIL, created: int | None = None,
    started: int | None = None, ended: int | None = None,
    last_activity: int | None = None, last_agent: int | None = 0,
) -> str:
    """One run. Each time is in minutes before now, or None for NULL. The
    creation and the last activity default to the start, or to now."""
    rid = str(uuid.uuid4())
    born = created if created is not None else (started if started is not None else 0)
    seen = last_activity if last_activity is not None else (ended if ended is not None else 0)
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (id, organization_id, task_id, member_email, "
            "client_via, status, started_by, created_at, started_at, ended_at, "
            "last_activity_at, last_agent_activity_at) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), CAST(:t AS uuid), :e, :via, :s, :e, "
            f"{_ago(born)}, {_ago(started)}, {_ago(ended)}, "
            f"{_ago(seen)}, {_ago(last_agent)})"),
            {"id": rid, "o": w.p.org_a, "t": tid, "e": email, "via": b.VIA, "s": status})
    return rid


def _ended(w: Any, status: str, minutes_ago: int, **kw: Any) -> str:
    """A run that reached *status* this many minutes ago, after ten minutes."""
    tid = _task(w, kw.pop("category", "todo"), assignee=kw.pop("assignee", None))
    _run(w, tid, status, started=minutes_ago + 10, ended=minutes_ago,
         last_agent=minutes_ago, **kw)
    return tid


def _run_act(
    w: Any, rid: str, tid: str, kind: str, *, minutes_ago: int, reply_to: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """One run activity at a chosen time. A prompt may answer a question."""
    aid = str(uuid.uuid4())
    with w.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_run_activities (id, organization_id, run_id, task_id, "
            "type, content, actor, actor_via, in_reply_to, created_at) VALUES "
            "(CAST(:id AS uuid), CAST(:o AS uuid), CAST(:r AS uuid), CAST(:t AS uuid), "
            ":k, CAST(:c AS jsonb), :e, :via, CAST(:reply AS uuid), "
            "now() - make_interval(mins => :ago))"),
            {"id": aid, "o": w.p.org_a, "r": rid, "t": tid, "k": kind,
             "c": json.dumps({"body": kind, **(extra or {})}), "e": A_EMAIL,
             "via": None if kind == "prompt" else b.VIA, "reply": reply_to,
             "ago": minutes_ago})
    return aid


async def _preview(
    w: Any, user: Any, *, project: bool = True, kpis: bool = False, root: Any = None,
) -> dict[str, Any]:
    """The body of a preview of the `agent_runs` section."""
    body: dict[str, Any] = {
        "config": {"sections": ["agent_runs"], "weeks": 1, "skip_current_week": True},
    }
    if kpis:
        body["kpis"] = True
    if project:
        body["project_id"] = (root or w.root)["id"]
    async with b._session(w.p) as call:
        got = await call(user, "POST", "/projects/reports/preview", body)
    assert got.status_code == 200, got.text
    return got.json()


async def _section(w: Any, user: Any, **kw: Any) -> dict[str, Any]:
    return (await _preview(w, user, **kw))["sections"]["agent_runs"]


def _day_of(w: Any, minutes_ago: int) -> str:
    """The UTC day, as ``YYYY-MM-DD``, of a time this many minutes ago."""
    with w.p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT CAST(date_trunc('day', (now() - make_interval(mins => :m))"
            " AT TIME ZONE 'UTC') AS date)"), {"m": minutes_ago}).scalar().isoformat()


# ── O1 and R1 ────────────────────────────────────────────────────────────────


def _seed_outcomes(w: Any) -> None:
    """Seven runs that ended in the window, two before it, one long ago, and
    one that is open."""
    _ended(w, "completed", 2 * DAY)
    _ended(w, "completed", 2 * DAY + 30)
    _ended(w, "completed", 12 * DAY + 60)
    _ended(w, "failed", 5 * DAY)
    _ended(w, "stopped", 1 * DAY)
    _ended(w, "abandoned", 3 * DAY)
    _ended(w, "released", 4 * DAY)
    # The window before: one completed and one failed.
    _ended(w, "completed", 16 * DAY)
    _ended(w, "failed", 20 * DAY)
    # Long ago, and open now: neither counts.
    _ended(w, "completed", 40 * DAY)
    _run(w, _task(w), "in_progress", started=60, last_agent=0)


class TestOutcomes:
    async def test_o1_each_run_counts_by_its_outcome_on_the_day_it_ended(self, w):
        _seed_outcomes(w)
        sec = await _section(w, w.a)
        assert sec["on"] is True and sec["agent_runs_seen"] is True
        assert sec["outcomes"] == {
            "completed": 3, "failed": 1, "stopped": 1, "abandoned": 1, "released": 1,
            "server_closed": 0,
        }
        assert sec["ended_total"] == 7
        assert sec["outcomes_before"] == {
            "completed": 1, "failed": 1, "stopped": 0, "abandoned": 0, "released": 0,
            "server_closed": 0,
        }
        series = sec["series"]
        assert len(series) == 14 == sec["days"]
        assert series[0]["day"] == sec["window_start"]
        assert series[-1]["day"] == _day_of(w, 0)
        # The days add up to the outcomes of the window.
        for outcome, n in sec["outcomes"].items():
            assert sum(d[outcome] for d in series) == n
        assert sum(d["total"] for d in series) == 7
        by_day = {d["day"]: d for d in series}
        assert by_day[_day_of(w, 5 * DAY)]["failed"] == 1
        assert by_day[_day_of(w, 1 * DAY)]["stopped"] == 1

    async def test_r1_the_success_rate_leaves_out_a_stop_and_a_release(self, w):
        _seed_outcomes(w)
        sec = await _section(w, w.a)
        success = sec["success"]
        # 3 completed over 3 completed, 1 failed and 1 abandoned.
        assert (success["completed"], success["rated"]) == (3, 5)
        assert success["rate"] == 0.6
        assert (success["rate_before"], success["rated_before"]) == (0.5, 2)
        assert success["trend"] == "up"
        assert sec["summary"] == (
            "7 agent runs ended in the last 14 days."
            " The success rate is 60%, up from 50% in the 14 days before."
        )


# ── S1 ───────────────────────────────────────────────────────────────────────


class TestSilent:
    async def test_s1_silent_runs_now_the_longest_silence_first(self, w):
        from acb_auth import agent_oauth

        quiet = _task(w)
        _run(w, quiet, "in_progress", started=60, last_activity=0, last_agent=20)
        mute = _task(w)
        _run(w, mute, "in_progress", email=PERSON, started=30, last_activity=30,
             last_agent=None)
        working = _task(w)
        _run(w, working, "in_progress", started=60, last_activity=0, last_agent=0)
        # The card pill reads this one idle (10 minutes). It is not silent yet.
        idle = _task(w)
        _run(w, idle, "in_progress", started=60, last_activity=10, last_agent=10)
        asking = _task(w)
        _run(w, asking, "awaiting", started=60, last_activity=60, last_agent=60)
        done = _task(w, "done")
        _run(w, done, "in_progress", started=60, last_activity=60, last_agent=60)

        body = await _preview(w, w.a, kpis=True)
        sec = body["sections"]["agent_runs"]
        assert sec["silent_total"] == 2
        assert [r["id"] for r in sec["silent"]] == [mute, quiet]
        assert sec["silent"][0]["age_hours"] == pytest.approx(0.5, abs=0.02)
        assert sec["silent"][1]["age_hours"] == pytest.approx(20 / 60, abs=0.02)
        # §6.8 E2: each row names the agent and the member it works for.
        assert sec["silent"][0]["agent"] == agent_oauth.client_label(b.VIA)
        assert sec["silent"][0]["member"] == PERSON
        assert sec["silent"][1]["member"] == A_EMAIL
        assert all(r["run_id"] for r in sec["silent"])
        # One rule: the KPI tile counts the same silent runs.
        assert body["kpis"]["runs"]["silent"] == sec["silent_total"]
        assert sec["silent_after_minutes"] == 15
        assert sec["summary"] == (
            "No agent run ended in the last 14 days. 2 runs are silent now."
        )


# ── Z1 and T1 ────────────────────────────────────────────────────────────────


class TestSizes:
    async def test_z1_completed_runs_by_agent_time_and_the_long_flag(self, w):
        from gateway.routes.projects import analytics_agent_runs as ar

        def completed(started: int, last_agent: int | None, ended: int) -> tuple[str, str]:
            tid = _task(w, "done")
            return tid, _run(w, tid, "completed", started=started, ended=ended,
                             last_agent=last_agent)

        completed(300, 290, 290)  # 10 minutes
        completed(300, 270, 270)  # 30 minutes
        completed(300, 180, 180)  # 2 hours
        five, _ = completed(600, 300, 300)  # 5 hours
        eight, _ = completed(1000, 520, 520)  # 8 hours
        # Closed by the server three days after ten minutes of work: the
        # agent time ends at the agent's last act (RA-2's run end).
        completed(3 * DAY + 10, 3 * DAY, 0)
        # Five hours, with a wait of two hours on a person: three hours.
        waits, rid = completed(400, 100, 100)
        q = _run_act(w, rid, waits, "elicitation", minutes_ago=350)
        _run_act(w, rid, waits, "prompt", minutes_ago=230, reply_to=q)
        _run_act(w, rid, waits, "thought", minutes_ago=225)
        # Completed with no start: no agent time, so no size.
        completed_none = _task(w, "done")
        _run(w, completed_none, "completed", started=None, created=200, ended=100,
             last_agent=100)
        # Not completed, or not in the window: no size.
        _run(w, _task(w, "done"), "abandoned", started=1000, ended=0, last_agent=100)
        _ended(w, "completed", 20 * DAY)

        sec = await _section(w, w.a)
        assert sec["sizes"] == {
            "under_15m": 2, "under_1h": 1, "under_4h": 2, "over_4h": 2, "unmeasured": 1,
        }
        assert sec["sizes"] == ar.size_counts(
            [10 / 60, 30 / 60, 2.0, 5.0, 8.0, 10 / 60, 3.0, None]
        )
        assert sec["sized_total"] == 8 == sec["outcomes"]["completed"]
        assert sec["long_total"] == 2
        assert [r["id"] for r in sec["long_runs"]] == [eight, five]
        assert sec["long_runs"][0]["hours"] == pytest.approx(8.0, abs=0.02)
        assert sec["long_runs"][1]["hours"] == pytest.approx(5.0, abs=0.02)
        assert sec["long_runs"][0]["member"] == A_EMAIL
        assert sec["long_line"] == (
            "2 completed runs took 4 hours or more of agent time:"
            " maybe a poorly specified task."
        )
        assert sec["summary"].endswith(sec["long_line"])

    async def test_t1_the_size_rule_is_one_rule_in_python_and_in_sql(self, w):
        from gateway.routes.projects import analytics_agent_runs as ar

        values = [None, 0.0, 0.2499, 0.25, 0.999, 1.0, 3.999, 4.0, 30.0]
        rows = ", ".join(
            "(CAST(NULL AS numeric))" if v is None else f"(CAST({v} AS numeric))"
            for v in values
        )
        with w.p.admin_engine.begin() as c:
            got = [r[0] for r in c.execute(text(
                f"SELECT {ar.size_bucket_sql('v.h')} FROM (VALUES {rows}) v(h)"
            ))]
        assert got == [ar.size_bucket(v) for v in values]


# ── V1, H1 and F1 ────────────────────────────────────────────────────────────


class TestVisibility:
    async def test_v1_a_member_reads_only_what_they_see(self, w):
        """M is no admin and holds no grant, so M sees the tasks M holds and
        nothing else. B is in the other organization and sees nothing."""
        mine_done = _ended(w, "completed", 2 * DAY, assignee=M_EMAIL)
        mine_open = _task(w, assignee=M_EMAIL)
        _run(w, mine_open, "in_progress", started=60, last_activity=0, last_agent=30)
        _ended(w, "failed", 1 * DAY)
        _ended(w, "completed", 3 * DAY)
        _run(w, _task(w), "in_progress", started=60, last_activity=0, last_agent=40)
        assert mine_done

        mine = await _section(w, w.m, project=False)
        assert mine["outcomes"] == {
            "completed": 1, "failed": 0, "stopped": 0, "abandoned": 0, "released": 0,
            "server_closed": 0,
        }
        assert mine["success"]["rate"] == 1.0
        assert mine["silent_total"] == 1
        assert [r["id"] for r in mine["silent"]] == [mine_open]
        assert mine["agent_runs_seen"] is True

        other = await _section(w, w.b, project=False)
        assert other["ended_total"] == 0 and other["silent_total"] == 0
        assert other["silent"] == [] and other["long_runs"] == []
        assert other["sized_total"] == 0
        assert sum(d["total"] for d in other["series"]) == 0
        assert other["agent_runs_seen"] is False

    async def test_h1_a_scope_with_no_agent_run_says_so(self, w):
        """The done-when, the server half: the Overview hides the panel when
        `agent_runs_seen` is false and nothing is in it."""
        _ended(w, "completed", 2 * DAY)  # in another project
        empty = b.project(w.p, w.p.org_a, A_EMAIL)
        sec = await _section(w, w.a, root=empty)
        assert sec["agent_runs_seen"] is False
        assert (sec["ended_total"], sec["silent_total"], sec["sized_total"]) == (0, 0, 0)
        assert sec["summary"] == "No agent run ended in the last 14 days."
        # The project with the run still shows its panel.
        assert (await _section(w, w.a))["agent_runs_seen"] is True

    async def test_f1_the_runs_flag_off_reads_no_run(self, w, monkeypatch):
        from acb_common import get_settings

        _seed_outcomes(w)
        _run(w, _task(w), "in_progress", started=60, last_activity=0, last_agent=30)
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        sec = await _section(w, w.a)
        assert sec["on"] is False
        assert sec["ended_total"] == 0 and sec["silent_total"] == 0
        assert sec["sized_total"] == 0 and sec["series"] == []
        assert sec["summary"] == (
            "Agent runs are off for this organization, so no run is counted."
        )


# ── RA-4b: a reopened run's idle gap, and a server close ─────────────────────


def _iso_ago(w: Any, minutes_ago: int) -> str:
    with w.p.admin_engine.begin() as c:
        return c.execute(text(
            "SELECT (now() - make_interval(mins => :m))::text"), {"m": minutes_ago}).scalar()


def _reopen_prompt(w: Any, rid: str, tid: str, *, minutes_ago: int, ended_ago: int,
                   reply_to: str | None = None) -> str:
    """The prompt that REOPENS a run: it carries the end it reopened."""
    return _run_act(w, rid, tid, "prompt", minutes_ago=minutes_ago, reply_to=reply_to,
                    extra={"reopens": {"ended_at": _iso_ago(w, ended_ago)}})


def _reopened(w: Any) -> tuple[str, str]:
    """The run of the brief: start 09:00, complete 09:20, a member prompt
    reopens it at 10:00, complete 10:15. Times are minutes before now, with
    the last finish ten minutes ago, so the run ended inside the window."""
    tid = _task(w, "done")
    rid = _run(w, tid, "completed", started=85, ended=10, last_agent=10)
    _run_act(w, rid, tid, "thought", minutes_ago=80)
    _run_act(w, rid, tid, "response", minutes_ago=65)  # complete at 09:20
    _reopen_prompt(w, rid, tid, minutes_ago=25, ended_ago=65)  # reopened at 10:00
    _run_act(w, rid, tid, "response", minutes_ago=10)  # complete again at 10:15
    return tid, rid


class TestReopenedRunGap:
    async def test_g4_a_steering_prompt_mid_run_makes_no_gap(self, w):
        """The ephemeral cap deleted the rows between a response and a steering
        prompt. The prompt has no marker, so 60 minutes of work stay in."""
        tid = _task(w, "done")
        rid = _run(w, tid, "completed", started=130, ended=10, last_agent=10)
        _run_act(w, rid, tid, "response", minutes_ago=100)
        _run_act(w, rid, tid, "prompt", minutes_ago=40)  # steering: no marker
        _run_act(w, rid, tid, "response", minutes_ago=10)
        sec = await _section(w, w.a)
        assert sec["sizes"]["under_4h"] == 1 and sec["sizes"]["under_1h"] == 0

    async def test_g5_a_gap_never_overlaps_the_wait_that_the_prompt_answers(self, w):
        """Asked at -100, the run ended at -90, and the prompt at -30 answers
        the question: the wait covers the gap. 110 - 70 = 40 minutes."""
        tid = _task(w, "done")
        rid = _run(w, tid, "completed", started=120, ended=10, last_agent=10)
        q = _run_act(w, rid, tid, "elicitation", minutes_ago=100)
        _run_act(w, rid, tid, "response", minutes_ago=90,
                 extra={"system": True, "server_close": "done"})
        _reopen_prompt(w, rid, tid, minutes_ago=30, ended_ago=90, reply_to=q)
        _run_act(w, rid, tid, "response", minutes_ago=10)
        with w.p.admin_engine.begin() as c:
            from gateway.routes.projects import analytics_agents as ag

            secs = c.execute(text(
                f"SELECT {ag.RUN_AGENT_SECONDS_SQL} FROM pm_agent_runs r"
                f" {ag.run_time_joins_sql()} WHERE r.id = CAST(:r AS uuid)"),
                {"r": rid}).scalar()
        assert float(secs) / 60.0 == pytest.approx(40, abs=1)

    async def test_g6_an_agent_act_that_ended_the_wait_leaves_the_gap_whole(self, w):
        """Asked at -100, the agent went on at -95, the run ended at -90, and
        the prompt at -30 answers the old question: wait 5, gap 60."""
        from gateway.routes.projects import analytics_agents as ag

        tid = _task(w, "done")
        rid = _run(w, tid, "completed", started=120, ended=10, last_agent=10)
        q = _run_act(w, rid, tid, "elicitation", minutes_ago=100)
        _run_act(w, rid, tid, "thought", minutes_ago=95)
        _run_act(w, rid, tid, "response", minutes_ago=90)
        _reopen_prompt(w, rid, tid, minutes_ago=30, ended_ago=90, reply_to=q)
        _run_act(w, rid, tid, "response", minutes_ago=10)
        with w.p.admin_engine.begin() as c:
            secs = c.execute(text(
                f"SELECT {ag.RUN_AGENT_SECONDS_SQL} FROM pm_agent_runs r"
                f" {ag.run_time_joins_sql()} WHERE r.id = CAST(:r AS uuid)"),
                {"r": rid}).scalar()
        assert float(secs) / 60.0 == pytest.approx(110 - 5 - 60, abs=1)

    async def test_g1_the_idle_gap_of_a_reopened_run_is_not_agent_time(self, w):
        """35 minutes, not 75: the 40 minutes between the first finish and the
        prompt are the member's. It is not long, and the twin agrees."""
        from gateway.routes.projects import analytics_agents as ag

        _, rid = _reopened(w)
        sec = await _section(w, w.a)
        assert sec["sizes"]["under_1h"] == 1 and sec["sizes"]["over_4h"] == 0
        assert sec["long_total"] == 0 and sec["long_runs"] == []
        with w.p.admin_engine.begin() as c:
            prompts = [(r[0], r[1], None) for r in c.execute(text(
                "SELECT created_at, CAST(content->'reopens'->>'ended_at' AS timestamptz)"
                " FROM pm_agent_run_activities WHERE run_id = CAST(:r AS uuid)"
                " AND type = 'prompt'"), {"r": rid})]
            started, ended = c.execute(text(
                "SELECT started_at, ended_at FROM pm_agent_runs"
                " WHERE id = CAST(:r AS uuid)"), {"r": rid}).one()
        hours = ag.run_hours(started, ended, ended, gaps=ag.reopen_gaps(prompts))
        assert hours == pytest.approx(35 / 60, abs=0.01)
        # The SQL text gives the same figure.
        with w.p.admin_engine.begin() as c:
            secs = c.execute(text(
                f"SELECT {ag.RUN_AGENT_SECONDS_SQL} FROM pm_agent_runs r"
                f" {ag.run_time_joins_sql()} WHERE r.id = CAST(:r AS uuid)"),
                {"r": rid}).scalar()
        assert float(secs) / 3600.0 == pytest.approx(hours, abs=0.01)

    async def test_g2_a_day_long_gap_stays_out_of_over_4h(self, w):
        """A member reopens a run a day later. Without the gap it reads 25 h."""
        tid = _task(w, "done")
        rid = _run(w, tid, "completed", started=DAY + 50, ended=10, last_agent=10)
        _run_act(w, rid, tid, "response", minutes_ago=DAY + 30)
        _reopen_prompt(w, rid, tid, minutes_ago=25, ended_ago=DAY + 30)
        _run_act(w, rid, tid, "response", minutes_ago=10)
        sec = await _section(w, w.a)
        assert sec["sizes"]["over_4h"] == 0 and sec["long_runs"] == []
        assert sec["sizes"]["under_1h"] == 1

    async def test_g3_a_run_with_no_reopen_keeps_its_whole_time(self, w):
        tid = _task(w, "done")
        rid = _run(w, tid, "completed", started=300, ended=100, last_agent=100)
        _run_act(w, rid, tid, "thought", minutes_ago=200)
        _run_act(w, rid, tid, "response", minutes_ago=100)
        sec = await _section(w, w.a)
        assert sec["sizes"]["under_4h"] == 1


def _closed(w: Any, marker: str | None, minutes_ago: int, *, late: bool = False,
            reopen: bool = False) -> str:
    """A completed run on a cancelled task that ended ``minutes_ago`` with a
    response line. ``marker`` is the ``server_close`` value of that line, or
    None for an agent finish."""
    tid = _task(w, "done")
    rid = _run(w, tid, "completed", started=minutes_ago + 20, ended=minutes_ago,
               last_agent=minutes_ago)
    _run_act(w, rid, tid, "thought", minutes_ago=minutes_ago + 5)
    extra = {"system": True, "server_close": marker} if marker else None
    _run_act(w, rid, tid, "response", minutes_ago=minutes_ago, extra=extra)
    if late:
        _run_act(w, rid, tid, "response", minutes_ago=max(0, minutes_ago - 1))
    if reopen:
        _run_act(w, rid, tid, "prompt", minutes_ago=max(0, minutes_ago - 2))
        _run_act(w, rid, tid, "response", minutes_ago=max(0, minutes_ago - 3))
    return rid


class TestServerClose:
    async def test_c1_a_sweep_close_is_its_own_outcome_and_raises_no_rate(self, w):
        _ended(w, "completed", 2 * DAY)
        _ended(w, "failed", 3 * DAY)
        base = (await _section(w, w.a))["success"]["rate"]
        assert base == 0.5
        # task_close for each of the three reasons, and archive_close.
        _closed(w, "cancelled", 1 * DAY)
        _closed(w, "done", 1 * DAY + 5)
        _closed(w, "archived", 1 * DAY + 10)
        _closed(w, "archive", 1 * DAY + 15)
        # A late summary after the close leaves it closed by Metorite.
        _closed(w, "done", 1 * DAY + 20, late=True)
        sec = await _section(w, w.a)
        assert sec["outcomes"]["server_closed"] == 5
        assert sec["outcomes"]["completed"] == 1
        assert sec["success"]["rate"] == base == 0.5
        assert (sec["success"]["completed"], sec["success"]["rated"]) == (1, 2)
        assert sum(d["server_closed"] for d in sec["series"]) == 5
        assert sec["ended_total"] == 7
        # Not an agent completion: it joins no size.
        assert sec["sized_total"] == sec["outcomes"]["completed"]
        # The window before counts the same way.
        _closed(w, "cancelled", 17 * DAY)
        again = await _section(w, w.a)
        assert again["outcomes_before"]["server_closed"] == 1
        assert again["success"]["rated_before"] == 0

    async def test_c2_a_reopened_close_is_the_agents_own_end(self, w):
        _closed(w, "cancelled", 1 * DAY, reopen=True)
        sec = await _section(w, w.a)
        assert sec["outcomes"]["server_closed"] == 0
        assert sec["outcomes"]["completed"] == 1
        assert sec["success"]["rate"] == 1.0

    async def test_c3_the_python_twin_agrees_with_the_sql(self, w):
        from gateway.routes.projects import analytics_agent_runs as ar

        cases = {
            _closed(w, "done", 1 * DAY): True,
            _closed(w, None, 1 * DAY + 30): False,
            _closed(w, "done", 1 * DAY + 60, late=True): True,
            _closed(w, "done", 1 * DAY + 90, reopen=True): False,
        }
        with w.p.admin_engine.begin() as c:
            for rid, want in cases.items():
                sql = c.execute(text(
                    f"SELECT {ar.SERVER_CLOSED_SQL} FROM pm_agent_runs r"
                    " WHERE r.id = CAST(:r AS uuid)"), {"r": rid}).scalar()
                acts = [(r[0], r[1], r[2]) for r in c.execute(text(
                    "SELECT seq, type, content->>'server_close' IS NOT NULL"
                    " FROM pm_agent_run_activities WHERE run_id = CAST(:r AS uuid)"
                    " ORDER BY seq"), {"r": rid})]
                assert sql is want
                assert ar.server_closed(acts, "completed") is want
