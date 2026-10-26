"""Projects · people and agents in the Overview — WS-27bn RA-2.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B, D, E and F, the
§5 rows "Silent run", "Agent PR merge rate" and "Agent run time", and §8
RA-2.

Three things live here.

1. **The split by actor of two sections.** ``finished`` gains
   ``actor_series`` (each week of the window, by finish label) and
   ``agent_members`` (each agent finish, under the member it works for).
   ``throughput`` gains ``actor_cycle`` (the median and the p90 of people and
   of agents). Both sections gain ``actor_summary``, the one sentence of the
   panel (§6.8 E5).
2. **The figures of the KPI strip that no section holds** (:func:`kpis_body`):
   agent runs now (working, silent, failed today), the agent PR merge rate of
   14 days, and the cost of each finished task with the agent run time as its
   measured fallback (§6.8 D).
3. **The one rule of a silent run** (:data:`SILENT_RUN_AFTER_S`).

⚠️ **Each rule is a rule that already exists, joined.** The labels are RA-1's
(`analytics.finish_labels_sql`, over Throughput's ``cyc``). "Working" is
`filters.AGENT_RUN_STATE_SQL` with `agent_summary.WORKING_STATES`, the rule of
the board strip. "Agent work" on a pull request is
`agent_worked.AGENT_WORKED_SQL`. The agent of a finish and its member come
from `analytics_waiting.agent_of`. "An agent act" is `analytics.agent_act_sql`.
A second copy of any of them would let the Overview and the board disagree.

⚠️ **Agents are ``agent_finished`` AND ``agent_assisted``** (the RA-2
decision). §6.8 B asks "are agent tasks really faster, end to end?". A task
that an agent worked and that a person or GitHub then moved to done is an
agent task end to end: merge-to-Done (`github_merge.py`) gives that label to
every task that it moves. People are ``person``: tasks that no agent worked
before the finish.

⚠️ **§7 rule 1.** Every query reads the caller's ``past_where`` or
``open_where``, which carry the caller's visibility, the triage exclusion and
the subject. The caller's tenant session runs every query. Nothing here reads
a tenant or a person from the request (R5). The member rows follow §7.1 rule
3 through `report_scope.filter_person_rows`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from gateway.routes.projects.agent_summary import WORKING_STATES
from gateway.routes.projects.agent_worked import agent_worked_sql
from gateway.routes.projects.analytics import (
    FINISH_AGENT,
    FINISH_ASSISTED,
    FINISH_LABELS,
    FINISH_PERSON,
    MAX_NAMED,
    _hours,
    activity_via_sql,
    agent_act_sql,
    cycle_cte_sql,
    finish_labels_sql,
    last_week_sql,
)
from gateway.routes.projects.analytics_waiting import (
    _member_name,
    age_words,
    agent_of,
    answered_at_sql,
    percentile_cont,
)
from gateway.routes.projects.filters import AGENT_RUN_STATE_SQL, agent_runs_enabled
from gateway.routes.projects.report_scope import filter_person_rows
from sqlalchemy import text

# ── The two groups ───────────────────────────────────────────────────────────

#: The group of the finish labels that an agent worked.
GROUP_AGENTS = "agents"
#: The group of the finish label that no agent worked.
GROUP_PEOPLE = "people"
#: The two groups, in the order a split prints them.
GROUPS: tuple[str, ...] = (GROUP_PEOPLE, GROUP_AGENTS)
#: The finish labels of the agents group. See the module header.
AGENT_GROUP_LABELS: tuple[str, ...] = (FINISH_AGENT, FINISH_ASSISTED)


def actor_group(label: str | None) -> str:
    """The group of one finish label: ``agents`` or ``people``."""
    return GROUP_AGENTS if label in AGENT_GROUP_LABELS else GROUP_PEOPLE


def actor_group_sql(label: str) -> str:
    """The SQL twin of :func:`actor_group`, over a label expression."""
    labels = ", ".join(f"'{x}'" for x in AGENT_GROUP_LABELS)
    return f"CASE WHEN {label} IN ({labels}) THEN '{GROUP_AGENTS}' ELSE '{GROUP_PEOPLE}' END"


# ── The silent run (§5, §6.8 F) ──────────────────────────────────────────────

#: Seconds with no agent activity before an open run in ``in_progress`` is
#: SILENT (`projects_reports.md` §5 and §6.8 F: "15 minutes or more").
#:
#: ⚠️ **ONE constant, and this module owns it.** RA-4's "Agent runs" panel
#: reads it from here. It is NOT `filters.AGENT_RUN_STALE_AFTER_S` (300
#: seconds). That constant is the idle state of the card pill, it reads
#: ``last_activity_at`` (a member prompt resets it), and the board strip
#: counts it as working. A silent run is a different question: has the AGENT
#: said anything for a quarter of an hour?
SILENT_RUN_AFTER_S = 15 * 60

#: The status of a run that can go silent. ``created`` reads queued, and an
#: ``awaiting`` run waits for a person, not for the agent.
SILENT_STATUS = "in_progress"


def is_silent_run(
    status: str | None,
    last_agent_at: datetime | None,
    started_at: datetime | None,
    created_at: datetime | None,
    *,
    now: datetime,
) -> bool:
    """True when a run in ``in_progress`` had no agent activity for
    :data:`SILENT_RUN_AFTER_S` or more. A run with no agent activity yet
    counts from its start, and from its creation when it has no start."""
    if status != SILENT_STATUS:
        return False
    since = last_agent_at or started_at or created_at
    if since is None:
        return False
    return now - since >= timedelta(seconds=SILENT_RUN_AFTER_S)


def silent_since_sql(alias: str = "r") -> str:
    """Since when the run ``alias`` has said nothing: its last agent act, or
    its start, or its creation. The clock of :func:`silent_run_sql`.
    WS-27bn RA-4 reads it for the age of each silent run."""
    return (
        f"coalesce({alias}.last_agent_activity_at, {alias}.started_at,"
        f" {alias}.created_at)"
    )


def silent_run_sql(alias: str = "r") -> str:
    """The SQL twin of :func:`is_silent_run`, over the run alias. It binds
    ``:silent_s``. The database ``now()`` decides, as the run states do."""
    return (
        f"({alias}.status = '{SILENT_STATUS}'"
        f" AND {silent_since_sql(alias)} <= now() - make_interval(secs => :silent_s))"
    )


# ── The agent PR merge rate (§6.8 B, E4) ─────────────────────────────────────

#: The window of the merge rate, in days. The trend compares it with the
#: window before it.
MERGE_WINDOW_DAYS = 14

#: When a pull request merged or closed, or NULL when nobody knows.
#:
#: Only the GitHub App path (`github_sync.py`) writes the two times that
#: GitHub gives. A merge has ``merged_at``. A close with no merge has no
#: close time in ``pm_task_refs``, so it reads ``pr_updated_at``, the time
#: of the last update of the pull request. That is the close, unless somebody
#: commented on the closed pull request later. The tile says so.
#:
#: ⚠️ **Never the row's own ``updated_at``** (RA-2 review, P1). The token path
#: of TR-1 (`refs.py`) writes ``state`` and ``updated_at = now()`` on each read
#: of the task, and no GitHub time. So a pull request merged 80 days ago read
#: as merged today once somebody opened its task. A pull request with no
#: known close time is left out of both windows.
_PR_CLOSED_AT = (
    "CASE WHEN rf.state = 'merged' THEN coalesce(rf.merged_at, rf.pr_updated_at)"
    " ELSE rf.pr_updated_at END"
)


def pr_closed_at(
    state: str | None, merged_at: datetime | None, pr_updated_at: datetime | None,
) -> datetime | None:
    """The Python twin of :data:`_PR_CLOSED_AT`. None when no GitHub time
    says when the pull request closed."""
    if state == "merged":
        return merged_at or pr_updated_at
    return pr_updated_at


def merge_rate(merged: int, closed: int) -> float | None:
    """The share of the agent pull requests that merged: merged over merged
    plus closed with no merge. None when none closed, never a zero."""
    total = int(merged or 0) + int(closed or 0)
    return None if total == 0 else round(int(merged or 0) / total, 4)


def merge_trend(rate: float | None, before: float | None) -> str | None:
    """``up``, ``down`` or ``flat`` against the window before, or None when
    either window has no closed pull request. ⚠️ No target: the trend says
    which way the rate moved, and never how far it is from 100% (§6.8 E4)."""
    if rate is None or before is None:
        return None
    if rate > before:
        return "up"
    if rate < before:
        return "down"
    return "flat"


# ── Cost (§6.8 D) ────────────────────────────────────────────────────────────

#: Why no cost figure exists. SW-20 (Metorite #330) captures usage, and it is
#: not built. So every finished task has an unknown cost, never $0.
COST_UNKNOWN_REASON = "Usage capture (SW-20) is not built."


def cost_figure(finished: int) -> dict[str, Any]:
    """The cost of each finished task, and how much of it is known.

    ⚠️ **Unknown is not zero.** No source holds a cost yet (§6.8 D items 1
    and 2), so ``per_task`` is None and ``known_tasks`` is 0. ``share`` is
    the part of the finished tasks with any cost data: 0.0 when tasks
    finished, and None when no task finished.
    """
    finished = int(finished or 0)
    return {
        "per_task": None,
        "source": None,
        "known_tasks": 0,
        "finished": finished,
        "share": 0.0 if finished else None,
        "reason": COST_UNKNOWN_REASON,
    }


# ── The weekly series and the cycle split ────────────────────────────────────


def actor_week(row: Any) -> dict[str, Any]:
    """One week of ``actor_series``: each label, the two groups and the total.

    The server adds the groups, so no client adds two counts.
    """
    counts = {label: int(getattr(row, label, 0) or 0) for label in FINISH_LABELS}
    agents = sum(counts[label] for label in AGENT_GROUP_LABELS)
    return {
        "week_start": row.week.date().isoformat(),
        **counts,
        GROUP_AGENTS: agents,
        GROUP_PEOPLE: counts[FINISH_PERSON],
        "completed": agents + counts[FINISH_PERSON],
    }


def actor_weekly_sql(scope_where: str, *, skip_current_week: bool = False) -> str:
    """Each week of the window, with the done tasks of each finish label.

    The labels are :func:`analytics.finish_labels_sql`, over the window of
    ``cyc``, so the weeks add up to ``actor_split`` of the same window. A
    quiet week is a zero, never an absent row, as `weekly_sql` rules. The
    spine ends on :func:`analytics.last_week_sql`, so a report that drops the
    running week drops it here too.
    """
    last = last_week_sql(skip_current_week)
    labels = finish_labels_sql(scope_where, skip_current_week=skip_current_week)
    counts = ", ".join(
        f"count(l.task_id) FILTER (WHERE l.label = '{label}') AS {label}"
        for label in FINISH_LABELS
    )
    return (
        f"WITH weeks AS ("
        f"  SELECT generate_series("
        f"    {last} - make_interval(weeks => :weeks - 1),"
        f"    {last}, interval '1 week') AS week"
        f")"
        f" SELECT w.week AS week, {counts}"
        f"   FROM weeks w"
        f"   LEFT JOIN ({labels}) l"
        f"     ON date_trunc('week', l.finished_at AT TIME ZONE 'UTC') = w.week"
        f"  GROUP BY w.week"
        f"  ORDER BY w.week"
    )


def actor_cycle_sql(scope_where: str, *, skip_current_week: bool = False) -> str:
    """The cycle time of each group, over the done tasks of the window.

    ``hours`` is Throughput's ``cyc.hours``, so a task with no recorded start
    stays out of the median, as in the panel's own figure.
    """
    labels = finish_labels_sql(scope_where, skip_current_week=skip_current_week)
    return (
        f"SELECT {actor_group_sql('l.label')} AS grp,"
        f"       count(*) AS completed,"
        f"       count(*) FILTER (WHERE l.label = '{FINISH_ASSISTED}') AS assisted,"
        f"       count(l.hours) AS measured,"
        f"       percentile_cont(0.5) WITHIN GROUP (ORDER BY l.hours) AS median_hours,"
        f"       percentile_cont(0.9) WITHIN GROUP (ORDER BY l.hours) AS p90_hours"
        f"  FROM ({labels}) l"
        f" GROUP BY 1"
    )


def _no_cycle() -> dict[str, Any]:
    return {"completed": 0, "assisted": 0, "measured": 0,
            "median_hours": None, "p90_hours": None}


def actor_cycle(rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Each group with its counts, median and p90. A group with no row has
    zeros and no median, never a zero median."""
    out = {group: _no_cycle() for group in GROUPS}
    for row in rows:
        if row.grp not in out:
            continue
        out[row.grp] = {
            "completed": int(row.completed or 0),
            "assisted": int(row.assisted or 0),
            "measured": int(row.measured or 0),
            "median_hours": _hours(row.median_hours),
            "p90_hours": _hours(row.p90_hours),
        }
    return out


