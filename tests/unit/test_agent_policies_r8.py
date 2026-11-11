"""WS-MCP SW-94 — one policy store for agent work, on a real Postgres (R8).

Spec: ``project-docs/specs/agent_coordination.md`` §3.11.5 ("One policy store")
and the slice SW-94 of §5.11. This file holds each item of its "Done when":

* the nearest project wins over the organization, and a tier row wins over
  ``any`` (:func:`agent_policies.policy_for`, on the walk of SW-31a);
* an unknown key gets 422, and a key on a scope kind it does not accept gets 422;
* a project lead writing an auth-path key gets 403;
* a policy cannot turn on what a flag turns off: flag ``warn`` and policy
  ``refuse`` give ``warn``;
* each save adds a row (and the trigger refuses an UPDATE).

It also holds the writer rules of SW-94 (a member pauses own agents and gets
403 on another member), the actor order (box, member, lane, org), the flag of
the store, and two organizations that read none of each other's rows (R5).

**Hermetic half** (no database): the migration is found by CONTENT (R1), the
module adds no tree walk of its own, the key registry, and the flag parsing.

**R8 half**: the real ``/agent-policies`` router and the real readers, as the
non-privileged role of the phase-4 rehearsal, in two organizations.

Mutations (R7), each turns the named test red. ``P`` is
``apps/services/gateway/gateway/routes/agent_policies.py``.

* P1. ``P`` ``policy_for``: read the org only (``ids = []``).
  ``test_the_nearest_project_wins_over_the_organization``.
* P2. ``P`` ``save_policy``: drop the auth-path 403 of a lead.
  ``test_a_lead_writing_an_auth_path_key_gets_403``.
* P3. ``P`` ``under_the_flag``: return the policy value as it is.
  ``test_the_flag_is_the_ceiling`` and ``test_flag_warn_and_policy_refuse_is_warn``.
* P4. ``P`` ``policy_for``: try ``any`` before the tier. ``test_a_tier_row_wins_over_any``.
* P5. ``P`` ``save_policy``: drop the self check of ``agents_paused``.
  ``test_a_member_pauses_own_agents_only``.
* P6. Migration: drop the trigger. ``test_a_saved_policy_cannot_change``.
* P7. ``P`` ``_assert_may_write``: drop the ``pause`` check, so a member resumes.
  ``test_a_member_cannot_resume_or_clear_a_pause``.
* P8 (F3). ``P`` ``may_write_any_policy``: check ``projects:settings:write``.
  ``test_a_manager_writes_plain_project_keys_only``.
* P9 (F4). ``P`` ``save_policy``: drop the ``pg_advisory_xact_lock`` statement.
  ``test_two_concurrent_saves_get_versions_one_and_two``.
* P10 (F5). ``P`` ``policy_for_actor``: drop the lower-casing of ``member``.
  ``test_a_member_pauses_own_agents_only``. ``P`` ``save_policy``: drop the
  lower-casing of the session email (``me``): the same test.
* P11 (F6). ``P`` ``retry_or_raise``: return for each violation.
  ``test_only_a_version_clash_is_retried``. Read the message text in
  ``_pg_error`` instead of the driver fields: ``test_the_driver_names_the_version_index``.
* P12 (F7). Drop a name from ``FLAGS_NOT_YET``. ``test_each_flag_is_a_setting_or_not_yet``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_common import get_settings
from fastapi import Request
from gateway.routes import agent_policies
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_REPO = Path(__file__).resolve().parents[2]
_MIGRATIONS = _REPO / "infra" / "postgres"


# ── Hermetic ────────────────────────────────────────────────────────────────


def _migration() -> str:
    """The migration that creates ``pm_agent_policies``, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("[0-9]*_*.sql"))
        if "CREATE TABLE IF NOT EXISTS pm_agent_policies" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0].read_text(encoding="utf-8")


def test_the_migration_is_a_tenant_table_with_history() -> None:
    sql = _migration()
    assert re.search(r"FORCE\s+ROW LEVEL SECURITY", sql) and "ENABLE ROW LEVEL SECURITY" in sql
    assert re.search(r"REFERENCES organization \(id\) ON DELETE CASCADE\s+DEFAULT", sql)
    assert "USING      (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    assert "WITH CHECK (organization_id = current_setting('app.tenant_id', true)::uuid)" in sql
    assert "BEFORE UPDATE ON pm_agent_policies" in sql
    assert "COALESCE(project_id" in sql and "COALESCE(scope_ref" in sql
    assert "GRANT" not in sql.replace("No GRANT", "")
    policies = (_MIGRATIONS / "generated" / "04_policies.sql").read_text(encoding="utf-8")
    assert "ALTER TABLE pm_agent_policies FORCE  ROW LEVEL SECURITY" in policies


