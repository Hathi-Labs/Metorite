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
    """An in-memory raw Redis client with the four calls the mark uses. A
    WhatsApp run reads the value with ``get`` (WS-47 WAC-P4)."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def setex(self, name: str, _seconds: int, value: str) -> bool:
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def exists(self, *names: str) -> int:
        return sum(1 for n in names if n in self.values)

    async def expire(self, name: str, _seconds: int) -> bool:
        return name in self.values


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
