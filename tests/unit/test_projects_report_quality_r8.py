"""WS-27bn RA-5 — "Quality", on a real Postgres (R8).

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B, E and F, the §5
rows "Agent PR outcome", "Rework" and "CI red", and §8 RA-5.

``test_projects_report_quality.py`` pins the Python twin of each rule. This
file pins the SQL of ``analytics_quality.py`` through the real report preview
(``POST /projects/reports/preview``, the path of the Overview), as the
non-privileged role of the phase-4 rehearsal (the ``promoted`` fixture), on a
database with the whole ladder and two organizations:

* **P1** the agent PR outcomes equal the KPI tile's merge figures for the
  same fixture, to the rate and the trend. The open count is the open and
  draft agent pull requests on open tasks.
* **F1** the open agent pull requests whose checks fail: the total, up to
  five named, the oldest first, each naming its agent and its member.
* **M1** merged while red: only a failing state read BEFORE the merge counts.
* **R1** reopened: split by the group of the finish that was undone, with
  the finishes of the same group as the denominator, in the window and in
  the window before. A move to cancelled is no reopen.
* **R2** retried: a failed run and a later run on the same task. With the
  runs flag off, no run is read.
* **T1** the reopen rule is one rule in Python and in SQL.
* **V1** §7 rule 1: a member who sees two tasks reads those tasks only, and
  the other organization reads nothing.
* **H1** a scope with nothing in it says so (``quality_seen`` false), so the
  Overview hides the panel.

Run (a private base database, one R8 suite at a time)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_ra5 \\
        uv run pytest tests/unit/test_projects_report_quality_r8.py -v -rs

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

A_EMAIL = "ra5-a@quality-r8.test"
PERSON = "ra5-p@quality-r8.test"
M_EMAIL = "ra5-m@quality-r8.test"
B_EMAIL = "ra5-b@quality-r8.test"

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
    w: Any, tid: str, status: str, *, email: str = A_EMAIL, created: int = 0,
    started: int | None = None, ended: int | None = None,
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
            f"{_ago(ended if ended is not None else 0)}, "
            f"{_ago(ended if ended is not None else 0)})"),
            {"id": rid, "o": w.p.org_a, "t": tid, "e": email, "via": b.VIA, "s": status})
    return rid


def _agent_task(w: Any, category: str = "todo", **kw: Any) -> str:
    """A task that an agent worked long ago: one finished run of 40 days ago."""
    tid = _task(w, category, **kw)
    _run(w, tid, "completed", created=40 * DAY, started=40 * DAY, ended=40 * DAY - 10)
    return tid


def _pr(
    w: Any, tid: str, number: int, state: str, *, checks: str | None = None,
    merged: int | None = None, closed: int | None = None, fetched: int | None = None,
    linked: int | None = None,
) -> str:
    """One pull request. *merged* and *closed* are minutes ago. *fetched* is
    the time of the last checks read, *linked* when the task linked it."""
    rid = b.ref(
        w.p, w.p.org_a, tid, number, state, checks=checks,
        updated=_ago(closed) if closed is not None else "now()",
        merged=_ago(merged) if merged is not None else None,
    )
    sets, binds = [], {"id": rid}
    if fetched is not None:
        sets.append(f"checks_fetched_at = {_ago(fetched)}")
    if linked is not None:
        sets.append(f"created_at = {_ago(linked)}")
    if sets:
        with w.p.admin_engine.begin() as c:
            c.execute(text(f"UPDATE pm_task_refs SET {', '.join(sets)} "
                           "WHERE id = CAST(:id AS uuid)"), binds)
    return rid


def _move(w: Any, tid: str, by: str, frm: str, to: str, minutes_ago: int, *,
          via: str | None = None) -> None:
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


async def _preview(
    w: Any, user: Any, *, project: bool = True, kpis: bool = False, root: Any = None,
) -> dict[str, Any]:
    """The body of a preview of the `quality` section."""
    body: dict[str, Any] = {
        "config": {"sections": ["quality"], "weeks": 1, "skip_current_week": True},
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
    return (await _preview(w, user, **kw))["sections"]["quality"]


# ── P1: the agent PR outcomes ────────────────────────────────────────────────


def _seed_prs(w: Any) -> None:
    """Agent pull requests in the window and before it, and the ones that
    must stay out."""
    n = iter(range(1, 100))
    for _ in range(3):  # merged in the window
        _pr(w, _agent_task(w), next(n), "merged", merged=2 * DAY)
    _pr(w, _agent_task(w), next(n), "closed", closed=1 * DAY)  # closed in the window
    for _ in range(2):  # merged before the window
        _pr(w, _agent_task(w), next(n), "merged", merged=20 * DAY)
    for _ in range(2):  # closed before the window
        _pr(w, _agent_task(w), next(n), "closed", closed=21 * DAY)
    # No agent worked this task: it is no agent pull request.
    _pr(w, _task(w), next(n), "merged", merged=1 * DAY)
    # Ancient: in neither window.
    _pr(w, _agent_task(w), next(n), "closed", closed=60 * DAY)
    # Open and draft, on open tasks of an agent: open now.
    _pr(w, _agent_task(w), next(n), "open")
    _pr(w, _agent_task(w), next(n), "draft")
    # Open, but the task is done: not open work.
    _pr(w, _agent_task(w, "done"), next(n), "open")
    # Open, no agent: no.
    _pr(w, _task(w), next(n), "open")


class TestOutcomes:
    async def test_p1_the_outcomes_equal_the_kpi_tile_and_the_trend_shows(self, w):
        _seed_prs(w)
        body = await _preview(w, w.a, kpis=True)
        prs = body["sections"]["quality"]["prs"]
        tile = body["kpis"]["merge"]
        for key in ("merged", "closed", "rate", "merged_before", "closed_before",
                    "rate_before", "trend"):
            assert prs[key] == tile[key], key
        assert (prs["merged"], prs["closed"]) == (3, 1)
        assert (prs["merged_before"], prs["closed_before"]) == (2, 2)
        assert prs["rate"] == 0.75 and prs["rate_before"] == 0.5
        assert prs["trend"] == "up"
        assert prs["open"] == 2
        sec = body["sections"]["quality"]
        assert sec["days"] == 14 == tile["days"]
        assert sec["summary"].startswith(
            "3 agent pull requests merged and 1 closed with no merge in the last 14 days."
            " The merge rate is 75%, up from 50% in the 14 days before."
            " 2 agent pull requests are open now."
        )
        assert "100%" not in sec["summary"]

    async def test_p1_the_rate_of_the_panel_is_the_tile_for_a_flat_fixture(self, w):
        n = iter(range(1, 20))
        for _ in range(2):
            _pr(w, _agent_task(w), next(n), "merged", merged=3 * DAY)
            _pr(w, _agent_task(w), next(n), "merged", merged=19 * DAY)
        body = await _preview(w, w.a, kpis=True)
        prs = body["sections"]["quality"]["prs"]
        assert prs["rate"] == body["kpis"]["merge"]["rate"] == 1.0
        assert prs["trend"] == body["kpis"]["merge"]["trend"] == "flat"
        # The text names the trend and never the goal.
        summary = body["sections"]["quality"]["summary"]
        assert "The merge rate is 100%, the same as in the 14 days before." in summary
        for word in ("goal", "target", "aim"):
            assert word not in summary.lower()


# ── F1: CI red on open agent pull requests ───────────────────────────────────


class TestFailing:
    async def test_f1_open_agent_prs_with_failing_checks_oldest_first_and_capped(self, w):
        from acb_auth import agent_oauth

        order = []
        # Six failing, linked at 6, 5, 4, 3, 2 and 1 days ago (oldest = 6).
        for i, days in enumerate((3, 6, 1, 5, 2, 4), start=1):
            tid = _agent_task(w)
            _pr(w, tid, i, "open", checks="failing", linked=days * DAY)
            order.append((days, tid))
        # Not failing, not open, or not an agent task: none of these count.
        _pr(w, _agent_task(w), 20, "open", checks="passing")
        _pr(w, _agent_task(w), 21, "open", checks="pending")
        _pr(w, _agent_task(w), 22, "open", checks=None)
        _pr(w, _agent_task(w), 23, "merged", checks="failing", merged=1 * DAY)
        _pr(w, _agent_task(w), 24, "closed", checks="failing", closed=1 * DAY)
        _pr(w, _task(w), 25, "open", checks="failing")
        _pr(w, _agent_task(w, "done"), 26, "open", checks="failing")
        draft = _agent_task(w)
        _pr(w, draft, 27, "draft", checks="failing", linked=7 * DAY)

        sec = await _section(w, w.a)
        checks = sec["checks"]
        assert checks["failing_total"] == 7
        assert sec["prs"]["open"] == 10
        named = checks["failing"]
        assert len(named) == 5 == sec["max_rows"]
        # The oldest first: the draft (7 days), then 6, 5, 4, 3 days.
        assert [r["id"] for r in named] == [
            draft, *[tid for days, tid in sorted(order, reverse=True)[:4]]
        ]
        assert named[0]["pr_state"] == "draft"
        # §6.8 E2: each row names the agent and the member it works for.
        assert named[0]["agent"] == agent_oauth.client_label(b.VIA)
        assert named[0]["member"] == A_EMAIL
        assert named[0]["ref"] == "acme/widgets#27"
        assert named[0]["age_hours"] == pytest.approx(7 * 24, abs=0.1)
        assert "10 agent pull requests are open now, and 7 fail their checks." in sec["summary"]


# ── M1: merged while red ─────────────────────────────────────────────────────


class TestMergedRed:
    async def test_m1_only_a_failing_state_read_before_the_merge_counts(self, w):
        n = iter(range(1, 50))
        # Failing, read an hour before the merge two days ago: red.
        _pr(w, _agent_task(w), next(n), "merged", checks="failing",
            merged=2 * DAY, fetched=2 * DAY + 60)
        # Failing, read a minute before the merge: red. (The two times come
        # from two statements, so "at the same instant" is the Python twin's.)
        _pr(w, _agent_task(w), next(n), "merged", checks="failing",
            merged=3 * DAY, fetched=3 * DAY + 1)
        # Failing, but read an hour AFTER the merge: a late check may have
        # rewritten the state, so it does not count.
        _pr(w, _agent_task(w), next(n), "merged", checks="failing",
            merged=2 * DAY, fetched=2 * DAY - 60)
        # No read time at all.
        _pr(w, _agent_task(w), next(n), "merged", checks="failing", merged=2 * DAY)
        # Passing, pending, none: not red.
        for state in ("passing", "pending", "none"):
            _pr(w, _agent_task(w), next(n), "merged", checks=state,
                merged=2 * DAY, fetched=2 * DAY + 60)
        # No agent worked the task.
        _pr(w, _task(w), next(n), "merged", checks="failing",
            merged=2 * DAY, fetched=2 * DAY + 60)
        # Closed with no merge is not a merge.
        _pr(w, _agent_task(w), next(n), "closed", checks="failing",
            closed=2 * DAY, fetched=2 * DAY + 60)
        # Red, in the window before.
        _pr(w, _agent_task(w), next(n), "merged", checks="failing",
            merged=20 * DAY, fetched=20 * DAY + 60)
        # Red, but older than both windows.
        _pr(w, _agent_task(w), next(n), "merged", checks="failing",
            merged=60 * DAY, fetched=60 * DAY + 60)

        sec = await _section(w, w.a)
        assert sec["checks"]["merged_red"] == 2
        assert sec["checks"]["merged_red_before"] == 1
        assert "2 agent pull requests merged while their checks failed." in sec["summary"]


# ── R1: reopened ─────────────────────────────────────────────────────────────

AGENT = "agent:coder"


def _seed_rework(w: Any) -> dict[str, str]:
    """Tasks in the window and before it. Times are in days ago."""
    t: dict[str, str] = {}
    # A: an agent finishes it (through MCP), a person reopens it.
    t["A"] = _task(w)
    _move(w, t["A"], PERSON, "todo", "done", 3 * DAY, via=b.VIA)
    _move(w, t["A"], PERSON, "done", "todo", 2 * DAY)
    # B: agent-assisted. A run began five days ago, a person finished it.
    t["B"] = _task(w)
    _run(w, t["B"], "completed", created=5 * DAY, started=5 * DAY, ended=4 * DAY)
    _move(w, t["B"], PERSON, "todo", "done", 3 * DAY)
    _move(w, t["B"], PERSON, "done", "started", 1 * DAY)
    # C: a person finishes it, and it comes back.
    t["C"] = _task(w)
    _move(w, t["C"], PERSON, "todo", "done", 3 * DAY)
    _move(w, t["C"], PERSON, "done", "todo", 1 * DAY)
    # D: a person finishes it, and it stays done.
    t["D"] = _task(w)
    _move(w, t["D"], PERSON, "todo", "done", 4 * DAY)
    # E: an agent in the app finishes it, and it stays done.
    t["E"] = _task(w)
    _move(w, t["E"], AGENT, "todo", "done", 4 * DAY)
    # F: finished, then cancelled. A relabel is no reopen.
    t["F"] = _task(w)
    _move(w, t["F"], PERSON, "todo", "done", 3 * DAY)
    _move(w, t["F"], PERSON, "done", "cancelled", 2 * DAY)
    # G: in the window before. Finished 20 days ago, reopened 18 days ago.
    t["G"] = _task(w)
    _move(w, t["G"], PERSON, "todo", "done", 20 * DAY)
    _move(w, t["G"], PERSON, "done", "todo", 18 * DAY)
    # H: finished, reopened, finished again. One reopen and two finishes.
    t["H"] = _task(w)
    _move(w, t["H"], PERSON, "todo", "done", 6 * DAY)
    _move(w, t["H"], PERSON, "done", "todo", 5 * DAY)
    _move(w, t["H"], PERSON, "todo", "done", 4 * DAY)
    # I: a move between two done lanes is no finish and no reopen.
    t["I"] = _task(w)
    _move(w, t["I"], PERSON, "done", "done", 2 * DAY)
    # J: older than both windows.
    t["J"] = _task(w)
    _move(w, t["J"], PERSON, "todo", "done", 50 * DAY)
    _move(w, t["J"], PERSON, "done", "todo", 49 * DAY)
    return t


def _assert_rates_at_most_one(sec: dict[str, Any]) -> None:
    """The cohort rate never passes 100%, in the window or the one before."""
    for group in sec["rework"]["reopened"].values():
        assert group["undone"] <= group["finished"]
        assert group["undone_before"] <= group["finished_before"]
        for key in ("rate", "rate_before"):
            assert group[key] is None or 0 <= group[key] <= 1


class TestReopened:
    async def test_r1_a_reopen_belongs_to_the_group_of_the_finish_it_undoes(self, w):
        _seed_rework(w)
        sec = await _section(w, w.a)
        agents = sec["rework"]["reopened"]["agents"]
        people = sec["rework"]["reopened"]["people"]
        # Agents finished A (through MCP), B (assisted) and E (in the app).
        # A and B came back.
        assert (agents["finished"], agents["undone"], agents["reopened"]) == (3, 2, 2)
        assert agents["rate"] == pytest.approx(0.6667, abs=1e-4)
        assert (agents["finished_before"], agents["reopened_before"]) == (0, 0)
        assert agents["rate_before"] is None and agents["trend"] is None
        # People finished C, D, F, H (twice) and the reopen of C and H
        # counts. F went to cancelled, so it is no reopen.
        assert (people["finished"], people["undone"], people["reopened"]) == (5, 2, 2)
        assert people["rate"] == 0.4
        # The window before: G finished and came back.
        assert (people["finished_before"], people["undone_before"]) == (1, 1)
        assert people["reopened_before"] == 1
        assert people["rate_before"] == 1.0 and people["trend"] == "down"
        _assert_rates_at_most_one(sec)

    async def test_r1_the_summary_compares_the_two_groups_in_one_sentence_each(self, w):
        _seed_rework(w)
        summary = (await _section(w, w.a))["summary"]
        assert "Of the tasks that agents finished, 2 of 3 were reopened later." in summary
        assert "Of the tasks that people finished, 2 of 5 were reopened later." in summary
        assert (
            "4 tasks were reopened after done in the last 14 days:"
            " 2 after an agent finish and 2 after a person finish." in summary
        )

    async def test_r1_a_reopen_of_an_old_finish_is_a_task_and_not_part_of_the_rate(self, w):
        """Three tasks finished 20 days ago and reopened a day ago, and one
        finish in the window that nobody reopened. The old way read 3 of 1."""
        for _ in range(3):
            t = _task(w)
            _move(w, t, PERSON, "todo", "done", 20 * DAY)
            _move(w, t, PERSON, "done", "todo", 1 * DAY)
        _move(w, _task(w), PERSON, "todo", "done", 2 * DAY)
        sec = await _section(w, w.a)
        people = sec["rework"]["reopened"]["people"]
        assert people["reopened"] == 3
        assert (people["finished"], people["undone"]) == (1, 0)
        assert people["rate"] == 0.0
        # The window before holds the three old finishes, and a reopen undid each.
        assert (people["finished_before"], people["undone_before"]) == (3, 3)
        assert people["rate_before"] == 1.0 and people["trend"] == "down"
        _assert_rates_at_most_one(sec)

    async def test_r1_a_task_finished_twice_and_reopened_once_gives_one_of_two(self, w):
        t = _task(w)
        _move(w, t, PERSON, "todo", "done", 10 * DAY)
        _move(w, t, PERSON, "done", "todo", 9 * DAY)
        _move(w, t, PERSON, "todo", "done", 8 * DAY)
        sec = await _section(w, w.a)
        people = sec["rework"]["reopened"]["people"]
        assert (people["finished"], people["undone"], people["reopened"]) == (2, 1, 1)
        assert people["rate"] == 0.5
        _assert_rates_at_most_one(sec)

    async def test_r1_a_task_reopened_twice_counts_once_in_the_group_of_the_latest(self, w):
        t = _task(w)
        _move(w, t, PERSON, "todo", "done", 10 * DAY)
        _move(w, t, PERSON, "done", "todo", 9 * DAY)
        _move(w, t, AGENT, "todo", "done", 8 * DAY, via=b.VIA)
        _move(w, t, PERSON, "done", "todo", 7 * DAY)
        sec = await _section(w, w.a)
        reopened = sec["rework"]["reopened"]
        # Two reopen moves, one task. The latest undid the agent finish.
        assert (reopened["agents"]["reopened"], reopened["people"]["reopened"]) == (1, 0)
        assert (reopened["agents"]["finished"], reopened["agents"]["undone"]) == (1, 1)
        assert (reopened["people"]["finished"], reopened["people"]["undone"]) == (1, 1)
        _assert_rates_at_most_one(sec)

    async def test_r1_no_reopen_says_so(self, w):
        t = _task(w)
        _move(w, t, PERSON, "todo", "done", 2 * DAY)
        sec = await _section(w, w.a)
        assert sec["rework"]["reopened"]["people"]["reopened"] == 0
        assert sec["rework"]["reopened"]["people"]["undone"] == 0
        assert "No finished task was reopened in the last 14 days." in sec["summary"]

    async def test_t1_the_reopen_rule_is_one_rule_in_python_and_in_sql(self, w):
        from gateway.routes.projects import analytics_quality as aq
        from gateway.routes.projects.core import CLOSING_CATEGORIES, COMPLETED_CATEGORY

        cats = ["backlog", "todo", "started", "done", "cancelled", None]
        pairs = [(f, t) for f in cats for t in cats]
        rows = ", ".join(
            f"(CAST({'NULL' if f is None else repr(f)} AS text),"
            f" CAST({'NULL' if t is None else repr(t)} AS text))"
            for f, t in pairs
        )
        binds = {"done_cat": COMPLETED_CATEGORY, "closing": sorted(CLOSING_CATEGORIES)}
        with w.p.admin_engine.begin() as c:
            for sql, twin in ((aq.REOPEN_MOVE_SQL, aq.is_reopen_move),
                              (aq.FINISH_MOVE_SQL, aq.is_finish_move)):
                got = [bool(r[0]) for r in c.execute(text(
                    f"SELECT ({sql}) FROM (SELECT jsonb_build_object("
                    f"'from_category', v.f, 'to_category', v.t) AS meta"
                    f" FROM (VALUES {rows}) v(f, t)) a"), binds)]
                assert got == [twin(f, t) for f, t in pairs], sql


# ── R2: retried ──────────────────────────────────────────────────────────────


class TestRetried:
    async def test_r2_a_failed_run_with_a_later_run_on_the_task_is_retried(self, w):
        # Retried: a later run was created after the failed run ended.
        a = _task(w)
        _run(w, a, "failed", created=3 * DAY + 60, started=3 * DAY + 60, ended=3 * DAY)
        _run(w, a, "completed", created=2 * DAY, started=2 * DAY, ended=1 * DAY)
        # Not retried: no other run.
        b1 = _task(w)
        _run(w, b1, "failed", created=3 * DAY + 60, started=3 * DAY + 60, ended=3 * DAY)
        # Not retried: the other run was created BEFORE the failed run ended.
        c = _task(w)
        _run(w, c, "completed", created=5 * DAY, started=5 * DAY, ended=4 * DAY)
        _run(w, c, "failed", created=3 * DAY + 60, started=3 * DAY + 60, ended=3 * DAY)
        # Not retried: a later run of ANOTHER task.
        d = _task(w)
        _run(w, d, "failed", created=3 * DAY + 60, started=3 * DAY + 60, ended=3 * DAY)
        _run(w, _task(w), "completed", created=2 * DAY, started=2 * DAY, ended=1 * DAY)
        # Before the window: failed and retried.
        e = _task(w)
        _run(w, e, "failed", created=21 * DAY + 60, started=21 * DAY + 60, ended=20 * DAY)
        _run(w, e, "completed", created=19 * DAY, started=19 * DAY, ended=18 * DAY)
        # A stop is not a failure.
        f = _task(w)
        _run(w, f, "stopped", created=3 * DAY + 60, started=3 * DAY + 60, ended=3 * DAY)
        _run(w, f, "completed", created=2 * DAY, started=2 * DAY, ended=1 * DAY)

        sec = await _section(w, w.a)
        retried = sec["rework"]["retried"]
        assert (retried["failed"], retried["retried"]) == (4, 1)
        assert (retried["failed_before"], retried["retried_before"]) == (1, 1)
        assert retried["rate"] == 0.25 and retried["rate_before"] == 1.0
        assert retried["trend"] == "down"
        assert "1 of 4 failed runs got a new run on the same task." in sec["summary"]

    async def test_r2_the_runs_flag_off_reads_no_run(self, w, monkeypatch):
        from acb_common import get_settings

        a = _task(w)
        _run(w, a, "failed", created=3 * DAY + 60, started=3 * DAY + 60, ended=3 * DAY)
        _run(w, a, "completed", created=2 * DAY, started=2 * DAY, ended=1 * DAY)
        _move(w, a, PERSON, "todo", "done", 2 * DAY)
        _move(w, a, PERSON, "done", "todo", 1 * DAY)
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        sec = await _section(w, w.a)
        assert sec["on"] is False
        assert sec["rework"]["retried"]["failed"] == 0
        assert sec["rework"]["retried"]["retried"] == 0
        # The reopened tasks do not depend on the flag. The task had an agent
        # run before its finish, so the finish is agent-assisted.
        assert sec["rework"]["reopened"]["agents"]["reopened"] == 1
        assert sec["summary"].endswith(
            "Agent runs are off for this organization, so no retry is counted."
        )


class TestRunsGate:
    async def test_r3_the_failing_list_reads_no_run_while_the_flag_is_off(self, w, monkeypatch):
        """RA-5 fix round 1 (P2-b). An old run exists. With the flag on it
        names the member. With the flag off the agent comes from the agent
        acts only, and the run is not read."""
        from acb_auth import agent_oauth
        from acb_common import get_settings

        tid = _task(w)
        _run(w, tid, "completed", created=40 * DAY, started=40 * DAY, ended=40 * DAY - 10)
        _pr(w, tid, 1, "open", checks="failing", linked=2 * DAY)

        on = (await _section(w, w.a))["checks"]["failing"][0]
        assert on["member"] == A_EMAIL and on["agent"] == agent_oauth.client_label(b.VIA)

        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        off = (await _section(w, w.a))["checks"]["failing"][0]
        # No run, and no agent act yet: the row names a plain agent.
        assert off["id"] == tid
        assert off["agent"] == "Agent" and off["member"] is None
        assert off["member_name"] is None and off["client_via"] is None
        # An agent act through MCP names the agent and its member, with no run.
        b.mcp_comment(w.p, w.p.org_a, tid, PERSON)
        named = (await _section(w, w.a))["checks"]["failing"][0]
        assert named["agent"] == agent_oauth.client_label(b.VIA)
        assert named["member"] == PERSON


# ── V1 and H1 ────────────────────────────────────────────────────────────────


class TestVisibility:
    async def test_v1_a_member_reads_only_what_they_see(self, w):
        """M is no admin and holds no grant, so M sees the tasks M holds and
        nothing else. B is in the other organization and sees nothing."""
        mine = _agent_task(w, assignee=M_EMAIL)
        _pr(w, mine, 1, "merged", merged=2 * DAY)
        mine_open = _agent_task(w, assignee=M_EMAIL)
        _pr(w, mine_open, 2, "open", checks="failing", linked=2 * DAY)
        _move(w, mine, PERSON, "todo", "done", 3 * DAY)
        _move(w, mine, PERSON, "done", "todo", 1 * DAY)
        # Tasks that M cannot open.
        _pr(w, _agent_task(w), 3, "merged", merged=2 * DAY)
        _pr(w, _agent_task(w), 4, "closed", closed=2 * DAY)
        _pr(w, _agent_task(w), 5, "open", checks="failing", linked=3 * DAY)
        hidden = _task(w)
        _move(w, hidden, PERSON, "todo", "done", 3 * DAY)
        _move(w, hidden, PERSON, "done", "todo", 1 * DAY)

        got = await _section(w, w.m, project=False)
        assert (got["prs"]["merged"], got["prs"]["closed"]) == (1, 0)
        assert got["prs"]["open"] == 1
        assert got["checks"]["failing_total"] == 1
        assert [r["id"] for r in got["checks"]["failing"]] == [mine_open]
        # An agent worked `mine` before its finish: the group is agents.
        agents = got["rework"]["reopened"]["agents"]
        assert (agents["finished"], agents["reopened"]) == (1, 1)
        assert got["rework"]["reopened"]["people"]["finished"] == 0
        assert got["quality_seen"] is True

        everyone = await _section(w, w.a, project=False)
        assert everyone["prs"]["merged"] == 2 and everyone["checks"]["failing_total"] == 2

        other = await _section(w, w.b, project=False)
        assert (other["prs"]["merged"], other["prs"]["open"]) == (0, 0)
        assert other["checks"]["failing"] == [] and other["checks"]["failing_total"] == 0
        assert other["rework"]["reopened"]["people"]["finished"] == 0
        assert other["quality_seen"] is False

    async def test_h1_a_scope_with_nothing_says_so(self, w):
        """The done-when, the server half: the Overview hides the panel when
        `quality_seen` is false and nothing is in it."""
        _pr(w, _agent_task(w), 1, "merged", merged=2 * DAY)  # in another project
        empty = b.project(w.p, w.p.org_a, A_EMAIL)
        sec = await _section(w, w.a, root=empty)
        assert sec["quality_seen"] is False
        assert sec["prs"]["merged"] == 0 and sec["checks"]["failing_total"] == 0
        assert sec["summary"] == (
            "No agent pull request merged or closed in the last 14 days."
            " No finished task was reopened in the last 14 days."
        )
        assert (await _section(w, w.a))["quality_seen"] is True

    async def test_h1_each_of_the_three_things_makes_the_scope_seen(self, w):
        for kind in ("pr", "reopen", "failed"):
            root = b.project(w.p, w.p.org_a, A_EMAIL)
            w.root = root
            tid = _task(w)
            if kind == "pr":
                _run(w, tid, "completed", created=40 * DAY, started=40 * DAY, ended=40 * DAY - 1)
                _pr(w, tid, 90, "open")
            elif kind == "reopen":
                _move(w, tid, PERSON, "done", "todo", 100 * DAY)
            else:
                _run(w, tid, "failed", created=100 * DAY, started=100 * DAY, ended=99 * DAY)
            sec = await _section(w, w.a)
            assert sec["quality_seen"] is True, kind
