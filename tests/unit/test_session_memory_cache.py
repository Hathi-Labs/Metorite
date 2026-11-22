"""Unit tests for session-scoped memory caching (Phase 4).

The memory block must be fetched ONCE per session (thread) and reused across
turns, so ``[stable prefix + memory]`` stays byte-stable and cache-eligible.
"""
from __future__ import annotations

import pytest
from acb_common.tenant_redis import TenantRedis
from acb_memory.session_cache import (
    get_session_memory,
    invalidate_session_memory,
)

#: Task 570: the block is keyed by the org, through the tenant wrapper. Each
#: fake below is the RAW client behind ``TenantRedis``, so a test sees the
#: wire keys that production writes.
ORG = "org-aaaaaaaa"
ORG_B = "org-bbbbbbbb"


class _FakeRedis:
    """Minimal async Redis double: get / setex / delete over an in-mem dict."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls = 0
        self.setex_calls = 0

    async def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls += 1
        self.store[key] = value

    async def delete(self, *keys: str) -> None:
        # Variadic, like redis-py's own `delete(*names)`. A single-key double
        # made the production call look broken when it was the double that was.
        for key in keys:
            self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("SESSION_MEMORY_CACHE", "1")
    monkeypatch.setenv("SESSION_MEMORY_CACHE_TTL", "600")


async def test_builds_once_then_reuses_across_turns():
    redis = _FakeRedis()
    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return f"MEM v{calls['n']}"

    # Turn 1 — miss → build + store.
    b1 = await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t1", build=build)
    # Turns 2 & 3 — hits → reuse, no rebuild.
    b2 = await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t1", build=build)
    b3 = await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t1", build=build)

    assert b1 == b2 == b3 == "MEM v1"  # byte-stable across the session
    assert calls["n"] == 1             # built exactly once
    assert redis.setex_calls == 1


async def test_empty_block_is_cached_too():
    # A session with no memory should not re-run the semantic search each turn.
    redis = _FakeRedis()
    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return ""

    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t2", build=build)
    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t2", build=build)
    assert calls["n"] == 1
    assert redis.setex_calls == 1


async def test_no_thread_id_skips_cache_builds_every_time():
    redis = _FakeRedis()
    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return "X"

    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id=None, build=build)
    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id=None, build=build)
    assert calls["n"] == 2
    assert redis.get_calls == 0  # never touched the cache


async def test_disabled_flag_bypasses_cache(monkeypatch):
    monkeypatch.setenv("SESSION_MEMORY_CACHE", "0")
    redis = _FakeRedis()
    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return "X"

    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t3", build=build)
    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t3", build=build)
    assert calls["n"] == 2
    assert redis.get_calls == 0


async def test_no_redis_degrades_to_fresh_fetch():
    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return "X"

    out = await get_session_memory(redis=None, organization_id=ORG, thread_id="t4", build=build)
    assert out == "X"
    assert calls["n"] == 1


async def test_redis_error_falls_back_to_build():
    class _Broken:
        async def get(self, key):
            raise RuntimeError("redis down")

        async def setex(self, *a):
            raise RuntimeError("redis down")

    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return "FRESH"

    out = await get_session_memory(redis=TenantRedis(_Broken()), organization_id=ORG, thread_id="t5", build=build)
    assert out == "FRESH"  # never raised; degraded to a fresh build
    assert calls["n"] == 1


async def test_invalidate_drops_the_session_block():
    redis = _FakeRedis()

    async def build() -> str:
        return "A"

    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t6", build=build)
    assert redis.store  # something cached
    await invalidate_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t6")
    assert f"cc:{ORG}:session-mem:t6" not in redis.store
    assert redis.store == {}


@pytest.mark.asyncio
async def test_invalidate_clears_every_clearance_variant():
    """A thread has one cache entry per clearance since the key gained that
    suffix, so invalidation must clear them all — otherwise a written fact
    takes up to the TTL to show up in a shared room."""
    redis = _FakeRedis()

    async def build() -> str:
        return "A"

    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t7", build=build, clearance="solo")
    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t7", build=build, clearance="room")
    assert len(redis.store) == 2

    async def _scan_iter(match="", count=64):
        prefix = match.rstrip("*")
        for k in list(redis.store):
            if k.startswith(prefix):
                yield k

    redis.scan_iter = _scan_iter
    await invalidate_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t7")
    assert redis.store == {}


@pytest.mark.asyncio
async def test_invalidate_still_drops_the_base_key_without_scan():
    """A client with no SCAN must not lose invalidation entirely.

    Both deletes used to share one try block, so a missing `scan` skipped the
    base delete too — silently doing nothing, which is worse than the
    single-key behaviour it replaced.
    """
    redis = _FakeRedis()

    async def build() -> str:
        return "A"

    await get_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t8", build=build)
    assert redis.store
    await invalidate_session_memory(redis=TenantRedis(redis), organization_id=ORG, thread_id="t8")   # no .scan_iter
    assert redis.store == {}


# ---------------------------------------------------------------------------
# Task 570 (H5, R5): the key carries the org.
# ---------------------------------------------------------------------------


async def test_the_key_is_prefixed_by_the_org_with_the_same_ttl():
    redis = _FakeRedis()
    ttls: list[int] = []
    setex = redis.setex

    async def _setex(key: str, ttl: int, value: str) -> None:
        ttls.append(ttl)
        await setex(key, ttl, value)

    redis.setex = _setex

    async def build() -> str:
        return "A"

    await get_session_memory(
        redis=TenantRedis(redis), organization_id=ORG, thread_id="t9",
        build=build, clearance="fp1",
    )
    assert list(redis.store) == [f"cc:{ORG}:session-mem:t9:fp1"]
    assert ttls == [600]


async def test_two_orgs_with_the_same_thread_do_not_share_a_block():
    """The same thread id in two orgs is two keys. Org B never reads the
    private block that org A cached."""
    redis = _FakeRedis()

    async def build_a() -> str:
        return "ORG A PRIVATE FACTS"

    async def build_b() -> str:
        return "org b facts"

    first = await get_session_memory(
        redis=TenantRedis(redis), organization_id=ORG, thread_id="same", build=build_a,
    )
    second = await get_session_memory(
        redis=TenantRedis(redis), organization_id=ORG_B, thread_id="same", build=build_b,
    )
    assert first == "ORG A PRIVATE FACTS"
    assert second == "org b facts"
    assert sorted(redis.store) == [
        f"cc:{ORG}:session-mem:same", f"cc:{ORG_B}:session-mem:same",
    ]
    await invalidate_session_memory(
        redis=TenantRedis(redis), organization_id=ORG_B, thread_id="same",
    )
    assert list(redis.store) == [f"cc:{ORG}:session-mem:same"]


async def test_no_org_caches_nothing_and_builds_each_turn():
    redis = _FakeRedis()
    calls = {"n": 0}

    async def build() -> str:
        calls["n"] += 1
        return "X"

    for _ in range(2):
        out = await get_session_memory(
            redis=TenantRedis(redis), organization_id=None, thread_id="t10", build=build,
        )
        assert out == "X"
    assert calls["n"] == 2
    assert redis.get_calls == 0 and redis.store == {}


async def test_an_org_id_that_the_wrapper_refuses_builds_and_never_raises():
    redis = _FakeRedis()

    async def build() -> str:
        return "Y"

    out = await get_session_memory(
        redis=TenantRedis(redis), organization_id="bad:org*", thread_id="t11", build=build,
    )
    assert out == "Y"
    assert redis.store == {}


async def test_a_raw_string_client_path_is_gone():
    """The old raw shape ``session_mem:<thread>`` is never read or written."""
    redis = _FakeRedis()
    redis.store["session_mem:t12"] = "STALE RAW BLOCK"

    async def build() -> str:
        return "fresh"

    out = await get_session_memory(
        redis=TenantRedis(redis), organization_id=ORG, thread_id="t12", build=build,
    )
    assert out == "fresh"
    assert redis.store["session_mem:t12"] == "STALE RAW BLOCK"
