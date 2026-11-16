"""Projects · the agent summary strip of a project (WS-55 EA-13e, EA3-43).

Spec: ``project-docs/specs/external_agent_access.md`` §12.2 item 4.

    GET /projects/{project_id}/agent-summary

Five counts over the tasks of the project and its subprojects that the CALLER
may see: ``queued``, ``working``, ``waiting_on_you``, ``in_review`` and
``merged_this_week``. The Projects board draws them above the board, and each
count is a button that sets the matching filter.

⚠️ **One scope, three reads.** The visible tasks come from the same two clauses
as ``GET /projects/tasks`` (``task_visibility_clause`` and the default archive
and triage exclusions), so a count never names a task that the board hides.
Each count is the number of tasks that ``filters.agent_focus_clause`` keeps.
``GET /projects/tasks?agent_focus=`` filters by the SAME text, which reads
``filters.agent_state_sql`` (the pill on a card). So the strip, the card and
the list behind a click cannot disagree.
The route reads the tenant from the session only (R5, R11), and the session
binds RLS.

⚠️ **The runs flag.** While ``agent_runs_enabled`` is off, the cards show no
pill, so the three state counts read 0 here too. The two pull request counts
do not depend on the flag.

    GET /projects/agents/overview

**The member view of agents (WS-AGA AGA-7, ``agents_view.md`` §3 and §4).**
One row for each agent that the caller may see: the name, a one-line job, the
source, the state, the count of runs, the last run and the last error in plain
words. It is not a mode of the summary above. The summary counts the tasks of
ONE project, and a path id that the caller cannot see answers 404. The
overview reads the whole organization, and most of its agents hold no task.

* **Three sources.** ``built_in`` is the code registry of
  ``gateway.routes.agent``, with the label "shared by Metorite". ``org`` is a
  row of ``dynamic_agents`` of the bound tenant. ``external`` is an outside
  agent, one row for each ``client_via`` of the task runs in
  ``pm_agent_runs``. Its name is ``agent_oauth.client_label(via)``, the one
  label seam of the run pill. The self-declared ``client_name`` of its
  connection never names it, because any host can declare "Claude Code".
* **The state.** An outside agent is ``waiting_on_person`` while one of its
  open runs is ``awaiting``, ``working`` while one is queued or in one of
  ``filters.WORKING_STATES``, and ``idle`` otherwise. A built-in or org agent
  is ``working`` while the live presence of the tenant
  (``acb_common.active_runs``) holds a run of it, and ``idle`` otherwise.
* **D12 (§4).** Each run read sits inside ``task_visibility_clause``. An admin
  (``data:org:read``) sees every run of the organization. A lead also sees
  the runs on the tasks of the projects they lead, through
  ``authority.led_projects``, the one reader of ``pm_projects.lead``. A member
  sees the runs on their own tasks (created by them or assigned to them) and
  their own runs. The live presence counts only the caller's own runs, unless
  the caller is an admin.
* **No cost (§3).** The rows hold no cost and no credit field. A cost that
  nobody measured is "not measured", never zero, and AGA-15 adds it.
* **Dark (AGA-6).** :func:`agents_view_allowed` is the one reader of
  ``AGENTS_VIEW_ORGS``. Off, the route answers 404 before it opens a session.
* The route reads the tenant from the session only (R5), and it writes
  nothing.

    GET /projects/agents/{agent_key}/runs

**The drill-down of one agent (WS-AGA AGA-9, ``agents_view.md`` §4).** At
most :data:`RUNS_MAX` lean runs, newest first: the id, the state, the start,
the end, the run time in seconds and, for a task run, the task id and its
title. It is a list, not a third summary.

* **The key.** ``agent_key`` is a ``key`` of the overview.
  :func:`parse_agent_key` checks its shape before any read, and each value
  is a bound parameter.
* **What a run is.** An outside agent: its task runs in ``pm_agent_runs``,
  through the SAME ``seen`` text as the overview (:data:`_SEEN_RUNS_SQL`), so
  the drill-down never shows a run that the overview does not count. A
  built-in or org agent: its chat runs in ``agent_run``, by ``agent_name``.
  An admin reads every run of the org. Any other caller reads their own runs
  only, the rule of the live presence above.
* **Lean (§4).** No row holds the trace, the traceback, the tool list, the
  model or the stored error text of ``agent_run``. The trace stays
  executive-only at ``/debug/runs/{run_id}``. A task run carries the one
  line of :func:`_external_error`, as the overview does.
* **No oracle.** An unknown key, a key of another org, a key out of the
  caller's reach, a key of a bad shape and the flag off all answer the SAME
  404, and its body names no input.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from acb_auth import UserContext, agent_oauth, get_current_user
from acb_common import active_runs, get_settings
from fastapi import Depends, HTTPException
from gateway.routes.projects.authority import led_projects
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    load_visible_project,
    resolve_visibility,
    router,
    task_visibility_clause,
    triage_exclusion_clause,
)
from gateway.routes.projects.filters import (
    AGENT_FOCI,
    AGENT_FOCUS_ME,
    AGENT_RUN_OPEN_STATUSES,
    AGENT_RUN_STATE_SQL,
    WORKING_STATES,
    agent_focus_clause,
    agent_runs_enabled,
    connection_gone_sql,
)
from sqlalchemy import text

#: ``scope`` is the CTE of the visible tasks. Each count is the number of
#: those tasks that ``filters.agent_focus_clause`` keeps, so a count is the
#: ``total`` of the list under the same focus (WS-55 EA-13e, task #986).
_SUMMARY_SQL = """
WITH RECURSIVE sub AS (
    SELECT id FROM pm_projects WHERE id = CAST(:pid AS uuid)
    UNION ALL
    SELECT p.id FROM pm_projects p JOIN sub s ON p.parent_project_id = s.id
), scope AS (
    SELECT t.id
      FROM pm_tasks t
     WHERE {visible}
       AND t.project_id IN (SELECT id FROM sub)
       AND t.archived_at IS NULL
       AND EXISTS (SELECT 1 FROM pm_projects pp
                    WHERE pp.id = t.project_id AND pp.archived_at IS NULL)
       AND {triage}
)
SELECT
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {queued}) AS queued,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {working}) AS working,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {waiting}) AS waiting_on_you,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {review}) AS in_review,
  (SELECT count(*) FROM pm_tasks t JOIN scope sc ON sc.id = t.id WHERE {merged}) AS merged_this_week
