"""Skill tests: the shape of a test, the pass rule and the run (WS-STU F4).

Spec: ``project-docs/specs/ai_studio_rows/F4.md``, ``ai_studio.md`` §7. A
saved skill holds tests in its version body, as ``body.tests``, at most
:data:`MAX_TESTS`::

    {"id": "t1", "input": "Add Ravi from Acme as a lead",
     "expect": [{"action": "object:lead.create", "fields": {"data.company": "Acme"}}]}

``expect: []`` means that the turn writes nothing. An ``expect`` entry names
ONE catalog action: ``tool:<name>``, ``object:<name>.<verb>`` or
``workflow:<id>``. A generic tool takes a pin instead.
``{"action": "tool:studio_record_create", "object": "lead"}`` reads as
``object:lead.create``, and ``{"action": "tool:run_workflow", "workflow": "<id>"}``
reads as ``workflow:<id>``. ``package`` pins the package of an object action.
A key of ``fields`` may name one level down, as ``data.company``.

**The pass rule reads the end state, never the words.** :func:`evaluate`
takes the calls that the dry-run door recorded
(``acb_skills.skill_dry_run``). It has no text parameter, so the answer of
the model cannot change a result. A test passes only when all three hold:

1. Each call is an action of the skill. A display tool is the one
   exception. A generic tool must name an object or a workflow of the
   skill. Else the reason is ``other_action``.
2. Each held write passes the schema of its tool. Else ``invalid_call``.
3. The held writes match ``expect`` one to one, by action, pins and
   ``fields``. ``fields`` is a subset match. Each named key must equal the
   argument after a trim of a string, with no case folding. Else
   ``missing_write``, ``extra_write`` or ``field_mismatch``.

A detail names a key, a tool or an action of the test, and never a value.

**The run (F4b).** :func:`run_suite` runs each test on a real turn of the
orchestrator, with the forced skill pack, inside ``dry_run_scope``. It
refuses the whole suite with ``cap_unset`` when no credit cap is set, before
any runner call and any meter read. A :class:`TestResult` holds codes and
action names only. The recorded arguments stay in memory for
:func:`evaluate`, and no answer and no store holds them.

**A turn that errors never passes.** :func:`agent_runner` reads each line of
the stream. A ``RUN_ERROR`` line, or a stream that ends with no
``RUN_FINISHED`` line, raises :class:`RunFailed`, and the test reads
``run_failed``. The detail holds a code, never the text of the error.

**One suite at a time for each member.** :func:`run_suite` takes a slot in
the tenant Redis before its first test, as the IMAP login test does. A second
suite of the same member raises :class:`SuiteRunning` with no run and no
meter read. A Redis fault raises :class:`SuiteSlotUnavailable`, so a fault
runs nothing.

Fence: ``tests/unit/test_skill_tests.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from acb_common import get_logger
from acb_common._log import job_member_scope
from acb_common.no_person import no_person_scope
from acb_common.tenant_redis import get_tenant_redis, organization_scope
from acb_common.tenant_redis import key as redis_key
from acb_llm.attribution import run_ceiling_scope
from acb_llm.run_errors import RUN_ERROR_CODES
from acb_skills.skill_dry_run import (
    MAX_BAD_KEYS,
    MAX_KEY_NAME,
    METER_TIMEOUT_S,
    TOOL_CALL_CAP,
    DryRun,
    DryRunStopped,
    RecordedCall,
    dry_run_scope,
)
from acb_skills.skill_packs import OBJECT_VERB_TOOLS, action_tool
from acb_skills.subagent_contract import DISPLAY_TOOLS

from gateway.db import tenant_session as _tenant_session

__all__ = [
    "MAX_EXPECT", "MAX_FIELDS", "MAX_INPUT_CHARS", "MAX_RUN", "MAX_TESTS", "REASONS",
    "ExpectedWrite", "RecordedCall", "RunFailed", "SkillTest", "SkillTestError",
    "SuiteResult", "SuiteRunning", "SuiteSlotUnavailable", "TestResult", "agent_runner",
    "call_action", "evaluate", "parse_tests", "run_suite",
]

_log = get_logger(__name__)

#: The most tests in one version body.
MAX_TESTS = 20
#: The most ``expect`` entries of one test.
MAX_EXPECT = 5
#: The most characters of the ``input`` of one test.
MAX_INPUT_CHARS = 2000
#: The most keys of ``fields`` of one ``expect`` entry.
MAX_FIELDS = 20
#: The most tests that one call of :func:`run_suite` runs. The rest are
#: ``not_run``.
MAX_RUN = 5
#: The most seconds of one call of :func:`run_suite`. A test that cannot
#: start inside it is ``not_run``.
SUITE_SECONDS = 300.0
#: The seconds that the slot of a suite lives. It outlives the longest suite,
#: so a worker that dies frees the slot by itself.
SLOT_SECONDS = int(SUITE_SECONDS) + 60
#: The namespace of the slot in the tenant Redis.
_SLOT_NAMESPACE = "studio-skill-test-busy"

#: The reason codes of a result. ``passed`` is the one good code. TOF-3a
#: uses the same set.
REASONS = (
    "passed", "other_action", "missing_write", "extra_write", "field_mismatch",
    "invalid_call", "cap_reached", "cap_unset", "call_cap", "cost_read_failed",
    "hold_unarmed", "skill_unloaded", "run_failed", "bad_test", "not_run",
)

#: The codes that stop the rest of a suite.
_SUITE_STOPS = frozenset({"cap_reached", "cost_read_failed", "hold_unarmed", "skill_unloaded"})

_TOOL_ACTION = re.compile(r"tool:[A-Za-z_][A-Za-z0-9_]*")
_WORKFLOW_ACTION = re.compile(r"workflow:[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}")
_MAX_ID = 64
_MAX_PIN = 128
#: The verbs of an object action that write. ``list`` and ``get`` read.
_WRITE_VERBS = frozenset({"create", "update", "delete"})
#: The verb of each record tool.
_VERB_OF: Mapping[str, str] = {tool: verb for verb, tool in OBJECT_VERB_TOOLS.items()}
_RUN_WORKFLOW = "run_workflow"
_GENERIC = frozenset({*_VERB_OF, _RUN_WORKFLOW})


class SkillTestError(ValueError):
    """A test list that has the wrong shape. The reason is ``bad_test``. The
    message names a path, never a value."""

    reason = "bad_test"


class RunFailed(RuntimeError):
    """The turn of a test errored, or it ended with no ``RUN_FINISHED``.
    *code* is a code, such as ``run_error:credits`` or ``run_unfinished``.
    It never holds the text of the error, which can quote the member."""

    def __init__(self, code: str) -> None:
        super().__init__(f"The turn failed: {code}")
        self.code = code


class SuiteRunning(RuntimeError):
    """A suite of this member runs already. The route answers 429."""

    reason = "suite_running"


class SuiteSlotUnavailable(RuntimeError):
    """The slot of the suite could not be read, so nothing runs (fail closed)."""

    reason = "slot_unavailable"


@dataclass(frozen=True)
class ExpectedWrite:
    """One write that a test expects: an action, its pinned package, and a
    subset of its arguments. A key of *fields* may be ``<arg>.<key>``."""

    action: str
    fields: Mapping[str, Any]
    package: str | None = None


@dataclass(frozen=True)
class SkillTest:
    """One test: a prompt, and the writes that the turn must hold.

    *expect* None is an input-only run (TOF-3a). Only the invariants count:
    the action rule and the schema rule. ``()`` means "writes nothing"."""

    id: str
    input: str
    expect: tuple[ExpectedWrite, ...] | None


@dataclass(frozen=True)
class TestResult:
    """The result of one test. *passed* is None for a test that did not run.

    *writes* and *calls* hold action names only, never an argument."""

    __test__ = False  # pytest: this is a result, never a test class

    test_id: str | None
    passed: bool | None
    reason: str
    detail: str = ""
    writes: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    credits: Decimal = Decimal(0)
    model_calls: int = 0
    run_id: str | None = None


@dataclass(frozen=True)
class SuiteResult:
    """The result of a suite. *stopped* names the code that ended it early."""

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


def _pin(path: str, item: Mapping[str, Any], key: str) -> str | None:
    """The pin *key* of an ``expect`` entry, or None when it is absent."""
    if key not in item:
        return None
    value = item[key]
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_PIN:
        raise _bad(f"{path}.{key}", f"must be a non-empty string of at most {_MAX_PIN} characters")
    return value.strip()


def _object_action(path: str, action: str) -> str:
    """*action* when it is ``object:<name>.<verb>`` of a write verb, by the ONE
    grammar of a uses string (``studio_manifest.parse_uses``)."""
    from acb_common.studio_manifest import parse_uses

    ref = parse_uses(action)
    if ref is None or ref.source != "object" or ref.op not in _WRITE_VERBS:
        raise _bad(f"{path}.action",
                   "must name an object write: object:<name>.create, update or delete")
    return action


def _workflow_action(path: str, action: str) -> str:
    if not _WORKFLOW_ACTION.fullmatch(action):
        raise _bad(f"{path}.action", "must be tool:<name>, object:<name>.<verb> or workflow:<id>")
    return action


def _parse_action(path: str, item: Mapping[str, Any]) -> tuple[str, str | None]:
    """The canonical action of an ``expect`` entry, and its package pin."""
    action = item.get("action")
    if not isinstance(action, str):
        raise _bad(f"{path}.action", "must be tool:<name>, object:<name>.<verb> or workflow:<id>")
    obj, workflow, package = (_pin(path, item, k) for k in ("object", "workflow", "package"))
    if _TOOL_ACTION.fullmatch(action):
        name = action[len("tool:"):]
        if name in _VERB_OF:
            if obj is None or workflow is not None:
                raise _bad(f"{path}.object", "must name the object of a record tool")
            return _object_action(path, f"object:{obj}.{_VERB_OF[name]}"), package
        if name == _RUN_WORKFLOW:
            if workflow is None or obj is not None or package is not None:
                raise _bad(f"{path}.workflow", "must name the workflow of run_workflow")
            return _workflow_action(path, f"workflow:{workflow}"), None
        if obj is not None or workflow is not None or package is not None:
            raise _bad(path, "pins an object, a workflow or a package of a tool that takes none")
        return action, None
    if obj is not None or workflow is not None:
        raise _bad(path, "pins an object or a workflow that the action names already")
    if action.startswith("object:"):
        return _object_action(path, action), package
    if package is not None:
        raise _bad(f"{path}.package", "pins the package of an object action only")
    return _workflow_action(path, action), None


def _field_key_ok(key: Any) -> bool:
    """A key of ``fields``: a name, or ``<arg>.<key>``, one level down."""
    if not isinstance(key, str) or not key.strip():
        return False
    parts = key.split(".")
    return len(parts) <= 2 and all(p.strip() and p == p.strip() for p in parts)


def _parse_expect(path: str, item: Any) -> ExpectedWrite:
    if not isinstance(item, Mapping):
        raise _bad(path, "must be an object")
    action, package = _parse_action(path, item)
    fields = item.get("fields", {})
    if not isinstance(fields, Mapping):
        raise _bad(f"{path}.fields", "must be an object")
    if len(fields) > MAX_FIELDS:
        raise _bad(f"{path}.fields", f"holds at most {MAX_FIELDS} keys")
    for key, value in fields.items():
        if not _field_key_ok(key):
            raise _bad(f"{path}.fields", "must name each field with a name, or a name.key")
        if not _is_scalar(value):
            raise _bad(f"{path}.fields", "must hold only text, numbers, true, false or null")
    return ExpectedWrite(action=action, fields=dict(fields), package=package)


def parse_tests(body: Any) -> list[SkillTest]:
    """The tests of a version *body*. An absent ``tests`` gives an empty list.

    Raises :class:`SkillTestError` (reason ``bad_test``) when ``tests`` is no
    list, holds more than :data:`MAX_TESTS`, repeats an id, or holds a test
    or an ``expect`` entry of the wrong shape. A test holds at most
    :data:`MAX_EXPECT` entries and an ``input`` of at most
    :data:`MAX_INPUT_CHARS` characters. Other keys on a test are ignored. The
    message names the path and never a value.
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
        if len(prompt) > MAX_INPUT_CHARS:
            raise _bad(f"{path}.input", f"holds at most {MAX_INPUT_CHARS} characters")
        expect = item.get("expect")
        if not isinstance(expect, list):
            raise _bad(f"{path}.expect", "must be a list")
        if len(expect) > MAX_EXPECT:
            raise _bad(f"{path}.expect", f"holds at most {MAX_EXPECT} entries")
        out.append(SkillTest(
            id=test_id, input=prompt,
            expect=tuple(_parse_expect(f"{path}.expect[{j}]", e)
                         for j, e in enumerate(expect))))
    return out


