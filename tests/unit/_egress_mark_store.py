"""The in-memory store of the upward-egress mark, for tests and offline evals.

WS-17 EM-G11d (``email_app_master_plan.md`` 12.3.13.8). The mark lives in
tenant Redis, and its read FAILS CLOSED where a card can show. A test run or
an offline eval has no Redis of its own (CI has no Redis service), so each
read would fail, read as marked, and change the run. That is the outage
behaviour, and ``tests/unit/test_delegation_no_egress.py`` tests it on
purpose. Every other run uses this store.

ONE helper for both callers: ``tests/conftest.py`` (an autouse fixture) and
``evals/routing/harness.py`` (the routing eval of skill-eval). Do not copy
it into a second place.
"""

from __future__ import annotations

from typing import Any


class MemoryMarkClient:
    """An in-memory raw Redis client with the calls the mark uses, and the
    hash calls of the sticky packs (WS-67 OA-7c, ``acb_skills.tool_packs``).
    Each call is appended to :attr:`calls`, so a test can count them."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str]] = []

    async def setex(self, name: str, _seconds: int, value: str) -> bool:
        self.calls.append(("setex", name))
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        self.calls.append(("get", name))
        return self.values.get(name)

    async def exists(self, *names: str) -> int:
        self.calls.append(("exists", ",".join(names)))
        return sum(1 for n in names if n in self.values or n in self.hashes)

    async def expire(self, name: str, _seconds: int) -> bool:
        self.calls.append(("expire", name))
        return name in self.values or name in self.hashes

    async def hincrby(self, name: str, key: str, amount: int = 1) -> int:
        self.calls.append(("hincrby", name))
        row = self.hashes.setdefault(name, {})
        row[key] = str(int(row.get(key, "0")) + int(amount))
        return int(row[key])

    async def hgetall(self, name: str) -> dict[str, str]:
        self.calls.append(("hgetall", name))
        return dict(self.hashes.get(name, {}))

    async def hset(self, name: str, key: str | None = None, value: str | None = None,
                   mapping: dict[str, str] | None = None) -> int:
        self.calls.append(("hset", name))
        row = self.hashes.setdefault(name, {})
        items = dict(mapping or {})
        if key is not None:
            items[key] = value if value is not None else ""
        added = sum(1 for k in items if k not in row)
        row.update({k: str(v) for k, v in items.items()})
        return added

    async def hdel(self, name: str, *keys: str) -> int:
        self.calls.append(("hdel", name))
        row = self.hashes.get(name, {})
        return sum(1 for k in keys if row.pop(k, None) is not None)


def install_memory_mark_store(patcher: Any) -> MemoryMarkClient | None:
    """Point ``acb_skills.egress`` at a new in-memory store, and empty the
    process cache of marks. *patcher* is a ``pytest.MonkeyPatch``, so its
    undo puts the real client back. Returns the raw client, or None when
    ``acb_skills`` does not import."""
    try:
        from acb_common.tenant_redis import TenantRedis
        from acb_skills import egress
    except ImportError:  # acb_skills unavailable, so there is no mark
        return None
    client = MemoryMarkClient()
    store = TenantRedis(client)
    patcher.setattr(egress, "get_tenant_redis", lambda: store)
    # The email working set of the one assistant (navigation_shell.md
    # §7.1.1) lives in the same tenant Redis, and a run reads it on each
    # email hand-off. The same store, so no run waits on a Redis it lacks.
    try:
        from acb_skills import email_chat_context
    except ImportError:
        pass
    else:
        patcher.setattr(email_chat_context, "get_tenant_redis", lambda: store)
    # WS-67 OA-4: the member's corrections and limits of a chat live in the
    # same tenant Redis, and each orchestrator turn reads them.
    try:
        from acb_skills import chat_constraints
    except ImportError:
        pass
    else:
        patcher.setattr(chat_constraints, "get_tenant_redis", lambda: store)
    # WS-67 OA-7c: the sticky packs of a chat live in the same tenant Redis.
    try:
        from acb_skills import tool_packs
    except ImportError:
        pass
    else:
        patcher.setattr(tool_packs, "get_tenant_redis", lambda: store)
    clear_marks()
    return client


def clear_marks() -> None:
    """Empty the process cache of marks."""
    try:
        from acb_skills import egress
    except ImportError:
        return
    with egress._MARKED_LOCK:
        egress._MARKED_SESSIONS.clear()
    try:
        from acb_skills import email_chat_context
    except ImportError:
        return
    email_chat_context.clear_cache()
    try:
        from acb_skills import chat_constraints
    except ImportError:
        return
    chat_constraints.clear_cache()
