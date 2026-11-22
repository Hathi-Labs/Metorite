"""Task 570 (H5, R5): the room stream and the presence rail are keyed by the org.

``gateway/room_stream.py`` held ``cc:room:<thread>`` (who typed what in a
shared chat) and ``cc:presence:<thread>`` (member emails) on a raw client. A
key carried no org, so a second tenant shared the key space. Slice 1 of task
570 moved both onto the tenant wrapper: ``cc:<org>:room:<thread>`` and
``cc:<org>:presence:<thread>``. The org is the session's
(``user.organization_id``), passed by the route, never a request field.

A fake raw client sits behind the real ``TenantRedis``, so each test sees the
wire keys that production writes.
"""

from __future__ import annotations

import ast
import asyncio
import contextvars
import json
import time
from pathlib import Path
from typing import Any

import pytest
from acb_common.tenant_redis import TenantRedis, organization_bound

from gateway import room_stream

ORG_A = "org-aaaaaaaa"
ORG_B = "org-bbbbbbbb"
THREAD = "thread-1"
_REPO = Path(__file__).resolve().parents[2]


class _Raw:
    """The raw client behind the wrapper: streams, hashes and TTLs in dicts."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}
        self.xadd_kwargs: list[dict[str, Any]] = []
        self._seq = 0

    async def xadd(self, key: str, fields: dict[str, str], **kwargs: Any) -> str:
        self._seq += 1
        eid = f"{self._seq}-0"
        self.streams.setdefault(key, []).append((eid, dict(fields)))
        self.xadd_kwargs.append(kwargs)
        return eid

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def xread(self, streams: dict[str, str], count: int = 100, block: int | None = None):
        out = []
        for key, since in streams.items():
            entries = self.streams.get(key, [])
            if since == "$":
                entries = []
            elif since != "0":
                entries = [e for e in entries if int(e[0].split("-")[0]) > int(since.split("-")[0])]
            if entries:
                out.append((key, entries[:count]))
        if not out and block:
            await asyncio.sleep(0)
        return out

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        bucket = self.hashes.get(key, {})
        return sum(1 for f in fields if bucket.pop(f, None) is not None)


@pytest.fixture
def raw(monkeypatch) -> _Raw:
    fake = _Raw()
    calls: list[dict[str, Any]] = []

    def _get_tenant_redis(**kwargs: Any) -> TenantRedis:
        calls.append(kwargs)
        return TenantRedis(fake)

    monkeypatch.setattr(room_stream, "get_tenant_redis", _get_tenant_redis)
    fake.pool_calls = calls  # type: ignore[attr-defined]
    return fake


async def test_two_orgs_with_the_same_thread_do_not_share_the_room(raw: _Raw) -> None:
    eid = await room_stream.publish_room_event(
        THREAD, {"type": "PARTICIPANT_JOINED", "subject": "a@x.test"}, organization_id=ORG_A,
    )
    assert eid
    assert list(raw.streams) == [f"cc:{ORG_A}:room:{THREAD}"]

    seen_a = await room_stream.replay_room_events(THREAD, organization_id=ORG_A)
    seen_b = await room_stream.replay_room_events(THREAD, organization_id=ORG_B)
    assert [e["type"] for e in seen_a] == ["PARTICIPANT_JOINED"]
    assert seen_b == []


async def test_two_orgs_do_not_share_the_presence_rail(raw: _Raw) -> None:
    await room_stream.touch_presence(THREAD, "a@x.test", organization_id=ORG_A)
    assert list(raw.hashes) == [f"cc:{ORG_A}:presence:{THREAD}"]
    assert [p["email"] for p in await room_stream.read_presence(THREAD, organization_id=ORG_A)] == ["a@x.test"]
    assert await room_stream.read_presence(THREAD, organization_id=ORG_B) == []

    await room_stream.clear_presence(THREAD, "a@x.test", organization_id=ORG_B)
    assert raw.hashes[f"cc:{ORG_A}:presence:{THREAD}"], "org B cleared org A's rail"
    await room_stream.clear_presence(THREAD, "a@x.test", organization_id=ORG_A)
    assert raw.hashes[f"cc:{ORG_A}:presence:{THREAD}"] == {}


async def test_the_same_ttl_and_cap_as_before(raw: _Raw) -> None:
    await room_stream.publish_room_event(THREAD, {"type": "X"}, organization_id=ORG_A)
    await room_stream.touch_presence(THREAD, "a@x.test", organization_id=ORG_A)
    assert raw.ttls == {
        f"cc:{ORG_A}:room:{THREAD}": 24 * 3600,
        f"cc:{ORG_A}:presence:{THREAD}": 24 * 3600,
    }
    assert raw.xadd_kwargs == [{"maxlen": 2_000, "approximate": True}]


async def test_a_stale_presence_entry_is_pruned_in_its_own_org(raw: _Raw) -> None:
    key_a = f"cc:{ORG_A}:presence:{THREAD}"
    raw.hashes[key_a] = {"old@x.test": json.dumps({"at": int(time.time()) - 600})}
    assert await room_stream.read_presence(THREAD, organization_id=ORG_A) == []
    assert raw.hashes[key_a] == {}


async def test_no_org_writes_nothing_and_reads_nothing(raw: _Raw) -> None:
    assert await room_stream.publish_room_event(THREAD, {"type": "X"}, organization_id=None) is None
    await room_stream.touch_presence(THREAD, "a@x.test", organization_id=None)
    await room_stream.clear_presence(THREAD, "a@x.test", organization_id=None)
    assert await room_stream.replay_room_events(THREAD, organization_id=None) == []
    assert await room_stream.read_presence(THREAD, organization_id=None) == []
    assert [e async for e in room_stream.subscribe_room_events(THREAD, organization_id=None)] == []
    assert raw.streams == {} and raw.hashes == {}


async def test_an_old_raw_key_is_not_read(raw: _Raw) -> None:
    raw.streams[f"cc:room:{THREAD}"] = [("1-0", {"event": json.dumps({"type": "OLD"})})]
    raw.hashes[f"cc:presence:{THREAD}"] = {"a@x.test": json.dumps({"at": int(time.time())})}
    assert await room_stream.replay_room_events(THREAD, organization_id=ORG_A) == []
    assert await room_stream.read_presence(THREAD, organization_id=ORG_A) == []


async def test_a_redis_fault_never_raises(monkeypatch) -> None:
    class _Down:
        async def xadd(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("down")

        async def xread(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("down")

        async def hset(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("down")

        async def hgetall(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("down")

    monkeypatch.setattr(room_stream, "get_tenant_redis", lambda **_k: TenantRedis(_Down()))
    assert await room_stream.publish_room_event(THREAD, {"type": "X"}, organization_id=ORG_A) is None
    assert await room_stream.replay_room_events(THREAD, organization_id=ORG_A) == []
    await room_stream.touch_presence(THREAD, "a@x.test", organization_id=ORG_A)
    assert await room_stream.read_presence(THREAD, organization_id=ORG_A) == []


async def test_the_live_tail_reads_on_the_blocking_pool_from_a_task_with_no_tenant(
    raw: _Raw,
) -> None:
    """The SSE generator runs after the request scope. It binds the org it
    was given around each read only, and the consumer never holds it."""
    await room_stream.publish_room_event(THREAD, {"type": "FIRST"}, organization_id=ORG_A)
    await room_stream.publish_room_event(THREAD, {"type": "OTHER"}, organization_id=ORG_B)

    async def _consume() -> tuple[list[str], list[bool]]:
        types: list[str] = []
        bound: list[bool] = []
        async for evt in room_stream.subscribe_room_events(
            THREAD, since_id="0", block_ms=1, organization_id=ORG_A,
        ):
            bound.append(organization_bound())
            types.append(evt["type"])
            if evt["type"] == "ROOM_PING" or len(types) >= 2:
                break
        return types, bound

    task = asyncio.get_running_loop().create_task(_consume(), context=contextvars.Context())
    types, bound = await asyncio.wait_for(task, timeout=5)
    assert types == ["FIRST", "ROOM_PING"]
    assert bound == [False, False]
    assert {"blocking": True} in raw.pool_calls  # type: ignore[attr-defined]


def test_each_caller_passes_the_org_of_the_session() -> None:
    """Every call of a room_stream function names ``organization_id``, and
    the value never comes from the request (R11)."""
    names = {
        "publish_room_event", "replay_room_events", "subscribe_room_events",
        "touch_presence", "clear_presence", "read_presence",
    }
    found = 0
    for rel in (
        "apps/services/gateway/gateway/routes/rooms.py",
        "apps/services/gateway/gateway/routes/agent.py",
    ):
        tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in names:
                continue
            found += 1
            kw = {k.arg: k.value for k in node.keywords}
            assert "organization_id" in kw, f"{rel}:{node.lineno} {name} has no organization_id"
            used = {n.id for n in ast.walk(kw["organization_id"]) if isinstance(n, ast.Name)}
            assert not used & {"req", "request", "payload", "body"}, (
                f"{rel}:{node.lineno} takes the org from the request"
            )
    assert found >= 12
