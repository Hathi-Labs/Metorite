"""ACC-3, hermetic: ``GET /auth/me/integrations``.

Spec: ``project-docs/specs/integrations_console.md`` §3.9.4 and §7.5 (row
ACC-3). Metorite task 3989. The real-database half is
``test_auth_me_integrations_r8.py``.

What it locks:

1. An org off ``INTEGRATION_ACCESS_ORGS`` gets 404, and no session opens.
2. A caller with no address, with access that is not active, or with no
   member row gets 403. A failed list or a failed rules read gets 503.
3. Each row has exactly five keys, and the list is sorted by service.
4. Each reason maps to its case, and ``allowed`` is ``user.access``.
5. No address, team slug, ``set_by`` or mode of a stored rule reaches the body.
6. The list is ``integration_access.closed_list``, and a stored rule of a
   service off the list does not show.
7. The route and the admin preview call the one shared step,
   ``_common.member_rule_access``.
8. A guest gets no service names.

**Mutations (R7).**

| # | Mutation | Test that fails |
|---|---|---|
| M1 | the flag check moves after the session | ``test_an_org_off_the_flag_gets_404_and_no_session`` |
| M2 | ``allowed`` comes from the fresh read, not ``user.access`` | ``test_a_cache_window_keeps_allowed_and_says_updating`` |
| M3 | the row copies the rule's subjects | ``test_no_address_team_or_set_by_reaches_the_body`` |
| M4 | the route builds its own list | ``test_the_route_lists_closed_list_only`` |
| M5 | the route narrows on its own | ``test_the_route_and_the_preview_call_the_shared_step`` |
"""

from __future__ import annotations

import inspect
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.admin", reason="gateway not installed")

import httpx
from acb_auth import UserContext, UserRole, build_access
from acb_auth.access import IntegrationRules
from gateway.routes.admin import _common, my_integrations
from gateway.routes.admin import integration_access as rules_routes
from gateway.routes.admin import members as members_routes

ORG = "00000000-0000-0000-0000-00000000000a"
ME = "me@x.test"
PATH = "/auth/me/integrations"
MEMBER_PERMS = ["integrations:use:*", "feature:chat"]
ADMIN_PERMS = [*MEMBER_PERMS, "integrations:manage"]
KEYS = {"service", "kind", "name", "allowed", "reason"}


def _use(service: str) -> str:
    return f"integrations:use:{service}"


def _user(access: Any, *, email: str | None = ME, org: str | None = ORG) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=access,
                       organization_id=org)


@pytest.fixture
def world(monkeypatch):
    """The route with each read answered from Python, the flag on for ORG."""
    from acb_common import get_settings

    state = SimpleNamespace(
        opened=0, status="active", member=True,
        services={"mcp-x": "mcp", "api-zoho_books": "api", "mcp-y": "mcp"},
        role_perms={"member": list(MEMBER_PERMS)}, overrides=[],
        rules=IntegrationRules(), list_fails=False,
    )

    @asynccontextmanager
    async def _session(organization_id: Any = None) -> Any:
        state.opened += 1
        yield object()

    async def _org(_db: Any, _user: Any) -> str:
        return ORG

    async def _find(_db: Any, org: str, email: str) -> dict[str, Any] | None:
        assert org == ORG and email == ME
        if not state.member:
            return None
        return {"id": "u-1", "email": ME, "status": state.status}

    async def _closed(_db: Any, org: str) -> dict[str, str]:
        assert org == ORG
        if state.list_fails:
            raise RuntimeError("list read failed")
        return dict(state.services)

    async def _perms(_db: Any, uid: str) -> dict[str, list[str]]:
        assert uid == "u-1"
        return state.role_perms

    async def _overrides(_db: Any, uid: str) -> list[dict[str, Any]]:
        assert uid == "u-1"
        return state.overrides

    async def _rules(_db: Any, org: str, uid: str) -> IntegrationRules:
        assert (org, uid) == (ORG, "u-1")
        return state.rules

    monkeypatch.setattr(get_settings(), "integration_access_orgs", ORG, raising=False)
    for name, value in (("_tenant_session", _session), ("get_org_id", _org),
                        ("find_member", _find), ("_role_permission_map", _perms),
                        ("_load_overrides", _overrides),
                        ("read_integration_rules", _rules)):
        monkeypatch.setattr(my_integrations, name, value)
    monkeypatch.setattr(rules_routes, "closed_list", _closed)
    return state


