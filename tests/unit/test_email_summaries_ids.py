"""Task 3863: ``POST /email/messages/summaries`` skips an id it cannot read.

R7 fence ``email-id-label-summaries-route``: a batch with ``id=<uuid>``,
``not-a-uuid`` and a plain UUID answers with the two good ids, and the SQL
parameter never holds a bad id. asyncpg raised ``DataError`` for a bad id, and
the route answered 500 for the whole batch. The route has no R8 suite. This
test runs with no database, so it does not skip.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from gateway.routes.email.transport import messages as route

_A = "41c0e817-fb4a-4e45-870a-2da9a953bf4b"
_B = "5b2f1c9e-0000-4000-8000-000000000002"


def test_summary_ids_drops_label_and_junk() -> None:
    got = route.summary_ids([f"id={_A}", "not-a-uuid", "", _B.upper(), "id=", None])  # type: ignore[list-item]
    assert got == [_A, _B]
    assert route.summary_ids(None) == []


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Db:
    def __init__(self):
        self.params: list[dict] = []

    async def execute(self, _stmt, params):
        self.params.append(params)
        rows = [
            SimpleNamespace(
                id=i, thread_id="t", subject="S", from_address={"name": "Ann"},
                received_at=None, is_read=True, has_attachments=False,
            )
            for i in params["ids"]
        ]
        return _Result(rows)


async def test_route_serves_good_ids_and_never_raises(monkeypatch) -> None:
    db = _Db()

    @asynccontextmanager
    async def fake_session():
        yield db

    monkeypatch.setattr(route, "_tenant_session", fake_session)
    user = SimpleNamespace(email="a@example.com")
    req = route.MessageSummariesRequest(ids=[f"id={_A}", "not-a-uuid", _B])
    out = await route.message_summaries(req, user)
    assert [s["id"] for s in out["summaries"]] == [_A, _B]
    assert db.params[0]["ids"] == [_A, _B]
    assert db.params[0]["user_id"] == "a@example.com"  # the owner check stays
    # All ids bad: 200 with no rows, and no query.
    db.params.clear()
    out = await route.message_summaries(
        route.MessageSummariesRequest(ids=["x", "id=y"]), user)
    assert out == {"summaries": []} and not db.params
