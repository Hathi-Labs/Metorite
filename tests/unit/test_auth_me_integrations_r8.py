"""ACC-3 on a real database (R8): ``GET /auth/me/integrations``.

Spec: ``project-docs/specs/integrations_console.md`` §3.9.4, §7.5 (row
ACC-3) and §8. Metorite task 3989. The hermetic half is
``test_auth_me_integrations.py``.

The route runs on a phase-4 promoted scratch database, as the non-privileged
app role, through asyncpg, with the org bound and ``IDENTITY_CUTOVER`` on.
The people, the two orgs and the helpers are those of
``test_integration_access_r8.py`` (ACC-1). The rules sit in
``organization.settings`` as the ACC-1 route writes them.

Run (a PRIVATE database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_auth_me_integrations_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

In each case, ``allowed`` of an MCP row equals
``resolve_access(email, use_cache=False)``, and for ``mcp-x`` it also equals
the decision of the chat's ``_integration_authorizer``. ``allowed`` of an API
row equals the answer of the call route's own ``_resolve`` for the same
access: a 404 is No. An API row shows only for a connected row of the org,
with ``API_CONNECTIONS_ORGS`` on. No body names another member, a team slug
or ``set_by``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.admin", reason="gateway not installed")
pytest.importorskip("orchestrator", reason="the chat authorizer is the parity check")

import httpx
from fastapi import HTTPException
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_integration_access_r8 import (  # noqa: F401 - fixtures
    GROUP,
    LISTED_B,
    MEMBER_B,
    ROOM_GROUP,
    TAG,
    _add_to_group,
    _authorizer,
    _email,
    _resolve,
    _rules,
    _seed_person,
    _use,
    ns,
    people,
)

pytestmark = _DB_GATE

PATH = "/auth/me/integrations"
KEYS = {"service", "kind", "name", "allowed", "reason"}
SET_BY = f"setter.{TAG}@acc1.test"
B_SERVER = f"bonly{TAG}"


@pytest.fixture(scope="module")
def extra(people):  # noqa: F811 - a fixture
    """One more person of org A, with a deny override of ``mcp-x``, a second
    MCP server ``y`` of org A, and one MCP server of org B only. The ACC-1
    fixture removes them at its end."""
    with people.p.admin_engine.begin() as c:
        _seed_person(c, people.a, _email("denyx"), "member",
                     (("integrations:use:mcp-x", "deny"),))
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status) VALUES (CAST(:o AS uuid), 'mcp:y', 'integrations', 'org', "
            "'connected')"), {"o": people.a})
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status) VALUES (CAST(:o AS uuid), :p, 'integrations', 'org', "
            "'connected')"), {"o": people.b, "p": f"mcp:{B_SERVER}"})
    return people


@pytest.fixture
def me(ns, extra, monkeypatch):  # noqa: F811 - a fixture
    """Both flags on for both orgs, so only the session decides the org."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "integration_access_orgs",
                        f"{ns.a},{ns.b}", raising=False)
    monkeypatch.setattr(get_settings(), "api_connections_orgs",
                        f"{ns.a},{ns.b}", raising=False)
    return ns


