"""API-2: the connect flow of Zoho Books, hermetic (task 3788, D228).

Spec: ``project-docs/specs/api_connections.md`` §2.2, §2.10 and §2.11 (AC1 to
AC7). The routes are ``gateway/routes/integrations_api_connect.py``, and the
pure helpers are ``gateway/connections/api/connect.py``. Zoho is an
``httpx.MockTransport`` (``FakeZoho`` of the CRM suite), the Books API is a
fake of the ``_https`` seam, Redis is a fake behind the real ``TenantRedis``,
and the store is an in-memory fake. ``test_api_connect_r8.py`` runs the real
SQL (AC8).

Mutation table. Each mutation was made by hand on 2026-11-19, and the named
test failed: 11 of 11 killed. M3 survived the first run, because the one-use
mark of the state is kept under the org of the session, so a state of org B
found no mark in org A. The test now plants the mark, so only the org check of
the state can refuse.

| # | Mutation | Test that fails |
|---|---|---|
| M1 | the flag dependency is gone from the router | ``test_ac1_flag_off_every_route_is_404_before_the_permission`` |
| M2 | the start drops ``require_permission`` | ``test_ac2_no_manage_permission_is_403`` |
| M3 | the callback does not compare the org of the state | ``test_ac3_the_state_must_name_the_session_even_with_a_mark`` |
| M3b | the callback does not compare the member of the state | ``test_ac3_the_state_must_name_the_session_even_with_a_mark`` |
| M4 | the callback reads the one-use mark with GET and not GETDEL | ``test_ac3_a_replayed_state_is_400_and_zoho_gets_no_call`` |
| M5 | the API domain is not checked against the template hosts | ``test_ac4_an_api_domain_off_the_template_is_400_and_the_token_goes`` |
| M6 | ``provider_meta`` keeps every key of the credential | ``test_provider_meta_keeps_the_public_facts_only`` |
| M7 | a failed test read does not stop the save | ``test_ac7_a_failed_test_read_saves_nothing_and_revokes`` |
| M8 | the pick takes any id, not one of the pending list | ``test_ac6_a_pick_outside_the_list_is_400_and_saves_nothing`` |
| M9 | the pick reads the entry with GET and not GETDEL | ``test_ac6_a_pick_is_taken_once`` |
| M10 | the test read sends no ``organization_id`` | ``test_the_test_read_sets_the_context_after_the_arguments`` |
| M11 | API-5: the pick takes the entry before it reads the member | ``test_gap3_another_admin_with_the_nonce_does_not_use_up_the_pick`` |
| M12 | API-5: the start does not call ``_start_again`` | ``test_gap1_a_new_start_revokes_the_old_pending_token_and_drops_it`` |
| M13 | API-5 review: the cancel takes the entry before it reads the member | ``test_cancel_by_another_admin_changes_nothing`` |
| M14 | API-5 review: ``_keep_pending`` does not drop the older entry | ``test_a_newer_consent_revokes_the_older_pending_token`` |
| M15 | API-5 review: ``_start_again`` runs before ``sign_state`` | ``test_a_signing_fault_keeps_the_old_pending_pick`` |
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

pytest.importorskip("gateway.routes.integrations_api_connect", reason="gateway not installed")

import structlog
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.tenant_redis import TenantRedis
from fastapi import FastAPI
from gateway import outbound_guard
from gateway.connections import store
from gateway.connections.api import connect
from gateway.connections.api.zoho_books import ZOHO_BOOKS
from gateway.crm_sources.base import SourceCredential
from gateway.crm_sources.zoho import ZohoSource
from gateway.routes import integrations_api_connect as routes
from gateway.routes.crm import connection as crm_connection
from gateway.routes.email.transport import signing

from tests.unit._mcp_oauth_fakes import FakeRawRedis
from tests.unit.test_crm_connection import (
    S_ACCESS,
    S_CODE,
    S_REFRESH,
    S_SECRET,
    FakeZoho,
)

ORG_A = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0dea001"
ORG_B = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0dea002"
ADMIN = "admin@api2.test"
OTHER_ADMIN = "other@api2.test"
S_OLD_REFRESH = "SENTINEL-API2-OLDREFRESH-61c0"
SENTINELS = (S_ACCESS, S_REFRESH, S_CODE, S_SECRET, S_OLD_REFRESH)

_NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


# ── Fakes ────────────────────────────────────────────────────────────────────


def _user(email: str = ADMIN, org: str = ORG_A, perms: list[str] | None = None) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(perms if perms is not None else [
            "feature:integrations", "integrations:manage"]),
        organization_id=org,
    )


@dataclass
class Books:
    """The Zoho Books API behind the ``_https`` seam. It records each call."""

    organizations: list[dict[str, Any]] = field(
        default_factory=lambda: [{"organization_id": "60001", "name": "Acme Books"}])
    invoice_status: int = 200
    invoice_body: bytes = b'{"code":0,"message":"success","invoices":[]}'
    requests: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)

    async def https(self, method: str, url: str, headers: dict[str, str]) -> outbound_guard.Answer:
        self.requests.append((method, url, dict(headers)))
        path = urlsplit(url).path
        if path == connect.ORGANIZATIONS_PATH:
            body = json.dumps({"code": 0, "message": "success",
                               "organizations": self.organizations}).encode()
            return outbound_guard.Answer(status_code=200, body=body)
        if path == "/books/v3/invoices":
            return outbound_guard.Answer(status_code=self.invoice_status, body=self.invoice_body)
        return outbound_guard.Answer(status_code=404, body=b"{}")


class FakeDb:
    def __init__(self) -> None:
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        self.sql.append((str(statement), dict(params or {})))
        return None


@dataclass
class Registry:
    """The in-memory twin of the ``api:zoho_books`` row of each org."""

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    saves: list[dict[str, Any]] = field(default_factory=list)
    db: FakeDb = field(default_factory=FakeDb)

    def _view(self, org: str) -> store.Connection | None:
        row = self.rows.get(org)
        if row is None:
            return None
        return store.Connection(
            id=row["id"], organization_id=org, provider=routes.PROVIDER,
            owning_app=routes.OWNING_APP, scope_kind="org", member_email=None,
            status=row["status"], provider_meta=dict(row["credential"].provider_meta),
            scopes=tuple(row["scopes"]), connected_by=row["connected_by"],
            last_sync_at=None, last_error=None, created_at=_NOW, updated_at=_NOW,
        )

    @asynccontextmanager
    async def session(self, org: str) -> Any:
        yield self.db

    async def get(self, db: Any, org: str, provider: str, owning_app: str, **kw: Any) -> Any:
        assert (provider, owning_app) == (routes.PROVIDER, routes.OWNING_APP)
        return self._view(org)

    async def load_credential_locked(self, db: Any, org: str, row_id: str) -> Any:
        row = self.rows.get(org)
        return row["credential"] if row else None

    async def save_credential(self, db: Any, org: str, provider: str, owning_app: str,
                              credential: SourceCredential, **kw: Any) -> Any:
        self.saves.append({"org": org, "provider": provider, "owning_app": owning_app,
                           "credential": credential, **kw})
        self.rows[org] = {"id": "0b0e8c1e-0000-4000-8000-00000000a2a2", "status": "connected",
                          "credential": credential, "scopes": list(kw.get("scopes") or ()),
                          "connected_by": kw.get("connected_by")}
        view = self._view(org)
        assert view is not None
        return view


@dataclass
class World:
    zoho: FakeZoho
    books: Books
    redis: FakeRawRedis
    registry: Registry

    def revoked(self) -> list[str]:
        return [parse_qs(r.content.decode()).get("token", [""])[0]
                for r in self.zoho.requests if r.url.path.endswith("/token/revoke")]

    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.zoho.requests if r.url.path.endswith("/oauth/v2/token")]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    import acb_llm.key_store as key_store

    settings = get_settings()
    for name, value in {
        "api_connections_orgs": "*",
        "crm_zoho_client_id": "1000.API2CLIENT",
        "crm_zoho_client_secret": S_SECRET,
        "gateway_session_secret": "api2-session-secret-not-a-default-0123456789",
        "acb_master_key": "api2-master-key-for-the-tests",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.metorite.com")
    monkeypatch.setattr(key_store, "_store", None)
    zoho = FakeZoho()
    monkeypatch.setattr(
        crm_connection, "_source",
        lambda credential=None, transport=None: ZohoSource(
            crm_connection.zoho_oauth_config(), credential, transport=zoho.transport),
    )
    books = Books()
    monkeypatch.setattr(routes, "_https", books.https)
    raw = FakeRawRedis()
    monkeypatch.setattr(routes, "_redis", lambda: TenantRedis(raw))
    registry = Registry()
    monkeypatch.setattr(routes, "tenant_session", registry.session)
    for name in ("get", "load_credential_locked", "save_credential"):
        monkeypatch.setattr(store, name, getattr(registry, name))
    return World(zoho=zoho, books=books, redis=raw, registry=registry)


async def _call(who: UserContext, method: str, path: str, *,
                params: dict[str, str] | None = None,
                body: dict[str, Any] | None = None) -> httpx.Response:
    """One request through the real router, as *who*."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    async def _caller() -> Any:
        if who.organization_id:
            bind_tenant(who.organization_id)
        return who

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
        ) as client:
            return await client.request(method, path, params=params, json=body)
    finally:
        release_tenant(scope)