# ── Each agent under its member (§6.8 E2) ────────────────────────────────────


def agent_members_sql(scope_where: str, *, skip_current_week: bool = False) -> str:
    """Each agent finish of the window, with the agent and its member.

    The newest run of the task at or before the done move names both, as
    `analytics_waiting.agent_of` reads a run. With no run, the newest agent
    act at or before the done move names them: its stamp is the client, and
    its actor is the member. An in-app agent (``agent:<name>``) names itself
    only.
    """
    labels = finish_labels_sql(scope_where, skip_current_week=skip_current_week)
    agent_labels = ", ".join(f"'{x}'" for x in AGENT_GROUP_LABELS)
    via = activity_via_sql("ma")
    return (
        f"SELECT l.label AS label,"
        f"       nr.client_via AS run_via, nr.member_email AS run_member,"
        f"       {_member_name('nr.member_email')} AS run_member_name,"
        f"       ma.created_by AS act_by, {via} AS act_via,"
        f"       {_member_name('ma.created_by')} AS act_member_name"
        f"  FROM ({labels}) l"
        f"  LEFT JOIN LATERAL ("
        f"    SELECT x.client_via, x.member_email FROM pm_agent_runs x"
        f"     WHERE x.task_id = l.task_id AND x.created_at <= l.done_at"
        f"     ORDER BY x.created_at DESC, x.id DESC LIMIT 1"
        f"  ) nr ON TRUE"
        f"  LEFT JOIN LATERAL ("
        f"    SELECT ma.created_by, ma.meta FROM pm_activities ma"
        f"     WHERE ma.task_id = l.task_id AND ma.deleted_at IS NULL"
        f"       AND ma.created_at <= l.done_at"
        f"       AND {agent_act_sql('ma.created_by', via)}"
        f"     ORDER BY ma.created_at DESC, ma.id DESC LIMIT 1"
        f"  ) ma ON TRUE"
        f" WHERE l.label IN ({agent_labels})"
    )


