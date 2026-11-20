"""R8: the Zoho Books connect flow against a real Postgres (API-2, AC8).

Spec: ``project-docs/specs/api_connections.md`` §2.2 and §2.11. The hermetic
twin is ``test_api_connect_zoho_books.py``. This file runs the real store SQL,
under FORCE ROW LEVEL SECURITY, as the NON-privileged role of the phase-4
rehearsal (no superuser, no BYPASSRLS), through the real routers.

What it proves, with the database:

- a connect writes ONE ``api:zoho_books`` row: owning app ``integrations``,
  scope ``org``, status ``connected``, public facts only in ``provider_meta``,
  and ``grant = admin``. The blob is ciphertext;
- a member of org B cannot see org A's row, through the route or the store,
  and cannot pick org A's pending consent;
- a reconnect updates the one row and revokes the old token;
- a failed test read leaves the older row exactly as it was;
- the console disconnect of the ``api:zoho_books:<uuid>`` row revokes at Zoho
  and drops the blob, and org B gets 404 for that id.

Mutation: change ``_KEY_WHERE`` of ``store.get`` to drop the org predicate and
run the RLS off, or replace ``tenant_session(org)`` in the routes with a
session of no org. ``test_org_b_cannot_see_or_pick_org_a`` fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.integrations_api_connect", reason="gateway not installed")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_api_connect_zoho_books import (
    S_ACCESS,
    S_CODE,
    S_REFRESH,
    S_SECRET,
    Books,
)
from tests.unit.test_crm_connection import FakeZoho
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

ADMIN_A = "admin-a@api2-r8.test"
ADMIN_B = "admin-b@api2-r8.test"
S_REFRESH_2 = "SENTINEL-API2-R8-REFRESH-2-77aa"


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed")


def _admin(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations", "integrations:manage",
                             "admin:members:read"]),
        organization_id=org,
    )


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    """The promoted catalog, the switch on, the fakes in, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings
    from acb_common.tenant_redis import TenantRedis
    from gateway.crm_sources.zoho import ZohoSource
    from gateway.routes import integrations_api_connect as routes
    from gateway.routes.crm import connection as crm_connection

    from tests.unit._mcp_oauth_fakes import FakeRawRedis

    settings = get_settings()
    for name, value in {
        "api_connections_orgs": "*",
        "crm_zoho_client_id": "1000.API2R8CLIENT",
        "crm_zoho_client_secret": S_SECRET,
        "gateway_session_secret": "api2-r8-session-secret-not-a-default-0123456789",
        "acb_master_key": "api2-r8-master-key-for-the-blob",
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
    promoted.zoho, promoted.books, promoted.redis = zoho, books, raw
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM integration_connections WHERE organization_id = CAST(:o AS uuid) "
                "AND provider = 'api:zoho_books'"), {"o": org})