# ── The pass rule ──────────────────────────────────────────────────────────


def call_action(call: RecordedCall) -> str:
    """The catalog action of one recorded call. A record tool with an
    ``object`` argument is ``object:<object>.<verb>``. ``run_workflow`` with a
    ``workflow`` argument is ``workflow:<id>``. Each other call is
    ``tool:<name>``."""
    verb = _VERB_OF.get(call.tool)
    if verb is not None:
        obj = call.args.get("object")
        if isinstance(obj, str) and obj.strip():
            return f"object:{obj.strip()}.{verb}"
    elif call.tool == _RUN_WORKFLOW:
        workflow = call.args.get("workflow")
        if isinstance(workflow, str) and workflow.strip():
            return f"workflow:{workflow.strip()}"
    return call.action


def _scope(skill_actions: Collection[str]) -> tuple[frozenset[str], frozenset[str]]:
    """The object and workflow actions of the skill, and the names of its
    other tools. ``skill_packs.action_tool`` is the ONE map of a door op."""
    pinned: set[str] = set()
    tools: set[str] = set()
    for raw in skill_actions:
        action = str(raw).strip()
        if action.startswith(("object:", "workflow:")):
            pinned.add(action)
        elif action.startswith("tool:"):
            tools.add(action[len("tool:"):])
        elif (tool := action_tool(action)) is not None:
            tools.add(tool)
    return frozenset(pinned), frozenset(tools - _GENERIC)


