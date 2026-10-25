"""WS-27bn RA-2 — the KPI strip and "Who did the work", the pure rules.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B, D, E and F, and §8
RA-2.

This file pins the Python rules of ``analytics_agents.py`` and the two keys
that RA-2 adds to ``analytics_waiting.waiting_body``. The SQL of each rule
is pinned on a real database in ``test_projects_report_kpis_r8.py``.

One test for each tile rule:

* finished this week -> :func:`actor_week` and the groups;
* median cycle time -> :func:`actor_cycle` and :func:`cycle_summary`;
* waiting on a person -> :func:`oldest_wait` and the body keys;
* agents working now -> :func:`is_silent_run` and the ONE constant;
* agent PR merge rate -> :func:`merge_rate` and :func:`merge_trend`;
* cost of each finished task -> :func:`cost_figure` and :func:`run_time`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from gateway.routes.projects import analytics as ana
from gateway.routes.projects import analytics_agents as ag
from gateway.routes.projects import analytics_waiting as wt
from gateway.routes.projects import filters

_PROJECTS = Path(ana.__file__).resolve().parent
_NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


# ── The two groups ───────────────────────────────────────────────────────────


def test_agent_assisted_counts_as_agents() -> None:
    """The RA-2 decision: an agent task is a task an agent worked."""
    assert ag.actor_group(ana.FINISH_AGENT) == ag.GROUP_AGENTS
    assert ag.actor_group(ana.FINISH_ASSISTED) == ag.GROUP_AGENTS
    assert ag.actor_group(ana.FINISH_PERSON) == ag.GROUP_PEOPLE
    assert ag.actor_group(None) == ag.GROUP_PEOPLE


def test_the_sql_twin_of_the_group_names_the_same_labels() -> None:
    sql = ag.actor_group_sql("l.label")
    for label in ag.AGENT_GROUP_LABELS:
        assert f"'{label}'" in sql
    assert f"'{ana.FINISH_PERSON}'" not in sql
    assert f"'{ag.GROUP_AGENTS}'" in sql and f"'{ag.GROUP_PEOPLE}'" in sql


# ── Tile 1: finished this week, and the weekly series ────────────────────────


def _week(**counts: int) -> SimpleNamespace:
    return SimpleNamespace(week=datetime(2026, 10, 19), **counts)


def test_a_week_carries_each_label_the_groups_and_the_total() -> None:
    row = _week(agent_finished=2, agent_assisted=1, person=4)
    assert ag.actor_week(row) == {
        "week_start": "2026-10-19",
        "agent_finished": 2, "agent_assisted": 1, "person": 4,
        "agents": 3, "people": 4, "completed": 7,
    }


def test_a_quiet_week_is_zeros_and_not_absent() -> None:
    row = _week(agent_finished=None, agent_assisted=0, person=None)
    week = ag.actor_week(row)
    assert week["completed"] == 0 and week["agents"] == 0 and week["people"] == 0


def test_the_weekly_series_reads_the_labels_of_the_window() -> None:
    """§7 rule 1 and one window: the series sits on RA-1's labels, which sit
    on Throughput's `cyc`. The spine ends where the window ends."""
    sql = ag.actor_weekly_sql("SCOPE")
    assert ana.finish_labels_sql("SCOPE") in sql
    assert ana.last_week_sql(False) in sql
    skip = ag.actor_weekly_sql("SCOPE", skip_current_week=True)
    assert ana.finish_labels_sql("SCOPE", skip_current_week=True) in skip
    assert ana.last_week_sql(True) in skip
    for label in ana.FINISH_LABELS:
        assert f"'{label}'" in sql


# ── Tile 2: median cycle time, by group ──────────────────────────────────────


def test_each_group_has_its_median_and_no_row_is_no_median() -> None:
    rows = [SimpleNamespace(grp="agents", completed=5, assisted=2, measured=4,
                            median_hours=3.333, p90_hours=10.0)]
    cycle = ag.actor_cycle(rows)
    assert cycle["agents"] == {"completed": 5, "assisted": 2, "measured": 4,
                               "median_hours": 3.33, "p90_hours": 10.0}
    # ⚠️ None, never a zero median: a zero says "instant".
    assert cycle["people"] == {"completed": 0, "assisted": 0, "measured": 0,
                               "median_hours": None, "p90_hours": None}


def test_the_cycle_split_reads_the_cycle_hours_of_the_labels() -> None:
    sql = ag.actor_cycle_sql("SCOPE")
    assert ana.finish_labels_sql("SCOPE") in sql
    assert "ORDER BY l.hours" in sql
    assert "c.hours AS hours" in ana.finish_labels_sql("SCOPE")


