"""A fake RAW Redis client for the CRM question tests (WS-53 CRM-Q32-8).

Wrap it in ``acb_common.tenant_redis.TenantRedis``, so the real wrapper still
builds each key with its tenant prefix and refuses a key of another bound
tenant. The fake only stores the raw strings. ``fail`` raises on each
command, for an outage.
"""

from __future__ import annotations

import fnmatch
from collections.abc import AsyncIterator
from typing import Any


class RawRedis:
    """``setex``, ``exists``, ``delete`` and ``scan_iter`` on raw names."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[int, str]] = {}
        self.fail: BaseException | None = None

    def _check(self) -> None:
        if self.fail is not None:
            raise self.fail

    async def setex(self, name: str, seconds: int, value: Any) -> bool:
        self._check()
        self.store[name] = (int(seconds), str(value))
        return True

    async def exists(self, *names: str) -> int:
        self._check()
        return sum(1 for n in names if n in self.store)

    async def delete(self, *names: str) -> int:
        self._check()
        return sum(1 for n in names if self.store.pop(n, None) is not None)

    async def scan_iter(self, match: str | None = None, count: int | None = None,
                        ) -> AsyncIterator[str]:
        self._check()
        pattern = (match or "*").replace("\\", "")
        for name in list(self.store):
            if fnmatch.fnmatchcase(name, pattern):
                yield name


def tenant_client(raw: RawRedis) -> Any:
    """The real wrapper over *raw*."""
    from acb_common.tenant_redis import TenantRedis

    return TenantRedis(raw)