def _of_skill(call: RecordedCall, pinned: frozenset[str], tools: frozenset[str]) -> bool:
    if call.tool in _GENERIC:
        return call_action(call) in pinned
    return call.tool in tools


def _shown(call: RecordedCall, pinned: frozenset[str]) -> str:
    """The name of a call in a result. A generic tool shows its catalog
    action only when the skill holds it, so no argument value shows."""
    action = call_action(call)
    return action if action in pinned else call.action


_MISSING = object()


def _arg(args: Mapping[str, Any], name: str) -> Any:
    """The argument *name* of a call. ``<arg>.<key>`` reads one level down.

    An argument that is a JSON text of an object, such as ``payload_json`` of
    ``run_workflow``, is read as that object for the one level."""
    if name in args:
        return args[name]
    head, dot, tail = name.partition(".")
    if not dot:
        return _MISSING
    sub = args.get(head)
    if isinstance(sub, str):
        try:
            sub = json.loads(sub)
        except ValueError:
            return _MISSING
    if not isinstance(sub, Mapping) or tail not in sub:
        return _MISSING
    return sub[tail]


def _same(expected: Any, actual: Any) -> bool:
    """Equal after a trim of a string. A type that differs is not equal, so
    ``True`` is no ``1`` and ``"5"`` is no ``5``. No case folding."""
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    return type(expected) is type(actual) and expected == actual


