"""Task 2313: a CRM record id that is not a UUID answers 404, never a 500.

``GET /crm/deals/aggregate`` has no route of its own, so "aggregate" reached
``/deals/{record_id}`` and ``CAST('aggregate' AS uuid)`` raised in Postgres.
``load_row`` now answers None for an id that is not a UUID, before any SQL.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from gateway.routes.crm import core


class _NoSql:
    """A session that fails the test when it is asked to run SQL."""

    calls = 0

    async def execute(self, *_a, **_k):
        _NoSql.calls += 1
        raise AssertionError("no SQL may run for an id that is not a UUID")


@pytest.mark.parametrize("bad", ["aggregate", "", "not-a-uuid", "1234", "x" * 40])
async def test_load_row_answers_none_for_an_id_that_is_not_a_uuid(bad: str) -> None:
    assert await core.load_row(_NoSql(), "crm_deals", bad) is None
    assert _NoSql.calls == 0


@pytest.mark.parametrize("bad", ["aggregate", "stats"])
async def test_require_row_answers_404_for_an_id_that_is_not_a_uuid(bad: str) -> None:
    with pytest.raises(HTTPException) as exc:
        await core.require_row(_NoSql(), "crm_deals", bad, "Deal")
    assert exc.value.status_code == 404
    assert exc.value.detail == "Deal not found"


async def test_a_uuid_still_reaches_the_query() -> None:
    class _Db:
        sql: list[str] = []

        async def execute(self, statement, params=None):
            _Db.sql.append(str(statement))

            class _R:
                @staticmethod
                def fetchone():
                    return None

            return _R()

    got = await core.load_row(_Db(), "crm_deals", "0b1c2d3e-0000-4000-8000-000000000001")
    assert got is None
    assert len(_Db.sql) == 1 and "CAST(:id AS uuid)" in _Db.sql[0]
