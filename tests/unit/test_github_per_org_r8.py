"""R8 — GitHub is the connection of one organization, against a real Postgres (WS-54 IN-7).

Spec: ``project-docs/specs/integrations_console.md`` §2.2 (IN-D10, IN-D18), §3.3
and §7.2 (IN-7). The device flow saves an ORG connection through
``connections/store.py``. ``connections/github.py`` is the one reader of the
token of a customer. The server's own ``GITHUB_TOKEN`` and the ``gh`` CLI are
the operator's, so no customer run reads either.

What this proves against a REAL Postgres, through the real ``/integrations``
router, as the NON-privileged role of the phase-4 rehearsal:

- two organizations each paste a token through ``PUT
  /integrations/github/token`` (WS-54 GH-7, a fake GitHub on
  ``httpx.MockTransport``) and hold two different tokens, as ciphertext;
- the route stores a token that ``GET /user`` accepts with BYOK OFF (T1),
  refuses one that GitHub refuses with 422 and stores nothing (T2), and no
  source file reads the customer client id any more (T5);
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

Fix round 1, item 3 (the App does not read a private agent repository, G-9),
each mutation red when run:

* 3-M1 ``PUT /token`` skips the live-App check ->
  ``test_a_token_takes_the_place_of_an_app_row_only_with_a_confirm``.
* 3-M2 the agent status keeps ``configured = _github_connected()`` ->
  ``test_item3_the_agent_status_is_for_each_use``.
* 3-M3 ``register_agent`` skips the private-repo refusal ->
  ``test_item3_an_app_org_cannot_register_a_private_agent_repo``.
* 3-M4 ``_private_repo_refusal`` answers ``None`` for a 404 ->
  ``test_item3_the_config_fetch_of_an_app_org_names_a_private_repo``.
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
            token = auth.removeprefix("token ").removeprefix("Bearer ").strip()
            if token in _BY_TOKEN:
                login, scope = _BY_TOKEN[token]
                return httpx.Response(
                    200, json={"login": login}, headers={"X-OAuth-Scopes": scope},
                )
            return httpx.Response(401, json={"message": "Bad credentials"})
        if host == "raw.githubusercontent.com":
            return httpx.Response(404, json={})
        if host == "api.github.com" and path == "/repos/octo/public-agent":
            # Item 3: a public repository with no config.json.
            return httpx.Response(200, json={"full_name": "octo/public-agent",
                                             "private": False})
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
    from gateway.routes import integrations, integrations_connections
    from gateway.routes.github import connect

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(integrations.router)
    app.include_router(integrations_connections.router)
    app.include_router(connect.token_router)
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
    """GH-7: the one writer of a pasted token. ``device`` names the token."""
    return await _call(
        p, _user(email, org), "PUT", "/integrations/github/token",
        {"token": _BY_DEVICE[device][0]},
    )


def _rows(p: Any, org: str) -> list[Any]:
    with p.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT status, connected_by, scopes, credentials_encrypted, "
            "provider_meta ->> 'login' AS login, provider_meta "
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
    assert a.status_code == 200 and a.json() == {
        "status": "connected", "login": "octo-a", "scopes": ["repo", "copilot"]}, a.text
    assert b.status_code == 200 and b.json() == {
        "status": "connected", "login": "octo-b", "scopes": ["repo"]}, b.text

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
    async with tenant_engine_scope(_app_dsn(ns)), tenant_session(ns.org_a) as db:
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
    assert "settings/tokens" in body["note"]
    assert ns.github.requests == [], "a disconnect calls nothing at GitHub"

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


# ── The move of an old row never overwrites a fresh token ───────────────────

async def test_the_move_of_an_old_row_never_overwrites_a_fresh_device_token(ns, app_engine) -> None:  # noqa: F811
    """A device flow commits between the first look and the move. The fresh token wins.

    The holder takes the advisory lock, so the reader blocks at the move. The
    holder saves the fresh token and commits. The reader then looks again under
    the lock and must keep the fresh token.
    """
    import asyncio

    from acb_common.db import tenant_session
    from gateway.connections import github

    fresh = "gho_FRESH_DEVICE_TOKEN_0123456789"
    async with tenant_engine_scope(_app_dsn(ns)):
        await _seed_legacy(ns, ns.org_a, LEGACY_A)
        async with tenant_session(ns.org_a) as holder:
            await github.lock(holder, ns.org_a)
            reader = asyncio.create_task(github.token_or_none(ns.org_a))
            await asyncio.sleep(1.5)
            assert not reader.done(), "the reader must wait for the lock"
            await github.save_token(
                holder, ns.org_a, access_token=fresh, login="octo-a",
                scopes=("repo",), connected_by=ADMIN_A,
            )
        token = await asyncio.wait_for(reader, 20)
    assert token == fresh
    assert await _read_token(ns, ns.org_a) == fresh
    assert _legacy_rows(ns, ns.org_a) == 0, "the stale old row must go"
    (row,) = _rows(ns, ns.org_a)
    assert row.login == "octo-a"


async def test_the_github_test_button_needs_the_manage_right(ns, app_engine) -> None:  # noqa: F811
    from acb_auth import UserContext, UserRole, build_access

    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    ns.github.requests.clear()
    reader = UserContext(
        email="reader@in7-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations"]), organization_id=ns.org_a,
    )
    res = await _call(ns, reader, "GET", "/integrations/test", params={"service": "github"})
    assert res.status_code == 403 and "integrations:manage" in res.text
    assert ns.github.requests == [], "the org token was spent for a member without the right"


async def test_the_console_disconnect_of_a_github_row_uses_the_github_disconnect(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """The console row drops the blob AND the old row, with an audit event and no revoke call."""
    import acb_audit

    events: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", events.append)
    await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    await _connect(ns, ns.org_b, ADMIN_B, "DEV-B")
    async with tenant_engine_scope(_app_dsn(ns)):
        await _seed_legacy(ns, ns.org_a, LEGACY_A)
    with ns.admin_engine.connect() as c:
        gid = c.execute(text(
            "SELECT id::text FROM integration_connections WHERE provider = 'github' "
            "AND organization_id = CAST(:o AS uuid)"), {"o": ns.org_a}).scalar_one()
    ns.github.requests.clear()

    res = await _call(ns, _user(ADMIN_A, ns.org_a), "POST",
                      f"/integrations/connections/github:{gid}/disconnect")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "disconnected" and res.json()["can_disconnect"] is False
    assert res.json()["provider"] == "github"
    assert _legacy_rows(ns, ns.org_a) == 0, "the console must clear the old row too"
    (row_a,) = _rows(ns, ns.org_a)
    assert row_a.status == "disconnected" and row_a.credentials_encrypted is None
    assert await _read_token(ns, ns.org_a) is None
    assert await _read_token(ns, ns.org_b) == TOKEN_B
    assert ns.github.requests == []
    # GH-7: each pasted token writes its own `github.token.connected` row.
    assert [e.action for e in events].count("github.token.connected") == 2
    (event,) = [e for e in events if e.action == "integration.disconnect"]
    assert event.target == f"github:{gid}" and event.action == "integration.disconnect"
    assert TOKEN_A not in res.text and LEGACY_A not in res.text


# ── GH-7: the Advanced token ────────────────────────────────────────────────

async def test_t1_the_token_route_stores_a_token_with_byok_off(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "byok_enabled", False, raising=False)
    res = await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    assert res.status_code == 200, res.text
    (row,) = _rows(ns, ns.org_a)
    assert row.status == "connected" and row.login == "octo-a"
    assert row.provider_meta["auth"] == "token"
    assert TOKEN_A not in row.credentials_encrypted
    assert await _read_token(ns, ns.org_a) == TOKEN_A
    assert TOKEN_A not in res.text


async def test_t2_a_token_that_github_refuses_is_422_and_stores_nothing(ns, app_engine) -> None:  # noqa: F811
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "PUT", "/integrations/github/token",
                      {"token": "ghp_NOT_A_REAL_TOKEN_0123456789"})
    assert res.status_code == 422 and res.json()["detail"] == "GitHub refused this token."
    assert _rows(ns, ns.org_a) == []
    assert "ghp_NOT_A_REAL_TOKEN_0123456789" not in res.text
    for bad in ("", "  ", "has space", "x" * 300):
        refused = await _call(ns, _user(ADMIN_A, ns.org_a), "PUT",
                              "/integrations/github/token", {"token": bad})
        assert refused.status_code in (400, 422), (bad[:10], refused.status_code)
        assert (bad.strip() == "" or bad not in refused.text)
    assert _rows(ns, ns.org_a) == []


async def test_the_token_route_needs_the_manage_right(ns, app_engine) -> None:  # noqa: F811
    from acb_auth import UserContext, UserRole, build_access

    reader = UserContext(
        email="reader@in7-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations"]), organization_id=ns.org_a,
    )
    res = await _call(ns, reader, "PUT", "/integrations/github/token", {"token": TOKEN_A})
    assert res.status_code == 403
    assert _rows(ns, ns.org_a) == [] and ns.github.requests == []


def _seed_app_row(ns: Any, org: str) -> None:
    import json

    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO integration_connections (organization_id, provider, owning_app, "
            "scope_kind, status, provider_meta) VALUES (CAST(:o AS uuid), 'github', "
            "'integrations', 'org', 'connected', CAST(:m AS jsonb))"),
            {"o": org, "m": json.dumps({"auth": "app", "installation_id": "424242"})})


async def test_a_token_takes_the_place_of_an_app_row_only_with_a_confirm(ns, app_engine) -> None:  # noqa: F811
    """Item 3: the org has ONE github row (G-6), so a token REPLACES the App.
    With no confirm the route answers 409, asks GitHub nothing, and keeps
    the App. With ``replace_app`` the token takes its place."""
    _seed_app_row(ns, ns.org_a)
    res = await _connect(ns, ns.org_a, ADMIN_A, "DEV-A")
    assert res.status_code == 409 and res.json()["detail"] == "app_connected", res.text
    assert ns.github.requests == [], "no GitHub call before the confirm"
    (row,) = _rows(ns, ns.org_a)
    assert row.provider_meta["auth"] == "app"
    res = await _call(ns, _user(ADMIN_A, ns.org_a), "PUT", "/integrations/github/token",
                      {"token": _BY_DEVICE["DEV-A"][0], "replace_app": True})
    assert res.status_code == 200, res.text
    (row,) = _rows(ns, ns.org_a)
    assert row.provider_meta["auth"] == "token"
    assert "installation_id" not in row.provider_meta
    assert await _read_token(ns, ns.org_a) == TOKEN_A


def test_t5_no_source_file_reads_the_customer_client_id() -> None:
    """WS-54 GH-7: nothing reads the org row of the OAuth App client id."""
    from pathlib import Path

    from acb_llm.key_store import INTEGRATION_ENV_MAP

    root = Path(__file__).resolve().parents[2]
    hits = [
        str(path.relative_to(root)) for base in ("apps", "packages")
        for path in (root / base).rglob("*.py")
        if "github:client_id" in path.read_text(encoding="utf-8")
    ]
    assert hits == [], hits
    assert "client_id" not in INTEGRATION_ENV_MAP["github"]
    from gateway.routes import integrations

    assert "GITHUB_CLIENT_ID" not in integrations.BUILTIN_ENV_KEYS
    assert not hasattr(integrations, "_github_org_store")


# ── Fix round 1, item 3: the App reads no private agent repository ──────────


async def _bound(ns: Any, org: str, coro: Any) -> Any:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    scope = clear_tenant()
    bind_tenant(org)
    try:
        async with tenant_engine_scope(_app_dsn(ns)):
            return await coro()
    finally:
        release_tenant(scope)


async def test_item3_the_agent_status_is_for_each_use(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """An App org reads pull requests, and NOT a private agent repo. So the
    agent wizard's GitHub step stays open for it, and closes for a token org."""
    from acb_common import get_settings

    # The App is ON, so the App org IS connected for pull requests.
    monkeypatch.setattr(get_settings(), "github_app_enabled", True, raising=False)
    _seed_app_row(ns, ns.org_a)
    await _connect(ns, ns.org_b, ADMIN_B, "DEV-B")
    for org, email, want in ((ns.org_a, ADMIN_A, False), (ns.org_b, ADMIN_B, True)):
        res = await _call(ns, _user(email, org), "GET", "/integrations/status",
                          params={"agent": "task-manager"})
        assert res.status_code == 200, res.text
        (github,) = [r for r in res.json() if r["service"] == "github"]
        assert github["configured"] is want, (org, github)
        assert github["github_coverage"]["agent_repo"] is want
    tile = await _call(ns, _user(ADMIN_A, ns.org_a), "GET", "/integrations/status")
    (row,) = [r for r in tile.json() if r["service"] == "github"]
    assert row["configured"] is True, "the tile reads the App as connected"
    assert row["github_coverage"] == {"pull_requests": True}


