"""Seeds for the EA-13 board suites (R8, a real Postgres).

``test_projects_pr_chip_r8.py`` and ``test_projects_agent_summary_r8.py`` plant
the same four things: a project tree, a task, a pull request reference and an
agent run. They plant them as the RLS-bypassing admin, and each INSERT stamps
``organization_id`` itself. The calls go through the real ``/projects`` router
as the non-privileged role (see ``test_agent_runs_r8._session``).

This is a helper module and holds no test.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text

from tests.unit.test_agent_runs_r8 import (  # noqa: F401
    CLIENT_ID,
    ORIGIN,
    VIA,
    _principal,
    _seed_connection,
    _session,
)

REPO = "acme/widgets"


def member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})


def project(
    p: Any, org: str, email: str, *, parent: dict[str, Any] | None = None,
    grant: str | None = None, sealed: bool = False,
) -> dict[str, Any]:
    """A root project with a `todo` and a `done` lane, or a subproject of
    *parent* that shares the lanes of its root. Returns ``{id, root, lanes}``."""
    pid = str(uuid.uuid4())
    member(p, org, email)
    with p.admin_engine.begin() as c:
        if parent is None:
            c.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses) "
                "VALUES (:id, :o, 'board', :e, true)"), {"id": pid, "o": org, "e": email})
            lanes: dict[str, str] = {}
            for position, (name, category) in enumerate((("To do", "todo"), ("Done", "done"))):
                sid = str(uuid.uuid4())
                c.execute(text(
                    "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                    "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                    {"id": sid, "o": org, "p": pid, "n": name, "c": category,
                     "pos": (position + 1) * 10})
                lanes[category] = sid
            root = pid
        else:
            c.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, created_by, "
                "parent_project_id, owns_statuses, sealed_at) VALUES (:id, :o, 'sub', :e, "
                "CAST(:par AS uuid), false, CASE WHEN :sealed THEN now() END)"),
                {"id": pid, "o": org, "e": email, "par": parent["id"], "sealed": sealed})
            lanes, root = parent["lanes"], parent["root"]
        if grant is not None:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": pid, "s": grant, "e": email})
    return {"id": pid, "root": root, "lanes": lanes}


_NUMBER = [0]


def task(
    p: Any, org: str, email: str, proj: dict[str, Any], *, category: str = "todo",
    assignee: str | None = None, title: str = "Fix the extruder",
) -> str:
    tid = str(uuid.uuid4())
    _NUMBER[0] += 1
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by) "
            "VALUES (:id, :o, :p, :r, :n, :s, :t, :e)"),
            {"id": tid, "o": org, "p": proj["id"], "r": proj["root"], "n": _NUMBER[0],
             "s": proj["lanes"][category], "t": title, "e": email})
        if assignee is not None:
            member(p, org, assignee)
            c.execute(text(
                "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
                "assigned_by) VALUES (:o, :t, :a, :e)"),
                {"o": org, "t": tid, "a": assignee, "e": email})
    return tid


def ref(
    p: Any, org: str, tid: str, number: int, state: str | None, *,
    checks: str | None = None, summary: dict[str, int] | None = None,
    updated: str = "now()", merged: str | None = None, title: str | None = None,
    repo: str = REPO, kind: str = "pull_request",
) -> str:
    """One reference. *updated* is a SQL time expression for ``pr_updated_at``
    and *merged* one for ``merged_at``."""
    rid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_refs (id, organization_id, task_id, kind, provider, ref, "
            "url, title, state, via, created_by, checks_state, checks_summary, "
            "pr_updated_at, merged_at) VALUES (CAST(:id AS uuid), CAST(:o AS uuid), "
            "CAST(:t AS uuid), :k, 'github', :ref, :url, :title, :state, 'github', "
            "'system:github', :cs, CAST(:sum AS jsonb), " + updated + ", "
            + (merged or "NULL") + ")"),
            {"id": rid, "o": org, "t": tid, "k": kind, "ref": f"{repo}#{number}",
             "url": f"https://github.com/{repo}/pull/{number}", "title": title,
             "state": state, "cs": checks,
             "sum": json.dumps(summary) if summary is not None else None})
    return rid


def run(
    p: Any, org: str, tid: str, email: str, status: str, *, via: str = VIA,
    created: str = "now()", last_activity: str = "now()", last_agent: str = "now()",
) -> str:
    rid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_runs (id, organization_id, task_id, member_email, client_via, "
            "status, started_by, created_at, last_activity_at, last_agent_activity_at) "
            "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), CAST(:t AS uuid), :e, :via, :s, :e, "
            + created + ", " + last_activity + ", " + last_agent + ")"),
            {"id": rid, "o": org, "t": tid, "e": email, "via": via, "s": status})
    return rid


def activity(
    p: Any, org: str, rid: str, tid: str, kind: str, body: str, *, email: str = "x@y.test",
) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, type, "
            "content, actor, actor_via) VALUES (CAST(:o AS uuid), CAST(:r AS uuid), "
            "CAST(:t AS uuid), :k, CAST(:c AS jsonb), :e, :via)"),
            {"o": org, "r": rid, "t": tid, "k": kind, "c": json.dumps({"body": body}),
             "e": email, "via": VIA})


def mcp_comment(p: Any, org: str, tid: str, email: str, *, via: str = VIA) -> None:
    """A timeline row that an act through MCP wrote."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_activities (organization_id, task_id, type, created_by, body, "
            "meta) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), 'comment', :e, 'hello', "
            "CAST(:m AS jsonb))"),
            {"o": org, "t": tid, "e": email, "m": json.dumps({"via": via})})


def clear(p: Any) -> None:
    """Remove what these suites planted, in the order that the keys allow."""
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_runs"))
        c.execute(text("DELETE FROM pm_task_refs"))
        c.execute(text("DELETE FROM pm_activities WHERE meta->>'via' LIKE 'mcp:%'"))


def when(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None