async def _call(user: UserContext) -> httpx.Response:
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.admin import me_router

    app = FastAPI()
    app.include_router(me_router)
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway.test") as client:
        return await client.get(PATH)


def _rows(r: httpx.Response) -> dict[str, dict[str, Any]]:
    assert r.status_code == 200, r.text
    return {row["service"]: row for row in r.json()["services"]}


# ── 1 and 2. The order ───────────────────────────────────────────────────────


async def test_an_org_off_the_flag_gets_404_and_no_session(world, monkeypatch) -> None:
    from acb_common import get_settings

    for flag in ("", "00000000-0000-0000-0000-0000000000bb"):
        monkeypatch.setattr(get_settings(), "integration_access_orgs", flag, raising=False)
        for user in (_user(build_access(ADMIN_PERMS)), _user(build_access([]), email=None),
                     _user(build_access(MEMBER_PERMS, is_active=False))):
            r = await _call(user)
            assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), flag
    assert world.opened == 0, "a dark route opened a session"


@pytest.mark.parametrize("user", [
    _user(build_access(MEMBER_PERMS), email=None),
    _user(build_access(MEMBER_PERMS), email="system:internal"),
    _user(build_access(MEMBER_PERMS, is_active=False)),
], ids=["no-address", "no-at", "inactive"])
async def test_an_unsigned_or_inactive_caller_gets_403_and_no_session(world, user) -> None:
    r = await _call(user)
    assert r.status_code == 403, r.text
    assert world.opened == 0


async def test_a_caller_with_no_member_row_gets_403(world) -> None:
    world.member = False
    r = await _call(_user(build_access(MEMBER_PERMS)))
    assert r.status_code == 403 and "services" not in r.text


@pytest.mark.parametrize("fault", ["list", "rules"])
async def test_a_failed_read_gets_503_never_a_list(world, fault) -> None:
    if fault == "list":
        world.list_fails = True
    else:
        world.rules = IntegrationRules(failed=True)
    r = await _call(_user(build_access(MEMBER_PERMS)))
    assert r.status_code == 503, r.text
    assert "services" not in r.json()


def test_the_route_takes_nothing_from_the_request() -> None:
    from gateway.routes.admin import me_router

    route = next(r for r in me_router.routes if getattr(r, "path", "") == PATH)
    dep = route.dependant
    assert not (dep.path_params or dep.query_params or dep.header_params
                or dep.cookie_params or dep.body_params), "the route reads request input"
    assert route.methods == {"GET"}


# ── 3. The shape ─────────────────────────────────────────────────────────────


async def test_each_row_has_exactly_five_keys_sorted_by_service(world) -> None:
    r = await _call(_user(build_access(MEMBER_PERMS)))
    body = r.json()
    assert set(body) == {"services", "source"} and body["source"] == "integration-rule"
    assert [row["service"] for row in body["services"]] == ["api-zoho_books", "mcp-x", "mcp-y"]
    for row in body["services"]:
        assert set(row) == KEYS, row
    rows = _rows(r)
    assert (rows["mcp-x"]["kind"], rows["mcp-x"]["name"]) == ("mcp", "x")
    assert (rows["api-zoho_books"]["kind"], rows["api-zoho_books"]["name"]) == (
        "api", "zoho_books")


# ── 4. The reasons ───────────────────────────────────────────────────────────


def _rule(*subjects: str) -> dict[str, Any]:
    return {"subjects": list(subjects), "set_by": "boss@x.test", "set_at": "2026-11-26"}


async def test_a_listed_member_is_on_rule_and_another_service_is_role(world) -> None:
    world.rules = IntegrationRules({"mcp-x": _rule(ME)})
    rows = _rows(await _call(_user(build_access(MEMBER_PERMS))))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "on-rule")
    assert (rows["mcp-y"]["allowed"], rows["mcp-y"]["reason"]) == (True, "role")


async def test_a_team_member_is_on_rule(world) -> None:
    world.rules = IntegrationRules({"mcp-x": _rule("group:ops")}, groups=("ops",))
    rows = _rows(await _call(_user(build_access(MEMBER_PERMS))))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "on-rule")


async def test_an_unlisted_member_is_refused_by_the_rule(world) -> None:
    world.rules = IntegrationRules({"mcp-x": _rule("other@x.test")})
    access = build_access(MEMBER_PERMS, [(_use("mcp-x"), "deny")])  # the resolver's answer
    rows = _rows(await _call(_user(access)))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "rule")
    assert rows["mcp-y"]["allowed"] is True