@pytest.mark.parametrize(("people", "agents", "want"), [
    (None, None, "No finished task of this period has a measured cycle time."),
    (48.0, None, "Tasks of people took 2 days at the median."
                 " No agent task of this period has a measured cycle time."),
    (None, 5.0, "Agent tasks took 5 hours at the median."
                " No task of people alone has a measured cycle time."),
    (48.0, 5.0, "At the median, agent tasks took 5 hours and tasks of people took 2 days."),
])
def test_the_cycle_sentence_says_both_medians(people, agents, want) -> None:
    cycle = {"people": {"median_hours": people}, "agents": {"median_hours": agents}}
    assert ag.cycle_summary(cycle) == want


# ── "Who did the work": the sentence and the members ─────────────────────────


@pytest.mark.parametrize(("split", "want"), [
    ({}, "No task finished in this period."),
    ({"person": 3}, "3 tasks finished in this period. People did all of them,"
                    " with no agent work."),
    ({"person": 1}, "1 task finished in this period. People did it, with no agent work."),
    ({"agent_finished": 2}, "2 tasks finished in this period. Agents worked on all of them."),
    ({"agent_finished": 2, "agent_assisted": 1, "person": 4},
     "7 tasks finished in this period. Agents worked on 3, and people did 4."
     " A person finished 1 of the agent tasks."),
    ({"agent_assisted": 2, "person": 1},
     "3 tasks finished in this period. Agents worked on 2, and people did 1."
     " A person finished 2 of the agent tasks."),
])
def test_the_finished_sentence(split, want) -> None:
    assert ag.finished_summary(split) == want


def _member_row(label, *, run_via=None, run_member=None, run_name=None,
                act_by=None, act_via=None, act_name=None) -> SimpleNamespace:
    return SimpleNamespace(label=label, run_via=run_via, run_member=run_member,
                           run_member_name=run_name, act_by=act_by, act_via=act_via,
                           act_member_name=act_name)


def test_each_agent_rolls_up_under_its_member_with_no_rank() -> None:
    """§6.8 E2 and E1: the member names each agent, and the rows sort by
    name, never by count."""
    rows = [
        _member_row("agent_finished", run_via="mcp:claude.ai", run_member="Zed@x.test",
                    run_name="Zed"),
        _member_row("agent_finished", run_via="mcp:claude.ai", run_member="zed@x.test",
                    run_name="Zed"),
        _member_row("agent_assisted", run_via="mcp:claude.ai", run_member="zed@x.test",
                    run_name="Zed"),
        _member_row("agent_assisted", act_by="ana@x.test", act_via="mcp:claude.ai",
                    act_name="Ana"),
        _member_row("agent_finished", act_by="agent:coder"),
    ]
    out = ag.agent_members(rows)
    assert [m["member"] for m in out] == ["ana@x.test", "zed@x.test", None]
    zed = out[1]
    assert zed["total"] == 3 and zed["member_name"] == "Zed"
    assert zed["agents"][0]["agent_finished"] == 2
    assert zed["agents"][0]["agent_assisted"] == 1
    assert out[2]["agents"][0]["agent"] == "coder"
    assert out[2]["member_name"] is None


def test_the_member_sql_reads_runs_and_agent_acts_before_the_done_move() -> None:
    sql = ag.agent_members_sql("SCOPE")
    assert "x.created_at <= l.done_at" in sql
    assert "ma.created_at <= l.done_at" in sql
    assert ana.agent_act_sql("ma.created_by", ana.activity_via_sql("ma")) in sql
    assert "fm.done_at AS done_at" in ana.finish_labels_sql("SCOPE")


# ── Tile 3: waiting on a person ──────────────────────────────────────────────


def test_the_oldest_wait_is_the_oldest_head_and_none_is_none() -> None:
    assert wt.oldest_wait(None, 5.0, 30.0) == 30.0
    assert wt.oldest_wait(2.0, None, None) == 2.0
    assert wt.oldest_wait(None, None, None) is None


def test_the_waiting_body_adds_its_own_totals_and_no_second_rule() -> None:
    src = Path(wt.__file__).read_text(encoding="utf-8")
    assert '"waiting_total": questions_total + reviews_total + handoffs_total' in src
    assert "oldest_wait(" in src


# ── Tile 4: agents working now, and the silent run ───────────────────────────


def test_a_run_is_silent_after_fifteen_minutes_with_no_agent_act() -> None:
    quiet = _NOW - timedelta(minutes=15)
    busy = _NOW - timedelta(minutes=14, seconds=59)
    assert ag.is_silent_run("in_progress", quiet, None, None, now=_NOW)
    assert not ag.is_silent_run("in_progress", busy, None, None, now=_NOW)


def test_a_run_with_no_agent_act_counts_from_its_start() -> None:
    old = _NOW - timedelta(hours=1)
    assert ag.is_silent_run("in_progress", None, old, None, now=_NOW)
    assert ag.is_silent_run("in_progress", None, None, old, now=_NOW)
    assert not ag.is_silent_run("in_progress", None, _NOW, old, now=_NOW)


