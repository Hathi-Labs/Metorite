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

In each case, ``allowed`` equals ``resolve_access(email, use_cache=False)``,
and for ``mcp-x`` it also equals the decision of the chat's
``_integration_authorizer``. No body names another member, a team slug or
``set_by``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.admin", reason="gateway not installed")
pytest.importorskip("orchestrator", reason="the chat authorizer is the parity check")

import httpx
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
    """One more person of org A, with a deny override of ``mcp-x``, and one
    MCP server of org B only. The ACC-1 fixture removes both at its end."""
    with people.p.admin_engine.begin() as c:
        _seed_person(c, people.a, _email("denyx"), "member",
                     (("integrations:use:mcp-x", "deny"),))
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status) VALUES (CAST(:o AS uuid), :p, 'integrations', 'org', "
            "'connected')"), {"o": people.b, "p": f"mcp:{B_SERVER}"})
    return people


@pytest.fixture
def me(ns, extra, monkeypatch):  # noqa: F811 - a fixture
    """The flag on for both orgs, so only the session decides the org."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "integration_access_orgs",
                        f"{ns.a},{ns.b}", raising=False)
    return ns


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


async def _parity(w: Any, email: str, rows: dict[str, dict[str, Any]],
                  org: str | None = None) -> None:
    """``allowed`` is the fresh resolver, and the chat authorizer for ``mcp-x``."""
    fresh = await _resolve(w, email, org or w.a)
    for service, row in rows.items():
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
    assert listed["api-zoho_books"]["reason"] == "role", "no rule there"
    member = await _case(me, "member")
    assert (member["mcp-x"]["allowed"], member["mcp-x"]["reason"]) == (False, "rule")
    assert member["api-zoho_books"]["allowed"] is True, "a rule narrows its own service only"


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
    assert (rows["api-zoho_books"]["allowed"], rows["api-zoho_books"]["reason"]) == (
        True, "role")


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
    assert (rows["api-zoho_books"]["allowed"], rows["api-zoho_books"]["reason"]) == (
        True, "role")
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