@pytest.fixture
def books(me, monkeypatch):
    """The Zoho Books connection of org A, saved as API-2 saves it: org scope,
    ``connected``, ``grant = admin`` and the ops hash of the template."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from acb_common.db import tenant_session
    from gateway.connections import store

    from tests.unit import _api_call_fakes as f

    monkeypatch.setattr(get_settings(), "acb_master_key", "acc3-r8-master-key-for-the-blob",
                        raising=False)
    monkeypatch.setattr(key_store, "_store", None)

    async def _save() -> None:
        cred = f.credential()
        async with tenant_engine_scope(me.dsn), tenant_session(me.a) as db:
            await store.save_credential(db, me.a, "api:zoho_books", "integrations", cred,
                                        connected_by=_email("admin"), scopes=cred.scopes)

    yield _save
    with me.p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM integration_connections WHERE organization_id = "
                       "CAST(:o AS uuid) AND provider = 'api:zoho_books'"), {"o": me.a})


async def _me(w: Any, email: str, org: str | None = None, *,
              cache: bool = False) -> dict[str, Any]:
    """The route as a request runs it. ``user.access`` is the resolver's answer."""
    from acb_auth import UserContext, UserRole, get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes.admin import me_router

    org = org or w.a
    access = await _resolve(w, email, org, cache=cache)
    user = UserContext(email=email, role=UserRole.EMPLOYEE, access=access,
                       organization_id=org)

    async def _caller() -> Any:
        bind_tenant(org)
        return user

    app = FastAPI()
    app.include_router(me_router)
    app.dependency_overrides[get_current_user] = _caller
    async with tenant_engine_scope(w.dsn):
        scope = clear_tenant()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://gateway.test") as client:
                r = await client.get(PATH)
        finally:
            release_tenant(scope)
    assert r.status_code == 200, r.text
    _assert_clean(r.text, email)
    return r.json()


def _assert_clean(body: str, email: str) -> None:
    """No address (the caller's own included), no team slug, no ``set_by``."""
    for word in ("@", GROUP, ROOM_GROUP, SET_BY, "set_by", "subjects", "people", "groups",
                 email):
        assert word not in body, f"{word!r} in the body: {body}"


def _rows(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {row["service"]: row for row in body["services"]}
    assert [r["service"] for r in body["services"]] == sorted(rows)
    for row in rows.values():
        assert set(row) == KEYS, row
    return rows


async def _route_says_yes(w: Any, email: str, access: Any, name: str, org: str) -> bool:
    """The call route's own grant and row: ``_resolve`` of ``api_connections``.

    A 404 is No. A pass, or the 409 of a changed template, is Yes."""
    from acb_auth import UserContext, UserRole
    from gateway.routes import api_connections

    user = UserContext(email=email, role=UserRole.EMPLOYEE, access=access,
                       organization_id=org)
    async with tenant_engine_scope(w.dsn):
        try:
            await api_connections._resolve(user, name)
        except HTTPException as exc:
            assert exc.status_code in (404, 409), exc
            return exc.status_code == 409
    return True


async def _parity(w: Any, email: str, rows: dict[str, dict[str, Any]],
                  org: str | None = None) -> None:
    """An MCP row is the fresh resolver, and the chat authorizer for ``mcp-x``.
    An API row is the call route's own answer for the same access."""
    fresh = await _resolve(w, email, org or w.a)
    for service, row in rows.items():
        if row["kind"] == "api":
            said = await _route_says_yes(w, email, fresh, row["name"], org or w.a)
            assert row["allowed"] is said, (email, service)
            continue
        assert row["allowed"] is fresh.has(_use(service)), (email, service)
    if "mcp-x" in rows and (org or w.a) == w.a:
        authz = await _authorizer(w, email)
        assert authz is not None and authz("mcp-x") is rows["mcp-x"]["allowed"], email


def _rule(*subjects: str) -> dict[str, Any]:
    return {"subjects": list(subjects), "set_by": SET_BY, "set_at": "2026-11-27T00:00:00+00:00"}


async def _case(w: Any, key: str) -> dict[str, dict[str, Any]]:
    email = _email(key)
    rows = _rows(await _me(w, email))
    await _parity(w, email, rows)
    return rows


# ── The reasons ──────────────────────────────────────────────────────────────


async def test_a_listed_person_is_yes_on_rule_and_an_unlisted_one_is_no_rule(me) -> None:
    _rules(me, {"mcp-x": _rule(_email("listed"))})
    listed = await _case(me, "listed")
    assert (listed["mcp-x"]["allowed"], listed["mcp-x"]["reason"]) == (True, "on-rule")
    assert (listed["mcp-x"]["kind"], listed["mcp-x"]["name"]) == ("mcp", "x")
    assert listed["mcp-y"]["reason"] == "role", "no rule there"
    member = await _case(me, "member")
    assert (member["mcp-x"]["allowed"], member["mcp-x"]["reason"]) == (False, "rule")
    assert member["mcp-y"]["allowed"] is True, "a rule narrows its own service only"


async def test_a_holder_of_integrations_manage_is_yes_admin(me) -> None:
    _rules(me, {"mcp-x": _rule()})
    for key in ("admin", "owner", "manageover"):
        rows = await _case(me, key)
        assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "admin"), key


