"""``kb_index.query.query(..., title_only=True)`` never runs the semantic half (K-4).

The route has its own guard that skips the embed call. This test covers the
second guard, in ``query.py``: a caller that passes a vector with
``title_only=True`` still gets the lexical half only. No database is needed,
because the fake records each statement that ``query`` sends.
"""

from __future__ import annotations

import asyncio
from typing import Any

from acb_auth import UserContext, UserRole, build_access
from gateway.kb_index import query as kb_query


class _Result:
    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return []


class _RecordingDb:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        self.sql.append(str(stmt))
        return _Result()


def _user() -> UserContext:
    return UserContext(
        email="reader@kb4.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:knowledge"]),
        organization_id="11111111-1111-4111-8111-111111111111",
    )


def _run(**kw: Any) -> _RecordingDb:
    db = _RecordingDb()
    asyncio.run(kb_query.query(db, _user(), "dock", vector=[0.1, 0.2, 0.3], **kw))
    return db


def test_title_only_skips_the_semantic_half_when_a_vector_is_passed() -> None:
    db = _run(title_only=True)
    assert len(db.sql) == 1, db.sql
    assert not any("embedding" in s or "hnsw" in s for s in db.sql)


def test_the_semantic_half_runs_without_title_only() -> None:
    """The control: the same call without the filter does run the semantic half."""
    db = _run(title_only=False)
    assert any("hnsw" in s for s in db.sql)
    assert any("c.embedding <=>" in s for s in db.sql)
