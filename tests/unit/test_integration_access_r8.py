"""ACC-1 on a real database (R8): who can use an integration.

Spec: ``project-docs/specs/integrations_console.md`` §3.9 and §7.5, rows
ACC-a to ACC-g. Metorite task 3987.

The reads run on a phase-4 promoted scratch database, as the non-privileged
app role, with the org bound and ``IDENTITY_CUTOVER`` on, so FORCE RLS binds.
``tenant_engine_scope`` points the one engine at it, and that engine runs on
asyncpg, the driver of production. The roles are the REAL system roles of
``provision_org_roles``. The rules sit in ``organization.settings`` as the
route writes them.

Run (a PRIVATE database, one R8 suite at a time, because the ``promoted``
fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_integration_access_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

Each refusal test has a positive control: the same member passes before the
rule, or a listed member passes with it.

**Mutations (R7).**

| # | Mutation | Test that fails |
|---|---|---|
| M1 | ``resolve_access`` skips ``_apply_rules`` | ``test_acc_b_*``, ``test_acc_c_*``, ``test_acc_d_*``, ``test_acc_e_*`` |
| M2 | ``narrow`` lets an exact allow override pass | ``test_acc_d_an_exact_allow_and_a_guest_are_refused`` |
| M3 | the rules read runs with no savepoint | ``test_acc_g_a_failed_read_refuses_integrations_only`` (raise mode) |
| M4 | the write drops the ``COALESCE`` of a missing parent | ``test_acc_f_the_write_persists_and_org_b_does_not_change`` |
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.admin", reason="gateway not installed")

import httpx
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
GROUP = f"ops{TAG}"
#: A second team of org A that holds the refused member. No rule names it.
ROOM_GROUP = f"room{TAG}"

#: The people of org A: their system role and their overrides.
PEOPLE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "owner": ("owner", ()),
    "admin": ("admin", ()),
    "manager": ("manager", ()),
    "member": ("member", ()),
    "guest": ("guest", ()),
    "listed": ("member", ()),
    "team": ("member", ()),
    "allow": ("member", (("integrations:use:mcp-x", "allow"),)),
    "guestlisted": ("guest", ()),
    "manageover": ("member", (("integrations:manage", "allow"),)),
    "admindeny": ("admin", (("integrations:manage", "deny"),)),
}
FIVE_ROLES = ("owner", "admin", "manager", "member", "guest")


def _email(key: str) -> str:
    return f"{key}.{TAG}@acc1.test"


MEMBER_B = f"memberb.{TAG}@acc1b.test"
LISTED_B = f"listedb.{TAG}@acc1b.test"


def _use(service: str) -> str:
    return f"integrations:use:{service}"


def _seed_person(c: Any, org: str, email: str, role: str,
                 overrides: tuple[tuple[str, str], ...]) -> str:
    rid = str(c.execute(text(
        "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) AND slug = :s"),
        {"o": org, "s": role}).scalar())
    uid = str(c.execute(text(
        "INSERT INTO app_user (email, display_name, role, status, organization_id) "
        "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
        {"e": email, "o": org}).scalar())
    c.execute(text(
        "INSERT INTO user_role (user_id, role_id, organization_id) "
        "VALUES (CAST(:u AS uuid), CAST(:r AS uuid), CAST(:o AS uuid))"),
        {"u": uid, "r": rid, "o": org})
    for permission, effect in overrides:
        c.execute(text(
            "INSERT INTO user_permission_override (user_id, permission, effect, reason, "
            "set_by, organization_id) VALUES (CAST(:u AS uuid), :p, :x, 'ACC-1 R8', "
            "'seed@acc1.test', CAST(:o AS uuid))"),
            {"u": uid, "p": permission, "x": effect, "o": org})
    return uid


def _add_to_group(c: Any, org: str, gid: str, uid: str) -> None:
    c.execute(text(
        "INSERT INTO org_group_member (group_id, user_id, role, added_by, organization_id) "
        "VALUES (CAST(:g AS uuid), CAST(:u AS uuid), 'member', 'seed', CAST(:o AS uuid))"),
        {"g": gid, "u": uid, "o": org})


@pytest.fixture(scope="module")
def people(promoted):  # noqa: F811 - a fixture
    a, b = promoted.org_a, promoted.org_b
    ids: dict[str, str] = {}
    with promoted.admin_engine.begin() as c:
        for org in (a, b):
            c.execute(text("SELECT provision_org_roles(:o)"), {"o": org})
        for key, (role, overrides) in PEOPLE.items():
            ids[key] = _seed_person(c, a, _email(key), role, overrides)
        ids["memberb"] = _seed_person(c, b, MEMBER_B, "member", ())
        ids["listedb"] = _seed_person(c, b, LISTED_B, "member", ())
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name, created_by) "
            "VALUES (CAST(:o AS uuid), :s, :s, 'seed') RETURNING id"),
            {"o": a, "s": GROUP}).scalar())
        _add_to_group(c, a, gid, ids["team"])
        room_gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name, created_by) "
            "VALUES (CAST(:o AS uuid), :s, :s, 'seed') RETURNING id"),
            {"o": a, "s": ROOM_GROUP}).scalar())
        _add_to_group(c, a, room_gid, ids["member"])
        # The same slug in org B, with org B's member in it. A rule of org A
        # that names the group must never reach it.
        gid_b = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name, created_by) "
            "VALUES (CAST(:o AS uuid), :s, :s, 'seed') RETURNING id"),
            {"o": b, "s": GROUP}).scalar())
        _add_to_group(c, b, gid_b, ids["memberb"])
        # One MCP server of org A, so the closed list holds `mcp-x`.
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status) VALUES (CAST(:o AS uuid), 'mcp:x', 'integrations', 'org', "
            "'connected')"), {"o": a})
    yield SimpleNamespace(
        p=promoted, a=a, b=b, ids=ids, gid=gid,
        dsn=promoted.app_url.render_as_string(hide_password=False),
    )
    with promoted.admin_engine.begin() as c:
        for org in (a, b):
            c.execute(text(
                "UPDATE organization SET settings = '{}'::jsonb WHERE id = CAST(:o AS uuid)"),
                {"o": org})
            c.execute(text("DELETE FROM integration_connections "
                           "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
        c.execute(text("DELETE FROM org_group WHERE slug IN (:s, :e, :r)"),
                  {"s": GROUP, "e": f"empty{TAG}", "r": ROOM_GROUP})
        c.execute(text("DELETE FROM chat_session WHERE user_id LIKE :t"), {"t": f"%.{TAG}@%"})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :t"), {"t": f"%.{TAG}@%"})


@pytest.fixture
def ns(people, monkeypatch):
    """A clean slate for each test: no rule in either org, an empty cache, the
    cutover on, the access tables present."""
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    _settings(people, people.a, {})
    _settings(people, people.b, {})
    access_mod.invalidate()
    yield people
    access_mod.invalidate()
    _settings(people, people.a, {})
    _settings(people, people.b, {})


def _settings(ns: Any, org: str, value: Any) -> None:
    """Write the WHOLE ``organization.settings`` of *org*, as the superuser."""
    with ns.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE organization SET settings = CAST(:v AS jsonb) WHERE id = CAST(:o AS uuid)"),
            {"o": org, "v": value if isinstance(value, str) else json.dumps(value)})


def _rules(ns: Any, value: Any, org: str | None = None) -> None:
    """Store *value* as the ``integration_access`` of *org* (org A by default)."""
    _settings(ns, org or ns.a, {"integration_access": value})


def _stored(ns: Any, org: str) -> Any:
    with ns.p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT settings FROM organization WHERE id = CAST(:o AS uuid)"), {"o": org}).scalar()


async def _resolve(ns: Any, email: str, org: str | None = None, *, cache: bool = False) -> Any:
    """``resolve_access`` as the request path runs it: the org bound, on the
    app role, through asyncpg."""
    import acb_auth.access as access_mod
    from acb_common.db import bind_tenant, release_tenant

    async with tenant_engine_scope(ns.dsn):
        token = bind_tenant(org or ns.a)
        try:
            return await access_mod.resolve_access(email, use_cache=cache)
        finally:
            release_tenant(token)


async def _resolve_in_org(ns: Any, email: str, org: str | None = None) -> Any:
    import acb_auth.access as access_mod

    async with tenant_engine_scope(ns.dsn):
        return await access_mod.resolve_access_in_org(email, org or ns.a)


def _expected(ns: Any, email: str) -> Any:
    """The access of before ACC-1: ``_ACCESS_SQL`` and ``build_access`` alone."""
    import acb_auth.access as access_mod
    from acb_auth import build_access

    with ns.p.admin_engine.connect() as c:
        row = c.execute(text(access_mod._ACCESS_SQL), {"email": email}).mappings().first()
    overrides = []
    for entry in row["overrides"] or []:
        perm, _, effect = str(entry).rpartition("=")
        overrides.append((perm, effect))
    return build_access(row["role_permissions"] or [], overrides, roles=row["roles"] or [],
                        is_active=row["status"] == "active")


def _user(email: str, access: Any, org: str) -> Any:
    from acb_auth import UserContext, UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, access=access,
                       organization_id=org)


# ── ACC-a: no rule, no change ────────────────────────────────────────────────


@pytest.mark.parametrize("key", FIVE_ROLES)
async def test_acc_a_with_no_rule_each_role_resolves_as_before(ns, key) -> None:
    email = _email(key)
    expected = _expected(ns, email)
    assert await _resolve(ns, email) == expected
    assert await _resolve_in_org(ns, email) == expected
    # Other keys of the settings, and a rule in org B, change nothing in org A.
    _settings(ns, ns.a, {"theme": {"accent": "x"}})
    _rules(ns, {"mcp-x": {"subjects": []}, "zoho-crm": {"subjects": []}}, org=ns.b)
    assert await _resolve(ns, email) == expected
    assert await _resolve_in_org(ns, email) == expected


# ── ACC-b: each site refuses a refused member ────────────────────────────────


async def _authorizer(ns: Any, email: str, thread: str | None = None) -> Any:
    import acb_auth.access as access_mod
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator import executor

    access_mod.invalidate()
    async with tenant_engine_scope(ns.dsn):
        token = bind_tenant(ns.a)
        try:
            return await executor._integration_authorizer({"user_email": email}, thread)
        finally:
            release_tenant(token)


async def test_acc_b_the_authorizer_drops_the_mcp_server(ns) -> None:
    pytest.importorskip("orchestrator")
    from gateway.connections.mcp_tools import _may_use
    from orchestrator._tool_injection import _maf_servers

    server = SimpleNamespace(name="x", transport="http-sse", url="https://mcp.acc1.test/x",
                             headers={})
    member, listed = _email("member"), _email("listed")
    before = await _authorizer(ns, member)
    assert before("mcp-x") is True and len(_maf_servers([server], before)) == 1
    assert _may_use(_user(member, await _resolve(ns, member), ns.a).has_permission, "x")

    _rules(ns, {"mcp-x": {"subjects": [listed]}})
    after = await _authorizer(ns, member)
    assert after("mcp-x") is False
    assert _maf_servers([server], after) == []
    assert after("apollo") is True, "a rule narrows its own service only"
    assert _may_use(_user(member, await _resolve(ns, member), ns.a).has_permission, "x") is False

    passes = await _authorizer(ns, listed)
    assert passes("mcp-x") is True and len(_maf_servers([server], passes)) == 1
    assert _may_use(_user(listed, await _resolve(ns, listed), ns.a).has_permission, "x")


@pytest.fixture
def world(tmp_path, monkeypatch, ns):
    """The harness of MCP-C3 with the REAL access reads (``access=False``)."""
    pytest.importorskip("agent_framework", reason="MAF not installed")
    from tests.unit.test_delegated_integration_filter import install_world

    w = install_world(tmp_path, monkeypatch, access=False)
    w.ns = ns
    return w


def _run(world: Any, member: str, *, delegated: bool, session: str | None = None) -> Any:
    """One run under a web chat of *member*. *delegated* names nobody in the
    payload, so the run acts as the member of its parent (MCP-C3)."""
    from orchestrator import executor

    from tests.unit.test_delegated_integration_filter import AGENT, parent_chat

    payload: dict[str, Any] = {"message": "the deals of Welmont", "mode": "sub_task"}
    if not delegated:
        payload["user_email"] = member

    async def go() -> Any:
        async with tenant_engine_scope(world.ns.dsn):
            return await executor.run_agent(AGENT, payload, run_id="r-acc1",
                                            organization_id=world.ns.a)

    with parent_chat(world, member, session=session or str(uuid.uuid4()), org=world.ns.a):
        return asyncio.run(go())


def test_acc_b_no_credential_of_a_ruled_registry_key_reaches_the_run(world) -> None:
    import acb_auth.access as access_mod

    ns = world.ns
    _run(world, _email("member"), delegated=False)
    assert world.state.ran == [["apollo", "zoho-crm"]], "the positive control"
    world.state.ran.clear()
    _rules(ns, {"zoho-crm": {"subjects": [_email("listed")]}})
    access_mod.invalidate()
    _run(world, _email("member"), delegated=False)
    assert world.state.ran == [["apollo"]], world.state.ran
    world.state.ran.clear()
    access_mod.invalidate()
    _run(world, _email("listed"), delegated=False)
    assert world.state.ran == [["apollo", "zoho-crm"]], world.state.ran


@pytest.fixture
def api(ns, monkeypatch):
    """The API-3 routes with the flag on, a fake wire and a fake Redis, and a
    grant that a member may hold (the test opens one; see the test)."""
    pytest.importorskip("gateway.routes.api_connections")
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from gateway.connections.api import connect
    from gateway.routes import api_connections as routes

    from tests.unit import _api_call_fakes as f

    monkeypatch.setattr(get_settings(), "acb_master_key", "acc1-r8-master-key-for-the-blob",
                        raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    f.flag(monkeypatch)
    f.use_redis(monkeypatch, f.FakeRedis())
    wire = f.use_wire(monkeypatch, f.Wire())
    # ⚠️ Today each API connection carries `grant = admin`, and only a holder
    # of `integrations:manage` passes it. Such a member passes every rule too,
    # so no rule can change the route today. The test opens a member grant to
    # prove that the route reads the narrowed access when one exists.
    monkeypatch.setattr(routes, "_GRANTS", (connect.GRANT_ADMIN, "members"))
    yield SimpleNamespace(f=f, wire=wire)
    with ns.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE organization_id = "
                       "CAST(:o AS uuid) AND provider = 'api:zoho_books'"), {"o": ns.a})


async def test_acc_b_the_api_route_answers_404(ns, api) -> None:
    from acb_common.db import tenant_session
    from gateway.connections import store

    f = api.f
    cred = f.credential()
    async with tenant_engine_scope(ns.dsn), tenant_session(ns.a) as db:
        await store.save_credential(db, ns.a, "api:zoho_books", "integrations", cred,
                                    connected_by=_email("admin"), scopes=cred.scopes)
    with ns.p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET provider_meta = jsonb_set(provider_meta, "
            "'{grant}', '\"members\"') WHERE organization_id = CAST(:o AS uuid) "
            "AND provider = 'api:zoho_books'"), {"o": ns.a})
    path = "/connections/api/zoho_books/read"

    async def call(email: str) -> httpx.Response:
        who = _user(email, await _resolve(ns, email), ns.a)
        async with tenant_engine_scope(ns.dsn):
            return await f.call(who, "POST", path, body=f.read())

    member, listed = _email("member"), _email("listed")
    assert (await call(member)).status_code == 200, "the positive control"
    _rules(ns, {"api-zoho_books": {"subjects": [listed]}})
    refused = await call(member)
    assert (refused.status_code, refused.json()) == (404, {"detail": "Not Found"})
    assert (await call(listed)).status_code == 200
    assert len(api.wire.sent) == 2


# ── ACC-c: a person, a new member, a team ────────────────────────────────────


async def test_acc_c_a_listed_person_passes_and_a_new_member_is_refused(ns) -> None:
    listed = _email("listed")
    _rules(ns, {"mcp-x": {"subjects": [listed.upper()]}})
    assert (await _resolve(ns, listed)).has(_use("mcp-x"))
    late = f"late.{TAG}@acc1.test"
    with ns.p.admin_engine.begin() as c:
        _seed_person(c, ns.a, late, "member", ())
    access = await _resolve(ns, late)
    assert access.is_active
    assert access.has(_use("zoho-crm")), "the positive control: no rule there"
    assert not access.has(_use("mcp-x")), "a member who joins later is not on the list"


async def test_acc_c_a_team_member_passes_until_the_route_removes_them(ns) -> None:
    from acb_auth import UserContext, UserRole
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.admin import groups

    team = _email("team")
    _rules(ns, {"mcp-x": {"subjects": [f"group:{GROUP}"]}})
    assert (await _resolve(ns, team, cache=True)).has(_use("mcp-x"))
    assert not (await _resolve(ns, _email("member"))).has(_use("mcp-x"))
    # Org B's member of a group with the same slug is untouched by org A's rule.
    assert (await _resolve(ns, MEMBER_B, ns.b)).has(_use("mcp-x"))

    admin_email = _email("admin")
    admin = UserContext(email=admin_email, role=UserRole.EXECUTIVE,
                        access=await _resolve(ns, admin_email), organization_id=ns.a)
    async with tenant_engine_scope(ns.dsn):
        token = bind_tenant(ns.a)
        try:
            out = await groups.remove_group_member(GROUP, team, admin=admin)
        finally:
            release_tenant(token)
    assert out["status"] == "removed"
    # The cached answer is gone: the next resolve refuses, with no restart.
    assert not (await _resolve(ns, team, cache=True)).has(_use("mcp-x"))
    with ns.p.admin_engine.begin() as c:
        _add_to_group(c, ns.a, ns.gid, ns.ids["team"])


# ── ACC-d: who passes a rule, and who does not ───────────────────────────────


async def test_acc_d_a_holder_of_integrations_manage_passes(ns) -> None:
    _rules(ns, {"mcp-x": {"subjects": []}})
    for key in ("owner", "admin", "manageover"):
        assert (await _resolve(ns, _email(key))).has(_use("mcp-x")), key
    # An admin whose override denies `integrations:manage` does not hold it.
    assert not (await _resolve(ns, _email("admindeny"))).has(_use("mcp-x"))
    assert (await _resolve(ns, _email("admindeny"))).has(_use("zoho-crm"))


async def test_acc_d_an_exact_allow_and_a_guest_are_refused(ns) -> None:
    allow, guest = _email("allow"), _email("guestlisted")
    assert (await _resolve(ns, allow)).has(_use("mcp-x")), "the positive control"
    _rules(ns, {"mcp-x": {"subjects": [guest]}})
    access = await _resolve(ns, allow)
    assert not access.has(_use("mcp-x")), "an exact allow override does not pass a rule"
    assert access.has(_use("zoho-crm"))
    assert not (await _resolve(ns, guest)).has(_use("mcp-x")), "a rule never grants"


# ── ACC-e: a shared session and a delegated run ──────────────────────────────


def _room(ns: Any, actor: str, subjects: list[str], org: str | None = None) -> str:
    org = org or ns.a
    room = str(uuid.uuid4())
    with ns.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, organization_id) "
            "VALUES (:s, :u, 'orchestrator', CAST(:o AS uuid))"),
            {"s": room, "u": actor, "o": org})
        for subject in [actor, *subjects]:
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :u, 'member', CAST(:o AS uuid))"),
                {"s": room, "u": subject, "o": org})
    return room


async def _fold(ns: Any, session: str, actor: str, org: str | None = None) -> Any:
    import acb_auth.access as access_mod
    from acb_common.db import bind_tenant, release_tenant

    async with tenant_engine_scope(ns.dsn):
        token = bind_tenant(org or ns.a)
        try:
            access_mod.invalidate()
            return await access_mod.resolve_session_access(session, actor)
        finally:
            release_tenant(token)


@pytest.mark.parametrize("subject", ["email", "group"])
async def test_acc_e_a_shared_session_with_a_refused_member_denies(ns, subject) -> None:
    listed, member, team = _email("listed"), _email("member"), _email("team")
    _rules(ns, {"mcp-x": {"subjects": [listed, f"group:{GROUP}", _email("admin")]}})
    # The refused member is in the room by address, or by a team that no rule
    # names.
    others = {"email": [member], "group": [f"group:{ROOM_GROUP}"]}[subject]
    room = _room(ns, listed, others)
    clean = _room(ns, listed, [team])

    folded, members = await _fold(ns, clean, listed)
    assert folded.has(_use("mcp-x")) and team in members, "the positive control"
    folded, members = await _fold(ns, room, listed)
    assert member in members, members
    assert not folded.has(_use("mcp-x")), f"the fold kept mcp-x with a {subject} subject"
    assert folded.has(_use("zoho-crm"))
    # A fold of an admin and a refused member refuses too.
    if subject == "email":
        admin_room = _room(ns, _email("admin"), [member])
        folded, _ = await _fold(ns, admin_room, _email("admin"))
        assert not folded.has(_use("mcp-x"))
        authz = await _authorizer(ns, _email("admin"), admin_room)
        assert authz("mcp-x") is False


async def test_acc_e_an_org_subject_with_a_refused_member_denies(ns) -> None:
    """Org B holds two members and no guest, so the ``org`` fold keeps the
    integrations of the member role, and only the rule can take one away."""
    room = _room(ns, LISTED_B, ["org"], org=ns.b)
    folded, members = await _fold(ns, room, LISTED_B, ns.b)
    assert set(members) == {LISTED_B, MEMBER_B}, members
    assert folded.has(_use("mcp-x")), "the positive control"
    _rules(ns, {"mcp-x": {"subjects": [LISTED_B]}}, org=ns.b)
    folded, _ = await _fold(ns, room, LISTED_B, ns.b)
    assert not folded.has(_use("mcp-x")) and folded.has(_use("zoho-crm"))


def test_acc_e_a_delegated_run_denies(world) -> None:
    import acb_auth.access as access_mod

    ns = world.ns
    _run(world, _email("member"), delegated=True)
    assert world.state.ran == [["apollo", "zoho-crm"]], "the positive control"
    world.state.ran.clear()
    _rules(ns, {"zoho-crm": {"subjects": [_email("listed")]}})
    access_mod.invalidate()
    _run(world, _email("member"), delegated=True)
    assert world.state.ran == [["apollo"]], world.state.ran


# ── ACC-f: the write ─────────────────────────────────────────────────────────


async def _admin_call(ns: Any, method: str, path: str, body: Any = None,
                      who: str | None = None) -> httpx.Response:
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes.admin import router

    email = who or _email("admin")
    user = _user(email, await _resolve(ns, email), ns.a)

    async def _caller() -> Any:
        bind_tenant(ns.a)
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    async with tenant_engine_scope(ns.dsn):
        scope = clear_tenant()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://gateway.test") as client:
                return await client.request(method, path, json=body)
        finally:
            release_tenant(scope)


@pytest.fixture
def flag_on(ns, monkeypatch):
    from acb_common import get_settings
    from gateway.routes.admin import integration_access as routes

    monkeypatch.setattr(get_settings(), "integration_access_orgs", ns.a, raising=False)
    audit: list[tuple[Any, ...]] = []
    monkeypatch.setattr(routes, "record_admin_change",
                        lambda *a, **k: audit.append((*a, k)))
    return audit


async def test_acc_f_the_write_persists_and_org_b_does_not_change(ns, flag_on) -> None:
    org_b = {"theme": {"accent": "y"},
             "integration_access": {"zoho-crm": {"subjects": [MEMBER_B]}}}
    _settings(ns, ns.b, org_b)
    assert _stored(ns, ns.a) == {}, "the first write meets empty settings"
    listed = _email("listed")

    r = await _admin_call(ns, "PUT", "/admin/integration-access/zoho-crm",
                          {"mode": "chosen", "people": [listed.upper()], "groups": [GROUP]})
    assert r.status_code == 200, r.text
    assert (r.json()["mode"], r.json()["people"], r.json()["groups"]) == (
        "chosen", [listed], [GROUP])
    first = _stored(ns, ns.a)["integration_access"]
    assert first["zoho-crm"]["subjects"] == sorted([listed, f"group:{GROUP}"])

    r = await _admin_call(ns, "PUT", "/admin/integration-access/mcp-x", {"mode": "admins"})
    assert r.status_code == 200, r.text
    both = _stored(ns, ns.a)["integration_access"]
    assert set(both) == {"zoho-crm", "mcp-x"}, "a second key keeps the first"
    assert both["mcp-x"]["subjects"] == []
    assert not (await _resolve(ns, _email("member"))).has(_use("mcp-x"))
    assert (await _resolve(ns, _email("admin"))).has(_use("mcp-x"))

    r = await _admin_call(ns, "PUT", "/admin/integration-access/zoho-crm", {"mode": "everyone"})
    assert r.status_code == 200 and r.json()["mode"] == "everyone", r.text
    assert set(_stored(ns, ns.a)["integration_access"]) == {"mcp-x"}, "a removal deletes the key"
    assert _stored(ns, ns.b) == org_b, "org B did not change"
    assert [(a[1], a[2]) for a in flag_on] == [
        ("org.integration_access_set", "integration:zoho-crm"),
        ("org.integration_access_set", "integration:mcp-x"),
        ("org.integration_access_set", "integration:zoho-crm"),
    ]


async def test_acc_f_the_list_names_each_service_of_the_org(ns, flag_on) -> None:
    _rules(ns, {"mcp-x": {"subjects": [_email("listed")], "set_by": "a@acc1.test",
                          "set_at": "2026-11-26T00:00:00+00:00"}})
    r = await _admin_call(ns, "GET", "/admin/integration-access")
    assert r.status_code == 200, r.text
    rows = {row["service"]: row for row in r.json()["services"]}
    assert rows["mcp-x"]["kind"] == "mcp" and rows["mcp-x"]["mode"] == "chosen"
    assert rows["mcp-x"]["people"] == [_email("listed")]
    assert rows["api-zoho_books"]["kind"] == "api" and rows["api-zoho_books"]["mode"] == "everyone"
    assert rows["zoho-crm"]["kind"] == "agent"
    # An MCP name that org A does not hold is not in the closed list.
    unknown = await _admin_call(ns, "PUT", "/admin/integration-access/mcp-nope",
                                {"mode": "admins"})
    assert unknown.status_code == 422


async def test_acc_f_the_route_refuses_a_group_or_person_of_org_b(ns, flag_on) -> None:
    for body in ({"mode": "chosen", "people": [MEMBER_B]},
                 {"mode": "chosen", "people": ["org"]},
                 {"mode": "chosen", "groups": ["no-such-team"]}):
        r = await _admin_call(ns, "PUT", "/admin/integration-access/zoho-crm", body)
        assert r.status_code == 422, (body, r.text)
    assert _stored(ns, ns.a) == {}


async def test_acc_f_a_group_that_a_rule_names_is_guarded(ns) -> None:
    from acb_auth import UserContext, UserRole, build_access
    from acb_common.db import bind_tenant, release_tenant
    from fastapi import HTTPException
    from gateway.routes.admin import groups

    # An empty team, so the delete reaches the rule check and not the
    # "members are still in it" refusal, which is a 409 too.
    empty = f"empty{TAG}"
    with ns.p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name, created_by) "
            "VALUES (CAST(:o AS uuid), :s, :s, 'seed')"), {"o": ns.a, "s": empty})
    _rules(ns, {"mcp-x": {"subjects": [f"group:{GROUP}", f"group:{empty}"]}})
    roster = UserContext(
        email=_email("admin"), role=UserRole.EXECUTIVE, organization_id=ns.a,
        access=build_access(["admin:members:read", "admin:members:manage"]),
    )
    async with tenant_engine_scope(ns.dsn):
        token = bind_tenant(ns.a)
        try:
            with pytest.raises(HTTPException) as add:
                await groups.add_group_member(
                    GROUP, groups.GroupMemberAdd(email=_email("member"),
                                                 grant_center_access=False), admin=roster)
            with pytest.raises(HTTPException) as gone:
                await groups.delete_group(empty, admin=roster)
        finally:
            release_tenant(token)
    assert add.value.status_code == 403 and "admin:access:manage" in add.value.detail
    assert gone.value.status_code == 409 and "rule" in gone.value.detail, gone.value.detail
    assert not (await _resolve(ns, _email("member"))).has(_use("mcp-x"))
    with ns.p.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM org_group WHERE slug = :s"),
                         {"s": empty}).scalar() == 1


# ── ACC-g: a malformed rule and a failed read ────────────────────────────────


async def test_acc_g_a_malformed_rule_is_admins_only_for_its_service(ns) -> None:
    import acb_auth.access as access_mod

    _rules(ns, {"mcp-x": "junk", "zoho-crm": {"subjects": ["org"]}})
    access = await _resolve(ns, _email("member"))
    assert access.is_active and access.has("feature:chat")
    assert not access.has(_use("mcp-x")) and not access.has(_use("zoho-crm"))
    assert access.has(_use("apollo"))
    assert (await _resolve(ns, _email("admin"))).has(_use("mcp-x"))
    # A value that the code cannot read at all refuses every integration.
    _settings(ns, ns.a, {"integration_access": [1, 2]})
    access = await _resolve(ns, _email("allow"))
    assert access.is_active and access.has("feature:chat")
    assert not access.has(_use("apollo")) and not access.has(_use("mcp-x"))
    assert access_mod._tables_missing is False


@pytest.mark.parametrize("mode", ["log", "raise"])
async def test_acc_g_a_failed_read_refuses_integrations_only(ns, monkeypatch, mode) -> None:
    """The read fails with a "does not exist" text, the text that latches
    ``_tables_missing`` in the access read. In ``raise`` mode an aborted
    transaction would raise at the end of the block: the savepoint keeps it
    whole."""
    import acb_auth.access as access_mod
    from acb_common import get_settings

    real_sql = access_mod._INTEGRATION_RULES_SQL
    monkeypatch.setattr(get_settings(), "tenant_session_abort_mode", mode, raising=False)
    monkeypatch.setattr(access_mod, "_INTEGRATION_RULES_SQL",
                        "SELECT * FROM acc1_no_such_table WHERE CAST(:org AS uuid) "
                        "IS NOT NULL AND CAST(:uid AS uuid) IS NOT NULL")
    allow = _email("allow")
    access = await _resolve(ns, allow, cache=True)
    assert access.is_active and access.has("feature:chat")
    assert not access.has(_use("mcp-x")), "an exact allow does not pass a failed read"
    assert not access.has(_use("zoho-crm"))
    assert (await _resolve(ns, _email("admin"))).has(_use("zoho-crm"))
    assert access_mod._tables_missing is False
    in_org = await _resolve_in_org(ns, allow)
    assert in_org.is_active and not in_org.has(_use("zoho-crm"))
    # The narrowed answer was not cached: the next read decides again.
    monkeypatch.setattr(access_mod, "_INTEGRATION_RULES_SQL", real_sql)
    assert (await _resolve(ns, allow, cache=True)).has(_use("zoho-crm"))
