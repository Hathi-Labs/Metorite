"""R8 — MCP OAuth refresh at run time and ``needs_reconnect`` (WS-54 IN-8b).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.6, §3.6.7 and
§3.6.11, rows O7, O8 and O14 (and the run half of O6 and O12). D98.

What this proves against a REAL Postgres, as the NON-privileged role of the
phase-4 rehearsal (the owner role bypasses RLS, so it would prove nothing),
through the real routers, the real store, the real row lock and the real
guard, with a local fake auth server (``_mcp_oauth_fakes.py``). No test
reaches a real provider.

- **O7.** Two workers find one token near its expiry. ONE refresh goes out,
  both workers send the new token, and the rotated refresh token is stored.
- **O8.** A refresh that answers ``invalid_grant`` sets ``needs_reconnect``.
  The next run sends no refresh, and the console shows the row as
  ``needs_reconnect``.
- **O14.** A 401 on the connect drops the server, and the run goes on. A 401
  after a refresh in the SAME run sets ``needs_reconnect``. A 401 with no
  refresh leaves the row connected.
- The refresh runs BEFORE the connect, and the tool sends the new token.
- A stalled token endpoint does not stall the run (the fetch cap).
- With ``MCP_OAUTH_ENABLED`` off, nothing refreshes and the row stays out.
- Org B's refresh never touches org A's row of the same name.
- No token reaches a log line, a column, ``provider_meta`` or the console.
- Review of #1011: a run refreshes no row that it cannot use, a process
  runs at most ``REFRESH_SLOTS`` refreshes at one time, and a Stop during
  the fetch still commits the rotated refresh token.

Mutation table (IN-8b). Each mutation is made by hand, and the named test
must fail. Results are in the PR.

| # | Mutation | Test that fails |
|---|---|---|
| R1 | no row lock: ``FOR UPDATE`` leaves ``store._BLOB_BY_ID_SQL`` | ``test_two_workers_send_one_refresh_and_both_get_the_new_token`` |
| R2 | the refresh happens after the connect: the reader never refreshes first (``needs_refresh`` is always false) | ``test_the_refresh_runs_before_the_connect_and_the_tool_sends_the_new_token`` |
| R3 | a 401 always sets ``needs_reconnect`` (the ``oauth_refreshed`` test of ``attach_mcp_tools`` is gone) | ``test_a_401_with_no_refresh_leaves_the_row_connected`` |
| R4 | ``invalid_grant`` is ignored (``_source_error`` drops the code) | ``test_invalid_grant_sets_needs_reconnect_and_the_next_run_sends_no_refresh`` |
| R5 | the refresh hop bypasses the guard (``httpx`` directly) | ``test_mcp_oauth_guard.py::test_no_oauth_module_fetches_by_itself`` and ``test_each_hop_refuses[refresh-*]`` |
| R6 | a token is logged (``mcp.oauth_refreshed`` carries it) | ``test_no_token_reaches_a_log_a_column_or_the_console`` |
| R7 | no cap on the refresh fetch (``REFRESH_FETCH_S`` is not applied) | ``test_a_stalled_token_endpoint_does_not_stall_the_run`` |

Rows R8 to R11 fence the review of #1011.

| # | Mutation | Test that fails |
|---|---|---|
| R8 | no slot cap: the step does not wait on ``_slots()`` | ``test_one_process_runs_at_most_the_slot_cap_of_refreshes`` |
| R9 | no unlocked check: every OAuth row goes to the locked refresh step | ``test_mcp_oauth_flow.py::test_flag_on_a_run_sends_the_bearer_token`` |
| R10 | the reader ignores ``oauth_wanted`` | ``test_a_run_never_refreshes_a_row_that_it_cannot_use`` |
| R11 | the run waits on the step with no ``asyncio.shield`` | ``test_a_stop_during_the_fetch_still_keeps_the_rotated_token`` |

Rows R12 to R16 fence review 2 of #1011. Row R3 changed with it: the
registry, not ``attach_mcp_tools``, now decides what a 401 does.

| # | Mutation | Test that fails |
|---|---|---|
| R3 | a 401 always sets ``needs_reconnect`` (``mark_unauthorized`` ignores *refreshed*) | ``test_a_401_with_no_refresh_ends_the_token_and_the_next_run_refreshes`` |
| R12 | no slot per org | ``test_an_org_whose_auth_server_stalls_never_delays_another_org`` |
| R13 | no back-off after a refresh that is not final | ``test_a_failed_refresh_is_not_tried_again_on_every_run`` |
| R14 | a 401 with no refresh changes nothing | ``test_a_401_with_no_refresh_ends_the_token_and_the_next_run_refreshes`` |
| R15 | the margin is :data:`TOKEN_MARGIN_S` for every token | ``test_a_short_token_gets_one_refresh_under_two_workers`` |
| R16 | ``fresh_credential`` reads a row that is no longer OAuth | ``test_a_row_registered_again_with_headers_is_not_marked`` |

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_oauth_refresh_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("agent_framework", reason="agent_framework not installed")

from tests.unit import _mcp_oauth_fakes as f
from tests.unit._maf_mcp_fakes import install_offline, logged_text, set_flag

# ``promoted``, ``app_engine``, ``graph_on_promoted``, ``registry_on_promoted``
# and ``ns`` are fixtures, used by name. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_mcp_registry_r8 import (  # noqa: F401
    _assert_non_priv,
    _call,
    _inject,
    _registry,
    ns,
)
from tests.unit.test_mcp_servers_org_scope import (  # noqa: F401
    graph_on_promoted,
    registry_on_promoted,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops.
pytestmark = _DB_GATE

ADMIN_A = "admin-a@in8b-r8.test"
ADMIN_B = "admin-b@in8b-r8.test"
TOKEN_A = "SENTINEL-in8b-r8-token-org-a"
TOKEN_B = "SENTINEL-in8b-r8-token-org-b"
#: Inside the margin of a run, and past the 60 s skew. A consent with this
#: ``expires_in`` is stored as a one-hour token with 600 s left
#: (:func:`_make_near`): a new token is due only at half its life.
NEAR = 600
#: The life of a NEAR token.
NEAR_LIFE = 3600
#: Past the margin: no refresh.
FAR = 3600


def _name(tag: str) -> str:
    return f"in8b-{tag}-{uuid.uuid4().hex[:6]}"


@pytest.fixture
def world(ns, monkeypatch) -> f.World:  # noqa: F811 - a fixture
    from gateway.connections import mcp

    f.configure(monkeypatch, keep_key=True)
    monkeypatch.setattr(mcp, "_BACKOFF", {}, raising=False)  # process-local
    return f.install(monkeypatch, f.World())


async def _connected(world: f.World, org: str, admin: str, name: str, token: str, *,
                     expires_in: int | None, scope: tuple[str, ...] = ("*",),
                     refresh: str | None = None) -> None:
    """Register *name* in *org*, and connect it by a consent whose access
    token ends in *expires_in* seconds. A refresh then lives an hour."""
    from gateway.connections import mcp

    await mcp.save(org, name=name, url=f.MCP_URL, headers={}, connected_by="r8-seed",
                   label=f"Label {name}", agent_scope=scope)
    world.access_token = token
    world.refresh_token = f"refresh-{token}" if refresh is None else refresh
    world.expires_in = expires_in
    _, code, state = await f.start_and_consent(f.user(admin, org), world, name)
    res = await f.finish(f.user(admin, org), code, state, iss=f.ISSUER)
    assert res.status_code == 302 and res.headers["location"].endswith(name), res.text
    world.expires_in = FAR
    if expires_in == NEAR:
        await _make_near(org, name)


async def _make_near(org: str, name: str) -> None:
    """Age the stored token: issued ``NEAR_LIFE - NEAR`` seconds ago, with
    ``NEAR`` seconds left. So it is past half its life, and due."""
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    from acb_common.db import tenant_session
    from gateway.connections import mcp, store

    now = datetime.now(UTC)
    async with tenant_session(org) as db:
        conn = await store.get(db, org, mcp.provider_for(name), mcp.OWNING_APP)
        cred = await store.load_credential(db, org, mcp.provider_for(name), mcp.OWNING_APP)
        assert conn is not None and cred is not None
        await store.update_tokens(db, org, conn.id, replace(
            cred, issued_at=now - timedelta(seconds=NEAR_LIFE - NEAR),
            expires_at=now + timedelta(seconds=NEAR)))


async def _stored(org: str, name: str) -> Any:
    """The decrypted credential of the row, read as the app role."""
    from acb_common.db import tenant_session
    from gateway.connections import mcp, store

    async with tenant_session(org) as db:
        return await store.load_credential(db, org, mcp.provider_for(name), mcp.OWNING_APP)


def _by_name(servers: list[Any]) -> dict[str, Any]:
    return {s.name: s for s in servers}


def _two_processes(monkeypatch) -> None:
    """Each call gets its own slots, as two gateway processes do. So only
    the row lock can serve one refresh to two workers (O7). One process
    would also serialize them on the slot of the org."""
    from gateway.connections import mcp

    monkeypatch.setattr(mcp, "_org_slots", lambda _org: asyncio.Semaphore(1))
    monkeypatch.setattr(mcp, "_slots", lambda: asyncio.Semaphore(1))


# ── O7: one refresh under two workers ───────────────────────────────────────


async def test_two_workers_send_one_refresh_and_both_get_the_new_token(
        ns, world, app_engine, monkeypatch) -> None:  # noqa: F811
    from gateway.connections import mcp

    _assert_non_priv(app_engine)
    name = _name("o7")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    _two_processes(monkeypatch)
    # The provider is slow, so both workers find the old token at one time.
    world.token_delay_s = 0.5
    one, two = await asyncio.gather(mcp.servers_for_org(ns.org_a),
                                    mcp.servers_for_org(ns.org_a))

    assert len(world.refreshes) == 1, f"{len(world.refreshes)} refreshes went out"
    assert world.refreshes[0]["refresh_token"] == f"refresh-{TOKEN_A}"
    new = {"Authorization": f"Bearer {f.SENTINEL_ACCESS}-refreshed-1"}
    a, b = _by_name(one)[name], _by_name(two)[name]
    assert a.headers == new and b.headers == new, "a worker sent the old token"
    assert sorted([a.refreshed, b.refreshed]) == [False, True], "one worker refreshed"
    row = _registry(ns, ns.org_a)[name]
    assert row["status"] == "connected", row["status"]
    stored = await _stored(ns.org_a, name)
    assert stored.access_token == f"{f.SENTINEL_ACCESS}-refreshed-1"
    assert stored.refresh_token == f"{f.SENTINEL_REFRESH}-rotated-1", "the rotation was lost"
    # A third run finds the fresh token and sends nothing.
    third = _by_name(await mcp.servers_for_org(ns.org_a))[name]
    assert third.headers == new and third.refreshed is False
    assert len(world.refreshes) == 1


# ── O8: invalid_grant sets needs_reconnect ──────────────────────────────────


async def test_invalid_grant_sets_needs_reconnect_and_the_next_run_sends_no_refresh(
        ns, world) -> None:  # noqa: F811
    from gateway.connections import mcp

    name = _name("o8")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    world.refresh_error = "invalid_grant"
    assert name not in _by_name(await mcp.servers_for_org(ns.org_a))
    row = _registry(ns, ns.org_a)[name]
    assert (row["status"], row["provider_meta"].get("auth")) == ("needs_reconnect", "oauth")
    assert len(world.refreshes) == 1

    assert name not in _by_name(await mcp.servers_for_org(ns.org_a))
    assert len(world.refreshes) == 1, "a dead refresh token was sent again"

    listed = await _call(f.user(ADMIN_A, ns.org_a), "GET", "/integrations/connections")
    assert listed.status_code == 200, listed.text
    rows = {r["id"]: r for r in listed.json()["connections"]}
    assert rows[f"mcp:{name}"]["status"] == "needs_reconnect"
    assert rows[f"mcp:{name}"]["last_error"] == "invalid_grant"
    for secret in (TOKEN_A, f"refresh-{TOKEN_A}"):
        assert secret not in listed.text

    # A new consent repairs it: the row is connected again.
    world.refresh_error = None
    world.access_token, world.refresh_token = TOKEN_B, f"refresh-{TOKEN_B}"
    _, code, state = await f.start_and_consent(f.user(ADMIN_A, ns.org_a), world, name)
    assert (await f.finish(f.user(ADMIN_A, ns.org_a), code, state,
                           iss=f.ISSUER)).status_code == 302
    assert _registry(ns, ns.org_a)[name]["status"] == "connected"


# ── O14: the 401 on the connect ─────────────────────────────────────────────


def _mcp_answers_401(monkeypatch, world: f.World) -> None:
    """A real MAF connect through the real pinned client, to an MCP server
    that refuses every token."""
    world.fixed[(f.MCP_HOST, "/mcp")] = httpx.Response(401, json={"error": "invalid_token"})
    f.install_session(monkeypatch, world)


async def test_a_401_after_a_refresh_in_this_run_sets_needs_reconnect(
        ns, world, monkeypatch, caplog) -> None:  # noqa: F811
    import structlog

    set_flag(monkeypatch, "*")
    name = _name("o14r")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    _mcp_answers_401(monkeypatch, world)
    with structlog.testing.capture_logs() as logs:
        agent = await _inject(ns.org_a)
    assert name not in {t.name for t in agent.mcp_tools}, "the server stayed in the run"
    assert len(world.refreshes) == 1
    sent = [r for r in world.requests if r.url.path == "/mcp"]
    assert sent[-1].headers["authorization"] == f"Bearer {f.SENTINEL_ACCESS}-refreshed-1"
    row = _registry(ns, ns.org_a)[name]
    assert row["status"] == "needs_reconnect"
    hits = [e for e in logs if e.get("event") == "mcp.oauth_unauthorized"]
    assert hits and set(hits[0]) == {"event", "name", "log_level"}
    text = logged_text(logs, caplog)
    for secret in (TOKEN_A, f.SENTINEL_ACCESS, f.SENTINEL_REFRESH):
        assert secret not in text


@pytest.mark.parametrize("expires_in", [FAR, None], ids=["far", "no-expires-in"])
async def test_a_401_with_no_refresh_ends_the_token_and_the_next_run_refreshes(
        ns, world, monkeypatch, expires_in) -> None:  # noqa: F811
    """Review 2 of #1011 (P1). One 401 with no refresh never asks an admin
    to connect again. It ends the stored token, so the next run refreshes.
    ``no-expires-in``: a token answer with no ``expires_in`` is stored for
    ten years, and without this rule a dead one would stay dead for ever."""
    from datetime import UTC, datetime, timedelta

    set_flag(monkeypatch, "*")
    name = _name("o14n")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=expires_in)
    if expires_in is None:
        assert (await _stored(ns.org_a, name)).expires_at > datetime.now(UTC) + timedelta(
            days=3000), "no expires_in is stored as ten years"
    _mcp_answers_401(monkeypatch, world)
    agent = await _inject(ns.org_a)
    assert name not in {t.name for t in agent.mcp_tools}
    assert world.refreshes == []
    sent = [r for r in world.requests if r.url.path == "/mcp"]
    assert sent[-1].headers["authorization"] == f"Bearer {TOKEN_A}"
    assert _registry(ns, ns.org_a)[name]["status"] == "connected", (
        "one 401 with no refresh asked the admin to connect again")
    stored = await _stored(ns.org_a, name)
    assert stored.access_token == TOKEN_A
    assert stored.expires_at <= datetime.now(UTC), "the 401 did not end the stored token"

    # The next run refreshes. Its new token gets the 401 too, so now the
    # admin must act, and the console says so.
    await _inject(ns.org_a)
    assert len(world.refreshes) == 1
    sent = [r for r in world.requests if r.url.path == "/mcp"]
    assert sent[-1].headers["authorization"] == f"Bearer {f.SENTINEL_ACCESS}-refreshed-1"
    assert _registry(ns, ns.org_a)[name]["status"] == "needs_reconnect"


async def test_a_401_on_a_token_with_no_refresh_token_needs_reconnect(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """Review 2 of #1011 (P1): nothing can refresh it, so the admin acts."""
    set_flag(monkeypatch, "*")
    name = _name("o14x")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=None, refresh="")
    assert (await _stored(ns.org_a, name)).refresh_token == ""
    _mcp_answers_401(monkeypatch, world)
    await _inject(ns.org_a)
    assert world.refreshes == []
    row = _registry(ns, ns.org_a)[name]
    assert row["status"] == "needs_reconnect"