def _bad_keys(want: ExpectedWrite, call: RecordedCall) -> list[str]:
    """The keys of *want* that *call* does not match, and ``package`` for a
    pinned package that differs. Sorted."""
    bad = [k for k, v in want.fields.items()
           if (got := _arg(call.args, k)) is _MISSING or not _same(v, got)]
    if want.package is not None and not _same(want.package, call.args.get("package_id")):
        bad.append("package")
    return sorted(bad)


def _fits(want: ExpectedWrite, call: RecordedCall) -> bool:
    return want.action == call_action(call) and not _bad_keys(want, call)


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
             skill_actions: Collection[str]) -> tuple[bool, str, str]:
    """Judge one test by the calls of its run. Answers ``(passed, reason, detail)``.

    *calls* is the record of the dry-run door. *skill_actions* holds the
    catalog actions of the skill. There is no text parameter: the answer of
    the model never decides a result.
    """
    pinned, tools = _scope(skill_actions)
    for call in calls:
        if call.tool in DISPLAY_TOOLS:
            continue
        if not _of_skill(call, pinned, tools):
            return False, "other_action", f"The turn called {call.tool}, which is no action of the skill."
    writes = [c for c in calls if c.held and c.write and c.tool not in DISPLAY_TOOLS]
    for call in writes:
        if not call.valid:
            keys = ", ".join(k[:MAX_KEY_NAME] for k in call.invalid_keys[:MAX_BAD_KEYS])                 or "its arguments"
            return False, "invalid_call", f"The real tool refuses the held write {call.tool}: {keys}."
    if test.expect is None:
        return True, "passed", ""
    owner = _match(test.expect, writes)
    left_writes = [w for w in range(len(writes)) if w not in owner]
    matched = set(owner.values())
    left_expect = [e for e in range(len(test.expect)) if e not in matched]

    mismatch: list[str] = []
    for e in list(left_expect):
        for w in left_writes:
            if call_action(writes[w]) == test.expect[e].action:
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
        names = ", ".join(sorted({_shown(writes[w], pinned) for w in left_writes}))
        return False, "extra_write", f"The turn held a write that the test does not expect: {names}."
    return True, "passed", ""


