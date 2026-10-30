"""``GET /auth/me/assistant-profile/block``, "Read as text" (WS-63 PZ-20a).

Spec: ``project-docs/specs/personalization.md`` §9, row PZ-20a. R7 fences:

* ``block-view-parity``: ``text`` equals ``assistant_profile.member_block``,
  the call that the executor makes for the block of a turn. This holds for
  unicode, for a profile over the cap and for an empty profile.
* ``block-view-off-org``: with the org off, the answer is ``enabled: false``
  with no text, and the route runs no statement.
* ``block-view-r5``: the route takes only the session user. It names no
  member, org, query or body.
* ``block-view-503``: a failed read is a 503, never an empty text.
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import assistant_profile as shared
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.admin import assistant_profile as ap
from gateway.routes.admin.me import me_router

URL = "/auth/me/assistant-profile/block"
ORG = "11111111-1111-4111-8111-111111111111"


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDB:
    def __init__(self) -> None:
        self.row: Any = None
        self.names: Any = None
        self.fail = False
        self.sessions = 0
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        sql = str(stmt)
        self.sql.append((sql, dict(params or {})))
        if "FROM app_user u LEFT JOIN people p" in sql:
            return _Result(self.names)
        return _Result(None if self.row is None else (self.row,))


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    fake = _FakeDB()

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        fake.sessions += 1
        yield fake

    monkeypatch.setattr(ap, "_tenant_session", _session)
    return fake


def _on(monkeypatch: pytest.MonkeyPatch, orgs: str = "") -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", True, raising=False)
    monkeypatch.setattr(get_settings(), "assistant_profile_orgs", orgs, raising=False)


def _user(email: str = "ana@alpha.example") -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=ORG,
                       access=build_access([], roles=["member"]))


def _client(user: UserContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(me_router)
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    return TestClient(app)


def _facts(n: int, size: int) -> list[dict[str, Any]]:
    return [{"id": f"f{i:03d}", "text": f"fact {i} " + "x" * size, "kind": "about",
             "source": "chat", "at": f"2026-10-{(i % 28) + 1:02d}T10:00:00Z"}
            for i in range(n)]


#: ``(label, stored column, (preferred name, display name), text is empty)``.
PROFILES: list[tuple[str, Any, tuple[Any, Any], bool]] = [
    ("stored", {"v": 1, "address": {"name": "VJ"}, "style": {"length": "short"},
                "facts": _facts(2, 10)}, (None, "Vijay Varada"), False),
    ("unicode", {"v": 1, "address": {"name": "विजय"},
                 "style": {"notes": ["Réponds en français, sauf si j'écris en 日本語"]},
                 "facts": [{"id": "f1", "text": "Aime le café ☕ — très fort",
                            "kind": "about", "source": "chat",
                            "at": "2026-10-23T11:00:00Z"}]},
     ("Zoë", "Zoë Müller"), False),
    ("over the cap", {"v": 1, "address": {"name": "VJ"}, "facts": _facts(60, 200)},
     (None, None), False),
    ("directory name only", None, ("Viju", "Vijay Varada"), False),
    ("no row, no name", None, (None, "vijay@hathilabs.com"), True),
    ("empty profile, no name", {"v": 1, "learning": True}, (None, None), True),
]


@pytest.mark.parametrize(("raw", "names", "empty"), [p[1:] for p in PROFILES],
                         ids=[p[0] for p in PROFILES])
def test_text_equals_member_block_of_the_stored_row(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch, raw: Any, names: Any, empty: bool,
) -> None:
    _on(monkeypatch)
    db.row, db.names = raw, (names[0], "Asia/Kolkata", names[1])
    res = _client().get(URL)
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-store"
    body = res.json()
    assert body["enabled"] is True
    assert body["text"] == shared.member_block(raw, names[0], names[1])
    if empty:
        assert body["text"] == ""
    else:
        assert body["text"].startswith(shared.BLOCK_HEADER)
        assert body["text"].endswith(shared.BLOCK_FOOTER)


def test_the_over_cap_profile_sheds_whole_items_and_keeps_the_footer(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on(monkeypatch)
    db.row = {"v": 1, "address": {"name": "VJ"}, "facts": _facts(60, 200)}
    text = _client().get(URL).json()["text"]
    assert len(text) <= shared.RENDER_MAX
    assert 0 < text.count("- [f") < 60
    assert text.splitlines()[-1] == shared.BLOCK_FOOTER


def test_the_executor_calls_the_same_function() -> None:
    import orchestrator.executor as ex

    src = inspect.getsource(ex._member_profile_entry)
    assert "assistant_profile.member_block(raw, preferred, display)" in src
    assert "render_block" not in src
    route = inspect.getsource(ap.get_assistant_profile_block)
    assert "member_block(" in route and "render_block" not in route


def test_an_org_off_the_list_gets_no_text_and_no_read(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on(monkeypatch, orgs="99999999-9999-4999-8999-999999999999")
    db.row = {"v": 1, "address": {"name": "VJ"}}
    res = _client().get(URL)
    assert res.status_code == 200
    assert res.json() == {"enabled": False, "text": ""}
    assert res.headers["cache-control"] == "no-store"
    assert db.sql == [] and db.sessions == 0


def test_the_master_switch_off_gets_no_text_and_no_read(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "assistant_profile_enabled", False, raising=False)
    db.row = {"v": 1, "address": {"name": "VJ"}}
    assert _client().get(URL).json() == {"enabled": False, "text": ""}
    assert db.sql == [] and db.sessions == 0


def test_the_route_takes_only_the_session_user() -> None:
    sig = inspect.signature(ap.get_assistant_profile_block)
    assert list(sig.parameters) == ["user"]
    route = next(r for r in me_router.routes if getattr(r, "path", "") == URL)
    assert route.methods == {"GET"}
    assert [p.name for p in route.dependant.query_params] == []
    assert [p.name for p in route.dependant.body_params] == []
    assert route.dependant.path_params == []


def test_the_member_is_the_session_s_and_the_org_is_the_bound_one(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on(monkeypatch)
    # A query that names a member changes nothing.
    _client(_user("bo@alpha.example")).get(
        URL, params={"member": "ana@alpha.example", "org": "x"})
    (read, names) = db.sql
    assert read[1] == {"uid": "bo@alpha.example"}
    assert names[1] == {"m": "bo@alpha.example"}
    assert "current_setting('app.tenant_id', true)" in read[0]
    assert "{org}" not in names[0] and ":org" not in names[0]


def test_no_session_address_is_a_401(db: _FakeDB, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(monkeypatch)
    assert _client(_user("")).get(URL).status_code == 401
    assert db.sql == []


def test_a_failed_read_is_a_503_never_an_empty_text(
    db: _FakeDB, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _on(monkeypatch)
    db.fail = True
    res = _client().get(URL)
    assert res.status_code == 503
    assert res.json() == {"detail": "unavailable"}
    assert "text" not in res.json()


def test_the_route_adds_no_sql_of_its_own() -> None:
    src = inspect.getsource(ap.get_assistant_profile_block)
    assert "_read(db, uid)" in src and "ADDRESS_SQL_TEMPLATE" in src
    for word in ("INSERT", "UPDATE", "DELETE", "SELECT"):
        assert word not in src