async def _call(p: Any, user: Any, method: str, path: str, *,
                params: dict | None = None, body: dict | None = None) -> Any:
    """One request through the real routers, as *user*. The tenant comes from
    the user, never from the request."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import integrations_api_connect as routes
    from gateway.routes import integrations_connections

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(integrations_connections.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test",
        ) as client:
            return await client.request(method, path, params=params, json=body)
    finally:
        release_tenant(scope)


async def _connect(p: Any, org: str, email: str) -> Any:
    user = _admin(email, org)
    started = await _call(p, user, "POST", "/integrations/api/zoho_books/start")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    return await _call(p, user, "GET", "/integrations/api/zoho/callback", params={
        "code": S_CODE, "state": state, "location": "in",
        "accounts-server": "https://accounts.zoho.in"})


def _rows(p: Any, org: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id::text, provider, owning_app, scope_kind, member_email, status, "
            "provider_meta, scopes, connected_by, credentials_encrypted, updated_at "
            "FROM integration_connections WHERE organization_id = CAST(:o AS uuid) "
            "AND provider = 'api:zoho_books'"), {"o": org}).all()


def _revoked(p: Any) -> list[str]:
    return [parse_qs(r.content.decode()).get("token", [""])[0]
            for r in p.zoho.requests if r.url.path.endswith("/token/revoke")]


async def test_a_connect_writes_one_admin_row_with_public_facts_only(ns, app_engine) -> None:  # noqa: F811
    from gateway.connections.api import connect

    _assert_non_priv(app_engine)
    res = await _connect(ns, ns.org_a, ADMIN_A)
    assert res.status_code == 302 and res.headers["location"] == "/integrations?api_connected=zoho_books", res.text
    (row,) = _rows(ns, ns.org_a)
    assert (row.provider, row.owning_app, row.scope_kind, row.member_email, row.status) == (
        "api:zoho_books", "integrations", "org", None, "connected")
    assert row.connected_by == ADMIN_A
    assert tuple(row.scopes) == connect.ZOHO_BOOKS_SCOPES
    assert set(row.provider_meta) == set(connect.PUBLIC_META_KEYS)
    assert row.provider_meta["grant"] == "admin"
    assert row.provider_meta["organization_id"] == "60001"
    assert row.credentials_encrypted
    for plain in (S_ACCESS, S_REFRESH, S_CODE, S_SECRET):
        assert plain not in str(row.provider_meta)
        assert plain not in row.credentials_encrypted
    status = await _call(ns, _admin(ADMIN_A, ns.org_a), "GET", "/integrations/api/zoho_books")
    assert status.json()["connected"] is True and status.json()["organization_id"] == "60001"


async def test_org_b_cannot_see_or_pick_org_a(ns, app_engine) -> None:  # noqa: F811
    from acb_common.db import tenant_session
    from gateway.connections import store

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A)).status_code == 302
    seen_b = await _call(ns, _admin(ADMIN_B, ns.org_b), "GET", "/integrations/api/zoho_books")
    assert seen_b.status_code == 200 and seen_b.json()["connected"] is False
    async with tenant_engine_scope(_app_dsn(ns)):
        async with tenant_session(ns.org_b) as db:
            foreign = await store.get(db, ns.org_a, "api:zoho_books", "integrations")
            blob = await store.load_credential(db, ns.org_a, "api:zoho_books", "integrations")
        async with tenant_session(ns.org_a) as db:
            own = await store.load_credential(db, ns.org_a, "api:zoho_books", "integrations")
    assert foreign is None and blob is None
    assert own is not None and own.refresh_token == S_REFRESH

    # A pending pick of org A: org B gets nothing, and org A's entry stays.
    ns.books.organizations = [{"organization_id": "60001", "name": "A1"},
                              {"organization_id": "60002", "name": "A2"}]
    pending = await _connect(ns, ns.org_a, ADMIN_A)
    pick = pending.headers["location"].split("api_pick=", 1)[1]
    peek_b = await _call(ns, _admin(ADMIN_A, ns.org_b), "GET", "/integrations/api/zoho_books",
                         params={"pick": pick})
    assert peek_b.json()["choices"] == []
    taken_b = await _call(ns, _admin(ADMIN_A, ns.org_b), "POST",
                          "/integrations/api/zoho_books/pick",
                          body={"pick": pick, "zoho_organization_id": "60002"})
    assert taken_b.status_code == 400 and taken_b.json()["detail"] == "invalid_pick"
    assert _rows(ns, ns.org_b) == []
    done = await _call(ns, _admin(ADMIN_A, ns.org_a), "POST", "/integrations/api/zoho_books/pick",
                       body={"pick": pick, "zoho_organization_id": "60002"})
    assert done.status_code == 200, done.text
    (row,) = _rows(ns, ns.org_a)
    assert row.provider_meta["organization_id"] == "60002"


async def test_a_reconnect_updates_the_one_row_and_revokes_the_old_token(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A)).status_code == 302
    (first,) = _rows(ns, ns.org_a)
    ns.zoho.requests.clear()
    ns.zoho.token_body = {"access_token": S_ACCESS, "refresh_token": S_REFRESH_2,
                          "expires_in": 3600, "api_domain": "https://www.zohoapis.in"}
    assert (await _connect(ns, ns.org_a, ADMIN_A)).status_code == 302
    (second,) = _rows(ns, ns.org_a)
    assert second.id == first.id, "a reconnect must update the row and add none"
    assert second.credentials_encrypted != first.credentials_encrypted
    assert _revoked(ns) == [S_REFRESH]


async def test_a_failed_test_read_leaves_the_older_row_unchanged(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A)).status_code == 302
    (before,) = _rows(ns, ns.org_a)
    ns.zoho.requests.clear()
    ns.zoho.token_body = {"access_token": S_ACCESS, "refresh_token": S_REFRESH_2,
                          "expires_in": 3600, "api_domain": "https://www.zohoapis.in"}
    ns.books.invoice_status = 403
    res = await _connect(ns, ns.org_a, ADMIN_A)
    assert res.headers["location"] == "/integrations?api_connect_error=test_read_failed"
    (after,) = _rows(ns, ns.org_a)
    assert after == before, "a failed test read must not touch the older row"
    assert _revoked(ns) == [S_REFRESH_2]


async def test_the_console_disconnects_the_row_and_revokes(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A)).status_code == 302
    (row,) = _rows(ns, ns.org_a)
    row_id = f"api:zoho_books:{row.id}"
    ns.zoho.requests.clear()
    foreign = await _call(ns, _admin(ADMIN_B, ns.org_b), "POST",
                          f"/integrations/connections/{row_id}/disconnect")
    assert foreign.status_code == 404, foreign.text
    assert _revoked(ns) == []
    listed = await _call(ns, _admin(ADMIN_A, ns.org_a), "GET", "/integrations/connections")
    assert row_id in {c["id"] for c in listed.json()["connections"]}
    gone = await _call(ns, _admin(ADMIN_A, ns.org_a), "POST",
                       f"/integrations/connections/{row_id}/disconnect")
    assert gone.status_code == 200, gone.text
    assert gone.json()["status"] == "disconnected"
    (after,) = _rows(ns, ns.org_a)
    assert after.status == "disconnected" and after.credentials_encrypted is None
    assert _revoked(ns) == [S_REFRESH]
    again = await _call(ns, _admin(ADMIN_A, ns.org_a), "POST",
                        f"/integrations/connections/{row_id}/disconnect")
    assert again.status_code == 200 and _revoked(ns) == [S_REFRESH]


def test_the_row_needs_no_new_migration() -> None:
    """The row fits migration 245: no CHECK names a provider or an app."""
    found = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
             if p.name != "schema.generated.sql"
             and "CREATE TABLE IF NOT EXISTS integration_connections" in p.read_text(encoding="utf-8")]
    assert len(found) == 1
    sql = found[0].read_text(encoding="utf-8")
    assert "provider IN" not in sql and "owning_app IN" not in sql