@pytest.mark.parametrize("status", ["awaiting", "created", "completed", "failed", "stopping"])
def test_only_a_run_in_progress_goes_silent(status) -> None:
    old = _NOW - timedelta(days=1)
    assert not ag.is_silent_run(status, old, old, old, now=_NOW)


def test_silent_is_one_constant_with_one_owner() -> None:
    """§5 says 15 minutes. The pill's idle band stays 5 minutes, unchanged."""
    assert ag.SILENT_RUN_AFTER_S == 15 * 60
    assert filters.AGENT_RUN_STALE_AFTER_S == 300
    hits = []
    for path in _PROJECTS.glob("*.py"):
        if re.search(r"^SILENT_RUN_AFTER_S\s*=", path.read_text(encoding="utf-8"), re.M):
            hits.append(path.name)
    assert hits == ["analytics_agents.py"]


def test_the_runs_now_query_reads_the_board_rule_of_working() -> None:
    sql = ag.runs_now_sql("OPEN")
    assert filters.AGENT_RUN_STATE_SQL in sql
    assert ":working_states" in sql
    assert ag.silent_run_sql("r") in sql
    assert "WHERE OPEN" in sql


def test_the_silent_sql_reads_the_agent_clock() -> None:
    sql = ag.silent_run_sql("r")
    assert "r.last_agent_activity_at, r.started_at, r.created_at" in sql
    assert ":silent_s" in sql and "'in_progress'" in sql


# ── Tile 5: agent PR merge rate ──────────────────────────────────────────────


def test_the_merge_rate_is_merged_over_merged_and_closed() -> None:
    assert ag.merge_rate(3, 1) == 0.75
    assert ag.merge_rate(0, 2) == 0.0
    # No pull request closed: no rate, never a zero.
    assert ag.merge_rate(0, 0) is None


@pytest.mark.parametrize(("rate", "before", "want"), [
    (0.8, 0.5, "up"), (0.5, 0.8, "down"), (0.5, 0.5, "flat"),
    (None, 0.5, None), (0.5, None, None),
])
def test_the_trend_says_which_way_and_names_no_target(rate, before, want) -> None:
    assert ag.merge_trend(rate, before) == want


def test_the_merge_query_counts_agent_work_before_the_close() -> None:
    sql = ag.merge_sql("PAST")
    assert "rf.state IN ('merged', 'closed')" in sql
    assert "'open'" not in sql and "'draft'" not in sql
    assert "aw_task" not in sql and "aw_before" not in sql
    assert ":merge_days" in sql and ":merge_days_before" in sql


def test_no_text_calls_100_percent_the_goal() -> None:
    """§6.8 E4. The merge rate has no target in any text of this module."""
    src = Path(ag.__file__).read_text(encoding="utf-8").lower()
    for phrase in ("target of 100", "aim for 100", "goal of 100", "should be 100"):
        assert phrase not in src


# ── Tile 6: cost, and the agent run time ─────────────────────────────────────


def test_cost_is_unknown_and_never_zero() -> None:
    cost = ag.cost_figure(12)
    assert cost["per_task"] is None and cost["source"] is None
    assert cost["known_tasks"] == 0 and cost["share"] == 0.0
    assert cost["finished"] == 12
    # No task finished: no share at all.
    assert ag.cost_figure(0)["share"] is None


def test_the_run_time_is_the_median_over_tasks_with_a_measured_run() -> None:
    got = ag.run_time([1.0, None, 3.0, 2.0])
    assert got == {"finished": 4, "run_tasks": 3, "median_hours": 2.0, "total_hours": 6.0}
    assert ag.run_time([None, None]) == {
        "finished": 2, "run_tasks": 0, "median_hours": None, "total_hours": None,
    }


def test_the_run_time_reads_the_window_and_the_runs_before_the_finish() -> None:
    sql = ag.run_time_sql("SCOPE")
    assert ana.cycle_cte_sql("SCOPE") in sql
    assert "r.created_at <= c.finished_at" in sql
    assert "r.ended_at - r.started_at" in sql


# ── The render ───────────────────────────────────────────────────────────────


def test_only_the_preview_asks_for_the_strip() -> None:
    """A saved report, the email and the chat render no strip."""
    from gateway.routes.projects import reports

    src = Path(reports.__file__).read_text(encoding="utf-8")
    assert src.count("kpis=kpis") == 1
    assert 'payload.get("kpis") is True' in src
    assert "await kpis_body(" in src


def test_no_new_read_of_via() -> None:
    """RA-1's fence: the analytics modules read the stamp through
    `activity_via_sql` only."""
    src = Path(ag.__file__).read_text(encoding="utf-8")
    assert not re.search(r"->>\s*'via'", src)