# ── The run (F4b) ──────────────────────────────────────────────────────────

#: ``await runner(message, *, run_id, thread_id, org, member)``: one turn.
Runner = Callable[..., Awaitable[Any]]
#: ``await meter(org, member, run_id)``: the credits that run *run_id* spent.
Meter = Callable[[str, str, str], Awaitable[Any]]

#: The agent of a skill test: the chat assistant, which loads skill packs.
TEST_AGENT = "orchestrator"


async def agent_runner(message: str, *, run_id: str, thread_id: str, org: str,
                       member: str) -> None:
    """The real runner: one stream run of the orchestrator, drained.

    The kwargs follow the chat route (``routes/agent.py``): the org and the
    member of the verified session, cards shown and a room of one. So the
    skill pack can load. As the chat route does for a room of one, the
    member is the acting user of the tools that read the gateway. The scope
    of that binding ends with the run. :func:`run_suite` sets the other
    scopes around it.

    The stream answers a failed model call with a ``RUN_ERROR`` line, and
    then it ends with no raise. So this runner reads each line. A
    ``RUN_ERROR`` line, or an end with no ``RUN_FINISHED`` line, raises
    :class:`RunFailed`. Its code holds the code of the error, never its text."""
    from acb_skills.memory_tools import _bind_memory_user_id, _unbind_memory_user_id
    from orchestrator.executor import run_agent_stream

    error: str | None = None
    finished = False
    binding = _bind_memory_user_id(member)
    try:
        async for line in run_agent_stream(
            TEST_AGENT, {"message": message}, run_id=run_id, thread_id=thread_id,
            organization_id=org, session_user=member, shows_cards=True, solo_room=True,
        ):
            for event in _events(line):
                kind = event.get("type")
                if kind == "RUN_ERROR" and error is None:
                    code = event.get("code")
                    error = code if code in RUN_ERROR_CODES else "unknown"
                elif kind == "RUN_FINISHED":
                    finished = True
    finally:
        _unbind_memory_user_id(binding)
    if error is not None:
        raise RunFailed(f"run_error:{error}")
    if not finished:
        raise RunFailed("run_unfinished")