async def _start(who: UserContext | None = None) -> str:
    started = await _call(who or _user(), "POST", "/integrations/api/zoho_books/start")
    assert started.status_code == 200, started.text
    return parse_qs(urlsplit(started.json()["url"]).query)["state"][0]


def _params(state: str, **over: str) -> dict[str, str]:
    return {"code": S_CODE, "state": state, "location": "in",
            "accounts-server": "https://accounts.zoho.in", **over}


async def _callback(state: str, who: UserContext | None = None, **over: str) -> httpx.Response:
    return await _call(who or _user(), "GET", "/integrations/api/zoho/callback",
                       params=_params(state, **over))


# ── The pure helpers ─────────────────────────────────────────────────────────


def test_the_scopes_are_books_reads_only() -> None:
    assert connect.ZOHO_BOOKS_SCOPES
    assert all(s.startswith("ZohoBooks.") and s.endswith(".READ") for s in connect.ZOHO_BOOKS_SCOPES)


def test_the_mark_round_trips_and_a_crm_state_has_none() -> None:
    assert connect.unmark_state(connect.mark_state("abc.def")) == "abc.def"
    assert connect.unmark_state("abc.def") is None
    assert connect.unmark_state(None) is None


def test_parse_organizations_keeps_ids_and_refuses_a_bad_one() -> None:
    body = json.dumps({"code": 0, "organizations": [
        {"organization_id": "1001", "name": "A​\nCo"},
        {"organization_id": 1002, "name": "B" * 500},
        {"organization_id": "1001", "name": "dup"},
    ]}).encode()
    pairs = connect.parse_organizations(body)
    assert [i for i, _ in pairs] == ["1001", "1002"]
    assert pairs[0][1] == "A Co" and len(pairs[1][1]) == connect.MAX_NAME
    for bad in (
        {"code": 0, "organizations": [{"organization_id": "1/../2"}]},
        {"code": 0, "organizations": [{"organization_id": True}]},
        {"code": 0, "organizations": [{"name": "no id"}]},
        {"code": 57, "message": "not authorized"},
        {"code": 0, "organizations": "x"},
    ):
        with pytest.raises(connect.ContextUnreadable):
            connect.parse_organizations(json.dumps(bad).encode())
    with pytest.raises(connect.ContextUnreadable):
        connect.parse_organizations(b"<html>")


