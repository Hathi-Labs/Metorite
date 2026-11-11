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
  ``pm_agent_runs``.
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
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from acb_auth import UserContext, get_current_user
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

#: The task runs that the caller may see, one row for each outside agent.
#: ``{visible}`` is ``task_visibility_clause`` and ``{reach}`` is the D12 arm
#: of :func:`_reach_sql`. The org predicate is defence in depth beside FORCE
#: RLS, which the session binds.
_EXTERNAL_SQL = """
WITH seen AS (
    SELECT r.id, r.client_via, r.status, r.created_at,
           {state} AS state,
           COALESCE(r.started_at, r.created_at) AS run_at,
           COALESCE(r.ended_at, r.last_activity_at) AS ended_at,
           r.error_metadata->>'body' AS error_body,
           {gone} AS connection_gone,
           (SELECT c.client_name FROM agent_connection c
             WHERE c.id = r.connection_id
               AND c.organization_id = r.organization_id) AS client_name
      FROM pm_agent_runs r
      JOIN pm_tasks t ON t.id = r.task_id
     WHERE r.task_id IS NOT NULL
       AND r.organization_id = CAST(:vis_org AS uuid)
       AND {visible}
       AND {reach}
), agg AS (
    SELECT client_via,
           count(*) AS runs,
           bool_or(status IN ({open}) AND state = 'awaiting') AS waiting,
           bool_or(status IN ({open}) AND state IN ({busy})) AS busy,
           (array_agg(run_at ORDER BY created_at DESC, id DESC))[1] AS last_at,
           (array_agg(state ORDER BY created_at DESC, id DESC))[1] AS last_state,
           (array_agg(client_name ORDER BY created_at DESC, id DESC)
              FILTER (WHERE client_name IS NOT NULL AND client_name <> ''))[1] AS client_name
      FROM seen GROUP BY client_via
), err AS (
    SELECT DISTINCT ON (client_via) client_via, status AS err_status,
           error_body, connection_gone AS err_gone, ended_at AS err_at
      FROM seen
     WHERE status = 'failed' OR (status IN ({open}) AND connection_gone)
     ORDER BY client_via, created_at DESC, id DESC
)
SELECT agg.*, err.err_status, err.error_body, err.err_gone, err.err_at
  FROM agg LEFT JOIN err USING (client_via)
 ORDER BY agg.last_at DESC NULLS LAST, agg.client_via
 LIMIT :cap
"""

#: The agents that the org registered (``dynamic_agents``, tenant-scoped).
_ORG_AGENTS_SQL = (
    "SELECT name, description FROM dynamic_agents"
    " WHERE organization_id = CAST(:vis_org AS uuid) ORDER BY name"
)


def _reach_sql(admin: bool) -> str:
    """The D12 arm of a run read (§4), over the run ``r`` and the task ``t``.

    An admin sees every visible run. Any other caller sees their own runs,
    the runs on their own tasks and the runs on the tasks of the projects
    they lead (``:led``, from :func:`authority.led_projects`).
    """
    if admin:
        return "true"
    return (
        "(lower(r.member_email) = :me"
        " OR lower(t.created_by) = :me"
        " OR EXISTS (SELECT 1 FROM pm_task_assignees a"
        "             WHERE a.task_id = t.id AND lower(a.assignee) = :me)"
        " OR t.project_id = ANY(CAST(:led AS uuid[])))"
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


def _external_name(client_via: str, client_name: Any) -> str:
    """The name of an outside agent: its connection name, else the last part
    of its stamp (``mcp:claude.ai:claude-code`` gives ``claude-code``)."""
    named = _line(client_name)
    if named:
        return named
    return _line(str(client_via or "").rsplit(":", 1)[-1], "Outside agent")


def _external_error(row: Any) -> dict[str, Any] | None:
    """The last error of an outside agent, in plain words: what happened, and
    the next step (AGA-7). ``None`` when no visible run failed."""
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
    what = (f"The last run failed. The agent said: {reason}" if reason
            else "The last run failed, and the agent gave no reason.")
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


@router.get("/agents/overview")
async def agents_overview(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Every agent that the caller may see, its state and its last run (AGA-7).

    Off for the org (``AGENTS_VIEW_ORGS``), it answers 404. The org and the
    caller are the session's, never a value from the request (R5).
    """
    if not agents_view_allowed(getattr(user, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Not Found")
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
                open=_OPEN, busy=_BUSY,
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
            "name": _external_name(via, row.client_name),
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
    viewer = "admin" if admin else ("lead" if led else "member")
    return {
        "agents": rows,
        "count": len(rows),
        "viewer": viewer,
        "truncated": len(external) > EXTERNAL_MAX,
    }