async def test_a_401_after_a_new_consent_keeps_the_new_consent(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """The mark compares the token: a consent between the refresh and the
    401 is the admin's, and the mark leaves it."""
    from gateway.connections import mcp

    name = _name("o14c")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=FAR)
    stale = {"Authorization": "Bearer an-older-token"}
    sent = {"Authorization": f"Bearer {TOKEN_A}"}
    for refreshed in (True, False):
        assert await mcp.mark_unauthorized(ns.org_a, name, stale,
                                           refreshed=refreshed) == mcp.MARK_UNCHANGED
        assert await mcp.mark_unauthorized(ns.org_b, name, sent,
                                           refreshed=refreshed) == mcp.MARK_UNCHANGED
    before = _registry(ns, ns.org_a)[name]
    assert before["status"] == "connected"
    assert await mcp.mark_unauthorized(ns.org_a, name, sent,
                                       refreshed=True) == mcp.MARK_NEEDS_RECONNECT
    assert _registry(ns, ns.org_a)[name]["status"] == "needs_reconnect"


# ── Before the connect, and the cap ─────────────────────────────────────────


async def test_the_refresh_runs_before_the_connect_and_the_tool_sends_the_new_token(
        ns, world, monkeypatch) -> None:  # noqa: F811
    from agent_framework import MCPStreamableHTTPTool

    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    seen: list[tuple[int, dict[str, str]]] = []

    async def _connect(self: Any, *, reset: bool = False) -> None:
        seen.append((len(world.refreshes), dict(self._static_headers)))

    monkeypatch.setattr(MCPStreamableHTTPTool, "connect", _connect)
    name = _name("order")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    agent = await _inject(ns.org_a)
    tools = {t.name: t for t in agent.mcp_tools}
    new = {"Authorization": f"Bearer {f.SENTINEL_ACCESS}-refreshed-1"}
    assert seen == [(1, new)], "the connect did not come after the refresh"
    # The header is fixed for the run: MAF never sees a second value.
    assert tools[name]._static_headers == new and tools[name]._header_provider is None


