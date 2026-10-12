"""R8 — the Zoho connection of an organization, against a real Postgres (WS-53 CRM-Z2).

Migration 245 adds ``integration_connections`` and ``crm_settings.connection_id``.
``connections/store.py`` is the only reader of the blob, and
``routes/crm/connection.py`` is the route layer. Spec:
``project-docs/specs/crm_platform.md`` §5.3, §5.4 and §13 (the CRM-Z2 row).

What this proves against a REAL Postgres, through the real ``/crm`` router, as
the NON-privileged role of the phase-4 rehearsal (no superuser, no BYPASSRLS),
on a database with the whole ladder and two organizations:

- two organizations each hold a Zoho connection, and each reads only its own,
  through the route and through the store;
- the blob is ciphertext in the table and round-trips through the one key seam;
- the callback writes the connection, ``crm_settings.mode = 'mirror'`` and
  ``connection_id`` in ONE transaction, and ``PATCH /crm/settings`` still
  refuses ``mirror``;
- a reconnect updates the one row (the unique index) and revokes the old token;
- a disconnect deletes the blob and keeps the mode;
- a refresh failure persists ``needs_reconnect`` (the mark survives the raise);
- a Native org gets 409 and Zoho gets no call;
- the table forces row level security, and migration 245 replays with no change.

Hermetic fakes agree with any SQL they are handed (``ON CONFLICT`` inference on
an expression index, ``FOR UPDATE``, the RLS policy), so none of this is proved
without the database. ``tests/unit/test_crm_connection.py`` holds the same rules
with no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_connection import FakeZoho
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

ADMIN_A = "admin-a@crm-z2-r8.test"
ADMIN_B = "admin-b@crm-z2-r8.test"


def _connections_migration() -> Path:
    """The migration that creates the table, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "CREATE TABLE IF NOT EXISTS integration_connections"
        in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _assert_non_priv(app_eng: Any) -> None:
    with app_eng.connect() as c:
        role = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1], (
        "this suite connects as a SUPERUSER/BYPASSRLS role, so RLS is bypassed"
    )


def _admin(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


class Zoho:
    """One fake Zoho for one data centre, with tokens of its own."""

    def __init__(self, tag: str, host: str, api: str) -> None:
        self.tag, self.host = tag, host
        self.fake = FakeZoho(token_body={
            "access_token": f"ACCESS-{tag}", "refresh_token": f"REFRESH-{tag}",
            "expires_in": 3600, "api_domain": api,
        })

    def params(self, state: str, location: str) -> dict[str, str]:
        return {"code": f"CODE-{self.tag}", "state": state, "location": location,
                "accounts-server": f"https://{self.host}"}


ZOHO_A = Zoho("A", "accounts.zoho.in", "https://www.zohoapis.in")
ZOHO_B = Zoho("B", "accounts.zoho.eu", "https://www.zohoapis.eu")


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog, the switches on, the key set, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    for name, value in {
        "crm_mirror": True,
        "crm_mirror_orgs": "*",
        "crm_zoho_client_id": "1000.R8CLIENT",
        "crm_zoho_client_secret": "r8-client-secret",
        "gateway_session_secret": "r8-session-secret-not-a-default-0123456789",
        "acb_master_key": "r8-master-key-for-the-blob",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.metorite.com")
    monkeypatch.setattr(key_store, "_store", None)

    from gateway.crm_sources.zoho import ZohoSource
    from gateway.routes.crm import connection as conn

    active: dict[str, Any] = {"zoho": ZOHO_A}
    promoted.active = active
    monkeypatch.setattr(
        conn, "_source",
        lambda credential=None, transport=None: ZohoSource(
            conn.zoho_oauth_config(), credential,
            transport=active["zoho"].fake.transport,
        ),
    )
    for z in (ZOHO_A, ZOHO_B):
        z.fake.requests.clear()
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM crm_settings WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org})
            c.execute(text(
                "DELETE FROM integration_connections "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
    params: dict | None = None,
) -> Any:
    """One request through the real ``/crm`` router, as *user*.

    The override binds the tenant from the user, which is what
    ``_with_resolved_access`` does for a real request. The tenant never comes
    from the request.
    """
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://crm.test",
                follow_redirects=False,
            ) as client:
                return await client.request(method, path, json=body, params=params)
    finally:
        release_tenant(scope)


