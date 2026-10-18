"""``GET`` and ``PUT /shell/feed/prefs`` — the hermetic half (WS-58 MD-2d).

Spec: ``project-docs/specs/my_day_feed.md`` §13.3 and §16 MD-2d. The R8 half,
against a real Postgres, is ``test_my_day_prefs_r8.py``. It proves the merge,
the seed and the tenant wall. This file holds what a fake can prove honestly:

- the allowlist of three keys, and a 422 for any other key or field;
- the limits of each key (20 kinds, and 10 lines of 300 characters with
  1,500 in all);
- that a refused body writes nothing;
- that the write merges ONE key with ``jsonb_set`` and changes no other
  column of an existing row;
- that a member with no row gets the calendar seed, and a member with a row
  does not;
- that the member comes from the session, never from the body;
- that a failed read or write is a 503, never an empty answer.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.shell import prefs
from gateway.routes.shell.search import router


class _Result:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _FakeDB:
    """Records each statement. ``row`` is the member's stored column, or
    ``_NO_ROW`` when the member has no settings row."""

    def __init__(self, row: Any) -> None:
        self.row = row
        self.fail = False
        self.sql: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.fail:
            raise RuntimeError("database away")
        sql, p = str(stmt), dict(params or {})
        self.sql.append((sql, p))
        if sql.startswith("SELECT my_day_prefs"):
            return _Result(None if self.row is _NO_ROW else (self.row,))
        # The write. Postgres does the merge; the fake only answers a value
        # of the right shape, so the route's answer can be read.
        base = self.row if isinstance(self.row, dict) else {}
        return _Result(({**base, p["pkey"]: json.loads(p["pval"])},))


_NO_ROW = object()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeDB:
    db = _FakeDB({"corrections": ["Acme mail never needs me"]})

    @asynccontextmanager
    async def _session(organization_id: Any = None):
        yield db

    monkeypatch.setattr(prefs, "_tenant_session", _session)
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


def _client(user: UserContext) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _writes(db: _FakeDB) -> list[tuple[str, dict[str, Any]]]:
    return [(s, p) for s, p in db.sql if not s.startswith("SELECT")]


def _put(body: Any, user: UserContext | None = None):
    return _client(user or _user()).put("/shell/feed/prefs", json=body)


# ── The allowlist and the limits ───────────────────────────────────────────


class TestTheAllowlist:
    def test_the_three_keys_are_the_spec_s(self) -> None:
        assert prefs.PREF_KEYS == ("mutes", "open_in_app", "corrections")
        assert set(prefs._CLEAN) == set(prefs.PREF_KEYS)

    @pytest.mark.parametrize("body", [
        # Any other key is a 422 (§16 MD-2d).
        {"key": "theme", "value": []},
        {"key": "shell_prefs", "value": []},
        {"key": "Mutes", "value": []},
        {"key": 5, "value": []},
        # The body is exactly {key, value}.
        {"key": "mutes"},
        {"value": ["overdue"]},
        {"key": "mutes", "value": [], "extra": 1},
        # The body never names the member or the tenant (R5, R11).
        {"key": "mutes", "value": [], "user_id": "someone@else.example"},
        {"key": "mutes", "value": [],
         "organization_id": "00000000-0000-0000-0000-000000000001"},
        # Not an object at all.
        None,
        [],
        ["mutes", []],
        "mutes",
    ])
    def test_a_body_outside_the_allowlist_is_refused(self, fake: _FakeDB, body: Any) -> None:
        res = _put(body)
        assert res.status_code == 422, res.text
        assert fake.sql == []


_LINE = "x" * prefs.CORRECTION_MAX


class TestTheLimits:
    @pytest.mark.parametrize("key", ["mutes", "open_in_app"])
    @pytest.mark.parametrize("value", [
        "overdue",                       # a kind, not a list of kinds
        None,
        [1],
        ["overdue", None],
        ["weather"],                     # not a kind of the registry
        ["overdue", "OVERDUE"],
        ["overdue"] * (prefs.MAX_KINDS + 1),
    ])
    def test_a_bad_list_of_kinds_is_refused(self, fake: _FakeDB, key: str, value: Any) -> None:
        res = _put({"key": key, "value": value})
        assert res.status_code == 422, res.text
        assert fake.sql == []

    @pytest.mark.parametrize("value", [
        "one line",                      # a line, not a list of lines
        None,
        [3],
        [""],
        ["   "],
        ["x" * (prefs.CORRECTION_MAX + 1)],
        ["a"] * (prefs.MAX_CORRECTIONS + 1),
        # Six lines of 300 hold 1,800 characters, more than the 1,500 cap.
        [_LINE] * 6,
        ["a line\nwith a break"],
        ["a NUL \x00 here"],
    ])
    def test_bad_corrections_are_refused(self, fake: _FakeDB, value: Any) -> None:
        res = _put({"key": "corrections", "value": value})
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_a_lone_surrogate_is_refused_before_postgres(self, fake: _FakeDB) -> None:
        """A JS ``slice()`` through an emoji leaves half of it. jsonb refuses
        a lone surrogate, so the route must refuse it first, or it answers 503
        and not 422. The body is raw bytes, because httpx cannot encode it."""
        raw = b'{"key": "corrections", "value": ["half an emoji \\ud83d"]}'
        res = _client(_user()).put("/shell/feed/prefs", content=raw,
                                   headers={"Content-Type": "application/json"})
        assert res.status_code == 422, res.text
        assert fake.sql == []

    def test_each_limit_admits_its_own_edge(self, fake: _FakeDB) -> None:
        kinds = sorted(prefs.feed_kinds())
        many = (kinds * prefs.MAX_KINDS)[:prefs.MAX_KINDS]
        assert _put({"key": "mutes", "value": many}).status_code == 200
        assert _put({"key": "open_in_app", "value": many}).status_code == 200
        five = [_LINE] * (prefs.CORRECTIONS_CLIP // prefs.CORRECTION_MAX)
        assert sum(map(len, five)) == prefs.CORRECTIONS_CLIP
        assert _put({"key": "corrections", "value": five}).status_code == 200
        ten = ["a"] * prefs.MAX_CORRECTIONS
        assert _put({"key": "corrections", "value": ten}).status_code == 200
        assert _put({"key": "mutes", "value": []}).status_code == 200

    def test_the_kinds_are_the_base_table_s(self) -> None:
        # MD-3c: the kinds are the rows of `feed_rank.BASE`, so a member may
        # mute an MD-3 kind while its provider is dark or not built yet.
        from gateway.routes.shell import feed_rank, needs

        every = set().union(*(p.kinds for p in needs.REGISTRY.values()))
        assert every <= prefs.feed_kinds() == set(feed_rank.BASE)
        # The Calendar kinds are feed-only, and a member may mute them too.
        assert {"block_soon", "overdue", "colleague_out", "agent_asks"} <= prefs.feed_kinds()
        # §5.9 item 10.6: the table holds exactly the cap. 20 after MD-3,
        # 21 since WS-53 CRM-Q28 added `digest_ready`, and 22 since WS-53
        # CRM-Q32-4 added `crm_open_questions`.
        assert len(prefs.feed_kinds()) == prefs.MAX_KINDS == 22

    def test_a_list_keeps_its_order_and_loses_its_repeats(self, fake: _FakeDB) -> None:
        res = _put({"key": "mutes", "value": ["block_soon", "overdue", "block_soon"]})
        assert res.status_code == 200
        [(_, params)] = _writes(fake)
        assert json.loads(params["pval"]) == ["block_soon", "overdue"]

    def test_a_correction_is_stored_trimmed(self, fake: _FakeDB) -> None:
        _put({"key": "corrections", "value": ["  Acme mail never needs me  "]})
        [(_, params)] = _writes(fake)
        assert json.loads(params["pval"]) == ["Acme mail never needs me"]


# ── The write merges one key ───────────────────────────────────────────────


class TestTheMerge:
    def test_the_write_merges_one_key_with_jsonb_set(self, fake: _FakeDB) -> None:
        res = _put({"key": "mutes", "value": ["overdue"]})
        assert res.status_code == 200, res.text
        [(sql, params)] = _writes(fake)
        assert (params["pkey"], json.loads(params["pval"])) == ("mutes", ["overdue"])
        update = sql.split("DO UPDATE SET", 1)[1]
        # The existing column is the base of the merge, never replaced.
        assert update.lstrip().startswith("my_day_prefs = jsonb_set(")
        assert "user_settings.my_day_prefs" in update
        assert "EXCLUDED.my_day_prefs" not in update
        # An existing row changes in `my_day_prefs` only.
        assert "shell_prefs" not in sql
        assert "day_start_hour" not in update
        # The tenant is the bound one, and the conflict key is 239's.
        assert "current_setting('app.tenant_id', true)" in sql
        assert "ON CONFLICT (organization_id, user_id)" in sql

    def test_the_answer_keeps_the_other_keys(self, fake: _FakeDB) -> None:
        res = _put({"key": "mutes", "value": ["overdue"]})
        assert res.json() == {"corrections": ["Acme mail never needs me"],
                              "mutes": ["overdue"]}

    def test_a_member_with_a_row_gets_no_seed(self, fake: _FakeDB) -> None:
        _put({"key": "mutes", "value": ["overdue"]})
        [(_, params)] = _writes(fake)
        assert fake.seeded == []
        assert "day_start_hour" not in params

    def test_a_member_with_no_row_gets_the_calendar_seed(self, fake: _FakeDB) -> None:
        fake.row = _NO_ROW
        res = _put({"key": "open_in_app", "value": ["needs_reply"]})
        assert res.status_code == 200, res.text
        [(sql, params)] = _writes(fake)
        assert fake.seeded == ["ana@alpha.example"]
        assert (params["day_start_hour"], params["day_end_hour"]) == (8, 19)
        insert = sql.split("ON CONFLICT", 1)[0]
        assert "day_start_hour" in insert and "my_day_prefs" in insert
        assert "jsonb_build_object(" in insert
        assert res.json() == {"open_in_app": ["needs_reply"]}

    def test_a_null_column_merges_as_an_empty_object(self, fake: _FakeDB) -> None:
        fake.row = None
        res = _put({"key": "corrections", "value": ["Newsletters never need me"]})
        assert res.status_code == 200
        assert fake.seeded == []
        assert res.json() == {"corrections": ["Newsletters never need me"]}


# ── The member and the failures ────────────────────────────────────────────


class TestTheMember:
    def test_the_member_is_the_session_s(self, fake: _FakeDB) -> None:
        _put({"key": "mutes", "value": []}, _user("bo@alpha.example"))
        assert {p["uid"] for _, p in fake.sql} == {"bo@alpha.example"}

    def test_no_session_address_is_a_401(self, fake: _FakeDB) -> None:
        assert _put({"key": "mutes", "value": []}, _user("")).status_code == 401
        res = _client(_user("")).get("/shell/feed/prefs")
        assert res.status_code == 401
        assert fake.sql == []

    def test_a_failed_write_is_a_503(self, fake: _FakeDB) -> None:
        fake.fail = True
        assert _put({"key": "mutes", "value": ["overdue"]}).status_code == 503


class TestTheRead:
    def test_no_row_reads_as_an_empty_object(self, fake: _FakeDB) -> None:
        fake.row = _NO_ROW
        assert _client(_user()).get("/shell/feed/prefs").json() == {}

    def test_a_null_column_reads_as_an_empty_object(self, fake: _FakeDB) -> None:
        fake.row = None
        assert _client(_user()).get("/shell/feed/prefs").json() == {}

    def test_an_absent_key_stays_absent(self, fake: _FakeDB) -> None:
        res = _client(_user()).get("/shell/feed/prefs")
        assert res.status_code == 200
        assert res.json() == {"corrections": ["Acme mail never needs me"]}

    def test_the_read_drops_what_it_does_not_know(self, fake: _FakeDB) -> None:
        fake.row = {"mutes": ["overdue", "gone_kind", 7], "open_in_app": "x",
                    "corrections": ["one", 2], "theme": "dark"}
        assert _client(_user()).get("/shell/feed/prefs").json() == {
            "mutes": ["overdue"], "corrections": ["one"]}

    def test_a_json_string_column_reads_too(self, fake: _FakeDB) -> None:
        fake.row = json.dumps({"mutes": ["block_soon"]})
        assert _client(_user()).get("/shell/feed/prefs").json() == {"mutes": ["block_soon"]}

    def test_a_failed_read_is_a_503_never_empty(self, fake: _FakeDB) -> None:
        fake.fail = True
        res = _client(_user()).get("/shell/feed/prefs")
        assert res.status_code == 503