async def test_a_stalled_token_endpoint_does_not_stall_the_run(
        ns, world, monkeypatch) -> None:  # noqa: F811
    from gateway.connections import mcp

    name = _name("cap")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    monkeypatch.setattr(mcp, "REFRESH_FETCH_S", 0.3)
    world.token_delay_s = 5.0
    # An order, not a wall-clock budget (test_no_new_clock_flakes.py): the
    # endpoint answers after 5 s, and records the refresh only then. So a run
    # that returns with no recorded refresh did not wait for the answer. The
    # 30 s guard only catches a hang.
    servers = _by_name(await asyncio.wait_for(mcp.servers_for_org(ns.org_a), 30))
    assert world.refreshes == [], "the run waited for the token endpoint"
    # Not final: the run keeps the stored token while it lives, and the row
    # stays as it was.
    assert servers[name].headers == {"Authorization": f"Bearer {TOKEN_A}"}
    assert servers[name].refreshed is False
    assert _registry(ns, ns.org_a)[name]["status"] == "connected"
    assert (await _stored(ns.org_a, name)).access_token == TOKEN_A


# ── The flag, two orgs, and no secret out ───────────────────────────────────


async def test_flag_off_sends_no_refresh_and_leaves_the_row_out(
        ns, world, monkeypatch) -> None:  # noqa: F811
    from acb_common import get_settings
    from gateway.connections import mcp

    name = _name("off")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    before = _registry(ns, ns.org_a)[name]
    monkeypatch.setattr(get_settings(), "mcp_oauth_enabled", False, raising=False)
    assert name not in _by_name(await mcp.servers_for_org(ns.org_a))
    assert world.refreshes == []
    assert _registry(ns, ns.org_a)[name]["whole"] == before["whole"]