"""


@router.get("/{project_id}/agent-summary")
async def agent_summary(
    project_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, int]:
    """The five counts of one project, over the tasks the caller can see.

    A project the caller cannot see answers 404, as the list does. The caller
    is the session's member, never a name from the request.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        await load_visible_project(db, vis, project_id)
        sql = _SUMMARY_SQL.format(
            visible=task_visibility_clause(vis),
            triage=triage_exclusion_clause(),
            **{focus: agent_focus_clause(focus) for focus in AGENT_FOCI},
        )
        row = (await db.execute(text(sql), {
            **vis.params,
            "pid": project_id,
            AGENT_FOCUS_ME: actor(user).strip().lower(),
        })).fetchone()
    return _counts(row)


def _counts(row: Any) -> dict[str, int]:
    keys = ("queued", "working", "waiting_on_you", "in_review", "merged_this_week")
    return {key: int(getattr(row, key, 0) or 0) for key in keys}


# ── WS-AGA AGA-6 and AGA-7: the member view of agents ───────────────────────


def agents_view_allowed(organization_id: str | None) -> bool:
    """True when ``AGENTS_VIEW_ORGS`` holds the org, or holds ``*``. Default OFF.

    The one reader of the flag (AGA-6). The org comes from the session, never
    from request input (R5). It reads ``get_settings()`` on each call, so a
    flag reload applies a change with no restart (``docs/flag_reload.md``).
    """
    org = str(organization_id or "").strip().lower()
    if not org:
        return False
    raw = str(getattr(get_settings(), "agents_view_orgs", "") or "")
    listed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    return "*" in listed or org in listed


#: The label of an agent that Metorite ships to each organization (§4).
SHARED_LABEL = "shared by Metorite"

