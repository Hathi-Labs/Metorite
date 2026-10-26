"""Projects · the ``quality`` report section — WS-27bn RA-5.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B ("Quality"), E and F,
the §5 rows "Agent PR outcome", "Rework" and "CI red", and §8 RA-5.

The panel answers "is the speed real?" in four parts, over the last 14 days
and the 14 days before them:

1. **Agent PR outcomes.** The agent pull requests that merged and that closed
   with no merge, the merge rate and its trend, and the agent pull requests
   that are open now. The numbers are RA-2's :func:`analytics_agents.merge_sql`
   itself, so this panel and the KPI tile cannot disagree.
2. **Rework** (§6.8 F). Two kinds. A task that went back from done to an open
   status (``reopened``), split by the group of the finish that was undone, so
   a reader compares agents with people. ``reopened`` counts DISTINCT tasks.
   The reopen RATE is a cohort: the finishes of the window, and the share that
   a later reopen undid. It never passes 100%, and the newest window reads low,
   because a recent finish has had less time to be reopened. And a failed run
   followed by a later run on the same task (``retried``).
3. **CI red on agent PRs.** The open agent pull requests whose checks fail
   now, up to five named, oldest first. And the agent pull requests that
   merged while their checks failed. See :func:`merged_red_sql`.
4. **The summary** (§6.8 E5). One plain sentence that the panel, the email
   and the chat print first.

⚠️ **Each rule is a rule that already exists, joined.** An agent pull request
is RA-2's: a pull request on a task that an agent worked before the close
(`agent_worked.AGENT_WORKED_SQL`). The merge count, the close time and the
rate are `analytics_agents.merge_sql`, `PR_CLOSED_AT_SQL` and `merge_rate`.
The trend is `merge_trend`. The label of a finish is RA-1's
`analytics.finish_actor_join` with `finish_label_sql`, grouped by RA-2's
`actor_group_sql`. The agent of a row is `analytics_waiting.agent_of`. A second
copy of any of them would let this panel and the KPI strip disagree.

⚠️ **The window is the window of the merge rate.** It is the last 14 days
counted back from now, as `merge_sql` counts them, and not 14 UTC calendar
days. The merge rate of this panel must equal the tile's, and the tile counts
back from now. The rework counts use the same two bounds.

⚠️ **No target (§6.8 E4).** The merge rate and the rework rates say what
happened and which way it moved. No text says that 100% is the goal.

⚠️ **No rank of people (§6.8 E1).** Rework splits into two groups, agents and
people, and never by person.

⚠️ **§7 rule 1.** The history reads (merged, closed, rework, retried) read
Throughput's history predicate. The open pull requests read Load's open-work
predicate. The caller's tenant session runs every query. Nothing here reads a
tenant or a person from the request (R5).

⚠️ **The runs flag.** While ``agent_runs_enabled`` is off, no run moves. The
retried count reads no run, and the failing list names its agent from the
agent acts only (``failing_prs_sql(runs_on=False)``). The test of an agent
pull request is RA-2's `agent_worked_sql`, which still sees a run that
exists, as the KPI tile does. The pull requests and the reopened tasks do not
depend on the flag.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from gateway.routes.projects.agent_worked import agent_worked_sql
from gateway.routes.projects.analytics import (
    activity_via_sql,
    agent_act_sql,
    finish_actor_join,
    finish_label_sql,
    history_where,
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.analytics_agents import (
    GROUP_AGENTS,
    GROUP_PEOPLE,
    GROUPS,
    MERGE_WINDOW_DAYS,
    PR_CLOSED_AT_SQL,
    actor_group_sql,
    merge_rate,
    merge_sql,
    merge_trend,
)
from gateway.routes.projects.analytics_waiting import (
    _member_name,
    _task_row,
    agent_of,
)
from gateway.routes.projects.core import CLOSING_CATEGORIES, COMPLETED_CATEGORY
from gateway.routes.projects.filters import agent_runs_enabled, attach_parent_context
from sqlalchemy import text

# ── The vocabulary ───────────────────────────────────────────────────────────

#: The window, in days counted back from now. It is RA-2's, so the merge
#: rate of the panel is the merge rate of the tile.
QUALITY_WINDOW_DAYS = MERGE_WINDOW_DAYS

#: How many failing open pull requests the panel names, the oldest first.
FAILING_NAMED = 5

#: The ``checks_state`` of a pull request whose checks fail
#: (``infra/postgres/282_github_app.sql``, ``pm_task_refs_checks_state_known``).
CHECKS_FAILING = "failing"

#: The states of a pull request that has no outcome yet
#: (``pm_task_refs.state``, migration 255).
OPEN_PR_STATES: tuple[str, ...] = ("open", "draft")


# ── The rules, in Python ─────────────────────────────────────────────────────
#
# Each function says one rule of the SQL below in Python. The pure suite
# (`test_projects_report_quality.py`) pins them, and the R8 suite
# (`test_projects_report_quality_r8.py`) pins the SQL to the same cases.


def is_finish_move(from_category: str | None, to_category: str | None) -> bool:
    """A status move INTO done from a status that is not done. A move between
    two done lanes is a tidy-up, and it is no finish (RA-1's `done_move`)."""
    return to_category == COMPLETED_CATEGORY and from_category != COMPLETED_CATEGORY


def is_reopen_move(from_category: str | None, to_category: str | None) -> bool:
    """A status move from done to an OPEN status: not done and not cancelled.

    A move from done to cancelled is a relabel and no rework
    (`analytics.cycle_cte_sql` rules the same). A move with no category is
    not a move that anybody can read.
    """
    return (
        from_category == COMPLETED_CATEGORY
        and to_category is not None
        and to_category not in CLOSING_CATEGORIES
    )


def is_retried(failed_ended_at: Any, later_run_created_at: Iterable[Any]) -> bool:
    """A run that failed counts as retried when a LATER run of the same task
    was created after the failed run ended. A run created at the same instant
    that the failed run ended is not later."""
    if failed_ended_at is None:
        return False
    return any(at is not None and at > failed_ended_at for at in later_run_created_at)


def finish_undone(finish_at: Any, finishes: Iterable[Any], reopens: Iterable[Any]) -> bool:
    """A finish of a task is UNDONE when a reopen of the same task comes after
    it, and no other finish of the task comes between the two (the cohort rule
    of RA-5 fix round 1). The Python twin of ``fin.undone`` in
    :func:`reopened_sql`. Any time up to now counts, so a recent finish has
    had less time to be reopened."""
    others = list(finishes)
    return any(
        r > finish_at and not any(finish_at < n < r for n in others) for r in reopens
    )


def reopened_tasks(moves: Iterable[tuple[Any, Any, str]]) -> dict[str, int]:
    """The DISTINCT tasks with a reopen in the window, by group. ``moves`` is
    ``(task, reopened_at, group)`` for each reopen move of the window. A task
    takes the group of its LATEST reopen. The Python twin of ``reo`` in
    :func:`reopened_sql`."""
    latest: dict[Any, tuple[Any, str]] = {}
    for task, at, group in moves:
        if task not in latest or at > latest[task][0]:
            latest[task] = (at, group)
    counts = dict.fromkeys(GROUPS, 0)
    for _, group in latest.values():
        counts[group] += 1
    return counts


def rate(part: int, whole: int) -> float | None:
    """``part`` over ``whole``, or None with no whole. Never a zero."""
    return None if not whole else round(int(part) / int(whole), 4)


def pr_failing(state: str | None, checks_state: str | None) -> bool:
    """An open or draft pull request whose checks fail now."""
    return state in OPEN_PR_STATES and checks_state == CHECKS_FAILING


def merged_red(
    state: str | None, checks_state: str | None, checks_fetched_at: Any, merged_at: Any,
) -> bool:
    """A merged pull request whose last checks read, made BEFORE the merge,
    said failing. The Python twin of :func:`merged_red_sql`.

    ⚠️ **Why the read must come first.** The checks write has no gate on the
    state of the pull request, so a late ``check_run`` event rewrites
    ``checks_state`` after the merge (`github_sync.py` ``_CHECKS_WRITE_SQL``).
    A state that was read after the merge says nothing about the merge, so it
    does not count.
    """
    return (
        state == "merged"
        and checks_state == CHECKS_FAILING
        and checks_fetched_at is not None
        and merged_at is not None
        and checks_fetched_at <= merged_at
    )


def _percent(value: float) -> str:
    return f"{round(value * 100)}%"


def _count(n: int, word: str) -> str:
    return f"1 {word}" if n == 1 else f"{n} {word}s"


def merge_line(prs: Mapping[str, Any], days: int = QUALITY_WINDOW_DAYS) -> str | None:
    """The merge rate and its trend in words, or None with no rate.

    ⚠️ It says which way the rate moved, and never how far it is from 100%
    (§6.8 E4).
    """
    value, before, trend = prs.get("rate"), prs.get("rate_before"), prs.get("trend")
    if value is None:
        return None
    line = f"The merge rate is {_percent(value)}"
    if trend == "flat":
        return f"{line}, the same as in the {days} days before."
    if trend in ("up", "down") and before is not None:
        return f"{line}, {trend} from {_percent(before)} in the {days} days before."
    return f"{line}."


def rework_line(group: str, counts: Mapping[str, Any]) -> str:
    """One sentence of the finishes of one group that a reopen undid. The
    rate is over the finishes of the window, so it never passes 100%."""
    who = "agents" if group == GROUP_AGENTS else "people"
    undone, finished = int(counts.get("undone", 0)), int(counts.get("finished", 0))
    if finished == 0:
        return f"{who.capitalize()} finished no task in that time."
    return (
        f"Of the tasks that {who} finished, {undone} of {finished}"
        f" {'was' if undone == 1 else 'were'} reopened later."
    )


def summary_line(
    *, prs: Mapping[str, Any], red: Mapping[str, Any], rework: Mapping[str, Any],
    runs_on: bool, days: int = QUALITY_WINDOW_DAYS,
) -> str:
    """The one plain summary of the panel (§6.8 E5). The panel, the email and
    the chat print this text, so they cannot say two things."""
    merged, closed = int(prs["merged"]), int(prs["closed"])
    if merged + closed == 0:
        parts = [f"No agent pull request merged or closed in the last {days} days."]
    else:
        parts = [
            f"{_count(merged, 'agent pull request')} merged and {closed} closed with no merge"
            f" in the last {days} days."
        ]
        if line := merge_line(prs, days):
            parts.append(line)
    open_n, failing = int(prs["open"]), int(red["failing_total"])
    if open_n and failing:
        parts.append(
            f"{_count(open_n, 'agent pull request')} {'is' if open_n == 1 else 'are'} open"
            f" now, and {failing} fail{'s' if failing == 1 else ''} their checks."
        )
    elif open_n:
        parts.append(
            f"{_count(open_n, 'agent pull request')} {'is' if open_n == 1 else 'are'} open now."
        )
    if (n := int(red["merged_red"])) > 0:
        parts.append(
            f"{_count(n, 'agent pull request')} merged while"
            f" {'its' if n == 1 else 'their'} checks failed."
        )
    reopened = rework["reopened"]
    tasks = sum(int(reopened[g]["reopened"]) for g in GROUPS)
    if tasks == 0 and sum(int(reopened[g]["undone"]) for g in GROUPS) == 0:
        parts.append(f"No finished task was reopened in the last {days} days.")
    else:
        if tasks:
            a, p = int(reopened[GROUP_AGENTS]["reopened"]), int(reopened[GROUP_PEOPLE]["reopened"])
            parts.append(
                f"{_count(tasks, 'task')} {'was' if tasks == 1 else 'were'} reopened after done"
                f" in the last {days} days: {a} after an agent finish and {p} after a person finish."
            )
        parts.extend(rework_line(g, reopened[g]) for g in (GROUP_AGENTS, GROUP_PEOPLE))
    if not runs_on:
        parts.append("Agent runs are off for this organization, so no retry is counted.")
    elif (retry := rework["retried"])["failed"]:
        n_failed, n_retried = int(retry["failed"]), int(retry["retried"])
        parts.append(
            f"{n_retried} of {_count(n_failed, 'failed run')}"
            f" got a new run on the same task."
        )
    return " ".join(parts)


# ── The rules, in SQL ────────────────────────────────────────────────────────

#: The first instant of the window, and of the window before it. The bounds
#: are the bounds of RA-2's `merge_sql`. They bind ``:merge_days`` and
#: ``:merge_days_before``.
_NOW_FROM = "now() - make_interval(days => :merge_days)"
_BEFORE_FROM = "now() - make_interval(days => :merge_days_before)"


def _agent_pr_scope(past_where: str, at: str) -> str:
    """The FROM and WHERE of the pull requests of an agent's task, closed at
    ``at``: the shape that `analytics_agents.merge_sql` reads."""
    return (
        f"  FROM pm_task_refs rf"
        f"  JOIN pm_tasks t ON t.id = rf.task_id"
        f" WHERE {past_where}"
        f"   AND rf.kind = 'pull_request'"
        f"   AND {agent_worked_sql('t.id', at)}"
    )


def open_prs_sql(open_where: str) -> str:
    """The open and draft agent pull requests on the open tasks of the scope:
    the count, and how many of them fail their checks now."""
    return (
        f"SELECT count(*) AS open_total,"
        f"       count(*) FILTER (WHERE rf.checks_state = '{CHECKS_FAILING}') AS failing_total"
        f"  FROM pm_task_refs rf"
        f"  JOIN pm_tasks t ON t.id = rf.task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
        f"   AND rf.kind = 'pull_request'"
        f"   AND rf.state IN ({', '.join(repr(s) for s in OPEN_PR_STATES)})"
        f"   AND {agent_worked_sql('t.id', 'now()')}"
    )


def failing_prs_sql(open_where: str, *, runs_on: bool = True) -> str:
    """The open agent pull requests whose checks fail now, the oldest first
    (by the time that the task linked the pull request). ``total`` counts
    every one before the cap. It binds ``:failing_named``.

    ⚠️ **While ``agent_runs_enabled`` is off, no run is read here.** The newest
    run names the agent and its member only with ``runs_on``. Else the agent
    comes from the newest agent act (``ma``), and the member name is empty.
    """
    run_cols = (
        f"       nr.client_via AS run_via, nr.member_email AS run_member,"
        f"       {_member_name('nr.member_email')} AS run_member_name,"
        if runs_on
        else "       NULL AS run_via, NULL AS run_member, NULL AS run_member_name,"
    )
    run_join = (
        # The newest run names the agent and its member, as `waiting` reads it.
        "  LEFT JOIN LATERAL ("
        "    SELECT x.client_via, x.member_email FROM pm_agent_runs x"
        "     WHERE x.task_id = t.id"
        "     ORDER BY x.created_at DESC, x.id DESC LIMIT 1"
        "  ) nr ON TRUE"
        if runs_on
        else ""
    )
    return (
        f"SELECT rf.id AS ref_id, rf.ref AS pr_ref, rf.url AS pr_url,"
        f"       rf.title AS pr_title, rf.state AS pr_state,"
        f"       rf.created_at AS linked_at,"
        f"       t.id, t.title, t.task_number, t.project_id, t.parent_task_id,"
        f"       p.name AS project_name,"
        f"{run_cols}"
        f"       ma.act_by, ma.act_via,"
        f"       count(*) OVER () AS total"
        f"  FROM pm_task_refs rf"
        f"  JOIN pm_tasks t ON t.id = rf.task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_projects p ON p.id = t.project_id"
        f"{run_join}"
        # Else the newest agent act does. RA-1's `agent_act_sql` and its one
        # read of the stamp (`activity_via_sql`) decide what an agent act is.
        f"  LEFT JOIN LATERAL ("
        f"    SELECT a.created_by AS act_by, {activity_via_sql('a')} AS act_via"
        f"      FROM pm_activities a"
        f"     WHERE a.task_id = t.id AND a.deleted_at IS NULL"
        f"       AND {agent_act_sql('a.created_by', activity_via_sql('a'))}"
        f"     ORDER BY a.created_at DESC, a.id DESC LIMIT 1"
        f"  ) ma ON TRUE"
        f" WHERE {open_where}"
        f"   AND rf.kind = 'pull_request'"
        f"   AND rf.state IN ({', '.join(repr(s) for s in OPEN_PR_STATES)})"
        f"   AND rf.checks_state = '{CHECKS_FAILING}'"
        f"   AND {agent_worked_sql('t.id', 'now()')}"
        f" ORDER BY rf.created_at ASC, rf.id ASC"
        f" LIMIT :failing_named"
    )


def merged_red_sql(past_where: str) -> str:
    """The agent pull requests that merged in the window, and in the window
    before it, whose last checks read BEFORE the merge said failing.

    The close time and the agent test are `merge_sql`'s: the same
    :data:`PR_CLOSED_AT_SQL` and the same `agent_worked_sql` at that time. The
    Python twin is :func:`merged_red`. It binds ``:merge_days`` and
    ``:merge_days_before``.

    ⚠️ **Why ``checks_fetched_at <= merged_at``** (RA-5, the decision of 3b).
    ``checks_state`` is written by the checks read of the GitHub App path
    (`github_sync.py` ``_CHECKS_WRITE_SQL``). That write matches the head
    SHA and nothing else, and ``_HEAD_HELD_SQL`` (the webhook gate in
    `routes/github/webhook.py` ``_on_checks``) matches a head that a MERGED
    reference holds too. So a ``check_run`` that completes after the merge
    rewrites the state. ``checks_fetched_at`` is the time of that read. A
    state read at or before ``merged_at`` is the state when the pull request
    merged. A state read later is not, so it stays out, and the count can
    only be too low. A new head resets the state to ``pending``
    (`github_sync.py` ``_PR_UPDATE_SQL``), so a failure that a push fixed does
    not count.
    """
    at = PR_CLOSED_AT_SQL
    return (
        f"SELECT"
        f"  count(*) FILTER (WHERE {at} >= {_NOW_FROM}) AS merged_red,"
        f"  count(*) FILTER (WHERE {at} < {_NOW_FROM}) AS merged_red_before"
        f"{_agent_pr_scope(past_where, at)}"
        f"   AND rf.state = 'merged'"
        f"   AND rf.merged_at IS NOT NULL"
        f"   AND {at} >= {_BEFORE_FROM}"
        f"   AND rf.checks_state = '{CHECKS_FAILING}'"
        f"   AND rf.checks_fetched_at IS NOT NULL"
        f"   AND rf.checks_fetched_at <= rf.merged_at"
    )


def finish_move_sql(a: str = "a") -> str:
    """A finish: a status move INTO done from a status that is not done, over
    the `pm_activities` alias ``a``. The twin is :func:`is_finish_move`. It
    binds ``:done_cat``."""
    return (
        f"{a}.meta->>'to_category' = :done_cat"
        f" AND {a}.meta->>'from_category' IS DISTINCT FROM :done_cat"
    )


def reopen_move_sql(a: str = "a") -> str:
    """A reopen: a move from done to an OPEN status, over the alias ``a``. The
    twin is :func:`is_reopen_move`. It binds ``:done_cat`` and ``:closing``."""
    return (
        f"{a}.meta->>'from_category' = :done_cat"
        f" AND {a}.meta->>'to_category' IS NOT NULL"
        f" AND {a}.meta->>'to_category' <> ALL(CAST(:closing AS text[]))"
    )


FINISH_MOVE_SQL = finish_move_sql("a")
REOPEN_MOVE_SQL = reopen_move_sql("a")


def reopened_sql(past_where: str) -> str:
    """For each group: the finishes of the window (``finished``), the ones of
    them that a later reopen undid (``undone``), and the DISTINCT tasks with a
    reopen in the window (``reopened``). Each count also covers the window
    before it (``n_before``). It binds ``:done_cat``, ``:closing``,
    ``:merge_days`` and ``:merge_days_before``.

    ⚠️ **Two different counts, on purpose** (RA-5 fix round 1). The reopen
    rate is a COHORT: the finishes of the window, and the share that a later
    reopen undid, at any time up to now. So it never passes 100%. A count of
    reopen moves over finish moves of one window can: three tasks finished 20
    days ago and reopened today are three reopens over no finish. ``reopened``
    is a count of tasks, and it takes the group of the finish that the
    LATEST reopen of the task in the window undid.

    A finish is undone by a reopen of the same task after it, with no other
    finish of the task between the two. The group is RA-1's label through
    RA-2's grouping. The finish join reads the LATEST finish at or before the
    move, so for a reopen it is the finish that the reopen undoes.
    """
    label = actor_group_sql(finish_label_sql())
    base = (
        f"  FROM pm_activities a"
        f"  JOIN pm_tasks t ON t.id = a.task_id"
        f"{finish_actor_join('a.task_id', 'a.created_at')}"
        f" WHERE ({past_where})"
        f"   AND a.type = 'status_change'"
        f"   AND a.deleted_at IS NULL"
        f"   AND a.created_at >= {_BEFORE_FROM}"
    )
    undone = (
        f"EXISTS (SELECT 1 FROM pm_activities r"
        f"        WHERE r.task_id = a.task_id AND r.type = 'status_change'"
        f"          AND r.deleted_at IS NULL AND {reopen_move_sql('r')}"
        f"          AND r.created_at > a.created_at"
        f"          AND NOT EXISTS (SELECT 1 FROM pm_activities n"
        f"                           WHERE n.task_id = a.task_id AND n.type = 'status_change'"
        f"                             AND n.deleted_at IS NULL AND {finish_move_sql('n')}"
        f"                             AND n.created_at > a.created_at"
        f"                             AND n.created_at < r.created_at))"
    )
    in_win = f"(a.created_at >= {_NOW_FROM})"
    return (
        f"WITH fin AS ("
        f"SELECT {label} AS grp, a.created_at AS moved_at, {undone} AS undone"
        f"{base} AND {finish_move_sql('a')}"
        f"), reo AS ("
        f"SELECT DISTINCT ON (a.task_id, {in_win}) {label} AS grp, {in_win} AS in_win"
        f"{base} AND {reopen_move_sql('a')}"
        f" ORDER BY a.task_id, {in_win}, a.created_at DESC, a.id DESC"
        f")"
        f" SELECT 'finished' AS kind, f.grp AS grp,"
        f"        count(*) FILTER (WHERE f.moved_at >= {_NOW_FROM}) AS n,"
        f"        count(*) FILTER (WHERE f.moved_at < {_NOW_FROM}) AS n_before"
        f"   FROM fin f GROUP BY f.grp"
        f" UNION ALL"
        f" SELECT 'undone', f.grp,"
        f"        count(*) FILTER (WHERE f.undone AND f.moved_at >= {_NOW_FROM}),"
        f"        count(*) FILTER (WHERE f.undone AND f.moved_at < {_NOW_FROM})"
        f"   FROM fin f GROUP BY f.grp"
        f" UNION ALL"
        f" SELECT 'reopened', o.grp,"
        f"        count(*) FILTER (WHERE o.in_win),"
        f"        count(*) FILTER (WHERE NOT o.in_win)"
        f"   FROM reo o GROUP BY o.grp"
    )


def retried_sql(past_where: str) -> str:
    """The runs that failed in the window and in the window before it, and
    how many of them have a later run on the same task. A later run is one
    created after the failed run ended (:func:`is_retried`). It binds
    ``:merge_days`` and ``:merge_days_before``."""
    return (
        f"SELECT"
        f"  count(*) FILTER (WHERE r.ended_at >= {_NOW_FROM}) AS failed,"
        f"  count(*) FILTER (WHERE r.ended_at >= {_NOW_FROM} AND x.retried) AS retried,"
        f"  count(*) FILTER (WHERE r.ended_at < {_NOW_FROM}) AS failed_before,"
        f"  count(*) FILTER (WHERE r.ended_at < {_NOW_FROM} AND x.retried) AS retried_before"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f"  CROSS JOIN LATERAL ("
        f"    SELECT EXISTS (SELECT 1 FROM pm_agent_runs r2"
        f"                    WHERE r2.task_id = r.task_id AND r2.id <> r.id"
        f"                      AND r2.created_at > r.ended_at) AS retried"
        f"  ) x"
        f" WHERE ({past_where})"
        f"   AND r.status = 'failed'"
        f"   AND r.ended_at IS NOT NULL"
        f"   AND r.ended_at >= {_BEFORE_FROM}"
    )


def quality_seen_sql(past_where: str) -> str:
    """True when the scope has an agent pull request, a reopened task or a
    failed run, ever. The Overview hides the panel without one (RA-3's
    rule). It binds ``:done_cat`` and ``:closing``."""
    return (
        f"SELECT ("
        f" EXISTS (SELECT 1 FROM pm_task_refs rf JOIN pm_tasks t ON t.id = rf.task_id"
        f"          WHERE ({past_where}) AND rf.kind = 'pull_request'"
        f"            AND {agent_worked_sql('t.id', 'now()')})"
        f" OR EXISTS (SELECT 1 FROM pm_activities a JOIN pm_tasks t ON t.id = a.task_id"
        f"            WHERE ({past_where}) AND a.type = 'status_change'"
        f"              AND a.deleted_at IS NULL AND {REOPEN_MOVE_SQL})"
        f" OR EXISTS (SELECT 1 FROM pm_agent_runs r JOIN pm_tasks t ON t.id = r.task_id"
        f"            WHERE ({past_where}) AND r.status = 'failed')"
        f") AS seen"
    )


# ── The body ─────────────────────────────────────────────────────────────────


_KINDS = ("finished", "undone", "reopened")


def _group_counts(rows: Iterable[Any]) -> dict[str, dict[str, int]]:
    """The rows of :func:`reopened_sql` as the counts of each group."""
    out = {g: {} for g in GROUPS}
    for g in GROUPS:
        for kind in _KINDS:
            out[g][kind] = 0
            out[g][f"{kind}_before"] = 0
    for r in rows:
        if r.grp in out and r.kind in _KINDS:
            out[r.grp][r.kind] = int(r.n or 0)
            out[r.grp][f"{r.kind}_before"] = int(r.n_before or 0)
    return out


def _group(counts: Mapping[str, int]) -> dict[str, Any]:
    """One group with its cohort rate, in the window and in the window before.
    The rate is the finishes that a reopen undid over the finishes, so it is
    never above 1."""
    now = rate(counts["undone"], counts["finished"])
    before = rate(counts["undone_before"], counts["finished_before"])
    return {**counts, "rate": now, "rate_before": before, "trend": merge_trend(now, before)}


def _failing_rows(rows: Iterable[Any], clock: Any) -> list[dict[str, Any]]:
    """The named failing pull requests. Each row opens its task and names
    the agent and the member it works for (§6.8 E2)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            **_task_row(r, clock, r.linked_at),
            "ref": r.pr_ref,
            "url": r.pr_url,
            "pr_title": r.pr_title,
            "pr_state": r.pr_state,
            **agent_of(r.run_via, r.run_member, r.act_by, r.act_via),
            "member_name": r.run_member_name if r.run_via else None,
        })
    return out


async def quality_body(
    db: Any,
    vis: Any,
    *,
    project_id: str | None,
    include_subtree: bool,
) -> dict[str, Any]:
    """The ``quality`` report section, on a session and a visibility that the
    caller resolved (`projects_reports.md` §8 RA-5).

    It reads its own window of :data:`QUALITY_WINDOW_DAYS` and never the
    report period, as the merge rate of RA-2 does. ``summary`` is the one
    sentence of the panel (§6.8 E5).
    """
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)
    open_where = load_open_where(scope_sql, vis)
    past_where = history_where(scope_sql, vis)
    params: dict[str, Any] = {
        **load_params(vis, project_id),
        "merge_days": QUALITY_WINDOW_DAYS,
        "merge_days_before": 2 * QUALITY_WINDOW_DAYS,
        "done_cat": COMPLETED_CATEGORY,
        "closing": sorted(CLOSING_CATEGORIES),
        "failing_named": FAILING_NAMED,
    }
    clock = (await db.execute(text("SELECT now()"))).scalar()
    runs_on = agent_runs_enabled()

    # RA-2's merge count, as it is. The rate and the trend are RA-2's too.
    pr = (await db.execute(text(merge_sql(past_where)), params)).one()
    merged, closed = int(pr.merged or 0), int(pr.closed or 0)
    merged_before, closed_before = int(pr.merged_before or 0), int(pr.closed_before or 0)
    value, before = merge_rate(merged, closed), merge_rate(merged_before, closed_before)

    opened = (await db.execute(text(open_prs_sql(open_where)), params)).one()
    failing_rows = (await db.execute(text(failing_prs_sql(open_where, runs_on=runs_on)), params)).fetchall()
    failing = _failing_rows(failing_rows, clock)
    failing_total = int(opened.failing_total or 0)

    red = (await db.execute(text(merged_red_sql(past_where)), params)).one()

    groups = _group_counts(
        (await db.execute(text(reopened_sql(past_where)), params)).fetchall()
    )
    reopened = {g: _group(groups[g]) for g in GROUPS}

    retried = {"failed": 0, "retried": 0, "failed_before": 0, "retried_before": 0}
    if runs_on:
        row = (await db.execute(text(retried_sql(past_where)), params)).one()
        retried = {k: int(getattr(row, k) or 0) for k in retried}
    retry_rate = rate(retried["retried"], retried["failed"])
    retry_before = rate(retried["retried_before"], retried["failed_before"])
    retried = {
        **retried, "rate": retry_rate, "rate_before": retry_before,
        "trend": merge_trend(retry_rate, retry_before),
    }

    seen = bool((await db.execute(text(quality_seen_sql(past_where)), params)).scalar())
    # D-PM-38: the same "↳ Parent" that every flat row carries.
    await attach_parent_context(db, vis, failing)

    prs = {
        "merged": merged,
        "closed": closed,
        "rate": value,
        "merged_before": merged_before,
        "closed_before": closed_before,
        "rate_before": before,
        "trend": merge_trend(value, before),
        "open": int(opened.open_total or 0),
    }
    checks = {
        "failing_total": failing_total,
        "failing": failing,
        "merged_red": int(red.merged_red or 0),
        "merged_red_before": int(red.merged_red_before or 0),
    }
    rework = {"reopened": reopened, "retried": retried}
    return {
        "summary": summary_line(prs=prs, red=checks, rework=rework, runs_on=runs_on),
        "on": runs_on,
        "quality_seen": seen,
        "days": QUALITY_WINDOW_DAYS,
        "prs": prs,
        "checks": checks,
        "rework": rework,
        "max_rows": FAILING_NAMED,
    }

