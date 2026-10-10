"""``GET`` and ``PUT /auth/me/shell`` — the hermetic half (WS-44 NS-7).

The R8 half, against a real Postgres, is ``test_auth_me_shell_r8.py``. This
file holds what a fake can prove honestly:

- the validation of a layout (unknown names, unknown keys, the caps);
- that a ``PUT`` of null resets and writes no row;
- that the routes take the member from the session and never from the body;
- that a guest with no feature reaches both routes (done-when 2);
- that a failed read is a 503, never the "never asked" layout;
- and that the gateway's names agree with the workbench's (``TestOneVocabulary``).
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.admin import me
from gateway.routes.shell.intent import JOBS

ROOT = Path(__file__).resolve().parents[2]
NAV = ROOT / "workbench" / "control_plane" / "src" / "lib" / "nav.ts"
PRESETS = ROOT / "workbench" / "control_plane" / "src" / "lib" / "shell" / "presets.ts"


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDB:
    """Records each statement. ``stored`` is the member's row, if any."""

    def __init__(self, stored: Any = None, fail: bool = False) -> None:
        self.stored = stored
        self.fail = fail
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        self.sql.append((str(stmt), dict(params or {})))
        if str(stmt).startswith("SELECT shell_prefs"):
            return _Result(None if self.stored is None else (self.stored,))
        return _Result(None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB()

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        yield db

    monkeypatch.setattr(me, "_tenant_session", _session)

    async def _seed(_db: Any, _uid: str) -> dict[str, int]:
        return {"day_start_hour": 8, "day_end_hour": 19}

    import gateway.routes.tasks.settings as settings

    monkeypatch.setattr(settings, "_seed_from_work_schedule", _seed)
    return db


def _user(email: str = "ana@alpha.example", roles: list[str] | None = None,
          perms: list[str] | None = None) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(perms or [], roles=roles or ["member"]))


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(me.me_router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _writes(db: _FakeDB) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in db.sql if not s.startswith("SELECT")]


# ── Validation ──────────────────────────────────────────────────────────────