def test_the_store_reuses_the_walk_of_sw31a() -> None:
    """SW-94 reuses the project-tree walk of ``agent_guidance.py`` and adds no
    walk of its own."""
    src = inspect.getsource(agent_policies)
    assert "WITH RECURSIVE" not in src.upper()
    assert "parent_project_id" not in src
    assert "from gateway.routes.agent_guidance import project_chains" in src
    assert "project_chains(db" in inspect.getsource(agent_policies.policy_for)


#: The keys of §3.11.5, and the auth-path keys of SW-94.
FIRST_KEYS = {
    "stages_required", "brief_required", "claim_scope_mode", "review_cap",
    "park_max_minutes", "overnight_allowed", "done_on", "question_auto_after_minutes",
    "lane_mode", "pr_open_to_review", "release_cutoff", "review_required",
    "required_checks", "budget_mode", "budget_minutes_day", "budget_writes_day",
    "loop_guard_repeats", "agents_paused",
}
AUTH_PATH = {"agents_paused", "budget_mode", "required_checks", "claim_scope_mode",
             "lane_mode", "question_auto_after_minutes", "pr_open_to_review"}


def test_the_registry_holds_the_first_keys() -> None:
    reg = agent_policies.REGISTRY
    assert set(reg) == FIRST_KEYS
    assert {k for k, s in reg.items() if s.auth_path} == AUTH_PATH
    assert {k for k, s in reg.items() if s.admin_only} == {"required_checks"}
    assert reg["release_cutoff"].default is None
    for name, spec in reg.items():
        assert set(spec.scopes) <= set(agent_policies.SCOPE_KINDS), name
        if spec.form == "actor":
            assert not spec.tiers and "project" not in spec.scopes, name
        else:
            assert set(spec.scopes) == {"organization", "project"}, name
        if spec.kind in ("mode", "choice"):
            assert spec.default in spec.modes, name
        else:
            agent_policies.check_value(spec, spec.default)
    assert reg["agents_paused"].form == "actor"
    assert "member" in reg["agents_paused"].scopes


def test_the_flag_of_the_store_is_per_org(monkeypatch: pytest.MonkeyPatch) -> None:
    org = str(uuid.uuid4())
    monkeypatch.setattr(get_settings(), "agent_policy_orgs", "")
    assert not agent_policies.policies_allowed(org)
    monkeypatch.setattr(get_settings(), "agent_policy_orgs", f"x, {org.upper()}")
    assert agent_policies.policies_allowed(org)
    assert not agent_policies.policies_allowed(str(uuid.uuid4()))
    monkeypatch.setattr(get_settings(), "agent_policy_orgs", "*")
    assert agent_policies.policies_allowed(org)
    assert not agent_policies.policies_allowed(None)


def test_the_flag_name_is_owner_only() -> None:
    """``AGENT_POLICY_ORGS`` is a platform setting, so a tenant cannot set it."""
    from acb_common import env_guard

    assert env_guard.is_platform_env("AGENT_POLICY_ORGS")


def test_each_flag_is_a_setting_or_not_yet() -> None:
    """Each master flag that a key names is a ``Settings`` field, or it is on
    the named list ``FLAGS_NOT_YET``. A name on that list is not a field yet,
    so the slice that adds the field must take the name off the list."""
    from acb_common.settings import Settings

    fields = set(Settings.model_fields)
    named = {n for s in agent_policies.REGISTRY.values() for n in (s.flag, s.gate) if n}
    assert named, "no key names a master flag"
    missing = sorted(n for n in named if n not in fields and n not in agent_policies.FLAGS_NOT_YET)
    assert missing == [], f"flags that are neither a setting nor on FLAGS_NOT_YET: {missing}"
    stale = sorted(n for n in agent_policies.FLAGS_NOT_YET if n in fields or n not in named)
    assert stale == [], f"FLAGS_NOT_YET names that are a setting now, or that no key names: {stale}"
    assert "agent_policy_orgs" in fields


class _Driver(Exception):
    """A driver error with the fields of asyncpg."""

    def __init__(self, sqlstate: str, constraint_name: str | None) -> None:
        super().__init__("duplicate key value violates unique constraint")
        self.sqlstate, self.constraint_name = sqlstate, constraint_name


def _integrity(sqlstate: str, name: str | None) -> Exception:
    from sqlalchemy.exc import IntegrityError

    return IntegrityError("INSERT ...", {}, _Driver(sqlstate, name))


def test_only_a_version_clash_is_retried() -> None:
    from fastapi import HTTPException

    assert agent_policies.retry_or_raise(
        _integrity("23505", agent_policies.VERSION_INDEX)) is None
    with pytest.raises(HTTPException) as other_index:
        agent_policies.retry_or_raise(_integrity("23505", "another_index"))
    with pytest.raises(HTTPException) as foreign_key:
        agent_policies.retry_or_raise(_integrity("23503", "pm_agent_policies_project_id_fkey"))
    with pytest.raises(HTTPException) as check:
        agent_policies.retry_or_raise(_integrity("23514", "pm_agent_policies_scope_shape"))
    with pytest.raises(HTTPException) as text_only:
        agent_policies.retry_or_raise(_integrity("23505", None))
    assert other_index.value.status_code == 422
    assert foreign_key.value.status_code == 404
    assert check.value.status_code == 422
    assert text_only.value.status_code == 422


