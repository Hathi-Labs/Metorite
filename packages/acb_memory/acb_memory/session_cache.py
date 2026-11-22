"""Session-scoped memory cache (specs/llm_caching_memory.md Phase 4).

Mem0 uses semantic search, so a different query against the same user's memory
returns a different result set — which means the assembled ``memory_context``
block changes every turn, and the combined ``[stable prefix + memory]`` never
stays byte-stable across turns. That defeats cross-turn prompt caching on the
memory portion (the stable prefix still caches; the memory does not).

The fix: fetch + assemble the memory block ONCE per session (thread) and reuse
it for every turn in that session, cached in Redis with a short TTL. The block
then stays byte-stable across the session, so ``[stable prefix + memory]``
becomes cache-eligible cross-turn. A 10-minute TTL matches the provider cache
TTL, so the two expire together.

Trade-off: memory doesn't refresh mid-session. Acceptable because agents can
call ``recall_timeline`` explicitly for fresh facts, and new facts are picked up
on the next session anyway. Set ``SESSION_MEMORY_CACHE=0`` to disable and always
fetch fresh (the pre-Phase-4 behaviour).

This module deliberately takes an injected Redis client + builder coroutine so
``acb_memory`` never imports the orchestrator's stream_relay (layering).

The tenant (task 570, H5, R5). The block holds the member's private facts and
the org's memory, so its key carries the org. The caller passes the tenant
Redis wrapper (``acb_common.tenant_redis.get_tenant_redis()``) and the org of
the session. The key is ``cc:<org>:session-mem:<thread>[:<clearance>]``. With
no org, nothing is cached and ``build`` runs each turn. A key of the old shape
``session_mem:<thread>`` is not read: it is a cache of 600 s, so the release
costs one rebuild for each open thread. Fence:
``tests/unit/test_session_memory_cache.py``.
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

from acb_common import get_logger
from acb_common.tenant_redis import key, match, organization_scope

_log = get_logger("acb_memory.session_cache")

#: The namespace of a session's assembled memory block in the tenant wrapper.
NAMESPACE = "session-mem"


def _key(thread_id: str, clearance: str) -> Any:
    """The tenant key of one block. Call it with the org bound."""
    return key(NAMESPACE, thread_id, clearance) if clearance else key(NAMESPACE, thread_id)


def _enabled() -> bool:
    return os.environ.get("SESSION_MEMORY_CACHE", "1") == "1"


def _ttl() -> int:
    try:
        return int(os.environ.get("SESSION_MEMORY_CACHE_TTL", "600"))
    except ValueError:
        return 600


async def get_session_memory(
    *,
    redis: Any,
    organization_id: str | None,
    thread_id: str | None,
    build: Callable[[], Awaitable[str]],
    clearance: str = "",
) -> str:
    """Return the session's memory block, fetching+caching it once per thread.

    Args:
        redis: the tenant Redis wrapper (``get_tenant_redis()``). If ``None``,
            caching is skipped.
        organization_id: the org of the session, from the server side, never
            from a request field. It prefixes the key. If ``None``, caching is
            skipped and ``build`` runs.
        thread_id: the conversation thread key. When ``None`` (a one-off run
            with no session) caching is skipped and ``build`` is called directly.
        build: a zero-arg coroutine that fetches + assembles the fresh memory
            block (Mem0 + Graphiti). Only invoked on a cache miss.
        clearance: fingerprint of the compartments the block was assembled
            from (``acb_memory.Clearance.fingerprint``). **Part of the cache
            key, and load-bearing.** A thread cached while solo built its block
            from the owner's private compartment; if the key ignored clearance
            that block would keep being served for the rest of the TTL after
            somebody was invited in — handing the room exactly the private
            facts the read rule excludes. Changing clearance changes the key,
            so a newly shared room misses and rebuilds. Empty keeps the old
            key, so callers with one clearance are unaffected.

    Returns:
        The memory block string (possibly empty). Never raises — on any Redis
        error it falls back to calling ``build`` directly, so a cache outage
        degrades to the pre-Phase-4 fetch-every-turn behaviour.
    """
    if not _enabled() or redis is None or not thread_id or not organization_id:
        return await build()

    # ── Cache read ────────────────────────────────────────────────────
    # The org binds for each command only, never across ``build()``. An org
    # id that the wrapper refuses is a read that failed: the block builds.
    try:
        with organization_scope(organization_id):
            cached = await redis.get(_key(thread_id, clearance))
    except Exception as exc:
        _log.debug("session_mem.read_failed", error=str(exc))
        cached = None

    if cached is not None:
        # decode_responses=True yields str; be defensive for bytes clients too.
        block = cached.decode() if isinstance(cached, bytes) else str(cached)
        _log.debug("session_mem.hit", thread=thread_id[:12], chars=len(block))
        return block

    # ── Cache miss → build fresh + store ──────────────────────────────
    block = await build()
    try:
        # Store even an empty string: a session with no memory should not
        # re-run the (expensive) semantic search on every turn.
        with organization_scope(organization_id):
            await redis.setex(_key(thread_id, clearance), _ttl(), block)
        _log.debug(
            "session_mem.stored",
            thread=thread_id[:12],
            chars=len(block),
            ttl=_ttl(),
        )
    except Exception as exc:
        _log.debug("session_mem.store_failed", error=str(exc))
    return block


async def invalidate_session_memory(
    *, redis: Any, organization_id: str | None, thread_id: str,
) -> None:
    """Drop every cached memory block for a thread (e.g. after a memory write).

    A thread has one entry PER CLEARANCE since the key gained that suffix, so
    this clears them all rather than the bare key — otherwise invalidation
    would silently stop matching anything the moment a thread was shared, and
    a written fact would take up to the TTL to appear.

    Best-effort — a failure just means the blocks live out their TTL.
    *redis* is the tenant wrapper, and *organization_id* is the org of the
    session. With no org, nothing is dropped: the blocks live out their TTL.
    """
    if redis is None or not thread_id or not organization_id:
        return

    # The bare key first, in its own try: it is the only one a client without
    # SCAN can have written, and it must not be left behind because the sweep
    # of clearance variants below failed. Collapsing both into one block made a
    # missing `scan` silently skip the delete entirely — worse than the
    # single-key behaviour this replaced.
    try:
        with organization_scope(organization_id):
            await redis.delete(_key(thread_id, ""))
    except Exception as exc:
        _log.debug("session_mem.invalidate_failed", error=str(exc))

    # Then every per-clearance variant. A thread has at most a handful, so the
    # scan is bounded and cheap.
    # The scan pattern is the wrapper's, so it cannot leave this org. A found
    # name is a wire string. The clearance is its last part, and the delete
    # rebuilds a typed key from it.
    try:
        with organization_scope(organization_id):
            found = [
                name async for name in redis.scan_iter(match(NAMESPACE, thread_id), count=64)
            ]
            prefix = f"{_key(thread_id, '').value}:"
            variants = [
                _key(thread_id, name[len(prefix):])
                for name in found
                if isinstance(name, str) and name.startswith(prefix) and name[len(prefix):]
            ]
            if variants:
                await redis.delete(*variants)
    except Exception as exc:
        _log.debug("session_mem.invalidate_variants_failed", error=str(exc))