#: The three sources of an agent (AGA-7).
SOURCES: tuple[str, ...] = ("built_in", "org", "external")

#: The three states of an agent (AGA-7).
STATES: tuple[str, ...] = ("working", "idle", "waiting_on_person")

#: The longest job line and error line, in characters.
LINE_MAX = 140

#: The most outside agents in one answer. A tenant with more is cut, and
#: ``truncated`` says so.
EXTERNAL_MAX = 200

#: The one-line job of an outside agent. Its connection holds no job text.
EXTERNAL_JOB = "An outside agent that works on tasks in Projects for a member."

_OPEN = ", ".join(f"'{s}'" for s in AGENT_RUN_OPEN_STATUSES)
_BUSY = ", ".join(f"'{s}'" for s in ("queued", *WORKING_STATES))

#: The task runs that the caller may see (§4). ONE text: the overview groups
#: them for each outside agent, and the drill-down (AGA-9) lists the runs of
#: one. ``{visible}`` is ``task_visibility_clause`` and ``{reach}`` is the D12
#: arm of :func:`_reach_sql`. ``{only}`` is empty for the overview, and
#: :data:`_ONE_AGENT` for the drill-down. The org predicate is defence in
#: depth beside FORCE RLS, which the session binds.
_SEEN_RUNS_SQL = """
    SELECT r.id, r.client_via, r.status, r.created_at, r.task_id,
           {state} AS state,
           COALESCE(r.started_at, r.created_at) AS run_at,
           COALESCE(r.ended_at, r.last_activity_at) AS ended_at,
           r.error_metadata->>'body' AS error_body,
           {gone} AS connection_gone
      FROM pm_agent_runs r
      JOIN pm_tasks t ON t.id = r.task_id
     WHERE r.task_id IS NOT NULL
       AND r.organization_id = CAST(:vis_org AS uuid)
       AND {visible}
       AND {reach}{only}
"""

#: The ``{only}`` of the drill-down: the runs of one outside agent.
_ONE_AGENT = "\n       AND r.client_via = :via"

#: A run of ``seen`` that reads as an error (AGA-7): it failed, or it is open
#: on a revoked or expired connection. ONE text for the overview and the
#: drill-down.
_ERR_SQL = f"(status = 'failed' OR (status IN ({_OPEN}) AND connection_gone))"

#: The task runs that the caller may see, one row for each outside agent.
_EXTERNAL_SQL = "WITH seen AS (" + _SEEN_RUNS_SQL + """), agg AS (
    SELECT client_via,
           count(*) AS runs,
           bool_or(status IN ({open}) AND state = 'awaiting') AS waiting,
           bool_or(status IN ({open}) AND state IN ({busy})) AS busy,
           (array_agg(run_at ORDER BY created_at DESC, id DESC))[1] AS last_at,
           (array_agg(state ORDER BY created_at DESC, id DESC))[1] AS last_state
      FROM seen GROUP BY client_via
), err AS (
    SELECT DISTINCT ON (client_via) client_via, status AS err_status,
           error_body, connection_gone AS err_gone, ended_at AS err_at
      FROM seen
     WHERE """ + _ERR_SQL + """
     ORDER BY client_via, created_at DESC, id DESC
)
SELECT agg.*, err.err_status, err.error_body, err.err_gone, err.err_at
  FROM agg LEFT JOIN err USING (client_via)
 ORDER BY agg.last_at DESC NULLS LAST, agg.client_via
 LIMIT :cap
"""

#: The newest task runs of one outside agent that the caller may see
#: (AGA-9). ``shown`` names the error columns as :func:`_external_error`
#: reads them. Each task is one that ``seen`` lets the caller see, so a run
#: on a task that the caller cannot see is left out, never masked.
_AGENT_RUNS_SQL = "WITH seen AS (" + _SEEN_RUNS_SQL + """), shown AS (
    SELECT id, status, state, created_at, run_at, ended_at, task_id,
           error_body, connection_gone AS err_gone, ended_at AS err_at,
           CASE WHEN """ + _ERR_SQL + """ THEN status END AS err_status
      FROM seen
     ORDER BY created_at DESC, id DESC
     LIMIT :cap
)
SELECT shown.*, t.title AS task_title
  FROM shown
  JOIN pm_tasks t ON t.id = shown.task_id
 ORDER BY shown.created_at DESC, shown.id DESC
"""