async def _connect(p: Any, org: str, email: str, zoho: Zoho, location: str) -> Any:
    """Start, then call back with the state from the consent URL."""
    from urllib.parse import parse_qs, urlsplit

    p.active["zoho"] = zoho
    user = _admin(email, org)
    started = await _call(p, user, "POST", "/crm/connection/zoho/start")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    return await _call(
        p, user, "GET", "/crm/oauth/zoho/callback",
        params=zoho.params(state, location),
    )


def _raw(p: Any, org: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id::text, status, connected_by, last_error, scopes, "
            "credentials_encrypted, provider_meta ->> 'location' AS location "
            "FROM integration_connections "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).all()


def _settings_row(p: Any, org: str) -> Any:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT mode, connection_id::text FROM crm_settings "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org}).first()


# ── Two orgs, each its own ───────────────────────────────────────────────────

async def test_two_orgs_each_hold_a_connection_and_read_only_their_own(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    a = await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")
    b = await _connect(ns, ns.org_b, ADMIN_B, ZOHO_B, "eu")
    assert a.status_code == 302 and a.headers["location"] == "/crm?connected=zoho", a.text
    assert b.status_code == 302 and b.headers["location"] == "/crm?connected=zoho", b.text

    seen_a = (await _call(ns, _admin(ADMIN_A, ns.org_a), "GET", "/crm/connection")).json()
    seen_b = (await _call(ns, _admin(ADMIN_B, ns.org_b), "GET", "/crm/connection")).json()
    assert (seen_a["connected_by"], seen_a["location"], seen_a["status"]) == (ADMIN_A, "in", "connected")
    assert (seen_b["connected_by"], seen_b["location"], seen_b["status"]) == (ADMIN_B, "eu", "connected")
    assert seen_a["scopes"] and all(s.endswith(".READ") for s in seen_a["scopes"])
    for body in (seen_a, seen_b):
        assert "credentials" not in str(body).lower()
        assert not any("ACCESS-" in str(v) or "REFRESH-" in str(v) for v in body.values())

    # A member of org A cannot read org B's connection through the store either.
    from acb_common.db import tenant_session
    from gateway.connections import store

    async with tenant_engine_scope(_app_dsn(ns)):
        async with tenant_session(ns.org_a) as db:
            own = await store.load_credential(db, ns.org_a, "zoho", "crm")
            foreign = await store.load_credential(db, ns.org_b, "zoho", "crm")
            foreign_row = await store.get(db, ns.org_b, "zoho", "crm")
        async with tenant_session(ns.org_b) as db:
            own_b = await store.load_credential(db, ns.org_b, "zoho", "crm")
    assert own is not None and own.refresh_token == "REFRESH-A"
    assert own_b is not None and own_b.refresh_token == "REFRESH-B"
    assert foreign is None and foreign_row is None


async def test_the_blob_is_ciphertext_at_rest(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    (row,) = _raw(ns, ns.org_a)
    assert row.credentials_encrypted
    for plain in ("REFRESH-A", "ACCESS-A", "refresh_token", "accounts.zoho.in"):
        assert plain not in row.credentials_encrypted


async def test_the_callback_sets_mirror_and_the_pointer_and_patch_still_refuses_mirror(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert _settings_row(ns, ns.org_a) is None
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    (row,) = _raw(ns, ns.org_a)
    mode, pointer = _settings_row(ns, ns.org_a)
    assert (mode, pointer) == ("mirror", row.id)
    # The org that has not connected cannot take the mode through PATCH.
    refused = await _call(ns, _admin(ADMIN_B, ns.org_b), "PATCH", "/crm/settings",
                          {"mode": "mirror"})
    assert refused.status_code == 422, refused.text
    assert _settings_row(ns, ns.org_b) is None


async def test_a_native_org_is_refused_and_zoho_gets_no_call(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    admin = _admin(ADMIN_A, ns.org_a)
    assert (await _call(ns, admin, "PATCH", "/crm/settings",
                        {"mode": "native"})).status_code == 200
    ZOHO_A.fake.requests.clear()
    started = await _call(ns, admin, "POST", "/crm/connection/zoho/start")
    assert started.status_code == 409, started.text
    assert _raw(ns, ns.org_a) == []
    assert ZOHO_A.fake.requests == []


async def test_a_state_of_org_b_does_not_connect_org_a(ns, app_engine) -> None:  # noqa: F811
    from urllib.parse import parse_qs, urlsplit

    _assert_non_priv(app_engine)
    ns.active["zoho"] = ZOHO_A
    started = await _call(ns, _admin(ADMIN_B, ns.org_b), "POST", "/crm/connection/zoho/start")
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    ZOHO_A.fake.requests.clear()
    response = await _call(ns, _admin(ADMIN_A, ns.org_a), "GET",
                           "/crm/oauth/zoho/callback",
                           params=ZOHO_A.params(state, "in"))
    assert response.status_code == 400
    assert ZOHO_A.fake.requests == []
    assert _raw(ns, ns.org_a) == [] and _raw(ns, ns.org_b) == []


# ── Reconnect and disconnect ─────────────────────────────────────────────────

async def test_a_reconnect_updates_the_one_row_and_revokes_the_old_token(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    (first,) = _raw(ns, ns.org_a)
    ZOHO_A.fake.requests.clear()
    ZOHO_A.fake.token_body = {**ZOHO_A.fake.token_body, "refresh_token": "REFRESH-A2"}
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    (second,) = _raw(ns, ns.org_a)
    assert second.id == first.id, "a reconnect must update the row and add none"
    revokes = [r for r in ZOHO_A.fake.requests if r.url.path.endswith("/token/revoke")]
    assert len(revokes) == 1 and b"REFRESH-A" in revokes[0].content
    assert b"REFRESH-A2" not in revokes[0].content
    assert revokes[0].url.query == b""
    ZOHO_A.fake.token_body["refresh_token"] = "REFRESH-A"


async def test_disconnect_deletes_the_blob_and_keeps_the_mode(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    ZOHO_A.fake.requests.clear()
    gone = await _call(ns, _admin(ADMIN_A, ns.org_a), "POST", "/crm/connection/disconnect")
    assert gone.status_code == 200, gone.text
    assert gone.json()["status"] == "disconnected"
    (row,) = _raw(ns, ns.org_a)
    assert row.status == "disconnected" and row.credentials_encrypted is None
    assert _settings_row(ns, ns.org_a)[0] == "mirror"
    assert sum(r.url.path.endswith("/token/revoke") for r in ZOHO_A.fake.requests) == 1
    # Org B is untouched by org A's disconnect.
    assert _raw(ns, ns.org_b) == []


def _spoil_the_blob(p: Any, org: str) -> None:
    """A blob that the key does not open: a rotated master key, or damage."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET credentials_encrypted = 'bm90LWEtYmxvYg==' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def test_a_blob_that_does_not_open_blocks_neither_reconnect_nor_disconnect(ns, app_engine) -> None:  # noqa: F811
    """CRM-Z2 review P1: a new consent and a disconnect are the two repairs."""
    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    _spoil_the_blob(ns, ns.org_a)
    ZOHO_A.fake.requests.clear()
    again = await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")
    assert again.status_code == 302, again.text
    assert not any(r.url.path.endswith("/token/revoke") for r in ZOHO_A.fake.requests)
    (row,) = _raw(ns, ns.org_a)
    assert row.status == "connected" and row.credentials_encrypted != "bm90LWEtYmxvYg=="

    _spoil_the_blob(ns, ns.org_a)
    ZOHO_A.fake.requests.clear()
    gone = await _call(ns, _admin(ADMIN_A, ns.org_a), "POST", "/crm/connection/disconnect")
    assert gone.status_code == 200, gone.text
    (row,) = _raw(ns, ns.org_a)
    assert row.status == "disconnected" and row.credentials_encrypted is None
    assert ZOHO_A.fake.requests == []


async def test_a_blob_that_does_not_open_stops_the_shared_token(ns, app_engine) -> None:  # noqa: F811
    from gateway.crm_sources.base import NeedsReconnect
    from gateway.routes.crm import connection as conn

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    _spoil_the_blob(ns, ns.org_a)
    ZOHO_A.fake.requests.clear()
    async with tenant_engine_scope(_app_dsn(ns)):
        with pytest.raises(NeedsReconnect):
            await conn.fresh_zoho_credential(ns.org_a, transport=ZOHO_A.fake.transport)
    (row,) = _raw(ns, ns.org_a)
    assert (row.status, row.last_error) == ("needs_reconnect", "undecryptable")
    assert ZOHO_A.fake.requests == []


async def test_a_failed_save_revokes_nothing(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """CRM-Z2 review P2: the old token is revoked only after the commit."""
    from fastapi import HTTPException
    from gateway.routes.crm import connection as conn

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    (before,) = _raw(ns, ns.org_a)

    async def _refused(*_a: Any, **_k: Any) -> None:
        raise HTTPException(status_code=409, detail="mode_is_native")

    monkeypatch.setattr(conn, "_adopt_mirror", _refused)
    ZOHO_A.fake.requests.clear()
    ZOHO_A.fake.token_body = {**ZOHO_A.fake.token_body, "refresh_token": "REFRESH-A2"}
    try:
        refused = await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")
    finally:
        ZOHO_A.fake.token_body["refresh_token"] = "REFRESH-A"
    assert refused.status_code == 409, refused.text
    assert not any(r.url.path.endswith("/token/revoke") for r in ZOHO_A.fake.requests)
    (after,) = _raw(ns, ns.org_a)
    assert after.credentials_encrypted == before.credentials_encrypted, "the save rolled back"


class _NewTokenEachTime(Zoho):
    """Zoho A, with a new refresh token on each code exchange."""

    def __init__(self) -> None:
        import httpx

        super().__init__("A", "accounts.zoho.in", "https://www.zohoapis.in")
        self.issued = 0
        self.fake.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: Any) -> Any:
        import httpx

        self.fake.requests.append(request)
        if request.url.path.endswith("/token/revoke"):
            return httpx.Response(200, json={"status": "success"})
        if request.url.path.endswith("/org"):  # CRM-X0: each callback reads it
            return httpx.Response(200, json={"org": [{"id": "Z-A", "domain_name": "a"}]})
        self.issued += 1
        return httpx.Response(200, json={
            "access_token": f"ACCESS-R{self.issued}",
            "refresh_token": f"REFRESH-R{self.issued}",
            "expires_in": 3600, "api_domain": "https://www.zohoapis.in",
        })


async def test_two_first_connects_at_one_time_revoke_the_replaced_token(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """CRM-Z2 re-review P2. With no row yet, ``FOR UPDATE`` locks nothing. The
    advisory lock makes the second callback wait, then read the first token."""
    import asyncio
    from urllib.parse import parse_qs, urlsplit

    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.connections import store
    from gateway.routes import crm as crm_package

    _assert_non_priv(app_engine)
    zoho = _NewTokenEachTime()
    ns.active["zoho"] = zoho
    user = _admin(ADMIN_A, ns.org_a)
    real_save = store.save_credential

    async def _slow_save(*a: Any, **k: Any) -> Any:
        await asyncio.sleep(0.3)  # both callbacks are past the read now
        return await real_save(*a, **k)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(ns)), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            follow_redirects=False,
        ) as client:
            states = []
            for _ in range(2):
                started = await client.post("/crm/connection/zoho/start")
                states.append(parse_qs(urlsplit(started.json()["url"]).query)["state"][0])
            monkeypatch.setattr(store, "save_credential", _slow_save)
            done = await asyncio.gather(*(
                client.get("/crm/oauth/zoho/callback", params=zoho.params(s, "in"))
                for s in states))
    finally:
        release_tenant(scope)
    assert [r.status_code for r in done] == [302, 302], [r.text for r in done]
    revokes = [r for r in zoho.fake.requests if r.url.path.endswith("/token/revoke")]
    assert len(revokes) == 1, "the token that the second save replaced is revoked"
    (row,) = _raw(ns, ns.org_a)
    assert row.status == "connected"


async def test_a_missing_master_key_fails_loud_and_marks_nothing(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """CRM-Z2 re-review P1. An operator fault is not a dead token."""
    from gateway.connections import store
    from gateway.routes.crm import connection as conn

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    await _age_the_token(ns, ns.org_a)

    def _no_key() -> Any:
        raise RuntimeError("ACB_MASTER_KEY is not set")

    monkeypatch.setattr(store, "get_key_store", _no_key)
    ZOHO_A.fake.requests.clear()
    async with tenant_engine_scope(_app_dsn(ns)):
        with pytest.raises(RuntimeError):
            await conn.fresh_zoho_credential(ns.org_a, transport=ZOHO_A.fake.transport)
    (row,) = _raw(ns, ns.org_a)
    assert (row.status, row.last_error) == ("connected", None)
    assert ZOHO_A.fake.requests == []


async def test_a_patch_that_read_a_stale_mode_writes_nothing(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """CRM-Z2 review P2: the callback set 'mirror' after the PATCH read the mode."""
    from gateway.routes.crm import settings as crm_settings

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302

    async def _stale(*_a: Any, **_k: Any) -> None:
        return None  # what the PATCH read before the callback committed

    monkeypatch.setattr(crm_settings, "read_settings", _stale)
    for body in ({"access": "everyone"}, {"mode": "native"}):
        refused = await _call(ns, _admin(ADMIN_A, ns.org_a), "PATCH", "/crm/settings", body)
        assert refused.status_code == 409, (body, refused.text)
    (row,) = _raw(ns, ns.org_a)
    assert _settings_row(ns, ns.org_a) == ("mirror", row.id)


# ── The shared token and a failed refresh ────────────────────────────────────

async def _age_the_token(p: Any, org: str) -> None:
    """Rewrite the stored expiry to the past, through the store's own seal."""
    from dataclasses import replace

    from acb_common.db import tenant_session
    from gateway.connections import store

    async with tenant_engine_scope(_app_dsn(p)), tenant_session(org) as db:
        cred = await store.load_credential(db, org, "zoho", "crm")
        row = await store.get(db, org, "zoho", "crm")
        old = replace(cred, expires_at=datetime.now(UTC) - timedelta(hours=1))
        await store.update_tokens(db, org, row.id, old)


async def test_a_due_token_is_refreshed_and_stored_for_every_worker(ns, app_engine) -> None:  # noqa: F811
    from gateway.crm_sources.zoho import auth
    from gateway.routes.crm import connection as conn

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    (before,) = _raw(ns, ns.org_a)
    await _age_the_token(ns, ns.org_a)
    refresher = FakeZoho(token_body={"access_token": "ACCESS-NEW", "expires_in": 3600})
    async with tenant_engine_scope(_app_dsn(ns)):
        fresh = await conn.fresh_zoho_credential(ns.org_a, transport=refresher.transport)
        again = await conn.fresh_zoho_credential(ns.org_a, transport=refresher.transport)
    assert fresh.access_token == "ACCESS-NEW" and again.access_token == "ACCESS-NEW"
    assert len(refresher.requests) == 1, "the second worker must read the stored token"
    assert not auth.needs_refresh(again, datetime.now(UTC))
    (after,) = _raw(ns, ns.org_a)
    assert after.credentials_encrypted != before.credentials_encrypted


async def test_a_refresh_failure_persists_needs_reconnect_and_stops(ns, app_engine) -> None:  # noqa: F811
    from gateway.crm_sources.base import NeedsReconnect
    from gateway.routes.crm import connection as conn

    _assert_non_priv(app_engine)
    assert (await _connect(ns, ns.org_a, ADMIN_A, ZOHO_A, "in")).status_code == 302
    await _age_the_token(ns, ns.org_a)
    dead = FakeZoho(token_body={"error": "invalid_code"})
    async with tenant_engine_scope(_app_dsn(ns)):
        with pytest.raises(NeedsReconnect):
            await conn.fresh_zoho_credential(ns.org_a, transport=dead.transport)
        with pytest.raises(NeedsReconnect):
            await conn.fresh_zoho_credential(ns.org_a, transport=dead.transport)
    assert len(dead.requests) == 1, "a dead token is never retried"
    (row,) = _raw(ns, ns.org_a)
    assert (row.status, row.last_error) == ("needs_reconnect", "invalid_code")
    seen = (await _call(ns, _admin(ADMIN_A, ns.org_a), "GET", "/crm/connection")).json()
    assert (seen["status"], seen["last_error"], seen["connected"]) == (
        "needs_reconnect", "invalid_code", False)


# ── The table itself ─────────────────────────────────────────────────────────

def test_the_table_refuses_another_tenants_row(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": ns.org_a})
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO integration_connections (organization_id, provider, "
                "owning_app) VALUES (CAST(:o AS uuid), 'zoho', 'crm')"), {"o": ns.org_b})
        trans.rollback()


def test_an_unbound_session_reads_no_connection(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app) "
            "VALUES (CAST(:o AS uuid), 'zoho', 'crm')"), {"o": ns.org_a})
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM integration_connections")).scalar() == 0


def test_one_live_connection_for_each_key_and_the_default_is_the_tenant(ns, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    with app_engine.connect() as c:
        trans = c.begin()
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": ns.org_a})
        c.execute(text(
            "INSERT INTO integration_connections (provider, owning_app) "
            "VALUES ('zoho', 'crm')"))
        stored = c.execute(text(
            "SELECT organization_id::text FROM integration_connections")).scalars().all()
        assert stored == [ns.org_a]
        sp = c.begin_nested()
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO integration_connections (provider, owning_app) "
                "VALUES ('zoho', 'crm')"))
        sp.rollback()
        # A member connection of the same provider is a different key.
        c.execute(text(
            "INSERT INTO integration_connections (provider, owning_app, scope_kind, "
            "member_email) VALUES ('zoho', 'crm', 'member', 'a@x.test')"))
        sp = c.begin_nested()
        with pytest.raises(DBAPIError):
            c.execute(text(
                "INSERT INTO integration_connections (provider, owning_app, "
                "scope_kind, member_email) VALUES ('zoho', 'crm', 'org', 'a@x.test')"))
        sp.rollback()
        trans.rollback()


def test_the_migration_replays_with_no_change(ns) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, "
            "owning_app, status, last_error) VALUES (CAST(:o AS uuid), 'zoho', 'crm', "
            "'needs_reconnect', 'invalid_code')"), {"o": ns.org_a})
    sql = _connections_migration().read_text(encoding="utf-8")
    with ns.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    with ns.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT status, last_error FROM integration_connections "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a}).first()
        policies = c.execute(text(
            "SELECT count(*) FROM pg_policies WHERE tablename = 'integration_connections'"),
        ).scalar()
        forced = c.execute(text(
            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
            "WHERE oid = 'integration_connections'::regclass")).scalar()
        pointer = c.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'crm_settings' AND column_name = 'connection_id'")).scalar()
    assert tuple(row) == ("needs_reconnect", "invalid_code")
    assert policies == 1
    assert forced is True
    assert pointer == 1
