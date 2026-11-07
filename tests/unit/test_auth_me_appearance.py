"""``GET`` and ``PUT /auth/me/appearance`` — the hermetic half (WS-PKG PKG-20).

Spec: ``project-docs/specs/package_host.md`` §9.5 and the PKG-20 row. The R8
half, against a real Postgres, is ``test_auth_me_appearance_r8.py``.

This file holds what a fake proves honestly:

- a guest with no feature reaches both routes, and a caller with no address
  is a 403;
- the body is the whole record: a mode, a density or null, an accent or null.
  A value outside the enums, an accent that the colour rule refuses, or a key
  the model does not know is a 422 that writes nothing;
- the write is the one write of the settings row (``write_member_settings``),
  it names ``appearance`` only, under the session's address, and the server
  stamps ``updated_at``;
- a failed read is a 503, never a default, and a member with no row reads
  all-null;
- the Python accent rule and the TypeScript ``isSafeColor`` accept and refuse
  the SAME samples (``tests/fixtures/accent_samples.json``, read by
  ``syncAppearance.test.ts`` too).

Mutation: let ``put_my_appearance`` take ``body: dict``, and the bad-body cases
fail. Let it keep a client ``updated_at``, and the stamp test fails. Use
Python's own ``\\s`` in ``SAFE_COLOR``, and the sample fence fails.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.admin import me

SAMPLES = Path(__file__).resolve().parents[1] / "fixtures" / "accent_samples.json"


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDB:
    def __init__(self, stored: Any = None, fail: bool = False) -> None:
        self.stored = stored
        self.has_row = False
        self.fail = fail
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        self.sql.append((str(stmt), dict(params or {})))
        if str(stmt).startswith("SELECT appearance"):
            if not self.has_row:
                return _Result(None)
            return _Result((self.stored,))
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


GOOD = {"mode": "light", "density": "compact", "accent": "hsl(347 77% 50%)"}


class TestPut:
    def test_a_member_with_no_feature_saves_the_record(self, fake: _FakeDB) -> None:
        res = _client(_user(perms=[])).put("/auth/me/appearance", json=GOOD)
        assert res.status_code == 200, res.text
        body = res.json()
        assert {k: body[k] for k in GOOD} == GOOD
        [(sql, params)] = _writes(fake)
        assert "INSERT INTO user_settings" in sql
        assert "DO UPDATE SET appearance = EXCLUDED.appearance, updated_at" in sql
        # The JSONB cast keeps its space (`test_sql_bindparam_jsonb_cast.py`).
        assert ":appearance ::jsonb" in sql
        stored = json.loads(params["appearance"])
        assert {k: stored[k] for k in GOOD} == GOOD

    def test_the_server_stamps_updated_at(self, fake: _FakeDB) -> None:
        res = _client(_user()).put("/auth/me/appearance", json=GOOD)
        stamp = res.json()["updated_at"]
        assert isinstance(stamp, str) and stamp.endswith("Z")
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        [(_, params)] = _writes(fake)
        assert json.loads(params["appearance"])["updated_at"] == stamp

    def test_a_client_stamp_is_refused(self, fake: _FakeDB) -> None:
        res = _client(_user()).put(
            "/auth/me/appearance", json={**GOOD, "updated_at": "2020-01-01T00:00:00Z"})
        assert res.status_code == 422
        assert fake.sql == []

    def test_null_density_and_null_accent_are_choices(self, fake: _FakeDB) -> None:
        res = _client(_user()).put(
            "/auth/me/appearance", json={"mode": "dark", "density": None, "accent": None})
        assert res.status_code == 200, res.text
        [(_, params)] = _writes(fake)
        stored = json.loads(params["appearance"])
        assert (stored["mode"], stored["density"], stored["accent"]) == ("dark", None, None)

    def test_the_accent_is_stored_trimmed(self, fake: _FakeDB) -> None:
        res = _client(_user()).put(
            "/auth/me/appearance", json={**GOOD, "accent": "  #E11D48 "})
        assert res.status_code == 200, res.text
        assert res.json()["accent"] == "#E11D48"

    def test_the_write_names_the_session_address_only(self, fake: _FakeDB) -> None:
        res = _client(_user("Bo@alpha.example")).put("/auth/me/appearance", json=GOOD)
        assert res.status_code == 200
        [(_, params)] = _writes(fake)
        assert params["uid"] == "Bo@alpha.example"

    @pytest.mark.parametrize("body", [
        {},
        {"density": "compact"},
        {**GOOD, "mode": "system"},
        {**GOOD, "mode": "Dark"},
        {**GOOD, "mode": None},
        {**GOOD, "density": "huge"},
        {**GOOD, "density": 1},
        {**GOOD, "accent": "url(x)"},
        {**GOOD, "accent": "var(--primary)"},
        {**GOOD, "accent": "red; background: url(x)"},
        {**GOOD, "accent": "rgb(" + "1" * 60 + ")"},
        {**GOOD, "accent": ""},
        {**GOOD, "accent": 5},
        {**GOOD, "look": "classic"},
        # The body never names the member or the tenant (R5).
        {**GOOD, "user_id": "someone@else.example"},
        {**GOOD, "organization_id": "00000000-0000-0000-0000-000000000001"},
    ])
    def test_a_bad_body_is_refused_and_writes_nothing(self, fake: _FakeDB, body: dict) -> None:
        res = _client(_user()).put("/auth/me/appearance", json=body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_a_caller_with_no_address_is_a_403(self, fake: _FakeDB) -> None:
        res = _client(_user(email="")).put("/auth/me/appearance", json=GOOD)
        assert res.status_code == 403
        assert fake.sql == []

    @pytest.mark.parametrize("where", ["query", "header"])
    def test_a_member_named_outside_the_session_changes_nothing(
            self, fake: _FakeDB, where: str) -> None:
        other = "someone@else.example"
        kwargs: dict[str, Any] = {"json": GOOD}
        if where == "query":
            kwargs["params"] = {"user_id": other, "email": other, "member": other}
        else:
            kwargs["headers"] = {"X-User-Email": other, "X-Member": other}
        res = _client(_user("ana@alpha.example")).put("/auth/me/appearance", **kwargs)
        assert res.status_code == 200, res.text
        [(_, params)] = _writes(fake)
        assert params["uid"] == "ana@alpha.example"

    def test_a_failed_write_is_a_503(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client(_user()).put("/auth/me/appearance", json=GOOD)
        assert res.status_code == 503


class TestGet:
    def test_it_reads_the_saved_record(self, fake: _FakeDB) -> None:
        fake.has_row = True
        fake.stored = {**GOOD, "updated_at": "2026-11-07T10:00:00.000Z"}
        res = _client(_user()).get("/auth/me/appearance")
        assert res.json() == {**GOOD, "updated_at": "2026-11-07T10:00:00.000Z"}
        [(sql, params)] = fake.sql
        assert "organization_id" in sql and params == {"uid": "ana@alpha.example"}

    def test_a_json_text_value_reads_too(self, fake: _FakeDB) -> None:
        fake.has_row = True
        fake.stored = json.dumps({**GOOD, "updated_at": "2026-11-07T10:00:00.000Z"})
        assert _client(_user()).get("/auth/me/appearance").json()["mode"] == "light"

    def test_a_member_with_no_row_reads_all_null(self, fake: _FakeDB) -> None:
        assert _client(_user()).get("/auth/me/appearance").json() == me.EMPTY_APPEARANCE

    def test_a_row_with_no_appearance_reads_all_null(self, fake: _FakeDB) -> None:
        fake.has_row = True
        fake.stored = None
        assert _client(_user()).get("/auth/me/appearance").json() == me.EMPTY_APPEARANCE

    def test_a_bad_stored_field_reads_null_and_the_rest_answer(self, fake: _FakeDB) -> None:
        fake.has_row = True
        fake.stored = {"mode": "dark", "density": "huge", "accent": "url(x)",
                       "updated_at": "not a time"}
        assert _client(_user()).get("/auth/me/appearance").json() == {
            "mode": "dark", "density": None, "accent": None, "updated_at": None}

    def test_a_caller_with_no_address_is_a_403(self, fake: _FakeDB) -> None:
        assert _client(_user(email="")).get("/auth/me/appearance").status_code == 403

    def test_a_failed_read_is_a_503_and_never_a_default(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client(_user()).get("/auth/me/appearance")
        assert res.status_code == 503
        assert "mode" not in res.json()


class TestAccentRule:
    """The Python port of `SAFE_COLOR` and the TypeScript `isSafeColor` read one
    sample list. `src/lib/theme/syncAppearance.test.ts` reads the same file."""

    def _samples(self) -> dict[str, list[str]]:
        return json.loads(SAMPLES.read_text(encoding="utf-8"))

    def test_the_sample_file_has_both_halves(self) -> None:
        data = self._samples()
        assert len(data["accepted"]) >= 20 and len(data["refused"]) >= 20

    def test_every_accepted_sample_passes(self) -> None:
        bad = [s for s in self._samples()["accepted"] if not me.is_safe_color(s)]
        assert bad == []

    def test_every_refused_sample_fails(self) -> None:
        bad = [s for s in self._samples()["refused"] if me.is_safe_color(s)]
        assert bad == []

    def test_the_port_names_the_typescript_rule(self) -> None:
        """The TypeScript source still holds the rule this module ports. A
        change there fails here, and the port must follow it."""
        css = (Path(__file__).resolve().parents[2] / "workbench" / "control_plane"
               / "src" / "lib" / "theme" / "css.ts").read_text(encoding="utf-8")
        assert (r"/^(#[0-9a-f]{3,8}|(rgb|hsl|oklch|lab|lch)a?\([0-9a-z%.,/\s+-]*\)"
                r"|[a-z]{3,20})$/i;") in css
        assert "v.length > 0 && v.length <= 64" in css

    def test_the_model_uses_the_rule(self) -> None:
        for s in self._samples()["refused"]:
            with pytest.raises(ValueError):
                me.AppearanceIn(mode="dark", accent=s)


def _must_run(workflow: str) -> list[str]:
    lines = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / workflow
             ).read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "--must-run \\")
    listed: list[str] = []
    for line in lines[start + 1:]:
        listed.append(line.strip().removesuffix("\\").strip())
        if not line.rstrip().endswith("\\"):
            break
    return listed


@pytest.mark.parametrize("workflow", ["pr-check.yml", "deploy.yml"])
def test_the_r8_twin_is_in_the_must_run_list(workflow: str) -> None:
    """The `--must-run` lists are kept by hand. Without the entry, the R8 twin
    skips in CI with no database and the job stays green."""
    assert "tests/unit/test_auth_me_appearance_r8.py" in _must_run(workflow)