def _events(line: Any) -> list[dict[str, Any]]:
    """The events of one SSE text of the stream: each ``data:`` part that
    holds a JSON object. Any other part is skipped."""
    out: list[dict[str, Any]] = []
    for part in str(line or "").split("\n"):
        part = part.strip()
        if not part.startswith("data:"):
            continue
        body = part[len("data:"):].strip()
        if not body or body == "[DONE]":
            continue
        try:
            event = json.loads(body)
        except ValueError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def _credits(value: Any) -> Decimal | None:
    """A meter answer as credits, or None when it is no finite number of 0
    or more."""
    if isinstance(value, Decimal) and value.is_finite() and value >= 0:
        return value
    return None


async def _read(meter: Meter, org: str, member: str, run_id: str) -> Decimal | None:
    """One meter read. A fault, a slow read and a bad answer give None."""
    try:
        return _credits(await asyncio.wait_for(meter(org, member, run_id), METER_TIMEOUT_S))
    except asyncio.CancelledError:
        raise
    except Exception:
        return None


async def _load_skill(org: str, member: str, skill_id: str,
                      version: int | None) -> tuple[Any, Any]:
    """The skill of *member* and its version (the newest for None). A skill
    of another member or org reads as ``(None, None)``."""
    from gateway.studio import skills_store as store

    async with _tenant_session(org) as db:
        skill = await store.get_skill(db, member, skill_id)
        if skill is None:
            return None, None
        return skill, await store.get_version(db, member, skill_id, version)


def _actions_of(body: Mapping[str, Any]) -> list[str]:
    found = body.get("actions")
    return [str(a) for a in found] if isinstance(found, list) else []


def _chosen(body: Mapping[str, Any], tests: Any, inputs: Sequence[str]) -> list[SkillTest]:
    """The tests of the run: *tests* when given, else the tests of *body*.
    TOF-3a passes the tests of an earlier version, so a new body cannot
    weaken them. Then one input-only run for each of *inputs*."""
    if tests is None:
        out = parse_tests(body)
    elif all(isinstance(t, SkillTest) for t in tests):
        out = list(tests)
    else:
        out = parse_tests({"tests": list(tests)})
    for i, text in enumerate(inputs):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT_CHARS:
            raise _bad(f"inputs[{i}]",
                       f"must be a non-empty string of at most {MAX_INPUT_CHARS} characters")
        out.append(SkillTest(id=f"input-{i + 1}", input=text, expect=None))
    return out


def _cap_of(cap: Any) -> Decimal | None:
    """The cap of the suite: the setting, lowered by *cap* when given. None
    when the setting is unset. A request cap can only lower it."""
    from gateway.routes.studio import flags

    setting = flags.skill_tests_cap()
    if setting is None:
        return None
    if cap is None:
        return setting
    if not isinstance(cap, Decimal) or not cap.is_finite() or cap <= 0:
        raise ValueError("a request cap is a finite Decimal above 0")
    return min(setting, cap)


def _not_run(test: SkillTest, reason: str = "not_run", detail: str = "") -> TestResult:
    return TestResult(test_id=test.id, passed=None, reason=reason, detail=detail)