async def test_a_holder_of_integrations_manage_is_admin(world) -> None:
    world.role_perms = {"admin": list(ADMIN_PERMS)}
    world.rules = IntegrationRules({"mcp-x": _rule()})
    rows = _rows(await _call(_user(build_access(ADMIN_PERMS))))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "admin")
    assert rows["mcp-y"]["reason"] == "role", "no rule there"


async def test_an_allow_override_with_no_rule_is_allowed_for_you(world) -> None:
    world.role_perms = {"guest": ["feature:chat"]}
    world.overrides = [{"permission": _use("mcp-x"), "effect": "allow"}]
    access = build_access(["feature:chat"], [(_use("mcp-x"), "allow")])
    rows = _rows(await _call(_user(access)))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "allowed-for-you")
    assert (rows["mcp-y"]["allowed"], rows["mcp-y"]["reason"]) == (False, "no-role")


async def test_an_exact_allow_under_a_rule_is_refused_by_the_rule(world) -> None:
    world.overrides = [{"permission": _use("mcp-x"), "effect": "allow"}]
    world.rules = IntegrationRules({"mcp-x": _rule("other@x.test")})
    from acb_auth.integration_access import narrow

    base = build_access(MEMBER_PERMS, [(_use("mcp-x"), "allow")])
    access = narrow(base, world.rules.raw, email=ME).access
    rows = _rows(await _call(_user(access)))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "rule")


async def test_a_deny_override_with_no_rule_is_denied_for_you(world) -> None:
    world.overrides = [{"permission": _use("mcp-x"), "effect": "deny"}]
    rows = _rows(await _call(_user(build_access(MEMBER_PERMS, [(_use("mcp-x"), "deny")]))))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "denied-for-you")


async def test_an_unreadable_value_is_rules_unreadable_and_the_admin_keeps_yes(world) -> None:
    from acb_auth.integration_access import narrow

    world.rules = IntegrationRules([1, 2])
    refused = narrow(build_access(MEMBER_PERMS), [1, 2], email=ME).access
    rows = _rows(await _call(_user(refused)))
    assert {(r["allowed"], r["reason"]) for r in rows.values()} == {(False, "rules-unreadable")}

    world.role_perms = {"admin": list(ADMIN_PERMS)}
    rows = _rows(await _call(_user(build_access(ADMIN_PERMS))))
    assert {(r["allowed"], r["reason"]) for r in rows.values()} == {(True, "admin")}


async def test_an_unreadable_single_rule_refuses_only_its_service(world) -> None:
    world.rules = IntegrationRules({"mcp-x": "junk"})
    access = build_access(MEMBER_PERMS, [(_use("mcp-x"), "deny")])
    rows = _rows(await _call(_user(access)))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "rule")
    assert (rows["mcp-y"]["allowed"], rows["mcp-y"]["reason"]) == (True, "role")


async def test_a_member_row_that_is_not_active_is_inactive(world) -> None:
    """The session still reads active (the cache), the row does not."""
    world.status = "suspended"
    rows = _rows(await _call(_user(build_access(["feature:chat"]))))
    assert {(r["allowed"], r["reason"]) for r in rows.values()} == {(False, "inactive")}


async def test_a_cache_window_keeps_allowed_and_says_updating(world) -> None:
    """``user.access`` is the cached Yes. A new rule refuses on a fresh read."""
    world.rules = IntegrationRules({"mcp-x": _rule("other@x.test")})
    rows = _rows(await _call(_user(build_access(MEMBER_PERMS))))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (True, "updating")
    # And the other way: the cached No, while a fresh read passes.
    world.rules = IntegrationRules({"mcp-x": _rule(ME)})
    access = build_access(MEMBER_PERMS, [(_use("mcp-x"), "deny")])
    world.overrides = []
    rows = _rows(await _call(_user(access)))
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["reason"]) == (False, "updating")


async def test_allowed_is_user_access_for_each_row(world) -> None:
    """Never a second Yes or No: each ``allowed`` equals ``user.access.has``."""
    world.rules = IntegrationRules({"mcp-x": _rule("other@x.test"), "mcp-y": _rule(ME)})
    access = build_access(MEMBER_PERMS, [(_use("mcp-x"), "deny"), (_use("api-zoho_books"), "deny")])
    rows = _rows(await _call(_user(access)))
    for service, row in rows.items():
        assert row["allowed"] is access.has(_use(service)), service
        assert row["reason"] in my_integrations.REASONS