class TestValidation:
    @pytest.mark.parametrize("body", [
        {"preset": "ceo"},
        {"pins": ["/nowhere"]},
        {"pins": ["/"]},
        {"cardOrder": ["weather"]},
        {"newOrder": ["launch-rocket"]},
        {"answered": "maybe"},
        {"answered": "answered"},
        {"pins": ["/tasks"] * 30},
        # The body never names the member or the tenant (R5, R11).
        {"pins": [], "user_id": "someone@else.example"},
        {"pins": [], "organization_id": "00000000-0000-0000-0000-000000000001"},
    ])
    def test_a_bad_layout_is_refused_and_writes_nothing(self, fake: _FakeDB, body: dict) -> None:
        res = _client(_user()).put("/auth/me/shell", json=body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_a_good_layout_is_stored_with_its_names(self, fake: _FakeDB) -> None:
        body = {"preset": "engineer", "answered": "answered",
                "pins": ["/tasks", "/tasks", "/crm"], "cardOrder": ["next"],
                "newOrder": ["capture"]}
        res = _client(_user()).put("/auth/me/shell", json=body)
        assert res.status_code == 200, res.text
        # A repeated pin is kept once. A preview pane may be stored.
        assert res.json()["pins"] == ["/tasks", "/crm"]
        [(sql, params)] = _writes(fake)
        assert "INSERT INTO user_settings" in sql
        assert json.loads(params["shell_prefs"])["preset"] == "engineer"

    def test_skip_is_a_stored_choice(self, fake: _FakeDB) -> None:
        res = _client(_user()).put("/auth/me/shell", json={"answered": "skipped"})
        assert res.status_code == 200
        assert res.json()["answered"] == "skipped"
        [(_, params)] = _writes(fake)
        assert json.loads(params["shell_prefs"])["answered"] == "skipped"


# ── The write ──────────────────────────────────────────────────────────────


class TestTheWrite:
    def test_a_put_of_null_resets_and_inserts_nothing(self, fake: _FakeDB) -> None:
        res = _client(_user()).put("/auth/me/shell", content="null",
                                   headers={"Content-Type": "application/json"})
        assert res.status_code == 200
        assert res.json() == me.EMPTY_SHELL
        [(sql, params)] = _writes(fake)
        assert sql.startswith("UPDATE user_settings SET shell_prefs = NULL")
        assert params == {"uid": "ana@alpha.example"}

    def test_the_member_is_the_session_s(self, fake: _FakeDB) -> None:
        _client(_user("ben@beta.example")).put("/auth/me/shell", json={"pins": []})
        assert {p["uid"] for _, p in fake.sql} == {"ben@beta.example"}

    def test_every_statement_names_the_bound_tenant(self, fake: _FakeDB) -> None:
        _client(_user()).put("/auth/me/shell", json={"pins": ["/tasks"]})
        for sql, _ in fake.sql:
            assert "current_setting('app.tenant_id', true)" in sql, sql

    def test_a_new_row_carries_the_calendar_seed(self, fake: _FakeDB) -> None:
        _client(_user()).put("/auth/me/shell", json={"pins": ["/tasks"]})
        [(sql, params)] = _writes(fake)
        assert params["day_start_hour"] == 8 and params["day_end_hour"] == 19
        # An existing row takes the layout only, never the seed.
        assert sql.endswith("DO UPDATE SET shell_prefs = EXCLUDED.shell_prefs, updated_at = now()")

    def test_an_existing_row_gets_no_seed(self, fake: _FakeDB) -> None:
        fake.stored = {"pins": []}
        _client(_user()).put("/auth/me/shell", json={"pins": ["/tasks"]})
        [(_, params)] = _writes(fake)
        assert "day_start_hour" not in params

    def test_a_caller_with_no_address_cannot_save(self, fake: _FakeDB) -> None:
        res = _client(_user(email="")).put("/auth/me/shell", json={"pins": []})
        assert res.status_code == 403
        assert fake.sql == []


# ── The read ───────────────────────────────────────────────────────────────


class TestTheRead:
    def test_no_row_is_never_asked(self, fake: _FakeDB) -> None:
        res = _client(_user()).get("/auth/me/shell")
        assert res.status_code == 200
        assert res.json() == me.EMPTY_SHELL

    def test_a_failed_read_is_503_never_the_empty_layout(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client(_user()).get("/auth/me/shell")
        assert res.status_code == 503

    def test_a_stale_name_is_dropped_not_refused(self, fake: _FakeDB) -> None:
        fake.stored = json.dumps({"preset": "retired-preset", "answered": "skipped",
                                  "pins": ["/tasks", "/gone"], "newOrder": ["capture", "x"]})
        body = _client(_user()).get("/auth/me/shell").json()
        assert body == {"preset": None, "answered": "skipped", "pins": ["/tasks"],
                        "cardOrder": None, "newOrder": ["capture"]}


# ── A guest (done-when 2) ──────────────────────────────────────────────────


class TestAGuest:
    def test_a_guest_with_no_feature_saves_and_reads_a_pin(self, fake: _FakeDB) -> None:
        guest = _user("guest@alpha.example", roles=["guest"], perms=[])
        assert not guest.access.has("feature:tasks")
        client = _client(guest)
        assert client.put("/auth/me/shell", json={"pins": ["/chat"]}).status_code == 200
        assert client.get("/auth/me/shell").status_code == 200

    def test_neither_route_carries_a_feature_gate(self) -> None:
        routes = [r for r in me.me_router.routes if getattr(r, "path", "") == "/auth/me/shell"]
        assert {m for r in routes for m in r.methods} == {"GET", "PUT"}
        for r in routes:
            assert not r.dependencies, f"{r.path} carries a router gate"


# ── One vocabulary, in two languages ───────────────────────────────────────


def _ts_list(source: str, name: str) -> set[str]:
    match = re.search(rf"export const {name}[^=]*=\s*\[(.*?)\];", source, re.S)
    assert match, f"{name} not found in presets.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


class TestOneVocabulary:
    """The gateway checks names; the workbench owns what they mean. A name in
    one list and not the other is a layout one side refuses."""

    def test_the_panes_are_nav_ts_s(self) -> None:
        source = NAV.read_text(encoding="utf-8")
        assert set(re.findall(r'href: "(/[^"]*)"', source)) == me.SHELL_PANES

    def test_the_presets_are_presets_ts_s(self) -> None:
        source = PRESETS.read_text(encoding="utf-8")
        ids = set(re.findall(r'^\s+id: "([a-z-]+)",$', source, re.M))
        assert ids == me.SHELL_PRESETS
        assert len(ids) == 8

    def test_the_cards_are_presets_ts_s(self) -> None:
        source = PRESETS.read_text(encoding="utf-8")
        assert _ts_list(source, "CARD_KEYS") == me.SHELL_CARDS

    def test_the_jobs_are_the_gateway_s_one_list(self) -> None:
        assert me._job_ids() == {j.id for j in JOBS}