def test_provider_meta_keeps_the_public_facts_only() -> None:
    meta = connect.provider_meta(ZOHO_BOOKS, {
        "location": "in", "api_domain": "https://www.zohoapis.in",
        "accounts_server": "https://accounts.zoho.in",
        "access_token": S_ACCESS, "account_id": "x",
    }, organization_id="60001")
    assert set(meta) == set(connect.PUBLIC_META_KEYS)
    assert meta["grant"] == "admin" and meta["organization_id"] == "60001"
    assert meta["template_version"] == str(ZOHO_BOOKS.version)
    assert meta["ops_hash"] == connect.ops_hash(ZOHO_BOOKS)
    assert S_ACCESS not in json.dumps(meta)
    with pytest.raises(connect.ContextUnreadable):
        connect.provider_meta(ZOHO_BOOKS, {}, organization_id="1 OR 1")


def test_the_ops_hash_changes_with_an_op() -> None:
    from dataclasses import replace

    changed = replace(ZOHO_BOOKS, operations=ZOHO_BOOKS.operations[:1])
    assert connect.ops_hash(changed) != connect.ops_hash(ZOHO_BOOKS)
    assert connect.ops_hash(ZOHO_BOOKS) == connect.ops_hash(ZOHO_BOOKS)


def test_the_test_read_sets_the_context_after_the_arguments() -> None:
    from acb_common.app_manifests.base import BadCall

    url = connect.call_url("https://www.zohoapis.in", ZOHO_BOOKS, "list_invoices",
                           {"per_page": "1"}, {"organization_id": "60001"})
    q = parse_qs(urlsplit(url).query)
    assert urlsplit(url).path == "/books/v3/invoices"
    assert q == {"per_page": ["1"], "organization_id": ["60001"]}
    with pytest.raises(BadCall):
        connect.call_url("https://www.zohoapis.in", ZOHO_BOOKS, "list_invoices",
                         {"organization_id": "666"}, {"organization_id": "60001"})


def test_a_host_off_the_template_is_refused() -> None:
    from gateway.crm_sources.base import UntrustedHost

    assert connect.template_host(ZOHO_BOOKS, "https://www.zohoapis.in") == "https://www.zohoapis.in"
    for bad in ("https://evil.example", "http://www.zohoapis.in", "https://www.zohoapis.in/x"):
        with pytest.raises(UntrustedHost):
            connect.template_host(ZOHO_BOOKS, bad)


# ── AC1 and AC2: the flag and the permission ────────────────────────────────

_ROUTES = (
    ("POST", "/integrations/api/zoho_books/start", None, None),
    ("GET", "/integrations/api/zoho/callback", {"state": "zb.x", "code": "c"}, None),
    ("POST", "/integrations/api/zoho_books/pick", None,
     {"pick": "A" * 22, "zoho_organization_id": "60001"}),
    ("GET", "/integrations/api/zoho_books", None, None),
    # API-5 review, P2: the cancel of a pending pick.
    ("POST", "/integrations/api/zoho_books/cancel", None, {"pick": "A" * 22}),
)


@pytest.mark.parametrize(("method", "path", "params", "body"), _ROUTES)
async def test_ac1_flag_off_every_route_is_404_before_the_permission(
    world: World, monkeypatch: pytest.MonkeyPatch, method: str, path: str,
    params: Any, body: Any,
) -> None:
    monkeypatch.setattr(get_settings(), "api_connections_orgs", ORG_B, raising=False)
    for who in (_user(), _user(perms=[])):
        res = await _call(who, method, path, params=params, body=body)
        assert res.status_code == 404, (who.access, res.text)
    assert world.zoho.requests == [] and world.books.requests == []