# ── 5. The sentinel ──────────────────────────────────────────────────────────


async def test_no_address_team_or_set_by_reaches_the_body(world) -> None:
    secret = {"subjects": ["foreign.person@other.test", "group:secretteam", ME],
              "set_by": "setter.person@x.test", "set_at": "2026-11-26T01:02:03+00:00"}
    world.rules = IntegrationRules(json.dumps({"mcp-x": secret, "mcp-y": {
        "subjects": ["group:secretteam"], "set_by": "setter.person@x.test"}}),
        groups=("myownteam",))
    r = await _call(_user(build_access(MEMBER_PERMS, [(_use("mcp-y"), "deny")])))
    text = r.text
    for word in ("foreign.person", "secretteam", "setter.person", "myownteam", "@",
                 "2026-11-26", "subjects", "set_by", "set_at", "mode", "groups",
                 "people", "chosen", "admins", "everyone"):
        assert word not in text, f"{word!r} reached the body: {text}"


# ── 6. The closed list ───────────────────────────────────────────────────────


async def test_the_route_lists_closed_list_only(world) -> None:
    world.services = {"mcp-only": "mcp"}
    world.rules = IntegrationRules({"mcp-gone": _rule(), "serpapi": _rule()})
    rows = _rows(await _call(_user(build_access(MEMBER_PERMS))))
    assert set(rows) == {"mcp-only"}, "a stored rule off the list showed"


def test_the_route_reads_the_list_of_the_rule_routes() -> None:
    source = inspect.getsource(my_integrations.get_my_integrations)
    assert "_rules_routes.closed_list(" in source
    assert my_integrations._rules_routes is rules_routes


# ── 7. One shared step ───────────────────────────────────────────────────────


async def test_the_route_and_the_preview_call_the_shared_step(world, monkeypatch) -> None:
    assert my_integrations.member_rule_access is _common.member_rule_access
    assert members_routes.member_rule_access is _common.member_rule_access
    calls: list[str] = []
    real = _common.member_rule_access

    def _spy(who: str):
        def wrapped(*a: Any, **k: Any) -> Any:
            calls.append(who)
            return real(*a, **k)
        return wrapped

    monkeypatch.setattr(my_integrations, "member_rule_access", _spy("self"))
    monkeypatch.setattr(members_routes, "member_rule_access", _spy("preview"))
    assert (await _call(_user(build_access(MEMBER_PERMS)))).status_code == 200

    @asynccontextmanager
    async def _session(organization_id: Any = None) -> Any:
        yield object()

    async def _member(*_a: Any) -> dict[str, Any]:
        return {"id": "u-1", "email": ME, "display_name": "M", "status": "active"}

    async def _async(value: Any):
        async def f(*_a: Any) -> Any:
            return value
        return f

    for name, value in (("_tenant_session", _session), ("get_org_id", await _async(ORG)),
                        ("get_member", _member), ("roles_for_user", await _async(["member"])),
                        ("_role_permission_map", await _async({"member": MEMBER_PERMS})),
                        ("_load_overrides", await _async([])),
                        ("read_integration_rules", await _async(IntegrationRules())),
                        ("_integration_names", lambda: []), ("_agent_names", lambda: [])):
        monkeypatch.setattr(members_routes, name, value)
    from tests.unit.test_admin_groups import FULL_ADMIN

    await members_routes.get_member_access(ME, admin=FULL_ADMIN)
    assert calls == ["self", "preview"]
    for fn in (my_integrations.get_my_integrations, members_routes.get_member_access):
        src = inspect.getsource(fn)
        assert "member_rule_access(" in src, fn.__name__
        assert "narrow_by_rules(" not in src and "build_access(" not in src, fn.__name__


# ── 8. A guest ───────────────────────────────────────────────────────────────


async def test_a_guest_gets_no_service_names(world) -> None:
    world.role_perms = {"guest": ["feature:chat"]}
    world.rules = IntegrationRules({"mcp-x": _rule(ME)})
    r = await _call(_user(build_access(["feature:chat"])))
    assert r.status_code == 200
    assert r.json() == {"services": [], "source": "integration-rule", "blocked": "no-role"}
    assert "mcp" not in r.text and "zoho" not in r.text