async def test_item3_the_config_fetch_of_an_app_org_names_a_private_repo(ns, app_engine) -> None:  # noqa: F811
    from fastapi import HTTPException
    from gateway.routes import agent

    _seed_app_row(ns, ns.org_a)
    with pytest.raises(HTTPException) as private:
        await _bound(ns, ns.org_a, lambda: agent.get_agent_config(
            repo="octo/private-agent", user=_user(ADMIN_A, ns.org_a)))
    assert private.value.status_code == 422
    assert private.value.detail == agent.PRIVATE_REPO_IN_APP_MODE
    with pytest.raises(HTTPException) as public:
        await _bound(ns, ns.org_a, lambda: agent.get_agent_config(
            repo="octo/public-agent", user=_user(ADMIN_A, ns.org_a)))
    assert public.value.status_code == 404, "a public repo with no config.json is a 404"
    repo_reads = [r for r in ns.github.requests if r.url.path.startswith("/repos/")]
    assert repo_reads and all("authorization" not in r.headers for r in repo_reads)


async def test_item3_an_app_org_cannot_register_a_private_agent_repo(ns, app_engine, monkeypatch) -> None:  # noqa: F811
    """Never a silent ``maf`` registration with no integrations: a clear 422."""
    from fastapi import BackgroundTasks, HTTPException
    from gateway.routes import agent

    saved: list[Any] = []
    monkeypatch.setattr(agent, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(agent, "_save_dynamic_agents", saved.append)
    _seed_app_row(ns, ns.org_a)
    req = agent.RegisterAgentRequest(name="private-bot", repo_url="octo/private-agent")
    with pytest.raises(HTTPException) as caught:
        await _bound(ns, ns.org_a, lambda: agent.register_agent(
            req, BackgroundTasks(), _user(ADMIN_A, ns.org_a)))
    assert caught.value.status_code == 422
    assert caught.value.detail == agent.PRIVATE_REPO_IN_APP_MODE
    assert saved == []
    public = agent.RegisterAgentRequest(name="public-bot", repo_url="octo/public-agent")
    entry = await _bound(ns, ns.org_a, lambda: agent.register_agent(
        public, BackgroundTasks(), _user(ADMIN_A, ns.org_a)))
    assert entry["agent_runtime"] == "maf" and len(saved) == 1