@pytest.mark.parametrize(("method", "path", "params", "body"), _ROUTES)
async def test_ac2_no_manage_permission_is_403(
    world: World, method: str, path: str, params: Any, body: Any,
) -> None:
    res = await _call(_user(perms=["feature:integrations"]), method, path,
                      params=params, body=body)
    assert res.status_code == 403, res.text
    assert "integrations:manage" in res.text
    assert world.zoho.requests == [] and world.books.requests == []


# ── AC3: the state ──────────────────────────────────────────────────────────


async def test_ac3_a_bad_state_is_400_and_zoho_gets_no_call(world: World) -> None:
    good = await _start()
    crm_state = signing.sign_state(purpose=crm_connection.STATE_PURPOSE, org=ORG_A, member=ADMIN)
    other_org = signing.sign_state(purpose=connect.STATE_PURPOSE, org=ORG_B, member=ADMIN)
    other_member = signing.sign_state(purpose=connect.STATE_PURPOSE, org=ORG_A,
                                      member=OTHER_ADMIN)
    expired = signing.sign_state(purpose=connect.STATE_PURPOSE, org=ORG_A, member=ADMIN,
                                 now=0.0)
    unmarked = connect.unmark_state(good)
    assert unmarked is not None
    for bad in (
        "zb.forged.mac",
        connect.mark_state(crm_state),       # another purpose, with the mark
        connect.mark_state(other_org),       # another org
        connect.mark_state(other_member),    # another member
        connect.mark_state(expired),
        unmarked,                            # no mark
        good[:-2] + "xx",                    # a changed MAC
    ):
        res = await _callback(bad)
        assert res.status_code == 400, (bad, res.text)
        assert res.json()["detail"] == "invalid_state"
    assert world.token_calls() == [] and world.books.requests == []
    assert world.registry.saves == []


@pytest.mark.parametrize(("org", "member"), [(ORG_B, ADMIN), (ORG_A, OTHER_ADMIN)])
async def test_ac3_the_state_must_name_the_session_even_with_a_mark(
    world: World, org: str, member: str,
) -> None:
    """The one-use mark of org A and the session member is planted, so only
    the org and member check of the state can refuse (M3)."""
    state = signing.sign_state(purpose=connect.STATE_PURPOSE, org=org, member=member)
    nonce = signing.verify_state(state, purpose=connect.STATE_PURPOSE)["nonce"]
    await routes._put(ORG_A, routes.STATE_NAMESPACE, nonce, ADMIN)
    res = await _callback(connect.mark_state(state))
    assert res.status_code == 400 and res.json()["detail"] == "invalid_state"
    assert world.token_calls() == []


async def test_ac3_a_state_of_another_member_is_400(world: World) -> None:
    state = await _start(_user(OTHER_ADMIN))
    res = await _callback(state)
    assert res.status_code == 400
    assert world.token_calls() == []


async def test_ac3_a_replayed_state_is_400_and_zoho_gets_no_call(world: World) -> None:
    state = await _start()
    first = await _callback(state)
    assert first.status_code == 302, first.text
    calls = len(world.zoho.requests)
    again = await _callback(state)
    assert again.status_code == 400 and again.json()["detail"] == "invalid_state"
    assert len(world.zoho.requests) == calls
    assert len(world.registry.saves) == 1


async def test_ac3_a_state_with_no_start_is_400(world: World) -> None:
    """A state that the server signed but never started (no one-use mark)."""
    state = connect.mark_state(signing.sign_state(
        purpose=connect.STATE_PURPOSE, org=ORG_A, member=ADMIN))
    res = await _callback(state)
    assert res.status_code == 400
    assert world.token_calls() == []


# ── AC4: the hosts ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("over", [
    {"accounts-server": "https://accounts.evil.example"},
    {"accounts-server": "http://accounts.zoho.in"},
    {"location": "eu"},
    {"location": "cn"},
])
async def test_ac4_an_untrusted_accounts_server_is_400(world: World, over: dict[str, str]) -> None:
    state = await _start()
    res = await _callback(state, **over)
    assert res.status_code == 400 and res.json()["detail"] == "untrusted_host"
    assert world.token_calls() == [] and world.books.requests == []


async def test_ac4_an_api_domain_off_the_zoho_list_is_400(world: World) -> None:
    world.zoho.token_body = {"access_token": S_ACCESS, "refresh_token": S_REFRESH,
                             "expires_in": 3600, "api_domain": "https://evil.example"}
    state = await _start()
    res = await _callback(state)
    assert res.status_code == 400 and res.json()["detail"] == "untrusted_host"
    assert world.books.requests == [] and world.registry.saves == []


