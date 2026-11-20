"""Skill tests: the shape, the pass rule and the carry-forward (WS-STU F4a).

Spec: ``project-docs/specs/ai_studio_rows/F4.md``. Code:
``gateway/studio/skill_tests.py`` and ``skills_store.add_version``. Hermetic:
no model, no database.

**The mutations (R7).**

* In ``skills_store.add_version``, drop the ``_carry_tests`` call.
  ``test_an_omitted_tests_key_keeps_the_tests`` goes red.
* In ``skill_tests._fits``, make the subset match exact (compare all keys of
  the call). ``test_fields_is_a_subset_match`` goes red.
* In ``skill_tests.evaluate``, skip the ``extra_write`` result.
  ``test_a_write_with_expect_empty_fails`` goes red.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from typing import Any

import pytest
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

SKILL_TOOLS = {"create_lead", "list_leads"}


def _call(tool: str, args: dict[str, Any] | None = None, held: bool = True) -> RecordedCall:
    return RecordedCall(tool=tool, action=f"tool:{tool}", args=args or {}, held=held)


def _test(expect: list[tuple[str, dict[str, Any]]]) -> SkillTest:
    return SkillTest(id="t1", input="Add Ravi from Acme as a lead",
                     expect=tuple(ExpectedWrite(a, f) for a, f in expect))


def _ev(test: SkillTest, calls: list[RecordedCall]) -> tuple[bool, str, str]:
    return evaluate(test, calls, skill_tools=SKILL_TOOLS)


# -- parse_tests ---------------------------------------------------------------


def _good(i: int = 1) -> dict[str, Any]:
    return {"id": f"t{i}", "input": "Add Ravi",
            "expect": [{"action": "tool:create_lead", "fields": {"company": "Acme"}}]}


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
    [{**_good(), "expect": ["x"]}],
    ["x"],
    {"a": 1},
    None,
], ids=["dup_id", "empty_id", "int_id", "blank_input", "expect_not_list", "no_prefix",
        "empty_name", "space_in_name", "fields_list", "nested_dict", "nested_list",
        "blank_key", "expect_item", "test_not_object", "tests_object", "tests_null"])
def test_a_bad_shape_raises_bad_test(tests: Any) -> None:
    with pytest.raises(SkillTestError) as exc:
        parse_tests({"tests": tests})
    assert exc.value.reason == "bad_test"


def test_a_message_names_the_path_and_never_the_value() -> None:
    secret = "sk-very-secret-value"
    bad = {**_good(), "expect": [{"action": "tool:create_lead",
                                   "fields": {"company": {"x": secret}}}]}
    with pytest.raises(SkillTestError) as exc:
        parse_tests({"tests": [bad]})
    assert "tests[0].expect[0].fields" in str(exc.value) and secret not in str(exc.value)


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
        "cap_reached", "call_cap", "cost_read_failed", "hold_unarmed",
        "skill_unloaded", "run_failed", "bad_test", "not_run")


def test_the_pass_rule_has_no_text_parameter() -> None:
    assert set(inspect.signature(evaluate).parameters) == {"test", "calls", "skill_tools"}


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
    # An expectation with no fields must not take the write that another needs.
    t2 = _test([("tool:create_lead", {}), ("tool:create_lead", {"company": "Zeta"})])
    assert _ev(t2, calls)[0]


def test_a_call_to_a_tool_outside_the_skill_fails() -> None:
    ok, reason, detail = _ev(_test([]), [_call("send_email", {"to": "a@b.c"})])
    assert (ok, reason) == (False, "other_action") and "send_email" in detail


def test_a_read_outside_the_skill_fails() -> None:
    ok, reason, _ = _ev(_test([]), [_call("web_search", {"q": "x"}, held=False)])
    assert (ok, reason) == (False, "other_action")


def test_a_read_of_the_skill_is_no_write() -> None:
    assert _ev(_test([]), [_call("list_leads", {}, held=False)])[0]


def test_a_display_tool_is_excepted() -> None:
    from acb_skills.subagent_contract import DISPLAY_TOOLS
    for tool in sorted(DISPLAY_TOOLS):
        assert _ev(_test([]), [_call(tool, {"a": 1})])[0], tool


def test_the_loader_is_no_exception() -> None:
    # The harness loads the forced skill. A model call of the loader fails.
    assert _ev(_test([]), [_call("load_skill_tools", {"id": "x"})])[1] == "other_action"


def test_other_action_comes_before_the_write_reasons() -> None:
    t = _test([("tool:create_lead", {})])
    assert _ev(t, [_call("send_email")])[1] == "other_action"


def test_the_same_calls_give_the_same_result() -> None:
    # No text goes in, so only the calls decide.
    t = _test([("tool:create_lead", {"company": "Acme"})])
    assert _ev(t, [])[1] == "missing_write"
    assert _ev(t, [])[1] == "missing_write"


# -- the carry-forward ----------------------------------------------------------

SID = "00000000-0000-0000-0000-000000000001"
TESTS = [_good(1)]
BASE = {"description": "d", "instructions": "i", "actions": []}


class _Db:
    """A fake session. It answers the two statements of add_version."""

    def __init__(self, newest: dict[str, Any] | None) -> None:
        self.newest = newest
        self.inserted: list[str] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        sql = str(statement)
        if sql.startswith("SELECT v.skill_id"):
            row = None if self.newest is None else (SID, 1, self.newest, "h", "m@x.y", None)
            return SimpleNamespace(first=lambda: row)
        assert params is not None
        self.inserted.append(params["body"])
        return SimpleNamespace(first=lambda: (SID, 2, {}, "h", "m@x.y", None))


def _save(newest: dict[str, Any] | None, body: dict[str, Any], *, carry: bool = True) -> dict[str, Any]:
    db = _Db(newest)

    async def go() -> None:
        sent = await store.carry_tests(db, "m@x.y", SID, body) if carry else body
        await store.add_version(db, "m@x.y", SID, sent)

    asyncio.run(go())
    return json.loads(db.inserted[0])


def test_an_omitted_tests_key_keeps_the_tests() -> None:
    saved = _save({**BASE, "tests": TESTS}, {**BASE, "instructions": "new"})
    assert saved["tests"] == TESTS and saved["instructions"] == "new"


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