#: The chat runs of one built-in or org agent in the durable store
#: ``agent_run`` (AGA-9), by ``agent_name``. Lean columns only: never
#: ``trace``, ``error_traceback``, ``error_message``, ``tool_summary`` or
#: ``model``. ``{reach}`` is :func:`_reach_sql` with no task. The org
#: predicate is defence in depth beside FORCE RLS, which the session binds.
_CHAT_RUNS_SQL = """
SELECT ar.run_id AS id, ar.status, ar.started_at, ar.ended_at, ar.duration_ms
  FROM agent_run ar
 WHERE ar.organization_id = CAST(:vis_org AS uuid)
   AND ar.agent_name = :name
   AND {reach}
 ORDER BY ar.started_at DESC, ar.run_id DESC
 LIMIT :cap
"""

#: The agents that the org registered (``dynamic_agents``, tenant-scoped).
_ORG_AGENTS_SQL = (
    "SELECT name, description FROM dynamic_agents"
    " WHERE organization_id = CAST(:vis_org AS uuid) ORDER BY name"
)


def _reach_sql(admin: bool, *, runner: str = "r.member_email", task: str | None = "t") -> str:
    """The D12 arm of a run read (§4). ONE rule for each run read here.

    An admin sees every visible run. Any other caller sees their own runs
    (*runner* is the member of the run), the runs on their own tasks and the
    runs on the tasks of the projects they lead (``:led``, from
    :func:`authority.led_projects`), over the task alias *task*.

    A chat run of ``agent_run`` has no task (``task=None``). Then only the
    first arm holds: a caller who is not an admin reads their own runs, as
    :func:`_live_by_agent` counts the live presence.
    """
    if admin:
        return "true"
    own = f"lower({runner}) = :me"
    if task is None:
        return f"({own})"
    return (
        f"({own}"
        f" OR lower({task}.created_by) = :me"
        " OR EXISTS (SELECT 1 FROM pm_task_assignees a"
        f"             WHERE a.task_id = {task}.id AND lower(a.assignee) = :me)"
        f" OR {task}.project_id = ANY(CAST(:led AS uuid[])))"
    )


def _line(value: Any, fallback: str = "") -> str:
    """One line of plain text: no control or format character, one space
    between words, at most :data:`LINE_MAX` characters."""
    raw = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in str(value or ""))
    line = re.sub(r"\s+", " ", raw).strip()
    if len(line) > LINE_MAX:
        line = line[: LINE_MAX - 1].rstrip() + "…"
    return line or fallback


