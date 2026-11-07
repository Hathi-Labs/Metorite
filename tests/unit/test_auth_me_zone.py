"""``GET`` and ``PUT /auth/me/zone`` — the hermetic half (WS-66 RX-7, item 8).

The R8 half, against a real Postgres, is ``test_auth_me_zone_r8.py``.

A member without ``feature:tasks`` cannot use ``/tasks/settings``, so the shell
could not save their zone and the morning summary used UTC for them. This
route needs only a signed-in member. This file holds what a fake proves
honestly:

- a guest with no feature reaches both routes;
- the body is one IANA zone, and a bad name or a key it does not know is a 422
  that writes nothing;
- the write is the one write of the settings row (``write_member_settings``),
  and it names the zone only, under the session's address;
- a failed read is a 503, never the default zone.

Mutation: let ``put_my_zone`` skip ``ZoneIn`` (take ``body: dict``), and the
bad-zone cases fail. Let it read the address from the body, and the
"session's address" test fails.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.admin import me


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDB:
    def __init__(self, stored: Any = None, fail: bool = False) -> None:
        self.stored = stored
        self.fail = fail
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        self.sql.append((str(stmt), dict(params or {})))
        if str(stmt).startswith("SELECT timezone"):
            return _Result(None if self.stored is None else (self.stored,))
        if str(stmt).startswith("SELECT 1"):
            return _Result((1,))
        return _Result(None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        yield db

    import gateway.routes.tasks.settings as settings

    monkeypatch.setattr(me, "_tenant_session", _session)
    monkeypatch.setattr(settings, "_tenant_session", _session)
    return db


def _user(email: str = "ana@alpha.example", perms: list[str] | None = None) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(perms or [], roles=["guest"]))


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(me.me_router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _writes(db: _FakeDB) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in db.sql if s.startswith("INSERT")]


class TestPut:
    def test_a_member_with_no_feature_saves_a_zone(self, fake: _FakeDB) -> None:
        res = _client(_user(perms=[])).put("/auth/me/zone", json={"timezone": "Asia/Kolkata"})
        assert res.status_code == 200, res.text
        assert res.json() == {"timezone": "Asia/Kolkata"}
        [(sql, params)] = _writes(fake)
        assert "INSERT INTO user_settings" in sql
        assert "DO UPDATE SET timezone = EXCLUDED.timezone" in sql
        assert params["timezone"] == "Asia/Kolkata"

    def test_the_write_names_the_session_address_only(self, fake: _FakeDB) -> None:
        res = _client(_user("Bo@alpha.example")).put("/auth/me/zone", json={"timezone": "Europe/Paris"})
        assert res.status_code == 200
        [(_, params)] = _writes(fake)
        assert params["uid"] == "Bo@alpha.example"

    @pytest.mark.parametrize("body", [
        {"timezone": "Mars/Olympus"},
        {"timezone": "America"},
        {"timezone": ""},
        {"timezone": "x" * 80},
        {"timezone": 5},
        {},
        # The body never names the member or the tenant (R5).
        {"timezone": "Asia/Kolkata", "user_id": "someone@else.example"},
        {"timezone": "Asia/Kolkata", "organization_id": "00000000-0000-0000-0000-000000000001"},
    ])
    def test_a_bad_body_is_refused_and_writes_nothing(self, fake: _FakeDB, body: dict) -> None:
        res = _client(_user()).put("/auth/me/zone", json=body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    @pytest.mark.parametrize("where", ["query", "header"])
    def test_a_member_named_outside_the_session_changes_nothing(
            self, fake: _FakeDB, where: str) -> None:
        """The route reads the member from the session only (R5). A query
        value or a header that names another member must not move the write.
        Mutation: take the member from the query, and this fails."""
        other = "someone@else.example"
        kwargs: dict[str, Any] = {"json": {"timezone": "Asia/Kolkata"}}
        if where == "query":
            kwargs["params"] = {"user_id": other, "email": other, "member": other}
        else:
            kwargs["headers"] = {"X-User-Email": other, "X-Member": other}
        res = _client(_user("ana@alpha.example")).put("/auth/me/zone", **kwargs)
        assert res.status_code == 200, res.text
        [(_, params)] = _writes(fake)
        assert params["uid"] == "ana@alpha.example"

    def test_a_failed_write_is_a_503(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client(_user()).put("/auth/me/zone", json={"timezone": "Asia/Kolkata"})
        assert res.status_code == 503


class TestGet:
    def test_it_reads_the_saved_zone(self, fake: _FakeDB) -> None:
        fake.stored = "Asia/Tokyo"
        res = _client(_user()).get("/auth/me/zone")
        assert res.json() == {"timezone": "Asia/Tokyo"}
        [(sql, params)] = fake.sql
        assert "organization_id" in sql and params == {"uid": "ana@alpha.example"}

    def test_a_member_with_no_row_reads_utc(self, fake: _FakeDB) -> None:
        assert _client(_user()).get("/auth/me/zone").json() == {"timezone": "UTC"}

    def test_a_bad_stored_zone_reads_utc(self, fake: _FakeDB) -> None:
        fake.stored = "Not/AZone"
        assert _client(_user()).get("/auth/me/zone").json() == {"timezone": "UTC"}

    def test_a_failed_read_is_a_503_and_never_a_zone(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client(_user()).get("/auth/me/zone")
        assert res.status_code == 503
        assert "timezone" not in res.json()