def test_flag_mode_reads_a_map_from_org_to_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    org, other = str(uuid.uuid4()), str(uuid.uuid4())
    modes = ("off", "warn", "refuse")
    flags = {"agent_lane_modes": f"*=warn, {org}=refuse"}
    monkeypatch.setattr(agent_policies, "flag_raw", lambda n: flags.get(n, ""))
    assert agent_policies.flag_mode("agent_lane_modes", org, modes) == "refuse"
    assert agent_policies.flag_mode("agent_lane_modes", other, modes) == "warn"
    flags["agent_lane_modes"] = f"{org}=loud"
    assert agent_policies.flag_mode("agent_lane_modes", org, modes) == "off"
    assert agent_policies.flag_mode("agent_missing_modes", org, modes) == "off"


def test_flag_warn_and_policy_refuse_is_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    org = str(uuid.uuid4())
    spec = agent_policies.REGISTRY["claim_scope_mode"]
    flags = {"agent_claim_scope_modes": f"{org}=warn"}
    monkeypatch.setattr(agent_policies, "flag_raw", lambda n: flags.get(n, ""))
    assert agent_policies.under_the_flag(spec, org, "refuse", found=True) == "warn"
    assert agent_policies.under_the_flag(spec, org, "off", found=True) == "off"
    assert agent_policies.under_the_flag(spec, org, None, found=False) == "warn"


# ── R8: the helpers ─────────────────────────────────────────────────────────


def _principal(email: str, org: str, *, perms: tuple[str, ...], agent: bool = False) -> Any:
    from acb_auth import AgentScope, UserContext, UserRole, build_access

    scope = None
    if agent:
        scope = AgentScope(
            connection_id=str(uuid.uuid4()), scopes=("projects:read", "projects:write"),
            include_personal=False, client_origin="https://claude.ai",
            actor_via="mcp:claude.ai:claude-code",
        )
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(list(perms)),
        organization_id=org, agent=scope,
    )


SEE_ALL = ("feature:projects", "data:org:read")
ADMIN = (*SEE_ALL, "projects:settings:write", "admin:settings:manage")


def _app(users: dict[str, Any]) -> Any:
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(agent_policies.router)
    app.dependency_overrides[get_current_user] = _caller
    return app


class _Client:
    def __init__(self, client: httpx.AsyncClient, users: dict[str, Any]) -> None:
        self.client, self.users = client, users

    async def __call__(self, user: Any, body: dict[str, Any]) -> httpx.Response:
        key = str(id(user))
        self.users[key] = user
        return await self.client.post("/agent-policies", json=body, headers={"x-test-as": key})

    async def task(self, org: str, task_id: str, key: str, *, tier: str | None = None) -> Any:
        """``policy_for`` in a session of the app role, bound to *org*."""
        from acb_common.db import bind_tenant, release_tenant
        from gateway.db import tenant_session

        token = bind_tenant(org)
        try:
            async with tenant_session() as db:
                row = (await db.execute(
                    text("SELECT * FROM pm_tasks WHERE id = CAST(:t AS uuid)"), {"t": task_id},
                )).fetchone()
                return await agent_policies.policy_for(db, row, key, tier=tier)
        finally:
            release_tenant(token)

    async def actor(self, session_org: str, org: str, key: str, *, member: str | None = None,
                    box: str | None = None, lane: str | None = None) -> Any:
        """``policy_for_actor`` in a session of the app role, bound to *session_org*."""
        from acb_common.db import bind_tenant, release_tenant
        from gateway.db import tenant_session

        token = bind_tenant(session_org)
        try:
            async with tenant_session() as db:
                return await agent_policies.policy_for_actor(db, org, member, box, lane, key)
        finally:
            release_tenant(token)

    async def count(self, org: str) -> int:
        from acb_common.db import bind_tenant, release_tenant
        from gateway.db import tenant_session

        token = bind_tenant(org)
        try:
            async with tenant_session() as db:
                return int((await db.execute(
                    text("SELECT count(*) FROM pm_agent_policies"))).scalar())
        finally:
            release_tenant(token)


@contextlib.asynccontextmanager
async def _session(p: Any):
    from acb_common.db import clear_tenant, release_tenant

    users: dict[str, Any] = {}
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)), base_url="http://policies.test",
            ) as client,
        ):
            yield _Client(client, users)
    finally:
        release_tenant(scope)