async def test_org_b_refresh_never_touches_org_a_row(
        ns, world, app_engine) -> None:  # noqa: F811
    from gateway.connections import mcp

    _assert_non_priv(app_engine)
    name = _name("orgs")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    await _connected(world, ns.org_b, ADMIN_B, name, TOKEN_B, expires_in=NEAR)
    before_a = _registry(ns, ns.org_a)[name]
    before_b = _registry(ns, ns.org_b)[name]

    got = _by_name(await mcp.servers_for_org(ns.org_b))[name]
    assert [r["refresh_token"] for r in world.refreshes] == [f"refresh-{TOKEN_B}"]
    assert got.headers == {"Authorization": f"Bearer {f.SENTINEL_ACCESS}-refreshed-1"}
    assert _registry(ns, ns.org_a)[name]["whole"] == before_a["whole"], "B's run wrote A's row"
    assert _registry(ns, ns.org_b)[name]["credentials_encrypted"] != (
        before_b["credentials_encrypted"])
    assert (await _stored(ns.org_a, name)).access_token == TOKEN_A


async def test_no_token_reaches_a_log_a_column_or_the_console(
        ns, world, caplog) -> None:  # noqa: F811
    import structlog
    from gateway.connections import mcp

    name = _name("leak")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    with structlog.testing.capture_logs() as logs:
        await mcp.servers_for_org(ns.org_a)
        world.refresh_error = "invalid_grant"
        await mcp.servers_for_org(ns.org_b)  # B has no row: nothing to do
        later = _name("leak2")
        await _connected(world, ns.org_a, ADMIN_A, later, TOKEN_B, expires_in=NEAR)
        await mcp.servers_for_org(ns.org_a)  # the second row dies, invalid_grant
    assert len(world.refreshes) == 2
    secrets = (TOKEN_A, TOKEN_B, f"refresh-{TOKEN_A}", f"refresh-{TOKEN_B}",
               f.SENTINEL_ACCESS, f.SENTINEL_REFRESH, f.SENTINEL_SECRET)
    text = logged_text(logs, caplog)
    listed = await _call(f.user(ADMIN_A, ns.org_a), "GET", "/integrations/connections")
    for row_name in (name, later):
        row = _registry(ns, ns.org_a)[row_name]
        for secret in secrets:
            assert secret not in row["whole"], f"{secret} is plain text in the row"
            assert secret not in str(row["provider_meta"])
    for secret in secrets:
        assert secret not in text, f"{secret} reached a log line"
        assert secret not in listed.text, f"{secret} reached the console"
    assert any(e.get("event") == "mcp.oauth_refreshed" for e in logs)
    assert any(e.get("event") == "mcp.oauth_needs_reconnect" for e in logs)


