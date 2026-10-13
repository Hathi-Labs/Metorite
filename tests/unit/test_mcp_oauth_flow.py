"""MCP OAuth for an org connection: the flow (WS-54 IN-8a, D98).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.3 to §3.6.6 and
§3.6.11, rows O4, O5 and O12.

Mutation table (each mutation is made by hand, and the named test must fail).
This table was committed BEFORE the code, so it named the fence first.

**Result, 2026-10-13: 13 of 13 killed.** M3 survived the first run. The
tenant prefix of the Redis key is a second wall, so a state of org A found no
pending flow in org B. The test now plants one in org B, so only the org of
the state can stop the callback. M12 needed the same kind of repair: the
pending flow also names the member, so the test now checks that the refused
call did not take the flow.

| # | Mutation | Test that fails |
|---|---|---|
| M1 | the revoke hop calls ``outbound_guard.request`` (it allows ``http``) and not ``request_https`` | ``test_mcp_oauth_guard.py::test_no_oauth_module_fetches_by_itself``. ``[revoke-http]`` stays green, because ``_https_443`` refuses ``http`` before the fetch |
| M2 | discovery sends to the ``resource_metadata`` URL without a check (a private address goes out) | ``test_mcp_oauth_guard.py::test_each_hop_refuses[prm-rfc1918]`` |
| M3 | the callback does not compare the org of the state with the session | ``test_mcp_oauth_state.py::test_a_state_of_another_org_is_400`` |
| M4 | the callback reads the pending flow with GET and does not delete it | ``test_mcp_oauth_state.py::test_a_replayed_state_is_400_and_sends_no_token_request`` |
| M5 | the consent URL sends ``plain`` when the server offers it | ``test_mcp_oauth_flow.py::test_the_consent_url_sends_s256_only`` |
| M6 | the callback skips the ``iss`` check | ``test_mcp_oauth_flow.py::test_an_iss_mismatch_is_400_before_the_token_request`` |
| M7 | the DCR ``client_secret`` goes in ``provider_meta`` | ``test_mcp_oauth_secrets.py::test_no_secret_reaches_a_log_a_response_a_url_or_a_column`` |
| M8 | the flag dependency is gone from the start and the callback | ``test_mcp_oauth_flow.py::test_flag_off_every_route_is_404`` |
| M9 | the runtime reader ignores the flag, so an OAuth row reaches a run | ``test_mcp_oauth_flow.py::test_flag_off_a_run_leaves_out_an_oauth_row`` |
| M10 | the row lock of the store write (``_LOCK_ROW_SQL``) loses its org predicate | ``test_mcp_oauth_org_scope_r8.py::test_the_store_predicate_holds_on_a_session_that_bypasses_rls`` |
| M11 | the ``client_id`` of the CIMD document is not its own URL | ``test_mcp_oauth_cimd.py::test_the_client_id_is_the_url_of_the_document`` |
| M12 | the callback does not compare the member of the state with the session | ``test_mcp_oauth_state.py::test_a_state_of_another_member_is_400`` |
| M13 | the code exchange sends no ``code_verifier`` | ``test_mcp_oauth_flow.py::test_the_whole_flow_saves_the_credential_as_the_admin`` |

Rows M14 to M21 fence the owner decisions on the review of #931 (2026-10-13).
They were written with the code. **Result, 2026-10-13: 9 of 9 killed, and
M1 to M13 run again on the same head: 13 of 13 killed.**

| # | Mutation | Test that fails |
|---|---|---|
| M14 | a register of an OAuth name replaces the row (the OAuth branch of ``mcp.save`` is gone) | ``test_mcp_oauth_org_scope_r8.py::test_a_register_keeps_an_oauth_row_and_merges_its_public_keys`` |
| M15 | a register may change the URL of an OAuth row | ``test_mcp_oauth_org_scope_r8.py::test_a_register_with_a_new_url_is_409_and_changes_nothing`` |
| M16 | a delete revokes nothing | ``test_mcp_oauth_org_scope_r8.py::test_a_delete_revokes_the_refresh_token_after_the_commit`` |
| M17 | the Test of an OAuth server sends no bearer | ``test_mcp_oauth_org_scope_r8.py::test_the_test_button_sends_the_stored_bearer`` |
| M18 | ``expires_in`` is not cut to ten years | ``test_mcp_oauth_flow.py::test_a_huge_expires_in_is_cut_to_ten_years`` |
| M19 | ``expires_in = 0`` reads as "never expires" again | ``test_mcp_oauth_flow.py::test_a_zero_expires_in_has_ended_and_does_not_mean_never`` |
| M20 | the O2 fence misses a fully qualified ``gateway.outbound_guard.request`` | ``test_mcp_oauth_guard.py::test_the_fence_catches_each_banned_form`` |
| M21 | the O2 fence bans ``httpx`` only, not the other HTTP clients | ``test_mcp_oauth_guard.py::test_the_fence_catches_each_banned_form`` |
| M22 | CIMD without ``iss`` | ``test_mcp_oauth_flow.py::test_cimd_without_iss_falls_back_to_dcr`` |
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog

from tests.unit import _mcp_oauth_fakes as f

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN = "admin@a.test"


@pytest.fixture
def redis(monkeypatch) -> f.FakeRawRedis:
    return f.configure(monkeypatch)


@pytest.fixture
def world(monkeypatch, redis) -> f.World:
    return f.install(monkeypatch, f.World())


@pytest.fixture
def reg(monkeypatch, world) -> f.Registry:
    return f.fake_registry(monkeypatch, ORG_A)


def _admin() -> Any:
    return f.user(ADMIN, ORG_A)


def _token_requests(world: f.World) -> list[Any]:
    return [r for r in world.requests if r.url.path == "/token"]


# ── The control case ────────────────────────────────────────────────────────


async def test_the_whole_flow_saves_the_credential_as_the_admin(world, reg) -> None:
    url, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302, res.text
    assert res.headers["location"] == "/integrations?mcp_connected=notion"
    assert len(reg.attached) == 1
    saved = reg.attached[0]
    assert saved["org"] == ORG_A and saved["connected_by"] == ADMIN  # D98
    cred = saved["credential"]
    assert cred.access_token == f.SENTINEL_ACCESS
    assert cred.refresh_token == f.SENTINEL_REFRESH
    assert cred.client_secret == f.SENTINEL_SECRET
    assert dict(cred.provider_meta) == {
        "issuer": f.ISSUER, "token_endpoint": f"{f.ISSUER}/token",
        "revocation_endpoint": f"{f.ISSUER}/revoke", "client_id": "dcr-client-1",
        "registration": "dcr", "token_auth": "client_secret_post", "resource": f.MCP_URL,
    }
    assert saved["scopes"] == ("read", "write")
    # The consent URL carries what §3.6.4 step 8 names, and the resource.
    q = f.query_of(url)
    assert q["response_type"] == "code" and q["client_id"] == "dcr-client-1"
    assert q["redirect_uri"] == f.REDIRECT_URI and q["resource"] == f.MCP_URL
    assert q["scope"] == "read write" and q["state"] == state


async def test_a_reconnect_revokes_the_old_refresh_token_after_the_save(world, reg) -> None:
    from gateway.crm_sources.base import SourceCredential

    reg.previous = SourceCredential(
        access_token="old-access", refresh_token="old-refresh",
        expires_at=datetime.now(UTC),
        provider_meta={"revocation_endpoint": f"{f.ISSUER}/revoke",
                       "client_id": "dcr-client-1", "token_auth": "client_secret_post"},
        client_secret=f.SENTINEL_SECRET)
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302
    assert world.revoked == ["old-refresh"]


async def test_a_server_that_is_gone_saves_nothing_and_revokes_the_new_token(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    reg.url = "https://mcp.fake.test/other"  # the admin changed the URL meanwhile
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.headers["location"] == "/integrations?mcp_connect_error=server_gone"
    assert reg.attached == []
    assert world.revoked == [f.SENTINEL_REFRESH]


# ── O4: PKCE ────────────────────────────────────────────────────────────────


async def test_the_consent_url_sends_s256_only(world, reg, redis) -> None:
    assert "plain" in world.as_meta["code_challenge_methods_supported"]
    url, _, _ = await f.start_and_consent(_admin(), world)
    q = f.query_of(url)
    assert q["code_challenge_method"] == "S256"
    pending = json.loads(next(iter(redis.data.values())))
    assert q["code_challenge"] == f.s256(pending["verifier"])
    # The verifier never goes into the URL or the state.
    assert pending["verifier"] not in url


async def test_a_server_without_s256_is_refused(world, reg) -> None:
    world.as_meta["code_challenge_methods_supported"] = ["plain"]
    res = await f.call(_admin(), "POST", "/integrations/mcp/notion/oauth/start")
    assert res.status_code == 502 and res.json()["detail"] == "pkce_unsupported"
    world.as_meta.pop("code_challenge_methods_supported")
    res = await f.call(_admin(), "POST", "/integrations/mcp/notion/oauth/start")
    assert res.status_code == 502 and res.json()["detail"] == "pkce_unsupported"


async def test_a_wrong_verifier_fails_and_saves_nothing(world, reg, redis) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    (key, raw), = redis.data.items()
    record = json.loads(raw)
    record["verifier"] = "w" * 64
    redis.data[key] = json.dumps(record)
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302
    assert res.headers["location"] == "/integrations?mcp_connect_error=invalid_grant"
    assert len(_token_requests(world)) == 1
    assert reg.attached == []


# ── O5: iss ─────────────────────────────────────────────────────────────────


async def test_an_iss_mismatch_is_400_before_the_token_request(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state, iss="https://evil.fake.test")
    assert res.status_code == 400 and res.json()["detail"] == "invalid_issuer"
    assert _token_requests(world) == [] and reg.attached == []


async def test_a_missing_iss_is_400_when_the_server_says_it_sends_one(world, reg) -> None:
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state)
    assert res.status_code == 400 and res.json()["detail"] == "invalid_issuer"
    assert _token_requests(world) == []


async def test_an_iss_is_checked_also_when_the_server_does_not_promise_one(world, reg) -> None:
    world.as_meta["authorization_response_iss_parameter_supported"] = False
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state, iss="https://evil.fake.test")
    assert res.status_code == 400 and res.json()["detail"] == "invalid_issuer"
    # With no iss at all, a server that promised none goes on.
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state)
    assert res.status_code == 302 and "mcp_connected" in res.headers["location"]


async def test_a_provider_error_redirects_with_a_safe_code(world, reg) -> None:
    _, _, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), None, state, iss=f.ISSUER, error="access_denied")
    assert res.headers["location"] == "/integrations?mcp_connect_error=access_denied"
    _, _, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), None, state, iss=f.ISSUER, error="<b>no</b>")
    assert res.headers["location"] == "/integrations?mcp_connect_error=failed"


# ── Discovery and registration (§3.6.3, §3.6.4) ─────────────────────────────


async def test_cimd_comes_before_dcr(world, reg) -> None:
    world.as_meta["client_id_metadata_document_supported"] = True
    world.as_meta["token_endpoint_auth_methods_supported"] = ["none"]
    world.registration = {"token_endpoint_auth_method": "none", "client_id": f.CIMD_URL}
    url, code, state = await f.start_and_consent(_admin(), world)
    assert f.query_of(url)["client_id"] == f.CIMD_URL
    assert [r for r in world.requests if r.url.path == "/register"] == []
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302 and "mcp_connected" in res.headers["location"]
    cred = reg.attached[0]["credential"]
    assert cred.provider_meta["registration"] == "cimd" and cred.client_secret == ""


async def test_no_cimd_and_no_dcr_is_no_registration(world, reg) -> None:
    world.as_meta.pop("registration_endpoint")
    res = await f.call(_admin(), "POST", "/integrations/mcp/notion/oauth/start")
    assert res.status_code == 502 and res.json()["detail"] == "no_registration"


async def test_no_pre_registered_client_exists_yet() -> None:
    """IN-8d is an owner gate: no operator client is stored or read."""
    from gateway.connections import mcp_oauth

    assert mcp_oauth._pre_registered(None) is None  # type: ignore[arg-type]


async def test_the_fallback_uses_the_origin_when_no_metadata_is_published(world) -> None:
    """Atlassian: no ``resource_metadata``, and no protected-resource document."""
    from gateway.connections import mcp_oauth

    world.www_authenticate = "Bearer"
    world.prm = None
    origin_meta = dict(world.as_meta, issuer=f"https://{f.MCP_HOST}")
    world.fixed[(f.MCP_HOST, "/.well-known/oauth-authorization-server")] = (
        httpx.Response(200, json=origin_meta))
    plan = await mcp_oauth.discover(f.MCP_URL)
    assert plan.issuer == f"https://{f.MCP_HOST}" and plan.resource == f.MCP_URL
    paths = [r.url.path for r in world.requests if r.headers["host"] == f.MCP_HOST]
    assert paths == ["/mcp", "/.well-known/oauth-protected-resource/mcp",
                     "/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-authorization-server"]


async def test_a_path_issuer_uses_the_path_inserted_form_first(world) -> None:
    """GitHub: the auth server has a path, and the root form is a 404."""
    from gateway.connections import mcp_oauth

    issuer = f"{f.ISSUER}/login/oauth"
    assert world.prm is not None
    world.prm["authorization_servers"] = [issuer]
    world.fixed[(f.AUTH_HOST, "/.well-known/oauth-authorization-server/login/oauth")] = (
        httpx.Response(200, json=dict(world.as_meta, issuer=issuer)))
    plan = await mcp_oauth.discover(f.MCP_URL)
    assert plan.issuer == issuer
    asked = [r.url.path for r in world.requests if r.headers["host"] == f.AUTH_HOST]
    assert asked == ["/.well-known/oauth-authorization-server/login/oauth"]


async def test_a_resource_that_is_not_the_server_is_refused(world) -> None:
    from gateway.connections import mcp_oauth

    assert world.prm is not None
    world.prm["resource"] = "https://other.fake.test/mcp"
    with pytest.raises(mcp_oauth.OAuthFailed) as err:
        await mcp_oauth.discover(f.MCP_URL)
    assert err.value.code == "resource_mismatch"


async def test_an_issuer_that_is_not_the_auth_server_is_refused(world) -> None:
    from gateway.connections import mcp_oauth

    world.as_meta["issuer"] = "https://evil.fake.test"
    with pytest.raises(mcp_oauth.OAuthFailed) as err:
        await mcp_oauth.discover(f.MCP_URL)
    assert err.value.code == "issuer_mismatch"


async def test_an_unknown_server_is_404(world, reg) -> None:
    res = await f.call(_admin(), "POST", "/integrations/mcp/linear/oauth/start")
    assert res.status_code == 404
    res = await f.call(_admin(), "POST", "/integrations/mcp/Bad_Name/oauth/start")
    assert res.status_code == 404


async def test_start_and_callback_need_integrations_manage(world, reg) -> None:
    member = f.user("member@a.test", ORG_A, ["feature:integrations", "admin:members:read"])
    res = await f.call(member, "POST", "/integrations/mcp/notion/oauth/start")
    assert res.status_code == 403
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(member, code, state, iss=f.ISSUER)
    assert res.status_code == 403 and reg.attached == []


# ── O12: the flag ───────────────────────────────────────────────────────────


async def test_flag_off_every_route_is_404(monkeypatch, world, reg) -> None:
    from acb_common import get_settings

    _, code, state = await f.start_and_consent(_admin(), world)
    monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", False, raising=False)
    nobody = f.user("x@a.test", ORG_A, [])
    for who in (_admin(), nobody):
        res = await f.call(who, "POST", "/integrations/mcp/notion/oauth/start")
        assert res.status_code == 404, res.text
        res = await f.finish(who, code, state, iss=f.ISSUER)
        assert res.status_code == 404, res.text
        res = await f.call(who, "GET", "/integrations/mcp/oauth/client-metadata")
        assert res.status_code == 404, res.text
    assert _token_requests(world) == [] and reg.attached == []


async def test_the_flag_reads_true_only_for_a_real_true(monkeypatch) -> None:
    from acb_common import get_settings
    from gateway.connections import mcp_oauth

    for value, expected in ((True, True), (False, False), ("true", False), (1, False)):
        monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", value, raising=False)
        assert mcp_oauth.oauth_enabled() is expected, value


def _conn(name: str, *, oauth: bool) -> Any:
    from gateway.connections import store

    meta: dict[str, Any] = {"name": name, "url": f.MCP_URL, "transport": "http-sse",
                            "agent_scope": ["*"]}
    if oauth:
        meta["auth"] = "oauth"
    return store.Connection(
        id=f"00000000-0000-0000-0000-0000000000{len(name):02d}", organization_id=ORG_A,
        provider=f"mcp:{name}", owning_app="integrations", scope_kind="org",
        member_email=None, status="connected", provider_meta=meta,
        scopes=(), connected_by=ADMIN, last_sync_at=None, last_error=None,
        created_at=None, updated_at=None)


@pytest.fixture
def runtime(monkeypatch) -> dict[str, Any]:
    """The runtime reader over two rows: a header server and an OAuth server."""
    from contextlib import asynccontextmanager

    from gateway.connections import mcp, store
    from gateway.crm_sources.base import SourceCredential

    f.configure(monkeypatch)
    state: dict[str, Any] = {
        "conns": [_conn("plain", oauth=False), _conn("notion", oauth=True)],
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }

    @asynccontextmanager
    async def _session(org: str):
        yield object()

    async def _list(db, org, prefix, app, **kw):
        return list(state["conns"])

    async def _headers(db, org, prefix, app, **kw):
        return {"mcp:plain": {"X-Key": "header-value"}}

    async def _cred(db, org, provider, app, **kw):
        assert provider == "mcp:notion"
        return SourceCredential(access_token=f.SENTINEL_ACCESS, refresh_token="r",
                                expires_at=state["expires_at"])

    async def _no_legacy(db, org):
        return 0

    monkeypatch.setattr(mcp, "tenant_session", _session)
    monkeypatch.setattr(mcp, "migrate_legacy", _no_legacy)
    monkeypatch.setattr(store, "list_by_prefix", _list)
    monkeypatch.setattr(store, "load_headers_by_prefix", _headers)
    monkeypatch.setattr(store, "load_credential", _cred)
    return state


async def test_flag_off_a_run_leaves_out_an_oauth_row(monkeypatch, runtime) -> None:
    from acb_common import get_settings
    from gateway.connections import mcp

    monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", False, raising=False)
    with structlog.testing.capture_logs() as logs:
        servers = await mcp.servers_for_org(ORG_A)
    assert [s.name for s in servers] == ["plain"]
    assert any(e.get("event") == "mcp.oauth_disabled" and e.get("name") == "notion"
               for e in logs)
    # The console still lists the row, with no header, and says it is OAuth.
    listed = {s.name: s for s in await mcp.list_servers(ORG_A)}
    assert listed["notion"].oauth is True and listed["notion"].header_names == ()
    assert listed["notion"].connected_by == ADMIN


async def test_flag_on_a_run_sends_the_bearer_token(runtime) -> None:
    from gateway.connections import mcp

    servers = {s.name: s for s in await mcp.servers_for_org(ORG_A)}
    assert servers["notion"].headers == {"Authorization": f"Bearer {f.SENTINEL_ACCESS}"}
    assert servers["plain"].headers == {"X-Key": "header-value"}
    assert f.SENTINEL_ACCESS not in repr(servers["notion"])


async def test_an_expired_token_is_left_out_until_in8b_refreshes_it(runtime) -> None:
    from gateway.connections import mcp

    runtime["expires_at"] = datetime.now(UTC) + timedelta(seconds=30)
    with structlog.testing.capture_logs() as logs:
        servers = await mcp.servers_for_org(ORG_A)
    assert [s.name for s in servers] == ["plain"]
    assert any(e.get("event") == "mcp.oauth_expired" for e in logs)


# ── Review of #931: expires_in (P2 4) ───────────────────────────────────────


def _pending() -> Any:
    from gateway.connections import mcp_oauth

    return mcp_oauth.Pending(
        name="notion", member=ADMIN, server_url=f.MCP_URL, resource=f.MCP_URL,
        issuer=f.ISSUER, iss_required=True, token_endpoint=f"{f.ISSUER}/token",
        revocation_endpoint=None, client_id="dcr-client-1", registration="dcr",
        token_auth="client_secret_post", scope="read write", verifier="v" * 64,
        client_secret=f.SENTINEL_SECRET)


async def _exchange(world: f.World, expires_in: Any) -> Any:
    from gateway.connections import mcp_oauth

    pending = _pending()
    world.expires_in = expires_in
    world.grants["c"] = f.Grant(challenge=f.s256(pending.verifier), method="S256",
                                client_id="dcr-client-1", redirect_uri=f.REDIRECT_URI,
                                resource=f.MCP_URL)
    now = datetime(2026, 10, 13, tzinfo=UTC)
    return now, await mcp_oauth.exchange_code(pending, "c", now=now)


TEN_YEARS = timedelta(days=3650)


async def test_a_huge_expires_in_is_cut_to_ten_years(world) -> None:
    now, cred = await _exchange(world, 10**12)
    assert cred.expires_at == now + TEN_YEARS


async def test_a_zero_expires_in_has_ended_and_does_not_mean_never(world) -> None:
    now, cred = await _exchange(world, 0)
    assert cred.expires_at == now


async def test_a_missing_expires_in_means_no_expiry(world) -> None:
    now, cred = await _exchange(world, None)
    assert cred.expires_at == now + TEN_YEARS


@pytest.mark.parametrize("bad", [-5, -1])
async def test_a_negative_expires_in_is_exchange_failed(world, bad: int) -> None:
    from gateway.connections import mcp_oauth

    with pytest.raises(mcp_oauth.OAuthFailed) as err:
        await _exchange(world, bad)
    assert err.value.code == "exchange_failed"


async def test_a_huge_expires_in_through_the_callback_is_no_500(world, reg) -> None:
    world.expires_in = 10**12
    _, code, state = await f.start_and_consent(_admin(), world)
    res = await f.finish(_admin(), code, state, iss=f.ISSUER)
    assert res.status_code == 302 and "mcp_connected" in res.headers["location"]


# ── Review of #931: CIMD only with iss (hardening 6) ────────────────────────


async def test_cimd_without_iss_falls_back_to_dcr(world, reg) -> None:
    world.as_meta["client_id_metadata_document_supported"] = True
    world.as_meta["authorization_response_iss_parameter_supported"] = False
    url, _, _ = await f.start_and_consent(_admin(), world)
    assert f.query_of(url)["client_id"] == "dcr-client-1"
    assert [r for r in world.requests if r.url.path == "/register"] != []


async def test_cimd_without_iss_and_no_dcr_is_no_registration(world, reg) -> None:
    world.as_meta["client_id_metadata_document_supported"] = True
    world.as_meta.pop("authorization_response_iss_parameter_supported")
    world.as_meta.pop("registration_endpoint")
    res = await f.call(_admin(), "POST", "/integrations/mcp/notion/oauth/start")
    assert res.status_code == 502 and res.json()["detail"] == "no_registration"


async def test_a_broken_oauth_import_never_drops_a_header_server(monkeypatch, runtime) -> None:
    """Review of #931 (item 8): with no OAuth row, the reader never imports
    the OAuth module, so a fault there cannot drop the header servers."""
    import sys

    from gateway.connections import mcp

    runtime["conns"] = [c for c in runtime["conns"] if c.provider == "mcp:plain"]
    import gateway.connections as package

    monkeypatch.setitem(sys.modules, "gateway.connections.mcp_oauth", None)
    monkeypatch.delattr(package, "mcp_oauth", raising=False)
    servers = await mcp.servers_for_org(ORG_A)
    assert [s.name for s in servers] == ["plain"]