def _project(c: Any, org: str, name: str, *, parent: str | None = None,
             lead: str | None = None, archived: bool = False) -> str:
    pid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_projects (id, organization_id, name, created_by, owns_statuses, "
        "parent_project_id, lead, status, archived_at) "
        "VALUES (:id, :o, :n, 'seed@policies.test', :own, CAST(:par AS uuid), :lead, "
        ":st, CASE WHEN :arch THEN now() END)"),
        {"id": pid, "o": org, "n": name, "own": parent is None, "par": parent, "lead": lead,
         "st": "archived" if archived else "active", "arch": archived})
    return pid


def _task(c: Any, org: str, project: str, root: str, number: int, *, status: str) -> str:
    tid = str(uuid.uuid4())
    c.execute(text(
        "INSERT INTO pm_tasks (id, organization_id, project_id, root_project_id, task_number, "
        "status_id, title, created_by) VALUES (:id, :o, :p, :r, :n, :s, 'a task', "
        "'seed@policies.test')"),
        {"id": tid, "o": org, "p": project, "r": root, "n": number, "s": status})
    return tid


def _user(c: Any, org: str, email: str) -> None:
    c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
        {"e": email, "o": org})


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Org A: a space S with a lead, P1 below it, P2 below P1, another space O
    with its own lead, and an archived project. Org B: one space with a task."""
    a, b = promoted.org_a, promoted.org_b
    ns = SimpleNamespace(p=promoted, a=a, b=b)
    ns.lead_s, ns.lead_o = "lead-s@policies.test", "lead-o@policies.test"
    ns.member, ns.other_member = "member@policies.test", "other@policies.test"
    ns.admin, ns.b_admin = "admin@policies.test", "admin-b@policies.test"
    ns.manager = "manager@policies.test"
    with promoted.admin_engine.begin() as c:
        for email in (ns.lead_s, ns.lead_o, ns.member, ns.other_member, ns.admin, ns.manager):
            _user(c, a, email)
        _user(c, b, ns.b_admin)
        ns.space = _project(c, a, "Space S", lead=ns.lead_s)
        ns.p1 = _project(c, a, "Project one", parent=ns.space)
        ns.p2 = _project(c, a, "Project two", parent=ns.p1)
        ns.other = _project(c, a, "Other space", lead=ns.lead_o)
        ns.archived = _project(c, a, "Old", parent=ns.space, archived=True)
        ns.b_space = _project(c, b, "B space")
        status = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status, "o": a, "p": ns.space})
        ns.task_p2 = _task(c, a, ns.p2, ns.space, 1, status=status)
        ns.task_space = _task(c, a, ns.space, ns.space, 2, status=status)
        ns.task_other = _task(c, a, ns.other, ns.other, 3, status=status)
        status_b = str(uuid.uuid4())
        c.execute(text(
            "INSERT INTO pm_task_statuses (id, organization_id, project_id, name, category, "
            "position) VALUES (:id, :o, :p, 'To do', 'todo', 10)"),
            {"id": status_b, "o": b, "p": ns.b_space})
        ns.task_b = _task(c, b, ns.b_space, ns.b_space, 1, status=status_b)
    ns.admin_u = _principal(ns.admin, a, perms=ADMIN)
    ns.lead_s_u = _principal(ns.lead_s, a, perms=SEE_ALL)
    ns.lead_o_u = _principal(ns.lead_o, a, perms=SEE_ALL)
    ns.member_u = _principal(ns.member, a, perms=SEE_ALL)
    # F5: the session email of the same member in mixed case.
    ns.member_mixed_u = _principal("Member@Policies.TEST", a, perms=SEE_ALL)
    # F3: a Projects settings admin who is not an org admin.
    ns.manager_u = _principal(ns.manager, a, perms=(*SEE_ALL, "projects:settings:write"))
    ns.agent_u = _principal(ns.admin, a, perms=ADMIN, agent=True)
    ns.b_admin_u = _principal(ns.b_admin, b, perms=ADMIN)
    return ns


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The flags that the module reads, with the store on for each org. A test
    sets a master flag here. The master flags of SW-46, SW-67 and SW-95 are
    not ``Settings`` fields yet, so the test sets the one reader."""
    values = {"agent_policy_orgs": "*"}
    monkeypatch.setattr(agent_policies, "flag_raw", lambda name: values.get(name, ""))
    return values


def _rows(pool: Any, sql: str, **params: Any) -> list[Any]:
    with pool.admin_engine.connect() as c:
        return list(c.execute(text(sql), params))


def _reset(w: Any) -> None:
    with w.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_agent_policies"))


def _body(key: str, kind: str, scope_id: str | None, value: Any, *, tier: str = "any",
          clear: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"key": key, "scope_kind": kind, "scope_id": scope_id,
                           "tier": tier, "value": value}
    if clear:
        out["clear"] = True
    return out


# ── R8: the order ───────────────────────────────────────────────────────────