async def test_ac4_an_api_domain_off_the_template_is_400_and_the_token_goes(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    only_eu = replace(ZOHO_BOOKS, hosts=(("eu", "https://www.zohoapis.eu"),))
    monkeypatch.setattr(routes, "TEMPLATE", only_eu)
    state = await _start()
    res = await _callback(state)
    assert res.status_code == 400 and res.json()["detail"] == "untrusted_host"
    assert world.books.requests == [] and world.registry.saves == []
    assert world.revoked() == [S_REFRESH]


# ── AC5: one organization ───────────────────────────────────────────────────


async def test_ac5_one_organization_saves_a_connected_admin_row(world: World) -> None:
    state = await _start()
    res = await _callback(state)
    assert res.status_code == 302, res.text
    assert res.headers["location"] == routes.RETURN_OK
    (saved,) = world.registry.saves
    assert (saved["org"], saved["provider"], saved["owning_app"]) == (
        ORG_A, "api:zoho_books", "integrations")
    assert saved["connected_by"] == ADMIN
    assert tuple(saved["scopes"]) == connect.ZOHO_BOOKS_SCOPES
    meta = dict(saved["credential"].provider_meta)
    assert set(meta) == set(connect.PUBLIC_META_KEYS)
    assert meta["grant"] == "admin" and meta["organization_id"] == "60001"
    assert meta["location"] == "in" and meta["api_domain"] == "https://www.zohoapis.in"
    # The context read, then the test read, each with the Zoho header.
    (ctx, read) = world.books.requests
    assert urlsplit(ctx[1]).path == "/books/v3/organizations"
    assert parse_qs(urlsplit(read[1]).query) == {"per_page": ["1"], "organization_id": ["60001"]}
    for _, url, headers in world.books.requests:
        assert url.startswith("https://www.zohoapis.in/")
        assert headers["Authorization"] == f"Zoho-oauthtoken {S_ACCESS}"
    # The lock of the save names the org and the provider.
    assert any("pg_advisory_xact_lock" in sql and p.get("key") == f"api.connect:{ORG_A}:api:zoho_books"
               for sql, p in world.registry.db.sql)
    # The consent asked for the Books scopes, with the CRM's redirect URI.
    exchange = parse_qs(world.token_calls()[0].content.decode())
    assert exchange["redirect_uri"] == ["https://app.metorite.com/api/crm/oauth/zoho/callback"]
    status = await _call(_user(), "GET", "/integrations/api/zoho_books")
    assert status.json()["connected"] is True and status.json()["grant"] == "admin"


async def test_the_consent_url_names_the_books_scopes_and_a_marked_state(world: World) -> None:
    started = await _call(_user(), "POST", "/integrations/api/zoho_books/start")
    q = parse_qs(urlsplit(started.json()["url"]).query)
    assert q["scope"] == [",".join(connect.ZOHO_BOOKS_SCOPES)]
    assert q["state"][0].startswith(connect.STATE_MARK)
    assert q["redirect_uri"] == ["https://app.metorite.com/api/crm/oauth/zoho/callback"]


async def test_a_reconnect_revokes_the_old_token_after_the_save(world: World) -> None:
    world.registry.rows[ORG_A] = {
        "id": "0b0e8c1e-0000-4000-8000-00000000a2a2", "status": "connected",
        "credential": SourceCredential(access_token="old", refresh_token=S_OLD_REFRESH,
                                       expires_at=_NOW, provider_meta={
                                           "accounts_server": "https://accounts.zoho.in"}),
        "scopes": [], "connected_by": ADMIN,
    }
    res = await _callback(await _start())
    assert res.status_code == 302
    assert world.revoked() == [S_OLD_REFRESH]


async def test_no_organization_revokes_and_saves_nothing(world: World) -> None:
    world.books.organizations = []
    res = await _callback(await _start())
    assert res.headers["location"] == f"{routes.RETURN_FAILED}no_books_organization"
    assert world.registry.saves == [] and world.revoked() == [S_REFRESH]


# ── AC6: several organizations ──────────────────────────────────────────────


def _two_orgs(world: World) -> None:
    world.books.organizations = [
        {"organization_id": "60001", "name": "Acme India"},
        {"organization_id": "60002", "name": "Acme Global"},
    ]


async def _pending(world: World) -> str:
    _two_orgs(world)
    res = await _callback(await _start())
    assert res.status_code == 302, res.text
    location = res.headers["location"]
    assert location.startswith(routes.RETURN_PICK)
    return location[len(routes.RETURN_PICK):]


async def test_ac6_several_organizations_save_nothing_and_wait_for_a_pick(world: World) -> None:
    pick = await _pending(world)
    assert world.registry.saves == []
    assert len(world.books.requests) == 1, "no test read before the pick"
    keys = [k for k in world.redis.data if f":{routes.PICK_NAMESPACE}:" in k]
    assert len(keys) == 1 and ORG_A in keys[0] and keys[0].endswith(pick)
    # API-5: the index of the admin's newest pick names the nonce, and its key
    # holds a hash of the member, never the address.
    (index,) = [k for k in world.redis.data if f":{routes.PICK_MEMBER_NAMESPACE}:" in k]
    assert world.redis.data[index] == pick and ADMIN not in index
    stored = world.redis.data[keys[0]]
    for sentinel in SENTINELS:
        assert sentinel not in stored, "the pending entry holds the token sealed only"
    status = await _call(_user(), "GET", "/integrations/api/zoho_books", params={"pick": pick})
    assert [c["id"] for c in status.json()["choices"]] == ["60001", "60002"]
    other = await _call(_user(OTHER_ADMIN), "GET", "/integrations/api/zoho_books",
                        params={"pick": pick})
    assert other.json()["choices"] == []


async def test_ac6_a_good_pick_saves_the_picked_organization(world: World) -> None:
    pick = await _pending(world)
    res = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60002"})
    assert res.status_code == 200, res.text
    assert res.json()["organization_id"] == "60002" and res.json()["connected"] is True
    (saved,) = world.registry.saves
    assert saved["credential"].provider_meta["organization_id"] == "60002"
    assert saved["credential"].refresh_token == S_REFRESH
    read = world.books.requests[-1][1]
    assert parse_qs(urlsplit(read).query)["organization_id"] == ["60002"]


async def test_ac6_a_pick_outside_the_list_is_400_and_saves_nothing(world: World) -> None:
    pick = await _pending(world)
    res = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60009"})
    assert res.status_code == 400 and res.json()["detail"] == "unknown_organization"
    assert world.registry.saves == []
    assert world.revoked() == [S_REFRESH]
    assert len(world.books.requests) == 1


async def test_ac6_a_pick_is_taken_once(world: World) -> None:
    pick = await _pending(world)
    first = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                        body={"pick": pick, "zoho_organization_id": "60001"})
    assert first.status_code == 200
    again = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                        body={"pick": pick, "zoho_organization_id": "60001"})
    assert again.status_code == 400 and again.json()["detail"] == "invalid_pick"
    assert len(world.registry.saves) == 1