def _when(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _split_description(name: str, description: str) -> tuple[str, str]:
    """``(name, job)`` from a registry description such as
    ``"Projects Assistant — the AI chat inside the Projects app. ..."``.

    The part before the dash is the name. The first sentence after it is the
    job. A description with no dash keeps the slug as the name.
    """
    text_ = _line(description)
    title, job = name, text_
    for dash in (" — ", " - "):
        if dash in text_:
            head, tail = text_.split(dash, 1)
            if head.strip():
                title, job = head.strip(), tail.strip()
            break
    job = re.split(r"(?<=[.!?])\s", job, maxsplit=1)[0].strip()
    if job and job[0].islower():
        job = job[0].upper() + job[1:]
    return _line(title, name), _line(job, "No job description yet.")


def _external_error(row: Any, *, subject: str = "The last run") -> dict[str, Any] | None:
    """The last error of an outside agent, in plain words: what happened, and
    the next step (AGA-7). ``None`` when no visible run failed.

    The drill-down (AGA-9) shapes the error of each run here too, with the
    *subject* "This run". The agent's text is one :func:`_line`, never more.
    """
    status = getattr(row, "err_status", None)
    if status is None:
        return None
    if getattr(row, "err_gone", False) and status != "failed":
        return {
            "at": _when(getattr(row, "err_at", None)),
            "what": "The connection of this agent was revoked or has expired, "
                    "so its run cannot go on.",
            "next_step": "Connect the agent again in Integrations, AI agents, "
                         "then start it again on the task.",
        }
    reason = _line(getattr(row, "error_body", None))
    what = (f"{subject} failed. The agent said: {reason}" if reason
            else f"{subject} failed, and the agent gave no reason.")
    return {
        "at": _when(getattr(row, "err_at", None)),
        "what": what,
        "next_step": "Open the task to read the run, then start the agent again.",
    }


def _external_state(row: Any) -> str:
    if getattr(row, "waiting", False):
        return "waiting_on_person"
    if getattr(row, "busy", False):
        return "working"
    return "idle"


def _live_by_agent(runs: list[dict[str, Any]], me: str, admin: bool) -> dict[str, list[dict[str, Any]]]:
    """The live presence of the tenant, by agent name. A caller who is not an
    admin counts only their own runs (D12)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        if not admin and str(run.get("user") or "").strip().lower() != me:
            continue
        out.setdefault(str(run.get("agent") or ""), []).append(run)
    return out


def _cast_row(
    *, name: str, description: str, source: str, live: list[dict[str, Any]],
) -> dict[str, Any]:
    title, job = _split_description(name, description)
    newest = max((str(r.get("ts") or "") for r in live), default="")
    return {
        "key": f"{source}:{name}",
        "name": title,
        "agent": name,
        "job": job,
        "source": source,
        "label": SHARED_LABEL if source == "built_in" else None,
        "state": "working" if live else "idle",
        "runs": len(live),
        "last_run": {"at": newest or None, "state": "working"} if live else None,
        "last_error": None,
    }


def _builtin_registry() -> list[dict[str, Any]]:
    """The code registry of the built-in agents. It reads no database."""
    # A lazy import: `gateway.routes.agent` is a heavy module of another router.
    from gateway.routes.agent import _AGENT_REGISTRY

    return [dict(a) for a in _AGENT_REGISTRY]


def _builtin_names() -> frozenset[str]:
    """The names of the built-in agents, as the overview writes their keys."""
    return frozenset(
        name for name in (str(a.get("name") or "").strip() for a in _builtin_registry()) if name
    )


#: The one body of a 404 here. It names no input, so an unknown key, a key
#: of another org, a key out of reach and the flag off read the same (AGA-9).
NOT_FOUND = "Not Found"


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=NOT_FOUND)


@router.get("/agents/overview")
async def agents_overview(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Every agent that the caller may see, its state and its last run (AGA-7).

    Off for the org (``AGENTS_VIEW_ORGS``), it answers 404. The org and the
    caller are the session's, never a value from the request (R5).
    """
    if not agents_view_allowed(getattr(user, "organization_id", None)):
        raise _not_found()
    me = actor(user).strip().lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        admin = bool(vis.unrestricted)
        led = sorted(await led_projects(db, user)) if not admin else []
        external: list[Any] = []
        if agent_runs_enabled():
            sql = _EXTERNAL_SQL.format(
                state=AGENT_RUN_STATE_SQL, gone=connection_gone_sql("r"),
                visible=task_visibility_clause(vis), reach=_reach_sql(admin),
                only="", open=_OPEN, busy=_BUSY,
            )
            external = list((await db.execute(text(sql), {
                **vis.params, "me": me, "led": led, "cap": EXTERNAL_MAX + 1,
            })).fetchall())
        org_agents = list((await db.execute(
            text(_ORG_AGENTS_SQL), {"vis_org": vis.organization_id},
        )).fetchall())
    live = _live_by_agent(await active_runs(organization_id=vis.organization_id), me, admin)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in _builtin_registry():
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(_cast_row(name=name, description=str(entry.get("description") or ""),
                              source="built_in", live=live.get(name, [])))
    for entry in org_agents:
        name = str(getattr(entry, "name", "") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(_cast_row(name=name, description=str(getattr(entry, "description", "") or ""),
                              source="org", live=live.get(name, [])))
    for row in external[:EXTERNAL_MAX]:
        via = str(row.client_via)
        rows.append({
            "key": f"external:{via}",
            # The one label seam, as on the run pill. The self-declared
            # client_name of the connection never names an agent.
            "name": _line(agent_oauth.client_label(via), "Agent"),
            "agent": via,
            "job": EXTERNAL_JOB,
            "source": "external",
            "label": None,
            "state": _external_state(row),
            "runs": int(row.runs or 0),
            "last_run": {"at": _when(row.last_at), "state": str(row.last_state)},
            "last_error": _external_error(row),
        })
    order = {state: i for i, state in enumerate(("waiting_on_person", "working", "idle"))}
    rows.sort(key=lambda r: (order[r["state"]], r["name"].lower()))
    return {
        "agents": rows,
        "count": len(rows),
        "viewer": _viewer(admin, led),
        "truncated": len(external) > EXTERNAL_MAX,
    }


def _viewer(admin: bool, led: list[str]) -> str:
    """The viewer level of §4: ``admin``, ``lead`` or ``member``."""
    return "admin" if admin else ("lead" if led else "member")


# ── WS-AGA AGA-9: the drill-down of one agent ───────────────────────────────

#: The most runs in one drill-down (AGA-9, ``agents_view.md`` §6).
RUNS_MAX = 40

#: The longest agent key, in characters. A longer key is 404 before the regex.
AGENT_KEY_MAX = 128

#: The one shape of an agent key, as the overview writes it. ``built_in:<name>``
#: and ``org:<name>`` name a registry agent, and ``external:<client_via>`` an
#: outside agent. The stamp part is the CHECK of ``pm_agent_runs.client_via``
#: (migration 342). A key of another shape never reaches a read.
AGENT_KEY_PATTERN = re.compile(
    r"(?P<source>built_in|org):(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,99})"
    r"|external:(?P<via>(?:mcp|studio):[a-z0-9:_.-]{1,60})"
)

#: The top keys of a drill-down answer, and the keys of one run (AGA-9).
#: Lean: no trace, no traceback, no tool list, no model, no stored error text.
ANSWER_KEYS: tuple[str, ...] = ("key", "source", "runs_of", "viewer", "runs", "count", "truncated")
RUN_KEYS: tuple[str, ...] = ("id", "state", "started_at", "ended_at", "run_seconds", "task", "error")

#: What a run of each source is (AGA-9). ``task``: a task run in
#: ``pm_agent_runs``. ``chat``: a chat run in ``agent_run``.
RUNS_OF = {"external": "task", "built_in": "chat", "org": "chat"}

#: The state of a chat run, in the words of the task run states
#: (``filters.AGENT_RUN_STATE_SQL``), so that a run reads one way in each
#: source. ``agent_run.status`` holds these four (``gateway/run_trace.py``).
CHAT_STATES: dict[str, str] = {
    "running": "working",
    "completed": "completed",
    "error": "failed",
    "cancelled": "stopped",
}

#: The error of a failed chat run. It never quotes ``agent_run``: the stored
#: message, the traceback and the trace stay executive-only.
CHAT_ERROR_WHAT = "This run failed before it finished."
CHAT_ERROR_NEXT = "Ask the agent again. If it fails again, tell an admin of your organization."


def parse_agent_key(raw: Any) -> tuple[str, str] | None:
    """``(source, name)`` of an agent key, or ``None`` for any other value.

    The check is the whole key against :data:`AGENT_KEY_PATTERN`, after a
    length cap. It reads nothing, and it echoes nothing.
    """
    if not isinstance(raw, str) or len(raw) > AGENT_KEY_MAX:
        return None
    found = AGENT_KEY_PATTERN.fullmatch(raw)
    if found is None:
        return None
    if found.group("via"):
        return "external", found.group("via")
    return found.group("source"), found.group("name")


def _seconds(start: Any, end: Any) -> float | None:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    return round(max(0.0, (end - start).total_seconds()), 1)


def _task_run_row(row: Any) -> dict[str, Any]:
    """One task run of an outside agent, lean (AGA-9).

    An open run has no end and no run time yet. The error is the one line
    of :func:`_external_error`, and the task title is one :func:`_line`.
    """
    is_open = str(row.status) in AGENT_RUN_OPEN_STATUSES
    ended = None if is_open else row.ended_at
    return {
        "id": str(row.id),
        "state": str(row.state),
        "started_at": _when(row.run_at),
        "ended_at": _when(ended),
        "run_seconds": None if is_open else _seconds(row.run_at, ended),
        "task": {"id": str(row.task_id), "title": _line(row.task_title, "Untitled task")},
        "error": _external_error(row, subject="This run"),
    }


def _chat_run_row(row: Any) -> dict[str, Any]:
    """One chat run of a built-in or org agent, lean (AGA-9).

    A running run has no end and no run time yet. The run time is the
    measured ``duration_ms`` when the store holds it. A chat run has no task.
    """
    status = str(row.status or "")
    running = status == "running"
    ended = None if running else row.ended_at
    if running:
        seconds = None
    elif row.duration_ms is not None:
        seconds = round(max(0, int(row.duration_ms)) / 1000, 1)
    else:
        seconds = _seconds(row.started_at, ended)
    error = None
    if status == "error":
        error = {"at": _when(ended), "what": CHAT_ERROR_WHAT, "next_step": CHAT_ERROR_NEXT}
    return {
        "id": str(row.id),
        "state": CHAT_STATES.get(status, "unknown"),
        "started_at": _when(row.started_at),
        "ended_at": _when(ended),
        "run_seconds": seconds,
        "task": None,
        "error": error,
    }


@router.get("/agents/{agent_key}/runs")
async def agent_recent_runs(
    agent_key: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """The newest runs of one agent that the caller may see (AGA-9).

    The flag, the key shape and the reach each answer the one 404 of
    :func:`_not_found`. The org and the caller are the session's, never a
    value from the request (R5). The route writes nothing.
    """
    if not agents_view_allowed(getattr(user, "organization_id", None)):
        raise _not_found()
    parsed = parse_agent_key(agent_key)
    if parsed is None:
        raise _not_found()
    source, name = parsed
    builtin = _builtin_names()
    if source == "built_in" and name not in builtin:
        raise _not_found()
    if source == "external" and not agent_runs_enabled():
        # The overview lists no outside agent while the runs flag is off.
        raise _not_found()
    me = actor(user).strip().lower()
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        admin = bool(vis.unrestricted)
        led = sorted(await led_projects(db, user)) if not admin else []
        if source == "external":
            sql = _AGENT_RUNS_SQL.format(
                state=AGENT_RUN_STATE_SQL, gone=connection_gone_sql("r"),
                visible=task_visibility_clause(vis), reach=_reach_sql(admin),
                only=_ONE_AGENT,
            )
            found = list((await db.execute(text(sql), {
                **vis.params, "me": me, "led": led, "via": name, "cap": RUNS_MAX + 1,
            })).fetchall())
            if not found:
                # No run that the caller may see: the overview has no row of
                # this agent for the caller, so the key is out of reach.
                raise _not_found()
            rows = [_task_run_row(r) for r in found[:RUNS_MAX]]
        else:
            if source == "org":
                registered = {
                    str(getattr(r, "name", "") or "").strip()
                    for r in (await db.execute(
                        text(_ORG_AGENTS_SQL), {"vis_org": vis.organization_id},
                    )).fetchall()
                }
                # The overview lists a name of the code registry once, as built-in.
                if name not in registered or name in builtin:
                    raise _not_found()
            sql = _CHAT_RUNS_SQL.format(reach=_reach_sql(admin, runner="ar.user_id", task=None))
            found = list((await db.execute(text(sql), {
                "vis_org": vis.organization_id, "name": name, "me": me, "cap": RUNS_MAX + 1,
            })).fetchall())
            rows = [_chat_run_row(r) for r in found[:RUNS_MAX]]
    return {
        "key": agent_key,
        "source": source,
        "runs_of": RUNS_OF[source],
        "viewer": _viewer(admin, led),
        "runs": rows,
        "count": len(rows),
        "truncated": len(found) > RUNS_MAX,
    }
