"""The live activity feed is keyed for each tenant (H-201c, task 1718).

A member of org B sees no activity entry of org A on the four routes that
read the feed: ``/observability/activity/recent``, ``/activity/stream``,
``/active`` and the presence set of ``/roster``.

The design (``acb_common.activity``):

* a writer captures the org at call time, the explicit ``organization_id``
  else the bound tenant, and writes ``cc:<org>:activity`` and
  ``cc:<org>:activity:live:<run>`` through ``acb_common.tenant_redis``;
* a writer with no org writes NOTHING, so no global key exists;
* each reader takes a required ``organization_id`` from the route, which
  takes it from the session (``viewer.organization_id``), and a falsy org
  gives an empty answer.

The fake is a raw Redis, patched at ``aioredis.from_url``, so every pool of
``tenant_redis`` and every one-shot client meets the same store. The test
reads the wire keys, so a key with no tenant is visible.

The fake ``XREAD`` does not block. It reads ``$`` as "from the start", so the
stream route sees the entries that the test wrote before it opened. The real
command reads only new entries. For isolation this is the stronger check: the
reader of org B can see every entry of its own key, and must see nothing of A.

Mutations this suite catches (R7). Each was run, and each turned it red:

* M1: ``publish_activity`` and ``recent_activity`` back on the global
  ``cc:activity`` key;
* M2: the ``active_runs`` SCAN on a raw ``cc:*:activity:live:*`` pattern;
* M3: no no-org guard, so a write with no tenant goes to a global key;
* M4: the readers take the ambient (writer's) tenant, not the viewer's org.
* M5: the early ``if not org: return`` in ``publish_activity``, so a priced
  call with no tenant no longer folds into the global ``cc:cost:{day}``;
* M6: no sleep after a failed ``XREAD``, so the SSE loop spins on a full pool.

Run::

    uv run pytest tests/unit/test_activity_feed_tenant.py -q
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import uuid
from typing import Any

import pytest

_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_ALICE = "alice@feed-a.test"
_CAROL = "carol@feed-b.test"


# ---------------------------------------------------------------------------
# A fake raw Redis: strings, streams and a SCAN that answers for real.
# ---------------------------------------------------------------------------

class _FakePipe:
    def __init__(self, owner: _FakeRedis) -> None:
        self._owner = owner
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str):
        def _op(*args: Any, **_kw: Any) -> _FakePipe:
            self._ops.append((name, args))
            return self
        return _op

    async def execute(self) -> list[Any]:
        for _name, args in self._ops:
            if args and isinstance(args[0], str):
                self._owner.hashes.setdefault(args[0], {})
        return [True] * len(self._ops)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.hashes: dict[str, dict[str, Any]] = {}
        self.scans: list[str] = []
        self._seq = 0

    def keys_written(self) -> set[str]:
        return set(self.store) | set(self.streams) | set(self.hashes)

    # strings
    async def set(self, key, value, ex=None, xx=False, **_kw):
        if xx and key not in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        n = 0
        for k in keys:
            n += int(self.store.pop(k, None) is not None)
        return n

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    # streams
    async def xadd(self, key, fields, maxlen=None, approximate=True, **_kw):
        self._seq += 1
        eid = f"{self._seq}-0"
        self.streams.setdefault(key, []).append((eid, dict(fields)))
        return eid

    async def xrevrange(self, key, max="+", min="-", count=None):
        entries = list(reversed(self.streams.get(key, [])))
        return entries[:count] if count else entries

    async def xread(self, streams, count=None, block=None):
        out = []
        for key, cursor in streams.items():
            after = 0 if cursor in ("$", "0", "0-0") else int(str(cursor).split("-")[0])
            entries = [
                (eid, f) for eid, f in self.streams.get(key, [])
                if int(eid.split("-")[0]) > after
            ]
            if count:
                entries = entries[:count]
            if entries:
                out.append((key, entries))
        return out

    # the keyspace walk, recorded and answered for real
    async def scan_iter(self, match=None, count=None):
        self.scans.append(str(match))
        for k in list(self.store):
            if fnmatch.fnmatchcase(k, match or "*"):
                yield k

    # hashes (the cost rollup, which stays on the raw client)
    def pipeline(self, transaction=False):
        return _FakePipe(self)

    async def aclose(self):
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    import redis.asyncio as aioredis
    from acb_common import activity, tenant_redis

    r = _FakeRedis()
    monkeypatch.setattr(aioredis, "from_url", lambda *_a, **_kw: r)
    tenant_redis.reset_pool_for_tests()
    monkeypatch.setattr(activity, "_client", None)
    yield r
    tenant_redis.reset_pool_for_tests()


@pytest.fixture
def roster_stub(monkeypatch):
    """The roster with one registered agent. The registry, the aliases and
    the avatars are stubs, as in test_h201_readers_under_rls.py. The presence
    set is the real reader over the fake Redis."""
    from gateway.routes import agent as agent_routes
    from gateway.routes import observability

    name = f"feed-agent-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [{"name": name, "description": "d"}])
    monkeypatch.setattr(agent_routes, "_load_dynamic_agents", lambda: [])
    monkeypatch.setattr(agent_routes, "_load_agent_aliases", lambda: {})
    monkeypatch.setattr(observability, "_load_avatars", lambda _org: {})
    monkeypatch.setattr(observability, "_load_app_details", lambda _s, _o: {})
    return name


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


async def _drain() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _publish_run(org: str, agent: str, run_id: str) -> None:
    """What a run does under its bound tenant: an agent start and a model call."""
    from acb_common import publish_activity
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        publish_activity(kind="agent", phase="start", agent=agent, run_id=run_id,
                         source="chat")
        publish_activity(kind="model", model="feed-model", agent=agent, run_id=run_id)
    finally:
        release_tenant(token)
    await _drain()


async def _stream_frames(viewer, *, limit: int = 10) -> list[bytes]:
    """Frames of the SSE route, up to the first heartbeat or the end."""
    from gateway.routes.observability import activity_stream

    resp = await activity_stream(viewer=viewer)
    gen = resp.body_iterator
    frames: list[bytes] = []
    try:
        async for frame in gen:
            raw = frame if isinstance(frame, bytes) else frame.encode()
            frames.append(raw)
            if raw.startswith(b": ping") or len(frames) >= limit:
                break
    finally:
        await gen.aclose()
    return frames


def _data(frames: list[bytes]) -> list[dict[str, Any]]:
    return [
        json.loads(f[len(b"data: "):].decode())
        for f in frames if f.startswith(b"data: ")
    ]


# ---------------------------------------------------------------------------
# The four routes: org B sees nothing of org A, and org A sees its own.
# ---------------------------------------------------------------------------

def test_the_four_routes_show_each_org_only_its_own_feed(fake_redis, roster_stub):
    from gateway.routes.observability import active, activity_recent, roster

    agent = roster_stub
    run_a = f"run-a-{uuid.uuid4().hex[:8]}"
    alice, carol = _user(_ALICE, _ORG_A), _user(_CAROL, _ORG_B)

    async def _go() -> dict[str, Any]:
        await _publish_run(_ORG_A, agent, run_a)
        return {
            "recent_a": await activity_recent(limit=100, viewer=alice),
            "recent_b": await activity_recent(limit=100, viewer=carol),
            "active_a": await active(viewer=alice),
            "active_b": await active(viewer=carol),
            "roster_a": await roster(viewer=alice),
            "roster_b": await roster(viewer=carol),
            "stream_a": await _stream_frames(alice),
            "stream_b": await _stream_frames(carol),
        }

    out = asyncio.run(_go())

    # /activity/recent
    a_events = out["recent_a"]["events"]
    assert {e.get("kind") for e in a_events} == {"agent", "model"}
    assert all(e.get("run_id") == run_a for e in a_events)
    assert out["recent_b"] == {"events": [], "count": 0}

    # /active
    assert [r["run_id"] for r in out["active_a"]["runs"]] == [run_a]
    assert out["active_b"] == {"runs": [], "count": 0}

    # /roster: the presence set
    [entry_a] = [e for e in out["roster_a"]["agents"] if e["name"] == agent]
    assert entry_a["status"] == "working" and entry_a["active_runs"] == 1
    [entry_b] = [e for e in out["roster_b"]["agents"] if e["name"] == agent]
    assert entry_b["status"] == "idle" and entry_b["active_runs"] == 0
    assert not any(e["status"] == "working" for e in out["roster_b"]["agents"])

    # /activity/stream
    assert {e.get("run_id") for e in _data(out["stream_a"])} == {run_a}
    assert _data(out["stream_b"]) == []
    assert out["stream_b"][0] == b": connected\n\n"


def test_each_key_starts_with_its_org(fake_redis, roster_stub):
    async def _go() -> None:
        await _publish_run(_ORG_A, roster_stub, "run-key-a")
        await _publish_run(_ORG_B, roster_stub, "run-key-b")

    asyncio.run(_go())
    written = fake_redis.keys_written()
    assert f"cc:{_ORG_A}:activity" in written
    assert f"cc:{_ORG_B}:activity" in written
    assert f"cc:{_ORG_A}:activity:live:run-key-a" in written
    assert f"cc:{_ORG_B}:activity:live:run-key-b" in written
    assert "cc:activity" not in written
    feed_keys = {k for k in written if not k.startswith("cc:cost:")}
    assert all(
        k.startswith((f"cc:{_ORG_A}:", f"cc:{_ORG_B}:")) for k in feed_keys
    ), feed_keys


def test_active_scans_only_the_viewers_prefix(fake_redis, roster_stub):
    from gateway.routes.observability import active

    async def _go() -> None:
        await _publish_run(_ORG_A, roster_stub, "run-scan-a")
        await active(viewer=_user(_CAROL, _ORG_B))

    asyncio.run(_go())
    assert fake_redis.scans == [f"cc:{_ORG_B}:activity:live:*"]


def test_a_viewer_with_no_org_gets_an_empty_answer(fake_redis, roster_stub):
    from gateway.routes.observability import active, activity_recent, roster

    nobody = _user(_ALICE, None)

    async def _go() -> dict[str, Any]:
        await _publish_run(_ORG_A, roster_stub, "run-none-a")
        return {
            "recent": await activity_recent(limit=100, viewer=nobody),
            "active": await active(viewer=nobody),
            "roster": await roster(viewer=nobody),
            "stream": await _stream_frames(nobody),
        }

    out = asyncio.run(_go())
    assert out["recent"] == {"events": [], "count": 0}
    assert out["active"] == {"runs": [], "count": 0}
    assert not any(e["status"] == "working" for e in out["roster"]["agents"])
    # The stream opens, sends no event, and ends. No busy loop.
    assert out["stream"] == [b": connected\n\n"]
    assert fake_redis.scans == []


def test_the_reader_follows_the_viewer_not_the_ambient_tenant(fake_redis, roster_stub):
    """A reader in a context bound to org A still answers for its viewer."""
    from acb_common.db import bind_tenant, release_tenant
    from gateway.routes.observability import active, activity_recent

    carol = _user(_CAROL, _ORG_B)

    async def _go() -> tuple[dict, dict]:
        await _publish_run(_ORG_A, roster_stub, "run-amb-a")
        token = bind_tenant(_ORG_A)
        try:
            return (await activity_recent(limit=100, viewer=carol),
                    await active(viewer=carol))
        finally:
            release_tenant(token)

    recent, live = asyncio.run(_go())
    assert recent == {"events": [], "count": 0}
    assert live == {"runs": [], "count": 0}


# ---------------------------------------------------------------------------
# The write side.
# ---------------------------------------------------------------------------

def test_a_publish_with_no_tenant_writes_no_key(fake_redis):
    from acb_common import publish_activity, refresh_run_presence

    async def _go() -> None:
        publish_activity(kind="agent", phase="start", agent="x", run_id="run-orphan")
        publish_activity(kind="model", model="m", run_id="run-orphan")
        refresh_run_presence("run-orphan", instance="u:a@b.test")
        await _drain()

    asyncio.run(_go())
    # The no-loop path drops it too.
    publish_activity(kind="agent", phase="start", agent="x", run_id="run-orphan-2")
    assert fake_redis.keys_written() == set()


def test_the_explicit_org_wins_over_the_bound_tenant(fake_redis):
    from acb_common import publish_activity
    from acb_common.db import bind_tenant, release_tenant

    async def _go() -> None:
        token = bind_tenant(_ORG_A)
        try:
            publish_activity(organization_id=_ORG_B, kind="workflow", agent="w",
                             run_id="run-explicit")
        finally:
            release_tenant(token)
        await _drain()

    asyncio.run(_go())
    assert set(fake_redis.streams) == {f"cc:{_ORG_B}:activity"}
    [(_eid, fields)] = fake_redis.streams[f"cc:{_ORG_B}:activity"]
    assert "organization_id" not in json.loads(fields["event"])


def test_the_no_loop_path_writes_under_the_org(fake_redis):
    from acb_common import publish_activity
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(_ORG_A)
    try:
        publish_activity(kind="model", model="m", run_id="run-sync")
    finally:
        release_tenant(token)
    assert set(fake_redis.streams) == {f"cc:{_ORG_A}:activity"}


def test_refresh_run_presence_patches_the_org_key(fake_redis):
    from acb_common import activity, publish_activity, refresh_run_presence
    from acb_common.db import bind_tenant, release_tenant

    async def _go() -> list[dict[str, Any]]:
        token = bind_tenant(_ORG_A)
        try:
            publish_activity(kind="agent", phase="start", agent="x", run_id="run-p")
            await _drain()
            refresh_run_presence("run-p", instance="u:alice@feed-a.test")
            await _drain()
        finally:
            release_tenant(token)
        return await activity.active_runs(organization_id=_ORG_A)

    live = asyncio.run(_go())
    snap = json.loads(fake_redis.store[f"cc:{_ORG_A}:activity:live:run-p"])
    assert snap["instance"] == "u:alice@feed-a.test"
    assert [e["instance"] for e in live] == ["u:alice@feed-a.test"]
    assert list(fake_redis.store) == [f"cc:{_ORG_A}:activity:live:run-p"]


def test_the_stream_read_uses_the_blocking_pool(fake_redis, monkeypatch):
    """The SSE ``XREAD BLOCK`` never takes a connection of the shared pool."""
    from acb_common import activity

    seen: list[bool] = []
    real = activity.get_tenant_redis

    def _spy(**kw):
        seen.append(bool(kw.get("blocking")))
        return real(**kw)

    monkeypatch.setattr(activity, "get_tenant_redis", _spy)
    asyncio.run(activity.read_activity_since("$", organization_id=_ORG_A, block_ms=1))
    assert seen == [True]


# ---------------------------------------------------------------------------
# The cost rollup stays global: a priced call with no tenant still counts.
# ---------------------------------------------------------------------------

def _cost_keys(r: _FakeRedis) -> set[str]:
    return {k for k in r.hashes if k.startswith("cc:cost:")}


def test_an_untenanted_priced_call_counts_and_writes_no_feed_key(fake_redis):
    """The deployment-key ``/v1/chat/completions`` binds no tenant. Its cost
    still folds into ``cc:cost:{day}``, and it writes no stream or presence
    key. M5: the early ``if not org: return`` in ``publish_activity`` turns
    this red."""
    from acb_common import publish_activity

    async def _go() -> None:
        publish_activity(kind="model", model="m", run_id="run-cost-none",
                         cost_usd=0.25, tokens=100)
        await _drain()

    asyncio.run(_go())
    written = fake_redis.keys_written()
    assert len(_cost_keys(fake_redis)) == 1
    assert written == _cost_keys(fake_redis), written
    assert fake_redis.streams == {} and fake_redis.store == {}
    assert not any(k.startswith("cc:activity") for k in written)


def test_a_tenanted_priced_call_writes_the_feed_and_the_cost(fake_redis):
    from acb_common import publish_activity
    from acb_common.db import bind_tenant, release_tenant

    async def _go() -> None:
        token = bind_tenant(_ORG_A)
        try:
            publish_activity(kind="model", model="m", run_id="run-cost-a",
                             cost_usd=0.25, tokens=100)
        finally:
            release_tenant(token)
        await _drain()

    asyncio.run(_go())
    assert set(fake_redis.streams) == {f"cc:{_ORG_A}:activity"}
    assert len(_cost_keys(fake_redis)) == 1


# ---------------------------------------------------------------------------
# A failed read waits, so the SSE loop cannot spin (a full blocking pool).
# ---------------------------------------------------------------------------

def _xread_fails(monkeypatch, r: _FakeRedis) -> list[int]:
    import redis.exceptions

    calls: list[int] = []

    async def _boom(*_a: Any, **_kw: Any):
        calls.append(1)
        raise redis.exceptions.ConnectionError("Too many connections")

    monkeypatch.setattr(r, "xread", _boom)
    return calls


def test_a_failed_read_waits_before_it_returns(fake_redis, monkeypatch):
    """M6: removing the sleep in ``read_activity_since`` turns this red."""
    import time

    from acb_common import activity

    _xread_fails(monkeypatch, fake_redis)
    monkeypatch.setattr(activity, "READ_ERROR_BACKOFF_SECONDS", 0.05)
    start = time.monotonic()
    out = asyncio.run(activity.read_activity_since("$", organization_id=_ORG_A))
    assert out == ([], "$")
    assert time.monotonic() - start >= 0.04


def test_the_empty_answer_after_block_does_not_wait(fake_redis, monkeypatch):
    """The normal empty answer already waited in BLOCK. It gets no backoff."""
    import time

    from acb_common import activity

    monkeypatch.setattr(activity, "READ_ERROR_BACKOFF_SECONDS", 5.0)
    start = time.monotonic()
    out = asyncio.run(activity.read_activity_since("$", organization_id=_ORG_A))
    assert out == ([], "$")
    assert time.monotonic() - start < 1.0


def test_the_sse_feed_does_not_spin_when_the_pool_is_full(fake_redis, monkeypatch):
    """With every read failed at once, the feed sends about one heartbeat for
    each backoff, not a flood. M6 turns this red too."""
    import time

    from acb_common import activity
    from gateway.routes.observability import _sse_activity

    calls = _xread_fails(monkeypatch, fake_redis)
    monkeypatch.setattr(activity, "READ_ERROR_BACKOFF_SECONDS", 0.02)

    async def _go() -> int:
        gen = _sse_activity(_ORG_A)
        frames = 0
        deadline = time.monotonic() + 0.2
        try:
            async for _frame in gen:
                frames += 1
                if time.monotonic() >= deadline or frames >= 500:
                    break
        finally:
            await gen.aclose()
        return frames

    frames = asyncio.run(_go())
    # 0.2 s / 0.02 s is about 10 reads. A spin makes hundreds.
    assert frames < 50, frames
    assert 1 <= len(calls) < 50, len(calls)