async def test_ac6_another_member_cannot_pick(world: World) -> None:
    pick = await _pending(world)
    res = await _call(_user(OTHER_ADMIN), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60001"})
    assert res.status_code == 400 and res.json()["detail"] == "invalid_pick"
    assert world.registry.saves == []


async def test_gap3_another_admin_with_the_nonce_does_not_use_up_the_pick(world: World) -> None:
    """API-5, gap 3 of §2.11 (M11). The pick reads the member of the entry
    BEFORE it takes the entry. Another admin of the org who holds the nonce
    gets 400, the entry stays, its token is not revoked, and the admin who
    started the consent still picks."""
    pick = await _pending(world)
    for _ in range(2):
        res = await _call(_user(OTHER_ADMIN), "POST", "/integrations/api/zoho_books/pick",
                          body={"pick": pick, "zoho_organization_id": "60001"})
        assert res.status_code == 400 and res.json()["detail"] == "invalid_pick"
    assert [k for k in world.redis.data if k.endswith(pick)], "the entry stays"
    assert world.revoked() == [] and world.registry.saves == []
    own = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60001"})
    assert own.status_code == 200, own.text
    assert len(world.registry.saves) == 1


def test_pending_member_reads_the_member_only() -> None:
    record = connect.pending_record(ADMIN, "sealed-blob", [("60001", "A")])
    assert connect.pending_member(record) == ADMIN
    for bad in (None, "", "not json", "[]", '{"member": 3}', '{"x": 1}'):
        assert connect.pending_member(bad) is None


# ── API-5, gap 1: start again ───────────────────────────────────────────────


def _pick_keys(world: World) -> list[str]:
    return [k for k in world.redis.data if f":{routes.PICK_NAMESPACE}:" in k]


async def test_gap1_a_new_start_revokes_the_old_pending_token_and_drops_it(world: World) -> None:
    """API-5, gap 1 of §2.11 (M12). The admin leaves a pick, then starts
    again. The new start revokes the OLD refresh token at Zoho, deletes the
    entry, and still gives a consent URL. The old pick is then 400."""
    pick = await _pending(world)
    assert len(_pick_keys(world)) == 1 and world.revoked() == []
    tokens_before = len(world.token_calls())
    started = await _call(_user(), "POST", "/integrations/api/zoho_books/start")
    assert started.status_code == 200, started.text
    assert started.json()["url"].startswith("https://")
    assert world.revoked() == [S_REFRESH], "the old pending token is revoked"
    assert len(world.token_calls()) == tokens_before, "no new code exchange at the start"
    assert _pick_keys(world) == []
    old = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60001"})
    assert old.status_code == 400 and old.json()["detail"] == "invalid_pick"
    # The new consent still works to the end.
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    world.books.organizations = [{"organization_id": "60001", "name": "Acme"}]
    done = await _callback(state)
    assert done.headers["location"] == routes.RETURN_OK
    assert len(world.registry.saves) == 1


async def test_gap1_a_start_with_nothing_pending_revokes_nothing(world: World) -> None:
    for _ in range(2):
        assert (await _call(_user(), "POST", "/integrations/api/zoho_books/start")).status_code == 200
    assert world.revoked() == []


async def test_gap1_another_admin_start_leaves_my_pending_pick(world: World) -> None:
    """The index is per member, so the start of another admin of the org
    neither revokes nor drops my entry."""
    pick = await _pending(world)
    assert (await _call(_user(OTHER_ADMIN), "POST",
                        "/integrations/api/zoho_books/start")).status_code == 200
    assert world.revoked() == [] and len(_pick_keys(world)) == 1
    res = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60002"})
    assert res.status_code == 200, res.text


async def test_gap1_a_failed_revoke_does_not_block_the_start_and_logs_a_count_only(
    world: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    await _pending(world)
    calls: list[str] = []

    async def failing_revoke(credential: Any, why: str) -> str | None:
        calls.append(credential.refresh_token)
        raise RuntimeError(f"Zoho refused {credential.refresh_token}")

    monkeypatch.setattr(routes, "_revoke", failing_revoke)
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        started = await _call(_user(), "POST", "/integrations/api/zoho_books/start")
    assert started.status_code == 200, started.text
    assert calls == [S_REFRESH], "the revoke was asked with the OLD token"
    assert _pick_keys(world) == [], "the entry is gone even when the revoke failed"
    restarted = [e for e in events if e.get("event") == "api.connect_restarted"]
    assert restarted == [{"event": "api.connect_restarted", "revoked": 0, "failed": 1,
                          "log_level": "info"}]
    logged = json.dumps(events, default=str) + "\n".join(r.getMessage() for r in caplog.records)
    for sentinel in SENTINELS:
        assert sentinel not in logged


async def test_gap1_a_redis_fault_in_the_start_again_does_not_block_the_start(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    real_take = routes._take

    async def take_once_broken(org: str, namespace: str, nonce: str) -> Any:
        if namespace == routes.PICK_MEMBER_NAMESPACE and calls["n"] == 0:
            calls["n"] += 1
            raise ConnectionError("redis down")
        return await real_take(org, namespace, nonce)

    monkeypatch.setattr(routes, "_take", take_once_broken)
    started = await _call(_user(), "POST", "/integrations/api/zoho_books/start")
    assert started.status_code == 200, started.text
    assert calls["n"] == 1


# ── API-5: the result card (IN-9) on the status ─────────────────────────────


async def test_the_status_of_a_connected_row_carries_the_one_result_card(world: World) -> None:
    from acb_common.connector_result_card import result_card

    before = (await _call(_user(), "GET", "/integrations/api/zoho_books")).json()
    assert before["connected"] is False and before["card"] is None
    assert (await _callback(await _start())).status_code == 302
    after = (await _call(_user(), "GET", "/integrations/api/zoho_books")).json()
    assert after["connected"] is True
    assert after["card"] == result_card("api:zoho_books")
    assert after["card"]["label"] == "Zoho Books"
    assert after["card"]["works_now"]["apps"] == []


async def test_ac6_the_state_of_a_pending_consent_is_used_once(world: World) -> None:
    _two_orgs(world)
    state = await _start()
    assert (await _callback(state)).status_code == 302
    calls = len(world.zoho.requests)
    again = await _callback(state)
    assert again.status_code == 400 and len(world.zoho.requests) == calls


# ── AC7: the test read ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("status", "body"), [
    (401, b'{"code":57,"message":"not authorized"}'),
    (200, b"<html>not json</html>"),
    (200, b'{"code":0,"message":"success"}'),
])
async def test_ac7_a_failed_test_read_saves_nothing_and_revokes(
    world: World, status: int, body: bytes,
) -> None:
    world.books.invoice_status, world.books.invoice_body = status, body
    res = await _callback(await _start())
    assert res.status_code == 302
    assert res.headers["location"] == f"{routes.RETURN_FAILED}test_read_failed"
    assert world.registry.saves == []
    assert world.revoked() == [S_REFRESH]


async def test_ac7_a_failed_test_read_of_a_pick_is_502(world: World) -> None:
    pick = await _pending(world)
    world.books.invoice_status = 500
    res = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60001"})
    assert res.status_code == 502 and res.json()["detail"] == "test_read_failed"
    assert world.registry.saves == [] and world.revoked() == [S_REFRESH]


# ── The sentinel fence ──────────────────────────────────────────────────────


async def test_no_code_token_or_secret_reaches_a_response_meta_or_a_log(
    world: World, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    bodies: list[str] = []
    with structlog.testing.capture_logs() as events:
        started = await _call(_user(), "POST", "/integrations/api/zoho_books/start")
        bodies.append(started.text)
        state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
        done = await _callback(state)
        bodies += [done.text, json.dumps(dict(done.headers))]
        bodies.append((await _call(_user(), "GET", "/integrations/api/zoho_books")).text)
        # A failed test read, a pending pick, a bad pick and a bad state.
        world.books.invoice_status = 500
        failed = await _callback(await _start())
        bodies += [failed.text, json.dumps(dict(failed.headers))]
        world.books.invoice_status = 200
        pick = await _pending(world)
        bad = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                          body={"pick": pick, "zoho_organization_id": "1"})
        bodies.append(bad.text)
        bodies.append((await _callback("zb.forged")).text)
    records = [r for r in caplog.records if "gateway.test" not in r.getMessage()]
    logged = "\n".join([r.getMessage() for r in records] + [str(r.args) for r in records]
                       + [json.dumps(e, default=str) for e in events])
    assert logged, "the log capture saw nothing, so the fence proves nothing"
    metas = json.dumps([dict(s["credential"].provider_meta) for s in world.registry.saves])
    haystack = "\n".join(bodies) + "\n" + logged + "\n" + metas
    for sentinel in SENTINELS:
        assert sentinel not in haystack, f"{sentinel} leaked"


# ── API-5 review, P2: the cancel of a pending pick ──────────────────────────


async def _cancel(pick: str, who: UserContext | None = None) -> httpx.Response:
    return await _call(who or _user(), "POST", "/integrations/api/zoho_books/cancel",
                       body={"pick": pick})


def _index_keys(world: World) -> list[str]:
    return [k for k in world.redis.data if f":{routes.PICK_MEMBER_NAMESPACE}:" in k]


async def test_cancel_revokes_my_pending_token_once_and_drops_the_entry(world: World) -> None:
    pick = await _pending(world)
    first = await _cancel(pick)
    assert first.status_code == 200 and first.json() == {"cancelled": True}
    assert world.revoked() == [S_REFRESH]
    assert _pick_keys(world) == [] and _index_keys(world) == []
    again = await _cancel(pick)
    assert again.status_code == 200 and again.json() == {"cancelled": False}
    assert world.revoked() == [S_REFRESH], "a second cancel revokes nothing"
    late = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                       body={"pick": pick, "zoho_organization_id": "60001"})
    assert late.status_code == 400 and world.registry.saves == []


