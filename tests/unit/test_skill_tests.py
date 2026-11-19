"""Skill tests: the shape, the pass rule, the carry-forward and the run (WS-STU F4).

Spec: ``project-docs/specs/ai_studio_rows/F4.md``. Code:
``gateway/studio/skill_tests.py`` and ``skills_store.carry_tests``. Hermetic:
no model, no database. ``run_suite`` runs with a fake runner and a fake
meter. The runner drives the real ``CapGuard`` and ``HoldAndRecord``.

**The mutations (R7).**

* In ``skills_store.carry_tests``, drop ``for_update=True``.
  ``test_the_carry_takes_the_row_lock_first`` goes red.
* In ``skill_tests._fits``, make the subset match exact (compare all keys of
  the call). ``test_fields_is_a_subset_match`` goes red.
* In ``skill_tests.evaluate``, skip the ``extra_write`` result.
  ``test_a_write_with_expect_empty_fails`` goes red.
* In ``skill_tests.evaluate``, skip the ``invalid_call`` loop.
  ``test_an_invalid_write_never_matches`` goes red.
* In ``skill_tests.run_suite``, skip the ``cap_unset`` return.
  ``test_cap_unset_runs_nothing_and_reads_no_meter`` goes red.
* In ``CapGuard.process``, call ``call_next`` before the refusal check.
  ``test_a_meter_fault_starts_no_model_call`` goes red.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import get_settings
from acb_common._log import get_run_context
from acb_common.no_person import no_person_present
from acb_llm.attribution import in_run_ceiling
from acb_skills.skill_dry_run import CapGuard, HoldAndRecord, current_dry_run
from agent_framework import FunctionTool
from gateway.studio import skill_tests as st
from gateway.studio import skills_store as store
from gateway.studio.skill_tests import (
    ExpectedWrite,
    RecordedCall,
    SkillTest,
    SkillTestError,
    evaluate,
    parse_tests,
)

ACTIONS = ("tool:create_lead", "tool:list_leads")


def _call(tool: str, args: dict[str, Any] | None = None, held: bool = True,
          **kw: Any) -> RecordedCall:
    return RecordedCall(tool=tool, action=f"tool:{tool}", args=args or {}, held=held, **kw)


def _test(expect: list[tuple[str, dict[str, Any]]] | None) -> SkillTest:
    return SkillTest(id="t1", input="Add Ravi from Acme as a lead",
                     expect=None if expect is None
                     else tuple(ExpectedWrite(a, f) for a, f in expect))


def _ev(test: SkillTest, calls: list[RecordedCall],
        actions: tuple[str, ...] = ACTIONS) -> tuple[bool, str, str]:
    return evaluate(test, calls, skill_actions=actions)


# -- parse_tests ---------------------------------------------------------------


def _good(i: int = 1) -> dict[str, Any]:
    return {"id": f"t{i}", "input": "Add Ravi",
            "expect": [{"action": "tool:create_lead", "fields": {"company": "Acme"}}]}


def _one(expect: dict[str, Any]) -> list[SkillTest]:
    return parse_tests({"tests": [{"id": "t", "input": "x", "expect": [expect]}]})


def test_a_valid_list_parses() -> None:
    out = parse_tests({"tests": [_good(1), {"id": "t2", "input": "Hi", "expect": []}]})
    assert [t.id for t in out] == ["t1", "t2"]
    assert out[0].expect == (ExpectedWrite("tool:create_lead", {"company": "Acme"}),)
    assert out[1].expect == ()


def test_no_tests_key_is_an_empty_list() -> None:
    assert parse_tests({"description": "d"}) == []
    assert parse_tests({"tests": []}) == []


def test_twenty_tests_pass_and_twenty_one_fail() -> None:
    assert len(parse_tests({"tests": [_good(i) for i in range(20)]})) == 20
    with pytest.raises(SkillTestError) as exc:
        parse_tests({"tests": [_good(i) for i in range(21)]})
    assert exc.value.reason == "bad_test"


def test_the_caps_of_one_test() -> None:
    entry = {"action": "tool:create_lead"}
    five = {**_good(), "expect": [entry] * 5}
    assert len(parse_tests({"tests": [five]})[0].expect) == 5
    long = {**_good(), "input": "x" * st.MAX_INPUT_CHARS}
    assert parse_tests({"tests": [long]})[0].input == "x" * 2000
    many = {**_good(), "expect": [{**entry, "fields": {f"k{i}": i for i in range(20)}}]}
    assert len(parse_tests({"tests": [many]})[0].expect[0].fields) == 20
    for bad, path in (({**_good(), "expect": [entry] * 6}, "expect"),
                      ({**_good(), "input": "x" * 2001}, "input"),
                      ({**_good(), "expect": [{**entry, "fields": {f"k{i}": i for i in range(21)}}]},
                       "fields")):
        with pytest.raises(SkillTestError) as exc:
            parse_tests({"tests": [bad]})
        assert path in str(exc.value)
    assert (st.MAX_EXPECT, st.MAX_INPUT_CHARS, st.MAX_FIELDS) == (5, 2000, 20)


@pytest.mark.parametrize("tests", [
    [_good(1), _good(1)],
    [{**_good(), "id": ""}],
    [{**_good(), "id": 5}],
    [{**_good(), "input": " "}],
    [{**_good(), "expect": "x"}],
    [{**_good(), "expect": [{"action": "create_lead"}]}],
    [{**_good(), "expect": [{"action": "tool:"}]}],
    [{**_good(), "expect": [{"action": "tool:a b"}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "fields": ["company"]}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "fields": {"c": {"a": 1}}}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "fields": {"c": [1]}}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "fields": {" ": 1}}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "fields": {"a.b.c": 1}}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "fields": {"a.": 1}}]}],
    [{**_good(), "expect": ["x"]}],
    [{**_good(), "expect": [{"action": "tool:studio_record_create"}]}],
    [{**_good(), "expect": [{"action": "tool:run_workflow"}]}],
    [{**_good(), "expect": [{"action": "tool:create_lead", "object": "lead"}]}],
    [{**_good(), "expect": [{"action": "object:lead.list"}]}],
    [{**_good(), "expect": [{"action": "object:lead"}]}],
    [{**_good(), "expect": [{"action": "workflow:"}]}],
    [{**_good(), "expect": [{"action": "workflow:w1", "package": "p"}]}],
    [{**_good(), "expect": [{"action": "object:lead.create", "object": "deal"}]}],
    ["x"],
    {"a": 1},
    None,
], ids=["dup_id", "empty_id", "int_id", "blank_input", "expect_not_list", "no_prefix",
        "empty_name", "space_in_name", "fields_list", "nested_dict", "nested_list",
        "blank_key", "two_dots", "trailing_dot", "expect_item", "record_no_object",
        "workflow_no_id", "pin_on_plain_tool", "object_read", "object_no_verb",
        "workflow_empty", "package_on_workflow", "double_object", "test_not_object",
        "tests_object", "tests_null"])
def test_a_bad_shape_raises_bad_test(tests: Any) -> None:
    with pytest.raises(SkillTestError) as exc:
        parse_tests({"tests": tests})
    assert exc.value.reason == "bad_test"


def test_the_pins_name_the_catalog_action() -> None:
    assert _one({"action": "tool:studio_record_create", "object": "lead", "package": "p1",
                 "fields": {"data.company": "Acme"}})[0].expect == (
        ExpectedWrite("object:lead.create", {"data.company": "Acme"}, "p1"),)
    assert _one({"action": "tool:run_workflow", "workflow": "w-1"})[0].expect == (
        ExpectedWrite("workflow:w-1", {}),)
    assert _one({"action": "object:deal.update"})[0].expect[0].action == "object:deal.update"


def test_a_message_names_the_path_and_never_the_value() -> None:
    secret = "sk-very-secret-value"
    for bad in ({"action": "tool:create_lead", "fields": {"company": {"x": secret}}},
                {"action": "tool:studio_record_create", "object": secret + "!"},
                {"action": "workflow:" + secret + " x"}):
        with pytest.raises(SkillTestError) as exc:
            parse_tests({"tests": [{**_good(), "expect": [bad]}]})
        assert "tests[0].expect[0]" in str(exc.value) and secret not in str(exc.value)


def test_the_store_check_body_refuses_a_bad_tests_list() -> None:
    body = {"description": "d", "instructions": "i", "tests": [{"id": "t1"}]}
    with pytest.raises(store.SkillStoreError) as exc:
        store.check_body(body)
    assert "body.tests[0]" in str(exc.value)
    store.check_body({**body, "tests": [_good()]})


# -- evaluate ------------------------------------------------------------------


def test_the_reason_codes_are_the_agreed_set() -> None:
    assert st.REASONS == (
        "passed", "other_action", "missing_write", "extra_write", "field_mismatch",
        "invalid_call", "cap_reached", "cap_unset", "call_cap", "cost_read_failed",
        "hold_unarmed", "skill_unloaded", "run_failed", "bad_test", "not_run")


def test_the_pass_rule_has_no_text_parameter() -> None:
    assert set(inspect.signature(evaluate).parameters) == {"test", "calls", "skill_actions"}


def test_a_matching_write_passes() -> None:
    ok, reason, _ = _ev(_test([("tool:create_lead", {"company": "Acme"})]),
                        [_call("create_lead", {"company": "Acme", "name": "Ravi"})])
    assert (ok, reason) == (True, "passed")


def test_fields_is_a_subset_match() -> None:
    t = _test([("tool:create_lead", {"company": "Acme"})])
    assert _ev(t, [_call("create_lead", {"company": "Acme", "name": "Ravi", "x": 1})])[0]
    assert _ev(_test([("tool:create_lead", {})]), [_call("create_lead", {"q": 1})])[0]


def test_equality_is_after_a_trim_with_no_case_folding() -> None:
    t = _test([("tool:create_lead", {"company": "  Acme "})])
    assert _ev(t, [_call("create_lead", {"company": "Acme  "})])[0]
    ok, reason, _ = _ev(t, [_call("create_lead", {"company": "acme"})])
    assert (ok, reason) == (False, "field_mismatch")


def test_a_type_that_differs_is_a_mismatch() -> None:
    t = _test([("tool:create_lead", {"n": 1})])
    assert not _ev(t, [_call("create_lead", {"n": True})])[0]
    assert not _ev(t, [_call("create_lead", {"n": "1"})])[0]
    assert _ev(t, [_call("create_lead", {"n": 1})])[0]


def test_field_mismatch_names_the_key_and_never_the_value() -> None:
    t = _test([("tool:create_lead", {"company": "Acme", "name": "Ravi"})])
    ok, reason, detail = _ev(t, [_call("create_lead", {"company": "Zeta-Corp", "name": "Ravi"})])
    assert (ok, reason) == (False, "field_mismatch")
    assert "company" in detail and "Zeta-Corp" not in detail and "Acme" not in detail
    assert "name" not in detail
    _, reason, detail = _ev(t, [_call("create_lead", {"name": "Ravi"})])
    assert reason == "field_mismatch" and "company" in detail


def test_a_write_with_expect_empty_fails() -> None:
    ok, reason, _ = _ev(_test([]), [_call("create_lead", {"company": "Acme"})])
    assert (ok, reason) == (False, "extra_write")
    assert _ev(_test([]), [])[:2] == (True, "passed")


def test_a_missing_write_fails() -> None:
    ok, reason, detail = _ev(_test([("tool:create_lead", {})]), [])
    assert (ok, reason) == (False, "missing_write") and "tool:create_lead" in detail


def test_an_extra_write_fails() -> None:
    t = _test([("tool:create_lead", {"company": "Acme"})])
    ok, reason, _ = _ev(t, [_call("create_lead", {"company": "Acme"}),
                            _call("create_lead", {"company": "Acme"})])
    assert (ok, reason) == (False, "extra_write")


def test_the_match_is_one_to_one_in_any_order() -> None:
    t = _test([("tool:create_lead", {"company": "Acme"}),
               ("tool:create_lead", {"company": "Zeta"})])
    calls = [_call("create_lead", {"company": "Zeta"}), _call("create_lead", {"company": "Acme"})]
    assert _ev(t, calls)[0]
    t2 = _test([("tool:create_lead", {}), ("tool:create_lead", {"company": "Zeta"})])
    assert _ev(t2, calls)[0]


def test_a_call_to_a_tool_outside_the_skill_fails() -> None:
    ok, reason, detail = _ev(_test([]), [_call("send_email", {"to": "a@b.c"})])
    assert (ok, reason) == (False, "other_action") and "send_email" in detail
    assert "a@b.c" not in detail


def test_a_read_outside_the_skill_fails() -> None:
    ok, reason, _ = _ev(_test([]), [_call("web_search", {"q": "x"}, held=False)])
    assert (ok, reason) == (False, "other_action")


def test_a_read_of_the_skill_is_no_write() -> None:
    assert _ev(_test([]), [_call("list_leads", {}, held=False)])[0]
    assert _ev(_test([]), [_call("list_leads", {}, held=True, write=False)])[0]


def test_a_display_tool_is_excepted() -> None:
    from acb_skills.subagent_contract import DISPLAY_TOOLS
    for tool in sorted(DISPLAY_TOOLS):
        assert _ev(_test([]), [_call(tool, {"a": 1})])[0], tool


def test_the_loader_is_no_exception() -> None:
    assert _ev(_test([]), [_call("load_skill_tools", {"id": "x"})])[1] == "other_action"


def test_other_action_comes_before_the_write_reasons() -> None:
    t = _test([("tool:create_lead", {})])
    assert _ev(t, [_call("send_email")])[1] == "other_action"


def test_the_same_calls_give_the_same_result() -> None:
    t = _test([("tool:create_lead", {"company": "Acme"})])
    assert _ev(t, [])[1] == "missing_write"
    assert _ev(t, [])[1] == "missing_write"


# -- F4b: the pinned action ------------------------------------------------------

RECORD = ("object:lead.create", "workflow:wf-1")


def _record(obj: str, data: dict[str, Any], **extra: Any) -> RecordedCall:
    return _call("studio_record_create", {"package_id": "p1", "object": obj, "data": data, **extra})


def test_a_record_write_with_a_wrong_nested_field_is_a_mismatch() -> None:
    t = _test([("object:lead.create", {"data.company": "Acme"})])
    assert _ev(t, [_record("lead", {"company": "Acme"})], RECORD)[:2] == (True, "passed")
    ok, reason, detail = _ev(t, [_record("lead", {"company": "Zeta-Corp"})], RECORD)
    assert (ok, reason) == (False, "field_mismatch")
    assert "data.company" in detail and "Zeta-Corp" not in detail


def test_a_record_write_of_another_object_is_other_action() -> None:
    t = _test([("object:lead.create", {"data.company": "Acme"})])
    ok, reason, detail = _ev(t, [_record("deal", {"company": "Acme"})], RECORD)
    assert (ok, reason) == (False, "other_action") and "deal" not in detail


def test_a_record_tool_with_no_object_is_other_action() -> None:
    call = _call("studio_record_create", {"data": {"company": "Acme"}})
    assert _ev(_test([]), [call], RECORD)[1] == "other_action"


def test_a_workflow_of_another_id_is_other_action() -> None:
    t = _test([("workflow:wf-1", {})])
    assert _ev(t, [_call("run_workflow", {"workflow": "wf-1"})], RECORD)[0]
    ok, reason, detail = _ev(t, [_call("run_workflow", {"workflow": "secret-wf"})], RECORD)
    assert (ok, reason) == (False, "other_action") and "secret-wf" not in detail


def test_a_pinned_package_must_match() -> None:
    want = (ExpectedWrite("object:lead.create", {}, "p1"),)
    t = SkillTest(id="t", input="x", expect=want)
    assert _ev(t, [_record("lead", {})], RECORD)[0]
    ok, reason, detail = _ev(t, [_call("studio_record_create", {
        "package_id": "p2", "object": "lead", "data": {}})], RECORD)
    assert (ok, reason) == (False, "field_mismatch") and "package" in detail


def test_an_invalid_write_never_matches() -> None:
    t = _test([("tool:create_lead", {"company": "Acme"})])
    bad = _call("create_lead", {"company": "Acme"}, valid=False, invalid_keys=("owner",))
    ok, reason, detail = _ev(t, [bad])
    assert (ok, reason) == (False, "invalid_call") and "owner" in detail and "Acme" not in detail


def test_an_input_only_run_counts_the_invariants_only() -> None:
    t = _test(None)
    assert _ev(t, [_call("create_lead", {"company": "Acme"})])[:2] == (True, "passed")
    assert _ev(t, [_call("send_email")])[1] == "other_action"
    assert _ev(t, [_call("create_lead", valid=False)])[1] == "invalid_call"


def test_a_door_op_maps_through_the_one_map(monkeypatch) -> None:
    monkeypatch.setattr(st, "action_tool", lambda a: "read_page" if a == "kb.read" else None)
    assert _ev(_test([]), [_call("read_page", held=False)], ("kb.read",))[0]


# -- the carry-forward ----------------------------------------------------------

SID = "00000000-0000-0000-0000-000000000001"
TESTS = [_good(1)]
BASE = {"description": "d", "instructions": "i", "actions": []}
_NOW = datetime(2026, 11, 19, tzinfo=UTC)


class _Db:
    """A fake session. It answers the locked read, the version read and the
    insert of add_version, and it logs each statement in order."""

    def __init__(self, newest: dict[str, Any] | None, exists: bool = True) -> None:
        self.newest = newest
        self.exists = exists
        self.inserted: list[str] = []
        self.log: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        if sql in (store._GET_SQL, store._GET_SQL + " FOR UPDATE"):
            self.log.append("lock" if sql.endswith("FOR UPDATE") else "read")
            row = (SID, "n", "s", "", "m@x.y", "private", "draft", 1, _NOW, _NOW)
            return SimpleNamespace(first=lambda: row if self.exists else None)
        if sql == store._GET_VERSION_SQL:
            self.log.append("version")
            row = None if self.newest is None else (SID, 1, self.newest, "h", "m@x.y", None)
            return SimpleNamespace(first=lambda: row)
        assert params is not None
        self.log.append("insert")
        self.inserted.append(params["body"])
        return SimpleNamespace(first=lambda: (SID, 2, {}, "h", "m@x.y", None))


def _save(newest: dict[str, Any] | None, body: dict[str, Any], *, carry: bool = True,
          db: _Db | None = None) -> dict[str, Any]:
    db = db or _Db(newest)

    async def go() -> None:
        sent = await store.carry_tests(db, "m@x.y", SID, body) if carry else body
        await store.add_version(db, "m@x.y", SID, sent)

    asyncio.run(go())
    return json.loads(db.inserted[0])


def test_an_omitted_tests_key_keeps_the_tests() -> None:
    saved = _save({**BASE, "tests": TESTS}, {**BASE, "instructions": "new"})
    assert saved["tests"] == TESTS and saved["instructions"] == "new"


def test_the_carry_takes_the_row_lock_first() -> None:
    for body in ({**BASE}, {**BASE, "tests": []}):
        db = _Db({**BASE, "tests": TESTS})
        _save(None, body, db=db)
        assert db.log[0] == "lock", db.log


def test_an_explicit_empty_list_clears_the_tests() -> None:
    assert _save({**BASE, "tests": TESTS}, {**BASE, "tests": []})["tests"] == []


def test_a_new_list_replaces_the_tests() -> None:
    new = [{"id": "z", "input": "Hi", "expect": []}]
    assert _save({**BASE, "tests": TESTS}, {**BASE, "tests": new})["tests"] == new


def test_a_first_version_or_one_with_no_tests_gains_none() -> None:
    assert "tests" not in _save(None, BASE)
    assert "tests" not in _save({**BASE}, BASE)
    assert "tests" not in _save({**BASE, "tests": []}, BASE)


def test_a_stored_list_of_an_old_shape_is_dropped_never_copied() -> None:
    assert "tests" not in _save({**BASE, "tests": [1, 2]}, BASE)


def test_add_version_alone_copies_the_body_exactly() -> None:
    """The undo route copies an older body through add_version. It must not
    gain the tests of the newest version."""
    assert "tests" not in _save({**BASE, "tests": TESTS}, BASE, carry=False)


def test_only_the_route_of_a_new_version_carries_the_tests() -> None:
    from pathlib import Path

    from gateway.routes.studio import skills

    src = Path(skills.__file__).read_text(encoding="utf-8")
    new_version = src[src.index("async def add_skill_version"): src.index("async def undo_skill_version")]
    undo = src[src.index("async def undo_skill_version"): src.index('@router.patch("/skills/{skill_id}/state")')]
    assert "store.carry_tests(" in new_version
    assert "carry_tests" not in undo


# -- F4b: run_suite with a fake runner and a fake meter ---------------------------

ORG = "11111111-1111-4111-8111-111111111111"
MEMBER = "ana@example.test"
RUN_BODY = {"description": "Use it for leads.", "instructions": "Add the lead.",
            "actions": ["object:lead.create", "object:lead.list"]}


def _skill_row(version: int = 2) -> store.Skill:
    return store.Skill(id=SID, name="Leads", slug="leads", description="", owner_email=MEMBER,
                       visibility="private", state="draft", current_version=version,
                       created_at=_NOW, updated_at=_NOW)


def _version_row(body: dict[str, Any], version: int = 2) -> store.Version:
    return store.Version(skill_id=SID, version=version, body=body, content_hash="h",
                         created_by=MEMBER, created_at=_NOW)


@pytest.fixture
def stored(monkeypatch):
    """The stored skill that ``_load_skill`` gives, and the cap setting."""
    world = SimpleNamespace(body={**RUN_BODY, "tests": [
        {"id": "t1", "input": "Add Ravi from Acme",
         "expect": [{"action": "object:lead.create", "fields": {"data.company": "Acme"}}]}]})

    async def _load(org: str, member: str, skill_id: str, version: int | None) -> Any:
        assert (org, member) == (ORG, MEMBER)
        return _skill_row(), _version_row(world.body)

    monkeypatch.setattr(st, "_load_skill", _load)
    monkeypatch.setattr(get_settings(), "studio_skill_tests_cap_credits", "5", raising=False)
    return world


def _record_tool() -> FunctionTool:
    async def studio_record_create(package_id: str = "", object: str = "",
                                   data: dict[str, Any] | None = None) -> str:
        raise AssertionError("a held write ran")

    return FunctionTool(func=studio_record_create, name="studio_record_create", description="d")


class _Runner:
    """A fake turn. It arms and loads the record as the attach would, sends
    *requests* model calls through the real ``CapGuard``, and sends each
    call of *calls* through the real ``HoldAndRecord``."""

    def __init__(self, requests: int = 1, calls: list[tuple[Any, dict[str, Any]]] | None = None,
                 *, arm: bool = True, load: bool = True) -> None:
        self.requests_wanted = requests
        self.calls = calls if calls is not None else [(_record_tool(), {
            "package_id": "p1", "object": "lead", "data": {"company": "Acme"}})]
        self.arm, self.load = arm, load
        self.requests = 0
        self.seen: list[dict[str, Any]] = []

    async def _send(self) -> None:
        self.requests += 1

    async def _send_tool(self) -> None:
        raise AssertionError("a held call ran")

    async def __call__(self, message: str, *, run_id: str, thread_id: str, org: str,
                       member: str) -> None:
        record = current_dry_run()
        ctx = get_run_context()
        self.seen.append({"message": message, "run_id": run_id, "thread_id": thread_id,
                          "org": org, "member": member, "marked": no_person_present(),
                          "dry": record is not None, "ceiling": in_run_ceiling(),
                          "user": ctx.get("user"), "verified": ctx.get("member_verified")})
        assert record is not None
        record.armed = self.arm
        record.loaded = self.load
        for i in range(self.requests_wanted):
            await CapGuard().process(SimpleNamespace(), self._send)
            if i == 0:
                for function, args in self.calls:
                    ctx_call = SimpleNamespace(result=None, function=function, arguments=args)
                    await HoldAndRecord().process(ctx_call, self._send_tool)


class _Meter:
    def __init__(self, values: list[Any]) -> None:
        self.values = values
        self.reads: list[str] = []

    async def __call__(self, org: str, member: str, run_id: str) -> Any:
        self.reads.append(run_id)
        value = self.values[min(len(self.reads) - 1, len(self.values) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


def _run(runner: Any, meter: Any, **kw: Any) -> st.SuiteResult:
    return asyncio.run(st.run_suite(org=ORG, member=MEMBER, skill_id=SID, runner=runner,
                                    meter=meter, **kw))


def test_cap_unset_runs_nothing_and_reads_no_meter(stored, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_skill_tests_cap_credits", "", raising=False)
    runner, meter = _Runner(), _Meter([Decimal(0)])
    result = _run(runner, meter)
    assert runner.seen == [] and meter.reads == [] and runner.requests == 0
    assert result.stopped == "cap_unset" and [r.reason for r in result.results] == ["cap_unset"]
    assert result.passed is False and result.cap is None


@pytest.mark.parametrize("raw", ["0", "-1", "NaN", "Infinity", "five", " "])
def test_a_bad_cap_setting_reads_as_unset(stored, monkeypatch, raw) -> None:
    monkeypatch.setattr(get_settings(), "studio_skill_tests_cap_credits", raw, raising=False)
    runner = _Runner()
    assert _run(runner, _Meter([Decimal(0)])).stopped == "cap_unset" and runner.seen == []


def test_a_passing_run_holds_the_write_and_passes(stored) -> None:
    runner, meter = _Runner(requests=2), _Meter([Decimal("0.5")])
    result = _run(runner, meter)
    only = result.results[0]
    assert (only.passed, only.reason, only.model_calls) == (True, "passed", 2)
    assert only.writes == ("object:lead.create",) and only.credits == Decimal("0.5")
    assert result.passed is True and result.credits == Decimal("0.5") and result.cap == Decimal(5)
    assert result.version == 2 and result.skill_id == SID


def test_the_run_binds_its_scopes(stored) -> None:
    runner = _Runner()
    _run(runner, _Meter([Decimal(0)]))
    seen = runner.seen[0]
    assert seen["marked"] is True and seen["dry"] is True and seen["ceiling"] is True
    assert (seen["user"], seen["verified"]) == (MEMBER, "1")
    assert seen["thread_id"].startswith("skilltest:") and seen["org"] == ORG
    assert current_dry_run() is None and no_person_present() is False


def test_the_meter_reads_as_the_proven_member_after_a_run_that_clears_its_context(
        stored) -> None:
    """The stream run clears the run context as it ends. The read after the
    run must still see the proven member, or ``console_meter`` refuses each
    run. Mutation: await the runner on this task (no own task) → the last
    read sees no member → red."""
    from acb_common._log import clear_run_context

    seen: list[tuple[Any, Any]] = []

    class _Clearing(_Runner):
        async def __call__(self, message: str, **kw: Any) -> None:
            await super().__call__(message, **kw)
            clear_run_context()

    async def meter(org: str, member: str, run_id: str) -> Decimal:
        ctx = get_run_context()
        seen.append((ctx.get("user"), ctx.get("member_verified")))
        return Decimal(0)

    result = _run(_Clearing(), meter)
    assert result.results[0].reason == "passed"
    assert seen and all(entry == (MEMBER, "1") for entry in seen), seen


def test_the_meter_at_the_cap_after_call_one_stops_the_run(stored) -> None:
    runner, meter = _Runner(requests=4), _Meter([Decimal(0), Decimal(5)])
    result = _run(runner, meter)
    assert runner.requests == 1
    assert result.results[0].reason == "cap_reached" and result.stopped == "cap_reached"
    assert result.results[0].passed is False


def test_a_meter_fault_starts_no_model_call(stored) -> None:
    runner, meter = _Runner(requests=3), _Meter([RuntimeError("down")])
    result = _run(runner, meter)
    assert runner.requests == 0 and result.results[0].model_calls == 0
    assert result.results[0].reason == "cost_read_failed" and result.stopped == "cost_read_failed"


def test_the_model_call_cap(stored) -> None:
    runner = _Runner(requests=20)
    result = _run(runner, _Meter([Decimal(0)]))
    assert runner.requests == 8 and result.results[0].reason == "call_cap"


def test_a_request_cap_can_only_lower_the_setting(stored) -> None:
    assert _run(_Runner(), _Meter([Decimal(0)]), cap=Decimal(50)).cap == Decimal(5)
    assert _run(_Runner(), _Meter([Decimal(0)]), cap=Decimal(2)).cap == Decimal(2)
    with pytest.raises(ValueError):
        _run(_Runner(), _Meter([Decimal(0)]), cap=Decimal(0))


def test_a_spent_suite_runs_no_more_tests(stored) -> None:
    stored.body = {**RUN_BODY, "tests": [
        {"id": f"t{i}", "input": "x", "expect": []} for i in range(3)]}
    runner = _Runner(calls=[])
    result = _run(runner, _Meter([Decimal(3)]))  # 3 spent, then 3 + 3 is over 5
    assert [r.reason for r in result.results] == ["passed", "cap_reached", "not_run"]
    assert result.stopped == "cap_reached" and len(runner.seen) == 2
    runner = _Runner(calls=[])
    result = _run(runner, _Meter([Decimal(0), Decimal(5)]))  # the first test spent it all
    assert [r.reason for r in result.results] == ["passed", "not_run", "not_run"]
    assert result.stopped == "cap_reached" and len(runner.seen) == 1


def test_tests_beyond_five_are_not_run(stored) -> None:
    stored.body = {**RUN_BODY, "tests": [
        {"id": f"t{i}", "input": "x", "expect": []} for i in range(7)]}
    runner = _Runner(calls=[])
    result = _run(runner, _Meter([Decimal(0)]))
    assert [r.reason for r in result.results] == ["passed"] * 5 + ["not_run"] * 2
    assert len(runner.seen) == 5 and result.passed is False


def test_tests_of_an_earlier_version_cannot_be_weakened(stored) -> None:
    stored.body = {**RUN_BODY}  # the repair dropped the tests
    v1 = [{"id": "v1", "input": "Add Ravi from Acme",
           "expect": [{"action": "object:lead.create", "fields": {"data.company": "Acme"}}]}]
    runner = _Runner(calls=[])
    result = _run(runner, _Meter([Decimal(0)]), body={**RUN_BODY}, tests=v1)
    assert [r.test_id for r in result.results] == ["v1"]
    assert result.results[0].reason == "missing_write" and runner.seen[0]["message"] == v1[0]["input"]


def test_an_input_only_run(stored) -> None:
    stored.body = {**RUN_BODY}
    result = _run(_Runner(), _Meter([Decimal(0)]), inputs=("Add Ravi",))
    assert [(r.test_id, r.reason) for r in result.results] == [("input-1", "passed")]
    with pytest.raises(SkillTestError):
        _run(_Runner(), _Meter([Decimal(0)]), inputs=("x" * 2001,))


def test_an_unarmed_or_unloaded_run_fails_closed(stored) -> None:
    assert _run(_Runner(arm=False), _Meter([Decimal(0)])).results[0].reason == "hold_unarmed"
    assert _run(_Runner(load=False, requests=0), _Meter([Decimal(0)])).results[0].reason == \
        "skill_unloaded"


def test_a_skill_that_does_not_load_runs_nothing(stored) -> None:
    stored.body = {**stored.body, "actions": ["no.such_op"]}
    runner = _Runner()
    result = _run(runner, _Meter([Decimal(0)]))
    assert runner.seen == [] and result.stopped == "skill_unloaded"
    assert result.results[0].reason == "skill_unloaded"


def test_a_result_holds_no_argument_value(stored) -> None:
    secret = "sk-secret-in-a-write"
    runner = _Runner(calls=[(_record_tool(), {
        "package_id": "p1", "object": "lead", "data": {"company": secret}})])
    result = _run(runner, _Meter([Decimal(0)]))
    assert result.results[0].reason == "field_mismatch"
    assert secret not in repr(result)


def test_a_write_of_another_object_shows_its_tool_never_its_argument(stored) -> None:
    runner = _Runner(calls=[(_record_tool(), {
        "package_id": "p1", "object": "secret_obj", "data": {}})])
    result = _run(runner, _Meter([Decimal(0)]))
    assert result.results[0].reason == "other_action"
    assert "secret_obj" not in repr(result) and result.results[0].calls == (
        "tool:studio_record_create",)


def test_an_unknown_skill_raises_lookup(monkeypatch) -> None:
    async def _none(*_a: Any) -> Any:
        return None, None

    monkeypatch.setattr(st, "_load_skill", _none)
    with pytest.raises(LookupError):
        _run(_Runner(), _Meter([Decimal(0)]))


def test_the_runner_names_no_pack_flag() -> None:
    """``test_tool_packs_registry.py`` allows one reader of the pack flag."""
    from pathlib import Path

    src = Path(st.__file__).read_text(encoding="utf-8")
    for word in ("packs_on(", "TOOL_PACKS", "tool_packs_orgs"):
        assert word not in src, word