async def run_suite(*, org: str, member: str, skill_id: str, version: int | None = None,
                    body: Mapping[str, Any] | None = None, tests: Any = None,
                    inputs: Sequence[str] = (), cap: Decimal | None = None,
                    runner: Runner, meter: Meter) -> SuiteResult:
    """Run the tests of one skill on real turns, each in a dry run.

    *org* and *member* come from the verified session, never from a request
    body. *body* is an unsaved body that the caller checked. With no body,
    the stored *version* runs, the newest by default. *tests* replaces the
    tests of the body (TOF-3a). *inputs* adds input-only runs. *cap* can only
    lower the setting ``STUDIO_SKILL_TESTS_CAP_CREDITS``. The caller injects
    *runner* and *meter*, so a test passes fakes. Raises LookupError for a
    skill or a version that the member does not hold.

    Each test gets a new run id and the thread ``skilltest:<uuid>``. It runs
    as the member (``job_member_scope``), with no person present, under the
    run ceiling, and in ``dry_run_scope``. At most :data:`MAX_RUN` tests run
    in :data:`SUITE_SECONDS`, and the rest are ``not_run``.

    Before the first test it takes the one slot of the member
    (:func:`_one_suite`). A suite of the member that runs already raises
    :class:`SuiteRunning`, and a Redis fault raises
    :class:`SuiteSlotUnavailable`. Then no test runs and no meter read
    happens. Each result writes one ``studio.skill_test.result`` line with
    ids and codes only."""
    from acb_skills import skill_packs, tool_packs

    from gateway.routes.studio import flags
    from gateway.studio import skills_store as store

    skill, stored = await _load_skill(org, member, skill_id, version)
    if skill is None or (body is None and stored is None):
        raise LookupError("no such skill or version")
    used: Mapping[str, Any] = body if body is not None else stored.body
    number = int(stored.version) if body is None else int(skill.current_version or 0)
    digest = store.content_hash(used)
    chosen = _chosen(used, tests, inputs)
    effective = _cap_of(cap)

    def suite(results: list[TestResult], spent: Decimal, stopped: str | None) -> SuiteResult:
        passed = bool(results) and all(r.passed is True for r in results)
        return SuiteResult(skill_id=str(skill.id), version=number, content_hash=digest,
                           results=tuple(results), passed=passed, credits=spent,
                           cap=effective, stopped=stopped)

    if effective is None:
        return suite([_not_run(t, "cap_unset") for t in chosen], Decimal(0), "cap_unset")
    pack = skill_packs.build(
        str(skill.id), skill.name, skill.description, number, digest, used,
        max_tools=tool_packs.MAX_PACK_TOOLS, chat_tools=flags.chat_tools_allowed(org))
    if isinstance(pack, str):
        return suite([_not_run(t, "skill_unloaded", f"The skill does not load: {pack}.")
                      for t in chosen], Decimal(0), "skill_unloaded")
    actions = _actions_of(used)
    results: list[TestResult] = []
    spent = Decimal(0)
    stopped: str | None = None
    async with _one_suite(org, member):
        deadline = time.monotonic() + SUITE_SECONDS
        for place, test in enumerate(chosen):
            left = deadline - time.monotonic()
            if stopped is not None or place >= MAX_RUN or left <= 0:
                results.append(_not_run(test))
                continue
            if spent >= effective:
                stopped = "cap_reached"
                results.append(_not_run(test))
                continue
            result, read_ok = await _run_one(
                test, pack, actions, org=org, member=member, runner=runner, meter=meter,
                cap=effective, before=spent, seconds=left)
            results.append(result)
            _log.info("studio.skill_test.result", org=org, skill=str(skill.id),
                      version=number, test=test.id, reason=result.reason,
                      credits=str(result.credits), model_calls=result.model_calls)
            # The best known cost counts, also after a fault, so the spend
            # of the suite is never short.
            spent += result.credits
            if not read_ok:
                stopped = "cost_read_failed"
            if result.reason in _SUITE_STOPS:
                stopped = stopped or result.reason
    return suite(results, spent, stopped)