async def test_cancel_by_another_admin_changes_nothing(world: World) -> None:
    """The member check comes first (M13): another admin who holds the nonce
    neither revokes nor drops my entry, and I still pick."""
    pick = await _pending(world)
    res = await _cancel(pick, _user(OTHER_ADMIN))
    assert res.status_code == 200 and res.json() == {"cancelled": False}
    assert world.revoked() == [] and len(_pick_keys(world)) == 1 and len(_index_keys(world)) == 1
    own = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": pick, "zoho_organization_id": "60001"})
    assert own.status_code == 200, own.text


async def test_cancel_with_a_failing_revoke_still_drops_and_logs_counts_only(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pick = await _pending(world)

    async def failing_revoke(credential: Any, why: str) -> str | None:
        raise RuntimeError(f"Zoho refused {credential.refresh_token}")

    monkeypatch.setattr(routes, "_revoke", failing_revoke)
    with structlog.testing.capture_logs() as events:
        res = await _cancel(pick)
    assert res.status_code == 200 and res.json() == {"cancelled": True}
    assert _pick_keys(world) == []
    assert [e for e in events if e.get("event") == "api.connect_cancelled"] == [
        {"event": "api.connect_cancelled", "revoked": 0, "failed": 1, "log_level": "info"}]
    for sentinel in SENTINELS:
        assert sentinel not in json.dumps(events, default=str)


async def test_cancel_of_an_unknown_or_bad_handle(world: World) -> None:
    assert (await _cancel("Z" * 22)).json() == {"cancelled": False}
    assert (await _cancel("short")).status_code == 422
    assert world.revoked() == []


async def test_a_newer_consent_revokes_the_older_pending_token(world: World) -> None:
    """Two consents of one admin in flight (P3-3 of the review, M14). The
    second pending pick replaces the index, so the older entry goes and its
    token is revoked. The newer one stays and picks."""
    _two_orgs(world)
    first_state, second_state = await _start(), await _start()
    assert (await _callback(first_state)).status_code == 302
    older = _pick_keys(world)
    assert len(older) == 1
    world.zoho.token_body = {"access_token": S_ACCESS, "refresh_token": S_OLD_REFRESH,
                             "expires_in": 3600, "api_domain": "https://www.zohoapis.in"}
    second = await _callback(second_state)
    newer = second.headers["location"][len(routes.RETURN_PICK):]
    assert world.revoked() == [S_REFRESH], "the older pending token is revoked"
    assert len(_pick_keys(world)) == 1 and _pick_keys(world)[0].endswith(newer)
    res = await _call(_user(), "POST", "/integrations/api/zoho_books/pick",
                      body={"pick": newer, "zoho_organization_id": "60002"})
    assert res.status_code == 200, res.text
    assert world.registry.saves[0]["credential"].refresh_token == S_OLD_REFRESH


async def test_a_signing_fault_keeps_the_old_pending_pick(
    world: World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_start_again`` runs only after the new state is signed (M15). A 503
    ``signing_unavailable`` leaves the old pick and its token alone."""
    pick = await _pending(world)

    def no_signing(**_k: Any) -> str:
        raise signing.SigningUnavailable("no key")

    monkeypatch.setattr(routes, "sign_state", no_signing)
    res = await _call(_user(), "POST", "/integrations/api/zoho_books/start")
    assert res.status_code == 503 and res.json()["detail"] == "signing_unavailable"
    assert world.revoked() == [] and len(_pick_keys(world)) == 1
    assert _pick_keys(world)[0].endswith(pick)


def test_the_bff_mirror_of_the_zoho_accounts_hosts_is_the_python_list() -> None:
    """P3 of the review. The start relay sends the browser only to a Zoho
    accounts host. Its list mirrors ``auth.ACCOUNTS_HOSTS``, the one list."""
    import re
    from pathlib import Path

    from gateway.crm_sources.zoho import auth as zoho_auth

    ts = (Path(__file__).resolve().parents[2]
          / "workbench/control_plane/src/lib/zohoAccountsHosts.ts").read_text(encoding="utf-8")
    body = ts.split("ZOHO_ACCOUNTS_HOSTS", 1)[1]
    hosts = re.findall(r'"([a-z0-9.-]+)"', body)
    assert sorted(hosts) == sorted(zoho_auth.ACCOUNTS_HOSTS)
    assert len(hosts) == len(set(hosts))
