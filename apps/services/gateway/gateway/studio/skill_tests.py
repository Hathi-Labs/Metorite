"""Skill tests: the shape of a test and the pass rule (WS-STU F4a).

Spec: ``project-docs/specs/ai_studio_rows/F4.md``, ``ai_studio.md`` §7. A
saved skill holds tests in its version body, as ``body.tests``, at most
:data:`MAX_TESTS`::

    {"id": "t1", "input": "Add Ravi from Acme as a lead",
     "expect": [{"action": "tool:create_lead", "fields": {"company": "Acme"}}]}

``expect: []`` means that the turn writes nothing.

**The pass rule reads the end state, never the words.** :func:`evaluate`
takes the calls that the dry-run door recorded
(``acb_skills.skill_dry_run``). It has no text parameter, so the answer of
the model cannot change a result. A test passes only when both hold:

1. Each call is a tool of the skill's actions. A display tool is the one
   exception. A call to any other tool, a read too,
   gives ``other_action``.
2. The held writes match ``expect`` one to one. ``fields`` is a subset
   match. Each named key must equal the argument after a trim of a string,
   with no case folding. A key that ``fields`` leaves out is ignored.

Then the reason is ``missing_write``, ``extra_write`` or ``field_mismatch``.
A detail names a key or a tool, and never a value. The `writes` and `calls`
of a result do hold the recorded arguments, so a store keeps codes and key names only.

F4a builds the shape and the rule. F4b builds ``run_suite``, which runs a
test on a real turn, and the credit cap and the flag with it. The reason
codes below are the whole vocabulary, and TOF-3a uses the same set.

Fence: ``tests/unit/test_skill_tests.py``.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from acb_skills.skill_dry_run import RecordedCall
from acb_skills.subagent_contract import DISPLAY_TOOLS

__all__ = [
    "MAX_TESTS", "REASONS", "ExpectedWrite", "RecordedCall", "SkillTest",
    "SkillTestError", "SuiteResult", "TestResult", "evaluate", "parse_tests",
]

#: The most tests in one version body.
MAX_TESTS = 20

#: The reason codes of a result. ``passed`` is the one good code. F4a builds
#: the first five, and F4b the rest.
REASONS = (
    "passed", "other_action", "missing_write", "extra_write", "field_mismatch",
    "cap_reached", "call_cap", "cost_read_failed", "hold_unarmed",
    "skill_unloaded", "run_failed", "bad_test", "not_run",
)

_ACTION = re.compile(r"tool:[A-Za-z_][A-Za-z0-9_]*")
_MAX_ID = 64


class SkillTestError(ValueError):
    """A test list that has the wrong shape. The reason is ``bad_test``. The
    message names a path, never a value."""

    reason = "bad_test"


@dataclass(frozen=True)
class ExpectedWrite:
    """One write that a test expects: ``tool:<name>`` and a subset of its
    arguments."""

    action: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class SkillTest:
    """One test: a prompt, and the writes that the turn must hold."""

    id: str
    input: str
    expect: tuple[ExpectedWrite, ...]


@dataclass(frozen=True)
class TestResult:
    """The result of one test. *passed* is None for a test that did not run."""

    __test__ = False  # pytest: this is a result, never a test class

    test_id: str | None
    passed: bool | None
    reason: str
    detail: str = ""
    writes: tuple[RecordedCall, ...] = ()
    calls: tuple[RecordedCall, ...] = ()
    credits: Decimal = Decimal(0)
    model_calls: int = 0
    run_id: str | None = None


@dataclass(frozen=True)
class SuiteResult:
    """The result of a suite. *stopped* names the cap that ended it early."""

    skill_id: str
    version: int
    content_hash: str
    results: tuple[TestResult, ...] = ()
    passed: bool = False
    credits: Decimal = Decimal(0)
    cap: Decimal | None = None
    stopped: str | None = None


def _bad(path: str, why: str) -> SkillTestError:
    return SkillTestError(f"{path} {why}")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _parse_expect(path: str, item: Any) -> ExpectedWrite:
    if not isinstance(item, Mapping):
        raise _bad(path, "must be an object")
    action = item.get("action")
    if not isinstance(action, str) or not _ACTION.fullmatch(action):
        raise _bad(f"{path}.action", "must be tool:<name>")
    fields = item.get("fields", {})
    if not isinstance(fields, Mapping):
        raise _bad(f"{path}.fields", "must be an object")
    for key, value in fields.items():
        if not isinstance(key, str) or not key.strip():
            raise _bad(f"{path}.fields", "must name each field with a non-empty string")
        if not _is_scalar(value):
            raise _bad(f"{path}.fields", "must hold only text, numbers, true, false or null")
    return ExpectedWrite(action=action, fields=dict(fields))


def parse_tests(body: Any) -> list[SkillTest]:
    """The tests of a version *body*. An absent ``tests`` gives an empty list.

    Raises :class:`SkillTestError` (reason ``bad_test``) when ``tests`` is no
    list, holds more than :data:`MAX_TESTS`, repeats an id, or holds a test
    or an ``expect`` entry of the wrong shape. Extra keys on a test are
    ignored. The message names the path and never a value.
    """
    if not isinstance(body, Mapping) or "tests" not in body:
        return []
    raw = body["tests"]
    if not isinstance(raw, list):
        raise _bad("body.tests", "must be a list")
    if len(raw) > MAX_TESTS:
        raise _bad("body.tests", f"holds at most {MAX_TESTS} tests")
    seen: set[str] = set()
    out: list[SkillTest] = []
    for i, item in enumerate(raw):
        path = f"body.tests[{i}]"
        if not isinstance(item, Mapping):
            raise _bad(path, "must be an object")
        test_id = item.get("id")
        if not isinstance(test_id, str) or not test_id.strip() or len(test_id) > _MAX_ID:
            raise _bad(f"{path}.id", f"must be a non-empty string of at most {_MAX_ID} characters")
        if test_id in seen:
            raise _bad(f"{path}.id", "repeats the id of an earlier test")
        seen.add(test_id)
        prompt = item.get("input")
        if not isinstance(prompt, str) or not prompt.strip():
            raise _bad(f"{path}.input", "must be a non-empty string")
        expect = item.get("expect")
        if not isinstance(expect, list):
            raise _bad(f"{path}.expect", "must be a list")
        out.append(SkillTest(
            id=test_id, input=prompt,
            expect=tuple(_parse_expect(f"{path}.expect[{j}]", e)
                         for j, e in enumerate(expect))))
    return out


def _same(expected: Any, actual: Any) -> bool:
    """Equal after a trim of a string. A type that differs is not equal, so
    ``True`` is no ``1`` and ``"5"`` is no ``5``. No case folding."""
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    return type(expected) is type(actual) and expected == actual


def _bad_keys(want: ExpectedWrite, call: RecordedCall) -> list[str]:
    """The keys of *want* that *call* does not match. Sorted."""
    return sorted(k for k, v in want.fields.items()
                  if k not in call.args or not _same(v, call.args[k]))


def _fits(want: ExpectedWrite, call: RecordedCall) -> bool:
    return want.action == call.action and not _bad_keys(want, call)


def _match(expect: Sequence[ExpectedWrite],
           writes: Sequence[RecordedCall]) -> dict[int, int]:
    """A largest one-to-one match of expectations to writes (augmenting
    paths). Answers ``{write index: expectation index}``."""
    owner: dict[int, int] = {}

    def place(e: int, seen: set[int]) -> bool:
        for w, call in enumerate(writes):
            if w in seen or not _fits(expect[e], call):
                continue
            seen.add(w)
            if w not in owner or place(owner[w], seen):
                owner[w] = e
                return True
        return False

    for e in range(len(expect)):
        place(e, set())
    return owner


def evaluate(test: SkillTest, calls: Sequence[RecordedCall], *,
             skill_tools: Collection[str]) -> tuple[bool, str, str]:
    """Judge one test by the calls of its run. Answers ``(passed, reason, detail)``.

    *calls* is the record of the dry-run door. *skill_tools* holds the names
    of the tools of the skill's actions. There is no text parameter: the
    answer of the model never decides a result.
    """
    for call in calls:
        if call.tool in DISPLAY_TOOLS:
            continue
        if call.tool not in skill_tools:
            return False, "other_action", f"The turn called {call.tool}, which is no action of the skill."
    writes = [c for c in calls if c.held and c.tool not in DISPLAY_TOOLS]
    owner = _match(test.expect, writes)
    left_writes = [w for w in range(len(writes)) if w not in owner]
    matched = set(owner.values())
    left_expect = [e for e in range(len(test.expect)) if e not in matched]

    mismatch: list[str] = []
    for e in list(left_expect):
        for w in left_writes:
            if writes[w].action == test.expect[e].action:
                keys = _bad_keys(test.expect[e], writes[w])
                mismatch.append(f"{test.expect[e].action}: {', '.join(keys)}")
                left_expect.remove(e)
                left_writes.remove(w)
                break
    if mismatch:
        return False, "field_mismatch", "A held write differs in: " + " and ".join(mismatch) + "."
    if left_expect:
        names = ", ".join(sorted({test.expect[e].action for e in left_expect}))
        return False, "missing_write", f"The turn did not hold the write {names}."
    if left_writes:
        names = ", ".join(sorted({writes[w].action for w in left_writes}))
        return False, "extra_write", f"The turn held a write that the test does not expect: {names}."
    return True, "passed", ""