@_DB_GATE
async def test_the_nearest_project_wins_over_the_organization(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        assert (await call(w.admin_u, _body("review_cap", "organization", None, 9))).status_code == 201
        org_only = await call.task(w.a, w.task_p2, "review_cap")
        assert (await call(w.lead_s_u, _body("review_cap", "project", w.space, 7))).status_code == 201
        space = await call.task(w.a, w.task_p2, "review_cap")
        assert (await call(w.lead_s_u, _body("review_cap", "project", w.p1, 3))).status_code == 201
        p1 = await call.task(w.a, w.task_p2, "review_cap")
        in_space = await call.task(w.a, w.task_space, "review_cap")
        other = await call.task(w.a, w.task_other, "review_cap")
    assert org_only.value == 9 and org_only.source["scope_kind"] == "organization"
    assert space.value == 7 and space.source["project_id"] == w.space
    # P2 has no row: its parent P1 is the nearest.
    assert p1.value == 3 and p1.source["project_id"] == w.p1
    assert in_space.value == 7
    assert other.value == 9


@_DB_GATE
async def test_no_row_gives_the_shipped_default(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        got = await call.task(w.a, w.task_p2, "release_cutoff")
        cap = await call.task(w.a, w.task_p2, "review_cap")
    assert got.value is None and got.source == {"scope_kind": "default"}
    assert cap.value == 5


@_DB_GATE
async def test_a_tier_row_wins_over_any(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        for body in (
            _body("review_required", "project", w.p1, False),
            _body("review_required", "project", w.p1, True, tier="HIGH"),
            _body("stages_required", "organization", None, ["build"]),
            _body("stages_required", "organization", None, ["build", "verify"], tier="HIGH"),
        ):
            assert (await call(w.admin_u, body)).status_code == 201, body
        high = await call.task(w.a, w.task_p2, "review_required", tier="HIGH")
        low = await call.task(w.a, w.task_p2, "review_required", tier="LOW")
        none = await call.task(w.a, w.task_p2, "review_required")
        stages_high = await call.task(w.a, w.task_p2, "stages_required", tier="HIGH")
        stages_none = await call.task(w.a, w.task_p2, "stages_required")
    assert high.value is True and high.source["tier"] == "HIGH"
    assert low.value is False and low.source["tier"] == "any"
    # A NULL tier is not LOW: it reads the `any` row only.
    assert none.value is False and none.source["tier"] == "any"
    assert stages_high.value == ["build", "verify"]
    assert stages_none.value == ["build"]


@_DB_GATE
async def test_a_nearer_any_row_wins_over_a_farther_tier_row(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        await call(w.admin_u, _body("review_required", "organization", None, True, tier="HIGH"))
        await call(w.admin_u, _body("review_required", "project", w.p1, False))
        got = await call.task(w.a, w.task_p2, "review_required", tier="HIGH")
    assert got.value is False and got.source["project_id"] == w.p1


@_DB_GATE
async def test_a_cleared_scope_gives_the_next_level(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        await call(w.admin_u, _body("review_cap", "organization", None, 9))
        await call(w.lead_s_u, _body("review_cap", "project", w.p1, 3))
        cleared = await call(w.lead_s_u, _body("review_cap", "project", w.p1, None, clear=True))
        got = await call.task(w.a, w.task_p2, "review_cap")
    assert cleared.status_code == 201 and cleared.json()["cleared"] is True
    assert cleared.json()["version"] == 2
    assert got.value == 9


# ── R8: the 422 rules ───────────────────────────────────────────────────────


@_DB_GATE
async def test_an_unknown_key_gets_422(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        r = await call(w.admin_u, _body("make_it_fast", "organization", None, True))
    assert r.status_code == 422 and "not a policy key" in r.text
    assert _rows(w.p, "SELECT 1 FROM pm_agent_policies") == []


@_DB_GATE
async def test_a_key_on_a_scope_kind_it_does_not_accept_gets_422(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        paused_on_project = await call(w.admin_u, _body("agents_paused", "project", w.p1, True))
        cap_on_member = await call(w.admin_u, _body("review_cap", "member", w.member, 2))
        budget_on_box = await call(
            w.admin_u, _body("budget_mode", "box", str(uuid.uuid4()), "warn"))
        tier_on_actor = await call(
            w.admin_u, _body("agents_paused", "organization", None, True, tier="HIGH"))
        bad_value = await call(w.admin_u, _body("review_cap", "organization", None, "five"))
        bad_mode = await call(w.admin_u, _body("brief_required", "organization", None, "loud"))
        bad_kind = await call(w.admin_u, _body("review_cap", "galaxy", None, 2))
    for r in (paused_on_project, cap_on_member, budget_on_box, tier_on_actor, bad_value,
              bad_mode, bad_kind):
        assert r.status_code == 422, r.text
    assert "takes the scope kinds" in paused_on_project.text
    assert _rows(w.p, "SELECT 1 FROM pm_agent_policies") == []


# ── R8: the writer rules ────────────────────────────────────────────────────


@_DB_GATE
async def test_a_lead_writing_an_auth_path_key_gets_403(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        results = {
            key: await call(w.lead_s_u, _body(key, "project", w.p1, value))
            for key, value in (
                ("claim_scope_mode", "warn"), ("lane_mode", "warn"),
                ("question_auto_after_minutes", 240), ("pr_open_to_review", True),
                ("required_checks", ["unit"]),
            )
        }
        admin = await call(w.admin_u, _body("claim_scope_mode", "project", w.p1, "warn"))
    for key, r in results.items():
        assert r.status_code == 403, (key, r.text)
    assert admin.status_code == 201
    assert len(_rows(w.p, "SELECT 1 FROM pm_agent_policies")) == 1


@_DB_GATE
async def test_a_lead_writes_a_plain_key_for_own_project_only(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        own = await call(w.lead_s_u, _body("brief_required", "project", w.p1, "warn"))
        not_lead = await call(w.lead_o_u, _body("brief_required", "project", w.p1, "warn"))
        member = await call(w.member_u, _body("brief_required", "project", w.p1, "warn"))
        org = await call(w.lead_s_u, _body("brief_required", "organization", None, "warn"))
        archived = await call(w.lead_s_u, _body("brief_required", "project", w.archived, "warn"))
        unknown = await call(w.admin_u, _body("brief_required", "project", str(uuid.uuid4()), "warn"))
        agent = await call(w.agent_u, _body("brief_required", "organization", None, "warn"))
    assert own.status_code == 201 and own.json()["written_by"] == w.lead_s
    assert not_lead.status_code == 403 and member.status_code == 403
    assert org.status_code == 403
    assert archived.status_code == 409
    assert unknown.status_code == 404
    assert agent.status_code == 403


@_DB_GATE
async def test_a_member_pauses_own_agents_only(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        # F5: a mixed-case session email and a lower-case scope id are one member.
        mine = await call(w.member_mixed_u, _body("agents_paused", "member", w.member, True))
        theirs = await call(w.member_u, _body("agents_paused", "member", w.other_member, True))
        org = await call(w.member_u, _body("agents_paused", "organization", None, True))
        budget = await call(w.member_u, _body("budget_mode", "lane", str(uuid.uuid4()), "off"))
        # F5: the reader gets the member's email in another case.
        paused = await call.actor(w.a, w.a, "agents_paused", member=w.member.upper())
        other = await call.actor(w.a, w.a, "agents_paused", member=w.other_member)
    assert mine.status_code == 201, mine.text
    assert mine.json()["scope_ref"] == w.member and mine.json()["written_by"] == w.member
    assert theirs.status_code == 403 and "own agents" in theirs.text
    assert org.status_code == 403 and budget.status_code == 403
    assert paused.value is True and other.value is False


@_DB_GATE
async def test_a_member_cannot_resume_or_clear_a_pause(world, flags) -> None:
    """F1. A member writes only a pause (true) of own agents. A resume (false)
    and a clear get 403, also on the member's own scope, so a member cannot
    lift a pause that an admin holds. The admin may do both."""
    w = world
    _reset(w)
    async with _session(w.p) as call:
        held = await call(w.admin_u, _body("agents_paused", "member", w.member, True))
        resume = await call(w.member_u, _body("agents_paused", "member", w.member, False))
        clear = await call(w.member_u, _body("agents_paused", "member", w.member, None, clear=True))
        still = await call.actor(w.a, w.a, "agents_paused", member=w.member)
        admin_resume = await call(w.admin_u, _body("agents_paused", "member", w.member, False))
        resumed = await call.actor(w.a, w.a, "agents_paused", member=w.member)
        admin_clear = await call(
            w.admin_u, _body("agents_paused", "member", w.member, None, clear=True))
    assert held.status_code == 201
    assert resume.status_code == 403 and "resume" in resume.text
    assert clear.status_code == 403 and "resume" in clear.text
    assert still.value is True
    assert admin_resume.status_code == 201 and resumed.value is False
    assert admin_clear.status_code == 201 and admin_clear.json()["cleared"] is True
    assert len(_rows(w.p, "SELECT 1 FROM pm_agent_policies")) == 3


@_DB_GATE
async def test_a_manager_writes_plain_project_keys_only(world, flags) -> None:
    """F3. A ``manager`` holds ``projects:settings:write`` and not
    ``admin:settings:manage``. It passes the lead floor of a project, so it
    writes a plain key there. It gets 403 on an auth-path key at project and
    at org scope, and on a plain key at org scope."""
    w = world
    _reset(w)
    async with _session(w.p) as call:
        plain = await call(w.manager_u, _body("review_cap", "project", w.p1, 4))
        auth_project = await call(w.manager_u, _body("claim_scope_mode", "project", w.p1, "warn"))
        auth_org = await call(w.manager_u, _body("claim_scope_mode", "organization", None, "warn"))
        paused_org = await call(w.manager_u, _body("agents_paused", "organization", None, True))
        plain_org = await call(w.manager_u, _body("review_cap", "organization", None, 4))
    assert plain.status_code == 201, plain.text
    for r in (auth_project, auth_org, paused_org, plain_org):
        assert r.status_code == 403, r.text
    assert len(_rows(w.p, "SELECT 1 FROM pm_agent_policies")) == 1


#: The saves of one round of the race, and the rounds.
RACE_PARTIES, RACE_ROUNDS = 2, 3


@_DB_GATE
async def test_two_concurrent_saves_get_versions_one_and_two(
    world, flags, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F4. Two saves of one scope at the same time get versions 1 and 2, with
    no duplicate and no 409. The saves wait at a barrier after the write
    check, just before the lock, so each sends its next statement before any
    commits. The retry has one attempt here, so only the advisory lock can
    keep both saves at 201."""
    w = world
    _reset(w)
    monkeypatch.setattr(agent_policies, "SAVE_ATTEMPTS", 1)
    real = agent_policies._assert_may_write
    gate: list[asyncio.Barrier] = []

    async def _check_then_wait(*args: Any, **kwargs: Any) -> None:
        await real(*args, **kwargs)
        await asyncio.wait_for(gate[0].wait(), timeout=20)

    monkeypatch.setattr(agent_policies, "_assert_may_write", _check_then_wait)
    async with _session(w.p) as call:
        for n in range(RACE_ROUNDS):
            _reset(w)
            gate[:] = [asyncio.Barrier(RACE_PARTIES)]
            rs = await asyncio.wait_for(asyncio.gather(
                *(call(w.lead_s_u, _body("review_cap", "project", w.p1, k + 1))
                  for k in range(RACE_PARTIES))), timeout=60)
            assert [r.status_code for r in rs] == [201] * RACE_PARTIES, (n, [r.text for r in rs])
            assert sorted(r.json()["version"] for r in rs) == [1, 2]
            rows = _rows(w.p, "SELECT version FROM pm_agent_policies ORDER BY version")
            assert [r.version for r in rows] == [1, 2]


@_DB_GATE
async def test_the_driver_names_the_version_index(world, flags) -> None:
    """F6. A real clash on the version index, through the gateway driver,
    reads as a retry. ``_pg_error`` reads the driver fields, not the text."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.db import tenant_session
    from sqlalchemy.exc import IntegrityError

    w = world
    _reset(w)
    sql = text("INSERT INTO pm_agent_policies (scope_kind, key, value, version, written_by) "
               "VALUES ('organization', 'review_cap', '3'::jsonb, 1, 'a@policies.test')")
    caught: list[BaseException] = []
    async with _session(w.p):
        token = bind_tenant(w.a)
        try:
            with pytest.raises(IntegrityError) as clash:
                async with tenant_session() as db:
                    await db.execute(sql)
                    await db.execute(sql)
            caught.append(clash.value)
        finally:
            release_tenant(token)
    codes, names = agent_policies._pg_error(caught[0])
    assert "23505" in codes and agent_policies.VERSION_INDEX in names
    assert agent_policies.retry_or_raise(caught[0]) is None


# ── R8: the actor order ─────────────────────────────────────────────────────


@_DB_GATE
async def test_the_actor_order_is_box_member_lane_org(world, flags) -> None:
    """Each write here is an admin's (F1): a member may write only a pause."""
    w = world
    _reset(w)
    box, lane = str(uuid.uuid4()), str(uuid.uuid4())
    async with _session(w.p) as call:
        await call(w.admin_u, _body("agents_paused", "organization", None, True))
        org = await call.actor(w.a, w.a, "agents_paused", member=w.member, box=box, lane=lane)
        await call(w.admin_u, _body("agents_paused", "lane", lane, False))
        by_lane = await call.actor(w.a, w.a, "agents_paused", member=w.member, box=box, lane=lane)
        await call(w.admin_u, _body("agents_paused", "member", w.member, True))
        by_member = await call.actor(w.a, w.a, "agents_paused", member=w.member, box=box,
                                     lane=lane)
        await call(w.admin_u, _body("agents_paused", "box", box, False))
        by_box = await call.actor(w.a, w.a, "agents_paused", member=w.member, box=box, lane=lane)
        no_box = await call.actor(w.a, w.a, "agents_paused", member=w.member, lane=lane)
    assert (org.value, org.source["scope_kind"]) == (True, "organization")
    assert (by_lane.value, by_lane.source["scope_kind"]) == (False, "lane")
    assert (by_member.value, by_member.source["scope_kind"]) == (True, "member")
    assert (by_box.value, by_box.source["scope_kind"]) == (False, "box")
    assert no_box.value is True


# ── R8: the master flag ─────────────────────────────────────────────────────


@_DB_GATE
async def test_the_flag_is_the_ceiling(world, flags) -> None:
    """A policy cannot turn on what a flag turns off. Flag ``warn`` with policy
    ``refuse`` gives ``warn``. The flag is the default of the org."""
    w = world
    _reset(w)
    async with _session(w.p) as call:
        no_row_off = await call.task(w.a, w.task_p2, "claim_scope_mode")
        await call(w.admin_u, _body("claim_scope_mode", "project", w.p1, "refuse"))
        flag_off = await call.task(w.a, w.task_p2, "claim_scope_mode")
        flags["agent_claim_scope_modes"] = f"{w.a}=warn"
        flag_warn = await call.task(w.a, w.task_p2, "claim_scope_mode")
        no_row_warn = await call.task(w.a, w.task_other, "claim_scope_mode")
        flags["agent_claim_scope_modes"] = "*=refuse"
        flag_refuse = await call.task(w.a, w.task_p2, "claim_scope_mode")
        await call(w.admin_u, _body("budget_mode", "organization", None, "stop"))
        flags["agent_budget_modes"] = f"{w.a}=warn"
        budget = await call.actor(w.a, w.a, "budget_mode", member=w.member)
        flags.pop("agent_budget_modes")
        budget_off = await call.actor(w.a, w.a, "budget_mode", member=w.member)
    assert no_row_off.value == "off" and flag_off.value == "off"
    assert flag_warn.value == "warn" and flag_warn.source["project_id"] == w.p1
    assert no_row_warn.value == "warn" and no_row_warn.source == {"scope_kind": "default"}
    assert flag_refuse.value == "refuse"
    assert budget.value == "warn" and budget_off.value == "off"


@_DB_GATE
async def test_a_gated_key_gives_its_default_while_the_gate_is_off(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        await call(w.admin_u, _body("question_auto_after_minutes", "organization", None, 240))
        off = await call.task(w.a, w.task_p2, "question_auto_after_minutes")
        flags["agent_auto_answer_orgs"] = w.a
        on = await call.task(w.a, w.task_p2, "question_auto_after_minutes")
    assert off.value is None and on.value == 240


# ── R8: history ─────────────────────────────────────────────────────────────


@_DB_GATE
async def test_each_save_adds_a_row(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        got = [
            (await call(w.lead_s_u, _body("done_on", "project", w.p1, v))).json()["version"]
            for v in ("merge", "live", "merge")
        ]
        newest = await call.task(w.a, w.task_p2, "done_on")
    assert got == [1, 2, 3]
    rows = _rows(w.p, "SELECT version, value, written_by FROM pm_agent_policies "
                      "WHERE project_id = :p ORDER BY version", p=w.p1)
    assert [(r.version, r.value) for r in rows] == [(1, "merge"), (2, "live"), (3, "merge")]
    assert all(r.written_by == w.lead_s for r in rows)
    assert newest.value == "merge" and newest.source["version"] == 3


@_DB_GATE
async def test_a_saved_policy_cannot_change(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        await call(w.admin_u, _body("review_cap", "organization", None, 4))
    with pytest.raises(DBAPIError, match="cannot change"), w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_policies SET value = '9'::jsonb"))


# ── R8: the flag of the store and two organizations ─────────────────────────


@_DB_GATE
async def test_the_store_flag_off_is_404_and_the_default(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        await call(w.admin_u, _body("review_cap", "organization", None, 9))
        flags["agent_policy_orgs"] = ""
        r = await call(w.admin_u, _body("review_cap", "organization", None, 8))
        got = await call.task(w.a, w.task_p2, "review_cap")
    assert r.status_code == 404
    assert got.value == 5 and got.source == {"scope_kind": "default"}


@_DB_GATE
async def test_two_orgs_read_none_of_each_other(world, flags) -> None:
    w = world
    _reset(w)
    async with _session(w.p) as call:
        assert (await call(w.admin_u, _body("review_cap", "organization", None, 9))).status_code == 201
        assert (await call(w.admin_u, _body("agents_paused", "organization", None, True))).status_code == 201
        b_task = await call.task(w.b, w.task_b, "review_cap")
        b_actor = await call.actor(w.b, w.b, "agents_paused", member=w.b_admin)
        # Org B's session that names org A reads no row of A (RLS).
        b_names_a = await call.actor(w.b, w.a, "agents_paused", member=w.member)
        b_count = await call.count(w.b)
        # Org B's admin cannot reach a project of org A.
        b_on_a = await call(w.b_admin_u, _body("review_cap", "project", w.p1, 1))
        assert (await call(w.b_admin_u, _body("review_cap", "organization", None, 2))).status_code == 201
        a_task = await call.task(w.a, w.task_p2, "review_cap")
        a_count = await call.count(w.a)
    assert b_task.value == 5 and b_actor.value is False and b_names_a.value is False
    assert b_count == 0
    assert b_on_a.status_code == 404
    assert a_task.value == 9 and a_count == 2
    stamped = _rows(w.p, "SELECT organization_id FROM pm_agent_policies WHERE value = '2'::jsonb")
    assert [str(r.organization_id) for r in stamped] == [w.b]
