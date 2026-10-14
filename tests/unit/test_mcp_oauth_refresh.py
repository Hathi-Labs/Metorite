"""MCP OAuth at run time: the refresh hop and the 401 (WS-54 IN-8b).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.5, §3.6.7 and
§3.6.11 (rows O14 and O6). The R8 half, with the real store and the row lock,
is ``tests/unit/test_mcp_oauth_refresh_r8.py``. That file also holds the
mutation table of IN-8b.

What this file proves, with no database and no real provider:

* **The refresh hop** (``mcp_oauth.refresh``). It sends the refresh grant,
  the resource and the client, through the REAL guard, to the pinned
  address. A rotated refresh token replaces the old one. An answer with none
  keeps the old one. ``invalid_grant`` comes back as its code. A credential
  with no refresh token sends nothing.
* **O14, the run half.** A real MAF connect, through the REAL pinned client,
  to a fake MCP server that answers 401. The server drops out and the run
  goes on. ``on_unauthorized`` runs only when the registry refreshed the
  token in this run. A 401 with no refresh, or another failure after a
  refresh, calls nothing. The log line names the server only.
* **The wiring.** The injection passes the OAuth flags of each row to MAF,
  and its callback names the org of the run binding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import structlog

pytest.importorskip("agent_framework", reason="agent_framework not installed")
pytest.importorskip("mcp", reason="mcp not installed")

from acb_common.db import clear_tenant

from tests.unit import _mcp_oauth_fakes as f
from tests.unit._maf_mcp_fakes import RegistrySpy, logged_text, native_agent, set_flag

ORG_A = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _no_tenant():
    clear_tenant()
    yield
    clear_tenant()


@pytest.fixture
def world(monkeypatch) -> f.World:
    f.configure(monkeypatch)
    w = f.World()
    w.live_refresh.add(f.SENTINEL_REFRESH)
    return f.install(monkeypatch, w)


def _credential(**over: Any) -> Any:
    from gateway.crm_sources.base import SourceCredential

    base: dict[str, Any] = dict(
        access_token=f.SENTINEL_ACCESS, refresh_token=f.SENTINEL_REFRESH,
        expires_at=datetime.now(UTC) + timedelta(seconds=30), scopes=("read", "write"),
        provider_meta={"issuer": f.ISSUER, "token_endpoint": f"{f.ISSUER}/token",
                       "client_id": "dcr-client-1", "registration": "dcr",
                       "token_auth": "client_secret_post", "resource": f.MCP_URL},
        client_secret=f.SENTINEL_SECRET,
    )
    base.update(over)
    return SourceCredential(**base)


# ── The refresh hop ─────────────────────────────────────────────────────────


async def test_a_refresh_sends_the_grant_through_the_guard_and_keeps_the_rotation(
        world: f.World) -> None:
    from gateway.connections import mcp_oauth

    now = datetime(2026, 10, 14, tzinfo=UTC)
    old = _credential()
    fresh = await mcp_oauth.refresh(old, now=now)

    assert world.refreshes == [{
        "grant_type": "refresh_token", "refresh_token": f.SENTINEL_REFRESH,
        "resource": f.MCP_URL, "client_id": "dcr-client-1",
        "client_secret": f.SENTINEL_SECRET,
    }]
    assert fresh.access_token == f"{f.SENTINEL_ACCESS}-refreshed-1"
    assert fresh.refresh_token == f"{f.SENTINEL_REFRESH}-rotated-1"
    assert fresh.expires_at == now + timedelta(seconds=3600)
    assert dict(fresh.provider_meta) == dict(old.provider_meta)
    assert fresh.client_secret == f.SENTINEL_SECRET and fresh.scopes == ("read", "write")
    assert fresh.issued_at == now, "review 2 of #1011: the life of the token"
    # The real guard: the hop went to the pinned address, on 443, by name.
    sent = [r for r in world.requests if r.url.path == "/token"]
    assert len(sent) == 1 and sent[0].url.host == f.PUBLIC_IP
    assert sent[0].headers["host"] == f.AUTH_HOST


async def test_an_answer_with_no_refresh_token_keeps_the_old_one(world: f.World) -> None:
    from gateway.connections import mcp_oauth

    world.rotate = False
    fresh = await mcp_oauth.refresh(_credential())
    assert fresh.refresh_token == f.SENTINEL_REFRESH
    assert fresh.access_token == f"{f.SENTINEL_ACCESS}-refreshed-1"


@pytest.mark.parametrize("code", ["invalid_grant", "invalid_client", "temporarily_unavailable"])
async def test_a_refused_refresh_carries_the_provider_code(world: f.World, code: str) -> None:
    from gateway.connections import mcp_oauth

    world.refresh_error = code
    with (structlog.testing.capture_logs() as logs,
          pytest.raises(mcp_oauth.OAuthFailed) as err):
        await mcp_oauth.refresh(_credential())
    assert err.value.code == code
    for secret in f.SENTINELS:
        assert secret not in f.text_of(logs) and secret not in str(err.value)


async def test_a_credential_with_no_refresh_token_sends_nothing(world: f.World) -> None:
    from gateway.connections import mcp_oauth

    with pytest.raises(mcp_oauth.OAuthFailed) as err:
        await mcp_oauth.refresh(_credential(refresh_token=""))
    assert err.value.code == mcp_oauth.NO_REFRESH_TOKEN
    assert world.requests == []


# ── O14, the run half: a real connect that gets a 401 ───────────────────────


@pytest.fixture
def session_world(monkeypatch) -> f.World:
    """The world under the guard AND under the pinned MCP client."""
    f.configure(monkeypatch)
    w = f.World()
    w.fixed[(f.MCP_HOST, "/mcp")] = httpx.Response(401, json={"error": "invalid_token"})
    return f.install_session(monkeypatch, w)


def _server(name: str, *, oauth: bool = True, refreshed: bool = False) -> Any:
    from orchestrator import _maf_mcp

    return _maf_mcp.McpServer(
        name=name, url=f.MCP_URL, headers={"Authorization": f"Bearer {f.SENTINEL_ACCESS}"},
        oauth=oauth, oauth_refreshed=refreshed)


async def _attach(servers: list[Any], calls: list[Any] | None = None, *,
                  fail: bool = False) -> list[str]:
    from orchestrator import _maf_mcp

    async def _mark(server: Any) -> None:
        if calls is not None:
            calls.append((server.name, server.oauth_refreshed))
        if fail:
            raise RuntimeError(f"the store refused {f.SENTINEL_ACCESS}")

    return await _maf_mcp.attach_mcp_tools(native_agent(), servers, [], on_unauthorized=_mark)


async def test_a_401_after_a_refresh_calls_on_unauthorized_and_the_run_goes_on(
        session_world: f.World, caplog) -> None:
    calls: list[str] = []
    with structlog.testing.capture_logs() as logs:
        names = await _attach([_server("notion", refreshed=True)], calls)
    assert names == [] and calls == [("notion", True)]
    sent = [r for r in session_world.requests if r.url.path == "/mcp"]
    assert sent and sent[0].headers["authorization"] == f"Bearer {f.SENTINEL_ACCESS}"
    assert sent[0].url.host == f.PUBLIC_IP, "the connect skipped the pinned transport"
    hits = [e for e in logs if e.get("event") == "mcp.oauth_unauthorized"]
    assert hits == [{"event": "mcp.oauth_unauthorized", "name": "notion",
                     "log_level": "warning"}], "the line names the server only"
    assert f.SENTINEL_ACCESS not in logged_text(logs, caplog)


async def test_a_401_with_no_refresh_in_this_run_says_so(session_world: f.World) -> None:
    """Review 2 of #1011 (P1): the registry decides, so the callback runs
    and carries "no refresh in this run"."""
    calls: list[Any] = []
    with structlog.testing.capture_logs() as logs:
        names = await _attach([_server("notion", refreshed=False)], calls)
    assert names == [] and calls == [("notion", False)]
    assert [e for e in logs if e.get("event") == "mcp.oauth_unauthorized"]


async def test_another_failure_after_a_refresh_calls_nothing(session_world: f.World) -> None:
    session_world.fixed[(f.MCP_HOST, "/mcp")] = httpx.Response(503)
    calls: list[str] = []
    with structlog.testing.capture_logs() as logs:
        names = await _attach([_server("notion", refreshed=True)], calls)
    assert names == [] and calls == []
    assert [e for e in logs if e.get("event") == "mcp.unreachable"]
    assert not [e for e in logs if e.get("event") == "mcp.oauth_unauthorized"]


async def test_a_header_server_401_is_unreachable_and_calls_nothing(
        session_world: f.World) -> None:
    calls: list[str] = []
    with structlog.testing.capture_logs() as logs:
        names = await _attach([_server("plain", oauth=False, refreshed=True)], calls)
    assert names == [] and calls == []
    assert [e for e in logs if e.get("event") == "mcp.unreachable"]


async def test_a_failed_mark_never_fails_the_run(session_world: f.World, caplog) -> None:
    calls: list[str] = []
    with structlog.testing.capture_logs() as logs:
        names = await _attach([_server("notion", refreshed=True)], calls, fail=True)
    assert names == [] and calls == [("notion", True)]
    failed = [e for e in logs if e.get("event") == "mcp.oauth_mark_failed"]
    assert failed and failed[0]["error"] == "RuntimeError"
    assert f.SENTINEL_ACCESS not in logged_text(logs, caplog)


# ── The wiring: registry row to MAF server to callback ──────────────────────


async def test_the_injection_passes_the_flags_and_names_the_org(
        session_world: f.World, monkeypatch) -> None:
    from gateway.connections import mcp
    from orchestrator._tool_injection import _inject_mcp_servers

    set_flag(monkeypatch, "*")
    spy = RegistrySpy([]).install(monkeypatch)
    header = {"Authorization": f"Bearer {f.SENTINEL_ACCESS}"}

    async def _servers(org: str, **_kw: Any) -> list[Any]:
        spy.statements.append(("servers_for_org", {"org": org}))
        return [mcp.StoredServer(
            name="notion", label="notion", description="", transport="http-sse",
            url=f.MCP_URL, agent_scope=("*",), enabled=True, header_names=("Authorization",),
            headers=dict(header), oauth=True, refreshed=True)]

    marked: list[tuple[str, str, dict[str, str], bool]] = []

    async def _mark(org: str, name: str, sent: dict[str, str], *, refreshed: bool) -> str:
        marked.append((org, name, dict(sent), refreshed))
        return mcp.MARK_NEEDS_RECONNECT

    monkeypatch.setattr(mcp, "servers_for_org", _servers)
    monkeypatch.setattr(mcp, "mark_unauthorized", _mark)
    agent = native_agent()
    await _inject_mcp_servers(agent, "apis-config", organization_id=ORG_A, opened=[])
    assert list(getattr(agent, "mcp_tools", []) or []) == []
    assert marked == [(ORG_A, "notion", header, True)]


# ── IN-8c: the Test button follows the rule of a run ───────────────────────
#
# ``mcp.oauth_test_header``, with the store and the refresh step faked. The
# R8 half (the real row lock and the real refresh) is in
# ``tests/unit/test_mcp_oauth_org_scope_r8.py``.


def _conn(status: str = "connected", *, oauth: bool = True) -> Any:
    from gateway.connections import store

    meta: dict[str, Any] = {"url": f.MCP_URL}
    if oauth:
        meta["auth"] = "oauth"
    return store.Connection(
        id="00000000-0000-0000-0000-000000000001", organization_id=ORG_A,
        provider="mcp:srv", owning_app="integrations", scope_kind="org", member_email=None,
        status=status, provider_meta=meta, scopes=(), connected_by="a@x.test",
        last_sync_at=None, last_error=None, created_at=None, updated_at=None,
    )


@pytest.fixture
def test_store(monkeypatch) -> dict[str, Any]:
    import contextlib

    from gateway.connections import mcp, store

    state: dict[str, Any] = {"conn": _conn(), "cred": _credential(), "steps": [],
                             "step_answer": ({"Authorization": "Bearer fresh"}, True)}

    @contextlib.asynccontextmanager
    async def _session(_org: str):
        yield object()

    async def _list(_db, _org, _prefix, _app):
        return [state["conn"]] if state["conn"] is not None else []

    async def _load(_db, _org, _provider, _app):
        return state["cred"]

    async def _step(org, conn, stored):
        state["steps"].append((org, conn.provider, stored))
        return state["step_answer"]

    set_flag(monkeypatch, "*")
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", True, raising=False)
    monkeypatch.setattr(mcp, "tenant_session", _session)
    monkeypatch.setattr(store, "list_by_prefix", _list)
    monkeypatch.setattr(store, "load_credential", _load)
    monkeypatch.setattr(mcp, "_oauth_header", _step)
    mcp._BACKOFF.clear()
    return state


async def test_a_test_refreshes_a_due_token_through_the_run_step(test_store) -> None:
    from gateway.connections import mcp

    # 30 s left: inside the margin of a run, so the token is due.
    outcome, header = await mcp.oauth_test_header(ORG_A, "srv")
    assert (outcome, header) == (mcp.TEST_BEARER_OK, {"Authorization": "Bearer fresh"})
    assert [s[1] for s in test_store["steps"]] == ["mcp:srv"]


async def test_a_test_whose_refresh_gives_nothing_reads_expired(test_store) -> None:
    from gateway.connections import mcp

    test_store["step_answer"] = None
    assert await mcp.oauth_test_header(ORG_A, "srv") == (mcp.TEST_BEARER_EXPIRED, {})


async def test_a_test_of_a_live_token_sends_it_with_no_refresh(test_store) -> None:
    from gateway.connections import mcp

    test_store["cred"] = _credential(expires_at=datetime.now(UTC) + timedelta(hours=2))
    outcome, header = await mcp.oauth_test_header(ORG_A, "srv")
    assert outcome == mcp.TEST_BEARER_OK
    assert header == {"Authorization": f"Bearer {f.SENTINEL_ACCESS}"}
    assert test_store["steps"] == []


async def test_a_test_of_a_row_in_needs_reconnect_sends_nothing(test_store) -> None:
    from gateway.connections import mcp

    test_store["conn"] = _conn("needs_reconnect")
    assert await mcp.oauth_test_header(ORG_A, "srv") == (mcp.TEST_BEARER_EXPIRED, {})
    assert test_store["steps"] == []


async def test_a_test_of_a_disabled_row_keeps_the_rule_before_in8c(test_store) -> None:
    """No run uses a disabled row, so a Test never refreshes it. It sends the
    stored token while it lives, and an ended token reads expired."""
    from gateway.connections import mcp

    test_store["conn"] = _conn("disconnected")
    # 5 minutes left: due for a run (inside the 15 minute margin), not ended.
    test_store["cred"] = _credential(expires_at=datetime.now(UTC) + timedelta(seconds=300))
    outcome, header = await mcp.oauth_test_header(ORG_A, "srv")
    assert outcome == mcp.TEST_BEARER_OK
    assert header == {"Authorization": f"Bearer {f.SENTINEL_ACCESS}"}
    test_store["cred"] = _credential(expires_at=datetime.now(UTC) + timedelta(seconds=10))
    assert await mcp.oauth_test_header(ORG_A, "srv") == (mcp.TEST_BEARER_EXPIRED, {})
    assert test_store["steps"] == []


async def test_a_test_of_a_header_row_or_a_missing_row_is_unreadable(test_store) -> None:
    from gateway.connections import mcp

    test_store["conn"] = _conn(oauth=False)
    assert await mcp.oauth_test_header(ORG_A, "srv") == (mcp.TEST_BEARER_UNREADABLE, {})
    test_store["conn"] = None
    assert await mcp.oauth_test_header(ORG_A, "srv") == (mcp.TEST_BEARER_UNREADABLE, {})
    assert test_store["steps"] == []


async def test_a_test_in_the_backoff_sends_the_stored_token_while_it_lives(test_store) -> None:
    from gateway.connections import mcp

    test_store["cred"] = _credential(expires_at=datetime.now(UTC) + timedelta(seconds=300))
    mcp._BACKOFF[(ORG_A, "mcp:srv")] = mcp._monotonic() + 60
    outcome, header = await mcp.oauth_test_header(ORG_A, "srv")
    assert outcome == mcp.TEST_BEARER_OK and test_store["steps"] == []
    assert header == {"Authorization": f"Bearer {f.SENTINEL_ACCESS}"}
    mcp._BACKOFF.clear()
