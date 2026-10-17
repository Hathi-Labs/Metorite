"""Shared helpers of the WS-54 GitHub R8 suites (``test_github_*_r8.py``).

Spec: ``project-docs/specs/github_integration.md``. Each helper seeds as the
RLS-bypassing admin of the ``promoted`` catalog and stamps
``organization_id`` itself. The suites then act as the NON-privileged role
``acb_app_h3rls``, through ``tenant_engine_scope``.

Not a test module: the name starts with ``_``, so pytest collects nothing here.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from tests.unit.test_h3_rls_promotion_rehearsal import _APP_ROLE

ROOT = Path(__file__).resolve().parents[2]
FN = "public.github_installation_org(bigint)"
SUPABASE_ROLES = ("anon", "authenticated", "service_role")


def migration() -> Path:
    """The GH-2 migration, found by CONTENT and never by number (R1)."""
    hits = [
        p for p in sorted((ROOT / "infra" / "postgres").glob("*.sql"))
        if "FUNCTION public.github_installation_org" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one GH-2 migration, found {hits}"
    return hits[0]


def grant_lookup(p: Any) -> None:
    """The lookup goes to ``acb_app`` only. The rehearsal role has a
    suite-private name, so it gets the same grant here, as the superuser."""
    with p.admin_engine.begin() as c:
        c.execute(text(f"GRANT EXECUTE ON FUNCTION {FN} TO {_APP_ROLE}"))


def app_dsn(p: Any) -> str:
    return p.app_url.render_as_string(hide_password=False)


def seed_member(p: Any, org: str, email: str) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
            "ON CONFLICT DO NOTHING"), {"e": email, "o": org})


def seed_project(
    p: Any, org: str, email: str, *, personal_owner: str | None = None,
    lanes: tuple[tuple[str, str], ...] = (("To do", "todo"), ("In review", "in_progress"),
                                          ("Done", "done"), ("Cancelled", "cancelled"),
                                          ("Triage", "triage")),
) -> tuple[str, dict[str, str]]:
    """A project that owns its statuses, and its lanes by category."""
    project = str(uuid.uuid4())
    by_category: dict[str, str] = {}
    seed_member(p, org, email)
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
            "personal_owner) VALUES (:id, :o, 'gh', :e, true, :po)"),
            {"id": project, "o": org, "e": email, "po": personal_owner})
        for position, (name, category) in enumerate(lanes):
            sid = str(uuid.uuid4())
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, "
                "category, position) VALUES (:id, :o, :p, :n, :c, :pos)"),
                {"id": sid, "o": org, "p": project, "n": name, "c": category,
                 "pos": (position + 1) * 10})
            by_category.setdefault(category, sid)
    return project, by_category


_TASK_NUMBERS: dict[str, int] = {}


def seed_task(
    p: Any, org: str, email: str, *, project: str | None = None,
    lanes: dict[str, str] | None = None, category: str = "todo",
    task_id: str | None = None, archived: bool = False, parent: str | None = None,
    personal_owner: str | None = None, recurring: bool = False,
) -> str:
    """One task in *org*. A new project is made unless *project* is given.
    *recurring* gives it a daily ``pm_recurrences`` rule."""
    if project is None or lanes is None:
        project, lanes = seed_project(p, org, email, personal_owner=personal_owner)
    task = task_id or str(uuid.uuid4())
    number = _TASK_NUMBERS[project] = _TASK_NUMBERS.get(project, 0) + 1
    with p.admin_engine.begin() as c:
        rule = None
        if recurring:
            rule = str(c.execute(text(
                "INSERT INTO pm_recurrences (organization_id, project_id, freq, created_by) "
                "VALUES (CAST(:o AS uuid), CAST(:p AS uuid), 'daily', :e) RETURNING id"),
                {"o": org, "p": project, "e": email}).scalar_one())
        c.execute(text(
            "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, "
            "task_number, status_id, title, created_by, archived_at, parent_task_id, "
            "recurrence_id) VALUES (:id, :o, :p, :p, :n, :s, 'Fix the extruder', :e, "
            "CASE WHEN :arch THEN now() ELSE NULL END, CAST(:parent AS uuid), "
            "CAST(:rule AS uuid))"),
            {"id": task, "o": org, "p": project, "n": number, "s": lanes[category],
             "e": email, "arch": archived, "parent": parent, "rule": rule})
    return task


def seed_github_row(
    p: Any, org: str, installation_id: int | None, *, status: str = "connected",
    auth: str = "app", extra: dict[str, Any] | None = None,
) -> str:
    """One ``github`` connection row of *org*, as the admin."""
    meta: dict[str, Any] = {"auth": auth}
    if installation_id is not None:
        meta["installation_id"] = str(installation_id)
        meta["account_login"] = f"acct-{installation_id}"
        meta["account_type"] = "Organization"
    meta.update(extra or {})
    with p.admin_engine.begin() as c:
        return str(c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status, provider_meta, connected_by) VALUES (CAST(:o AS uuid), "
            "'github', 'integrations', 'org', :s, CAST(:m AS jsonb), 'admin@gh.test') "
            "ON CONFLICT (organization_id, provider, owning_app, scope_kind, "
            "(COALESCE(member_email, ''))) DO UPDATE SET status = EXCLUDED.status, "
            "provider_meta = EXCLUDED.provider_meta RETURNING id"),
            {"o": org, "s": status, "m": json.dumps(meta)}).scalar_one())


def clear_github(p: Any) -> None:
    """Delete every GitHub row, reference and activity the suites wrote."""
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE provider = 'github'"))
        c.execute(text("DELETE FROM pm_task_refs"))


def scalar(p: Any, sql: str, **params: Any) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(sql), params).scalar()


def rows(p: Any, sql: str, **params: Any) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(sql), params).all())
