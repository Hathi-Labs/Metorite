"""``_edit_rows`` reads a capped number of batches for one list call (K-7).

A member who may decide few of the pending rows must not make the list read
every pending row of the org. The fake serves 6000 pending rows that the caller
can neither propose nor write, in batches by the key of the real query.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from acb_auth import UserContext, UserRole, build_access
from gateway.routes.knowledge import proposals

ORG = "11111111-1111-4111-8111-111111111111"
TOTAL = 6000


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _Db:
    """Serves TOTAL rows, oldest first, after the key the query passes."""

    def __init__(self) -> None:
        self.reads = 0
        self.served = 0

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Rows:
        self.reads += 1
        start = int(params["after_id"]) + 1 if "after_id" in params else 0
        end = min(start + params["batch"], TOTAL)
        rows = [
            SimpleNamespace(
                id=str(i), created_at=datetime(2026, 1, 1, tzinfo=UTC),
                space_id="space-1", proposer_email="someone.else@kb7.test",
            )
            for i in range(start, end)
        ]
        self.served += len(rows)
        return _Rows(rows)


def _user() -> UserContext:
    return UserContext(
        email="reader@kb7.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:knowledge"]), organization_id=ORG,
    )


def _run(monkeypatch: Any) -> tuple[_Db, list[Any], bool]:
    async def no_space(db: Any, user: Any, ids: Any) -> set[str]:
        return set()

    monkeypatch.setattr(proposals, "_writable_space_ids", no_space)
    db = _Db()
    kept, truncated = asyncio.run(proposals._edit_rows(db, _user(), "pending"))
    return db, kept, truncated


def test_a_list_call_reads_at_most_the_cap_of_batches(monkeypatch: Any) -> None:
    db, kept, truncated = _run(monkeypatch)
    cap = proposals.MAX_EDIT_BATCHES
    assert cap == 10 and proposals._EDIT_BATCH == 500
    assert db.reads == cap, db.reads
    assert db.served == cap * proposals._EDIT_BATCH < TOTAL
    assert kept == []
    assert truncated is True


def test_a_short_list_is_not_truncated(monkeypatch: Any) -> None:
    monkeypatch.setattr(sys_module(), "TOTAL", 700)
    db, _kept, truncated = _run(monkeypatch)
    assert db.reads == 2 and truncated is False


def sys_module() -> Any:
    import sys

    return sys.modules[__name__]
