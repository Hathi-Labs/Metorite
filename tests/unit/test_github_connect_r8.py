"""R8 — the GitHub App connect flow and the one reader (WS-54 GH-3).

Spec: ``project-docs/specs/github_integration.md`` §6.3 and §7.1 to §7.5.
A real Postgres (the promoted phase-4 catalog, as the NON-privileged role),
the real routers, and GitHub mocked at the HTTP layer (``httpx.MockTransport``).
Redis is the private server that ``GITHUB_R8_REDIS_URL`` names, or a fake
under the real ``TenantRedis`` wrapper when it is unset (CI).

* **C1** start answers the install URL with a state, and writes the pending key.
* **C2** a callback with no pending key gives ``expired`` and writes nothing.
* **C3** a state of another member or org gives ``bad_state``.
* **C4** an installation that ``GET /user/installations`` does not list gives
  ``not_yours``.
* **C5** an installation that org B holds gives ``other_org`` for org A.
* **C6** ``setup_action=request`` writes nothing.
* **C7** a good callback writes nothing, and the confirm writes one ``app`` row
  with no blob and one audit row. The user token appears nowhere.
* **C8** ``token_or_none`` returns an installation token for an ``app`` row and
  the stored token for a ``token`` row.
* **C9** a member without ``integrations:manage`` gets 403 at start and at the
  callback.
* **C11** a user who only reads a repository gets ``not_admin``, and so does a
  ``User`` account of another id.
* **C12** a confirm with no pending key writes nothing. (The cross-site half is
  the BFF's, in ``app/api/integrations/github/app/confirm/route.test.ts``.)
* **C13** in ``app`` mode ``_test_github`` calls ``GET
  /installation/repositories``, ``_github_connected`` makes no token, and
  ``_org_github_token`` returns an empty token.

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* C-M1 drop the ``pending is None`` refusal in the callback -> ``test_c2_*``.
* C-M2 drop the org and member compare of the state -> ``test_c3_*``.
* C-M3 skip the ``installations.get(iid)`` check -> ``test_c4_*``.
* C-M4 ``_administers`` returns True -> ``test_c11_*``.
* C-M5 the callback writes the row itself -> ``test_c7_*``.
* C-M6 ``token_or_none`` ignores the ``app`` mode -> ``test_c8_*``.
* C-M7 ``_github_connected`` asks for a token in ``app`` mode -> ``test_c13_*``.
* C-M8 ``_org_github_token`` reads ``token_or_none`` -> ``test_c13_*``.
* C-M9 drop ``require_permission`` from the callback -> ``test_c9_*``.

Fix round 1, each one red when run:

* 9-M1 ``_state_matches`` skips the nonce compare ->
  ``test_item9_a_state_of_an_older_start_is_bad_state``.
* 9-M2 an empty ``state`` counts as absent (``if state:``) ->
  ``test_item9_a_state_that_github_sends_empty_is_bad_state``.
* F4-M1 the start writes the pending key with ``nx=True`` again ->
  ``test_f4_a_cancelled_confirm_does_not_expire_the_next_connect``.
* F5-M3 ``installation_of`` ignores the flag ->
  ``test_f5_the_flag_off_stops_every_app_reader_and_keeps_a_token_row``.

Run (real Postgres, private Redis)::

    TENANT_LADDER_DATABASE_URL=... GITHUB_R8_REDIS_URL=redis://127.0.0.1:<port>/0 \\
        uv run pytest tests/unit/test_github_connect_r8.py -v -rs
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import structlog

pytest.importorskip("sqlalchemy")

from tests.unit import _github_r8 as g
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient

ADMIN_A = "admin-a@gh3-r8.test"
ADMIN_B = "admin-b@gh3-r8.test"
MEMBER_A = "member-a@gh3-r8.test"

#: The user tokens that the fake GitHub hands out. None may reach a row,
#: a Redis value, a log line or an answer.
USER_TOKENS = {
    "CODE-ADMIN": "ghu_USER_TOKEN_OF_THE_ADMIN_must_not_leak",
    "CODE-READER": "ghu_USER_TOKEN_OF_A_READER_must_not_leak",
    "CODE-OWNER": "ghu_USER_TOKEN_OF_THE_ACCOUNT_OWNER",
    "CODE-STRANGER": "ghu_USER_TOKEN_OF_ANOTHER_USER",
}

INSTALL_ORG = 1001       # on the GitHub organization `acme`
INSTALL_USER = 2002      # on the personal account `octo` (id 77)
INSTALL_SUSPENDED = 3003


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_requests = 0

    def _user_token(self, request: httpx.Request) -> str:
        return request.headers.get("authorization", "").removeprefix("Bearer ").strip()

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "github.com" and path == "/login/oauth/access_token":
            form = dict(httpx.QueryParams(request.content.decode()))
            token = USER_TOKENS.get(form.get("code", ""))
            if token and form.get("client_id") == "Iv23.r8-client":
                return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})
            return httpx.Response(200, json={"error": "bad_verification_code"})
        assert host == "api.github.com", host
        token = self._user_token(request)
        acme = {"login": "acme", "type": "Organization", "id": 50}
        octo = {"login": "octo", "type": "User", "id": 77}
        if path == "/user/installations":
            lists = {
                USER_TOKENS["CODE-ADMIN"]: [{"id": INSTALL_ORG, "account": acme},
                                            {"id": INSTALL_SUSPENDED, "account": acme}],
                USER_TOKENS["CODE-READER"]: [{"id": INSTALL_ORG, "account": acme}],
                USER_TOKENS["CODE-OWNER"]: [{"id": INSTALL_USER, "account": octo}],
                USER_TOKENS["CODE-STRANGER"]: [{"id": INSTALL_USER, "account": octo}],
            }
            if token not in lists:
                return httpx.Response(401, json={})
            return httpx.Response(200, json={"total_count": 1, "installations": lists[token]})
        if path == "/user":
            ids = {USER_TOKENS["CODE-OWNER"]: 77, USER_TOKENS["CODE-STRANGER"]: 78,
                   USER_TOKENS["CODE-ADMIN"]: 9}
            return httpx.Response(200, json={"id": ids.get(token, 0), "login": "x"})
        if path == "/user/memberships/orgs/acme":
            if token == USER_TOKENS["CODE-ADMIN"]:
                return httpx.Response(200, json={"role": "admin", "state": "active"})
            if token == USER_TOKENS["CODE-READER"]:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(403, json={})
        if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
            self.token_requests += 1
            iid = path.split("/")[3]
            return httpx.Response(201, json={
                "token": f"ghs_INSTALLATION_TOKEN_{iid}",
                "expires_at": "2099-01-01T00:00:00Z"})
        if path.startswith("/app/installations/"):
            assert token.startswith("ey"), "the installation read must carry the App JWT"
            iid = int(path.split("/")[3])
            account = octo if iid == INSTALL_USER else acme
            return httpx.Response(200, json={
                "id": iid, "account": account, "repository_selection": "selected",
                "permissions": {"pull_requests": "read", "checks": "read"},
                "suspended_at": "2026-10-01T00:00:00Z" if iid == INSTALL_SUSPENDED else None,
            })
        if path == "/installation/repositories":
            return httpx.Response(200, json={"total_count": 1, "repositories": []})
        return httpx.Response(404, json={})

    def sent(self) -> str:
        return "\n".join(f"{r.url} {dict(r.headers)} {r.content!r}" for r in self.requests)


@pytest.fixture
def world(promoted, monkeypatch, tmp_path):  # noqa: F811
    import acb_audit
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    g.configure_app(monkeypatch, g.make_private_key(tmp_path))
    settings = get_settings()
    monkeypatch.setattr(settings, "acb_master_key", "gh3-r8-master-key", raising=False)
    monkeypatch.setattr(settings, "database_url",
                        promoted.admin_engine.url.render_as_string(hide_password=False),
                        raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    monkeypatch.setattr(settings, "gateway_session_secret", "gh3-r8-state-secret-0123456789abcdef",
                        raising=False)
    fake = FakeGitHub()
    g.patch_github(monkeypatch, fake.handle, REAL_CLIENT)
    events: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", events.append)
    redis = g.use_redis(monkeypatch)
    g.clear_github(promoted)

    class W:
        pass

    w = W()
    w.p, w.gh, w.audit, w.redis = promoted, fake, events, redis
    yield w
    g.clear_github(promoted)


def _routers() -> list[Any]:
    from gateway.routes import integrations
    from gateway.routes.github import connect

    return [connect.router, integrations.router]


async def _call(w: Any, who: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    return await g.call(w.p, who, _routers(), method, path, real_client=REAL_CLIENT, **kwargs)


def _code_of(resp: httpx.Response) -> str:
    assert resp.status_code == 302, (resp.status_code, resp.text)
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert urlparse(resp.headers["location"]).path == "/integrations"
    return query["github"][0]


def _github_rows(w: Any, org: str) -> list[Any]:
    return g.rows(w.p,
                  "SELECT status, provider_meta, credentials_encrypted, connected_by "
                  "FROM integration_connections WHERE organization_id = CAST(:o AS uuid) "
                  "AND provider = 'github'", o=org)


async def _start(w: Any, who: Any) -> str:
    resp = await _call(w, who, "POST", "/integrations/github/app/start")
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    return parse_qs(urlparse(url).query)["state"][0]


async def _callback(w: Any, who: Any, **params: Any) -> httpx.Response:
    return await _call(w, who, "GET", "/integrations/github/app/callback",
                       params={k: str(v) for k, v in params.items()})


# ── C1 ──────────────────────────────────────────────────────────────────────


async def test_c1_start_answers_the_install_url_and_writes_the_pending_key(world) -> None:
    w = world
    resp = await _call(w, g.user(ADMIN_A, w.p.org_a), "POST", "/integrations/github/app/start")
    assert resp.status_code == 200, resp.text
    url = urlparse(resp.json()["url"])
    assert (url.scheme, url.netloc, url.path) == (
        "https", "github.com", "/apps/metorite-test/installations/new")
    assert parse_qs(url.query)["state"][0]
    key = f"cc:{w.p.org_a}:github:connect:{ADMIN_A}"
    items = w.redis.items()
    assert key in items and json.loads(items[key])["stage"] == "start"
    assert 0 < w.redis.ttl(key) <= 600
    assert w.gh.requests == []


async def test_the_flag_off_answers_404_everywhere(world, monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "github_app_enabled", False)
    for method, path in (("POST", "/integrations/github/app/start"),
                         ("GET", "/integrations/github/app/callback"),
                         ("POST", "/integrations/github/app/confirm")):
        resp = await _call(world, g.user(ADMIN_A, world.p.org_a), method, path)
        assert resp.status_code == 404, (path, resp.status_code)


# ── C2, C3 ──────────────────────────────────────────────────────────────────


async def test_c2_a_callback_with_no_pending_key_is_expired(world) -> None:
    w = world
    resp = await _callback(w, g.user(ADMIN_A, w.p.org_a), code="CODE-ADMIN",
                           installation_id=INSTALL_ORG, setup_action="install")
    assert _code_of(resp) == "expired"
    assert _github_rows(w, w.p.org_a) == []
    assert w.gh.requests == [], "an expired callback must call nothing outside"


async def test_c3_a_state_of_another_member_or_org_is_bad_state(world) -> None:
    from gateway.routes.email.transport.signing import sign_state

    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    for state in (
        sign_state(purpose="github_install", org=w.p.org_a, member=MEMBER_A),
        sign_state(purpose="github_install", org=w.p.org_b, member=ADMIN_A),
        sign_state(purpose="crm.zoho.connect", org=w.p.org_a, member=ADMIN_A),
        "garbage.state",
    ):
        await _start(w, admin)
        resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                               state=state)
        assert _code_of(resp) == "bad_state", state
    assert _github_rows(w, w.p.org_a) == []
    assert w.gh.requests == []


# ── C4, C6, C11 ─────────────────────────────────────────────────────────────


async def test_c4_an_installation_the_user_cannot_reach_is_not_yours(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=999999, state=state)
    assert _code_of(resp) == "not_yours"
    assert _github_rows(w, w.p.org_a) == []


async def test_c6_an_install_request_writes_nothing(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", setup_action="request", state=state)
    assert _code_of(resp) == "requested"
    assert _github_rows(w, w.p.org_a) == [] and w.gh.requests == []


async def test_c11_a_reader_and_another_user_are_not_admin(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-READER", installation_id=INSTALL_ORG, state=state)
    assert _code_of(resp) == "not_admin"
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-STRANGER", installation_id=INSTALL_USER,
                           state=state)
    assert _code_of(resp) == "not_admin"
    # The owner of the personal account passes.
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-OWNER", installation_id=INSTALL_USER,
                           state=state)
    assert _code_of(resp) == "confirm"
    assert _github_rows(w, w.p.org_a) == []


async def test_a_suspended_installation_is_refused(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_SUSPENDED,
                           state=state)
    assert _code_of(resp) == "suspended"


async def test_a_callback_with_no_state_still_needs_the_pending_key(world) -> None:
    """U1: the state may not come back. The pending connect binds the flow."""
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG)
    assert _code_of(resp) == "confirm"
    # The pending key was taken once. A replay finds nothing.
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG)
    assert _code_of(resp) == "expired"


# ── C7 ──────────────────────────────────────────────────────────────────────


async def test_c7_the_callback_writes_nothing_and_the_confirm_writes_one_app_row(
    world, caplog,
) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    caplog.set_level(logging.DEBUG)
    answers: list[str] = []
    with structlog.testing.capture_logs() as logs:
        state = await _start(w, admin)
        resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                               state=state, setup_action="install")
        answers.append(resp.headers.get("location", "") + resp.text)
        assert _code_of(resp) == "confirm"
        assert parse_qs(urlparse(resp.headers["location"]).query)["account"] == ["acme"]
        assert _github_rows(w, w.p.org_a) == [], "the callback must write nothing"
        values_after_callback = json.dumps(w.redis.items())
        confirm = await _call(w, admin, "POST", "/integrations/github/app/confirm")
        answers.append(confirm.text)
    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == {"status": "connected", "account_login": "acme",
                              "account_type": "Organization",
                              "repository_selection": "selected"}
    (row,) = _github_rows(w, w.p.org_a)
    meta = row.provider_meta
    assert row.status == "connected" and row.credentials_encrypted is None
    assert row.connected_by == ADMIN_A
    assert (meta["auth"], meta["installation_id"], meta["account_login"]) == (
        "app", str(INSTALL_ORG), "acme")
    assert meta["account_type"] == "Organization" and meta["app_slug"] == "metorite-test"
    (event,) = [e for e in w.audit if e.action == "github.app.connected"]
    assert event.payload["installation_id"] == INSTALL_ORG
    assert event.payload["account_login"] == "acme"
    assert event.organization_id == w.p.org_a
    # The user token reaches nothing: no row, no Redis value, no log, no answer.
    everywhere = "\n".join([
        json.dumps(meta), values_after_callback, json.dumps(w.redis.items()),
        json.dumps(logs, default=str), caplog.text, *answers,
        json.dumps([e.payload for e in w.audit], default=str),
    ])
    for token in USER_TOKENS.values():
        assert token not in everywhere
    assert "r8-client-secret-never-sent-out" not in everywhere
    # The pending key is spent.
    assert not [k for k in w.redis.items() if k.endswith(f"github:connect:{ADMIN_A}")]


async def test_c7_a_token_row_becomes_an_app_row(world) -> None:
    from gateway.connections import github as github_connection

    w = world
    from acb_common.db import tenant_session

    from tests.unit._tenant_ladder import tenant_engine_scope

    async with tenant_engine_scope(g.app_dsn(w.p)), tenant_session(w.p.org_a) as db:
        await github_connection.save_token(
            db, w.p.org_a, access_token="ghp_OLD_PASTED_TOKEN", login="old", scopes=("repo",),
            connected_by=ADMIN_A)
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    assert _code_of(await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                                    state=state)) == "confirm"
    assert (await _call(w, admin, "POST", "/integrations/github/app/confirm")).status_code == 200
    (row,) = _github_rows(w, w.p.org_a)
    assert row.credentials_encrypted is None and row.provider_meta["auth"] == "app"


# ── C5, C12 ─────────────────────────────────────────────────────────────────


async def test_c5_an_installation_held_by_org_b_is_other_org_for_org_a(world) -> None:
    w = world
    g.seed_github_row(w.p, w.p.org_b, INSTALL_ORG)
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    assert _code_of(await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                                    state=state)) == "confirm"
    confirm = await _call(w, admin, "POST", "/integrations/github/app/confirm")
    assert confirm.status_code == 409 and confirm.json()["detail"] == "other_org"
    assert _github_rows(w, w.p.org_a) == []
    (row_b,) = _github_rows(w, w.p.org_b)
    assert row_b.provider_meta["installation_id"] == str(INSTALL_ORG)


async def test_c12_a_confirm_with_no_pending_key_writes_nothing(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    resp = await _call(w, admin, "POST", "/integrations/github/app/confirm")
    assert resp.status_code == 409 and resp.json()["detail"] == "expired"
    # A start alone is not a confirm either.
    await _start(w, admin)
    resp = await _call(w, admin, "POST", "/integrations/github/app/confirm")
    assert resp.status_code == 409
    assert _github_rows(w, w.p.org_a) == [] and w.audit == []


async def test_a_confirm_of_another_member_finds_nothing(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    assert _code_of(await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                                    state=state)) == "confirm"
    other = g.user("admin-two-a@gh3-r8.test", w.p.org_a)
    resp = await _call(w, other, "POST", "/integrations/github/app/confirm")
    assert resp.status_code == 409
    assert _github_rows(w, w.p.org_a) == []


# ── C9 ──────────────────────────────────────────────────────────────────────


async def test_c9_a_member_without_manage_gets_403(world) -> None:
    w = world
    member = g.user(MEMBER_A, w.p.org_a, manage=False)
    for method, path in (("POST", "/integrations/github/app/start"),
                         ("GET", "/integrations/github/app/callback"),
                         ("POST", "/integrations/github/app/confirm")):
        resp = await _call(w, member, method, path,
                           params={"code": "CODE-ADMIN", "installation_id": str(INSTALL_ORG)})
        assert resp.status_code == 403, (path, resp.status_code)
    assert w.redis.items() == {} and w.gh.requests == []


# ── C8, C13 ─────────────────────────────────────────────────────────────────


async def test_c8_one_reader_answers_both_modes(world) -> None:
    from gateway.connections import github as github_connection

    from tests.unit._tenant_ladder import tenant_engine_scope

    w = world
    g.seed_github_row(w.p, w.p.org_a, INSTALL_ORG)
    from acb_common.db import tenant_session

    async with tenant_engine_scope(g.app_dsn(w.p)):
        assert await github_connection.token_or_none(w.p.org_a) == \
            f"ghs_INSTALLATION_TOKEN_{INSTALL_ORG}"
        async with tenant_session(w.p.org_b) as db:
            await github_connection.save_token(
                db, w.p.org_b, access_token="ghp_PASTED_TOKEN_OF_B", login="b",
                scopes=("repo",), connected_by=ADMIN_B)
        assert await github_connection.token_or_none(w.p.org_b) == "ghp_PASTED_TOKEN_OF_B"
        # A suspended or disconnected app row gives nothing and asks GitHub nothing.
        g.seed_github_row(w.p, w.p.org_a, INSTALL_ORG, status="needs_reconnect")
        before = w.gh.token_requests
        assert await github_connection.token_or_none(w.p.org_a) is None
        assert w.gh.token_requests == before


async def test_c13_the_three_callers_in_app_mode(world) -> None:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.routes import agent, integrations

    from tests.unit._tenant_ladder import tenant_engine_scope

    w = world
    g.seed_github_row(w.p, w.p.org_a, INSTALL_ORG)
    scope = clear_tenant()
    bind_tenant(w.p.org_a)
    try:
        async with tenant_engine_scope(g.app_dsn(w.p)):
            assert await integrations._github_connected() is True
            assert w.gh.token_requests == 0, "a status read must make no token"
            assert await agent._org_github_token() == ""
            assert w.gh.token_requests == 0
            result = await integrations._test_github(g.user(ADMIN_A, w.p.org_a))
    finally:
        release_tenant(scope)
    assert result["ok"] is True, result
    paths = [r.url.path for r in w.gh.requests]
    assert "/installation/repositories" in paths
    assert "/user" not in paths


async def test_disconnect_of_an_app_row_gives_the_uninstall_link(world) -> None:
    w = world
    g.seed_github_row(w.p, w.p.org_a, INSTALL_ORG)
    resp = await _call(w, g.user(ADMIN_A, w.p.org_a), "DELETE", "/integrations/github")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["installation_url"] == (
        f"https://github.com/organizations/acct-{INSTALL_ORG}/settings/installations/{INSTALL_ORG}")
    assert body["revoked_at_github"] is False
    (row,) = _github_rows(w, w.p.org_a)
    assert row.status == "disconnected"
    assert w.gh.requests == [], "a disconnect does not uninstall at GitHub (GH-E10)"


async def test_the_account_card_reads_the_app_row(world) -> None:
    w = world
    g.seed_github_row(w.p, w.p.org_a, INSTALL_ORG)
    resp = await _call(w, g.user(ADMIN_A, w.p.org_a), "GET", "/integrations/github/account")
    assert resp.status_code == 200
    body = resp.json()
    assert (body["connected"], body["auth"], body["login"]) == (True, "app", f"acct-{INSTALL_ORG}")
    assert "token" not in json.dumps({k: v for k, v in body.items() if k.startswith("gh_")})


# ── Fix round 1: the state nonce (item 9), the pending key (F4), the switch (F5)


async def test_item9_a_state_of_an_older_start_is_bad_state(world) -> None:
    """A state that verifies for this org and member, but of an OLDER start,
    does not match the nonce of the pending connect."""
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    older = await _start(w, admin)
    newer = await _start(w, admin)
    assert older != newer
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                           state=older)
    assert _code_of(resp) == "bad_state"
    assert w.gh.requests == []
    await _start(w, admin)
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                           state=state)
    assert _code_of(resp) == "confirm"


async def test_item9_a_state_that_github_sends_empty_is_bad_state(world) -> None:
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    await _start(w, admin)
    resp = await _call(w, admin, "GET",
                       f"/integrations/github/app/callback?code=CODE-ADMIN"
                       f"&installation_id={INSTALL_ORG}&state=")
    assert _code_of(resp) == "bad_state"


async def test_f4_a_cancelled_confirm_does_not_expire_the_next_connect(world) -> None:
    """The admin reached the confirm and pressed Cancel. The next Connect
    starts clean, and its callback reaches the confirm again."""
    w = world
    admin = g.user(ADMIN_A, w.p.org_a)
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                           state=state)
    assert _code_of(resp) == "confirm"
    # No confirm (the dialog was cancelled). Connect again.
    state = await _start(w, admin)
    resp = await _callback(w, admin, code="CODE-ADMIN", installation_id=INSTALL_ORG,
                           state=state)
    assert _code_of(resp) == "confirm"


async def test_f5_the_flag_off_stops_every_app_reader_and_keeps_a_token_row(
    world, monkeypatch,
) -> None:
    from acb_common import get_settings
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from gateway.connections import github as github_connection
    from gateway.db import tenant_session
    from gateway.routes import integrations
    from gateway.routes.projects import refs

    from tests.unit._tenant_ladder import tenant_engine_scope

    w = world
    g.seed_github_row(w.p, w.p.org_a, INSTALL_ORG)
    async with tenant_engine_scope(g.app_dsn(w.p)), tenant_session(w.p.org_b) as db:
        await github_connection.save_token(
            db, w.p.org_b, access_token="ghp_TOKEN_ROW_OF_B", login="b",
            scopes=("repo",), connected_by=ADMIN_B)
    monkeypatch.setattr(get_settings(), "github_app_enabled", False)
    scope = clear_tenant()
    bind_tenant(w.p.org_a)
    try:
        async with tenant_engine_scope(g.app_dsn(w.p)):
            assert await github_connection.token_or_none(w.p.org_a) is None
            assert await integrations._github_connected() is False
            result = await integrations._test_github(g.user(ADMIN_A, w.p.org_a))
            started = await refs._refresh_checks([{
                "provider": "github", "kind": "pull_request", "ref": "acme/w#1",
                "head_sha": "a" * 40, "state": "open", "checks_state": None,
                "checks_fetched_at": None}])
            # A token row of another org keeps working.
            assert await github_connection.token_or_none(w.p.org_b) == "ghp_TOKEN_ROW_OF_B"
    finally:
        release_tenant(scope)
    assert result == {"ok": False, "detail": "The GitHub App is turned off on this server."}
    assert started == 0
    assert w.gh.token_requests == 0, "no installation token while the switch is off"
