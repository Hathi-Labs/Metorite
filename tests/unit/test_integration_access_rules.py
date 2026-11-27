"""ACC-1, hermetic: who can use an integration.

Spec: ``project-docs/specs/integrations_console.md`` §3.9 and §7.5 (the
hermetic list). Metorite task 3987. The real-database half is
``test_integration_access_r8.py``.

What it locks:

1. The pure rule (``acb_auth.integration_access.narrow``): no rule changes
   nothing, a rule only narrows, a holder of ``integrations:manage`` passes,
   an exact allow loses, a fault narrows and never raises.
2. Both routes answer 404 for an org off ``INTEGRATION_ACCESS_ORGS``, before
   any permission check, and 403 without either permission.
3. An unknown service, group or address, and ``org``, answer 422.
4. Each write records the audit line and clears the access cache.
5. Each service name equals ``mcp_service`` of both modules, and
   ``api-<template>`` as the API route checks it.
6. The access preview shows the rule as the source.
7. The group-write gate of §3.9.2 holds.
8. The write statements, by their text, and the one writer of the key.
9. Fix round 1: a member purge takes the address off each rule first, under
   the row lock of the org. The PUT takes that lock before it checks the
   groups and the people, and the group gates take ``FOR SHARE``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.admin", reason="gateway not installed")

import httpx
from acb_auth import UserContext, UserRole, build_access
from acb_auth.integration_access import (
    MANAGE,
    RULE_SOURCE,
    USE_ALL,
    narrow,
    parse_rules,
    rules_name_group,
    service_name_ok,
    without_member,
)
from fastapi import HTTPException
from gateway.routes.admin import _common
from gateway.routes.admin import groups as groups_routes
from gateway.routes.admin import integration_access as routes

from tests.unit.test_admin_groups import (  # noqa: F401 - `db` is a fixture
    FULL_ADMIN,
    ROSTER_ADMIN,
    _FakeDB,
    db,
)

REPO = Path(__file__).resolve().parents[2]
ORG = "00000000-0000-0000-0000-00000000000a"
MEMBER = build_access(["integrations:use:*", "feature:chat"], roles=["member"])
ADMIN_PERMS = ["admin:members:read", "integrations:manage", "admin:access:manage",
               "integrations:use:*"]


def _use(service: str) -> str:
    return f"integrations:use:{service}"


# ── 1. The pure rule ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, {}, "{}", "null"])
def test_no_rule_returns_the_same_object(raw: Any) -> None:
    out = narrow(MEMBER, raw, email="a@x.test")
    assert out.access is MEMBER and not out.refused and not out.all_refused


def test_a_rule_narrows_its_own_service_only() -> None:
    out = narrow(MEMBER, {"mcp-x": {"subjects": ["b@x.test"]}}, email="a@x.test")
    assert not out.access.has(_use("mcp-x")) and out.access.has(_use("mcp-y"))
    assert out.access.has("feature:chat") and out.refused == {"mcp-x"}
    assert out.access.granted == MEMBER.granted, "a rule never grants"


def test_a_subject_passes_by_address_or_by_group() -> None:
    rules = {"mcp-x": {"subjects": ["a@x.test", "group:ops"]}}
    assert narrow(MEMBER, rules, email="A@X.test").access.has(_use("mcp-x"))
    assert narrow(MEMBER, rules, email="c@x.test", groups=["OPS"]).access.has(_use("mcp-x"))
    assert not narrow(MEMBER, rules, email="c@x.test", groups=["sales"]).access.has(_use("mcp-x"))


def test_a_holder_of_integrations_manage_passes_and_a_deny_of_it_does_not() -> None:
    rules = {"mcp-x": {"subjects": []}}
    admin = build_access(["integrations:use:*", MANAGE])
    assert narrow(admin, rules, email="a@x.test").access is admin
    over = build_access(["integrations:use:*"], [(MANAGE, "allow")])
    assert narrow(over, rules, email="a@x.test").access.has(_use("mcp-x"))
    denied = build_access(["integrations:use:*", MANAGE], [(MANAGE, "deny")])
    assert not narrow(denied, rules, email="a@x.test").access.has(_use("mcp-x"))


def test_an_exact_allow_does_not_pass_a_rule_or_a_failed_read() -> None:
    allow = build_access(["integrations:use:*"], [(_use("mcp-x"), "allow")])
    assert not narrow(allow, {"mcp-x": {"subjects": []}}, email="a@x.test").access.has(
        _use("mcp-x"))
    failed = narrow(allow, None, email="a@x.test", read_failed=True)
    assert failed.all_refused and not failed.access.has(_use("mcp-x"))
    assert not failed.access.has(_use("other")) and USE_ALL in failed.access.denied


def test_a_guest_on_the_list_gets_nothing() -> None:
    guest = build_access(["feature:chat"], roles=["guest"])
    out = narrow(guest, {"mcp-x": {"subjects": ["g@x.test"]}}, email="g@x.test")
    assert not out.access.has(_use("mcp-x"))


@pytest.mark.parametrize("raw", [[1, 2], "not json", 7, {"Bad Key": {"subjects": []}},
                                 {"a:b": {"subjects": []}}])
def test_a_value_that_the_code_cannot_read_refuses_integrations_only(raw: Any) -> None:
    out = narrow(MEMBER, raw, email="a@x.test")
    assert out.all_refused and not out.access.has(_use("anything"))
    assert out.access.has("feature:chat") and out.access.is_active


@pytest.mark.parametrize("rule", ["junk", {"subjects": "a@x.test"}, {"subjects": [1]},
                                  {"subjects": ["org"]}, {"nothing": []}])
def test_a_rule_that_the_code_cannot_read_is_admins_only(rule: Any) -> None:
    out = narrow(MEMBER, {"mcp-x": rule}, email="a@x.test")
    assert not out.access.has(_use("mcp-x")) and out.access.has(_use("mcp-y"))


def test_narrow_never_raises(monkeypatch) -> None:
    from acb_auth import integration_access as ia

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(ia, "parse_rules", boom)
    out = ia.narrow(MEMBER, {"mcp-x": {"subjects": []}}, email="a@x.test")
    assert out.all_refused and not out.access.has(_use("mcp-x"))


def test_an_inactive_member_stays_inactive() -> None:
    gone = build_access(["integrations:use:*"], is_active=False)
    assert narrow(gone, {"mcp-x": {"subjects": []}}, email="a@x.test").access is gone


def test_the_fold_keeps_a_rule_deny() -> None:
    admin = build_access(["integrations:use:*", MANAGE])
    refused = narrow(MEMBER, {"mcp-x": {"subjects": []}}, email="a@x.test").access
    assert not admin.intersect(refused).has(_use("mcp-x"))
    assert not refused.intersect(admin).has(_use("mcp-x"))


def test_the_group_names_and_the_service_names() -> None:
    assert rules_name_group({"a": {"subjects": ["group:ops"]}}, "OPS")
    assert not rules_name_group({"a": {"subjects": ["group:sales"]}}, "ops")
    assert rules_name_group({"a": "junk"}, "ops"), "an unreadable rule counts as named"
    assert rules_name_group([1], "ops")
    assert not rules_name_group(None, "ops")
    for good in ("mcp-tally", "api-zoho_books", "zoho-crm", "google-maps"):
        assert service_name_ok(good), good
    for bad in ("", "*", "a:b", "MCP-X", "a b", None):
        assert not service_name_ok(bad), bad
    assert parse_rules('{"mcp-x": {"subjects": ["A@X.test"]}}') == (
        {"mcp-x": frozenset({"a@x.test"})}, True)


# ── 2 to 4. The routes ───────────────────────────────────────────────────────


def _user(perms: list[str], org: str | None = ORG) -> UserContext:
    return UserContext(email="admin@x.test", role=UserRole.EXECUTIVE,
                       access=build_access(perms), organization_id=org)


class _RulesDB:
    """The statements of the PUT, answered from Python. The SQL itself runs in
    the R8 suite: this fake decides nothing that the database decides."""

    def __init__(self, rules: dict[str, Any] | None = None) -> None:
        self.rules: dict[str, Any] = dict(rules or {})
        self.groups = {"ops"}
        self.people = {"a@x.test"}
        self.writes: list[str] = []
        #: Each statement in order, as a short word. ACC-1 fix round 1 checks
        #: that the row lock comes before the checks of groups and people.
        self.order: list[str] = []

    async def execute(self, sql: Any, params: dict | None = None) -> Any:
        s = " ".join(str(sql).split())
        p = params or {}
        read = " ".join(_common.ORG_INTEGRATION_RULES_SQL.split())
        for lock, suffix in [(None, ""), *_common.ORG_RULE_LOCKS.items()]:
            if s == read + suffix:
                self.order.append(f"rules:{lock}")
                return _Result([{"rules": json.dumps(self.rules)}])
        if "FROM org_group" in s:
            self.order.append("groups")
            return _Result([(g,) for g in p["slugs"] if g in self.groups])
        if "FROM app_user" in s:
            self.order.append("people")
            return _Result([(e,) for e in p["emails"] if e in self.people])
        if s == " ".join(routes.SCRUB_RULES_SQL.split()):
            self.writes.append("scrub")
            self.rules = json.loads(p["rules"])
            return _Result([], rowcount=1)
        if s == " ".join(routes.SET_RULE_SQL.split()):
            self.writes.append("set")
            self.rules[p["svc"]] = json.loads(p["rule"])
            return _Result([], rowcount=1)
        if s == " ".join(routes.REMOVE_RULE_SQL.split()):
            self.writes.append("remove")
            self.rules.pop(p["svc"], None)
            return _Result([], rowcount=1)
        raise AssertionError(f"unhandled SQL in fake: {s}")


class _Result:
    def __init__(self, rows: list[Any], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> _Result:
        return self

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return self._rows


@pytest.fixture
def world(monkeypatch):
    """The admin router with a fake session, the flag on for :data:`ORG`, and
    the audit and the cache recorded."""
    from contextlib import asynccontextmanager

    from acb_common import get_settings

    fake = _RulesDB()

    @asynccontextmanager
    async def _session(organization_id: Any = None) -> Any:
        yield fake

    async def _org(_db: Any, _user: Any) -> str:
        return ORG

    async def _closed(_db: Any, _org: str) -> dict[str, str]:
        return {"mcp-x": "mcp", "api-zoho_books": "api"}

    state = SimpleNamespace(db=fake, audit=[], cleared=[])
    monkeypatch.setattr(get_settings(), "integration_access_orgs", ORG, raising=False)
    monkeypatch.setattr(routes, "_tenant_session", _session)
    monkeypatch.setattr(routes, "get_org_id", _org)
    monkeypatch.setattr(routes, "closed_list", _closed)
    monkeypatch.setattr(routes, "record_admin_change",
                        lambda *a, **k: state.audit.append((a, k)))
    monkeypatch.setattr(routes, "invalidate_access",
                        lambda *a: state.cleared.append(a))
    return state


async def _call(user: UserContext, method: str, path: str, body: Any = None,
                content: bytes | None = None) -> httpx.Response:
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from gateway.routes.admin import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://gateway.test") as client:
        if content is not None:
            return await client.request(method, path, content=content,
                                        headers={"content-type": "application/json"})
        return await client.request(method, path, json=body)


GET = ("GET", "/admin/integration-access", None)
PUT = ("PUT", "/admin/integration-access/mcp-x", {"mode": "admins"})


@pytest.mark.parametrize("route", [GET, PUT], ids=["get", "put"])
async def test_an_org_off_the_flag_gets_404_before_any_check(world, monkeypatch, route) -> None:
    from acb_common import get_settings

    for flag in ("", "00000000-0000-0000-0000-0000000000bb"):
        monkeypatch.setattr(get_settings(), "integration_access_orgs", flag, raising=False)
        for user in (_user(ADMIN_PERMS), _user([])):
            r = await _call(user, *route)
            assert (r.status_code, r.json()) == (404, {"detail": "Not Found"}), (flag, r.text)
        # A body that is not JSON is a 404 too: the body is read after the flag.
        r = await _call(_user(ADMIN_PERMS), "PUT", PUT[1], content=b"{not json")
        assert r.status_code == 404
    assert world.db.writes == [] and world.audit == []


@pytest.mark.parametrize("route", [GET, PUT], ids=["get", "put"])
@pytest.mark.parametrize("missing", ["integrations:manage", "admin:access:manage"])
async def test_a_caller_without_either_permission_gets_403(world, route, missing) -> None:
    perms = [p for p in ADMIN_PERMS if p != missing]
    r = await _call(_user(perms), *route)
    assert r.status_code == 403 and missing in r.json()["detail"], r.text
    assert world.db.writes == []


def test_the_flag_reader_takes_the_org_of_the_session(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "integration_access_orgs", f" {ORG.upper()} ,x",
                        raising=False)
    assert _common.integration_access_allowed(ORG)
    assert not _common.integration_access_allowed(None)
    assert not _common.integration_access_allowed("other")
    monkeypatch.setattr(get_settings(), "integration_access_orgs", "*", raising=False)
    assert _common.integration_access_allowed("any-org")
    monkeypatch.setattr(get_settings(), "integration_access_orgs", "", raising=False)
    assert not _common.integration_access_allowed(ORG), "default empty is off"


@pytest.mark.parametrize("service,body", [
    ("mcp-nope", {"mode": "admins"}),
    ("org", {"mode": "admins"}),
    ("MCP-X", {"mode": "admins"}),
    ("mcp-x", {"mode": "chosen", "groups": ["no-such-team"]}),
    ("mcp-x", {"mode": "chosen", "people": ["stranger@x.test"]}),
    ("mcp-x", {"mode": "chosen", "people": ["org"]}),
    ("mcp-x", {"mode": "chosen", "groups": ["group:org", "org"]}),
    ("mcp-x", {"mode": "chosen", "people": ["group:ops"]}),
    ("mcp-x", {"mode": "admins", "people": ["a@x.test"]}),
    ("mcp-x", {"mode": "everyone", "groups": ["ops"]}),
    ("mcp-x", {"mode": "org"}),
    ("mcp-x", {"mode": "admins", "organization_id": ORG}),
], ids=["unknown-mcp", "service-org", "upper", "group", "person", "person-org", "group-org",
        "group-as-person", "admins-with-people", "everyone-with-groups", "mode-org",
        "org-in-body"])
async def test_an_unknown_service_group_or_address_and_org_get_422(world, service, body) -> None:
    r = await _call(_user(ADMIN_PERMS), "PUT", f"/admin/integration-access/{service}", body)
    assert r.status_code == 422, r.text
    assert world.db.writes == [] and world.audit == [] and world.cleared == []


async def test_each_write_records_the_audit_line_and_clears_the_cache(world) -> None:
    r = await _call(_user(ADMIN_PERMS), "PUT", "/admin/integration-access/mcp-x",
                    {"mode": "chosen", "groups": ["OPS"], "people": ["A@X.test"]})
    assert r.status_code == 200, r.text
    assert (r.json()["mode"], r.json()["groups"], r.json()["people"]) == (
        "chosen", ["ops"], ["a@x.test"])
    assert world.db.rules["mcp-x"]["subjects"] == ["a@x.test", "group:ops"]
    r = await _call(_user(ADMIN_PERMS), "PUT", "/admin/integration-access/mcp-x",
                    {"mode": "everyone"})
    assert r.status_code == 200 and "mcp-x" not in world.db.rules
    assert world.db.writes == ["set", "remove"]
    assert [(a[1], a[2], k["mode"]) for a, k in world.audit] == [
        ("org.integration_access_set", "integration:mcp-x", "chosen"),
        ("org.integration_access_set", "integration:mcp-x", "everyone"),
    ]
    assert world.audit[0][1]["subjects"] == ["group:ops", "a@x.test"]
    assert world.cleared == [(), ()], "each write clears the cache of every member"


async def test_everyone_clears_a_stale_rule_of_a_service_that_left_the_list(world) -> None:
    world.db.rules = {"mcp-gone": {"subjects": []}}
    r = await _call(_user(ADMIN_PERMS), "PUT", "/admin/integration-access/mcp-gone",
                    {"mode": "everyone"})
    assert r.status_code == 200 and world.db.rules == {}
    r = await _call(_user(ADMIN_PERMS), "PUT", "/admin/integration-access/mcp-gone",
                    {"mode": "admins"})
    assert r.status_code == 422, "a stale service takes no new rule"


async def test_the_list_shows_each_service_and_a_stale_rule(world) -> None:
    world.db.rules = {"mcp-x": {"subjects": ["group:ops"], "set_by": "b@x.test",
                                "set_at": "t"}, "mcp-gone": {"subjects": []}}
    r = await _call(_user(ADMIN_PERMS), *GET)
    assert r.status_code == 200, r.text
    rows = {row["service"]: row for row in r.json()["services"]}
    assert rows["mcp-x"]["mode"] == "chosen" and rows["mcp-x"]["groups"] == ["ops"]
    assert rows["mcp-x"]["set_by"] == "b@x.test" and rows["mcp-x"]["listed"]
    assert rows["mcp-gone"]["mode"] == "admins" and not rows["mcp-gone"]["listed"]
    assert rows["api-zoho_books"]["mode"] == "everyone"
    assert {row["kind"] for row in rows.values() if row["listed"]} == {"mcp", "api"}
    assert r.json()["readable"] is True


#: The real list, kept before the `world` fixture replaces it.
_REAL_CLOSED_LIST = routes.closed_list


def _api_templates() -> list[str]:
    from gateway.connections import api as api_templates

    return list(api_templates.TEMPLATES)


async def test_a_built_in_connector_takes_no_new_rule_and_a_stored_one_clears(
        world, monkeypatch) -> None:
    """ACC-1 fix round 2. A tool reads a `_REGISTRY` credential with no check,
    so the real list refuses each key. A rule that is already stored still
    shows with `listed` false, and `everyone` clears it."""
    from acb_skills.integrations import list_registered
    from gateway.connections import store

    async def _rows(_db: Any, _org: str, _prefix: str, _app: str) -> list[Any]:
        return [SimpleNamespace(provider="mcp:x")]

    monkeypatch.setattr(store, "list_by_prefix", _rows)
    monkeypatch.setattr(routes, "closed_list", _REAL_CLOSED_LIST)
    keys = list_registered()
    assert {"serpapi", "litellm", "smtp", "zoho-crm"} <= set(keys)
    for key in keys:
        r = await _call(_user(ADMIN_PERMS), "PUT", f"/admin/integration-access/{key}",
                        {"mode": "admins"})
        assert r.status_code == 422, (key, r.text)
    assert world.db.writes == [] and world.audit == []

    world.db.rules = {"serpapi": {"subjects": []}}
    r = await _call(_user(ADMIN_PERMS), *GET)
    rows = {row["service"]: row for row in r.json()["services"]}
    assert set(rows) - {"serpapi"} == {"mcp-x", *(f"api-{t}" for t in _api_templates())}
    assert (rows["serpapi"]["listed"], rows["serpapi"]["mode"]) == (False, "admins")
    r = await _call(_user(ADMIN_PERMS), "PUT", "/admin/integration-access/serpapi",
                    {"mode": "everyone"})
    assert r.status_code == 200 and world.db.rules == {}, r.text
    assert r.json()["listed"] is False


# ── 5. The service names ─────────────────────────────────────────────────────


async def test_each_service_name_is_the_name_that_each_check_uses(monkeypatch) -> None:
    pytest.importorskip("orchestrator")
    from acb_skills.integrations import list_registered
    from gateway.connections import api as api_templates
    from gateway.connections import store
    from gateway.connections.mcp_tools import mcp_service as gateway_mcp_service
    from gateway.routes import api_connections
    from orchestrator._maf_mcp import mcp_service as run_mcp_service

    async def _rows(_db: Any, _org: str, prefix: str, owning_app: str) -> list[Any]:
        assert (prefix, owning_app) == ("mcp:", "integrations")
        return [SimpleNamespace(provider="mcp:tally"), SimpleNamespace(provider="mcp:books-1")]

    monkeypatch.setattr(store, "list_by_prefix", _rows)
    services = await routes.closed_list(object(), ORG)
    for name in ("tally", "books-1"):
        assert gateway_mcp_service(name) == run_mcp_service(name)
        assert services[gateway_mcp_service(name)] == "mcp"
    for tid in api_templates.TEMPLATES:
        assert services[f"api-{tid}"] == "api"
        # The API route checks exactly this name.
        row = SimpleNamespace(provider_meta={"grant": "admin"})
        ok = UserContext(email="a@x.test", role=UserRole.EMPLOYEE, organization_id=ORG,
                         access=build_access([_use(f"api-{tid}"), MANAGE]))
        assert api_connections._granted(ok, tid, row)
        refused = UserContext(email="a@x.test", role=UserRole.EMPLOYEE, organization_id=ORG,
                              access=build_access(["integrations:use:*", MANAGE],
                                                  [(_use(f"api-{tid}"), "deny")]))
        assert not api_connections._granted(refused, tid, row)
    # ACC-1 fix round 2: no built-in connector is on the list. Its tools read
    # the credential through `credential()`, with no access check.
    assert set(services.values()) == {"mcp", "api"}, services
    for key in list_registered():
        assert key not in services, key


# ── 6. The preview ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("failed", [False, True])
async def test_the_preview_shows_the_rule_as_the_source(monkeypatch, failed) -> None:
    from contextlib import asynccontextmanager

    from acb_auth.access import IntegrationRules
    from gateway.routes.admin import members

    @asynccontextmanager
    async def _session(organization_id: Any = None) -> Any:
        yield object()

    async def _org(*_a: Any) -> str:
        return ORG

    async def _member(*_a: Any) -> dict[str, Any]:
        return {"id": "u-1", "email": "m@x.test", "display_name": "M", "status": "active"}

    async def _roles(*_a: Any) -> list[str]:
        return ["member"]

    async def _perms(*_a: Any) -> dict[str, list[str]]:
        return {"member": ["integrations:use:*", "feature:chat"]}

    async def _overrides(*_a: Any) -> list[Any]:
        return []

    async def _rules(*_a: Any) -> IntegrationRules:
        if failed:
            return IntegrationRules(failed=True)
        return IntegrationRules({"mcp-x": {"subjects": []}, "zoho-crm": {"subjects": []}})

    for name, value in (("_tenant_session", _session), ("get_org_id", _org),
                        ("get_member", _member), ("roles_for_user", _roles),
                        ("_role_permission_map", _perms), ("_load_overrides", _overrides),
                        ("read_integration_rules", _rules),
                        ("_integration_names", lambda: ["apollo", "zoho-crm"]),
                        ("_agent_names", lambda: [])):
        monkeypatch.setattr(members, name, value)
    out = await members.get_member_access("m@x.test", admin=FULL_ADMIN)
    rows = {r["name"]: r for r in out["integrations"]}
    if failed:
        assert set(rows) == {"apollo", "zoho-crm"}
        assert all(not r["allowed"] and r["source"] == RULE_SOURCE for r in rows.values())
        assert all(r["pattern"] == USE_ALL for r in rows.values())
        return
    assert set(rows) == {"apollo", "zoho-crm", "mcp-x"}, "each ruled service is listed"
    assert (rows["mcp-x"]["allowed"], rows["mcp-x"]["source"]) == (False, RULE_SOURCE)
    assert rows["mcp-x"]["pattern"] == _use("mcp-x")
    assert (rows["zoho-crm"]["allowed"], rows["zoho-crm"]["source"]) == (False, RULE_SOURCE)
    assert (rows["apollo"]["allowed"], rows["apollo"]["source"]) == (True, "role")
    assert out["denied"] == [], "the member's own overrides, not the rule"
    chat = next(f for f in out["features"] if f["slug"] == "chat")
    assert chat["allowed"] and chat["source"] == "role"


# ── 7. The group-write gate ──────────────────────────────────────────────────


@pytest.fixture
def ruled_db(db: _FakeDB) -> _FakeDB:  # noqa: F811 - the fixture of test_admin_groups
    db.seed_group("g-ops", "ops", "Ops")
    db.seed_group("g-free", "free", "Free")
    db.seed_user("u-1", "priya@fracktal.in", "Priya")
    db.integration_access = {"mcp-x": {"subjects": ["group:ops"]}}
    return db


async def test_a_member_write_to_a_ruled_group_needs_admin_access_manage(ruled_db) -> None:
    add = groups_routes.GroupMemberAdd(email="priya@fracktal.in", grant_center_access=False)
    with pytest.raises(HTTPException) as refused:
        await groups_routes.add_group_member("ops", add, admin=ROSTER_ADMIN)
    assert refused.value.status_code == 403 and "admin:access:manage" in refused.value.detail
    assert ruled_db.members == {}
    # A team that no rule names keeps its old floor.
    await groups_routes.add_group_member("free", add, admin=ROSTER_ADMIN)
    # An access admin may change the ruled team, and the removal is gated too.
    await groups_routes.add_group_member("ops", add, admin=FULL_ADMIN)
    with pytest.raises(HTTPException) as removal:
        await groups_routes.remove_group_member("ops", "priya@fracktal.in", admin=ROSTER_ADMIN)
    assert removal.value.status_code == 403
    await groups_routes.remove_group_member("ops", "priya@fracktal.in", admin=FULL_ADMIN)


async def test_nobody_deletes_a_group_that_a_rule_names(ruled_db) -> None:
    with pytest.raises(HTTPException) as refused:
        await groups_routes.delete_group("ops", admin=FULL_ADMIN)
    assert refused.value.status_code == 409 and "rule" in refused.value.detail
    assert ruled_db._group_by_slug("ops") is not None
    ruled_db.integration_access = "not json"
    with pytest.raises(HTTPException) as unreadable:
        await groups_routes.delete_group("free", admin=FULL_ADMIN)
    assert unreadable.value.status_code == 409, "an unreadable value counts as named"
    ruled_db.integration_access = None
    out = await groups_routes.delete_group("free", admin=FULL_ADMIN)
    assert out["status"] == "deleted"


# ── 8. The statements and the one writer ─────────────────────────────────────


def test_the_write_makes_a_missing_parent_and_names_the_org() -> None:
    for sql in (routes.SET_RULE_SQL, routes.REMOVE_RULE_SQL):
        flat = " ".join(sql.split())
        assert "COALESCE(settings -> 'integration_access', '{}'::jsonb)" in flat
        assert flat.endswith("WHERE id = CAST(:org AS uuid)")
    # asyncpg refuses a bare `:svc` in jsonb_build_object ("could not determine
    # data type of parameter $1", measured 2026-11-26).
    assert "jsonb_build_object(CAST(:svc AS text), CAST(:rule AS jsonb))" in routes.SET_RULE_SQL


def test_the_rules_read_is_never_part_of_the_access_read() -> None:
    import acb_auth.access as access_mod

    assert "integration_access" not in access_mod._ACCESS_SQL
    assert "integration_access" not in access_mod._ACCESS_IN_ORG_SQL
    flat = " ".join(access_mod._INTEGRATION_RULES_SQL.split())
    assert "WHERE o.id = CAST(:org AS uuid)" in flat
    assert "g.organization_id = o.id" in flat, "org_group has no row security"


def test_only_the_route_module_writes_the_key() -> None:
    """R5 and §3.9.2: no other code writes ``integration_access``."""
    pattern = re.compile(r"\{integration_access\}|'integration_access'\s*,")
    writers = []
    for root in ("apps", "packages"):
        for path in (REPO / root).rglob("*.py"):
            if "node_modules" in path.parts or ".venv" in path.parts:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                writers.append(path.relative_to(REPO).as_posix())
    assert writers == ["apps/services/gateway/gateway/routes/admin/integration_access.py"]


# ── 9. ACC-1 fix round 1: the purge scrub and the row lock ──────────────────


def test_without_member_takes_the_address_off_each_list_and_keeps_the_rule() -> None:
    raw = {"mcp-x": {"subjects": ["Gone@X.test ", "a@x.test"], "set_by": "b@x.test"},
           "zoho-crm": {"subjects": ["gone@x.test"]},
           "apollo": {"subjects": ["group:ops"]}}
    out, changed = without_member(json.dumps(raw), "GONE@x.test")
    assert changed == 2
    assert out["mcp-x"] == {"subjects": ["a@x.test"], "set_by": "b@x.test"}
    assert out["zoho-crm"] == {"subjects": []}, "an empty rule stays: admins only"
    assert out["apollo"] == raw["apollo"]
    # The scrubbed rules refuse the address, and refuse a member who joins
    # later with it (rule 5). The empty rule is admins only, never everyone.
    narrowed = narrow(MEMBER, out, email="gone@x.test")
    assert narrowed.refused == {"mcp-x", "zoho-crm", "apollo"}
    assert parse_rules(out)[0]["zoho-crm"] == frozenset()


@pytest.mark.parametrize("raw", [
    None, "not json", [1, 2], {"mcp-x": "junk"}, {"mcp-x": {"subjects": "gone@x.test"}},
    {"mcp-x": {"subjects": ["a@x.test"]}},
], ids=["none", "text", "list", "junk-rule", "junk-subjects", "not-named"])
def test_without_member_leaves_an_unreadable_or_unnamed_value_alone(raw: Any) -> None:
    assert without_member(raw, "gone@x.test") == (None, 0)


def test_without_member_never_widens_a_rule_that_the_code_cannot_read() -> None:
    raw = {"mcp-x": {"subjects": ["gone@x.test", 5]}}
    out, changed = without_member(raw, "gone@x.test")
    assert changed == 1 and out["mcp-x"]["subjects"] == [5]
    assert parse_rules(out)[0]["mcp-x"] is None, "still unreadable, so admins only"


async def test_the_scrub_locks_the_org_row_then_writes() -> None:
    fake = _RulesDB({"mcp-x": {"subjects": ["gone@x.test", "a@x.test"]},
                     "zoho-crm": {"subjects": ["a@x.test"]}})
    assert await routes.scrub_member_rules(fake, ORG, "gone@x.test") == 1
    assert fake.order == ["rules:update"] and fake.writes == ["scrub"]
    assert fake.rules == {"mcp-x": {"subjects": ["a@x.test"]},
                          "zoho-crm": {"subjects": ["a@x.test"]}}
    # Nothing names the address now, so a second scrub writes nothing.
    assert await routes.scrub_member_rules(fake, ORG, "gone@x.test") == 0
    assert fake.writes == ["scrub"]
    flat = " ".join(routes.SCRUB_RULES_SQL.split())
    assert flat.endswith("WHERE id = CAST(:org AS uuid)"), "R5: the org of the session"


async def test_the_scrub_refuses_a_write_that_changed_no_row() -> None:
    class _NoRow(_RulesDB):
        async def execute(self, sql: Any, params: dict | None = None) -> Any:
            if "jsonb_set(settings, '{integration_access}', CAST(:rules" in str(sql):
                return _Result([], rowcount=0)
            return await super().execute(sql, params)

    with pytest.raises(HTTPException) as failed:
        await routes.scrub_member_rules(_NoRow({"mcp-x": {"subjects": ["gone@x.test"]}}),
                                        ORG, "gone@x.test")
    assert failed.value.status_code == 500


async def test_the_purge_scrubs_the_rules_first(monkeypatch) -> None:
    """The scrub is the first statement of the purge body, so its row lock
    comes before each row lock of the deletes, and a refusal rolls it back."""
    from gateway.routes.admin import members

    calls: list[str] = []

    async def _scrub(_db: Any, org: str, email: str) -> int:
        calls.append(f"scrub:{org}:{email}")
        return 1

    async def _owner(*_a: Any, **_k: Any) -> None:
        calls.append("owner")
        raise HTTPException(status_code=409, detail="the last owner")

    monkeypatch.setattr(routes, "scrub_member_rules", _scrub)
    monkeypatch.setattr(members, "assert_owner_survives", _owner)
    with pytest.raises(HTTPException):
        await members.purge_member_rows(
            object(), ORG, {"id": "u-1", "email": " Gone@X.test"}, actor_email="a@x.test")
    assert calls == [f"scrub:{ORG}:gone@x.test", "owner"]


async def test_the_put_locks_the_org_row_before_it_checks_groups_and_people(world) -> None:
    r = await _call(_user(ADMIN_PERMS), "PUT", "/admin/integration-access/mcp-x",
                    {"mode": "chosen", "groups": ["ops"], "people": ["a@x.test"]})
    assert r.status_code == 200, r.text
    assert world.db.order[:3] == ["rules:update", "groups", "people"], world.db.order


async def test_the_group_gates_take_the_share_lock(ruled_db) -> None:
    add = groups_routes.GroupMemberAdd(email="priya@fracktal.in", grant_center_access=False)
    await groups_routes.add_group_member("ops", add, admin=FULL_ADMIN)
    await groups_routes.remove_group_member("ops", "priya@fracktal.in", admin=FULL_ADMIN)
    with pytest.raises(HTTPException):
        await groups_routes.delete_group("ops", admin=FULL_ADMIN)
    assert len(ruled_db.rule_reads) == 3
    assert all(s.endswith("FOR SHARE") for s in ruled_db.rule_reads), ruled_db.rule_reads
