"""The Zoho connection of an organization (WS-53 CRM-Z2).

Spec: ``project-docs/specs/crm_platform.md`` §5.2 items 1, 3, 4, 8, 11, 12, §5.3,
§5.4 and §13 (the CRM-Z2 row). Hermetic: Zoho is an ``httpx.MockTransport`` (no
network), the store is an in-memory fake, and the ``/crm`` router is mounted on a
bare FastAPI app. ``tests/unit/test_crm_connection_r8.py`` runs the real SQL.

What this file pins:

1. A forged, expired, wrong-purpose, wrong-org or wrong-member state is refused
   with 400 and NO call to Zoho.
2. A refresh failure sets ``needs_reconnect`` and is never retried.
3. A sentinel token and a sentinel secret never reach a response or a log line.
4. ``CRM_MIRROR`` off gives 404 on each new route.
5. The fences (R7): one module names the blob column, one module reads the
   mirror switch, one module reads the client settings.
"""

from __future__ import annotations

import ast
import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

import structlog
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.connections import shared_token, store
from gateway.crm_sources.base import NeedsReconnect, RateLimited, SourceCredential
from gateway.crm_sources.zoho import ZohoSource, auth
from gateway.routes import crm as crm_package
from gateway.routes.crm import connection as conn
from gateway.routes.email.transport import signing

from tests.unit._crm_fakes import crm_switch_on

ORG_A = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001"
ORG_B = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002"
ADMIN = "admin@zoho-z2.test"
OTHER_ADMIN = "other@zoho-z2.test"

S_ACCESS = "SENTINEL-ACCESS-9f3a61"
S_REFRESH = "SENTINEL-REFRESH-7c1d42"
S_NEW_ACCESS = "SENTINEL-NEWACCESS-4b8e07"
S_OLD_REFRESH = "SENTINEL-OLDREFRESH-2d6f19"
S_CODE = "SENTINEL-CODE-5e2b80"
S_SECRET = "SENTINEL-CLIENTSECRET-1a2b3c"
SENTINELS = (S_ACCESS, S_REFRESH, S_NEW_ACCESS, S_OLD_REFRESH, S_CODE, S_SECRET)