async def test_an_exact_allow_under_a_rule_is_no_rule(me) -> None:
    rows = await _case(me, "allow")
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (
        True, "allowed-for-you"), "the positive control"
    _rules(me, {"mcp-x": _rule(_email("listed"))})
    rows = await _case(me, "allow")
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "rule")


async def test_a_deny_override_with_no_rule_is_no_denied_for_you(me) -> None:
    rows = await _case(me, "denyx")
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "denied-for-you")
    assert (rows["mcp-y"]["allowed"], rows["mcp-y"]["reason"]) == (True, "role")


async def test_a_guest_gets_no_service_names(me) -> None:
    _rules(me, {"mcp-x": _rule(_email("guestlisted"))})
    for key in ("guest", "guestlisted"):
        body = await _me(me, _email(key))
        assert body == {"services": [], "source": "integration-rule", "blocked": "no-role"}, key


async def test_a_team_member_is_yes_until_the_group_route_removes_them(me) -> None:
    from acb_auth import UserContext, UserRole
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.admin import groups

    team = _email("team")
    _rules(me, {"mcp-x": _rule(f"group:{GROUP}")})
    rows = _rows(await _me(me, team, cache=True))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "on-rule")
    await _parity(me, team, rows)

    admin_email = _email("admin")
    admin = UserContext(email=admin_email, role=UserRole.EXECUTIVE,
                        access=await _resolve(me, admin_email), organization_id=me.a)
    async with tenant_engine_scope(me.dsn):
        token = bind_tenant(me.a)
        try:
            out = await groups.remove_group_member(GROUP, team, admin=admin)
        finally:
            release_tenant(token)
    try:
        assert out["status"] == "removed"
        # The cached answer is gone: the next read refuses, with no restart.
        rows = _rows(await _me(me, team, cache=True))
        assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "rule")
        await _parity(me, team, rows)
    finally:
        with me.p.admin_engine.begin() as c:
            _add_to_group(c, me.a, me.gid, me.ids["team"])


async def test_an_unreadable_rule_refuses_only_its_service(me) -> None:
    _rules(me, {"mcp-x": "junk"})
    rows = await _case(me, "member")
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "rule")
    assert (rows["mcp-y"]["allowed"], rows["mcp-y"]["reason"]) == (True, "role")
    admin = await _case(me, "admin")
    assert (admin["mcp-x"]["allowed"], admin["mcp-x"]["reason"]) == (True, "admin")


async def test_an_unreadable_value_is_rules_unreadable_and_the_admin_keeps_yes(me) -> None:
    from tests.unit.test_integration_access_r8 import _settings

    _settings(me, me.a, {"integration_access": [1, 2]})
    rows = await _case(me, "member")
    assert rows and {(r["allowed"], r["reason"]) for r in rows.values()} == {
        (False, "rules-unreadable")}
    admin = await _case(me, "admin")
    assert {(r["allowed"], r["reason"]) for r in admin.values()} == {(True, "admin")}


# ── Two orgs ─────────────────────────────────────────────────────────────────


