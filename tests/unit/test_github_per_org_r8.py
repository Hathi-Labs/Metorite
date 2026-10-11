"""R8 — GitHub is the connection of one organization, against a real Postgres (WS-54 IN-7).

Spec: ``project-docs/specs/integrations_console.md`` §2.2 (IN-D10, IN-D18), §3.3
and §7.2 (IN-7). The device flow saves an ORG connection through
``connections/store.py``. ``connections/github.py`` is the one reader of the
token of a customer. The server's own ``GITHUB_TOKEN`` and the ``gh`` CLI are
the operator's, so no customer run reads either.

What this proves against a REAL Postgres, through the real ``/integrations``
router, as the NON-privileged role of the phase-4 rehearsal:

- two organizations each run the device-flow poll (a fake GitHub on
  ``httpx.MockTransport``) and hold two different tokens, as ciphertext;
- a reader in org A gets A's token and never B's;
- a reader with no connection gets "not connected", and no request carries the
  sentinel ``GITHUB_TOKEN`` of the deployment;
- ``GET /integrations/github/account`` shows the login of the org, not of the
  operator, and holds no token;
- an old ``provider_keys`` ``github:token`` row moves into the store on the
  first read, once, and the old row is gone;
- a disconnect deletes the blob, and an old row cannot bring the token back;
- no token appears in a response or in a log line.

Hermetic fakes agree with any SQL they are handed, so none of this is proved
without the database.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops.
pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient  # the test's own client, taken before any patch

ADMIN_A = "admin-a@in7-r8.test"
ADMIN_B = "admin-b@in7-r8.test"

#: The deployment-wide token of the operator. It must reach no request.
OPERATOR_SENTINEL = "ghp_OPERATOR_SENTINEL_must_never_leave_the_box"

TOKEN_A = "gho_TOKEN_OF_ORG_A_0123456789"
TOKEN_B = "gho_TOKEN_OF_ORG_B_0123456789"
LEGACY_A = "gho_LEGACY_ROW_OF_ORG_A_012345"

_BY_DEVICE = {
    "DEV-A": (TOKEN_A, "repo,copilot", "octo-a"),
    "DEV-B": (TOKEN_B, "repo", "octo-b"),
}
_BY_TOKEN = {tok: (login, scope) for tok, scope, login in _BY_DEVICE.values()}
_BY_TOKEN[LEGACY_A] = ("legacy-a", "repo")


class FakeGithub:
    """One fake GitHub. It records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "github.com" and path == "/login/oauth/access_token":
            device = dict(httpx.QueryParams(request.content.decode())).get("device_code", "")
            if device in _BY_DEVICE:
                token, scope, _ = _BY_DEVICE[device]
                return httpx.Response(200, json={
                    "access_token": token, "token_type": "bearer", "scope": scope,
                })
            return httpx.Response(200, json={"error": "authorization_pending"})
        if host == "api.github.com" and path == "/user":
            auth = request.headers.get("authorization", "")
            token = auth.removeprefix("token ").strip()
            if token in _BY_TOKEN:
                login, scope = _BY_TOKEN[token]
                return httpx.Response(
                    200, json={"login": login}, headers={"X-OAuth-Scopes": scope},
                )
            return httpx.Response(401, json={"message": "Bad credentials"})
        if host == "raw.githubusercontent.com":
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    def sent_text(self) -> str:
        """Every header and body of every request, as one string."""
        return "\n".join(
            f"{r.url} {dict(r.headers)} {r.content!r}" for r in self.requests
        )


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _admin_dsn(ns: Any) -> str:
    return ns.admin_engine.url.render_as_string(hide_password=False)


def _user(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations", "integrations:manage"]),
        organization_id=org,
    )


