"""Projects · the ``waiting`` report section — WS-27bn RA-3.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 C, the §5 row
"Waiting on a person", and §8 RA-3.

The coordination panel of the Overview. It answers "what waits on a person,
and on whom?" in four parts:

1. **Questions.** An open agent run in ``awaiting`` with an open question (an
   elicitation that no prompt answers). The oldest question comes first.
2. **Reviews.** A task that an agent moved into a review lane, or an open
   pull request on a task that an agent worked, with no person act since.
3. **Handoffs.** An open ``blocks`` link where an agent holds one task and a
   person holds the other.
4. **Answer time.** The median and the p90 from a question to its answer,
   over the report period, and the count of questions with no answer.

⚠️ **Each rule here is a rule that already exists, joined.** "Awaiting" is
`filters.AGENT_RUN_STATE_SQL`. "An open question" is
`agent_runs._OPEN_ELICITATIONS_SQL`, run as it is. "An agent act" is
`analytics.agent_act_sql`, the twin of `is_agent_act`. "Agent work" is
`agent_worked.AGENT_WORKED_SQL`. "Held by" is RA-1's `holder_labels_sql`. A
second copy of any of them would let this panel and the board disagree.

⚠️ **§7 rule 1.** Each list reads the open-work predicate of Load
(`load_open_where`), and the answer time reads the history predicate of
Throughput (`history_where`). Both carry the caller's visibility and the
triage exclusion, and the caller's tenant session runs every query. Nothing
here reads a tenant or a person from the request (R5).

⚠️ **The runs flag.** While ``agent_runs_enabled`` is off, the cards show no
agent pill and a member cannot answer a question. So the questions and the
answer time read nothing then, as `agent_summary.py` does for its counts.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from acb_auth import agent_oauth
from gateway.routes.projects.agent_dispatch import AGENT_PREFIX
from gateway.routes.projects.agent_runs import _OPEN_ELICITATIONS_SQL
from gateway.routes.projects.agent_worked import agent_worked_sql
from gateway.routes.projects.analytics import (
    HOLDER_AGENT,
    HOLDER_PERSON,
    MAX_NAMED,
    _starts_with,
    _window,
    activity_via_sql,
    agent_act_sql,
    history_where,
    holder_labels_sql,
    is_agent_act,
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.core import STARTED_CATEGORY
from gateway.routes.projects.filters import (
    AGENT_RUN_STATE_SQL,
    agent_runs_enabled,
    attach_parent_context,
)
from sqlalchemy import text

#: The run state that waits for a person (`filters.AGENT_RUN_STATE_SQL`).
AWAITING = "awaiting"

#: The word in the name of a review lane. The MCP protocol tells an agent to
#: pick "a status of the category in_progress whose name says review" when
#: its pull request opens (`agent_edge/prompts.py`), so the server reads the
#: same two facts.
REVIEW_WORD = "review"

#: The prefix of an automation actor, such as a workflow or GitHub. An act
#: with it is not a person's act. `github_merge._person_took_it_back` reads
#: the same prefix.
SYSTEM_PREFIX = "system:"

#: Why a task waits for a review.
REASON_LANE = "review_lane"
REASON_PR = "pull_request"

#: The side of a handoff link that the agent holds.
SIDE_BLOCKER = "blocker"
SIDE_BLOCKED = "blocked"


# ── The rules, in Python ─────────────────────────────────────────────────────
#
# Each function says one rule of the SQL below in Python. The pure suite
# (`test_projects_report_waiting.py`) pins them, and the R8 suite
# (`test_projects_report_waiting_r8.py`) pins the SQL to the same cases. As
# in RA-1, the SQL decides each list, and the body does not run a rule twice.


def is_open_question(state: str | None, open_question: Any) -> bool:
    """A run waits on a person: it reads ``awaiting`` and holds a question
    that no prompt answers (§5, "Waiting on a person")."""
    return state == AWAITING and open_question is not None


def is_review_lane(category: str | None, name: str | None) -> bool:
    """A status of the ``in_progress`` category whose name says review."""
    return category == STARTED_CATEGORY and REVIEW_WORD in str(name or "").lower()


def is_person_act(created_by: str | None, via: str | None) -> bool:
    """A person made the act: no agent (`is_agent_act`), no automation
    (``system:…``), and a real actor."""
    by = str(created_by or "").strip().lower()
    return bool(by) and not is_agent_act(created_by, via) and not by.startswith(SYSTEM_PREFIX)


def review_since(
    *,
    in_lane: bool,
    lane_at: datetime | None,
    lane_by: str | None,
    lane_via: str | None,
    pr_at: datetime | None,
    worked: bool,
    person_acts: Iterable[datetime],
) -> datetime | None:
    """When the task began to wait for a review, or None when it does not.

    Two arms. **The lane**: the task sits in a review lane and the latest
    status move, into it, was an agent act. **The pull request**: the task
    has an open pull request (``pr_at`` is the oldest one), and an agent
    worked the task. An arm waits only while no person acted after it. The
    task waits since the earlier arm that still waits.
    """
    acts = list(person_acts)
    arms: list[datetime] = []
    if in_lane and lane_at is not None and is_agent_act(lane_by, lane_via):
        arms.append(lane_at)
    if worked and pr_at is not None:
        arms.append(pr_at)
    waiting = [at for at in arms if not any(act > at for act in acts)]
    return min(waiting) if waiting else None


def handoff_side(blocker_holder: str | None, blocked_holder: str | None) -> str | None:
    """Which side of a ``blocks`` link the agent holds, or None.

    The link is a handoff only when an agent holds one task and a person
    holds the other. The labels are RA-1's (`classify_holder`).
    """
    if (blocker_holder, blocked_holder) == (HOLDER_AGENT, HOLDER_PERSON):
        return SIDE_BLOCKER
    if (blocker_holder, blocked_holder) == (HOLDER_PERSON, HOLDER_AGENT):
        return SIDE_BLOCKED
    return None


def percentile_cont(values: Iterable[float | None], fraction: float) -> float | None:
    """Postgres ``percentile_cont``: a linear step between the two nearest
    values. None is skipped, and no value gives None (never zero)."""
    xs = sorted(float(v) for v in values if v is not None)
    if not xs:
        return None
    pos = fraction * (len(xs) - 1)
    low, high = math.floor(pos), math.ceil(pos)
    return xs[low] + (xs[high] - xs[low]) * (pos - low)


def _round(hours: float | None) -> float | None:
    """Two places for transport. None stays None: no answer is not zero."""
    return None if hours is None else round(float(hours), 2)


def answer_time(pairs: Iterable[tuple[datetime, datetime | None]]) -> dict[str, Any]:
    """The answer time of ``(asked_at, answered_at)`` pairs.

    An unanswered question has no time, so it stays out of the median and
    the p90, and ``unanswered`` counts it. The SQL twin is
    :func:`answer_time_sql`.
    """
    asked = list(pairs)
    hours = [
        (answered - at).total_seconds() / 3600.0
        for at, answered in asked
        if answered is not None
    ]
    return {
        "asked": len(asked),
        "answered": len(hours),
        "unanswered": len(asked) - len(hours),
        "median_hours": _round(percentile_cont(hours, 0.5)),
        "p90_hours": _round(percentile_cont(hours, 0.9)),
    }


def age_words(hours: float | None) -> str:
    """An age as a person reads it: "under an hour", "5 hours", "3 days"."""
    if hours is None or hours < 1:
        return "under an hour"
    if hours < 48:
        n = round(hours)
        return f"{n} hour" if n == 1 else f"{n} hours"
    return f"{round(hours / 24)} days"


def summary_line(
    *, questions: int, oldest_hours: float | None, reviews: int, handoffs: int,
) -> str:
    """The one plain summary of the panel (§6.8 E5). The panel, the email and
    the chat print this text, so they cannot say two things."""
    parts: list[str] = []
    if questions == 1:
        parts.append(
            f"1 agent question waits for an answer. It is {age_words(oldest_hours)} old."
        )
    elif questions > 1:
        parts.append(
            f"{questions} agent questions wait for an answer."
            f" The oldest is {age_words(oldest_hours)} old."
        )
    if reviews == 1:
        parts.append("1 task waits for a review.")
    elif reviews > 1:
        parts.append(f"{reviews} tasks wait for a review.")
    if handoffs == 1:
        parts.append("1 handoff between a person and an agent is blocked.")
    elif handoffs > 1:
        parts.append(f"{handoffs} handoffs between people and agents are blocked.")
    return " ".join(parts) or "Nobody is waiting on a person."


def agent_of(
    run_via: str | None,
    run_member: str | None,
    act_by: str | None = None,
    act_via: str | None = None,
    agent_assignee: str | None = None,
) -> dict[str, Any]:
    """The agent of a row and the member it works for (§6.8 E2).

    The newest run of the task names both. Else an act through MCP names
    them: its stamp is the client, and its actor is the member. Else an
    in-app agent names only itself (``agent:<name>``), and it works for no
    member. The client name comes from `agent_oauth.client_label`, the one
    map, never from a name that the client chose.
    """
    if run_via:
        return {
            "agent": agent_oauth.client_label(run_via),
            "client_via": run_via,
            "member": run_member or None,
        }
    if str(act_via or "").lower().startswith("mcp:"):
        return {
            "agent": agent_oauth.client_label(act_via),
            "client_via": act_via,
            "member": act_by or None,
        }
    for name in (act_by, agent_assignee):
        if str(name or "").lower().startswith(AGENT_PREFIX):
            return {
                "agent": str(name)[len(AGENT_PREFIX):] or "Agent",
                "client_via": None,
                "member": None,
            }
    return {"agent": "Agent", "client_via": None, "member": None}


# ── The rules, in SQL ────────────────────────────────────────────────────────


def review_lane_sql(status: str = "s") -> str:
    """The SQL twin of :func:`is_review_lane`, over the status alias. It
    binds ``:started_cat``. ``review`` holds no LIKE wildcard."""
    return (
        f"({status}.category = :started_cat"
        f" AND lower({status}.name) LIKE '%{REVIEW_WORD}%')"
    )


def person_act_sql(alias: str) -> str:
    """The SQL twin of :func:`is_person_act`, over a `pm_activities` alias."""
    return (
        f"(btrim(coalesce({alias}.created_by, '')) <> ''"
        f" AND NOT {agent_act_sql(f'{alias}.created_by', activity_via_sql(alias))}"
        f" AND NOT {_starts_with(f'{alias}.created_by', SYSTEM_PREFIX)})"
    )


def _no_person_act_since(task: str, at: str) -> str:
    """No person acted on the task ``task`` after the time ``at``."""
    return (
        f"NOT EXISTS (SELECT 1 FROM pm_activities pa"
        f" WHERE pa.task_id = {task} AND pa.deleted_at IS NULL"
        f" AND pa.created_at > {at} AND {person_act_sql('pa')})"
    )


def _member_name(email: str) -> str:
    """The directory name of a member, as `filters.agent_state_sql` reads it."""
    return (
        f"(SELECT u.display_name FROM app_user u"
        f" WHERE lower(u.email) = lower({email}) LIMIT 1)"
    )


def questions_sql(open_where: str) -> str:
    """Each run in ``awaiting`` on an open task of the scope.

    ``open_where`` is :func:`analytics.load_open_where`. The question of each
    run comes from `agent_runs._OPEN_ELICITATIONS_SQL`, in a second query
    over these run ids, so the rule of an open question has one text.
    """
    return (
        f"SELECT r.id AS run_id, t.id, r.client_via, r.member_email,"
        f"       {_member_name('r.member_email')} AS member_name,"
        f"       t.title, t.task_number, t.project_id, t.parent_task_id,"
        f"       p.name AS project_name"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_projects p ON p.id = t.project_id"
        f" WHERE {open_where}"
        f"   AND ({AGENT_RUN_STATE_SQL}) = '{AWAITING}'"
    )


def reviews_sql(open_where: str) -> str:
    """The open tasks that wait for a review: the SQL twin of
    :func:`review_since`, one row for each task, the longest wait first.

    It binds ``:started_cat`` and ``:max_named``. ``total`` counts every
    task before the cap.
    """
    lane_ok = (
        f"({review_lane_sql('s')} AND lm.act_at IS NOT NULL"
        f" AND {agent_act_sql('lm.act_by', 'lm.act_via')}"
        f" AND {_no_person_act_since('t.id', 'lm.act_at')})"
    )
    pr_ok = f"(pr.pr_at IS NOT NULL AND {_no_person_act_since('t.id', 'pr.pr_at')})"
    return (
        f"WITH cand AS ("
        f"  SELECT t.id, t.title, t.task_number, t.project_id, t.parent_task_id,"
        f"         p.name AS project_name,"
        f"         CASE WHEN {lane_ok} THEN lm.act_at END AS lane_since,"
        f"         CASE WHEN {pr_ok} THEN pr.pr_at END AS pr_since,"
        f"         lm.act_by, lm.act_via, pr.pr_url,"
        f"         nr.client_via AS run_via, nr.member_email AS run_member,"
        f"         {_member_name('nr.member_email')} AS run_member_name"
        f"    FROM pm_tasks t"
        f"    JOIN pm_task_statuses s ON s.id = t.status_id"
        f"    JOIN pm_projects p ON p.id = t.project_id"
        # The latest status move of the task. A task in a review lane got
        # there through this move.
        f"    LEFT JOIN LATERAL ("
        f"      SELECT a.created_at AS act_at, a.created_by AS act_by,"
        f"             {activity_via_sql('a')} AS act_via"
        f"        FROM pm_activities a"
        f"       WHERE a.task_id = t.id AND a.type = 'status_change'"
        f"         AND a.deleted_at IS NULL"
        f"       ORDER BY a.created_at DESC, a.id DESC LIMIT 1"
        f"    ) lm ON TRUE"
        # The oldest open pull request, only on a task that an agent worked.
        # A draft is not ready for a review.
        f"    LEFT JOIN LATERAL ("
        f"      SELECT rf.created_at AS pr_at, rf.url AS pr_url"
        f"        FROM pm_task_refs rf"
        f"       WHERE rf.task_id = t.id AND rf.kind = 'pull_request'"
        f"         AND rf.state = 'open'"
        f"       ORDER BY rf.created_at ASC, rf.id ASC LIMIT 1"
        f"    ) pr ON {agent_worked_sql('t.id', 'now()')}"
        f"    LEFT JOIN LATERAL ("
        f"      SELECT x.client_via, x.member_email FROM pm_agent_runs x"
        f"       WHERE x.task_id = t.id"
        f"       ORDER BY x.created_at DESC, x.id DESC LIMIT 1"
        f"    ) nr ON TRUE"
        f"   WHERE {open_where}"
        f")"
        f" SELECT c.*, least(c.lane_since, c.pr_since) AS since,"
        f"        count(*) OVER () AS total"
        f"   FROM cand c"
        f"  WHERE c.lane_since IS NOT NULL OR c.pr_since IS NOT NULL"
        f"  ORDER BY since ASC, c.id ASC"
        f"  LIMIT :max_named"
    )


def handoffs_sql(blocked_where: str, blocking_where: str) -> str:
    """The open ``blocks`` links where an agent holds one task and a person
    holds the other: the SQL twin of :func:`handoff_side`.

    ``blocked_where`` selects the blocked tasks (the scope), and
    ``blocking_where`` the blocking tasks (any open task the reader can
    see). RA-1's `holder_labels_sql` labels both. It binds ``:max_named``.
    """
    agent_side = f"CASE WHEN kh.label = '{HOLDER_AGENT}' THEN b.id ELSE t.id END"
    person_side = f"CASE WHEN kh.label = '{HOLDER_AGENT}' THEN t.id ELSE b.id END"
    return (
        f"WITH blocked AS ({holder_labels_sql(blocked_where)}),"
        f" blocking AS ({holder_labels_sql(blocking_where)})"
        f" SELECT l.created_at AS since,"
        f"        t.id, t.title, t.task_number, t.project_id, t.parent_task_id,"
        f"        tp.name AS project_name,"
        f"        b.id AS blocker_id, b.title AS blocker_title,"
        f"        b.task_number AS blocker_number,"
        f"        kh.label AS blocker_holder, bh.label AS blocked_holder,"
        f"        ar.client_via AS run_via, ar.member_email AS run_member,"
        f"        {_member_name('ar.member_email')} AS run_member_name,"
        f"        aa.assignee AS agent_assignee, pa.assignee AS person,"
        f"        {_member_name('pa.assignee')} AS person_name,"
        f"        count(*) OVER () AS total"
        f"   FROM pm_task_links l"
        f"   JOIN blocked bh ON bh.task_id = l.target_task_id"
        f"   JOIN blocking kh ON kh.task_id = l.source_task_id"
        f"   JOIN pm_tasks t ON t.id = l.target_task_id"
        f"   JOIN pm_projects tp ON tp.id = t.project_id"
        f"   JOIN pm_tasks b ON b.id = l.source_task_id"
        f"   LEFT JOIN LATERAL ("
        f"     SELECT x.client_via, x.member_email FROM pm_agent_runs x"
        f"      WHERE x.task_id = {agent_side}"
        f"      ORDER BY x.created_at DESC, x.id DESC LIMIT 1"
        f"   ) ar ON TRUE"
        f"   LEFT JOIN LATERAL ("
        f"     SELECT min(x.assignee) AS assignee FROM pm_task_assignees x"
        f"      WHERE x.task_id = {agent_side}"
        f"        AND {_starts_with('x.assignee', AGENT_PREFIX)}"
        f"   ) aa ON TRUE"
        f"   LEFT JOIN LATERAL ("
        f"     SELECT min(x.assignee) AS assignee FROM pm_task_assignees x"
        f"      WHERE x.task_id = {person_side}"
        f"        AND btrim(coalesce(x.assignee, '')) <> ''"
        f"        AND NOT {_starts_with('x.assignee', AGENT_PREFIX)}"
        f"   ) pa ON TRUE"
        f"  WHERE l.link_type = 'blocks'"
        f"    AND ((kh.label = '{HOLDER_AGENT}' AND bh.label = '{HOLDER_PERSON}')"
        f"      OR (kh.label = '{HOLDER_PERSON}' AND bh.label = '{HOLDER_AGENT}'))"
        f"  ORDER BY l.created_at ASC, l.id ASC"
        f"  LIMIT :max_named"
    )


def answer_time_sql(past_where: str, *, skip_current_week: bool = False) -> str:
    """The SQL twin of :func:`answer_time`, over the questions asked in the
    report window on the tasks of ``past_where``.

    The answer to a question is the prompt whose ``in_reply_to`` names it.
    The server sets that link, and never takes it from input (EA12-7).
    ``percentile_cont`` skips a NULL, so an unanswered question stays out of
    the median and the p90. The window is `analytics._window`, which binds
    ``:weeks``, so the period is the one that Throughput reads.
    """
    start, end = _window(skip_current_week)
    answered = (
        "(SELECT min(p.created_at) FROM pm_agent_run_activities p"
        "  WHERE p.in_reply_to = e.id AND p.type = 'prompt')"
    )
    return (
        f"SELECT count(*) AS asked, count(q.answered_at) AS answered,"
        f"       percentile_cont(0.5) WITHIN GROUP (ORDER BY q.hours)"
        f"         AS median_hours,"
        f"       percentile_cont(0.9) WITHIN GROUP (ORDER BY q.hours)"
        f"         AS p90_hours"
        f"  FROM ("
        f"    SELECT a.answered_at,"
        f"           extract(epoch FROM (a.answered_at - a.asked_at)) / 3600.0"
        f"             AS hours"
        f"      FROM ("
        f"        SELECT e.created_at AS asked_at, {answered} AS answered_at"
        f"          FROM pm_agent_run_activities e"
        f"          JOIN pm_tasks t ON t.id = e.task_id"
        f"         WHERE e.type = 'elicitation'"
        f"           AND e.created_at >= {start} AND e.created_at < {end}"
        f"           AND ({past_where})"
        f"      ) a"
        f"  ) q"
    )


def agent_runs_seen_sql(past_where: str) -> str:
    """True when a task of the scope that the reader can see has an agent
    run of any status. The Overview hides the panel without one."""
    return (
        f"SELECT EXISTS (SELECT 1 FROM pm_agent_runs r"
        f" JOIN pm_tasks t ON t.id = r.task_id WHERE {past_where}) AS seen"
    )


def _links_to(column: str) -> str:
    """The task ``t`` is the ``column`` end of a ``blocks`` link."""
    return (
        f"EXISTS (SELECT 1 FROM pm_task_links lx WHERE lx.{column} = t.id"
        f" AND lx.link_type = 'blocks')"
    )


# ── The body ─────────────────────────────────────────────────────────────────


def _hours_between(clock: datetime, at: datetime | None) -> float | None:
    return None if at is None else round((clock - at).total_seconds() / 3600.0, 2)


def _task_row(row: Any, clock: datetime, since: datetime | None) -> dict[str, Any]:
    """The task half of one row: what opens it, and how long it waits."""
    return {
        "id": str(row.id),
        "task_number": int(row.task_number) if row.task_number is not None else None,
        "title": row.title,
        "project_id": str(row.project_id),
        "project_name": row.project_name,
        "parent_task_id": (
            str(row.parent_task_id) if getattr(row, "parent_task_id", None) else None
        ),
        "since": since.isoformat() if since else None,
        "age_hours": _hours_between(clock, since),
    }


async def _questions(
    db: Any, open_where: str, params: dict[str, Any], clock: datetime,
) -> list[dict[str, Any]]:
    runs = (await db.execute(text(questions_sql(open_where)), params)).fetchall()
    if not runs:
        return []
    open_questions = {
        str(q.run_id): q
        for q in (
            await db.execute(
                text(_OPEN_ELICITATIONS_SQL), {"ids": [str(r.run_id) for r in runs]},
            )
        ).fetchall()
    }
    # The SQL already chose the runs in `awaiting`. A run waits on a person
    # when it also holds an open question. One gate for each half, so a
    # mutation of either one shows.
    waiting = [
        (open_questions[str(run.run_id)], run)
        for run in runs
        if str(run.run_id) in open_questions
    ]
    # The oldest question first. The run id breaks a tie, so two renders of
    # one state list the same rows.
    waiting.sort(key=lambda pair: (pair[0].created_at, str(pair[1].run_id)))
    return [
        {
            **_task_row(run, clock, question.created_at),
            **agent_of(run.client_via, run.member_email),
            "member_name": run.member_name or None,
        }
        for question, run in waiting
    ]


async def waiting_body(
    db: Any,
    vis: Any,
    *,
    project_id: str | None,
    include_subtree: bool,
    weeks: int,
    skip_current_week: bool,
) -> dict[str, Any]:
    """The ``waiting`` report section, on a session and a visibility that the
    caller resolved (`projects_reports.md` §8 RA-3).

    Each list names up to :data:`MAX_NAMED` rows, the longest wait first, and
    a ``*_total`` that counts every row, so a reader sees what the cap cut.
    The lists read the state now. The answer time reads the report period.
    ``summary`` is the one sentence of the panel (§6.8 E5).
    """
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)
    open_where = load_open_where(scope_sql, vis)
    past_where = history_where(scope_sql, vis)
    params: dict[str, Any] = {
        **load_params(vis, project_id),
        "started_cat": STARTED_CATEGORY,
        "max_named": MAX_NAMED,
        "weeks": int(weeks),
    }
    # One clock for every age, the database's, as the run states read it.
    clock = (await db.execute(text("SELECT now()"))).scalar()
    runs_on = agent_runs_enabled()

    questions = await _questions(db, open_where, params, clock) if runs_on else []

    review_rows = (await db.execute(text(reviews_sql(open_where)), params)).fetchall()
    reviews = [
        {
            **_task_row(r, clock, r.since),
            "reasons": [
                reason for reason, at in ((REASON_LANE, r.lane_since), (REASON_PR, r.pr_since))
                if at is not None
            ],
            "pr_url": r.pr_url if r.pr_since is not None else None,
            **agent_of(r.run_via, r.run_member, r.act_by, r.act_via),
            "member_name": r.run_member_name if r.run_via else None,
        }
        for r in review_rows
    ]

    blocked_where = f"({open_where}) AND {_links_to('target_task_id')}"
    blocking_where = f"({load_open_where('TRUE', vis)}) AND {_links_to('source_task_id')}"
    handoff_rows = (
        await db.execute(text(handoffs_sql(blocked_where, blocking_where)), params)
    ).fetchall()
    handoffs = [
        {
            **_task_row(h, clock, h.since),
            "blocker_id": str(h.blocker_id),
            "blocker_number": (
                int(h.blocker_number) if h.blocker_number is not None else None
            ),
            "blocker_title": h.blocker_title,
            "agent_side": handoff_side(h.blocker_holder, h.blocked_holder),
            **agent_of(h.run_via, h.run_member, agent_assignee=h.agent_assignee),
            "member_name": h.run_member_name if h.run_via else None,
            "person": h.person,
            "person_name": h.person_name or None,
        }
        for h in handoff_rows
    ]

    if runs_on:
        found = (
            await db.execute(
                text(answer_time_sql(past_where, skip_current_week=skip_current_week)),
                params,
            )
        ).one()
        asked, answered = int(found.asked or 0), int(found.answered or 0)
        answer = {
            "asked": asked,
            "answered": answered,
            "unanswered": asked - answered,
            "median_hours": _round(found.median_hours),
            "p90_hours": _round(found.p90_hours),
        }
    else:
        answer = answer_time([])

    seen = bool(
        (await db.execute(text(agent_runs_seen_sql(past_where)), params)).scalar()
    )

    # D-PM-38: the same "↳ Parent" that every flat row carries.
    await attach_parent_context(db, vis, [*questions, *reviews, *handoffs])

    questions_total = len(questions)
    reviews_total = int(review_rows[0].total) if review_rows else 0
    handoffs_total = int(handoff_rows[0].total) if handoff_rows else 0
    oldest = questions[0]["age_hours"] if questions else None
    return {
        "summary": summary_line(
            questions=questions_total, oldest_hours=oldest,
            reviews=reviews_total, handoffs=handoffs_total,
        ),
        "agent_runs_seen": seen,
        "questions": questions[:MAX_NAMED],
        "questions_total": questions_total,
        "reviews": reviews,
        "reviews_total": reviews_total,
        "handoffs": handoffs,
        "handoffs_total": handoffs_total,
        "answer_time": answer,
        "max_rows": MAX_NAMED,
    }
