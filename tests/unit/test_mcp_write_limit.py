"""WS-55 MCP-G28a (Metorite task 2541, D209): the write cap has two counters.

Spec: ``project-docs/specs/external_agent_access.md`` §9.2, "The write limit".

Before this slice, branch 0 counted one write for each signed write, so each
item of ``update_tasks`` cost one write of the cap. A plan of 500 tasks
stalled on the cap. Now the cap has two counters, per connection, both in the
window of ``AGENT_WRITE_WINDOW_S``:

* **Calls** (``AGENT_WRITE_CAP``, default 120). A write with no batch key is
  1 call. All the writes of one ``update_tasks`` call carry one signed batch
  key (``X-Agent-Batch``), and the key costs 1 call for each 50 rows.
* **Rows** (``AGENT_WRITE_ROW_CAP``, default 5000). Each signed write is 1 row.

So a batch of 50 rows costs 1 call and 50 rows, and a single write costs 1
call and 1 row. A refusal names the counter that refused and holds
``retry_after`` (task 844).

**Database-free.** Part A calls the REAL ``deps._claim_write`` on the fake
Redis under the REAL tenant wrapper. Part B runs the edge in process in front
of ``_CapGateway``, which puts the same ``_claim_write`` in front of a fake
board. Part C runs the REAL branch 0 of ``get_current_user`` in a stand-in
gateway, plain and with the tripwire armed.

The R8 case through the real gateway app is
``test_mcp_edge_bulk_updates.py::TestBulkUpdatesOnARealDatabase::test_the_cap_counts_the_call_and_each_row``.

Run::

    PYTEST_ADDOPTS="-o timeout_method=thread" uv run pytest tests/unit/test_mcp_write_limit.py -q

**The mutations (R7).** ``A`` is ``packages/acb_auth/acb_auth`` and ``E`` is
``apps/services/agent_edge/agent_edge``. Each one turns the named test red.

* L1. ``A/deps.py``: ``_claim_write`` counts a call for each row of a batch key. ``test_a_50_row_batch_costs_one_call_and_50_rows``.
* L2. ``A/deps.py``: ``_claim_write`` skips the row counter. ``test_5000_rows_trip_the_row_counter``.
* L3. ``A/deps.py``: ``_claim_write`` keeps the batch key after the call counter refused it. ``test_a_refused_batch_gets_no_free_rows``.
* L4. ``A/deps.py``: the batch key leaves out the connection. ``test_the_counters_are_per_connection``.
* L5. ``A/deps.py``: a batch key counts a call for its first row only. ``test_a_batch_key_counts_one_call_for_each_50_rows``.
* L6. ``A/deps.py``: ``_verify_signed_request`` leaves the batch key out of the signed string. ``test_an_unsigned_batch_key_refuses``.
* L7. ``A/deps.py``: ``_verify_signed_request`` takes a batch key on a GET. ``test_a_batch_key_on_a_get_refuses``.
* L8. ``A/deps.py``: the row refusal sends the detail of the call counter. ``test_the_refusal_names_the_counter_and_holds_retry_after``.
* L9. ``E/tools.py``: ``_update_tasks`` sends no batch key. ``test_update_tasks_sends_one_batch_key_for_each_call``.
* L10. ``E/tools.py``: ``_cap_text`` gives ``TEXT_CAP`` for each detail. ``test_the_refusal_names_the_counter_and_holds_retry_after``.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import (
    agent_oauth,
    agent_signing,
    deps,
    get_current_user,
    require_authenticated,
)
from acb_auth.roles import UserContext
from acb_common import get_settings
from agent_edge import gateway_client, tools
from fastapi import Depends, FastAPI, HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError

from tests.unit import test_mcp_edge_bulk_updates as b
from tests.unit import test_mcp_edge_signing as s
from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_write_clarity as c
from tests.unit import test_mcp_edge_writes as w

edge, redis_fake = t.edge, t.redis_fake
world = s.world
_tool, _text, _post, _call = w._tool, w._text, w._post, w._call

ORG = w.ORG
CONN_A = w.CONN
CONN_B = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
OTHER_ORG = "99999999-2222-4333-8444-555555555555"


# ═══════════════════════════════════════════════════════════════════════════
# Part A: the REAL _claim_write on the fake Redis
# ═══════════════════════════════════════════════════════════════════════════


def _conn(connection_id: str = CONN_A, org: str = ORG) -> SimpleNamespace:
    return SimpleNamespace(scopes=frozenset({agent_oauth.PROJECTS_WRITE}),
                           organization_id=org, connection_id=connection_id)


async def _claim(connection: SimpleNamespace, batch: str | None = None) -> HTTPException | None:
    """One signed write through the cap: ``None``, or the 429 or 503."""
    try:
        refused = await deps._claim_write(
            connection, agent_signing.new_nonce(), agent_oauth.PROJECTS_WRITE, batch)
    except HTTPException as exc:
        return exc
    assert refused is None, refused
    return None


def _count(fake: Any, namespace: str, connection_id: str = CONN_A, org: str = ORG) -> int:
    found = fake.store.get(f"cc:{org}:{namespace}:{connection_id}")
    return int(found[0]) if found else 0


def _calls(fake: Any, connection_id: str = CONN_A, org: str = ORG) -> int:
    return _count(fake, agent_oauth.WRITE_CAP_NAMESPACE, connection_id, org)


def _rows(fake: Any, connection_id: str = CONN_A, org: str = ORG) -> int:
    return _count(fake, agent_oauth.WRITE_ROW_CAP_NAMESPACE, connection_id, org)


def _is_cap(exc: HTTPException | None, detail: str) -> None:
    assert exc is not None, "the cap let the write through"
    assert exc.status_code == 429
    assert exc.detail == detail
    assert 1 <= int(exc.headers["Retry-After"]) <= agent_oauth.write_window_s()


class TestTheDefaults:
    def test_the_defaults_keep_a_single_write_agent_as_strict_as_before(self):
        """A plain single-write agent meets the call counter at 120, as it met
        the old cap. The row counter is 5000. The window is 600 seconds."""
        assert (agent_oauth.WRITE_CAP, agent_oauth.WRITE_ROW_CAP,
                agent_oauth.WRITE_WINDOW_S) == (120, 5000, 600)
        settings = get_settings()
        assert (settings.agent_write_cap, settings.agent_write_row_cap,
                settings.agent_write_window_s) == (120, 5000, 600)
        assert agent_oauth.WRITE_ROWS_PER_CALL == tools.MAX_BULK_UPDATES == 50

    def test_the_counters_have_their_own_namespaces(self):
        names = {agent_oauth.WRITE_CAP_NAMESPACE, agent_oauth.WRITE_ROW_CAP_NAMESPACE,
                 agent_oauth.WRITE_BATCH_NAMESPACE}
        assert len(names) == 3
        # The call counter keeps the key of before, so a live window carries on.
        assert agent_oauth.WRITE_CAP_NAMESPACE == "agent_writes"
        assert agent_oauth.WRITE_CAP_DETAIL != agent_oauth.WRITE_ROW_CAP_DETAIL


class TestTheTwoCounters:
    async def test_a_single_write_costs_one_call_and_one_row(self, redis_fake):
        assert await _claim(_conn()) is None
        assert (_calls(redis_fake), _rows(redis_fake)) == (1, 1)

    async def test_a_50_row_batch_costs_one_call_and_50_rows(self, redis_fake):
        batch = agent_signing.new_batch_key()
        for _ in range(50):
            assert await _claim(_conn(), batch) is None
        assert (_calls(redis_fake), _rows(redis_fake)) == (1, 50)

    async def test_120_single_writes_trip_the_call_counter(self, redis_fake):
        for _ in range(120):
            assert await _claim(_conn()) is None
        over = await _claim(_conn())
        _is_cap(over, agent_oauth.WRITE_CAP_DETAIL)
        assert over.headers["Retry-After"] == "600"
        # The refused write counts a call, and no row.
        assert (_calls(redis_fake), _rows(redis_fake)) == (121, 120)

    async def test_5000_rows_trip_the_row_counter(self, redis_fake):
        """100 calls of 50 rows are 100 calls and 5000 rows, under both caps.
        The next row meets the row counter, in a batch and alone."""
        for _ in range(100):
            batch = agent_signing.new_batch_key()
            for _ in range(50):
                assert await _claim(_conn(), batch) is None
        assert (_calls(redis_fake), _rows(redis_fake)) == (100, 5000)
        _is_cap(await _claim(_conn(), agent_signing.new_batch_key()),
                agent_oauth.WRITE_ROW_CAP_DETAIL)
        _is_cap(await _claim(_conn()), agent_oauth.WRITE_ROW_CAP_DETAIL)
        # The call counter is still under its cap of 120.
        assert _calls(redis_fake) == 102

    async def test_a_batch_key_counts_one_call_for_each_50_rows(self, redis_fake):
        """The most rows of one call (``WRITE_ROWS_PER_CALL``). A key that the
        edge sent again for more rows never escapes the call counter."""
        batch = agent_signing.new_batch_key()
        for _ in range(51):
            assert await _claim(_conn(), batch) is None
        assert (_calls(redis_fake), _rows(redis_fake)) == (2, 51)
        for _ in range(50):
            assert await _claim(_conn(), batch) is None
        assert (_calls(redis_fake), _rows(redis_fake)) == (3, 101)

    async def test_a_refused_batch_gets_no_free_rows(self, redis_fake, monkeypatch):
        """The call counter refuses the first row of a key. A second row of
        the same key must pay the call again, so it is refused too."""
        monkeypatch.setattr(get_settings(), "agent_write_cap", 1)
        assert await _claim(_conn()) is None
        batch = agent_signing.new_batch_key()
        _is_cap(await _claim(_conn(), batch), agent_oauth.WRITE_CAP_DETAIL)
        _is_cap(await _claim(_conn(), batch), agent_oauth.WRITE_CAP_DETAIL)
        assert _rows(redis_fake) == 1

    async def test_the_counters_are_per_connection(self, redis_fake, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_write_cap", 2)
        monkeypatch.setattr(settings, "agent_write_row_cap", 3)
        shared = agent_signing.new_batch_key()
        # A: one row of the shared key (1 call), one single write (2 calls).
        assert await _claim(_conn(CONN_A), shared) is None
        assert await _claim(_conn(CONN_A)) is None
        _is_cap(await _claim(_conn(CONN_A)), agent_oauth.WRITE_CAP_DETAIL)
        # B is not touched by A, and the same key string costs B its own call.
        assert await _claim(_conn(CONN_B), shared) is None
        assert (_calls(redis_fake, CONN_B), _rows(redis_fake, CONN_B)) == (1, 1)
        assert await _claim(_conn(CONN_B), shared) is None
        assert await _claim(_conn(CONN_B), shared) is None
        _is_cap(await _claim(_conn(CONN_B), shared), agent_oauth.WRITE_ROW_CAP_DETAIL)
        assert (_calls(redis_fake, CONN_A), _rows(redis_fake, CONN_A)) == (3, 2)
        # Another organization has counters of its own (the tenant prefix, R5).
        assert await _claim(_conn(CONN_A, OTHER_ORG)) is None
        assert (_calls(redis_fake, CONN_A, OTHER_ORG), _rows(redis_fake, CONN_A, OTHER_ORG)) == (
            1, 1)
        assert all(k.startswith((f"cc:{ORG}:", f"cc:{OTHER_ORG}:")) for k in redis_fake.store)

    async def test_both_windows_are_the_setting(self, redis_fake, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_write_window_s", 900)
        batch = agent_signing.new_batch_key()
        assert await _claim(_conn(), batch) is None
        end = redis_fake.now + 900
        for namespace in (agent_oauth.WRITE_CAP_NAMESPACE, agent_oauth.WRITE_ROW_CAP_NAMESPACE):
            assert redis_fake.store[f"cc:{ORG}:{namespace}:{CONN_A}"][1] == end
        batch_key = f"cc:{ORG}:{agent_oauth.WRITE_BATCH_NAMESPACE}:{CONN_A}:{batch}"
        assert redis_fake.store[batch_key][1] == end

    async def test_a_wait_of_the_named_time_clears_the_counter(self, redis_fake, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_write_row_cap", 1)
        assert await _claim(_conn()) is None
        over = await _claim(_conn())
        _is_cap(over, agent_oauth.WRITE_ROW_CAP_DETAIL)
        redis_fake.now += int(over.headers["Retry-After"])
        assert await _claim(_conn()) is None

    async def test_an_outage_is_the_503_never_a_401(self, redis_fake):
        redis_fake.fail = RedisConnectionError("refused")
        exc = await _claim(_conn(), agent_signing.new_batch_key())
        assert exc is not None and exc.status_code == 503
        assert exc.headers["Retry-After"] == "2"


_REDIS_URL = w._REDIS_URL
_REAL_REDIS_GATE = pytest.mark.skipif(
    not _REDIS_URL,
    reason=("AGENT_WRITES_REDIS_URL unset — this class runs the two counters on a "
            "REAL Redis 7. A skip is not a pass."))


@pytest.fixture
async def real_redis(monkeypatch):
    from acb_common.tenant_redis import TenantRedis
    from redis import asyncio as aioredis

    client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    monkeypatch.setattr(deps.tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(client))
    try:
        yield client
    finally:
        await client.aclose()


class TestOnARealRedis:
    """The batch key, both counters and the delete on a real Redis 7. Each
    run uses a new organization id, so no key of another run is touched."""

    pytestmark = _REAL_REDIS_GATE

    async def test_the_two_counters_on_a_real_redis(self, real_redis, monkeypatch):
        import uuid

        org = str(uuid.uuid4())
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_write_cap", 2)
        monkeypatch.setattr(settings, "agent_write_row_cap", 52)
        batch = agent_signing.new_batch_key()
        for _ in range(50):
            assert await _claim(_conn(CONN_A, org), batch) is None
        calls = f"cc:{org}:{agent_oauth.WRITE_CAP_NAMESPACE}:{CONN_A}"
        rows = f"cc:{org}:{agent_oauth.WRITE_ROW_CAP_NAMESPACE}:{CONN_A}"
        key = f"cc:{org}:{agent_oauth.WRITE_BATCH_NAMESPACE}:{CONN_A}:{batch}"
        assert (await real_redis.get(calls), await real_redis.get(rows)) == ("1", "50")
        assert await real_redis.get(key) == "50"
        for name in (calls, rows, key):
            assert 0 < await real_redis.ttl(name) <= 600
        assert await _claim(_conn(CONN_A, org)) is None
        # The 51st row of the key pays a call, and the call counter is full.
        _is_cap(await _claim(_conn(CONN_A, org), batch), agent_oauth.WRITE_CAP_DETAIL)
        assert await real_redis.get(key) is None
        assert await real_redis.get(rows) == "51"
        for name in (calls, rows):
            await real_redis.delete(name)


# ═══════════════════════════════════════════════════════════════════════════
# Part B: the edge in front of the REAL cap
# ═══════════════════════════════════════════════════════════════════════════


def _items(start: int, count: int) -> list[dict[str, Any]]:
    return [b._item(b._id(i), title=f"g28a {i}") for i in range(start, start + count)]


class TestTheEdge:
    async def test_update_tasks_sends_one_batch_key_for_each_call(self, edge, redis_fake):
        board = b._Board(8)
        gate = c._CapGateway(board)
        with gateway_client.use_transport(httpx.ASGITransport(app=gate)):
            first = b._results(await _tool("update_tasks", {"updates": _items(0, 3)}))
            second = b._results(await _tool("update_tasks", {"updates": _items(3, 3)}))
            single = await _tool("update_task", b._item(b._id(6), title="alone"))
        assert all(r["ok"] for r in first + second) and single["isError"] is False
        assert len(gate.batches) == 7
        one, two = set(gate.batches[:3]), set(gate.batches[3:6])
        assert len(one) == len(two) == 1, gate.batches
        assert one != two
        assert all(agent_signing.batch_key_is_well_formed(k) for k in one | two)
        assert gate.batches[6] is None
        assert (_calls(redis_fake), _rows(redis_fake)) == (3, 7)

    async def test_a_50_item_call_costs_one_call_and_50_rows(self, edge, redis_fake):
        board = b._Board(50)
        with gateway_client.use_transport(httpx.ASGITransport(app=c._CapGateway(board))):
            rows = b._results(await _tool("update_tasks", {"updates": _items(0, 50)}))
        assert len(rows) == 50 and all(r["ok"] for r in rows)
        assert (_calls(redis_fake), _rows(redis_fake)) == (1, 50)

    async def test_the_refusal_names_the_counter_and_holds_retry_after(
            self, edge, redis_fake, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_write_cap", 2)
        monkeypatch.setattr(settings, "agent_write_row_cap", 4)
        board = b._Board(9)
        with gateway_client.use_transport(httpx.ASGITransport(app=c._CapGateway(board))):
            # 1 call and 3 rows, then 1 call and 1 row: the row counter is full.
            assert all(r["ok"] for r in b._results(
                await _tool("update_tasks", {"updates": _items(0, 3)})))
            rows = b._results(await _tool("update_tasks", {"updates": _items(3, 3)}))
            # The call counter is full too now.
            calls = await _post(_call("update_task", b._item(b._id(8), title="x")))
        assert [(r["ok"], r["tried"]) for r in rows] == [
            (True, True), (False, True), (False, False)]
        assert rows[1]["error"] == rows[2]["error"] == tools.TEXT_ROW_CAP
        assert rows[1]["retry_after"] == rows[2]["retry_after"] == 600
        result = calls.json()["result"]
        assert result["content"][0]["text"] == tools.TEXT_CALL_CAP
        assert result["structuredContent"] == {"retry_after": 600}
        assert result["content"][1]["text"] == "Try again in 600 seconds."
        assert calls.headers["retry-after"] == "600"
        # The texts name the counter, and neither holds the gateway detail.
        assert "write calls" in tools.TEXT_CALL_CAP and "rows" in tools.TEXT_ROW_CAP
        for text in (tools.TEXT_CALL_CAP, tools.TEXT_ROW_CAP):
            assert text not in (agent_oauth.WRITE_CAP_DETAIL, agent_oauth.WRITE_ROW_CAP_DETAIL)
        assert board.writes == 4

    @pytest.mark.parametrize("detail,text", [
        (agent_oauth.WRITE_CAP_DETAIL, tools.TEXT_CALL_CAP),
        (agent_oauth.WRITE_ROW_CAP_DETAIL, tools.TEXT_ROW_CAP),
        ("Too many changes from this connection. Wait a few minutes.", tools.TEXT_CAP),
        ("SECRET", tools.TEXT_CAP),
    ])
    def test_a_429_maps_its_detail_to_one_fixed_text(self, detail, text):
        answer = gateway_client.Answer(status=429, body={"detail": detail}, retry_after=7)
        with pytest.raises(tools.ToolError) as raised:
            tools._body_of(answer, tools.TEXT_TASK_NOT_FOUND, write=True)
        assert (raised.value.text, raised.value.retry_after) == (text, 7)

    def test_each_cap_text_stops_a_bulk_call(self):
        assert {tools.TEXT_CAP, tools.TEXT_CALL_CAP, tools.TEXT_ROW_CAP} <= (
            tools._BULK_STOP_TEXTS)

    def test_only_update_tasks_sets_the_batch_key(self):
        """The key comes from the edge, never from a tool input. ``_write``
        passes the key of its context on, and only ``_update_tasks`` mints
        one."""
        source = Path(tools.__file__).read_text(encoding="utf-8")
        assert re.findall(r"\bbatch=([A-Za-z_.]+)", source) == [
            "ctx.batch", "new_batch_key"]
        update_tasks = inspect.getsource(tools._update_tasks)
        assert "replace(ctx, batch=new_batch_key())" in update_tasks


# ═══════════════════════════════════════════════════════════════════════════
# Part C: the REAL branch 0 reads the signed batch key
# ═══════════════════════════════════════════════════════════════════════════

TASK = s.TASK


def _write_app() -> FastAPI:
    """A stand-in gateway with one write pair of ``MCP_WRITE_ROUTES``."""
    app = FastAPI(dependencies=[require_authenticated(public=frozenset({"/healthz"}))])
    if s._ARMED:
        app.dependency_overrides[get_current_user] = (
            s.tripwire.current_user_override(s._ARMED[0]))

    @app.patch("/projects/tasks/{task_id}")
    async def _patch(task_id: str, user: UserContext = Depends(get_current_user)) -> dict:
        return {"email": user.email}

    @app.get("/projects/tasks/{task_id}")
    async def _read(task_id: str, user: UserContext = Depends(get_current_user)) -> dict:
        return {"email": user.email}

    return app


async def _send(method: str, headers: list[tuple[str, str]] | dict[str, str],
                body: bytes = b"") -> httpx.Response:
    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_write_app()),
                                     base_url="http://gateway") as client:
            return await client.request(method, TASK, headers=headers, content=body)

    return await asyncio.create_task(_go())


def _signed_write(body: bytes, batch: str | None = None) -> dict[str, str]:
    return agent_signing.signed_headers(
        s.KEY, organization_id=s.ORG_A, connection_id=s.CONN, actor_via=s.VIA,
        method="PATCH", path=TASK, body=body, batch=batch)


@pytest.fixture
def writer(world, redis_fake, monkeypatch):
    """The stand-in world of ``test_mcp_edge_signing.py``, with writes on and
    a connection that holds ``projects:write``."""
    monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", True)
    key = (s.ORG_A, s.CONN)
    world.connections[key] = dataclasses.replace(
        world.connections[key], scopes=(agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE))
    return SimpleNamespace(world=world, redis=redis_fake)


def _branch_calls(fake: Any) -> tuple[int, int]:
    return (_calls(fake, s.CONN, s.ORG_A), _rows(fake, s.CONN, s.ORG_A))


class TestBranchZero:
    async def test_a_signed_batch_key_counts_the_call_once(self, writer):
        batch = agent_signing.new_batch_key()
        for n in range(3):
            body = json.dumps({"title": f"t{n}"}).encode()
            r = await _send("PATCH", _signed_write(body, batch), body)
            assert r.status_code == 200, r.text
        body = b'{"title":"alone"}'
        assert (await _send("PATCH", _signed_write(body), body)).status_code == 200
        assert _branch_calls(writer.redis) == (2, 4)

    async def test_an_unsigned_batch_key_refuses(self, writer):
        """The key comes ONLY from the signed header. A key added after the
        signing, or another key, breaks the signature."""
        body = b'{"title":"t"}'
        added = {**_signed_write(body), agent_signing.HEADER_BATCH: agent_signing.new_batch_key()}
        assert (await _send("PATCH", added, body)).status_code == 401
        swapped = _signed_write(body, agent_signing.new_batch_key())
        swapped[agent_signing.HEADER_BATCH] = agent_signing.new_batch_key()
        assert (await _send("PATCH", swapped, body)).status_code == 401
        assert _branch_calls(writer.redis) == (0, 0)

    async def test_a_batch_key_on_a_get_refuses(self, writer):
        signed = agent_signing.signed_headers(
            s.KEY, organization_id=s.ORG_A, connection_id=s.CONN, actor_via=s.VIA,
            method="GET", path=TASK)
        assert (await _send("GET", signed)).status_code == 200
        signed[agent_signing.HEADER_BATCH] = agent_signing.new_batch_key()
        assert (await _send("GET", signed)).status_code == 401
        with pytest.raises(ValueError):
            agent_signing.signed_headers(
                s.KEY, organization_id=s.ORG_A, connection_id=s.CONN, actor_via=s.VIA,
                method="GET", path=TASK, batch=agent_signing.new_batch_key())

    @pytest.mark.parametrize("bad", ["short", "A" * 23, "A" * 21 + ".", "A" * 21 + "\n"])
    async def test_a_batch_key_of_another_shape_refuses(self, writer, bad):
        body = b'{"title":"t"}'
        signed = {**_signed_write(body), agent_signing.HEADER_BATCH: bad}
        assert (await _send("PATCH", signed, body)).status_code == 401
        with pytest.raises(ValueError):
            _signed_write(body, bad)

    async def test_two_batch_headers_refuse(self, writer):
        body = b'{"title":"t"}'
        batch = agent_signing.new_batch_key()
        headers = [*_signed_write(body, batch).items(), (agent_signing.HEADER_BATCH, batch)]
        assert (await _send("PATCH", headers, body)).status_code == 401


def test_a_write_with_no_batch_signs_the_string_of_before():
    """No batch key, no line: the string is byte for byte the one before
    MCP-G28a, so an old edge still agrees with a new gateway. With a key, the
    last line is ``batch:<key>``, after the nonce and the session line."""
    base = dict(timestamp="1", organization_id=s.ORG_A, connection_id=s.CONN,
                actor_via=s.VIA, method="PATCH", path=TASK, nonce="N" * 22)
    plain = agent_signing.canonical_string(**base)
    assert len(plain.split("\n")) == 9
    batch = "B" * 22
    assert agent_signing.canonical_string(**base, batch=batch) == plain + "\nbatch:" + batch
    both = agent_signing.canonical_string(**base, session=s.SESSION, batch=batch)
    assert both.split("\n")[-3:] == ["N" * 22, "session:" + s.SESSION, "batch:" + batch]
