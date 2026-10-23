"""``/auth/me/assistant-profile`` — the hermetic half (WS-63 PZ-1).

Spec: ``project-docs/specs/personalization.md`` §4. The R8 half, against a
real Postgres, is ``test_assistant_profile_r8.py``. It proves the merge, the
per-item edits, the seed, the concurrency and the tenant wall. This file
holds what a fake can prove honestly:

- every cap of §4.2 is a 422 that names the cap, and a refused body writes
  nothing;
- each body forbids an extra field, so a body cannot name a member, an org,
  an ``id``, a ``source`` or an ``at``;
- a lone surrogate or a control character is a 422, never a 503;
- the write is strict and the read is lenient;
- the SQL takes the tenant from the bound session, and no parameter carries
  an org;
- a per-item edit or delete is ONE ``UPDATE`` statement, never a read and a
  second write;
- a missing session address is a 401, and a failed read or write is a 503.
"""
from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.admin import assistant_profile as ap
from gateway.routes.admin.me import me_router

_NO_ROW = object()
_URL = "/auth/me/assistant-profile"


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDB:
    """Records each statement. ``row`` is the member's stored column, or
    ``_NO_ROW`` when the member has no settings row. Postgres does the merge
    in the real route. The fake only answers a value of the right shape, so
    the route's checks on the stored value can be read."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.fail = False
        self.sql: list[tuple[str, dict[str, Any]]] = []
        self.rolled_back = 0

    def _base(self) -> dict[str, Any]:
        return copy.deepcopy(self.row) if isinstance(self.row, dict) else {}

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        sql, p = str(stmt), dict(params or {})
        self.sql.append((sql, p))
        if sql.startswith("SELECT assistant_profile"):
            return _Result(None if self.row is _NO_ROW else (self.row,))
        if sql.startswith("INSERT INTO user_settings"):
            base = self._base()
            base["v"] = 1
            if "pkey" in p:
                base[p["pkey"]] = json.loads(p["pval"])
            else:
                items = base.get(p["lkey"])
                base[p["lkey"]] = [*(items if isinstance(items, list) else []),
                                   json.loads(p["item"])]
            self.row = base
            return _Result((base,))
        if "assistant_profile = NULL" in sql:
            if self.row is not _NO_ROW:
                self.row = None
            return _Result(None)
        # A per-item UPDATE.
        base = self._base()
        items = base.get(p["lkey"])
        if not isinstance(items, list) or not any(
                isinstance(i, dict) and i.get("id") == p["iid"] for i in items):
            return _Result(None)
        if "patch" in p:
            patch = json.loads(p["patch"])
            base[p["lkey"]] = [{**i, **patch} if i.get("id") == p["iid"] else i
                               for i in items]
        else:
            base[p["lkey"]] = [i for i in items if i.get("id") != p["iid"]]
        self.row = base
        return _Result((base,))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB({"v": 1, "learning": True, "address": {"name": "VJ"},
                  "facts": [{"id": "f1", "text": "Travels every Friday",
                             "kind": "routine", "source": "chat",
                             "at": "2026-10-23T11:00:00Z"}],
                  "aliases": [{"id": "a1", "alias": "Raj", "email": "raj@fracktal.in"}]})

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        # As `tenant_session`: an exception inside the block rolls back.
        saved = copy.deepcopy(db.row) if db.row is not _NO_ROW else _NO_ROW
        try:
            yield db
        except Exception:
            db.row = saved
            db.rolled_back += 1
            raise

    monkeypatch.setattr(ap, "_tenant_session", _session)
    db.seeded = []

    async def _seed(_db: Any, uid: str) -> dict[str, int]:
        db.seeded.append(uid)
        return {"day_start_hour": 8, "day_end_hour": 19}

    import gateway.routes.tasks.settings as settings

    monkeypatch.setattr(settings, "_seed_from_work_schedule", _seed)
    return db


def _user(email: str = "ana@alpha.example") -> UserContext:
    # No feature at all: the routes need none, as `/auth/me/shell` needs none.
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access([], roles=["member"]))


def _client(user: UserContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(me_router)
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    return TestClient(app)


def _writes(db: _FakeDB) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in db.sql if not s.startswith("SELECT")]


def _put(body: Any, user: UserContext | None = None):
    return _client(user).put(_URL, json=body)


def _detail(res: Any) -> str:
    return json.dumps(res.json().get("detail"))


# ── PUT: the allowlist and the caps ────────────────────────────────────────


class TestThePut:
    @pytest.mark.parametrize("body", [
        {"key": "facts", "value": []},          # a list has its own routes
        {"key": "aliases", "value": []},
        {"key": "v", "value": 2},
        {"key": "Learning", "value": True},
        {"key": "learning"},
        {"value": True},
        {"key": "learning", "value": True, "extra": 1},
        # The body never names the member or the tenant (R5, R11).
        {"key": "learning", "value": True, "user_id": "someone@else.example"},
        {"key": "learning", "value": True,
         "organization_id": "00000000-0000-0000-0000-000000000001"},
        None, [], "learning",
    ])
    def test_a_body_outside_the_allowlist_is_refused(self, fake: _FakeDB, body: Any) -> None:
        res = _put(body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    @pytest.mark.parametrize(("key", "value", "names"), [
        ("learning", "yes", "true or false"),
        ("learning", 1, "true or false"),
        ("address", "VJ", "an object"),
        ("address", {"name": "x" * (ap.NAME_MAX + 1)}, "40 characters"),
        ("address", {"name": ""}, "empty"),
        ("address", {"formality": "posh"}, "casual, neutral, formal"),
        ("address", {"greeting": "loud"}, "none, brief, warm"),
        ("address", {"nickname": "VJ"}, "unknown key"),
        ("style", {"length": "huge"}, "short, default, detailed"),
        ("style", {"language": "x" * (ap.LANGUAGE_MAX + 1)}, "16 characters"),
        ("style", {"language": "english please"}, "BCP 47"),
        ("style", {"notes": "one"}, "a list"),
        ("style", {"notes": ["a"] * (ap.NOTES_MAX + 1)}, "at most 5"),
        ("style", {"notes": ["x" * (ap.NOTE_MAX + 1)]}, "200 characters"),
        ("style", {"notes": ["a line\nwith a break"]}, "control character"),
        ("style", {"tone": "dry"}, "unknown key"),
    ])
    def test_each_cap_is_a_422_that_names_it(
        self, fake: _FakeDB, key: str, value: Any, names: str,
    ) -> None:
        res = _put({"key": key, "value": value})
        assert res.status_code == 422, res.text
        assert names in _detail(res)
        assert fake.sql == []

    def test_each_cap_admits_its_own_edge(self, fake: _FakeDB) -> None:
        assert _put({"key": "address", "value": {
            "name": "x" * ap.NAME_MAX, "formality": "formal", "greeting": "warm"},
        }).status_code == 200
        assert _put({"key": "style", "value": {
            "length": "detailed", "language": "en-IN",
            "notes": ["x" * ap.NOTE_MAX] * ap.NOTES_MAX}}).status_code == 200
        assert _put({"key": "learning", "value": False}).status_code == 200

    def test_the_write_merges_one_key_with_jsonb_set(self, fake: _FakeDB) -> None:
        res = _put({"key": "style", "value": {"length": "short"}})
        assert res.status_code == 200, res.text
        [(sql, params)] = _writes(fake)
        assert (params["pkey"], json.loads(params["pval"])) == ("style", {"length": "short"})
        update = sql.split("DO UPDATE SET", 1)[1]
        assert update.lstrip().startswith("assistant_profile = jsonb_set(")
        assert "user_settings.assistant_profile" in update
        assert "EXCLUDED.assistant_profile" not in update
        # An existing row changes in `assistant_profile` only.
        assert "shell_prefs" not in sql and "my_day_prefs" not in sql
        assert "day_start_hour" not in update
        # The first write sets the version.
        assert "ARRAY['v']" in update
        assert "current_setting('app.tenant_id', true)" in sql
        assert "ON CONFLICT (organization_id, user_id)" in sql

    def test_the_answer_is_the_whole_profile(self, fake: _FakeDB) -> None:
        res = _put({"key": "learning", "value": False})
        body = res.json()
        assert body["learning"] is False
        assert body["address"] == {"name": "VJ"}
        assert [f["id"] for f in body["facts"]] == ["f1"]
        assert body["v"] == 1

    def test_a_name_is_stored_trimmed(self, fake: _FakeDB) -> None:
        _put({"key": "address", "value": {"name": "  VJ  "}})
        [(_, params)] = _writes(fake)
        assert json.loads(params["pval"]) == {"name": "VJ"}

    def test_a_member_with_no_row_gets_the_calendar_seed(self, fake: _FakeDB) -> None:
        fake.row = _NO_ROW
        res = _put({"key": "learning", "value": False})
        assert res.status_code == 200, res.text
        [(sql, params)] = _writes(fake)
        assert fake.seeded == ["ana@alpha.example"]
        assert (params["day_start_hour"], params["day_end_hour"]) == (8, 19)
        insert = sql.split("ON CONFLICT", 1)[0]
        assert "day_start_hour" in insert and "jsonb_build_object('v', 1" in insert

    def test_a_member_with_a_row_gets_no_seed(self, fake: _FakeDB) -> None:
        _put({"key": "learning", "value": False})
        assert fake.seeded == []

    def test_the_whole_profile_cap_rolls_the_write_back(self, fake: _FakeDB) -> None:
        fake.row = {"facts": [{"id": f"f{i}", "text": "x" * 300, "kind": "about"}
                              for i in range(20)]}
        before = copy.deepcopy(fake.row)
        res = _put({"key": "learning", "value": False})
        assert res.status_code == 422
        assert res.json()["detail"] == "profile exceeds 6000 characters"
        assert fake.rolled_back == 1
        assert fake.row == before


class TestTheText:
    @pytest.mark.parametrize(("url", "raw"), [
        (f"{_URL}/facts", b'{"text": "half an emoji \\ud83d", "kind": "about"}'),
        (f"{_URL}/aliases", b'{"alias": "R\\udc00j", "email": "raj@fracktal.in"}'),
    ])
    def test_a_lone_surrogate_is_refused_before_postgres(
        self, fake: _FakeDB, url: str, raw: bytes,
    ) -> None:
        """jsonb refuses a lone surrogate, so the route must refuse it first,
        or it answers 503 and not 422. The body is raw bytes, because httpx
        cannot encode it."""
        res = _client().post(url, content=raw, headers={"Content-Type": "application/json"})
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_a_lone_surrogate_in_a_put_is_refused(self, fake: _FakeDB) -> None:
        raw = b'{"key": "address", "value": {"name": "V\\ud83dJ"}}'
        res = _client().put(_URL, content=raw, headers={"Content-Type": "application/json"})
        assert res.status_code == 422, res.text
        assert fake.sql == []

    @pytest.mark.parametrize("key", ["address", "style"])
    def test_a_lone_surrogate_in_a_key_is_a_422_not_a_500(self, fake: _FakeDB, key: str) -> None:
        # PZ-1 review, P2: the 422 message named the member's key, and a
        # lone surrogate could not be encoded, so the answer was a 500.
        raw = b'{"key": "%s", "value": {"\\ud800": 1}}' % key.encode()
        res = _client().put(_URL, content=raw, headers={"Content-Type": "application/json"})
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_a_nul_is_refused(self, fake: _FakeDB) -> None:
        res = _client().post(f"{_URL}/facts", json={"text": "a \x00 here", "kind": "about"})
        assert res.status_code == 422
        assert fake.sql == []

    def test_the_422_does_not_echo_the_input(self, fake: _FakeDB) -> None:
        res = _client().post(f"{_URL}/facts", json={"text": "secret " * 60, "kind": "about"})
        assert res.status_code == 422
        assert "secret" not in res.text
        assert "300 characters" in res.text


# ── Facts ──────────────────────────────────────────────────────────────────


class TestTheFacts:
    def test_an_add_is_201_and_the_server_sets_id_source_and_at(self, fake: _FakeDB) -> None:
        res = _client().post(f"{_URL}/facts", json={"text": "Lunch at 13:00", "kind": "routine"})
        assert res.status_code == 201, res.text
        fact = res.json()
        assert fact["id"].startswith("f") and fact["id"] != "f1"
        assert (fact["text"], fact["kind"], fact["source"]) == (
            "Lunch at 13:00", "routine", "settings")
        assert fact["at"].endswith("Z") and fact["at"][4] == "-"
        [(sql, params)] = _writes(fake)
        assert params["lkey"] == "facts"
        assert "jsonb_build_array(CAST(:item AS jsonb))" in sql

    @pytest.mark.parametrize("body", [
        {"text": "x", "kind": "secret"},
        {"text": "x"},
        {"kind": "about"},
        {"text": "x" * (ap.FACT_MAX + 1), "kind": "about"},
        {"text": "   ", "kind": "about"},
        # The server sets these, never the body.
        {"text": "x", "kind": "about", "source": "chat"},
        {"text": "x", "kind": "about", "id": "f1"},
        {"text": "x", "kind": "about", "at": "2020-01-01T00:00:00Z"},
        {"text": "x", "kind": "about", "organization_id": "x"},
        {"text": "x", "kind": "about", "user_id": "someone@else.example"},
        None,
    ])
    def test_a_bad_fact_is_refused(self, fake: _FakeDB, body: Any) -> None:
        res = _client().post(f"{_URL}/facts", json=body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_the_41st_fact_is_refused_and_rolled_back(self, fake: _FakeDB) -> None:
        fake.row = {"facts": [{"id": f"f{i}", "text": "x", "kind": "about"}
                              for i in range(ap.FACTS_MAX)]}
        res = _client().post(f"{_URL}/facts", json={"text": "one more", "kind": "about"})
        assert res.status_code == 422
        assert res.json()["detail"] == "at most 40 facts"
        assert fake.rolled_back == 1
        assert len(fake.row["facts"]) == ap.FACTS_MAX

    def test_an_edit_is_one_update_statement(self, fake: _FakeDB) -> None:
        res = _client().patch(f"{_URL}/facts/f1", json={"kind": "work"})
        assert res.status_code == 200, res.text
        assert res.json()["kind"] == "work"
        assert res.json()["text"] == "Travels every Friday"
        [(sql, params)] = fake.sql
        assert sql.startswith("UPDATE user_settings SET assistant_profile = jsonb_set(")
        assert "jsonb_agg(" in sql and "WITH ORDINALITY" in sql
        assert "current_setting('app.tenant_id', true)" in sql
        assert (params["iid"], json.loads(params["patch"])) == ("f1", {"kind": "work"})

    @pytest.mark.parametrize("body", [
        {}, {"text": None}, {"kind": "secret"}, {"text": "x", "source": "chat"},
        {"text": "x" * (ap.FACT_MAX + 1)},
    ])
    def test_a_bad_edit_is_refused(self, fake: _FakeDB, body: Any) -> None:
        res = _client().patch(f"{_URL}/facts/f1", json=body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_an_edit_of_an_absent_fact_is_404(self, fake: _FakeDB) -> None:
        assert _client().patch(f"{_URL}/facts/f9", json={"text": "x"}).status_code == 404
        fake.row = _NO_ROW
        assert _client().patch(f"{_URL}/facts/f1", json={"text": "x"}).status_code == 404

    def test_a_delete_is_204_and_one_statement(self, fake: _FakeDB) -> None:
        res = _client().delete(f"{_URL}/facts/f1")
        assert res.status_code == 204
        assert res.content == b""
        [(sql, _)] = fake.sql
        assert sql.startswith("UPDATE user_settings")
        assert "IS DISTINCT FROM" in sql
        assert fake.row["facts"] == []

    def test_a_delete_of_an_absent_fact_is_404(self, fake: _FakeDB) -> None:
        assert _client().delete(f"{_URL}/facts/f9").status_code == 404
        # An alias id is not a fact id.
        assert _client().delete(f"{_URL}/facts/a1").status_code == 404


# ── Aliases ────────────────────────────────────────────────────────────────


class TestTheAliases:
    def test_an_add_is_201(self, fake: _FakeDB) -> None:
        res = _client().post(f"{_URL}/aliases", json={"alias": "Mo", "email": "mo@alpha.example"})
        assert res.status_code == 201, res.text
        assert res.json()["id"].startswith("a")
        assert (res.json()["alias"], res.json()["email"]) == ("Mo", "mo@alpha.example")

    @pytest.mark.parametrize("body", [
        {"alias": "Mo", "email": "not an address"},
        {"alias": "Mo", "email": "mo@nodot"},
        {"alias": "Mo"},
        {"alias": "x" * (ap.ALIAS_MAX + 1), "email": "mo@alpha.example"},
        {"alias": "Mo", "email": "mo@alpha.example", "id": "a1"},
        {"alias": "Mo", "email": "mo@alpha.example", "organization_id": "x"},
    ])
    def test_a_bad_alias_is_refused(self, fake: _FakeDB, body: Any) -> None:
        res = _client().post(f"{_URL}/aliases", json=body)
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_the_51st_alias_is_refused(self, fake: _FakeDB) -> None:
        fake.row = {"aliases": [{"id": f"a{i}", "alias": "x", "email": "x@y.z"}
                                for i in range(ap.ALIASES_MAX)]}
        res = _client().post(f"{_URL}/aliases", json={"alias": "Mo", "email": "mo@a.b"})
        assert res.status_code == 422
        assert res.json()["detail"] == "at most 50 aliases"
        assert fake.rolled_back == 1

    def test_an_edit_and_a_delete(self, fake: _FakeDB) -> None:
        res = _client().patch(f"{_URL}/aliases/a1", json={"email": "raj@other.in"})
        assert res.status_code == 200, res.text
        assert res.json() == {"id": "a1", "alias": "Raj", "email": "raj@other.in"}
        assert _client().patch(f"{_URL}/aliases/a9", json={"alias": "R"}).status_code == 404
        assert _client().delete(f"{_URL}/aliases/a9").status_code == 404
        assert _client().delete(f"{_URL}/aliases/a1").status_code == 204
        assert fake.row["aliases"] == []


# ── Forget everything ──────────────────────────────────────────────────────


class TestTheForget:
    def test_delete_sets_the_column_null(self, fake: _FakeDB) -> None:
        res = _client().delete(_URL)
        assert res.status_code == 204
        [(sql, params)] = fake.sql
        assert sql.startswith("UPDATE user_settings SET assistant_profile = NULL")
        assert "current_setting('app.tenant_id', true)" in sql
        assert params == {"uid": "ana@alpha.example"}
        assert fake.row is None
        assert _client().get(_URL).json() == ap.empty_profile()


# ── The read ───────────────────────────────────────────────────────────────


class TestTheRead:
    def test_no_row_and_null_read_as_the_empty_profile(self, fake: _FakeDB) -> None:
        empty = {"v": 1, "learning": True, "address": {}, "style": {},
                 "aliases": [], "facts": []}
        fake.row = _NO_ROW
        assert _client().get(_URL).json() == empty
        fake.row = None
        assert _client().get(_URL).json() == empty

    def test_the_read_drops_what_it_does_not_know(self, fake: _FakeDB) -> None:
        fake.row = {
            "v": 1, "learning": "yes", "theme": "dark",
            "address": {"name": "VJ", "formality": "posh", "nickname": "x"},
            "style": {"length": "short", "language": "not a tag", "notes": "x"},
            "facts": [{"id": "f1", "text": "ok", "kind": "about", "source": "fax"},
                      {"id": "f2", "text": "bad kind", "kind": "secret"},
                      {"text": "no id", "kind": "about"}, 7],
            "aliases": [{"id": "a1", "alias": "Raj", "email": "bad"},
                        {"id": "a2", "alias": "Mo", "email": "mo@a.b"}],
        }
        assert _client().get(_URL).json() == {
            "v": 1, "learning": True,
            "address": {"name": "VJ"}, "style": {"length": "short"},
            "facts": [{"id": "f1", "text": "ok", "kind": "about"},
                      {"id": "f2", "text": "bad kind", "kind": "about"}],
            "aliases": [{"id": "a2", "alias": "Mo", "email": "mo@a.b"}],
        }

    def test_an_edit_of_an_item_the_read_drops_is_not_a_503(self, fake: _FakeDB) -> None:
        # PZ-1 verify, finding 2: the write commits, so the answer is a 200.
        fake.row = {"aliases": [{"id": "a1", "alias": "Raj", "email": "bad"}]}
        res = _client().patch(f"{_URL}/aliases/a1", json={"alias": "R"})
        assert res.status_code == 200, res.text
        assert res.json()["id"] == "a1"

    def test_a_json_string_column_reads_too(self, fake: _FakeDB) -> None:
        fake.row = json.dumps({"learning": False})
        assert _client().get(_URL).json()["learning"] is False

    def test_a_failed_read_is_a_503_never_empty(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client().get(_URL)
        assert res.status_code == 503
        assert res.json() == {"detail": "unavailable"}


# ── The member and the tenant ──────────────────────────────────────────────


_CALLS = [
    ("get", _URL, None), ("put", _URL, {"key": "learning", "value": True}),
    ("delete", _URL, None),
    ("post", f"{_URL}/facts", {"text": "x", "kind": "about"}),
    ("patch", f"{_URL}/facts/f1", {"text": "x"}), ("delete", f"{_URL}/facts/f1", None),
    ("post", f"{_URL}/aliases", {"alias": "x", "email": "x@y.z"}),
    ("patch", f"{_URL}/aliases/a1", {"alias": "x"}), ("delete", f"{_URL}/aliases/a1", None),
]


def _call(user: UserContext, method: str, url: str, body: Any):
    client = _client(user)
    if body is None:
        return getattr(client, method)(url)
    return client.request(method.upper(), url, json=body)


class TestTheMember:
    @pytest.mark.parametrize(("method", "url", "body"), _CALLS)
    def test_no_session_address_is_a_401(
        self, fake: _FakeDB, method: str, url: str, body: Any,
    ) -> None:
        assert _call(_user(""), method, url, body).status_code == 401
        assert fake.sql == []

    @pytest.mark.parametrize(("method", "url", "body"), _CALLS)
    def test_the_member_is_the_session_s_and_no_org_is_a_parameter(
        self, fake: _FakeDB, method: str, url: str, body: Any,
    ) -> None:
        _call(_user("bo@alpha.example"), method, url, body)
        assert fake.sql
        for sql, params in fake.sql:
            assert params["uid"] == "bo@alpha.example"
            assert not any("org" in k for k in params), params
            assert "current_setting('app.tenant_id', true)" in sql

    @pytest.mark.parametrize(("method", "url", "body"), _CALLS[1:])
    def test_a_failed_write_is_a_503(
        self, fake: _FakeDB, method: str, url: str, body: Any,
    ) -> None:
        fake.fail = True
        res = _call(_user(), method, url, body)
        assert res.status_code == 503
        assert res.json() == {"detail": "unavailable"}


def test_no_jsonb_cast_sits_beside_a_bind_name() -> None:
    """``:name::jsonb`` reads as a bind called ``name::jsonb`` to SQLAlchemy.
    Every cast here is ``CAST(... AS ...)``."""
    checked = []
    for name in dir(ap):
        value = getattr(ap, name)
        if name.startswith("__") or not name.startswith("_") or not isinstance(value, str):
            continue
        checked.append(name)
        assert "::" not in value, name
    assert {"_READ_SQL", "_PATCH_ITEM_SQL", "_DELETE_ITEM_SQL", "_MERGE_KEY",
            "_APPEND_ITEM", "_FORGET_SQL"} <= set(checked)
