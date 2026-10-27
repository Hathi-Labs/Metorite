"""Projects · the ``agent_runs`` report section — WS-27bn RA-4.

Spec: ``project-docs/specs/projects_reports.md`` §6.8 B ("Agent runs"), E
and F, the §5 rows "Run outcome", "Success rate", "Silent run", "Run size"
and "Long run", and §8 RA-4.

The panel answers "can I trust agents with this work?" in four parts:

1. **Run outcomes.** The runs that ENDED in the last 14 days, by outcome:
   completed, failed, stopped, abandoned, released and closed by Metorite. A
   daily series feeds the small stacked chart (Paperclip's Run Activity).
2. **The success rate.** Completed over completed, failed and abandoned, with
   the trend against the 14 days before. A stop, a release and a server close
   stay out. See :func:`success_rate`.
3. **Silent runs now.** The open runs in ``in_progress`` that the agent has
   not spoken in for :data:`analytics_agents.SILENT_RUN_AFTER_S`.
4. **Run sizes.** The completed runs of the window, by agent time, and the
   long ones (4 hours or more), which may point at a poorly specified task.

⚠️ **Each rule is a rule that already exists, joined.** The silent rule is
RA-2's `analytics_agents.silent_run_sql` and its ONE constant. The agent time
of a run is RA-2's `analytics_agents.run_time_joins_sql`, the text that the
KPI tile reads. The outcomes are `agent_runs.TERMINAL_STATUSES`. "Seen" is
RA-3's `analytics_waiting.agent_runs_seen_sql`. The agent of a row and its
member are `analytics_waiting.agent_of`. A second copy of any of them would
let this panel and the KPI strip disagree.

⚠️ **"Ended" is ``ended_at``.** Every terminal move stamps it
(`agent_runs._move_run`), and a reopen clears it. So it is the time that the
outcome was decided. A takeover three days after the agent went quiet ended
the run at the takeover. RA-2's run end (`_RUN_END`, the agent's last act) is
the end of the agent's TIME, so the sizes read it and the outcomes do not.

⚠️ **§7 rule 1.** The outcomes and the sizes read Throughput's history
predicate (`history_where`), so a run on a task that is done or archived now
still counts in its window. The silent list reads Load's open-work predicate
(`load_open_where`), as the KPI tile does. The caller's tenant session runs
every query. Nothing here reads a tenant or a person from the request (R5).

⚠️ **The runs flag.** While ``agent_runs_enabled`` is off, no run moves, and
the section reads no outcome, no silent run and no size, as
`agent_summary.py` does for its counts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from gateway.routes.projects.analytics import (
    MAX_NAMED,
    history_where,
    load_open_where,
    load_params,
    scope_clause,
)
from gateway.routes.projects.analytics_agents import (
    RUN_AGENT_SECONDS_SQL,
    RUN_MEASURED_SQL,
    SILENT_RUN_AFTER_S,
    merge_trend,
    run_time_joins_sql,
    silent_run_sql,
    silent_since_sql,
)
from gateway.routes.projects.analytics_waiting import (
    _member_name,
    _round,
    _task_row,
    agent_of,
    agent_runs_seen_sql,
)
from gateway.routes.projects.filters import agent_runs_enabled, attach_parent_context
from sqlalchemy import text

# ── The vocabulary ───────────────────────────────────────────────────────────

#: The window of the outcomes and the sizes, in UTC days, today included.
#: The trend compares the same number of days before it.
RUN_WINDOW_DAYS = 14

#: The five outcomes of a run, in the order the panel draws them.
#:
#: ⚠️ **The terminal statuses of `agent_runs.py`, in a fixed order.**
#: `test_projects_report_agent_runs.py` holds the set equal to
#: `agent_runs.TERMINAL_STATUSES`, so a sixth terminal status fails there
#: before it goes uncounted.
RUN_OUTCOMES: tuple[str, ...] = (
    "completed", "failed", "stopped", "abandoned", "released", "server_closed",
)

#: The outcomes that are a STATUS of a run (``agent_runs.TERMINAL_STATUSES``).
#: ``server_closed`` is a status ``completed`` that the SERVER wrote, so it is
#: an outcome of this panel and never a status.
RUN_STATUS_OUTCOMES: tuple[str, ...] = RUN_OUTCOMES[:-1]

#: RA-4b. A run that Metorite closed: the sweep after its task was done,
#: cancelled or archived (``task_close``), or the approved archive
#: (``archive_close``). The agent did not finish it, so it is no success.
OUTCOME_SERVER_CLOSED = "server_closed"

#: The outcome that is a success.
OUTCOME_SUCCESS = "completed"

#: The outcomes that the success rate counts. A run that completed is a
#: success. A run that failed (the agent reported an error) or that another
#: session took over after its lease (the agent went quiet) is not.
RATED_OUTCOMES: tuple[str, ...] = ("completed", "failed", "abandoned")

#: The outcomes that the success rate leaves out, and why.
#:
#: ⚠️ **A person's act is not an agent's failure.** A member's Stop ends a run
#: that the member chose to end (`member_stop`). A release gives the task back
#: with no claim of an outcome: the holder, a person or an admin may do it,
#: and the MCP protocol tells an agent to release a task it will not finish.
#: Neither says whether the agent could do the work.
#:
#: ⚠️ **A server close is not an agent's success** (RA-4b). A sweep that
#: closes the run of a task that a person cancelled says nothing of the
#: agent's work, and it would raise the rate.
UNRATED_OUTCOMES: tuple[str, ...] = ("stopped", "released", "server_closed")

#: The four sizes of a completed run, as ``(key, the first hour that is too
#: long for it)``. The last size has no upper edge.
SIZE_BUCKETS: tuple[tuple[str, float | None], ...] = (
    ("under_15m", 0.25),
    ("under_1h", 1.0),
    ("under_4h", 4.0),
    ("over_4h", None),
)
#: The size of a completed run with no agent time: it never started, or its
#: end comes before its start.
SIZE_UNMEASURED = "unmeasured"

#: A long run: 4 hours of agent time or more, the size after "under 4 hours".
LONG_RUN_HOURS = 4.0
#: How many long runs the panel names, the longest first.
LONG_RUNS_NAMED = 5
#: What a long run may mean, in the words of the spec (§8 RA-4).
LONG_RUN_FLAG = "maybe a poorly specified task"


# ── The rules, in Python ─────────────────────────────────────────────────────
#
# Each function says one rule of the SQL below in Python. The pure suite
# (`test_projects_report_agent_runs.py`) pins them, and the R8 suite
# (`test_projects_report_agent_runs_r8.py`) pins the SQL to the same cases.


def outcome_counts(rows: Iterable[tuple[str | None, int | None]]) -> dict[str, int]:
    """Each outcome with its count. An outcome with no row is zero, and a
    status that is not an outcome (an open run) is not counted."""
    counts = dict.fromkeys(RUN_OUTCOMES, 0)
    for status, n in rows:
        if status in counts:
            counts[status] += int(n or 0)
    return counts


def success_rate(outcomes: Mapping[str, int]) -> float | None:
    """Completed over completed, failed and abandoned (§5, "Success rate").

    None when no run of the window completed, failed or was abandoned, never
    a zero. ⚠️ No target: the rate says what happened, and the trend says
    which way it moved (§6.8 E4).
    """
    rated = sum(int(outcomes.get(o, 0) or 0) for o in RATED_OUTCOMES)
    if rated == 0:
        return None
    return round(int(outcomes.get(OUTCOME_SUCCESS, 0) or 0) / rated, 4)


def size_bucket(hours: float | None) -> str:
    """The size of one completed run by its agent time (§5, "Run size")."""
    if hours is None:
        return SIZE_UNMEASURED
    for key, below in SIZE_BUCKETS:
        if below is None or hours < below:
            return key
    return SIZE_BUCKETS[-1][0]  # pragma: no cover — the last size has no edge


def is_long_run(hours: float | None) -> bool:
    """A run of :data:`LONG_RUN_HOURS` of agent time or more (§5, "Long run")."""
    return hours is not None and hours >= LONG_RUN_HOURS


def size_counts(hours: Iterable[float | None]) -> dict[str, int]:
    """The Python twin of :func:`sizes_sql`: each size with its count."""
    counts = {key: 0 for key, _ in SIZE_BUCKETS}
    counts[SIZE_UNMEASURED] = 0
    for h in hours:
        counts[size_bucket(h)] += 1
    return counts


def _percent(rate: float) -> str:
    return f"{round(rate * 100)}%"


def _runs(n: int, word: str = "run") -> str:
    return f"1 {word}" if n == 1 else f"{n} {word}s"


def success_line(success: Mapping[str, Any], days: int = RUN_WINDOW_DAYS) -> str | None:
    """The success rate and its trend in words, or None with no rate.

    ⚠️ It says which way the rate moved, and never how far it is from 100%
    (§6.8 E4).
    """
    rate, before, trend = success.get("rate"), success.get("rate_before"), success.get("trend")
    if rate is None:
        return None
    line = f"The success rate is {_percent(rate)}"
    if trend == "flat":
        return f"{line}, the same as in the {days} days before."
    if trend in ("up", "down") and before is not None:
        return f"{line}, {trend} from {_percent(before)} in the {days} days before."
    return f"{line}."


def long_line(count: int) -> str | None:
    """The flag of the long runs, with its count, or None with none."""
    if count <= 0:
        return None
    return (
        f"{_runs(count, 'completed run')} took {int(LONG_RUN_HOURS)} hours or more"
        f" of agent time: {LONG_RUN_FLAG}."
    )


def summary_line(
    *, on: bool, ended: int, success: Mapping[str, Any], silent: int, long_runs: int,
    days: int = RUN_WINDOW_DAYS,
) -> str:
    """The one plain summary of the panel (§6.8 E5). The panel, the email and
    the chat print this text, so they cannot say two things."""
    if not on:
        return "Agent runs are off for this organization, so no run is counted."
    if ended == 0:
        parts = [f"No agent run ended in the last {days} days."]
    else:
        parts = [f"{_runs(ended, 'agent run')} ended in the last {days} days."]
    if line := success_line(success, days):
        parts.append(line)
    if silent == 1:
        parts.append("1 run is silent now.")
    elif silent > 1:
        parts.append(f"{silent} runs are silent now.")
    if line := long_line(long_runs):
        parts.append(line)
    return " ".join(parts)


# ── The rules, in SQL ────────────────────────────────────────────────────────

#: Midnight today, in UTC, as a ``timestamp`` (no time zone). The days of the
#: series step from it, so a session time zone with a summer hour moves
#: nothing.
_DAY_NOW = "date_trunc('day', now() AT TIME ZONE 'UTC')"


def window_start_sql(before: bool = False) -> str:
    """The first instant of the window, as a ``timestamptz``: midnight UTC,
    ``:run_days - 1`` days ago. With ``before``, the first instant of the
    window before it. It binds ``:run_days``."""
    back = "2 * :run_days - 1" if before else ":run_days - 1"
    return f"(({_DAY_NOW} - make_interval(days => {back})) AT TIME ZONE 'UTC')"


def window_start_day(clock: datetime) -> date:
    """The first UTC day of the window: the Python twin of
    :func:`window_start_sql`, on the database clock."""
    return clock.astimezone(UTC).date() - timedelta(days=RUN_WINDOW_DAYS - 1)


#: RA-4b. True when the run ``r`` ended by a server close: it is ``completed``,
#: and the newest of its server-close lines and member prompts is a server
#: close. ⚠️ A prompt after the close REOPENED the run, so the later end is the
#: agent's, and the close line is not the last word. A late summary of the
#: agent after the close is neither, so the run stays closed by Metorite.
#: `agent_runs._system_activity` writes ``content.server_close`` on the system
#: line of ``task_close`` and ``archive_close``, and no column holds it.
SERVER_CLOSED_SQL = (
    "(r.status = 'completed' AND coalesce((SELECT a.content->>'server_close' IS NOT NULL"
    " FROM pm_agent_run_activities a WHERE a.run_id = r.id"
    " AND (a.content->>'server_close' IS NOT NULL OR a.type = 'prompt')"
    " ORDER BY a.seq DESC LIMIT 1), false))"
)

#: The outcome of the run ``r``: its status, or ``server_closed``.
OUTCOME_SQL = (
    f"CASE WHEN {SERVER_CLOSED_SQL} THEN '{OUTCOME_SERVER_CLOSED}' ELSE r.status END"
)


def server_closed(acts: Iterable[tuple[int, str, bool]], status: str) -> bool:
    """The Python twin of :data:`SERVER_CLOSED_SQL`. ``acts`` holds ``(seq,
    type, has the server_close marker)`` for each activity of the run."""
    if status != "completed":
        return False
    last = None
    for _seq, kind, marked in sorted(acts, key=lambda a: a[0]):
        if marked or kind == "prompt":
            last = marked
    return bool(last)


def _ended_runs(past_where: str) -> str:
    """The runs on the tasks of ``past_where`` with an outcome. It binds
    ``:outcomes``."""
    return (
        f"FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f" WHERE ({past_where})"
        f"   AND r.status = ANY(CAST(:outcomes AS text[]))"
        f"   AND r.ended_at IS NOT NULL"
    )


def outcome_series_sql(past_where: str) -> str:
    """Each UTC day of the window, with the runs that ended on it, by outcome.

    A quiet day is a zero, never an absent row, as `weekly_sql` rules. The
    days add up to the outcomes of the window, because the window starts at
    the first day of the series. It binds ``:run_days`` and ``:outcomes``.
    """
    counts = ", ".join(
        f"count(e.id) FILTER (WHERE e.outcome = '{o}') AS {o}" for o in RUN_OUTCOMES
    )
    return (
        f"WITH days AS ("
        f"  SELECT generate_series({_DAY_NOW} - make_interval(days => :run_days - 1),"
        f"    {_DAY_NOW}, interval '1 day') AS day"
        f")"
        f" SELECT d.day AS day, {counts}"
        f"   FROM days d"
        f"   LEFT JOIN (SELECT r.id, {OUTCOME_SQL} AS outcome, r.ended_at"
        f"                {_ended_runs(past_where)}"
        f"                AND r.ended_at >= {window_start_sql()}) e"
        f"     ON date_trunc('day', e.ended_at AT TIME ZONE 'UTC') = d.day"
        f"  GROUP BY d.day"
        f"  ORDER BY d.day"
    )


def outcomes_before_sql(past_where: str) -> str:
    """The outcomes of the window before, for the trend of the success rate."""
    return (
        f"SELECT {OUTCOME_SQL} AS status, count(*) AS n {_ended_runs(past_where)}"
        f"   AND r.ended_at >= {window_start_sql(before=True)}"
        f"   AND r.ended_at < {window_start_sql()}"
        f" GROUP BY 1"
    )


def silent_runs_sql(open_where: str) -> str:
    """The silent runs on the open tasks of the scope, the longest silence
    first. ``total`` counts every one before the cap.

    The rule is RA-2's :func:`analytics_agents.silent_run_sql`, and the age
    reads its own clock, :func:`analytics_agents.silent_since_sql`. It binds
    ``:silent_s`` and ``:max_named``.
    """
    return (
        f"SELECT r.id AS run_id, t.id, t.title, t.task_number, t.project_id,"
        f"       t.parent_task_id, p.name AS project_name,"
        f"       r.client_via, r.member_email,"
        f"       {_member_name('r.member_email')} AS member_name,"
        f"       {silent_since_sql('r')} AS quiet_since,"
        f"       count(*) OVER () AS total"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f"  JOIN pm_task_statuses s ON s.id = t.status_id"
        f"  JOIN pm_projects p ON p.id = t.project_id"
        f" WHERE ({open_where})"
        f"   AND {silent_run_sql('r')}"
        f" ORDER BY quiet_since ASC, r.id ASC"
        f" LIMIT :max_named"
    )


def size_bucket_sql(hours: str) -> str:
    """The SQL twin of :func:`size_bucket`, over an hours expression."""
    arms = " ".join(
        f"WHEN {hours} < {below} THEN '{key}'" for key, below in SIZE_BUCKETS if below is not None
    )
    return (
        f"CASE WHEN {hours} IS NULL THEN '{SIZE_UNMEASURED}' {arms}"
        f" ELSE '{SIZE_BUCKETS[-1][0]}' END"
    )


def _sized_runs_sql(past_where: str) -> str:
    """The completed runs that ended in the window, each with its agent time
    in hours, or NULL when it has none. The agent time is RA-2's text
    (:func:`analytics_agents.run_time_joins_sql`)."""
    return (
        f"SELECT r.id AS run_id, r.client_via, r.member_email, r.ended_at,"
        f"       t.id, t.title, t.task_number, t.project_id, t.parent_task_id,"
        f"       CASE WHEN {RUN_MEASURED_SQL} THEN {RUN_AGENT_SECONDS_SQL} / 3600.0 END"
        f"         AS hours"
        f"  FROM pm_agent_runs r"
        f"  JOIN pm_tasks t ON t.id = r.task_id"
        f" {run_time_joins_sql()}"
        f" WHERE ({past_where})"
        f"   AND r.status = '{OUTCOME_SUCCESS}'"
        f"   AND NOT {SERVER_CLOSED_SQL}"
        f"   AND r.ended_at >= {window_start_sql()}"
    )


def sizes_sql(past_where: str) -> str:
    """Each size with its count of completed runs (§5, "Run size")."""
    return (
        f"WITH sized AS ({_sized_runs_sql(past_where)})"
        f" SELECT {size_bucket_sql('z.hours')} AS bucket, count(*) AS n"
        f"   FROM sized z"
        f"  GROUP BY 1"
    )


def long_runs_sql(past_where: str) -> str:
    """The long runs, the longest first (§5, "Long run"). It binds
    ``:long_hours`` and ``:long_named``."""
    return (
        f"WITH sized AS ({_sized_runs_sql(past_where)})"
        f" SELECT z.*, p.name AS project_name,"
        f"        {_member_name('z.member_email')} AS member_name"
        f"   FROM sized z"
        f"   JOIN pm_projects p ON p.id = z.project_id"
        f"  WHERE z.hours >= :long_hours"
        f"  ORDER BY z.hours DESC, z.run_id ASC"
        f"  LIMIT :long_named"
    )


# ── The body ─────────────────────────────────────────────────────────────────


def _day_row(row: Any) -> dict[str, Any]:
    """One day of the series: each outcome and the total. The server adds."""
    counts = {o: int(getattr(row, o, 0) or 0) for o in RUN_OUTCOMES}
    return {"day": row.day.date().isoformat(), **counts, "total": sum(counts.values())}


def _success(outcomes: Mapping[str, int], before: Mapping[str, int]) -> dict[str, Any]:
    rate, rate_before = success_rate(outcomes), success_rate(before)
    return {
        "rate": rate,
        "completed": int(outcomes.get(OUTCOME_SUCCESS, 0)),
        "rated": sum(int(outcomes.get(o, 0)) for o in RATED_OUTCOMES),
        "rate_before": rate_before,
        "rated_before": sum(int(before.get(o, 0)) for o in RATED_OUTCOMES),
        # RA-2's trend rule, as the merge rate reads it: up, down or flat.
        "trend": merge_trend(rate, rate_before),
    }


def _empty_sizes() -> dict[str, int]:
    return size_counts(())


def _who(row: Any) -> dict[str, Any]:
    """The agent of a run and the member it works for (§6.8 E2)."""
    return {
        "run_id": str(row.run_id),
        **agent_of(row.client_via, row.member_email),
        "member_name": row.member_name or None,
    }


async def agent_runs_body(
    db: Any,
    vis: Any,
    *,
    project_id: str | None,
    include_subtree: bool,
) -> dict[str, Any]:
    """The ``agent_runs`` report section, on a session and a visibility that
    the caller resolved (`projects_reports.md` §8 RA-4).

    It reads its own window of :data:`RUN_WINDOW_DAYS` and never the report
    period, as the merge rate of RA-2 does. The silent list names up to
    :data:`MAX_NAMED` rows beside ``silent_total``, and the long list
    :data:`LONG_RUNS_NAMED` beside ``long_total``. ``summary`` is the one
    sentence of the panel (§6.8 E5).
    """
    scope_sql = await scope_clause(db, vis, project_id, include_subtree)
    open_where = load_open_where(scope_sql, vis)
    past_where = history_where(scope_sql, vis)
    params: dict[str, Any] = {
        **load_params(vis, project_id),
        "run_days": RUN_WINDOW_DAYS,
        "outcomes": list(RUN_STATUS_OUTCOMES),
        "silent_s": SILENT_RUN_AFTER_S,
        "max_named": MAX_NAMED,
        "long_hours": LONG_RUN_HOURS,
        "long_named": LONG_RUNS_NAMED,
    }
    # One clock for every age, the database's, as the run states read it.
    clock: datetime = (await db.execute(text("SELECT now()"))).scalar()
    start = window_start_day(clock)
    runs_on = agent_runs_enabled()

    series: list[dict[str, Any]] = []
    outcomes = outcome_counts(())
    before = outcome_counts(())
    silent: list[dict[str, Any]] = []
    silent_total = 0
    sizes = _empty_sizes()
    long_runs: list[dict[str, Any]] = []
    if runs_on:
        days = (await db.execute(text(outcome_series_sql(past_where)), params)).fetchall()
        series = [_day_row(d) for d in days]
        outcomes = {o: sum(d[o] for d in series) for o in RUN_OUTCOMES}
        before = outcome_counts(
            (r.status, r.n)
            for r in (await db.execute(text(outcomes_before_sql(past_where)), params)).fetchall()
        )

        quiet = (await db.execute(text(silent_runs_sql(open_where)), params)).fetchall()
        silent = [{**_task_row(r, clock, r.quiet_since), **_who(r)} for r in quiet]
        silent_total = int(quiet[0].total) if quiet else 0

        for r in (await db.execute(text(sizes_sql(past_where)), params)).fetchall():
            if r.bucket in sizes:
                sizes[r.bucket] = int(r.n or 0)
        for r in (await db.execute(text(long_runs_sql(past_where)), params)).fetchall():
            row = _task_row(r, clock, None)
            # A long run is a size, not a wait: it has no age.
            row.pop("since", None)
            row.pop("age_hours", None)
            long_runs.append({
                **row, **_who(r),
                "hours": _round(r.hours),
                "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            })

    seen = bool(
        (await db.execute(text(agent_runs_seen_sql(past_where)), params)).scalar()
    )
    # D-PM-38: the same "↳ Parent" that every flat row carries.
    await attach_parent_context(db, vis, [*silent, *long_runs])

    success = _success(outcomes, before)
    ended = sum(outcomes.values())
    long_total = sizes[SIZE_BUCKETS[-1][0]]
    return {
        "summary": summary_line(
            on=runs_on, ended=ended, success=success, silent=silent_total,
            long_runs=long_total,
        ),
        "on": runs_on,
        "agent_runs_seen": seen,
        "days": RUN_WINDOW_DAYS,
        "window_start": start.isoformat(),
        "outcomes": outcomes,
        "ended_total": ended,
        "outcomes_before": before,
        "series": series,
        "success": success,
        "silent": silent[:MAX_NAMED],
        "silent_total": silent_total,
        "silent_after_minutes": SILENT_RUN_AFTER_S // 60,
        "sizes": sizes,
        "sized_total": sum(sizes.values()),
        "long_runs": long_runs,
        "long_total": long_total,
        "long_after_hours": int(LONG_RUN_HOURS),
        "long_line": long_line(long_total),
        "max_rows": MAX_NAMED,
    }