_REPO = Path(__file__).resolve().parents[2]
_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeZoho:
    """Zoho as an ``httpx.MockTransport``. It records each request."""

    def __init__(self, *, token_body: dict[str, Any] | None = None,
                 revoke_body: dict[str, Any] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.token_body = token_body
        self.revoke_body = revoke_body or {"status": "success"}
        self.transport = httpx.MockTransport(self._handle)

    #: The account that ``GET org`` names (CRM-X0: each callback reads it).
    org_id = "Z-TEST"

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/org"):
            return httpx.Response(200, json={"org": [{
                "id": self.org_id, "domain_name": f"dom-{self.org_id}"}]})
        if request.url.path.endswith("/token/revoke"):
            return httpx.Response(200, json=self.revoke_body)
        if self.token_body is not None:
            return httpx.Response(200, json=self.token_body)
        form = parse_qs(request.content.decode())
        if form.get("grant_type") == ["refresh_token"]:
            return httpx.Response(200, json={
                "access_token": S_NEW_ACCESS, "expires_in": 3600,
                "api_domain": "https://www.zohoapis.in",
            })
        return httpx.Response(200, json={
            "access_token": S_ACCESS, "refresh_token": S_REFRESH,
            "expires_in": 3600, "api_domain": "https://www.zohoapis.in",
        })

    def forms(self) -> list[dict[str, list[str]]]:
        return [parse_qs(r.content.decode()) for r in self.requests]


class FakeRegistry:
    """The in-memory twin of ``integration_connections`` for one org at a time."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.modes: dict[str, str | None] = {}
        self.adopted: list[tuple[str, str]] = []

    def _view(self, row: dict[str, Any]) -> store.Connection:
        return store.Connection(
            id=row["id"], organization_id=row["org"], provider="zoho",
            owning_app="crm", scope_kind="org", member_email=None,
            status=row["status"], provider_meta=dict(
                row.get("meta") or (row["credential"].provider_meta if row["credential"] else {})),
            scopes=tuple(row["scopes"]), connected_by=row["connected_by"],
            last_sync_at=None, last_error=row["last_error"],
            created_at=_NOW, updated_at=_NOW,
        )

    async def get(self, db, org, provider, owning_app, **kw):
        row = self.rows.get(org)
        return self._view(row) if row else None

    async def load_credential(self, db, org, provider, owning_app, **kw):
        row = self.rows.get(org)
        if not row or row["status"] == "disconnected" or row["credential"] is None:
            return None
        return row["credential"]

    async def load_credential_locked(self, db, org, connection_id):
        row = self.rows.get(org)
        if not row or row["id"] != connection_id or row["status"] == "disconnected":
            return None
        return row["credential"]

    async def save_credential(self, db, org, provider, owning_app, credential, *,
                              connected_by, scopes=None, **kw):
        row = self.rows.setdefault(org, {"id": f"conn-{org[-4:]}"})
        row.update(org=org, status="connected", credential=credential,
                   meta=dict(credential.provider_meta),
                   scopes=list(scopes or credential.scopes),
                   connected_by=connected_by, last_error=None)
        return self._view(row)

    async def update_tokens(self, db, org, connection_id, credential):
        self.rows[org]["credential"] = credential

    async def mark_needs_reconnect(self, db, org, connection_id, code):
        self.rows[org].update(status="needs_reconnect",
                              last_error=store.safe_error_code(code))

    async def disconnect(self, db, org, connection_id):
        row = self.rows[org]
        row.update(status="disconnected", credential=None, last_error=None)
        return self._view(row)

    async def read_mode(self, db, org):
        return self.modes.get(org)

    async def known_account(self, db, org):
        """CRM-X0: the org knows no account and holds no Zoho record, so the
        callback goes on. ``test_crm_reset_r8.py`` holds the real reads."""
        return conn.KnownAccount()

    async def adopt_mirror(self, db, org, connection_id, email):
        if self.modes.get(org) == "native":
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail="mode_is_native")
        self.modes[org] = "mirror"
        self.adopted.append((org, connection_id))


class _LockOnly:
    """The fake store answers every query. Only the advisory lock reaches here."""

    async def execute(self, statement: Any, params: Any = None) -> None:
        assert "pg_advisory_xact_lock" in str(statement), str(statement)


@asynccontextmanager
async def _no_session(*_a: Any, **_k: Any):
    yield _LockOnly()


def _credential(*, refresh: str = S_REFRESH, access: str = S_ACCESS,
                expires: datetime | None = None) -> SourceCredential:
    return SourceCredential(
        access_token=access, refresh_token=refresh,
        expires_at=expires or _NOW + timedelta(hours=1),
        scopes=conn.ZOHO_READ_SCOPES,
        provider_meta={
            auth.META_ACCOUNTS_SERVER: "https://accounts.zoho.in",
            auth.META_API_DOMAIN: "https://www.zohoapis.in",
            auth.META_LOCATION: "in",
        },
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeRegistry, FakeZoho]:
    """The mirror switch on, the client set, the store and Zoho faked."""
    crm_switch_on(monkeypatch, "*")
    settings = get_settings()
    for name, value in {
        "crm_mirror": True,
        "crm_mirror_orgs": "*",
        "crm_zoho_client_id": "1000.CLIENTID",
        "crm_zoho_client_secret": S_SECRET,
        "gateway_session_secret": "z2-test-secret-not-a-default-0123456789",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.metorite.com")
    registry, zoho = FakeRegistry(), FakeZoho()
    for name in ("get", "load_credential", "load_credential_locked",
                 "save_credential", "update_tokens", "mark_needs_reconnect",
                 "disconnect"):
        monkeypatch.setattr(store, name, getattr(registry, name))
    monkeypatch.setattr(conn, "_read_mode", registry.read_mode)
    monkeypatch.setattr(conn, "_adopt_mirror", registry.adopt_mirror)
    monkeypatch.setattr(conn, "_known_account", registry.known_account)
    monkeypatch.setattr(conn, "_tenant_session", _no_session)
    # The router's access rule reads crm_settings. A fake keeps it off the DB.
    from types import SimpleNamespace

    from gateway.routes.crm import access as crm_access

    async def _everyone(db, org):
        return SimpleNamespace(access="everyone", access_groups=[])

    monkeypatch.setattr(crm_access, "_tenant_session", _no_session)
    monkeypatch.setattr(crm_access, "read_settings", _everyone)
    monkeypatch.setattr(shared_token, "tenant_session", _no_session)
    monkeypatch.setattr(
        conn, "_source",
        lambda credential=None, transport=None: ZohoSource(
            conn.zoho_oauth_config(), credential, transport=zoho.transport,
        ),
    )
    return registry, zoho


def _user(email: str = ADMIN, org: str = ORG_A, *perms: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(list(perms or ("feature:crm", "admin:access:manage"))),
        organization_id=org,
    )


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def _state(org: str = ORG_A, member: str = ADMIN, **kw: Any) -> str:
    return signing.sign_state(purpose=conn.STATE_PURPOSE, org=org, member=member, **kw)


def _callback(client: TestClient, state: str | None, **extra: str) -> httpx.Response:
    params = {"code": S_CODE, "accounts-server": "https://accounts.zoho.in",
              "location": "in", **extra}
    if state is not None:
        params["state"] = state
    return client.get("/crm/oauth/zoho/callback", params=params)


# ── 1. The state ─────────────────────────────────────────────────────────────


def _tampered() -> str:
    body, tag = _state().split(".")
    return f"{body}.{'A' if tag[0] != 'A' else 'B'}{tag[1:]}"


def _wrong_purpose() -> str:
    return signing.sign_state(purpose="crm.hubspot.connect", org=ORG_A, member=ADMIN)


def _email_state() -> str:
    return signing.sign_oauth_state(org=ORG_A, member=ADMIN, provider="microsoft")


def _expired() -> str:
    return _state(now=time.time() - 2 * signing.OAUTH_STATE_TTL_SECONDS)


@pytest.mark.parametrize(
    ("label", "make"),
    [
        ("tampered", _tampered),
        ("wrong purpose", _wrong_purpose),
        ("an email state", _email_state),
        ("expired", _expired),
        ("another org", lambda: _state(org=ORG_B)),
        ("another member", lambda: _state(member=OTHER_ADMIN)),
        ("garbage", lambda: "not-a-state"),
        ("empty", lambda: ""),
    ],
)
def test_a_bad_state_is_a_400_with_no_zoho_call(
    world: tuple[FakeRegistry, FakeZoho], label: str, make: Callable[[], str],
) -> None:
    registry, zoho = world
    response = _callback(_client(_user()), make() or None)
    assert response.status_code == 400, (label, response.text)
    assert response.json()["detail"] == "invalid_state"
    assert zoho.requests == [], label
    assert registry.rows == {}
    assert S_CODE not in response.text


def test_a_state_for_another_org_is_refused_for_the_session_of_this_org(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    """Org B's admin signs a valid state. It does not work in org A's session."""
    _registry, zoho = world
    stolen = _state(org=ORG_B, member=ADMIN)
    assert _callback(_client(_user(org=ORG_A)), stolen).status_code == 400
    assert zoho.requests == []


def test_a_good_state_connects_and_moves_the_org_to_mirror(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    response = _callback(_client(_user()), _state())
    assert response.status_code == 302
    assert response.headers["location"] == "/crm?connected=zoho"
    assert registry.rows[ORG_A]["status"] == "connected"
    assert registry.rows[ORG_A]["connected_by"] == ADMIN
    assert registry.modes[ORG_A] == "mirror"
    assert registry.adopted == [(ORG_A, registry.rows[ORG_A]["id"])]
    assert [r.url.path for r in zoho.requests] == ["/oauth/v2/token", "/crm/v8/org"]
    assert str(zoho.requests[0].url.host) == "accounts.zoho.in"


def test_the_callback_refuses_an_accounts_server_off_the_allowlist(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    _registry, zoho = world
    response = _callback(_client(_user()), _state(),
                         **{"accounts-server": "https://accounts.evil.example"})
    assert response.status_code == 400
    assert response.json()["detail"] == "untrusted_host"
    assert zoho.requests == []


def test_a_location_that_does_not_own_the_server_is_refused(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    _registry, zoho = world
    response = _callback(_client(_user()), _state(), location="eu")
    assert response.status_code == 400
    assert zoho.requests == []


def test_a_denied_consent_goes_back_with_a_code_and_calls_nothing(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    client = _client(_user())
    response = client.get("/crm/oauth/zoho/callback",
                          params={"state": _state(), "error": "access_denied"})
    assert response.status_code == 302
    assert response.headers["location"] == "/crm?connect_error=access_denied"
    assert zoho.requests == []
    assert registry.rows == {}


def test_a_native_org_is_refused_and_nothing_is_sent(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    registry.modes[ORG_A] = "native"
    response = _callback(_client(_user()), _state())
    assert response.status_code == 409
    assert zoho.requests == []
    start = _client(_user()).post("/crm/connection/zoho/start")
    assert start.status_code == 409


def test_a_member_without_the_admin_right_gets_403_on_each_write(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    _registry, zoho = world
    client = _client(_user(ADMIN, ORG_A, "feature:crm"))
    assert client.post("/crm/connection/zoho/start").status_code == 403
    assert client.post("/crm/connection/disconnect").status_code == 403
    assert _callback(client, _state()).status_code == 403
    assert zoho.requests == []
    # A member may read the state of the connection (no secret in it).
    assert client.get("/crm/connection").status_code == 200


def test_the_connection_read_carries_the_mode_for_a_plain_member(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    """CRM-Z7: each member needs the mode, and ``/crm/settings`` is admin-only."""
    registry, _zoho = world
    member = _client(_user(ADMIN, ORG_A, "feature:crm"))
    # Not chosen: no row, and the mode is null.
    assert member.get("/crm/connection").json()["mode"] is None
    registry.modes[ORG_A] = "native"
    assert member.get("/crm/connection").json()["mode"] == "native"
    registry.modes[ORG_A] = "mirror"
    assert member.get("/crm/connection").json()["mode"] == "mirror"


# ── Start ────────────────────────────────────────────────────────────────────


def test_start_returns_the_consent_url_with_read_scopes_and_a_state_that_verifies(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    _registry, zoho = world
    response = _client(_user()).post("/crm/connection/zoho/start")
    assert response.status_code == 200
    url = urlsplit(response.json()["url"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.zoho.com"
    assert query["redirect_uri"] == ["https://app.metorite.com/api/crm/oauth/zoho/callback"]
    assert query["access_type"] == ["offline"]
    scopes = query["scope"][0].split(",")
    assert scopes == list(conn.ZOHO_READ_SCOPES)
    assert all(s.endswith(".READ") for s in scopes)
    payload = signing.verify_state(query["state"][0], purpose=conn.STATE_PURPOSE)
    assert payload is not None
    assert (payload["org"], payload["member"]) == (ORG_A, ADMIN)
    assert signing.verify_oauth_state(query["state"][0]) is None
    assert zoho.requests == []
    assert S_SECRET not in response.text


def test_start_is_503_when_the_client_is_not_set(
    world: tuple[FakeRegistry, FakeZoho], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "crm_zoho_client_secret", "", raising=False)
    response = _client(_user()).post("/crm/connection/zoho/start")
    assert response.status_code == 503
    assert response.json()["detail"] == "zoho_not_configured"


def test_the_redirect_uri_is_the_one_in_the_owner_runbook() -> None:
    runbook = (_REPO / "docs" / "ZOHO_OAUTH_SETUP.md").read_text(encoding="utf-8")
    assert "https://app.metorite.com" + conn.REDIRECT_PATH in runbook
    # The /api/crm proxy forwards /api/crm/<x> to /crm/<x>.
    assert conn.REDIRECT_PATH == "/api/crm" + conn.CALLBACK_ROUTE
    paths = {r.path for r in crm_package.router.routes}
    assert "/crm" + conn.CALLBACK_ROUTE in paths


# ── Reconnect, revoke, disconnect ────────────────────────────────────────────


def test_a_reconnect_revokes_the_old_refresh_token_in_the_form_body(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    registry.rows[ORG_A] = {
        "id": "conn-0001", "org": ORG_A, "status": "needs_reconnect",
        "credential": _credential(refresh=S_OLD_REFRESH), "scopes": [],
        "connected_by": ADMIN, "last_error": "invalid_code",
    }
    assert _callback(_client(_user()), _state()).status_code == 302
    revokes = [r for r in zoho.requests if r.url.path.endswith("/token/revoke")]
    assert len(revokes) == 1
    assert revokes[0].url.query == b""
    assert parse_qs(revokes[0].content.decode()) == {"token": [S_OLD_REFRESH]}
    assert str(revokes[0].url.host) == "accounts.zoho.in"
    row = registry.rows[ORG_A]
    assert row["status"] == "connected"
    assert row["last_error"] is None
    assert row["credential"].refresh_token == S_REFRESH


def test_disconnect_revokes_deletes_the_credential_and_is_idempotent(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    client = _client(_user())
    assert _callback(client, _state()).status_code == 302
    zoho.requests.clear()
    first = client.post("/crm/connection/disconnect")
    assert first.status_code == 200
    assert first.json()["status"] == "disconnected"
    assert first.json()["connected"] is False
    assert registry.rows[ORG_A]["credential"] is None
    sent = [r for r in zoho.requests if r.url.path.endswith("/token/revoke")]
    assert len(sent) == 1
    zoho.requests.clear()
    second = client.post("/crm/connection/disconnect")
    assert second.status_code == 200
    assert zoho.requests == []


def test_a_failed_revoke_does_not_stop_the_disconnect(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    client = _client(_user())
    assert _callback(client, _state()).status_code == 302
    zoho.revoke_body = {"status": "failure"}
    response = client.post("/crm/connection/disconnect")
    assert response.status_code == 200
    assert registry.rows[ORG_A]["credential"] is None


async def test_revoke_posts_the_token_in_the_form_and_never_in_the_url() -> None:
    zoho = FakeZoho()
    config = zoho_config()
    assert await auth.revoke(config, _credential(), transport=zoho.transport) is True
    (request,) = zoho.requests
    assert request.method == "POST"
    assert request.url.path == "/oauth/v2/token/revoke"
    assert request.url.query == b""
    assert S_REFRESH not in str(request.url)
    assert parse_qs(request.content.decode()) == {"token": [S_REFRESH]}


async def test_revoke_off_the_allowlist_sends_nothing() -> None:
    zoho = FakeZoho()
    bad = _credential()
    bad = SourceCredential(
        access_token=bad.access_token, refresh_token=bad.refresh_token,
        expires_at=bad.expires_at, scopes=bad.scopes,
        provider_meta={**bad.provider_meta,
                       auth.META_ACCOUNTS_SERVER: "https://accounts.evil.example"},
    )
    with pytest.raises(Exception, match="allowlist"):
        await auth.revoke(zoho_config(), bad, transport=zoho.transport)
    assert zoho.requests == []


def zoho_config():
    from gateway.crm_sources.base import OAuthClientConfig

    return OAuthClientConfig(client_id="1000.CLIENTID", client_secret=S_SECRET,
                             redirect_uri="https://app.metorite.com/x")


def test_cn_is_not_a_data_centre() -> None:
    assert "cn" not in auth.ZOHO_DATA_CENTRES
    assert len(auth.ZOHO_DATA_CENTRES) == 8


# ── 2. The shared token and a refresh failure ────────────────────────────────


def _expired_row(registry: FakeRegistry) -> None:
    registry.rows[ORG_A] = {
        "id": "conn-0001", "org": ORG_A, "status": "connected",
        "credential": _credential(expires=_NOW - timedelta(minutes=1)),
        "scopes": [], "connected_by": ADMIN, "last_error": None,
    }


async def test_a_due_token_is_refreshed_and_written_back_for_every_worker(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    _expired_row(registry)
    fresh = await conn.fresh_zoho_credential(ORG_A, transport=zoho.transport,
                                             clock=lambda: _NOW)
    assert fresh is not None and fresh.access_token == S_NEW_ACCESS
    assert registry.rows[ORG_A]["credential"].access_token == S_NEW_ACCESS
    assert registry.rows[ORG_A]["credential"].refresh_token == S_REFRESH
    # A second worker reads the stored token and sends no request.
    again = await conn.fresh_zoho_credential(ORG_A, transport=zoho.transport,
                                             clock=lambda: _NOW)
    assert again is not None and again.access_token == S_NEW_ACCESS
    assert len(zoho.requests) == 1


async def test_no_connection_gives_none_and_no_request(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    _registry, zoho = world
    assert await conn.fresh_zoho_credential(ORG_B, transport=zoho.transport) is None
    assert zoho.requests == []


@pytest.mark.parametrize("code", ["invalid_code", "invalid_grant", "invalid_client"])
async def test_a_refresh_failure_sets_needs_reconnect_and_is_never_retried(
    world: tuple[FakeRegistry, FakeZoho], code: str,
) -> None:
    registry, zoho = world
    zoho.token_body = {"error": code}
    _expired_row(registry)
    with pytest.raises(NeedsReconnect):
        await conn.fresh_zoho_credential(ORG_A, transport=zoho.transport, clock=lambda: _NOW)
    row = registry.rows[ORG_A]
    assert row["status"] == "needs_reconnect"
    assert row["last_error"] == code
    assert len(zoho.requests) == 1
    # Never retried: the next call stops at the row and sends nothing.
    with pytest.raises(NeedsReconnect):
        await conn.fresh_zoho_credential(ORG_A, transport=zoho.transport, clock=lambda: _NOW)
    assert len(zoho.requests) == 1
    shown = _client(_user()).get("/crm/connection").json()
    assert shown["status"] == "needs_reconnect"
    assert shown["last_error"] == code
    assert shown["connected"] is False


async def test_a_throttle_does_not_mark_the_connection(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    registry, zoho = world
    zoho.token_body = {"error": "Access Denied",
                       "error_description": "You have made too many requests"}
    _expired_row(registry)
    with pytest.raises(RateLimited):
        await conn.fresh_zoho_credential(ORG_A, transport=zoho.transport, clock=lambda: _NOW)
    assert registry.rows[ORG_A]["status"] == "connected"


async def test_a_blob_that_does_not_open_is_none_or_undecryptable_with_no_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRM-Z2 review P1. The plain read gives ``None``, so a disconnect and a
    reconnect go on. The locked read raises, so the shared token can stop.

    A key IS set: with none, the key store raises RuntimeError, and that must
    not count as a blob that does not open (re-review P1)."""
    from types import SimpleNamespace

    import acb_llm.key_store as key_store

    monkeypatch.setattr(get_settings(), "acb_master_key", "z2-hermetic-master-key",
                        raising=False)
    monkeypatch.setattr(key_store, "_store", None)

    class _OneRow:
        async def execute(self, *_a: Any, **_k: Any) -> Any:
            return SimpleNamespace(first=lambda: SimpleNamespace(
                credentials_encrypted="bm90LWEtYmxvYg=="))

    db = _OneRow()
    assert await store.load_credential(db, ORG_A, "zoho", "crm") is None
    with pytest.raises(store.Undecryptable) as caught:
        await store.load_credential_locked(db, ORG_A, "conn-0001")
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert "bm90" not in str(caught.value)
    assert str(caught.value) == "InvalidToken"

    # With no key at all, the fault is loud and is not a dead blob.
    monkeypatch.setattr(key_store, "_store", None)
    monkeypatch.setattr(get_settings(), "acb_master_key", "", raising=False)
    monkeypatch.delenv("ACB_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError):
        await store.load_credential(db, ORG_A, "zoho", "crm")


def test_the_last_error_keeps_a_code_and_never_free_text() -> None:
    assert store.safe_error_code("invalid_code") == "invalid_code"
    assert store.safe_error_code(f"bad {S_REFRESH} token") == "refresh_failed"
    assert store.safe_error_code("") == "refresh_failed"
    assert store.safe_error_code("x" * 41) == "refresh_failed"


# ── 3. The sentinel fence ────────────────────────────────────────────────────


def test_no_token_and_no_secret_reaches_a_response_or_a_log_line(
    world: tuple[FakeRegistry, FakeZoho], caplog: pytest.LogCaptureFixture,
) -> None:
    registry, zoho = world
    client = _client(_user())
    bodies: list[str] = []
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        start = client.post("/crm/connection/zoho/start")
        bodies.append(start.text)
        done = _callback(client, _state())
        bodies += [done.text, json.dumps(dict(done.headers))]
        bodies.append(client.get("/crm/connection").text)
        # A reconnect (revokes the old token) and a refresh of the stored one.
        again = _callback(client, _state())
        bodies += [again.text, json.dumps(dict(again.headers))]
        registry.rows[ORG_A]["credential"] = _credential(
            expires=_NOW - timedelta(minutes=1), refresh=S_REFRESH)
        import asyncio

        asyncio.run(conn.fresh_zoho_credential(ORG_A, transport=zoho.transport,
                                               clock=lambda: _NOW))
        zoho.token_body = {"error": "invalid_code", "error_description": S_REFRESH}
        registry.rows[ORG_A]["credential"] = _credential(
            expires=_NOW - timedelta(minutes=1), refresh=S_REFRESH)
        with pytest.raises(NeedsReconnect) as caught:
            asyncio.run(conn.fresh_zoho_credential(ORG_A, transport=zoho.transport,
                                                   clock=lambda: _NOW))
        bodies.append(str(caught.value) + repr(caught.value))
        bodies.append(client.get("/crm/connection").text)
        bodies.append(client.post("/crm/connection/disconnect").text)
        # A bad state answers with no secret either.
        bodies.append(_callback(client, "forged").text)
    # The test client is httpx too, and httpx logs its own request line with the
    # query. That line is the test's, not the gateway's, so it is left out.
    records = [r for r in caplog.records if "testserver" not in r.getMessage()]
    logged = "\n".join(
        [r.getMessage() for r in records]
        + [json.dumps(e, default=str) for e in events]
        + [str(r.args) for r in records]
    )
    assert logged, "the log capture saw nothing, so the fence proves nothing"
    haystack = "\n".join(bodies) + "\n" + logged
    for sentinel in SENTINELS:
        assert sentinel not in haystack, f"{sentinel} leaked"
    for request in zoho.requests:
        for sentinel in SENTINELS:
            assert sentinel not in str(request.url), "a secret is in a URL"


def test_the_response_model_has_no_secret_field() -> None:
    names = set(conn.ConnectionModel.model_fields)
    assert names == {
        "connected", "provider", "owning_app", "status", "location", "scopes",
        "connected_by", "last_sync_at", "last_error", "updated_at", "mode",
    }
    assert set(conn.StartModel.model_fields) == {"url"}


def test_a_connection_row_object_has_no_blob() -> None:
    from dataclasses import fields

    assert not any("credential" in f.name or "token" in f.name
                   for f in fields(store.Connection))


# ── 4. CRM_MIRROR off → 404 ──────────────────────────────────────────────────

_NEW_ROUTES = [
    ("GET", "/crm/connection"),
    ("POST", "/crm/connection/zoho/start"),
    ("GET", "/crm/oauth/zoho/callback"),
    ("POST", "/crm/connection/disconnect"),
]


def test_with_the_mirror_switch_off_each_new_route_is_404(
    world: tuple[FakeRegistry, FakeZoho], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registry, zoho = world
    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    client = _client(_user())
    for method, path in _NEW_ROUTES:
        response = client.request(method, path, params={"state": _state(), "code": "x"})
        assert response.status_code == 404, (method, path)
    assert zoho.requests == []


# ── 4b. CRM_MIRROR_ORGS: the per-org mirror list (CRM-L2) ────────────────────

ORG_OUT = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de00ff"


def test_an_org_outside_the_mirror_list_gets_404_on_each_connect_route(
    world: tuple[FakeRegistry, FakeZoho], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registry, zoho = world
    monkeypatch.setattr(get_settings(), "crm_mirror_orgs", ORG_A, raising=False)
    inside = _client(_user(org=ORG_A))
    assert inside.get("/crm/connection").status_code == 200
    outside = _client(_user(org=ORG_OUT))
    for method, path in _NEW_ROUTES:
        response = outside.request(method, path, params={"state": _state(ORG_OUT), "code": "x"})
        assert response.status_code == 404, (method, path)
    assert zoho.requests == []


def test_an_empty_mirror_list_allows_no_org(
    world: tuple[FakeRegistry, FakeZoho], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.routes.crm import flags

    monkeypatch.setattr(get_settings(), "crm_mirror_orgs", "", raising=False)
    assert flags.crm_mirror_allowed(ORG_A) is False
    assert _client(_user(org=ORG_A)).get("/crm/connection").status_code == 404


def test_a_star_in_the_mirror_list_allows_each_org_and_a_missing_org_none(
    world: tuple[FakeRegistry, FakeZoho], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.routes.crm import flags

    monkeypatch.setattr(get_settings(), "crm_mirror_orgs", "*", raising=False)
    assert flags.crm_mirror_allowed(ORG_A) is True
    assert flags.crm_mirror_allowed(ORG_OUT) is True
    assert flags.crm_mirror_allowed(None) is False
    # The mirror list never opens an org that the CRM list or the switch shuts.
    monkeypatch.setattr(get_settings(), "crm_orgs", ORG_A, raising=False)
    assert flags.crm_mirror_allowed(ORG_OUT) is False
    monkeypatch.setattr(get_settings(), "crm_mirror", False, raising=False)
    assert flags.crm_mirror_allowed(ORG_A) is False


def test_the_mirror_list_defaults_to_empty() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["crm_mirror_orgs"].default == ""


async def test_the_scheduler_skips_an_org_outside_the_mirror_list(
    world: tuple[FakeRegistry, FakeZoho], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.crm_sync import scheduler

    monkeypatch.setattr(get_settings(), "crm_mirror_orgs", ORG_A, raising=False)
    # The org check runs before any database call, so a skipped org opens no session.
    def _no_session(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("is_due opened a tenant session for a skipped org")

    monkeypatch.setattr(scheduler, "tenant_session", _no_session)
    assert await scheduler.is_due(ORG_OUT) is False


def test_the_mirror_switch_defaults_to_off() -> None:
    from acb_common.settings import Settings

    assert Settings.model_fields["crm_mirror"].default is False
    assert Settings.model_fields["crm_zoho_client_id"].default == ""


@pytest.mark.parametrize("name", ["CRM_MIRROR", "CRM_ZOHO_CLIENT_ID", "CRM_ZOHO_CLIENT_SECRET"])
def test_a_tenant_cannot_set_the_new_names(name: str) -> None:
    from acb_common.env_guard import is_platform_env

    assert is_platform_env(name) is True


def test_every_new_route_of_the_gateway_carries_both_switches() -> None:
    import gateway.main as main
    from fastapi.dependencies.utils import get_dependant
    from gateway.routes.crm import flags

    from tests.unit._routes import served_routes

    found = {}
    for route in served_routes(main.app.routes):
        path = str(getattr(route, "path", ""))
        if path in {"/crm/connection", "/crm/connection/zoho/start",
                    "/crm/oauth/zoho/callback", "/crm/connection/disconnect"}:
            calls = {getattr(d, "dependency", None) for d in route.dependencies or []}
            calls |= {d.call for d in get_dependant(path=path, call=route.endpoint).dependencies}
            found[path] = calls
    assert len(found) == 4, sorted(found)
    for path, calls in found.items():
        assert flags.require_crm_mirror in calls, path


# ── 5. The fences (R7) ───────────────────────────────────────────────────────

# ⚠️ The column name is NOT unique in the tree: the email and WhatsApp account
# tables carry a column of the same name. So the fence names the TABLE.
_BLOB = "integration" + "_connections"
_STORE = _REPO / "apps/services/gateway/gateway/connections/store.py"


def _names(roots: list[Path], needle: str, *, exempt: Path | None = None) -> list[str]:
    """Each python file that names *needle* as an identifier, attribute or string."""
    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            if exempt is not None and path.resolve() == exempt.resolve():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                text = None
                if isinstance(node, ast.Name):
                    text = node.id
                elif isinstance(node, ast.Attribute):
                    text = node.attr
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = node.value
                elif isinstance(node, ast.keyword):
                    text = node.arg
                if text and needle in text:
                    hits.append(path.resolve().as_posix())
                    break
    return hits


def test_only_the_store_names_the_connections_table() -> None:
    assert _STORE.exists()
    readers = _names([_REPO / "apps", _REPO / "packages"], _BLOB, exempt=_STORE)
    assert readers == [], f"Only connections/store.py may name the connections table: {readers}"
    assert _names([_STORE.parent], _BLOB) == [_STORE.resolve().as_posix()], (
        "the fence is vacuous if the store no longer names the table"
    )


def test_the_table_fence_sees_a_second_reader(tmp_path: Path) -> None:
    for i, source in enumerate((
        f"def f(r):\n    return r.{_BLOB}\n",
        f"SQL = 'SELECT {_BLOB} FROM t'\n",
        f"def g(**kw):\n    return h({_BLOB}=1)\n",
    )):
        plant = tmp_path / f"second{i}.py"
        plant.write_text(source, encoding="utf-8")
        assert _names([tmp_path], _BLOB) != [], source
        plant.unlink()


def _attr_readers(names: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for root in (_REPO / "apps", _REPO / "packages"):
        for path in root.rglob("*.py"):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            seen: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in names:
                    seen.add(node.attr)
                elif isinstance(node, ast.Constant) and node.value in names:
                    # getattr(settings, "crm_zoho_client_id") also counts.
                    seen.add(node.value)
            for name in seen:
                out.setdefault(name, []).append(path.resolve().as_posix())
    return out


def test_one_module_reads_the_zoho_client_settings() -> None:
    reader = (_REPO / "apps/services/gateway/gateway/routes/crm/connection.py").resolve().as_posix()
    found = _attr_readers({"crm_zoho_client_id", "crm_zoho_client_secret"})
    for name, files in found.items():
        assert files == [reader], (name, files)
    assert set(found) == {"crm_zoho_client_id", "crm_zoho_client_secret"}


def test_the_store_uses_the_one_key_seam_and_makes_no_key() -> None:
    tree = ast.parse(_STORE.read_text(encoding="utf-8"))
    imported = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert "acb_llm.key_store" in imported
    forbidden = {"cryptography", "cryptography.fernet"}
    plain = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not (imported | plain) & forbidden, "the store must not build its own Fernet"


# ── Signing: the purpose argument ────────────────────────────────────────────


def test_a_state_signed_for_one_purpose_never_verifies_for_another(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    token = signing.sign_state(purpose="p.one", org=ORG_A, member=ADMIN)
    assert signing.verify_state(token, purpose="p.one") is not None
    assert signing.verify_state(token, purpose="p.two") is None
    assert signing.verify_oauth_state(token) is None
    assert signing.verify_webhook_org(ORG_A, token) is None
    email = signing.sign_oauth_state(org=ORG_A, member=ADMIN, provider="microsoft")
    assert signing.verify_state(email, purpose="p.one") is None
    assert signing.verify_oauth_state(email) is not None


def test_the_generic_signer_refuses_the_email_purposes(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    for purpose in (signing.OAUTH_STATE_PURPOSE, signing.WEBHOOK_PURPOSE, ""):
        with pytest.raises(ValueError):
            signing.sign_state(purpose=purpose, org=ORG_A, member=ADMIN)
        with pytest.raises(ValueError):
            signing.verify_state("a.b", purpose=purpose)


def test_the_state_carries_org_member_nonce_and_a_ten_minute_expiry(
    world: tuple[FakeRegistry, FakeZoho],
) -> None:
    token = signing.sign_state(purpose="p.one", org=ORG_A, member="Admin@Zoho-Z2.test",
                               now=1_000_000.0)
    payload = signing.verify_state(token, purpose="p.one", now=1_000_001.0)
    assert payload is not None
    assert payload["org"] == ORG_A
    assert payload["member"] == "admin@zoho-z2.test"
    assert payload["exp"] == 1_000_000 + 600
    assert payload["nonce"]
    assert signing.verify_state(token, purpose="p.one", now=1_000_601.0) is None
    other = signing.sign_state(purpose="p.one", org=ORG_A, member=ADMIN, now=1_000_000.0)
    assert signing.verify_state(other, purpose="p.one", now=1_000_001.0)["nonce"] != payload["nonce"]


# ── CRM-X0: the same Zoho account, another one, or no answer ────────────────


def _org_answer(zoho: FakeZoho, body: dict[str, Any] | None) -> None:
    """Make ``GET org`` answer *body*. The token calls answer as before."""
    plain = zoho._handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/org"):
            zoho.requests.append(request)
            return httpx.Response(200, json=body or {})
        return plain(request)

    zoho.transport = httpx.MockTransport(handle)


@pytest.fixture
def stored(world, monkeypatch):
    """A source profile names the account ``Z-A``. A start puts the old row in."""
    registry, zoho = world

    async def _stored(db: Any, org: str) -> Any:
        return conn.KnownAccount(profile_org={"id": "Z-A", "domain_name": "orga"})

    async def _needs_reset(org: str) -> None:
        registry.rows[org].update(status="needs_reset", last_error="different_org")

    monkeypatch.setattr(conn, "_known_account", _stored)
    monkeypatch.setattr(conn, "_mark_needs_reset", _needs_reset)
    registry.rows[ORG_A] = {
        "id": "conn-0001", "org": ORG_A, "status": "disconnected", "credential": None,
        "connected_by": ADMIN, "last_error": None, "scopes": [],
    }
    registry.modes[ORG_A] = "mirror"
    monkeypatch.setattr(
        conn, "_source",
        lambda credential=None, transport=None: ZohoSource(
            conn.zoho_oauth_config(), credential, transport=zoho.transport,
        ),
    )
    return registry, zoho


def test_x0_the_same_account_connects_and_keeps_the_mode(stored) -> None:
    registry, zoho = stored
    _org_answer(zoho, {"org": [{"id": "Z-A", "domain_name": "orga"}]})
    response = _callback(_client(_user()), _state())
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "/crm?connected=zoho"
    assert registry.rows[ORG_A]["status"] == "connected"
    assert [r.url.path.rsplit("/", 1)[-1] for r in zoho.requests] == ["token", "org"]


def test_x0_another_account_needs_a_reset_and_revokes_the_new_token(stored) -> None:
    registry, zoho = stored
    _org_answer(zoho, {"org": [{"id": "Z-OTHER", "domain_name": "other"}]})
    response = _callback(_client(_user()), _state())
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "/crm?connect_error=different_org"
    assert registry.rows[ORG_A]["status"] == "needs_reset"
    assert registry.adopted == []
    revokes = [r for r in zoho.requests if r.url.path.endswith("/token/revoke")]
    assert len(revokes) == 1 and S_REFRESH.encode() in revokes[0].content


@pytest.mark.parametrize("body", [{}, {"org": []}, {"org": [{"currency": "INR"}]}])
def test_x0_an_account_that_does_not_answer_changes_nothing(stored, body) -> None:
    registry, zoho = stored
    _org_answer(zoho, body)
    response = _callback(_client(_user()), _state())
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "/crm?connect_error=org_unreadable"
    assert registry.rows[ORG_A]["status"] == "disconnected"
    assert registry.adopted == []
    assert any(r.url.path.endswith("/token/revoke") for r in zoho.requests)


def test_x0_every_return_has_one_query_name() -> None:
    """Review P1-1: the BFF callback passes a Location with ONE query name
    only (``allowedLocation`` in ``api/crm/oauth/zoho/callback/route.ts``)."""
    from urllib.parse import parse_qsl, urlsplit

    for location in (conn.RETURN_OK, conn.RETURN_DIFFERENT_ORG, f"{conn.RETURN_FAILED}x"):
        parts = urlsplit(location)
        assert parts.path == "/crm"
        assert len(parse_qsl(parts.query)) == 1, location