async def test_org_b_names_never_appear_for_org_a_and_its_rule_changes_nothing(me) -> None:
    member = _email("member")
    before = await _me(me, member)
    assert B_SERVER not in str(before)
    b_rows = _rows(await _me(me, MEMBER_B, me.b))
    assert f"mcp-{B_SERVER}" in b_rows, "the positive control: org B lists its server"
    assert "mcp-x" not in b_rows, "org A's server is not on org B's list"
    await _parity(me, MEMBER_B, b_rows, me.b)

    _rules(me, {"mcp-x": _rule(LISTED_B), f"mcp-{B_SERVER}": _rule(LISTED_B),
                "api-zoho_books": _rule(LISTED_B)}, org=me.b)
    after = await _me(me, member)
    assert after == before, "a rule of org B changed org A"
    b_rows = _rows(await _me(me, MEMBER_B, me.b))
    assert (b_rows[f"mcp-{B_SERVER}"]["allowed"], b_rows[f"mcp-{B_SERVER}"]["reason"]) == (
        False, "rule"), "the positive control: org B's rule binds in org B"
    await _parity(me, MEMBER_B, b_rows, me.b)


# ── An API row: the call route's grant, for a connection the org holds ───────


def _api(rows: dict[str, dict[str, Any]]) -> tuple[bool, str]:
    row = rows["api-zoho_books"]
    assert (row["kind"], row["name"]) == ("api", "zoho_books")
    return row["allowed"], row["reason"]


async def test_an_unconnected_template_is_absent(me) -> None:
    for key in ("member", "admin", "owner"):
        rows = await _case(me, key)
        assert "api-zoho_books" not in rows, key
        assert {"mcp-x", "mcp-y"} <= set(rows), "the positive control"


async def test_a_member_of_a_connected_api_is_no_admin_only(me, books) -> None:
    """The seeded member role holds ``integrations:use:*``. The row holds
    ``grant = admin``, so the call route answers 404 to the member."""
    await books()
    for key in ("member", "listed", "allow", "admindeny"):
        rows = await _case(me, key)
        assert _api(rows) == (False, "admin-only"), key


async def test_a_holder_of_integrations_manage_is_yes_on_the_api_row(me, books) -> None:
    await books()
    for key in ("admin", "owner", "manageover"):
        rows = await _case(me, key)
        assert _api(rows) == (True, "role"), key


async def test_under_a_rule_the_rule_refuses_first(me, books) -> None:
    await books()
    _rules(me, {"api-zoho_books": _rule(_email("listed"))})
    assert _api(await _case(me, "listed")) == (False, "admin-only"), "the grant mark"
    assert _api(await _case(me, "member")) == (False, "rule"), "the rule comes first"
    for key in ("admin", "manageover"):
        assert _api(await _case(me, key)) == (True, "admin"), key


async def test_api_connections_off_hides_the_api_row(me, books, monkeypatch) -> None:
    from acb_common import get_settings

    await books()
    assert "api-zoho_books" in await _case(me, "admin"), "the positive control"
    monkeypatch.setattr(get_settings(), "api_connections_orgs", me.b, raising=False)
    for key in ("admin", "member"):
        assert "api-zoho_books" not in await _case(me, key), key


async def test_a_disconnected_api_row_is_absent(me, books) -> None:
    await books()
    with me.p.admin_engine.begin() as c:
        c.execute(text("UPDATE integration_connections SET status = 'disconnected' "
                       "WHERE organization_id = CAST(:o AS uuid) "
                       "AND provider = 'api:zoho_books'"), {"o": me.a})
    for key in ("admin", "member"):
        assert "api-zoho_books" not in await _case(me, key), key


async def test_org_a_api_row_never_shows_in_org_b(me, books) -> None:
    await books()
    assert "api-zoho_books" in await _case(me, "admin"), "the positive control"
    b_rows = _rows(await _me(me, MEMBER_B, me.b))
    assert "api-zoho_books" not in b_rows, "org A's connection showed in org B"
    await _parity(me, MEMBER_B, b_rows, me.b)


def test_the_suite_is_on_the_r8_must_run_list() -> None:
    from tests.unit import _r8_lists

    assert "tests/unit/test_auth_me_integrations_r8.py" in _r8_lists.must_run()
