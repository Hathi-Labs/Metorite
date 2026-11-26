"""WS-TOF TOF-3a F12a: the store of skill runs, with no database.

Spec: ``project-docs/specs/ai_studio_rows/F12.md``, "The table
``studio_skill_runs`` (F12a)", "The input hash" and "The reason codes".
Code: ``gateway/studio/skill_runs.py``. The SQL runs on a real Postgres in
``test_studio_skill_runs_r8.py``.

What this holds:

1. The closed reason sets: a live row takes ``passed`` or a live code, a
   test row only the seven faults of the skill, a proposal each code of
   ``skill_tests.REASONS``.
2. The input hash: the org id, one NUL byte, and the input after the one
   white-space rule. A change of white space alone gives the same hash.
3. Each writer refuses a bad shape BEFORE any statement: a writes item that
   holds a value, a reason outside its set, a live row with no run id or no
   input, a test row with no test id, a bad ``repair_of``, a cap.
4. No column gets the input text. The org of the hash comes from the bound
   session.
5. One module runs SQL on the table, and it holds no UPDATE.

Mutations, each red here: drop ``invalid_call`` from ``SKILL_FAULTS``
(case 1). Hash the input with no fold (case 2). Drop the item key check of
``check_writes`` (case 3, ``writes_value``). Pass ``input_text`` as a
parameter of the INSERT (case 4).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from gateway.studio import skill_runs as sr
from gateway.studio import skill_tests

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "apps" / "services" / "gateway" / "gateway" / "studio" / "skill_runs.py"

ORG = "5b0a6f4e-0f1c-4c55-9a51-3d1f2b9b7c01"
SKILL = "0d4e8f2a-1b3c-4d5e-8f60-718293a4b5c6"
REPAIR = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
KEY = "ab" * 32
ANA = "ana@f12a.test"
INPUT = "Call Asha on 555 0100"


class _Result:
    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar(self) -> Any:
        return self._scalar


class _FakeDB:
    """Records each statement. *answers* maps a part of the SQL to a result."""

    def __init__(self, answers: dict[str, _Result] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.answers = answers or {}

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        self.calls.append((sql, dict(params or {})))
        for part, result in self.answers.items():
            if part in sql:
                return result
        if "current_setting('app.tenant_id', true)" in sql and sql.startswith("SELECT current"):
            return _Result(scalar=ORG)
        return _Result()


def _row(**kw: Any) -> tuple[Any, ...]:
    base = {
        "id": str(uuid.uuid4()), "skill_id": SKILL, "version": 1, "kind": "live",
        "passed": False, "reason": "tool_error", "detail": "create_task: ToolError",
        "body_key": KEY, "input_hash": "cd" * 32, "writes": "[]", "repair_of": None,
        "test_id": None, "run_id": "run-1", "thread_id": None, "credits": None,
        "created_by": ANA, "created_at": None,
    }
    base.update(kw)
    return tuple(base.values())


# ── 1. The reason codes ─────────────────────────────────────────────────────


def test_the_three_reason_sets_are_closed_as_the_spec_says() -> None:
    faults = {"other_action", "missing_write", "extra_write", "field_mismatch",
              "invalid_call", "run_failed", "call_cap"}
    live = {"tool_error", "card_declined", "member_said_wrong"}
    assert faults == sr.SKILL_FAULTS
    assert live == sr.LIVE_CODES
    assert sr.REASONS["live"] == sr.LIVE_CODES | {"passed"}
    assert sr.REASONS["test"] == sr.SKILL_FAULTS
    assert set(skill_tests.REASONS) <= sr.REASONS["proposal"]
    assert "passed" in sr.REASONS["proposal"]
    assert set(sr.REASONS) == set(sr.KINDS) == {"live", "test", "proposal"}
    for codes in sr.REASONS.values():
        for code in codes:
            assert re.fullmatch(r"[a-z_]{1,32}", code), code


def test_a_stop_that_is_no_skill_fault_never_makes_a_repeat() -> None:
    """D-F12-13: a cap, a meter fault or the time limit is no failure of
    the skill. The repeat check counts only a live code or a skill fault.
    F4b gives ``run_failed`` to a time limit and a runner exception too, so
    it does not count until F4b gives a time limit its own code. Mutation:
    put ``run_failed`` back, and this fails."""
    assert sr.LIVE_CODES | (sr.SKILL_FAULTS - {"run_failed"}) == sr.REPEAT_REASONS
    for stop in ("cap_reached", "cost_read_failed", "hold_unarmed", "skill_unloaded",
                 "bad_test", "not_run", "passed", "run_failed"):
        assert stop not in sr.REPEAT_REASONS


# ── 2. The input hash ───────────────────────────────────────────────────────


def test_the_input_hash_is_the_org_one_nul_and_the_folded_input() -> None:
    want = hashlib.sha256(ORG.encode() + b"\x00" + INPUT.encode()).hexdigest()
    assert sr.input_hash(ORG, INPUT) == want
    assert sr.input_hash(ORG.upper(), "  Call\tAsha\n on  555 0100 ") == want
    assert sr.input_hash(str(uuid.uuid4()), INPUT) != want
    assert sr.input_hash(ORG, "Call Asha on 555 0101") != want
    assert re.fullmatch(r"[0-9a-f]{64}", want)


def test_fold_space_is_the_one_white_space_rule() -> None:
    assert sr.fold_space("  a \t b\n\nc  ") == "a b c"
    assert sr.fold_space(chr(0xA0) + "a" + chr(0x2003) + "b") == "a b"
    assert sr.fold_space("   ") == ""


@pytest.mark.parametrize("org", ["", "not-a-uuid", None, 12])
def test_the_input_hash_refuses_an_org_that_is_no_uuid(org: Any) -> None:
    with pytest.raises(sr.SkillRunError) as err:
        sr.input_hash(org, INPUT)
    assert err.value.code == "org"


# ── 3. The writes ───────────────────────────────────────────────────────────


def test_check_writes_keeps_names_sorted_with_no_repeat() -> None:
    got = sr.check_writes([{"action": "tool:create_task", "keys": ["title", "due", "title"]}])
    assert got == [{"action": "tool:create_task", "keys": ["due", "title"]}]
    assert sr.check_writes(()) == []


@pytest.mark.parametrize("writes", [
    [{"action": "tool:create_task", "keys": ["title"], "fields": {"title": INPUT}}],
    [{"action": "tool:create_task", "keys": {"title": INPUT}}],
    [{"action": "tool:create_task", "keys": [INPUT]}],
    [{"action": "tool:create_task", "keys": "title"}],
    [{"action": INPUT, "keys": []}],
    [{"action": "tool:create_task"}],
    [{"action": "tool:create_task", "keys": [{"title": "x"}]}],
    [{"action": "tool:create_task", "keys": [1]}],
    [{"action": "Asha", "keys": ["0100"]}],
    [{"action": "555", "keys": []}],
    ["tool:create_task"],
])
def test_check_writes_refuses_an_item_that_holds_a_value(writes: Any) -> None:
    with pytest.raises(sr.SkillRunError) as err:
        sr.check_writes(writes)
    assert err.value.code == "writes_value"
    assert INPUT not in str(err.value)


@pytest.mark.parametrize("writes", [
    "[]", {"action": "x", "keys": []},
    [{"action": f"tool:a{n}", "keys": []} for n in range(11)],
    [{"action": "tool:a", "keys": [f"k{n}" for n in range(33)]}],
])
def test_check_writes_refuses_a_bad_list_or_a_cap(writes: Any) -> None:
    with pytest.raises(sr.SkillRunError) as err:
        sr.check_writes(writes)
    assert err.value.code == "writes"


def test_check_writes_refuses_more_than_4096_bytes() -> None:
    item = {"action": "tool:" + "a" * 75, "keys": [f"k{n:02d}" + "k" * 61 for n in range(5)]}
    big = [dict(item) for _ in range(10)]
    assert sr.writes_bytes(sr.check_writes(big[:1])) < sr.MAX_WRITES_BYTES
    with pytest.raises(sr.SkillRunError) as err:
        sr.check_writes(big)
    assert err.value.code == "writes"


def test_writes_bytes_is_the_jsonb_text_length() -> None:
    items = [{"action": "tool:create_task", "keys": ["due", "title"]}]
    # jsonb writes the keys of an object by length, then ", " and ": ".
    assert sr.writes_bytes(items) == len('[{"keys": ["due", "title"], "action": "tool:create_task"}]')


# ── 3b. Each writer refuses before any statement ────────────────────────────


def _live(**kw: Any) -> dict[str, Any]:
    return {"version": 1, "passed": False, "reason": "tool_error", "body_key": KEY,
            "input_text": INPUT, "run_id": "run-1", **kw}


def _test(**kw: Any) -> dict[str, Any]:
    return {"version": 1, "reason": "missing_write", "body_key": KEY, "test_id": "t1", **kw}


def _proposal(**kw: Any) -> dict[str, Any]:
    return {"passed": False, "reason": "extra_write", "body_key": KEY, **kw}


_LIVE_BAD = [
    ("reason", _live(reason="missing_write")),
    ("reason", _live(reason="cap_reached")),
    ("reason", _live(reason="Tool Error")),
    ("reason", _live(passed=True, reason="tool_error")),
    ("reason", _live(passed=False, reason="passed")),
    ("passed", _live(passed=1)),
    ("run_id", _live(run_id=None)),
    ("run_id", _live(run_id="  ")),
    ("run_id", _live(run_id="x" * 201)),
    ("input", _live(input_text=None)),
    ("input", _live(input_text=" \n ")),
    ("body_key", _live(body_key="AB" * 32)),
    ("body_key", _live(body_key="ab" * 31)),
    ("version", _live(version=0)),
    ("version", _live(version=True)),
    ("version", _live(version="1")),
    ("thread_id", _live(thread_id="t\nx")),
    ("writes_value", _live(writes=[{"action": "tool:a", "keys": ["k"], "value": 1}])),
]


@pytest.mark.parametrize(("code", "kw"), _LIVE_BAD)
async def test_record_live_refuses_a_bad_shape_before_any_statement(code: str, kw: dict) -> None:
    db = _FakeDB()
    with pytest.raises(sr.SkillRunError) as err:
        await sr.record_live(db, ANA, SKILL, **kw)
    assert err.value.code == code
    assert db.calls == []
    assert INPUT not in str(err.value)


_TEST_BAD = [
    ("reason", _test(reason="passed")),
    ("reason", _test(reason="tool_error")),
    ("reason", _test(reason="cap_reached")),
    ("reason", _test(reason="bad_test")),
    ("test_id", _test(test_id=None)),
    ("test_id", _test(test_id="")),
    ("test_id", _test(test_id="t" * 65)),
    ("credits", _test(credits=Decimal("-1"))),
    ("credits", _test(credits=1.5)),
    ("credits", _test(credits=Decimal("NaN"))),
    ("writes", _test(writes=[{"action": f"tool:a{n}", "keys": []} for n in range(11)])),
]


@pytest.mark.parametrize(("code", "kw"), _TEST_BAD)
async def test_record_test_refuses_a_bad_shape_before_any_statement(code: str, kw: dict) -> None:
    db = _FakeDB()
    with pytest.raises(sr.SkillRunError) as err:
        await sr.record_test(db, ANA, SKILL, **kw)
    assert err.value.code == code
    assert db.calls == []


_PROPOSAL_BAD = [
    ("repair_of", REPAIR[:-1], _proposal()),
    ("repair_of", None, _proposal()),
    ("reason", REPAIR, _proposal(reason="tool_error")),
    ("reason", REPAIR, _proposal(passed=True, reason="extra_write")),
    ("credits", REPAIR, _proposal(credits=-1)),
    ("writes_value", REPAIR, _proposal(writes=[{"action": "tool:a", "keys": ["a b"]}])),
]


@pytest.mark.parametrize(("code", "repair_of", "kw"), _PROPOSAL_BAD)
async def test_record_proposal_refuses_a_bad_shape_before_any_statement(
        code: str, repair_of: Any, kw: dict) -> None:
    db = _FakeDB()
    with pytest.raises(sr.SkillRunError) as err:
        await sr.record_proposal(db, ANA, SKILL, repair_of, **kw)
    assert err.value.code == code
    assert db.calls == []


@pytest.mark.parametrize("member", ["", "  ", None, 5])
async def test_each_writer_refuses_a_member_that_is_empty(member: Any) -> None:
    db = _FakeDB()
    for call in (sr.record_live(db, member, SKILL, **_live()),
                 sr.record_test(db, member, SKILL, **_test()),
                 sr.record_proposal(db, member, SKILL, REPAIR, **_proposal())):
        with pytest.raises(sr.SkillRunError) as err:
            await call
        assert err.value.code == "member"
    assert db.calls == []


@pytest.mark.parametrize("reason", sorted(sr.REASONS["proposal"]))
async def test_a_proposal_takes_each_code_of_the_skill_tests(reason: str) -> None:
    db = _FakeDB({"INSERT INTO studio_skill_runs": _Result([_row(kind="proposal")])})
    await sr.record_proposal(db, ANA, SKILL, REPAIR, **_proposal(
        passed=reason == "passed", reason=reason))
    assert len(db.calls) == 1


# ── 4. No input text ────────────────────────────────────────────────────────


async def test_no_input_text_is_kept() -> None:
    """The live writer sends the hash only. The org of the hash is the
    bound org of the session, read in SQL."""
    db = _FakeDB({"INSERT INTO studio_skill_runs": _Result([_row()])})
    await sr.record_live(db, ANA.upper(), SKILL, **_live(
        detail="create_task: ToolError",
        writes=[{"action": "tool:create_task", "keys": ["title"]}]))
    assert [c[0].split(" ", 1)[0] for c in db.calls] == ["SELECT", "INSERT"]
    _, params = db.calls[1]
    assert params["input_hash"] == sr.input_hash(ORG, INPUT)
    assert params["by"] == ANA
    blob = json.dumps(params, default=str)
    for word in ("Asha", "555", "0100", "Call"):
        assert word not in blob, word
    assert "organization_id" not in db.calls[1][0].split("SELECT", 1)[0]


async def test_a_live_row_with_no_bound_tenant_is_refused() -> None:
    db = _FakeDB({"SELECT current_setting": _Result(scalar="")})
    with pytest.raises(sr.SkillRunError) as err:
        await sr.record_live(db, ANA, SKILL, **_live())
    assert err.value.code == "org"
    assert all("INSERT" not in sql for sql, _ in db.calls)


async def test_a_second_failed_live_row_of_one_run_is_a_named_refusal() -> None:
    db = _FakeDB({"SELECT 1 FROM studio_skills": _Result([(1,)])})
    with pytest.raises(sr.DuplicateFailure):
        await sr.record_live(db, ANA, SKILL, **_live())


async def test_a_skill_of_no_row_is_unknown() -> None:
    db = _FakeDB()
    with pytest.raises(sr.UnknownSkill):
        await sr.record_test(db, ANA, SKILL, **_test())


async def test_a_second_proposal_is_already_repaired() -> None:
    db = _FakeDB({"kind = 'proposal' AND repair_of": _Result([_row(kind="proposal")])})
    with pytest.raises(sr.AlreadyRepaired) as err:
        await sr.record_proposal(db, ANA, SKILL, REPAIR, **_proposal())
    assert err.value.code == "already_repaired"


async def test_a_proposal_of_no_failed_row_is_not_repairable() -> None:
    db = _FakeDB()
    with pytest.raises(sr.NotRepairable):
        await sr.record_proposal(db, ANA, SKILL, REPAIR, **_proposal())


async def test_the_history_holds_at_most_ten_rows() -> None:
    db = _FakeDB({"GROUP BY reason": _Result([("tool_error", 3)])})
    got = await sr.history(db, ANA, SKILL, limit=50)
    assert db.calls[0][1]["n"] == sr.HISTORY_ROWS == 10
    assert dict(got.others) == {"tool_error": 3}
    with pytest.raises(sr.SkillRunError):
        await sr.history(db, ANA, SKILL, limit=0)


async def test_the_repeat_check_counts_only_a_skill_failure() -> None:
    db = _FakeDB({"SELECT EXISTS": _Result(scalar=True)})
    assert await sr.repeats_a_failed_body(db, SKILL, KEY) is True
    assert set(db.calls[0][1]["reasons"]) == sr.REPEAT_REASONS


async def test_a_long_detail_is_cut_and_never_refused() -> None:
    """A detail of F4b has no bound. The store cuts it to 300 characters and
    removes each control character, and it writes the row. Mutation: raise
    for a long detail again, and this fails."""
    assert sr.cut_detail("x" * 5000) == "x" * sr.MAX_DETAIL_CHARS
    assert sr.cut_detail("a\x00b\nc\td\x7f" + chr(0xD800) + "e") == "a b c d e"
    assert sr.cut_detail(None) == ""
    for kind in ("live", "test", "proposal"):
        db = _FakeDB({"INSERT INTO studio_skill_runs": _Result([_row(kind=kind)])})
        long = "create_task: " + "K\n" * 4000
        if kind == "live":
            await sr.record_live(db, ANA, SKILL, **_live(detail=long))
        elif kind == "test":
            await sr.record_test(db, ANA, SKILL, **_test(detail=long))
        else:
            await sr.record_proposal(db, ANA, SKILL, REPAIR, **_proposal(detail=long))
        detail = db.calls[-1][1]["detail"]
        assert len(detail) <= sr.MAX_DETAIL_CHARS and "\n" not in detail
        assert detail.startswith("create_task: K K")


def test_a_proposal_names_only_a_failed_row_of_the_same_member() -> None:
    """Defence in depth: the INSERT of a proposal selects the failed row of
    the member only. Mutation: drop the term, and this fails, and so does
    ``test_studio_skill_runs_r8.py``."""
    assert "r.created_by = CAST(:by AS text)" in sr._INSERT_PROPOSAL_SQL


# ── 5. The one module ───────────────────────────────────────────────────────


def test_one_module_runs_sql_on_the_table_and_it_holds_no_update() -> None:
    """Only ``skill_runs.py`` names the table in code, beside the one purge
    clause of ``routes/admin/members.py``. The store holds no UPDATE, no
    org parameter, and no engine of its own."""
    allowed = {_STORE, _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes"
               / "admin" / "members.py"}
    found = set()
    for top in ("apps", "packages"):
        for path in (_ROOT / top).rglob("*.py"):
            if "node_modules" in path.parts or "tests" in path.parts:
                continue
            if "studio_skill_runs" in path.read_text(encoding="utf-8", errors="replace"):
                found.add(path)
    assert found == allowed, sorted(str(p) for p in found - allowed)
    src = _STORE.read_text(encoding="utf-8")
    assert "UPDATE studio_skill_runs" not in src
    assert "create_engine" not in src and "create_async_engine" not in src
    assert "APIRouter" not in src
    for name in dir(sr):
        if name.endswith("_SQL"):
            stmt = getattr(sr, name)
            assert ":org" not in stmt, name
            if "INSERT INTO" in stmt:
                columns = stmt.split("INSERT INTO", 1)[1].split(")", 1)[0]
                assert "organization_id" not in columns, name
                assert "input_text" not in stmt, name
