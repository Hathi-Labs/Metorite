"""Seeds and the client of the two CC-2 suites (R8, a real Postgres).

``test_today_rule_r8.py`` and ``test_today_focus_cap_r8.py`` plant the same
things: members, a project with a lane for each category, tasks, the member's
overlay rows, the One Thing and the saved zone and cap. They plant them as the
RLS-bypassing admin, and each INSERT stamps ``organization_id`` itself. The
calls go through the real ``/projects`` and ``/tasks`` routers as the
non-privileged role, so FORCE RLS holds (see ``test_agent_runs_r8._session``).

The cases of the one Today rule live in ``tests/fixtures/today_rule_cases.json``,
and :func:`cases` reads them. This is a helper module and holds no test.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_agent_runs_r8 import _Client

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "today_rule_cases.json"

#: The lanes of a seeded project, one for each category the rule reads.
CATEGORIES = ("todo", "in_progress", "backlog", "done", "cancelled", "triage")


def cases() -> dict[str, Any]:
    """The shared fixture: ``zone``, ``today``, ``at`` and the ``cases``."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def instant(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def person(email: str, org: str, *, unrestricted: bool = False) -> Any:
    """A member of the session. ``data:org:read`` only when asked, so a role
    never widens what a reader sees."""
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:projects", "feature:tasks"]
    if unrestricted:
        perms.append("data:org:read")
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms), organization_id=org,
    )


def member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})


def project(p: Any, org: str, owner: str, *, grants: tuple[str, ...] = (),
            archived: bool = False) -> dict[str, Any]:
    """A root project with one lane for each of :data:`CATEGORIES`, and a
    grant for each address of *grants*. Returns ``{id, lanes}``."""
    pid = str(uuid.uuid4())
    member(p, org, owner)
    lanes: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses,"
            " archived_at) VALUES (:id, :o, 'today', :e, true,"
            " CASE WHEN :archived THEN now() END)"),
            {"id": pid, "o": org, "e": owner, "archived": archived})
        for position, category in enumerate(CATEGORIES):
            sid = str(uuid.uuid4())
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": sid, "o": org, "p": pid, "n": category.title(), "c": category,
                 "pos": (position + 1) * 10})
            lanes[category] = sid
        for subject in grants:
            c.execute(text(
                "INSERT INTO pm_project_grants (organization_id, project_id, subject, "
                "created_by) VALUES (:o, :p, :s, :e)"),
                {"o": org, "p": pid, "s": subject, "e": owner})
    return {"id": pid, "lanes": lanes}


_NUMBER = [0]


def task(
    p: Any, org: str, proj: dict[str, Any], *, by: str, category: str = "todo",
    assignee: str | None = None, completed_at: datetime | None = None,
    start_date: date | None = None, due_at: datetime | None = None,
    archived: bool = False, title: str = "Weld the frame",
) -> str:
    tid = str(uuid.uuid4())
    _NUMBER[0] += 1
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by, completed_at, start_date, due_at, "
            "archived_at) VALUES (:id, :o, :p, :p, :n, :s, :t, :e, :done, :start, :due, "
            "CASE WHEN :archived THEN now() END)"),
            {"id": tid, "o": org, "p": proj["id"], "n": _NUMBER[0],
             "s": proj["lanes"][category], "t": title, "e": by, "done": completed_at,
             "start": start_date, "due": due_at, "archived": archived})
        if assignee is not None:
            member(p, org, assignee)
            c.execute(text(
                "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, "
                "assigned_by) VALUES (:o, :t, :a, :e)"),
                {"o": org, "t": tid, "a": assignee, "e": by})
    return tid


def overlay(p: Any, org: str, tid: str, email: str, **cols: Any) -> None:
    """The member's overlay row of a task, as the admin, with its tenant."""
    names = ["organization_id", "task_id", "member_email", *cols]
    values = ["CAST(:o AS uuid)", "CAST(:t AS uuid)", ":m", *(f":{k}" for k in cols)]
    sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in cols) or "updated_at = now()"
    with p.admin_engine.begin() as c:
        c.execute(text(
            f"INSERT INTO pm_task_personal ({', '.join(names)}) VALUES ({', '.join(values)}) "
            f"ON CONFLICT (task_id, member_email) DO UPDATE SET {sets}"),
            {"o": org, "t": tid, "m": email, **cols})


def one_thing(p: Any, org: str, email: str, on: date, tid: str | None) -> None:
    """The member's One Thing of *on*, or none."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO calendar_day_state (user_id, day, one_thing_id, organization_id) "
            "VALUES (:u, :d, CAST(:t AS uuid), CAST(:o AS uuid)) "
            "ON CONFLICT (user_id, day) DO UPDATE SET one_thing_id = EXCLUDED.one_thing_id"),
            {"u": email, "d": on, "t": tid, "o": org})


def settings(p: Any, org: str, email: str, *, zone: str, cap: int | None = None) -> None:
    """The member's saved zone and cap of today's focus, in *org*."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (user_id, organization_id, timezone, today_focus_cap) "
            "VALUES (:u, CAST(:o AS uuid), :z, :cap) "
            "ON CONFLICT (organization_id, user_id) DO UPDATE "
            "SET timezone = EXCLUDED.timezone, today_focus_cap = EXCLUDED.today_focus_cap"),
            {"u": email, "o": org, "z": zone, "cap": cap})


def rows(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _app(users: dict[str, Any]) -> Any:
    """The real ``/projects`` and ``/tasks`` routers. The caller is the principal
    that the test header names, and the override binds the tenant from it, as
    ``_with_resolved_access`` does. The tenant never comes from the request."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import projects as projects_package
    from gateway.routes import tasks as tasks_package

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(projects_package.router)
    app.include_router(tasks_package.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


@contextlib.asynccontextmanager
async def session(p: Any):
    """One engine scope and one ASGI client for a whole test, so two requests
    may run at once."""
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://today.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


def rows_by_task(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The rows of a Today read, by task id."""
    return {str(r["task"]["id"]): r for r in answer.get("rows", [])}