# ── Review of #1011 ─────────────────────────────────────────────────────────


async def test_a_run_never_refreshes_a_row_that_it_cannot_use(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """P2 1: the scope filter runs before the refresh."""
    set_flag(monkeypatch, "*")
    install_offline(monkeypatch)
    name = _name("scope")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR,
                     scope=("another-agent",))
    before = _registry(ns, ns.org_a)[name]
    agent = await _inject(ns.org_a)
    assert name not in {t.name for t in agent.mcp_tools}
    assert world.refreshes == [], "a run refreshed a server that it cannot use"
    assert _registry(ns, ns.org_a)[name]["whole"] == before["whole"]


async def test_one_process_runs_at_most_the_slot_cap_of_refreshes(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """P1: each step holds a pool connection across its fetch, so the steps
    of one process are capped, and the pool of other orgs stays free."""
    from gateway.connections import mcp

    # Two orgs, so the slot of each org does not hide the cap of the process.
    name_a, name_b = _name("slot-a"), _name("slot-b")
    await _connected(world, ns.org_a, ADMIN_A, name_a, TOKEN_A, expires_in=NEAR)
    await _connected(world, ns.org_b, ADMIN_B, name_b, TOKEN_B, expires_in=NEAR)
    monkeypatch.setattr(mcp, "REFRESH_SLOTS", 1)
    world.token_delay_s = 0.3
    live = {"now": 0, "most": 0}
    inner = world.ahandle

    async def _counted(request: httpx.Request) -> httpx.Response:
        refresh = b"grant_type=refresh_token" in request.content
        if refresh:
            live["now"] += 1
            live["most"] = max(live["most"], live["now"])
        try:
            return await inner(request)
        finally:
            if refresh:
                live["now"] -= 1

    monkeypatch.setattr(world, "ahandle", _counted)
    got_a, got_b = await asyncio.gather(mcp.servers_for_org(ns.org_a),
                                        mcp.servers_for_org(ns.org_b))
    assert len(world.refreshes) == 2
    assert live["most"] == 1, f"{live['most']} refreshes ran at one time"
    assert _by_name(got_a)[name_a].refreshed and _by_name(got_b)[name_b].refreshed


async def test_a_stop_during_the_fetch_still_keeps_the_rotated_token(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """P2 2: the step runs in its own task, so a Stop cancels the wait and
    not the write. The provider rotated the refresh token, and the row keeps
    the new one."""
    from gateway.connections import mcp

    name = _name("stop")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    world.token_delay_s = 0.5
    sent = asyncio.Event()
    inner = world.ahandle

    async def _marked(request: httpx.Request) -> httpx.Response:
        if b"grant_type=refresh_token" in request.content:
            sent.set()
        return await inner(request)

    monkeypatch.setattr(world, "ahandle", _marked)
    run = asyncio.create_task(mcp.servers_for_org(ns.org_a))
    await asyncio.wait_for(sent.wait(), 10)
    run.cancel()  # the provider has the request, and rotates the token
    with pytest.raises(asyncio.CancelledError):
        await run
    for _ in range(100):
        if not mcp._INFLIGHT:
            break
        await asyncio.sleep(0.05)
    stored = await _stored(ns.org_a, name)
    assert stored.refresh_token == f"{f.SENTINEL_REFRESH}-rotated-1", (
        "a Stop lost the rotated refresh token")
    assert _registry(ns, ns.org_a)[name]["status"] == "connected"


# ── Review 2 of #1011 ───────────────────────────────────────────────────────


async def _until(check: Any, what: str) -> None:
    """Wait for *check* to hold: an order, never a wall-clock budget. The 30 s
    guard only catches a hang."""
    async def _poll() -> None:
        while not check():
            await asyncio.sleep(0.02)

    try:
        await asyncio.wait_for(_poll(), 30)
    except TimeoutError:
        raise AssertionError(f"never happened: {what}") from None


async def test_an_org_whose_auth_server_stalls_never_delays_another_org(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """P1: org A's two stalled servers hold ONE slot of the process. Org B's
    refresh takes the other slot, and finishes while A still waits."""
    from gateway.connections import mcp

    a1, a2, b1 = _name("stall-a1"), _name("stall-a2"), _name("free-b1")
    await _connected(world, ns.org_a, ADMIN_A, a1, f"{TOKEN_A}-1", expires_in=NEAR)
    await _connected(world, ns.org_a, ADMIN_A, a2, f"{TOKEN_A}-2", expires_in=NEAR)
    await _connected(world, ns.org_b, ADMIN_B, b1, TOKEN_B, expires_in=NEAR)
    # A short wait for a slot, so a run that waits gives up soon. The step of
    # org A that holds a slot waits on the gate until the end of the test.
    monkeypatch.setattr(mcp, "REFRESH_BUDGET_S", 3.0)
    monkeypatch.setattr(mcp, "REFRESH_FETCH_S", 60.0)
    world.gate = asyncio.Event()
    world.gated = {f"refresh-{TOKEN_A}-1", f"refresh-{TOKEN_A}-2"}
    run_a = asyncio.create_task(mcp.servers_for_org(ns.org_a))
    try:
        await _until(lambda: world.arrivals, "org A's refresh reached its auth server")
        got = _by_name(await asyncio.wait_for(mcp.servers_for_org(ns.org_b), 30))
        assert got[b1].refreshed is True, "org B waited behind org A's stalled servers"
        a_seen = [t for t in world.arrivals if t in world.gated]
        assert len(a_seen) == 1, f"org A held {len(a_seen)} slots at one time"
    finally:
        world.gate.set()
        await asyncio.wait_for(run_a, 30)
        await _until(lambda: not mcp._INFLIGHT, "the steps of org A ended")


async def test_a_failed_refresh_is_not_tried_again_on_every_run(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """P1: after a refresh fails for a reason that is not final, the process
    waits ``REFRESH_BACKOFF_S`` before it tries that server again. A run in
    the gap uses the stored token while it lives."""
    from gateway.connections import mcp

    name = _name("backoff")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    world.refresh_error = "temporarily_unavailable"
    clock = {"t": 1000.0}
    monkeypatch.setattr(mcp, "_monotonic", lambda: clock["t"], raising=False)
    stored = {"Authorization": f"Bearer {TOKEN_A}"}

    first = _by_name(await mcp.servers_for_org(ns.org_a))[name]
    assert len(world.refreshes) == 1 and first.headers == stored
    second = _by_name(await mcp.servers_for_org(ns.org_a))[name]
    assert len(world.refreshes) == 1, "a failed refresh was tried again at once"
    assert second.headers == stored and second.refreshed is False

    clock["t"] += mcp.REFRESH_BACKOFF_S + 1
    world.refresh_error = None
    third = _by_name(await mcp.servers_for_org(ns.org_a))[name]
    assert len(world.refreshes) == 2 and third.refreshed is True
    assert _registry(ns, ns.org_a)[name]["status"] == "connected"


async def test_a_short_token_gets_one_refresh_under_two_workers(
        ns, world, monkeypatch) -> None:  # noqa: F811
    """P2: a token that lives 300 s (the Keycloak default) is due at half its
    life, not at once. The second worker reads the new token under the lock
    and sends nothing (O7 for a short token)."""
    from datetime import UTC, datetime, timedelta

    from gateway.connections import mcp

    name = _name("short")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=300)
    world.expires_in = 300  # each refresh lives 300 s too
    # 200 s later: 100 s left, past half the life, so the token is due.
    shift = timedelta(seconds=200)
    monkeypatch.setattr(mcp, "_utcnow", lambda: datetime.now(UTC) + shift, raising=False)
    _two_processes(monkeypatch)
    world.token_delay_s = 0.5
    one, two = await asyncio.gather(mcp.servers_for_org(ns.org_a),
                                    mcp.servers_for_org(ns.org_a))
    assert len(world.refreshes) == 1, f"{len(world.refreshes)} refreshes went out"
    new = {"Authorization": f"Bearer {f.SENTINEL_ACCESS}-refreshed-1"}
    assert _by_name(one)[name].headers == new and _by_name(two)[name].headers == new
    assert _registry(ns, ns.org_a)[name]["status"] == "connected"
    third = _by_name(await mcp.servers_for_org(ns.org_a))[name]
    assert third.refreshed is False and len(world.refreshes) == 1


async def test_a_row_registered_again_with_headers_is_not_marked(
        ns, world) -> None:  # noqa: F811
    """The noted item: a server deleted and registered again with headers
    while a refresh step waited for its slot is no longer the OAuth row. The
    step leaves it, and never marks it ``undecryptable``."""
    from acb_common.db import tenant_session
    from gateway.connections import mcp, store

    name = _name("rereg")
    await _connected(world, ns.org_a, ADMIN_A, name, TOKEN_A, expires_in=NEAR)
    async with tenant_session(ns.org_a) as db:
        stale = next(c for c in await store.list_by_prefix(
            db, ns.org_a, mcp.PREFIX, mcp.OWNING_APP) if c.provider == mcp.provider_for(name))
    assert await mcp.remove(ns.org_a, name)
    await mcp.save(ns.org_a, name=name, url=f.MCP_URL, headers={"X-Key": "header-value"},
                   connected_by="r8-seed")
    assert await mcp._oauth_header(ns.org_a, stale, None) is None
    row = _registry(ns, ns.org_a)[name]
    assert row["status"] == "connected", "the step marked a header row"
    assert json.loads(row["whole"])["last_error"] is None
    assert world.refreshes == []