def _sort_name(name: str | None, email: str | None) -> str:
    return str(name or email or "").strip().lower()


def agent_members(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """The agent finishes, under the member each agent works for.

    ⚠️ **No rank (§6.8 E1).** The members sort by name, and the agents of a
    member by name, never by count. The in-app agents, which work for no
    member, come last.
    """
    members: dict[str | None, dict[str, Any]] = {}
    for row in rows:
        who = agent_of(row.run_via, row.run_member, row.act_by, row.act_via)
        email = str(who["member"]).strip().lower() if who["member"] else None
        name = row.run_member_name if row.run_via else row.act_member_name
        entry = members.setdefault(email, {
            "member": email, "member_name": name or None, "total": 0, "agents": {},
        })
        key = (who["agent"], who["client_via"])
        agent = entry["agents"].setdefault(key, {
            "agent": who["agent"], "client_via": who["client_via"],
            FINISH_AGENT: 0, FINISH_ASSISTED: 0, "total": 0,
        })
        label = row.label if row.label in AGENT_GROUP_LABELS else FINISH_AGENT
        agent[label] += 1
        agent["total"] += 1
        entry["total"] += 1
    out = []
    for entry in members.values():
        entry["agents"] = sorted(
            entry["agents"].values(),
            key=lambda a: (str(a["agent"]).lower(), str(a["client_via"] or "")),
        )
        out.append(entry)
    out.sort(key=lambda m: (m["member"] is None, _sort_name(m["member_name"], m["member"])))
    return out


# ── The summary sentences (§6.8 E5) ──────────────────────────────────────────


def _tasks(n: int) -> str:
    return "1 task" if n == 1 else f"{n} tasks"


def finished_summary(split: dict[str, int]) -> str:
    """The one plain sentence of "Who did the work". The panel, the email
    and the chat print this text, so they cannot say two things."""
    finished = int(split.get(FINISH_AGENT, 0) or 0)
    assisted = int(split.get(FINISH_ASSISTED, 0) or 0)
    person = int(split.get(FINISH_PERSON, 0) or 0)
    total = finished + assisted + person
    if total == 0:
        return "No task finished in this period."
    agents = finished + assisted
    head = f"{_tasks(total)} finished in this period."
    if agents == 0:
        return f"{head} People did {'it' if total == 1 else 'all of them'}, with no agent work."
    if person == 0:
        line = f"{head} Agents worked on {'it' if total == 1 else 'all of them'}."
    else:
        line = f"{head} Agents worked on {agents}, and people did {person}."
    if assisted == 1:
        line += " A person finished 1 of the agent tasks."
    elif assisted > 1:
        line += f" A person finished {assisted} of the agent tasks."
    return line


def cycle_summary(cycle: dict[str, dict[str, Any]]) -> str:
    """The one plain sentence of the split of "How long it took"."""
    people = cycle.get(GROUP_PEOPLE) or _no_cycle()
    agents = cycle.get(GROUP_AGENTS) or _no_cycle()
    p, a = people.get("median_hours"), agents.get("median_hours")
    if p is None and a is None:
        return "No finished task of this period has a measured cycle time."
    if a is None:
        return (
            f"Tasks of people took {age_words(p)} at the median."
            " No agent task of this period has a measured cycle time."
        )
    if p is None:
        return (
            f"Agent tasks took {age_words(a)} at the median."
            " No task of people alone has a measured cycle time."
        )
    return (
        f"At the median, agent tasks took {age_words(a)}"
        f" and tasks of people took {age_words(p)}."
    )


# ── The sections ─────────────────────────────────────────────────────────────


async def finished_actor_body(
    db: Any, past_where: str, params: dict[str, Any], *,
    skip_current_week: bool, allowed: frozenset[str] | None,
) -> dict[str, Any]:
    """``actor_series`` and ``agent_members`` of the ``finished`` section.

    ``allowed`` is the reader's §7.1 filter (``None`` is everyone). The member
    rows that it hides are counted in ``hidden_people``, as `load` counts
    them. Only a restricted reader receives that key.
    """
    weeks = (
        await db.execute(
            text(actor_weekly_sql(past_where, skip_current_week=skip_current_week)),
            params,
        )
    ).fetchall()
    found = (
        await db.execute(
            text(agent_members_sql(past_where, skip_current_week=skip_current_week)),
            params,
        )
    ).fetchall()
    members, hidden = filter_person_rows(agent_members(found), allowed, key="member")
    out: dict[str, Any] = {
        "actor_series": [actor_week(w) for w in weeks],
        "agent_members": members[:MAX_NAMED],
        "agent_members_total": len(members),
    }
    if allowed is not None:
        out["hidden_people"] = hidden
    return out


async def throughput_actor_body(
    db: Any, past_where: str, params: dict[str, Any], *, skip_current_week: bool,
) -> dict[str, Any]:
    """``actor_cycle`` and ``actor_summary`` of the ``throughput`` section."""
    rows = (
        await db.execute(
            text(actor_cycle_sql(past_where, skip_current_week=skip_current_week)),
            params,
        )
    ).fetchall()
    cycle = actor_cycle(rows)
    return {"actor_cycle": cycle, "actor_summary": cycle_summary(cycle)}


# ── The KPI strip (§6.8 B) ───────────────────────────────────────────────────


def runs_now_sql(open_where: str) -> str:
    """The open runs in ``in_progress`` on the open tasks of the scope, by
    member: how many work, and how many are silent.

    "Working" is the board strip's rule: the run state of
    `filters.AGENT_RUN_STATE_SQL` is one of `agent_summary.WORKING_STATES`.
    A silent run (:func:`silent_run_sql`) is not working. It binds
    ``:working_states`` and ``:silent_s``.
    """
    silent = silent_run_sql("r")
    return (
        f"SELECT lower(r.member_email) AS member,"
        f"       {_member_name('r.member_email')} AS member_name,"
        f"       count(*) FILTER (WHERE ({AGENT_RUN_STATE_SQL})"
        f"         = ANY(CAST(:working_states AS text[])) AND NOT {silent}) AS working,"
        f"       count(*) FILTER (WHERE {silent}) AS silent"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f" WHERE {open_where}"
        f"   AND r.status = '{SILENT_STATUS}'"
        f" GROUP BY 1, 2"
    )


#: Midnight today, in UTC, as a ``timestamptz``. The pulse reads one UTC day.
_TODAY_UTC = "(date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')"


def failed_today_sql(past_where: str) -> str:
    """The runs on the tasks of the scope that failed since midnight UTC.

    A run fails as a terminal move, which stamps ``ended_at``.
    """
    return (
        f"SELECT count(*) AS failed"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f" WHERE {past_where}"
        f"   AND r.status = 'failed'"
        f"   AND coalesce(r.ended_at, r.last_activity_at) >= {_TODAY_UTC}"
    )


def merge_sql(past_where: str) -> str:
    """The agent pull requests that merged or closed, in this window and in
    the window before it.

    A pull request is an agent's when an agent worked its task before it
    merged or closed (`agent_worked.AGENT_WORKED_SQL`, at that time). A draft
    and an open one have no outcome yet, so they do not count. It binds
    ``:merge_days`` and ``:merge_days_before``.
    """
    at = _PR_CLOSED_AT
    now_from = "now() - make_interval(days => :merge_days)"
    before_from = "now() - make_interval(days => :merge_days_before)"
    return (
        f"SELECT"
        f"  count(*) FILTER (WHERE rf.state = 'merged' AND {at} >= {now_from}) AS merged,"
        f"  count(*) FILTER (WHERE rf.state = 'closed' AND {at} >= {now_from}) AS closed,"
        f"  count(*) FILTER (WHERE rf.state = 'merged' AND {at} < {now_from})"
        f"    AS merged_before,"
        f"  count(*) FILTER (WHERE rf.state = 'closed' AND {at} < {now_from})"
        f"    AS closed_before"
        f"  FROM pm_task_refs rf"
        f"  JOIN pm_tasks t ON t.id = rf.task_id"
        f" WHERE {past_where}"
        f"   AND rf.kind = 'pull_request'"
        f"   AND rf.state IN ('merged', 'closed')"
        f"   AND {at} >= {before_from}"
        f"   AND {agent_worked_sql('t.id', at)}"
    )


#: What the run time measures. Said in the payload, so no client calls it
#: start to end, and none calls it money.
RUN_TIME_BASIS = "agent_time_less_waits"

#: The end of a run for its agent time: the agent's last act, never later
#: than the end that the server stamped (RA-2 review, P2).
#:
#: ⚠️ ``ended_at`` alone over-counts. A takeover (``abandoned``), a release
#: and an SW-10a server close stamp ``ended_at`` at the time of that move,
#: which can be days after the agent went quiet. A run with no agent act
#: ends at its start, so it adds nothing. ``least`` skips a NULL.
_RUN_END = "least(r.ended_at, coalesce(r.last_agent_activity_at, r.started_at))"

#: The next act of the agent after the question ``e`` in its run: a row that
#: is not a member's prompt and not a line that the server wrote.
_NEXT_AGENT_ACT = (
    "(SELECT min(n.created_at) FROM pm_agent_run_activities n"
    " WHERE n.run_id = e.run_id AND n.seq > e.seq AND n.type <> 'prompt'"
    " AND coalesce(n.content->>'system', '') <> 'true')"
)


def run_hours(
    started: datetime | None,
    ended: datetime | None,
    last_agent: datetime | None,
    questions: Iterable[tuple[datetime, datetime | None, datetime | None]] = (),
    gaps: Iterable[tuple[datetime, datetime]] = (),
) -> float | None:
    """The agent time of one run, in hours, or None when it has none.

    The run lasts from its start to the agent's last act, and never past
    ``ended``. Each question takes out its wait on a person: from the
    question to its answer, or to the agent's next act when that comes
    first, and never past the end. A question with no answer waits until
    that next act or the end. ``questions`` holds ``(asked, answered,
    next_agent_act)`` for each question of the run. The SQL twin is
    :func:`run_time_sql`.

    ⚠️ **The waits never overlap.** A wait ends at or before the agent's next
    act, and the next question is an agent act. So no second for one wait
    is taken out two times.

    RA-4b: each gap takes out the idle time of a reopened run, from the
    end that a member prompt reopened to that prompt (:func:`reopen_gaps`).
    The gap leaves out the part that a wait already covers. The part of a
    gap outside the run is ignored.
    """
    if started is None or ended is None:
        return None
    end = min(ended, last_agent or started)
    if end < started:
        return None
    total = (end - started).total_seconds()
    for asked, answered, next_act in questions:
        if not (started <= asked < end):
            continue
        stop = min(t for t in (answered, next_act, end) if t is not None)
        total -= max(0.0, (stop - asked).total_seconds())
    for idle_from, idle_to in gaps:
        lo, hi = max(idle_from, started), min(idle_to, end)
        total -= max(0.0, (hi - lo).total_seconds())
    return max(0.0, total) / 3600.0


def reopen_gaps(
    prompts: Iterable[tuple[datetime, datetime | None, datetime | None]],
) -> list[tuple[datetime, datetime]]:
    """The idle gaps of a reopened run (RA-4b), as ``(from, to)`` pairs.

    ``prompts`` holds ``(prompt_at, reopened_end, wait_end)`` for each member
    prompt of the run. ``reopened_end`` is the ``content.reopens.ended_at``
    that `agent_runs._write_activity` stamps on a prompt that REOPENS a
    terminal run. A prompt without it makes no gap: it went to a run that was
    still open, and the old rows have no marker. ``wait_end`` is the end of
    the wait on the question that the prompt answers, or None.

    ⚠️ **A gap never overlaps a wait.** The question ``e`` was asked before the
    run ended, and the prompt answers it. Then the wait from ``e`` to the
    answer already covers the time from the end to the prompt. So the gap
    starts at the later of the end and the end of that wait. When an agent act
    ended the wait before the run ended, the gap is whole. The SQL twin is
    :data:`_REOPEN_GAPS_JOIN`.
    """
    gaps: list[tuple[datetime, datetime]] = []
    for at, ended, wait_end in prompts:
        if ended is None:
            continue
        start = max(ended, wait_end) if wait_end is not None else ended
        if at > start:
            gaps.append((start, at))
    return gaps


#: RA-4b. The idle gaps of a reopened run: each member prompt that carries
#: ``content.reopens`` (`agent_runs._write_activity`), from the end it reopened
#: to the prompt, less the part that a wait on a person already covers, and
#: cut to the run. The Python twin is :func:`reopen_gaps`.
#:
#: ⚠️ **A reopen keeps its run.** A member prompt moves ``completed`` to
#: ``in_progress`` in the SAME run, ``started_at`` stays and ``ended_at``
#: clears (`agent_runs._move_run`). So the time between the terminal move and
#: that prompt is the member's, not the agent's. Without this join a run that
#: a member reopens a day later reads one day long.
#:
#: ⚠️ **The marker, never the row trail.** The cap of ephemeral rows deletes
#: the thoughts and actions between two acts, so a trail of rows can look
#: unbroken when the agent worked all that time. A prompt with no marker went
#: to an open run and makes no gap.
def _reopen_gaps_join() -> str:
    wait_end = (
        f"least(coalesce({answered_at_sql('e')}, re.run_end),"
        f" coalesce({_NEXT_AGENT_ACT}, re.run_end), re.run_end)"
    )
    covered = (
        f"coalesce((SELECT {wait_end} FROM pm_agent_run_activities e"
        f" WHERE e.id = p.in_reply_to AND e.type = 'elicitation'),"
        f" '-infinity'::timestamptz)"
    )
    return (
        " LEFT JOIN LATERAL ("
        "   SELECT sum(greatest(0, extract(epoch FROM"
        "            (least(p.created_at, re.run_end) - greatest(g.from_at, r.started_at)))))"
        "            AS gap_s"
        "     FROM pm_agent_run_activities p"
        "    CROSS JOIN LATERAL (SELECT greatest("
        "      CAST(p.content->'reopens'->>'ended_at' AS timestamptz),"
        f"      {covered}) AS from_at) g"
        "    WHERE p.run_id = r.id AND p.type = 'prompt'"
        "      AND p.content->'reopens'->>'ended_at' IS NOT NULL"
        " ) rg ON TRUE"
    )


_REOPEN_GAPS_JOIN = _reopen_gaps_join()


def run_time_joins_sql() -> str:
    """The joins that give the run ``r`` its agent time (§6.8 D item 3).

    ``re.run_end`` is :data:`_RUN_END`, ``w.wait_s`` is the seconds of
    each wait on a person (`analytics_waiting.answered_at_sql` pairs a
    question with its answer, as the answer time does), and ``rg.gap_s`` is
    the seconds of each idle gap of a reopened run (:data:`_REOPEN_GAPS_JOIN`,
    RA-4b). Read the agent time
    of the run as :data:`RUN_AGENT_SECONDS_SQL`, and only where
    :data:`RUN_MEASURED_SQL` holds.

    ⚠️ **ONE text for the agent time of a run.** The KPI tile
    (:func:`run_time_sql`) sums it for each finished task, and WS-27bn RA-4
    (`analytics_agent_runs.py`) puts each completed run in a size. A second
    copy would let the tile and the sizes measure two different times.
    """
    wait_end = (
        f"least(coalesce({answered_at_sql('e')}, re.run_end),"
        f" coalesce({_NEXT_AGENT_ACT}, re.run_end), re.run_end)"
    )
    return (
        f" CROSS JOIN LATERAL (SELECT {_RUN_END} AS run_end) re"
        f" LEFT JOIN LATERAL ("
        f"   SELECT sum(greatest(0, extract(epoch FROM ({wait_end} - e.created_at))))"
        f"            AS wait_s"
        f"     FROM pm_agent_run_activities e"
        f"    WHERE e.run_id = r.id AND e.type = 'elicitation'"
        f"      AND e.created_at >= r.started_at AND e.created_at < re.run_end"
        f" ) w ON TRUE"
        f"{_REOPEN_GAPS_JOIN}"
    )


#: The agent seconds of the run ``r``, over :func:`run_time_joins_sql`: from
#: its start to its end, less the waits. The SQL twin of :func:`run_hours`.
RUN_AGENT_SECONDS_SQL = (
    "greatest(0, extract(epoch FROM (re.run_end - r.started_at))"
    " - coalesce(w.wait_s, 0) - coalesce(rg.gap_s, 0))"
)

#: The run ``r`` has an agent time: it started, it ended, and its end is not
#: before its start. :func:`run_hours` gives None for the other runs.
RUN_MEASURED_SQL = (
    "(r.started_at IS NOT NULL AND r.ended_at IS NOT NULL"
    " AND re.run_end >= r.started_at)"
)


def run_time_sql(scope_where: str, *, skip_current_week: bool = False) -> str:
    """The agent time of each task done in the window (§6.8 D item 3).

    The SQL twin of :func:`run_hours`, summed over the runs of each task. A
    run counts when it started, ended, and was made at or before the finish
    that the window counts. Each wait on a person comes out, through
    :func:`run_time_joins_sql`. ``finished`` counts every done task of the
    window, and ``run_tasks`` the ones with a measured run.
    """
    return (
        f"{cycle_cte_sql(scope_where, skip_current_week=skip_current_week)}"
        f" SELECT count(*) AS finished, count(x.hours) AS run_tasks,"
        f"        percentile_cont(0.5) WITHIN GROUP (ORDER BY x.hours) AS median_hours,"
        f"        sum(x.hours) AS total_hours"
        f"   FROM cyc c"
        f"   LEFT JOIN LATERAL ("
        f"     SELECT sum({RUN_AGENT_SECONDS_SQL}) / 3600.0 AS hours"
        f"       FROM pm_agent_runs r"
        f"      {run_time_joins_sql()}"
        f"      WHERE r.task_id = c.task_id"
        f"        AND {RUN_MEASURED_SQL}"
        f"        AND r.created_at <= c.finished_at"
        f"   ) x ON TRUE"
        f"  WHERE c.to_category = :done_cat"
    )


def run_time(hours: Iterable[float | None]) -> dict[str, Any]:
    """The Python twin of :func:`run_time_sql`'s figures, over the run hours
    of each done task (None for a task with no measured run)."""
    xs = list(hours)
    measured = [float(h) for h in xs if h is not None]
    return {
        "finished": len(xs),
        "run_tasks": len(measured),
        "median_hours": _hours(percentile_cont(measured, 0.5)),
        "total_hours": _hours(sum(measured)) if measured else None,
    }


async def kpis_body(
    db: Any, *, past_where: str, open_where: str, params: dict[str, Any],
    skip_current_week: bool, allowed: frozenset[str] | None,
) -> dict[str, Any]:
    """The figures of the KPI strip that no section holds (§6.8 B).

    ``runs`` reads the state now. ``merge`` reads 14 days back from now.
    ``time`` and ``cost`` read the report window. While ``agent_runs_enabled``
    is off, ``runs`` reads nothing, as `agent_summary.py` does. The pull
    requests do not depend on the flag.
    """
    binds = {
        **params,
        "working_states": list(WORKING_STATES),
        "silent_s": SILENT_RUN_AFTER_S,
        "merge_days": MERGE_WINDOW_DAYS,
        "merge_days_before": 2 * MERGE_WINDOW_DAYS,
    }
    runs_on = agent_runs_enabled()
    rows: list[Any] = []
    failed = 0
    if runs_on:
        rows = (await db.execute(text(runs_now_sql(open_where)), binds)).fetchall()
        failed = int(
            (await db.execute(text(failed_today_sql(past_where)), binds)).scalar() or 0
        )
    members = sorted(
        (
            {
                "member": r.member,
                "member_name": r.member_name or None,
                "working": int(r.working or 0),
                "silent": int(r.silent or 0),
            }
            for r in rows
        ),
        key=lambda m: _sort_name(m["member_name"], m["member"]),
    )
    shown, hidden = filter_person_rows(members, allowed, key="member")
    runs: dict[str, Any] = {
        "on": runs_on,
        "working": sum(m["working"] for m in members),
        "silent": sum(m["silent"] for m in members),
        "failed_today": failed,
        "silent_after_minutes": SILENT_RUN_AFTER_S // 60,
        "members": shown[:MAX_NAMED],
    }
    if allowed is not None:
        runs["hidden_people"] = hidden

    pr = (await db.execute(text(merge_sql(past_where)), binds)).one()
    merged, closed = int(pr.merged or 0), int(pr.closed or 0)
    merged_before, closed_before = int(pr.merged_before or 0), int(pr.closed_before or 0)
    rate, before = merge_rate(merged, closed), merge_rate(merged_before, closed_before)
    merge = {
        "days": MERGE_WINDOW_DAYS,
        "merged": merged,
        "closed": closed,
        "rate": rate,
        "merged_before": merged_before,
        "closed_before": closed_before,
        "rate_before": before,
        "trend": merge_trend(rate, before),
    }

    tm = (
        await db.execute(
            text(run_time_sql(past_where, skip_current_week=skip_current_week)), binds,
        )
    ).one()
    finished = int(tm.finished or 0)
    time = {
        "finished": finished,
        "run_tasks": int(tm.run_tasks or 0),
        "median_hours": _hours(tm.median_hours),
        "total_hours": _hours(tm.total_hours),
        "basis": RUN_TIME_BASIS,
    }
    return {"runs": runs, "merge": merge, "time": time, "cost": cost_figure(finished)}