@pytest.fixture
def ns(promoted, monkeypatch, tmp_path):  # noqa: F811 — a fixture
    """The promoted catalog, the settings, a fake GitHub, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    settings = get_settings()
    for name, value in {
        "acb_master_key": "r8-master-key-in7",
        "database_url": _admin_dsn(promoted),
        "github_client_id": "Iv1.in7-r8-client",
        "github_token": OPERATOR_SENTINEL,
        "byok_enabled": True,
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", OPERATOR_SENTINEL)
    monkeypatch.setenv("GH_TOKEN", OPERATOR_SENTINEL)
    monkeypatch.setattr(key_store, "_store", None)

    fake = FakeGithub()

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(fake.handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)

    ran: list[Any] = []
    monkeypatch.setattr("subprocess.run", lambda *a, **k: ran.append(a))
    promoted.github = fake
    promoted.subprocess_runs = ran
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM integration_connections "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})
            c.execute(text(
                "DELETE FROM provider_keys "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def _call(
    p: Any, user: Any, method: str, path: str, body: dict | None = None,
    params: dict | None = None,
) -> Any:
    """One request through the real ``/integrations`` router, as *user*.

    The override binds the tenant from the user. The tenant never comes from
    the request.
    """
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import integrations

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(integrations.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with REAL_CLIENT(
                transport=transport, base_url="http://integrations.test",
            ) as client:
                return await client.request(method, path, json=body, params=params)
    finally:
        release_tenant(scope)


async def _connect(p: Any, org: str, email: str, device: str) -> Any:
    return await _call(
        p, _user(email, org), "POST", "/integrations/github/device/poll",
        {"device_code": device},
    )


def _rows(p: Any, org: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT status, connected_by, scopes, credentials_encrypted, "
            "provider_meta ->> 'login' AS login "
            "FROM integration_connections WHERE organization_id = CAST(:o AS uuid) "
            "AND provider = 'github' AND owning_app = 'integrations' "
            "AND scope_kind = 'org'"), {"o": org}).all())


def _legacy_rows(p: Any, org: str) -> int:
    with p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT count(*) FROM provider_keys WHERE organization_id = "
            "CAST(:o AS uuid) AND provider = 'github:token'"), {"o": org}).scalar_one()


async def _read_token(p: Any, org: str) -> str | None:
    from gateway.connections import github

    async with tenant_engine_scope(_app_dsn(p)):
        return await github.token_or_none(org)


async def _seed_legacy(p: Any, org: str, token: str) -> None:
    """Write the old ``provider_keys`` row, as the pre-IN-7 device flow did."""
    from acb_llm.key_store import get_key_store

    await get_key_store().put(
        "github:token", token, credential_type="integration",
        service="github", organization_id=org,
    )


# ── Two orgs, two tokens ─────────────────────────────────────────────────────

async def test_two_orgs_hold_two_tokens_and_a_reader_gets_its_own(ns, app_engine) -> None:  # noqa: F811
    from acb_common.db import tenant_session
    from gateway.connections import github, store

    a = await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    b = await _connect(ns, ns.org_b, ADMIN_B, "DEV-B")
    assert a.status_code == 200 and a.json() == {"status": "authorized", "login": "octo-a"}, a.text
    assert b.status_code == 200 and b.json() == {"status": "authorized", "login": "octo-b"}, b.text

    (row_a,) = _rows(ns, ns.org_a)
    (row_b,) = _rows(ns, ns.org_b)
    assert (row_a.status, row_a.connected_by, row_a.login) == ("connected", ADMIN_A, "octo-a")
    assert (row_b.status, row_b.connected_by, row_b.login) == ("connected", ADMIN_B, "octo-b")
    assert list(row_a.scopes) == ["repo", "copilot"] and list(row_b.scopes) == ["repo"]
    for row, token in ((row_a, TOKEN_A), (row_b, TOKEN_B)):
        assert row.credentials_encrypted and token not in row.credentials_encrypted

    # Nothing goes to provider_keys any more.
    assert _legacy_rows(ns, ns.org_a) == 0 and _legacy_rows(ns, ns.org_b) == 0

    # A reader of org A gets A's token. It never gets B's, even when it asks.
    assert await _read_token(ns, ns.org_a) == TOKEN_A
    assert await _read_token(ns, ns.org_b) == TOKEN_B
    async with tenant_engine_scope(_app_dsn(ns)):
        async with tenant_session(ns.org_a) as db:
            foreign = await store.load_credential(db, ns.org_b, github.PROVIDER, github.OWNING_APP)
            foreign_row = await store.get(db, ns.org_b, github.PROVIDER, github.OWNING_APP)
    assert foreign is None and foreign_row is None


async def test_the_poll_never_sends_the_operator_token(ns, app_engine) -> None:  # noqa: F811
    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    assert ns.github.requests, "the fake GitHub saw no request"
    assert OPERATOR_SENTINEL not in ns.github.sent_text()


# ── No connection: not connected, and never the deployment token ────────────

async def test_a_reader_with_no_connection_is_not_connected_and_sends_no_request(ns, app_engine) -> None:  # noqa: F811
    from gateway.connections import github

    assert await _read_token(ns, ns.org_a) is None
    async with tenant_engine_scope(_app_dsn(ns)):
        with pytest.raises(github.GitHubNotConnected) as caught:
            await github.token_for_org(ns.org_a)
    assert str(caught.value) == "GitHub is not connected for your organization."
    assert ns.github.requests == []
    assert OPERATOR_SENTINEL not in ns.github.sent_text()


async def test_the_agent_config_fetch_of_an_org_without_a_connection_sends_no_token(ns, app_engine) -> None:  # noqa: F811
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import HTTPException
    from gateway.routes import agent

    scope = clear_tenant()
    bind_tenant(ns.org_a)
    try:
        async with tenant_engine_scope(_app_dsn(ns)):
            with pytest.raises(HTTPException):
                await agent.get_agent_config(
                    repo="octo/agent-x", user=_user(ADMIN_A, ns.org_a),
                )
    finally:
        release_tenant(scope)
    assert ns.github.requests, "the fetch went nowhere"
    for request in ns.github.requests:
        assert "authorization" not in request.headers
    assert OPERATOR_SENTINEL not in ns.github.sent_text()


async def test_the_agent_config_fetch_uses_the_token_of_its_own_org(ns, app_engine) -> None:  # noqa: F811
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import HTTPException
    from gateway.routes import agent

    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    await _connect(ns, ns.org_b, ADMIN_B, "DEV-B")
    ns.github.requests.clear()
    scope = clear_tenant()
    bind_tenant(ns.org_b)
    try:
        async with tenant_engine_scope(_app_dsn(ns)):
            with pytest.raises(HTTPException):
                await agent.get_agent_config(
                    repo="octo/agent-x", user=_user(ADMIN_B, ns.org_b),
                )
    finally:
        release_tenant(scope)
    raw = [r for r in ns.github.requests if r.url.host == "raw.githubusercontent.com"]
    assert raw and all(r.headers["authorization"] == f"token {TOKEN_B}" for r in raw)
    assert TOKEN_A not in ns.github.sent_text()
    assert OPERATOR_SENTINEL not in ns.github.sent_text()


# ── The account card ─────────────────────────────────────────────────────────

async def test_the_account_shows_the_org_login_and_no_token(ns, app_engine) -> None:  # noqa: F811
    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    await _connect(ns, ns.org_b, ADMIN_B, "DEV-B")
    ns.github.requests.clear()

    seen_a = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/github/account")
    seen_b = await _call(ns, _user(ADMIN_B, ns.org_b), "GET", "/integrations/github/account")
    assert seen_a.status_code == 200 and seen_b.status_code == 200
    a, b = seen_a.json(), seen_b.json()
    assert (a["login"], a["connected_by"], a["status"], a["connected"]) == (
        "octo-a", ADMIN_A, "connected", True)
    assert a["scopes"] == ["repo", "copilot"] and a["token_has_copilot"] is True
    assert (b["login"], b["connected_by"], b["scopes"]) == ("octo-b", ADMIN_B, ["repo"])
    for body in (seen_a.text, seen_b.text):
        for secret in (TOKEN_A, TOKEN_B, OPERATOR_SENTINEL):
            assert secret not in body
    # The card makes no GitHub request and runs no `gh`.
    assert ns.github.requests == []
    assert ns.subprocess_runs == []
    assert a["gh_cli_available"] is False and a["gh_cli_login"] is None


async def test_an_org_without_a_connection_sees_not_connected_and_not_the_operator(ns, app_engine) -> None:  # noqa: F811
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/github/account")
    body = res.json()
    assert res.status_code == 200
    assert (body["connected"], body["login"], body["status"]) == (False, None, "not_connected")
    assert body["token_configured"] is False
    assert OPERATOR_SENTINEL not in res.text
    assert ns.subprocess_runs == []


async def test_the_test_button_uses_the_org_token_and_never_the_operator_token(ns, app_engine) -> None:  # noqa: F811
    none = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/test",
                       params={"service": "github"})
    assert none.json()["ok"] is False
    assert none.json()["detail"] == "GitHub is not connected for your organization."
    assert ns.github.requests == []

    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    ns.github.requests.clear()
    ok = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/test",
                     params={"service": "github"})
    assert ok.json()["ok"] is True and "octo-a" in ok.json()["detail"]
    assert [r.headers["authorization"] for r in ns.github.requests] == [f"token {TOKEN_A}"]
    assert OPERATOR_SENTINEL not in ns.github.sent_text()


# ── The old provider_keys row moves once ────────────────────────────────────

async def test_an_old_provider_keys_row_moves_into_the_store_once(ns, app_engine) -> None:  # noqa: F811
    async with tenant_engine_scope(_app_dsn(ns)):
        await _seed_legacy(ns, ns.org_a, LEGACY_A)
    assert _legacy_rows(ns, ns.org_a) == 1 and _rows(ns, ns.org_a) == []

    # Org B has no old row, and org A's row is not B's.
    assert await _read_token(ns, ns.org_b) is None
    assert _legacy_rows(ns, ns.org_a) == 1

    assert await _read_token(ns, ns.org_a) == LEGACY_A
    (row,) = _rows(ns, ns.org_a)
    assert row.status == "connected" and LEGACY_A not in row.credentials_encrypted
    assert _legacy_rows(ns, ns.org_a) == 0, "the old row must be gone"
    first_blob = row.credentials_encrypted

    # The second read comes from the store. It adds no row and writes no blob.
    assert await _read_token(ns, ns.org_a) == LEGACY_A
    (again,) = _rows(ns, ns.org_a)
    assert again.credentials_encrypted == first_blob
    assert _legacy_rows(ns, ns.org_a) == 0


async def test_the_account_card_moves_an_old_row_too(ns, app_engine) -> None:  # noqa: F811
    async with tenant_engine_scope(_app_dsn(ns)):
        await _seed_legacy(ns, ns.org_a, LEGACY_A)
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/github/account")
    assert res.json()["connected"] is True
    assert LEGACY_A not in res.text
    assert _legacy_rows(ns, ns.org_a) == 0 and len(_rows(ns, ns.org_a)) == 1


# ── Disconnect ───────────────────────────────────────────────────────────────

async def test_a_disconnect_deletes_the_blob_and_the_old_row_cannot_bring_it_back(ns, app_engine) -> None:  # noqa: F811
    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    await _connect(ns, ns.org_b, ADMIN_B, "DEV-B")
    async with tenant_engine_scope(_app_dsn(ns)):
        await _seed_legacy(ns, ns.org_a, LEGACY_A)
    ns.github.requests.clear()

    res = await _call(ns, _user(ADMIN_A, ns.org_a), "DELETE", "/integrations/github")
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["disconnected"] is True and body["revoked_at_github"] is False
    assert "client secret" in body["note"]
    assert ns.github.requests == [], "no revoke call is possible without the client secret"

    (row_a,) = _rows(ns, ns.org_a)
    assert row_a.status == "disconnected" and row_a.credentials_encrypted is None
    assert _legacy_rows(ns, ns.org_a) == 0
    assert await _read_token(ns, ns.org_a) is None
    # Org B keeps its token.
    assert await _read_token(ns, ns.org_b) == TOKEN_B
    assert _rows(ns, ns.org_b)[0].status == "connected"

    card = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/github/account")
    assert card.json()["connected"] is False and card.json()["status"] == "disconnected"


async def test_a_member_without_manage_cannot_disconnect(ns, app_engine) -> None:  # noqa: F811
    from acb_auth import UserContext, UserRole, build_access

    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    reader = UserContext(
        email="reader@in7-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations"]), organization_id=ns.org_a,
    )
    res = await _call(ns, reader, "DELETE", "/integrations/github")
    assert res.status_code == 403
    assert _rows(ns, ns.org_a)[0].status == "connected"


# ── No token in a response or a log line ────────────────────────────────────

async def test_no_github_token_appears_in_any_response_or_log_line(ns, app_engine, capsys, caplog) -> None:  # noqa: F811
    caplog.set_level("DEBUG")
    outputs: list[str] = []
    for org, email, device in ((ns.org_a, ADMIN_A, "DEV-A"), (ns.org_b, ADMIN_B, "DEV-B")):
        outputs.append((await _connect(ns, org, email, device)).text)
        user = _user(email, org)
        outputs.append((await _call(ns, user, "GET", "/integrations/github/account")).text)
        outputs.append((await _call(ns, user, "GET", "/integrations/test",
                                    params={"service": "github"})).text)
        outputs.append((await _call(ns, user, "GET", "/integrations/status")).text)
    async with tenant_engine_scope(_app_dsn(ns)):
        await _seed_legacy(ns, ns.org_a, LEGACY_A)
    await _read_token(ns, ns.org_a)
    outputs.append((await _call(ns, _user(ADMIN_A, ns.org_a), "DELETE", "/integrations/github")).text)

    captured = capsys.readouterr()
    haystack = "\n".join(outputs) + captured.out + captured.err + caplog.text
    for secret in (TOKEN_A, TOKEN_B, LEGACY_A, OPERATOR_SENTINEL):
        assert secret not in haystack, f"a GitHub token leaked: {secret[:8]}..."