@contextlib.asynccontextmanager
async def _one_suite(org: str, member: str) -> AsyncIterator[None]:
    """Hold the one slot of *member* for a suite, in the tenant Redis.

    The idiom of the IMAP login test (``routes/email/transport/accounts.py``):
    ``SET NX EX`` of a random token on a key of a hash of the member, and a
    release only while the key still holds that token. The key expires after
    :data:`SLOT_SECONDS`, so a worker that dies frees it. A Redis fault raises
    :class:`SuiteSlotUnavailable`, and nothing runs."""
    token = secrets.token_hex(8)
    who = hashlib.sha256(member.strip().lower().encode("utf-8")).hexdigest()[:24]
    try:
        with organization_scope(org):
            busy = redis_key(_SLOT_NAMESPACE, who)
            taken = await get_tenant_redis().set(busy, token, nx=True, ex=SLOT_SECONDS)
    except Exception as exc:  # fail closed, and say nothing of the fault
        _log.warning("studio.skill_test.slot_unavailable", error_type=type(exc).__name__)
        raise SuiteSlotUnavailable("The slot of the suite is not readable.") from None
    if not taken:
        raise SuiteRunning("A suite of this member runs already.")
    try:
        yield
    finally:
        # A fault frees nothing here, and the key expires by itself.
        with contextlib.suppress(Exception), organization_scope(org):
            redis = get_tenant_redis()
            held = await redis.get(busy)
            if isinstance(held, bytes):
                held = held.decode("utf-8", errors="replace")
            if held == token:
                await redis.delete(busy)


async def _run_one(test: SkillTest, pack: Any, actions: Sequence[str], *, org: str,
                   member: str, runner: Runner, meter: Meter, cap: Decimal,
                   before: Decimal, seconds: float) -> tuple[TestResult, bool]:
    """One test on one real turn. Answers the result and whether the meter
    read after the run worked. When that read faults, the credits of the
    result are the highest read of the run, so the spend is never short."""
    run_id = str(uuid.uuid4())
    thread_id = f"skilltest:{uuid.uuid4()}"

    seen: list[Decimal] = []

    async def spend() -> Decimal:
        value = _credits(await meter(org, member, run_id))
        if value is None:
            raise ValueError("the meter gave no number")
        seen.append(value)
        return before + value

    record = DryRun(pack=pack, call_cap=TOOL_CALL_CAP, credit_cap=cap, spend=spend)
    failed: str | None = None
    with job_member_scope(member, app="studio", agent="studio.skill_test"), \
            no_person_scope(), run_ceiling_scope(cap - before), dry_run_scope(record):
        # The runner runs in its own task, on a copy of these scopes. The
        # stream run clears the run context as it ends, so on this task the
        # proven member stays bound for the meter read after the run.
        turn = asyncio.ensure_future(runner(test.input, run_id=run_id, thread_id=thread_id,
                                            org=org, member=member))
        try:
            await asyncio.wait_for(turn, seconds)
        except asyncio.CancelledError:
            raise
        except DryRunStopped:
            pass
        except RunFailed as exc:
            failed = exc.code
        except TimeoutError:
            failed = "run_timeout"
        except Exception:
            failed = "runner_raised"
        credits = await _read(meter, org, member, run_id)
    if credits is None and record.stopped is None:
        record.stopped = "cost_read_failed"  # a cost that no one can read fails the test
    reason, passed, detail = _outcome(test, record, actions, failed)
    pinned, _tools = _scope(actions)
    result = TestResult(
        test_id=test.id, passed=passed, reason=reason, detail=detail,
        writes=tuple(_shown(c, pinned) for c in record.calls
                     if c.held and c.write and c.tool not in DISPLAY_TOOLS),
        calls=tuple(_shown(c, pinned) for c in record.calls),
        credits=credits if credits is not None else max(seen, default=Decimal(0)),
        model_calls=record.model_calls, run_id=run_id)
    return result, credits is not None


def _outcome(test: SkillTest, record: DryRun, actions: Sequence[str],
             failed: str | None) -> tuple[str, bool, str]:
    """The reason, the verdict and the detail of one run, in order: a stop,
    an unloaded skill, an unarmed door, a failed run, then the pass rule. The
    attach refuses an unloaded skill, so that run is unarmed too, and the
    first cause names it."""
    if record.stopped is not None:
        return record.stopped, False, ""
    if not record.loaded:
        return "skill_unloaded", False, "The skill did not load in the run."
    if not record.armed:
        return "hold_unarmed", False, "The run had no dry-run door."
    if failed is not None:
        # A code only, never the text of the error.
        return "run_failed", False, f"The run failed: {failed}."
    ok, reason, detail = evaluate(test, record.calls, skill_actions=actions)
    return reason, ok, detail
