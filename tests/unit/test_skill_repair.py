"""WS-TOF TOF-3a F12: the repair of a saved skill (hermetic).

Spec: ``project-docs/specs/ai_studio_rows/F12.md``. F12b builds the repair
key (``gateway/studio/skill_repair.py`` ``body_key``) and the two writers of
the store of skill runs:

* the live writer of the chat run, in ``orchestrator/_tool_injection.py``
  (``SkillRunNotes``, ``_note_skill_call`` in the pack tool guard, and
  ``schedule_skill_runs`` at the end of ``executor.run_agent_stream``);
* the test writer of the test run route, ``_record_test_failures`` in
  ``gateway/routes/studio/skills.py``.

F12c adds the five fences of the owner to this file. The R8 half of F12b is
``tests/unit/test_skill_run_writers_r8.py``.

**The mutations (R7).**

* Drop the ``except`` note in ``_pack_tool``:
  ``test_a_raise_of_a_skill_tool_is_tool_error`` goes red.
* Read ``turn.member_part`` or ``run_ctx["member"]`` for the member:
  ``test_the_member_comes_from_the_run_identity`` goes red.
* Drop the flag check of ``_note_skill_facts``:
  ``test_flag_off_keeps_no_fact_and_calls_no_store`` goes red.
* Let ``_write_skill_runs`` raise: ``test_a_store_fault_never_fails_the_run``
  goes red.
* Write a test row for ``cap_reached``:
  ``test_the_test_writer_writes_only_the_seven_skill_faults`` goes red.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import dataclasses
import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_common.tenant_redis import TenantRedis
from acb_skills import ask_tools
from acb_skills import studio_tools as stools
from acb_skills.skill_dry_run import HoldAndRecord, current_dry_run
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from agent_framework import FunctionTool
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.studio import skill_repair, skill_runs, skill_tests
from gateway.studio import skills_store as store
from orchestrator import _tool_injection as ti

from tests.unit.test_skill_tests import SlotRedis

_ROOT = Path(__file__).resolve().parents[2]

ORG = "0a0a0a0a-0000-4000-8000-0000000012bb"
MEMBER = "ana@f12b.test"
SKILL = str(uuid.UUID("11111111-2222-4333-8444-777777777777"))
SKILL2 = str(uuid.UUID("11111111-2222-4333-8444-888888888888"))
SLUG = f"skill:{SKILL}"
SLUG2 = f"skill:{SKILL2}"
KEY = "ab" * 32
WORDS = "Call Asha on 555 0100"
SECRET_BITS = ("Asha", "555", "0100")
RUN_KEY = "key-run-1"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _flags(monkeypatch, *, studio: str = "*", repair: str = "*") -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", studio, raising=False)
    monkeypatch.setattr(settings, "studio_skill_repair_orgs", repair, raising=False)
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: ORG)


def _loaded(skill_id: str = SKILL, **actions: str) -> Any:
    return ti._LoadedSkill(skill_id=skill_id, version=2, body_key=KEY,
                           actions=actions or {"create_task": "tool:create_task",
                                               "list_tasks": "tool:list_tasks"})


def _notes(**over: Any) -> ti.SkillRunNotes:
    values: dict[str, Any] = {
        "org": ORG, "member": MEMBER, "run_id": "run-1", "thread_id": "thread-1",
        "run_key": RUN_KEY, "skills": {SLUG: _loaded()}, "words": WORDS}
    values.update(over)
    return ti.SkillRunNotes(**values)


@contextlib.contextmanager
def _run(notes: Any, run_key: str = RUN_KEY) -> Iterator[None]:
    """The run of a member's own chat, bound by the server, with *notes*."""
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.tool_packs import PACK_RUN_KEY

    token = bind_tenant(ORG)
    try:
        with artifact_context_scope():
            values: dict[str, Any] = {
                "run_id": "run-1", "session_id": "thread-1", "agent_name": "orchestrator",
                "member": MEMBER, "no_egress": False, PACK_RUN_KEY: run_key}
            if notes is not None:
                values[ti.SKILL_RUN_NOTES_KEY] = notes
            bind_artifact_context(**values)
            yield
    finally:
        release_tenant(token)


@pytest.fixture
def writes_are(monkeypatch):
    """``create_task`` is a write and ``list_tasks`` a read, as the risk
    registry would say."""
    from acb_skills import subagent_contract

    monkeypatch.setattr(subagent_contract, "annotated_read_only",
                        lambda name: name == "list_tasks")
    monkeypatch.setenv("AGENT_PERMISSION_MODE", "approve_all")


class ToolBoom(RuntimeError):
    pass


def _tool(name: str, body: Any) -> Any:
    """A pack tool *name* built by the one guarded builder (``_pack_call``)."""
    async def tool(title: str = "", due: str = "") -> Any:
        return await body()

    tool.__name__ = name
    tool.__qualname__ = name
    return ti._pack_call(tool, "projects-assistant", False, RUN_KEY, "projects")


async def _ok() -> str:
    return "Done."


async def _boom() -> str:
    raise ToolBoom(WORDS)  # a message that echoes the input


async def _refused() -> str:
    return "Refused: the task needs a project."


async def _declined() -> str:
    ask_tools._LAST_OUTCOME.set(ask_tools.CARD_DECLINED)
    return ask_tools.card_outcome_text(ask_tools.CARD_DECLINED)


# ── The repair key ──────────────────────────────────────────────────────────


def test_the_repair_key_folds_white_space_and_sorts_actions() -> None:
    body = {"instructions": "Make  a\n task.", "actions": ["tool:b", "tool:a"],
            "description": " Log a call. "}
    same = {"instructions": "Make a task.", "actions": ["tool:a", "tool:b"],
            "description": "Log a call."}
    other = {**same, "instructions": "Make two tasks."}
    assert skill_repair.body_key(body) == skill_repair.body_key(same)
    assert skill_repair.body_key(other) != skill_repair.body_key(same)
    assert len(skill_repair.body_key(same)) == 64
    with pytest.raises(TypeError):
        skill_repair.body_key(["not", "a", "body"])  # type: ignore[arg-type]


def test_safe_writes_skips_a_made_up_name_and_keeps_the_rest() -> None:
    """Instruction 4 of the F12a review: a key with a space raises in
    ``check_writes``. The writers pass each item through ``safe_writes``."""
    kept = skill_runs.safe_writes([
        {"action": "tool:create_task", "keys": ["title"]},
        {"action": "tool:create_task", "keys": ["Call Asha"]},
        {"action": "Call Asha on", "keys": []},
        {"action": "tool:list_tasks", "keys": ["due"], "value": "x"},
        "no item",
    ])
    assert kept == [{"action": "tool:create_task", "keys": ["title"]}]
    many = [{"action": f"tool:t{i}", "keys": []} for i in range(30)]
    assert len(skill_runs.safe_writes(many)) == skill_runs.MAX_WRITES


# ── The live writer: the notes of the pack tool guard ───────────────────────


class TestTheWriters:
    """The live writer and the test writer (F12.md "The writers (F12b)")."""

    async def test_a_passed_run_has_no_failure_and_names_its_writes(self, writes_are) -> None:
        notes = _notes()
        with _run(notes):
            assert await _tool("create_task", _ok)(title="Call back", due="today") == "Done."
            await _tool("list_tasks", _ok)()
        assert notes.row_of(SLUG) == (True, "passed", "", [
            {"action": "tool:create_task", "keys": ["due", "title"]}])

    async def test_a_raise_of_a_skill_tool_is_tool_error(self, writes_are) -> None:
        notes = _notes()
        with _run(notes):
            out = await _tool("create_task", _boom)(title="x")
        assert out.startswith("The tool failed ("), "the guard still answers text"
        passed, reason, detail, _w = notes.row_of(SLUG)
        assert (passed, reason, detail) == (False, "tool_error", "create_task: ToolBoom")

    async def test_a_refused_answer_is_tool_error(self, writes_are) -> None:
        notes = _notes()
        with _run(notes):
            await _tool("create_task", _refused)(title="x")
        assert notes.row_of(SLUG)[:3] == (False, "tool_error", "create_task: Refused")

    async def test_a_declined_card_is_card_declined(self, writes_are) -> None:
        notes = _notes()
        with _run(notes):
            await _tool("create_task", _declined)(title="x")
        assert notes.row_of(SLUG)[:3] == (False, "card_declined", "create_task")

    async def test_an_old_card_outcome_does_not_count(self, writes_are) -> None:
        """A declined card of an earlier call in this frame is no signal."""
        notes = _notes()
        with _run(notes):
            ask_tools._LAST_OUTCOME.set(ask_tools.CARD_DECLINED)
            await _tool("create_task", _ok)(title="x")
        assert notes.row_of(SLUG)[:2] == (True, "passed")

    async def test_tool_error_wins_over_card_declined(self, writes_are) -> None:
        notes = _notes()
        with _run(notes):
            await _tool("create_task", _declined)(title="x")
            await _tool("list_tasks", _boom)()
            await _tool("create_task", _declined)(title="y")
        assert notes.row_of(SLUG)[:3] == (False, "tool_error", "list_tasks: ToolBoom")

    async def test_a_tool_of_no_skill_and_another_run_note_nothing(self, writes_are) -> None:
        notes = _notes()
        with _run(notes):
            await _tool("send_email", _boom)()
        with _run(notes, run_key="key-of-a-child-run"):
            await ti._pack_call(_named("create_task", _boom), "projects-assistant", False,
                                "key-of-a-child-run", "projects")(title="x")
        assert notes.row_of(SLUG) == (True, "passed", "", [])

    async def test_a_tool_of_two_skills_notes_both(self, writes_are) -> None:
        notes = _notes(skills={SLUG: _loaded(), SLUG2: _loaded(SKILL2, create_task="crm.x")})
        with _run(notes):
            await _tool("create_task", _boom)(title="x")
        assert notes.row_of(SLUG)[1] == notes.row_of(SLUG2)[1] == "tool_error"
        assert notes.row_of(SLUG2)[3] == [{"action": "crm.x", "keys": ["title"]}]

    # ── The facts and the notes of the apply ────────────────────────────────

    def test_flag_off_keeps_no_fact_and_calls_no_store(self, monkeypatch) -> None:
        _flags(monkeypatch, repair="")
        calls: list[Any] = []
        monkeypatch.setattr(skill_runs, "record_live", lambda *a, **k: calls.append(a))
        token = ti._SKILL_FACTS.set({})
        try:
            pack = SimpleNamespace(slug=SLUG, skill_id=SKILL, version=2)
            ti._note_skill_facts([(pack, {"instructions": "x", "actions": []})])
            facts = ti._SKILL_FACTS.get()
        finally:
            ti._SKILL_FACTS.reset(token)
        assert facts == {}
        assert ti._skill_run_notes(facts, [pack], {"run_id": "run-1"}, RUN_KEY,
                                   words=WORDS) is None
        applied = ti.PackApply(builds=(), record=None, run_key=RUN_KEY)
        assert ti.SKILL_RUN_NOTES_KEY not in applied.context()
        assert ti.schedule_skill_runs(applied) is None
        assert calls == []

    def test_flag_on_keeps_the_facts_of_each_built_skill(self, monkeypatch) -> None:
        _flags(monkeypatch)
        monkeypatch.setattr("acb_skills.skill_packs.action_tool",
                            lambda a: a.split(":", 1)[1] if a.startswith("tool:") else None)
        body = {"instructions": "Make a task.", "actions": [" tool:create_task ", "crm.x"]}
        token = ti._SKILL_FACTS.set({})
        try:
            ti._note_skill_facts([(SimpleNamespace(slug=SLUG, skill_id=SKILL, version=3), body)])
            facts = dict(ti._SKILL_FACTS.get() or {})
        finally:
            ti._SKILL_FACTS.reset(token)
        assert facts[SLUG] == ti._LoadedSkill(
            skill_id=SKILL, version=3, body_key=skill_repair.body_key(body),
            actions={"create_task": "tool:create_task"})
        # With no binding of the apply, the store keeps nothing.
        ti._note_skill_facts([(SimpleNamespace(slug=SLUG, skill_id=SKILL, version=3), body)])
        assert ti._SKILL_FACTS.get() is None

    def test_the_member_comes_from_the_run_identity(self, monkeypatch) -> None:
        from acb_common.db import bind_tenant, release_tenant
        from orchestrator import executor

        facts = {SLUG: _loaded(), SLUG2: _loaded(SKILL2)}
        kept = [SimpleNamespace(slug=SLUG)]  # SLUG2 left the run in the apply
        ctx = {"run_id": "run-1", "session_id": "thread-1", "member": "mallory@evil.test"}
        monkeypatch.setattr(executor, "_verified_run_member", lambda: MEMBER)
        token = bind_tenant(ORG)
        try:
            notes = ti._skill_run_notes(facts, kept, ctx, RUN_KEY, words=WORDS)
            assert notes is not None
            assert (notes.member, notes.org, notes.run_id, notes.thread_id) == (
                MEMBER, ORG, "run-1", "thread-1")
            assert list(notes.skills) == [SLUG]
            assert notes.words() == WORDS and WORDS not in repr(notes)
            applied = ti.PackApply(builds=(), record=None, run_key=RUN_KEY, skill_notes=notes)
            assert applied.context()[ti.SKILL_RUN_NOTES_KEY] is notes
            monkeypatch.setattr(executor, "_verified_run_member", lambda: "")
            assert ti._skill_run_notes(facts, kept, ctx, RUN_KEY, words=WORDS) is None
            monkeypatch.setattr(executor, "_verified_run_member", lambda: MEMBER)
            assert ti._skill_run_notes(facts, kept, ctx, RUN_KEY, words=" ") is None, (
                "no words, no row")
        finally:
            release_tenant(token)

    def test_the_apply_hands_the_words_of_the_pack_turn(self) -> None:
        """The words of the row are ``PackTurn.words``, the sent text that the
        pack read took at the start of the run. No module but
        ``assistant_profile`` names ``MEMBER_SENT`` (the fence of
        ``test_tool_packs_choice.py``)."""
        src = (_ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py").read_text(
            encoding="utf-8")
        assert 'words=str(getattr(turn, "words", "") or "")' in src
        assert "MEMBER_SENT" not in src

    # ── The live writer: the write at the end of the run ────────────────────

    async def test_the_writer_writes_one_row_for_each_loaded_skill(self, monkeypatch) -> None:
        _flags(monkeypatch)
        seen, _session = _store(monkeypatch)
        notes = _notes(skills={SLUG: _loaded(), SLUG2: _loaded(SKILL2, send_email="tool:send_email")})
        notes.saw("create_task", error="ToolBoom", keys=("title", "Call Asha"))
        with _bound():
            assert await ti._write_skill_runs(notes) == 2
        first, second = seen
        assert first["args"][1:] == (MEMBER, SKILL)
        assert first["kw"] | {"writes": None} == {
            "version": 2, "passed": False, "reason": "tool_error", "body_key": KEY,
            "input_text": WORDS, "run_id": "run-1", "thread_id": "thread-1",
            "detail": "create_task: ToolBoom", "writes": None}
        assert first["kw"]["writes"] == [], "a key with a space leaves its item"
        assert second["kw"]["passed"] is True and second["kw"]["reason"] == "passed"

    async def test_flag_off_at_the_end_writes_nothing(self, monkeypatch) -> None:
        _flags(monkeypatch, repair="")
        seen, session = _store(monkeypatch)
        with _bound():
            assert await ti._write_skill_runs(_notes()) == 0
        assert seen == [] and session == []

    async def test_another_bound_org_writes_nothing(self, monkeypatch) -> None:
        _flags(monkeypatch)
        seen, session = _store(monkeypatch)
        with _bound(str(uuid.uuid4())):
            assert await ti._write_skill_runs(_notes()) == 0
        assert seen == [] and session == []

    async def test_a_duplicate_failure_is_swallowed(self, monkeypatch) -> None:
        _flags(monkeypatch)
        seen, _session = _store(monkeypatch, fail={SKILL: skill_runs.DuplicateFailure()})
        notes = _notes(skills={SLUG: _loaded(), SLUG2: _loaded(SKILL2)})
        with _bound():
            assert await ti._write_skill_runs(notes) == 1
        assert len(seen) == 2

    @pytest.mark.parametrize("fault", [RuntimeError(WORDS), skill_runs.UnknownSkill()])
    async def test_a_store_fault_never_fails_the_run(self, monkeypatch, fault) -> None:
        _flags(monkeypatch)
        _store(monkeypatch, fail={SKILL: fault})
        lines = _log_spy(monkeypatch)
        with _bound():
            applied = ti.PackApply(builds=(), record=None, run_key=RUN_KEY,
                                   skill_notes=_notes())
            task = ti.schedule_skill_runs(applied)
            assert task is not None
            assert await task == 0
        assert any(kw.get("error_type") == type(fault).__name__ for _e, kw in lines)
        _assert_no_words(lines)

    async def test_a_session_fault_never_fails_the_run(self, monkeypatch) -> None:
        _flags(monkeypatch)
        import acb_common.db as db

        @contextlib.asynccontextmanager
        async def broken(*_a: Any, **_k: Any) -> Any:
            raise ConnectionError(WORDS)
            yield  # pragma: no cover

        monkeypatch.setattr(db, "tenant_session", broken)
        lines = _log_spy(monkeypatch)
        with _bound():
            assert await ti._write_skill_runs(_notes()) == 0
        _assert_no_words(lines)

    async def test_no_input_text_is_kept(self, monkeypatch, writes_are) -> None:
        """F12b: the words reach the store only as ``input_text``, which the
        store hashes. No other argument, no log line and no repr holds a word
        of the input, also when a tool raises with the words in its message."""
        _flags(monkeypatch)
        seen, _session = _store(monkeypatch)
        lines = _log_spy(monkeypatch)
        notes = _notes()
        with _run(notes):
            await _tool("create_task", _boom)(title=WORDS)
        with _bound():
            await ti._write_skill_runs(notes)
        (call,) = seen
        others = {k: v for k, v in call["kw"].items() if k != "input_text"}
        for bit in SECRET_BITS:
            assert bit not in repr((call["args"], others)), bit
            assert bit not in repr(notes), bit
        _assert_no_words(lines)

    def test_the_run_end_schedules_the_write_once(self) -> None:
        """The end of ``run_agent_stream`` is the one caller, in its
        ``finally``, and nothing else in the orchestrator calls the store."""
        src = (_ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(
            encoding="utf-8")
        assert src.count("schedule_skill_runs(_pack_apply)") == 1
        assert "    finally:\n        # TOF-3a F12b: the rows of the skills of this run" in src
        inj = (_ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py").read_text(
            encoding="utf-8")
        assert inj.count("skill_runs.record_live(") == 1
        assert "record_test(" not in inj and "record_proposal(" not in inj

    # ── The test writer ─────────────────────────────────────────────────────

    async def test_the_test_writer_writes_only_the_seven_skill_faults(self, monkeypatch) -> None:
        _flags(monkeypatch)
        route, seen, versions = _route(monkeypatch)
        result = _suite(*skill_tests.REASONS)
        await route._record_test_failures(ORG, MEMBER, SKILL, result, None)
        assert sorted(c["kw"]["reason"] for c in seen) == sorted(skill_runs.SKILL_FAULTS)
        assert versions == [2], "the body of the version that ran"
        one = next(c for c in seen if c["kw"]["reason"] == "missing_write")
        assert one["args"][1:] == (MEMBER, SKILL)
        assert one["kw"]["test_id"] == "t-missing_write"
        assert one["kw"]["body_key"] == skill_repair.body_key(_BODY)
        assert one["kw"]["credits"] == Decimal("1.5") and one["kw"]["run_id"] == "r-missing_write"
        assert one["kw"]["writes"] == [{"action": "tool:create_task", "keys": []}]
        assert {c["kw"]["version"] for c in seen} == {2}, "the stored version that ran"

    async def test_an_unsaved_body_writes_no_row(self, monkeypatch) -> None:
        """Review P2-B: ``run_suite`` reports ``current_version`` for a body
        that is not saved, so a row would name a version that did not run.
        Mutation: write the rows of an unsaved body -> red."""
        _flags(monkeypatch)
        route, seen, versions = _route(monkeypatch)
        opened: list[Any] = []
        monkeypatch.setattr(route, "_tenant_session", lambda *a, **k: opened.append(a))
        body = {"instructions": "Other.", "actions": ["tool:create_task"]}
        await route._record_test_failures(ORG, MEMBER, SKILL, _suite("extra_write"), body)
        assert seen == [] and versions == [] and opened == []

    async def test_a_stored_version_that_is_gone_writes_no_row(self, monkeypatch) -> None:
        _flags(monkeypatch)
        route, seen, _versions = _route(monkeypatch)

        async def gone(*_a: Any, **_k: Any) -> Any:
            return None

        monkeypatch.setattr(route.store, "get_version", gone)
        await route._record_test_failures(ORG, MEMBER, SKILL, _suite("extra_write"), None)
        assert seen == []

    async def test_the_test_writer_with_the_flag_off_opens_no_session(self, monkeypatch) -> None:
        route, seen, versions = _route(monkeypatch)
        opened: list[Any] = []
        monkeypatch.setattr(route, "_tenant_session", lambda *a, **k: opened.append(a))
        for studio, repair in (("*", ""), ("", "*")):
            _flags(monkeypatch, studio=studio, repair=repair)
            await route._record_test_failures(ORG, MEMBER, SKILL, _suite("missing_write"), None)
        assert seen == [] and versions == [] and opened == []

    async def test_a_test_store_fault_is_swallowed(self, monkeypatch) -> None:
        """Verifier P3-4: the warning of the test writer names the class of a
        fault, never its text. Mutation: log ``str(exc)`` -> red."""
        _flags(monkeypatch)
        route, seen, _versions = _route(monkeypatch, fail=RuntimeError(WORDS))
        lines: list[tuple[str, dict[str, Any]]] = []
        for level in ("debug", "info", "warning", "error", "exception"):
            monkeypatch.setattr(route._log, level, lambda event, *a, **kw: lines.append(
                (str(event), {**kw, "a": a})))
        await route._record_test_failures(
            ORG, MEMBER, SKILL, _suite("missing_write", "call_cap"), None)
        assert len(seen) == 1, "a fault that is no refusal ends the write"
        assert any(kw.get("error_type") == "RuntimeError" for _e, kw in lines), lines
        _assert_no_words(lines)
        route, seen, _versions = _route(monkeypatch, fail=skill_runs.SkillRunError("x", "y"))
        await route._record_test_failures(
            ORG, MEMBER, SKILL, _suite("missing_write", "call_cap"), None)
        assert len(seen) == 2, "a refusal skips its row only"


def _named(name: str, body: Any) -> Any:
    async def tool(title: str = "") -> Any:
        return await body()

    tool.__name__ = name
    return tool


# ── The fakes of the store ──────────────────────────────────────────────────


@contextlib.contextmanager
def _bound(org: str = ORG) -> Iterator[None]:
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(org)
    try:
        yield
    finally:
        release_tenant(token)


def _store(monkeypatch, *, fail: dict[str, BaseException] | None = None,
           ) -> tuple[list[dict[str, Any]], list[Any]]:
    """A fake ``tenant_session`` and ``record_live``. Each call is kept."""
    import acb_common.db as db

    seen: list[dict[str, Any]] = []
    opened: list[Any] = []

    @contextlib.asynccontextmanager
    async def session(*a: Any, **_k: Any) -> Any:
        opened.append(a)
        yield SimpleNamespace()

    async def record_live(*args: Any, **kw: Any) -> Any:
        seen.append({"args": args, "kw": kw})
        if fail and args[2] in fail:
            raise fail[args[2]]
        return None

    monkeypatch.setattr(db, "tenant_session", session)
    monkeypatch.setattr(skill_runs, "record_live", record_live)
    return seen, opened


def _log_spy(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    lines: list[tuple[str, dict[str, Any]]] = []
    for level in ("debug", "info", "warning", "error"):
        monkeypatch.setattr(ti._log, level,
                            lambda event, *a, **kw: lines.append((str(event), {**kw, "a": a})))
    return lines


def _assert_no_words(lines: list[tuple[str, dict[str, Any]]]) -> None:
    for bit in SECRET_BITS:
        assert bit not in repr(lines), bit


_BODY = {"instructions": "Make a task.", "actions": ["tool:create_task"]}


def _suite(*reasons: str) -> skill_tests.SuiteResult:
    results = tuple(
        skill_tests.TestResult(
            test_id=f"t-{r}", passed=(r == "passed") if r not in ("not_run",) else None,
            reason=r, detail=f"The turn did not hold the write {r}.",
            writes=("tool:create_task", "tool:create_task", "Call Asha"),
            credits=Decimal("1.5"), run_id=f"r-{r}")
        for r in reasons)
    return skill_tests.SuiteResult(skill_id=SKILL, version=2, content_hash="c" * 64,
                                   results=results)


def _route(monkeypatch, *, fail: BaseException | None = None) -> tuple[Any, list[Any], list[int]]:
    from gateway.routes.studio import skills as route

    seen: list[dict[str, Any]] = []
    versions: list[int] = []

    @contextlib.asynccontextmanager
    async def session(*_a: Any, **_k: Any) -> Any:
        yield SimpleNamespace()

    async def get_version(_db: Any, member: str, skill_id: str, version: int) -> Any:
        assert (member, skill_id) == (MEMBER, SKILL)
        versions.append(version)
        return SimpleNamespace(version=version, body=dict(_BODY))

    async def record_test(*args: Any, **kw: Any) -> Any:
        seen.append({"args": args, "kw": kw})
        if fail is not None:
            raise fail
        return None

    monkeypatch.setattr(route, "_tenant_session", session)
    monkeypatch.setattr(route.store, "get_version", get_version)
    monkeypatch.setattr(skill_runs, "record_test", record_test)
    return route, seen, versions


def test_the_suite_fake_matches_the_result_shape() -> None:
    """The fake of the suite keeps the fields of ``TestResult``."""
    names = {f.name for f in dataclasses.fields(skill_tests.TestResult)}
    assert {"test_id", "passed", "reason", "detail", "writes", "credits", "run_id"} <= names


# ── Review round 1: the best-effort parts are fenced (R7) ───────────────────


@pytest.mark.parametrize("broken", ["annotated_read_only", "is_refusal", "saw"])
async def test_a_fault_of_the_note_never_changes_a_good_call(monkeypatch, writes_are,
                                                             broken) -> None:
    """Verifier P2-A: a fault inside ``_note_skill_call`` after a call that
    worked leaves the answer of the tool as it was. The guard must not answer
    "The tool failed". Mutation: narrow its ``except Exception`` -> red."""
    def boom(*_a: Any, **_k: Any) -> Any:
        raise LookupError(WORDS)

    if broken == "annotated_read_only":
        monkeypatch.setattr("acb_skills.subagent_contract.annotated_read_only", boom)
    elif broken == "is_refusal":
        monkeypatch.setattr("acb_common.tool_refusals.is_refusal", boom)
    else:
        monkeypatch.setattr(ti.SkillRunNotes, "saw", boom)
    lines = _log_spy(monkeypatch)
    notes = _notes()
    with _run(notes):
        assert await _tool("create_task", _ok)(title="x") == "Done."
    assert notes.row_of(SLUG)[:2] == (True, "passed")
    assert any(kw.get("error_type") == "LookupError" for _e, kw in lines)
    _assert_no_words(lines)


async def test_a_fault_of_the_notes_never_stops_the_apply(monkeypatch) -> None:
    """Verifier P2-A: a fault inside ``_skill_run_notes`` gives no notes,
    and ``_apply_tool_packs`` still loads the skill pack, so the turn goes
    on. Mutation: narrow its ``except Exception`` -> red."""
    import dataclasses as dc

    from orchestrator import executor

    from tests.unit.test_skill_packs import SESSION as PACK_SESSION
    from tests.unit.test_skill_packs import _pack, _tenant
    from tests.unit.test_skill_packs import _turn as _pack_turn

    found = _pack(actions=[])

    async def _with(turn: Any, **_k: Any) -> Any:
        facts = ti._SKILL_FACTS.get()
        assert facts is not None, "the apply binds the facts"
        facts[found.slug] = _loaded(found.skill_id)
        return dc.replace(turn, skills=(found,))

    def boom(*_a: Any, **_k: Any) -> Any:
        raise LookupError(WORDS)

    monkeypatch.setattr(ti, "_turn_with_skills", _with)
    monkeypatch.setattr(ti, "_load_disabled_skill_families", lambda _name: frozenset())
    monkeypatch.setattr(ti, "_pack_base_count", lambda *_a, **_k: 40)
    monkeypatch.setattr(ti, "_pack_base_names", lambda *_a, **_k: set())
    monkeypatch.setattr(executor, "_verified_run_member", boom)
    agent = SimpleNamespace(default_options={"tools": [], "instructions": "BASE"})
    with _tenant():
        applied = await ti._apply_tool_packs(
            [agent], _pack_turn("use the quote helper", ()),
            {"session_id": PACK_SESSION, "run_id": "run-1"}, allowed=frozenset(),
            agent_name="orchestrator", tool_scope=None, agent_config=None, bullets={})
    assert applied is not None and applied.record.skills == (found.slug,)
    assert applied.skill_notes is None
    assert ti.SKILL_RUN_NOTES_KEY not in applied.context()


def test_schedule_never_raises(monkeypatch) -> None:
    """A fault of the start of the task gives None, never a raise."""
    def no_loop() -> Any:
        raise RuntimeError(WORDS)

    monkeypatch.setattr(ti.asyncio, "get_running_loop", no_loop)
    applied = ti.PackApply(builds=(), record=None, run_key=RUN_KEY, skill_notes=_notes())
    assert ti.schedule_skill_runs(applied) is None
    assert ti.schedule_skill_runs(object()) is None


def test_the_finally_holds_each_exception_of_the_schedule() -> None:
    """The call in the ``finally`` of ``run_agent_stream`` sits inside
    ``contextlib.suppress(Exception)``, so a fault never replaces the end of
    the run, and a ``CancelledError`` still passes. Mutation: call it with no
    suppress, or suppress ``BaseException`` -> red."""
    import ast

    src = (_ROOT / "apps/services/orchestrator/orchestrator/executor.py").read_text(
        encoding="utf-8")
    found = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.With):
            continue
        items = [ast.unparse(i.context_expr) for i in node.items]
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                 and ast.unparse(n.func) == "schedule_skill_runs"]
        if calls:
            found.append(items)
    assert found == [["contextlib.suppress(Exception)"]], found


def test_a_child_context_drops_the_notes() -> None:
    from orchestrator import executor

    assert executor._without_pack_keys()[ti.SKILL_RUN_NOTES_KEY] is None


def test_a_skill_with_no_pack_tool_is_unobserved(monkeypatch) -> None:
    """Review P2-C: a skill whose tools no pack tool of the run holds gets
    the code word ``unobserved`` in the detail of its passed row. A failed
    row keeps its own detail. Mutation: drop the ``pack_tools`` test -> red."""
    from acb_common.db import bind_tenant, release_tenant
    from orchestrator import executor

    monkeypatch.setattr(executor, "_verified_run_member", lambda: MEMBER)
    facts = {SLUG: _loaded(), SLUG2: _loaded(SKILL2, send_email="tool:send_email")}
    kept = [SimpleNamespace(slug=SLUG), SimpleNamespace(slug=SLUG2)]
    token = bind_tenant(ORG)
    try:
        notes = ti._skill_run_notes(facts, kept, {"run_id": "run-1"}, RUN_KEY, words=WORDS,
                                    pack_tools={"create_task", "read_email"})
    finally:
        release_tenant(token)
    assert notes is not None and notes.unobserved == {SLUG2}
    assert notes.row_of(SLUG) == (True, "passed", "", [])
    assert notes.row_of(SLUG2) == (True, "passed", ti.UNOBSERVED, [])
    notes.saw("send_email", error="ToolBoom")
    assert notes.row_of(SLUG2)[:3] == (False, "tool_error", "send_email: ToolBoom")


# ══ F12c: the repair, the approve and the undo ══════════════════════════════
#
# The five fences of the owner (F12.md "Done when"), hermetic. The routes run
# in a FastAPI app with the real router. The store and the store of skill runs
# are fakes in memory. ``skill_tests.run_suite`` is REAL, with a fake runner
# that sends each call through the real ``HoldAndRecord`` and a fake meter, as
# the F4b tests do. No model call and no credit read happen. The R8 half is
# ``tests/unit/test_skill_repair_r8.py``.
#
# **The mutations (R7).**
#
# * Drop the ``check_contract`` call of the repair route:
#   ``test_a_repair_that_adds_an_action_is_refused`` and
#   ``test_a_repair_that_changes_another_key_is_refused`` go red.
# * Compare only ``actions`` in ``check_contract``:
#   ``test_a_repair_that_changes_another_key_is_refused`` goes red.
# * Drop the class read of ``check_contract``:
#   ``test_the_class_check_reads_the_catalog`` goes red.
# * Call the approve route on each decision:
#   ``test_only_approved_calls_the_approve_route`` goes red.
# * Drop check 5: ``test_a_second_repair_of_one_run_is_refused`` goes red.
# * Compare ``content_hash`` in place of ``body_key``, or drop check 8:
#   ``test_a_repeat_of_a_failed_body_is_refused`` goes red.
# * Offer version 2 on the input run alone, or read the text of the model:
#   ``test_no_card_while_a_test_fails`` and ``test_no_card_while_the_input_fails``
#   go red.
# * Drop check 11: ``test_cap_unset_spends_nothing`` goes red.

R_ORG = "11111111-1111-4111-8111-111111111111"
ANA = "ana@f12c.test"
BEN = "ben@f12c.test"
R_SID = "22222222-3333-4444-8666-777777777777"
_T0 = datetime(2026, 11, 20, tzinfo=UTC)
R_INPUT = "Call Asha on 555 0100 about the Acme lead"
R_BITS = ("Asha", "555", "0100")
R_BASE = {
    "description": "Use it for leads.", "instructions": "Add the lead.",
    "actions": ["object:lead.create", "object:lead.list"],
    "tests": [{"id": "t1", "input": "Add Ravi from Acme",
               "expect": [{"action": "object:lead.create", "fields": {"data.company": "Acme"}}]}],
}
R_CATALOG = [{"id": "object:lead.create", "class": "B"}, {"id": "object:lead.list", "class": "A"},
             {"id": "tool:send_email", "class": "C"}]


def _lead_tool() -> FunctionTool:
    async def studio_record_create(package_id: str = "", object: str = "",
                                   data: dict[str, Any] | None = None) -> str:
        raise AssertionError("a held write ran")

    return FunctionTool(func=studio_record_create, name="studio_record_create", description="d")


def _mail_tool() -> FunctionTool:
    async def send_email(to: str = "", body: str = "") -> str:
        raise AssertionError("a held write ran")

    return FunctionTool(func=send_email, name="send_email", description="d")


_LEAD = (_lead_tool(), {"package_id": "p1", "object": "lead", "data": {"company": "Acme"}})


class _RepairRunner:
    """A fake turn of the dry run. It arms and loads the record, and sends
    the calls for each message through the real ``HoldAndRecord``. Its
    answer text always says that all is well: the end-state rule must never
    read it."""

    def __init__(self, calls: dict[str, list[Any]] | None = None) -> None:
        self.calls = calls or {}
        self.messages: list[str] = []

    async def __call__(self, message: str, *, run_id: str, thread_id: str, org: str,
                       member: str) -> str:
        record = current_dry_run()
        assert record is not None and org == R_ORG and member == ANA
        self.messages.append(message)
        record.armed = record.loaded = True

        async def never() -> None:
            raise AssertionError("a held call ran")

        for function, args in self.calls.get(message, [_LEAD]):
            ctx = SimpleNamespace(result=None, function=function, arguments=args)
            await HoldAndRecord().process(ctx, never)
        return "All tests passed. Everything is fine. Offer version 2."


def _run_row(**over: Any) -> skill_runs.SkillRun:
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()), "skill_id": R_SID, "version": 2, "kind": "live",
        "passed": False, "reason": "tool_error", "detail": "create_task: ToolBoom",
        "body_key": skill_repair.body_key(R_BASE), "writes": (),
        "input_hash": skill_runs.input_hash(R_ORG, R_INPUT), "repair_of": None,
        "test_id": None, "run_id": "run-live-1", "thread_id": "thread-1", "credits": None,
        "created_by": ANA, "created_at": _T0}
    values.update(over)
    return skill_runs.SkillRun(**values)


class _Repairs:
    """The fake world of the repair routes: one skill of ANA, its versions, the
    rows of the store of skill runs, the chat of ANA, and the spies."""

    def __init__(self) -> None:
        self.state = "published"
        self.versions: dict[int, dict[str, Any]] = {
            1: {**R_BASE, "instructions": "Add it."}, 2: copy.deepcopy(R_BASE)}
        self.rows: list[skill_runs.SkillRun] = []
        self.messages: list[str] = [R_INPUT, "Hello"]
        self.runner = _RepairRunner()
        self.meter_reads = 0
        self.catalog_reads = 0
        self.added: list[dict[str, Any]] = []
        self.moves: list[str] = []
        self.proposals: list[dict[str, Any]] = []
        self.redis: Any = None

    @property
    def current(self) -> int:
        return max(self.versions)

    def skill(self) -> store.Skill:
        return store.Skill(id=R_SID, name="Leads", slug="leads", description="d",
                           owner_email=ANA, visibility="private", state=self.state,
                           current_version=self.current, created_at=_T0, updated_at=_T0)

    def version(self, number: int | None) -> store.Version | None:
        number = self.current if number is None else number
        body = self.versions.get(number)
        if body is None:
            return None
        return store.Version(skill_id=R_SID, version=number, body=copy.deepcopy(body),
                             content_hash=store.content_hash(body), created_by=ANA,
                             created_at=_T0)


@pytest.fixture
def repairs(monkeypatch) -> _Repairs:  # noqa: C901 - one fake world
    from gateway.jobs import headless_turn
    from gateway.routes.studio import actions as actions_route
    from gateway.routes.studio import skills as route

    w = _Repairs()
    settings = get_settings()
    for key in ("studio_orgs", "studio_skill_tests_orgs", "studio_skill_repair_orgs"):
        monkeypatch.setattr(settings, key, "*", raising=False)
    monkeypatch.setattr(settings, "studio_skill_tests_cap_credits", "5", raising=False)

    @contextlib.asynccontextmanager
    async def session(*_a: Any, **_k: Any) -> Any:
        yield SimpleNamespace()

    async def catalog_rows() -> list[dict[str, Any]]:
        w.catalog_reads += 1
        return [dict(r) for r in R_CATALOG]

    def mine(member: str, sid: str) -> bool:
        return member.strip().lower() == ANA and sid == R_SID

    async def get_skill(_db: Any, member: str, sid: str, *, for_update: bool = False) -> Any:
        return w.skill() if mine(member, sid) else None

    async def get_version(_db: Any, member: str, sid: str, version: int | None = None) -> Any:
        return w.version(version) if mine(member, sid) else None

    async def add_version(_db: Any, member: str, sid: str, body: Any) -> Any:
        assert mine(member, sid)
        store.check_body(body)
        w.added.append(copy.deepcopy(dict(body)))
        w.versions[w.current + 1] = copy.deepcopy(dict(body))
        return w.version(None)

    async def set_state(_db: Any, member: str, sid: str, state: str) -> bool:
        w.moves.append(state)
        w.state = state
        return True

    async def load_skill(org: str, member: str, sid: str, version: int | None) -> Any:
        assert (org, member, sid) == (R_ORG, ANA, R_SID)
        return w.skill(), w.version(version)

    async def get_run(_db: Any, rid: str) -> Any:
        return next((r for r in w.rows if r.id == rid), None)

    async def proposal_for(_db: Any, repair_of: str) -> Any:
        return next((r for r in w.rows if r.kind == "proposal" and r.repair_of == repair_of),
                    None)

    async def repeats(_db: Any, sid: str, key: str) -> bool:
        return any(r.skill_id == sid and r.body_key == key and not r.passed
                   and r.reason in skill_runs.REPEAT_REASONS for r in w.rows)

    async def record_proposal(_db: Any, member: str, sid: str, repair_of: str,
                              **kw: Any) -> Any:
        if any(r.kind == "proposal" and r.repair_of == repair_of for r in w.rows):
            raise skill_runs.AlreadyRepaired()
        failed = next(r for r in w.rows if r.id == repair_of)
        w.proposals.append({"member": member, "sid": sid, "repair_of": repair_of, **kw})
        row = _run_row(kind="proposal", passed=kw["passed"], reason=kw["reason"],
                       detail=kw.get("detail", ""), body_key=kw["body_key"],
                       repair_of=repair_of, version=failed.version,
                       writes=tuple(kw.get("writes") or ()), run_id=kw.get("run_id"),
                       thread_id=None, credits=kw.get("credits"))
        w.rows.append(row)
        return row

    async def record_live(_db: Any, member: str, sid: str, **kw: Any) -> Any:
        if any(r.kind == "live" and not r.passed and r.run_id == kw["run_id"] for r in w.rows):
            raise skill_runs.DuplicateFailure()
        row = _run_row(reason=kw["reason"], run_id=kw["run_id"], thread_id=kw["thread_id"],
                       version=kw["version"], body_key=kw["body_key"],
                       input_hash=skill_runs.input_hash(R_ORG, kw["input_text"]))
        w.rows.append(row)
        return row

    async def newest_passed_live(_db: Any, member: str, sid: str) -> Any:
        found = [r for r in w.rows if r.kind == "live" and r.passed and r.created_by == member]
        return found[-1] if found else None

    async def failed_live_of_run(_db: Any, sid: str, run_id: str) -> Any:
        return next((r for r in w.rows if r.kind == "live" and not r.passed
                     and r.run_id == run_id), None)

    async def failed_input(_db: Any, member: str, thread_id: Any, want: Any) -> Any:
        if member != ANA or thread_id != "thread-1":
            return None
        return next((m for m in w.messages if skill_runs.input_hash(R_ORG, m) == want), None)

    async def runner(message: str, **kw: Any) -> Any:
        return await w.runner(message, **kw)

    async def meter(org: str, member: str, run_id: str) -> Decimal:
        w.meter_reads += 1
        return Decimal("0.5")

    monkeypatch.setattr(route, "_tenant_session", session)
    monkeypatch.setattr(actions_route, "catalog_rows", catalog_rows)
    monkeypatch.setattr(route.store, "get_skill", get_skill)
    monkeypatch.setattr(route.store, "get_version", get_version)
    monkeypatch.setattr(route.store, "add_version", add_version)
    monkeypatch.setattr(route.store, "set_state", set_state)
    monkeypatch.setattr(skill_tests, "_load_skill", load_skill)
    w.redis = SlotRedis()
    monkeypatch.setattr(skill_tests, "get_tenant_redis", lambda: TenantRedis(w.redis))
    monkeypatch.setattr(skill_tests, "agent_runner", runner)
    monkeypatch.setattr(headless_turn, "console_meter", meter)
    for name, fn in (("get_run", get_run), ("proposal_for", proposal_for),
                     ("repeats_a_failed_body", repeats), ("record_proposal", record_proposal),
                     ("record_live", record_live), ("newest_passed_live", newest_passed_live),
                     ("failed_live_of_run", failed_live_of_run)):
        monkeypatch.setattr(skill_runs, name, fn)
    monkeypatch.setattr(skill_repair, "failed_input", failed_input)
    return w


def _app(email: str = ANA, org: str = R_ORG) -> TestClient:
    from acb_auth import UserContext, UserRole, get_current_user
    from acb_auth.access import build_access
    from gateway.routes.studio import actions as actions_route

    app = FastAPI()
    app.include_router(actions_route.router)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(["feature:studio"]),
        organization_id=org)
    return TestClient(app)


def _failed(w: _Repairs, **over: Any) -> skill_runs.SkillRun:
    row = _run_row(**over)
    w.rows.append(row)
    return row


def _repair(w: _Repairs, failure: str | None, *, email: str = ANA, **body: Any) -> Any:
    payload = {"instructions": "Add the lead. Always fill the company.", **body}
    if failure is not None:
        payload["failure_id"] = failure
    return _app(email).post(f"/studio/skills/{R_SID}/repairs", json=payload)


def _spent(w: _Repairs) -> tuple[int, int]:
    return len(w.runner.messages), w.meter_reads


# -- the pure contract --------------------------------------------------------

_CLASSES = {r["id"]: r["class"] for r in R_CATALOG}


def test_the_contract_takes_new_instructions_and_a_removed_action() -> None:
    proposed = skill_repair.proposed_body(R_BASE, "Add the lead, then stop.",
                                          ["object:lead.list"])
    assert proposed["actions"] == ["object:lead.create"]
    assert proposed["tests"] == R_BASE["tests"] and proposed["description"] == R_BASE["description"]
    assert skill_repair.check_contract(R_BASE, proposed, _CLASSES) is None
    with pytest.raises(skill_repair.RepairError) as bad:
        skill_repair.proposed_body(R_BASE, "", ["tool:send_email"])
    assert bad.value.code == "not_an_action"


def test_a_change_of_white_space_alone_is_no_change() -> None:
    same = skill_repair.proposed_body(R_BASE, "  Add   the lead. ", [])
    assert skill_repair.check_contract(R_BASE, same, _CLASSES) == "no_change"


@pytest.mark.parametrize("key", ["description", "tests", "cards", "knowledge"])
def test_each_other_key_is_fixed(key: str) -> None:
    changed = {**copy.deepcopy(R_BASE), "instructions": "New."}
    changed[key] = "changed" if key != "tests" else []
    assert skill_repair.check_contract(R_BASE, changed, _CLASSES) == "widened"
    gone = {k: v for k, v in R_BASE.items() if k != "description"}
    assert skill_repair.check_contract(R_BASE, {**gone, "instructions": "x"}, _CLASSES) == "widened"


def test_an_unknown_class_ranks_at_the_top() -> None:
    assert skill_repair.class_rank("A") < skill_repair.class_rank("B") \
        < skill_repair.class_rank("C") < skill_repair.class_rank("X")
    assert skill_repair.class_rank("?") == skill_repair.class_rank("X")


# -- fence 1: no widening -----------------------------------------------------


def test_a_repair_that_adds_an_action_is_refused(repairs, monkeypatch) -> None:
    """The route builds the proposal so that it cannot add an action. A
    builder that adds one still stops at ``check_contract``, with no runner
    call. Mutation: drop the subset check or the route's contract call -> red."""
    added = {**copy.deepcopy(R_BASE), "actions": [*R_BASE["actions"], "tool:send_email"],
             "instructions": "Send a mail too."}
    assert skill_repair.check_contract(R_BASE, added, _CLASSES) == "widened"
    failed = _failed(repairs)
    removed = _repair(repairs, failed.id, remove_actions=["tool:send_email"])
    assert removed.status_code == 422 and removed.json()["detail"]["code"] == "not_an_action"
    monkeypatch.setattr(skill_repair, "proposed_body", lambda *_a: copy.deepcopy(added))
    answer = _repair(repairs, failed.id)
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == "widened"
    assert answer.json()["detail"]["message"] == skill_repair.WORDS["widened"]
    assert _spent(repairs) == (0, 0) and repairs.proposals == [] and repairs.added == []


def test_a_repair_that_changes_another_key_is_refused(repairs, monkeypatch) -> None:
    """Mutation: compare only ``actions`` -> the description change passes -> red."""
    changed = {**copy.deepcopy(R_BASE), "description": "Use it for each mail.",
               "instructions": "New words."}
    assert skill_repair.check_contract(R_BASE, changed, _CLASSES) == "widened"
    failed = _failed(repairs)
    monkeypatch.setattr(skill_repair, "proposed_body", lambda *_a: copy.deepcopy(changed))
    answer = _repair(repairs, failed.id)
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == "widened"
    assert _spent(repairs) == (0, 0) and repairs.proposals == []


def test_the_class_check_reads_the_catalog(repairs) -> None:
    """The route reads the catalog once, before its session, and the
    contract reads the class of each action. Mutation: remove the class read
    -> no read -> red."""
    class Spy(dict):
        def __init__(self, *a: Any) -> None:
            super().__init__(*a)
            self.read: list[str] = []

        def get(self, key: Any, default: Any = None) -> Any:
            self.read.append(key)
            return super().get(key, default)

    spy = Spy(_CLASSES)
    proposed = skill_repair.proposed_body(R_BASE, "New words.", [])
    assert skill_repair.check_contract(R_BASE, proposed, spy) is None
    assert set(spy.read) == set(R_BASE["actions"])
    # A class that the catalog ranks higher than each class of the base widens.
    wide = Spy({"object:lead.create": "B", "object:lead.list": "A"})
    narrow = {**R_BASE, "actions": ["object:lead.list"]}
    assert skill_repair.check_contract(narrow, {**narrow, "instructions": "x"}, wide) is None
    failed = _failed(repairs)
    assert _repair(repairs, failed.id).status_code == 200
    assert repairs.catalog_reads == 1


# -- fence 3: one repair for each failed run ----------------------------------


def test_a_second_repair_of_one_run_is_refused(repairs) -> None:
    """Mutation: drop check 5 -> the second repair runs -> red."""
    failed = _failed(repairs)
    first = _repair(repairs, failed.id)
    assert first.status_code == 200, first.text
    spent = _spent(repairs)
    second = _repair(repairs, failed.id, instructions="Another fix of the lead.")
    assert second.status_code == 409 and second.json()["detail"]["code"] == "already_repaired"
    assert _spent(repairs) == spent and len(repairs.proposals) == 1


def test_a_proposal_that_failed_still_uses_the_one_repair(repairs) -> None:
    repairs.runner = _RepairRunner({R_INPUT: [(_mail_tool(), {"to": "x"})]})
    failed = _failed(repairs)
    assert _repair(repairs, failed.id).json()["passed"] is False
    again = _repair(repairs, failed.id, instructions="Another fix of the lead.")
    assert again.json()["detail"]["code"] == "already_repaired"


def test_a_second_it_was_wrong_of_one_run_is_refused(repairs) -> None:
    repairs.rows.append(_run_row(passed=True, reason="passed", run_id="run-ok"))
    first = _app().post(f"/studio/skills/{R_SID}/repairs", json={
        "said_wrong": True, "instructions": "Add the lead. Always fill the company."})
    assert first.status_code == 200, first.text
    [wrong] = [r for r in repairs.rows if r.reason == "member_said_wrong"]
    assert wrong.run_id == "run-ok" and repairs.proposals[0]["repair_of"] == wrong.id
    spent = _spent(repairs)
    second = _app().post(f"/studio/skills/{R_SID}/repairs", json={
        "said_wrong": True, "instructions": "Another fix of the lead."})
    assert second.status_code == 409 and second.json()["detail"]["code"] == "already_repaired"
    assert _spent(repairs) == spent


# -- fence 4: no repeat of a failed body --------------------------------------


@pytest.mark.parametrize("kind", ["live", "test", "proposal"])
def test_a_repeat_of_a_failed_body_is_refused(repairs, kind: str) -> None:
    """A body that failed before, by its repair key, as a live, a test or a
    proposal row. A body that differs only in white space is a repeat too.
    Mutation: compare ``content_hash`` -> the white space passes -> red."""
    failed = _failed(repairs)
    tried = skill_repair.proposed_body(R_BASE, "Add the lead. Fill the company.", [])
    reason = {"live": "tool_error", "test": "missing_write", "proposal": "extra_write"}[kind]
    extra = {"test_id": "t1"} if kind == "test" else {}
    if kind == "proposal":
        extra = {"repair_of": str(uuid.uuid4())}
    repairs.rows.append(_run_row(kind=kind, reason=reason, body_key=skill_repair.body_key(tried),
                                 run_id=f"run-{kind}", **extra))
    answer = _repair(repairs, failed.id, instructions="Add  the lead.\n Fill the   company.")
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "repeat_change"
    assert _spent(repairs) == (0, 0) and repairs.proposals == []


def test_a_stop_of_the_platform_is_no_repeat(repairs) -> None:
    """D-F12-13: ``cap_reached`` and ``run_failed`` are no fault of the skill."""
    failed = _failed(repairs)
    tried = skill_repair.proposed_body(R_BASE, "Add the lead. Fill the company.", [])
    for reason in ("cap_reached", "run_failed"):
        repairs.rows.append(_run_row(kind="proposal", reason=reason, repair_of=str(uuid.uuid4()),
                                     body_key=skill_repair.body_key(tried)))
    answer = _repair(repairs, failed.id, instructions="Add the lead. Fill the company.")
    assert answer.status_code == 200, answer.text


# -- fence 5: the end-state rule ----------------------------------------------


def test_a_passing_try_offers_the_change(repairs) -> None:
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id)
    out = answer.json()
    assert answer.status_code == 200 and out["passed"] is True and out["tests"] == 1
    assert out["next_version"] == 3 and out["instructions"].startswith("Add the lead.")
    assert out["message"].startswith("I tried version 3 on your 1 test")
    assert repairs.runner.messages == ["Add Ravi from Acme", R_INPUT]
    assert repairs.proposals[0]["passed"] is True and repairs.added == []


def test_no_card_while_a_test_fails(repairs) -> None:
    """The test run holds an extra write. The text of the model says that all
    is well. No change goes back, so no card can show. Mutation: offer on the
    input run alone, or read the text -> red."""
    repairs.runner = _RepairRunner({"Add Ravi from Acme": [_LEAD, _LEAD]})
    failed = _failed(repairs)
    out = _repair(repairs, failed.id).json()
    assert out["passed"] is False and out["reason"] == "extra_write"
    assert "instructions" not in out and "next_version" not in out
    assert out["message"] == skill_repair.WORDS["failed"].format(next=3)
    assert repairs.proposals[0]["passed"] is False and repairs.added == []


def test_no_card_while_the_input_fails(repairs) -> None:
    """The failed input calls a tool that the skill does not hold."""
    repairs.runner = _RepairRunner({R_INPUT: [(_mail_tool(), {"to": "x", "body": "y"})]})
    failed = _failed(repairs)
    out = _repair(repairs, failed.id).json()
    assert out["passed"] is False and out["reason"] == "other_action"
    assert "instructions" not in out
    [result] = [r for r in out["results"] if r["test_id"] == "input-1"]
    assert result["words"] == skill_repair.WORDS["other_action"]


def test_the_outcome_reads_the_door_never_the_text() -> None:
    import inspect as _inspect

    assert list(_inspect.signature(skill_repair.outcome).parameters) == ["result"]
    good = skill_tests.TestResult(test_id="t1", passed=True, reason="passed")
    bad = skill_tests.TestResult(test_id="input-1", passed=False, reason="invalid_call")
    suite = skill_tests.SuiteResult(skill_id=R_SID, version=2, content_hash="c",
                                    results=(good, bad), passed=True)
    found = skill_repair.outcome(suite)
    assert found.passed is False and found.reason == "invalid_call"


def test_the_base_tests_run(monkeypatch) -> None:
    """``run_suite`` gets the tests of the base version, also when the
    proposed body holds others."""
    seen: dict[str, Any] = {}

    async def fake(**kw: Any) -> Any:
        seen.update(kw)
        return "result"

    monkeypatch.setattr(skill_tests, "run_suite", fake)
    base_tests = skill_tests.parse_tests(R_BASE)
    proposed = {**R_BASE, "tests": [{"id": "weak", "input": "x", "expect": []}]}
    got = asyncio.run(skill_repair.try_proposal(
        org=R_ORG, member=ANA, skill_id=R_SID, proposed=proposed, base_tests=base_tests,
        input_text=R_INPUT, runner=lambda *a, **k: None, meter=lambda *a: None))
    assert got == "result" and seen["tests"] == base_tests and seen["inputs"] == (R_INPUT,)


def test_cap_unset_spends_nothing(repairs, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_skill_tests_cap_credits", "", raising=False)
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "cap_unset"
    assert _spent(repairs) == (0, 0) and repairs.proposals == []


@pytest.mark.parametrize(("setup", "code"), [
    ("moved", "skill_changed"), ("gone", "input_gone"), ("long", "input_too_long"),
    ("tests", "too_many_tests")])
def test_the_checks_refuse_before_any_spend(repairs, setup: str, code: str) -> None:
    failed = _failed(repairs, version=1 if setup == "moved" else 2)
    if setup == "gone":
        repairs.messages = ["Hello"]
    if setup == "long":
        long_text = "x" * 2001
        repairs.messages = [long_text]
        repairs.rows[-1] = failed = _run_row(id=failed.id,
                                             input_hash=skill_runs.input_hash(R_ORG, long_text))
    if setup == "tests":
        repairs.versions[2]["tests"] = [
            {"id": f"t{i}", "input": f"Add {i}", "expect": []} for i in range(5)]
    answer = _repair(repairs, failed.id)
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == code, answer.text
    assert answer.json()["detail"]["message"] == skill_repair.WORDS[code]
    assert _spent(repairs) == (0, 0) and repairs.proposals == []


# -- the identity, the flag and the input -------------------------------------


def test_a_member_cannot_repair_the_skill_or_the_run_of_another(repairs) -> None:
    """Another member's skill is the plain 404. On the caller's own skill, a
    row that cannot start a repair gives 404 ``no_failed_run``, and a row of
    another member gives the SAME answer as an id that does not exist, so the
    answer tells nothing about it."""
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id, email=BEN)
    assert (answer.status_code, answer.json()["detail"]) == (404, "Not found")
    nobody = _repair(repairs, str(uuid.uuid4()))
    assert nobody.status_code == 404
    assert nobody.json()["detail"] == {"code": "no_failed_run",
                                       "message": skill_repair.WORDS["no_failed_run"]}
    other = _failed(repairs, created_by=BEN, run_id="run-ben")
    passed = _failed(repairs, passed=True, reason="passed", run_id="run-ok")
    test_row = _failed(repairs, kind="test", reason="missing_write", test_id="t1",
                       run_id="run-t")
    proposal = _failed(repairs, kind="proposal", reason="extra_write",
                       repair_of=str(uuid.uuid4()), run_id="run-p")
    for row in (other, passed, test_row, proposal):
        got = _repair(repairs, row.id)
        assert (got.status_code, got.json()) == (404, nobody.json()), row.kind
    assert _spent(repairs) == (0, 0)


def test_it_was_wrong_with_no_recent_run_is_no_failed_run(repairs) -> None:
    answer = _app().post(f"/studio/skills/{R_SID}/repairs", json={
        "said_wrong": True, "instructions": "Add the lead. Always fill the company."})
    assert answer.status_code == 404 and answer.json()["detail"]["code"] == "no_failed_run"


def test_the_org_and_the_member_come_from_the_session(repairs) -> None:
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id, organization_id=str(uuid.uuid4()), member=BEN)
    assert answer.status_code == 200 and repairs.proposals[0]["member"] == ANA


def test_flag_off_answers_404_on_each_route(repairs, monkeypatch) -> None:
    failed = _failed(repairs)
    for key in ("studio_skill_repair_orgs", "studio_skill_tests_orgs", "studio_orgs"):
        monkeypatch.setattr(get_settings(), key, "", raising=False)
        c = _app()
        assert _repair(repairs, failed.id).status_code == 404, key
        assert c.post(f"/studio/skills/{R_SID}/repairs/{uuid.uuid4()}/approve",
                      json={"expected_version": 2}).status_code == 404, key
        if key != "studio_skill_tests_orgs":
            assert c.get(f"/studio/skills/{R_SID}/runs").status_code == 404, key
        monkeypatch.setattr(get_settings(), key, "*", raising=False)
    assert _spent(repairs) == (0, 0) and repairs.catalog_reads == 0


def test_no_input_text_is_kept(repairs, monkeypatch) -> None:
    """After a try with "Call Asha on 555 0100", no row, no log line and no
    answer holds a word of it. The writer refuses a ``writes`` item with a
    value."""
    from gateway.routes.studio import skills as route

    lines: list[Any] = []
    for level in ("info", "warning", "error"):
        monkeypatch.setattr(route._log, level, lambda event, *a, **kw: lines.append((event, kw)))
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id)
    assert answer.status_code == 200
    assert any(e == "studio.skill_repair.result" for e, _ in lines)
    for bit in R_BITS:
        assert bit not in answer.text, bit
        assert bit not in repr(repairs.proposals), bit
        assert bit not in repr(lines), bit
    with pytest.raises(skill_runs.SkillRunError):
        skill_runs.check_writes([{"action": "object:lead.create", "keys": ["Call Asha"]}])


def test_the_history_gives_codes_and_earlier_versions(repairs) -> None:
    repairs.rows.extend([
        _run_row(version=1, reason="card_declined", run_id="r1"),
        _run_row(version=2, reason="tool_error", run_id="r2"),
    ])

    async def history(_db: Any, member: str, sid: str) -> Any:
        return skill_runs.History(rows=tuple(reversed(repairs.rows)), others={"tool_error": 2})

    import gateway.studio.skill_runs as runs_mod
    runs_mod_history = runs_mod.history
    try:
        runs_mod.history = history
        out = _app().get(f"/studio/skills/{R_SID}/runs").json()
    finally:
        runs_mod.history = runs_mod_history
    assert [r["reason"] for r in out["runs"]] == ["tool_error", "card_declined"]
    assert out["runs"][0]["words"] == skill_repair.WORDS["tool_error"]
    assert out["others"] == {"tool_error": 2}
    assert out["earlier_versions"] == [{"version": 1, "instructions": "Add it.",
                                        "truncated": False}]
    assert _app(BEN).get(f"/studio/skills/{R_SID}/runs").status_code == 404


# -- the approve --------------------------------------------------------------


def _passed_proposal(repairs: _Repairs) -> dict[str, Any]:
    failed = _failed(repairs)
    out = _repair(repairs, failed.id, remove_actions=["object:lead.list"]).json()
    assert out["passed"] is True, out
    return out


def _approve(out: dict[str, Any], **over: Any) -> Any:
    body = {"expected_version": out["base_version"], "instructions": out["instructions"],
            "remove_actions": out["remove_actions"], **over}
    return _app().post(f"/studio/skills/{R_SID}/repairs/{out['proposal_id']}/approve",
                       json=body)


def test_the_approve_writes_one_version_and_keeps_the_state(repairs) -> None:
    out = _passed_proposal(repairs)
    answer = _approve(out)
    assert answer.status_code == 201, answer.text
    assert answer.json()["version"]["version"] == 3 and repairs.moves == []
    [written] = repairs.added
    assert written["actions"] == ["object:lead.create"] and written["tests"] == R_BASE["tests"]
    assert answer.json()["message"] == ("I saved version 3. The skill stays published. "
                                        "Say undo to go back.")
    again = _approve(out)
    assert again.status_code == 409 and len(repairs.added) == 1


def test_the_approve_refuses_a_body_that_is_not_the_proposal(repairs) -> None:
    out = _passed_proposal(repairs)
    for over in ({"instructions": "Another text."}, {"remove_actions": []},
                 {"expected_version": 1}):
        answer = _approve(out, **over)
        assert answer.status_code == 409, over
    assert repairs.added == []


def test_the_approve_refuses_a_proposal_that_failed(repairs) -> None:
    repairs.runner = _RepairRunner({R_INPUT: [(_mail_tool(), {"to": "x"})]})
    failed = _failed(repairs)
    _repair(repairs, failed.id)
    [proposal] = [r for r in repairs.rows if r.kind == "proposal"]
    answer = _app().post(f"/studio/skills/{R_SID}/repairs/{proposal.id}/approve", json={
        "expected_version": 2, "instructions": "Add the lead. Always fill the company."})
    assert answer.status_code == 409 and answer.json()["detail"]["code"] == "not_passed"
    assert repairs.added == []


def test_only_the_approve_route_writes_a_version_in_the_repair_path() -> None:
    """A source fence: in the repair path, only ``approve_skill_repair``
    calls ``add_version``, and none calls ``set_state``. Mutation: add a
    version in the repair route -> red."""
    src = (_ROOT / "apps/services/gateway/gateway/routes/studio/skills.py").read_text(
        encoding="utf-8")
    path = {"skill_run_history", "_the_failure", "_the_proposal", "_the_input",
            "repair_skill", "_dry_run_and_record", "approve_skill_repair"}
    calls: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in path:
            calls[node.name] = {ast.unparse(n.func).rsplit(".", 1)[-1]
                                for n in ast.walk(node) if isinstance(n, ast.Call)}
    assert set(calls) == path
    assert {n for n, c in calls.items() if "add_version" in c} == {"approve_skill_repair"}
    assert not any("set_state" in c for c in calls.values())
    repair_src = (_ROOT / "apps/services/gateway/gateway/studio/skill_repair.py").read_text(
        encoding="utf-8")
    assert "add_version" not in repair_src and "set_state" not in repair_src


# -- the chat tools -----------------------------------------------------------


class _ToolGateway:
    """The answers of the routes for the chat tools, by method and path."""

    def __init__(self, tried: dict[str, Any], *, status: int = 200) -> None:
        body = copy.deepcopy(R_BASE)
        self.read = {"skill": {"id": R_SID, "name": "Leads", "state": "published",
                               "current_version": 2},
                     "version": {"version": 2, "body": body,
                                 "content_hash": store.content_hash(body)}}
        self.tried, self.status = tried, status
        self.calls: list[tuple[str, str, Any]] = []

    async def send(self, method: str, path: str, body: Any, act: str = "", miss: str = "",
                   timeout: float = 15.0) -> Any:
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/runs"):
            return stools._Reply(404, {"detail": "Not found"})
        if method == "GET":
            return stools._Reply(200, self.read)
        if path.endswith("/repairs"):
            assert timeout == stools.REPAIR_WAIT_SECONDS
            return stools._Reply(self.status, self.tried)
        if path.endswith("/approve"):
            return stools._Reply(201, {"skill": {**self.read["skill"], "current_version": 3},
                                       "message": skill_repair.saved_words(3, "published")})
        return stools._Reply(201, {"skill": {**self.read["skill"], "state": "draft"},
                                   "version": {"version": 4}})


def _tried(**over: Any) -> dict[str, Any]:
    out = {"proposal_id": str(uuid.uuid4()), "base_version": 2, "next_version": 3,
           "passed": True, "tests": 1, "state": "published",
           "instructions": "Add the lead. Always fill the company.",
           "remove_actions": ["object:lead.list"], "same_writes": False,
           "message": skill_repair.passed_words(3, 1),
           "results": [{"test_id": "t1", "passed": True, "reason": "passed",
                        "words": "passed"}]}
    out.update(over)
    return out


def _tool_world(monkeypatch, tried: dict[str, Any], decision: str = "approved",
                status: int = 200) -> tuple[_ToolGateway, list[str]]:
    gw = _ToolGateway(tried, status=status)
    cards: list[str] = []

    async def decide(title: str, detail: str, context: str) -> str:
        cards.append(context)
        return decision

    async def held() -> bool:
        return False

    monkeypatch.setattr(stools, "_send", gw.send)
    monkeypatch.setattr(stools, "_decide", decide)
    monkeypatch.setattr(stools, "_chat_is_held", held)
    monkeypatch.setattr(stools, "_must_ask", held)
    return gw, cards


def _call_repair(gw: _ToolGateway, **kw: Any) -> dict[str, Any]:
    args = {"skill_id": R_SID, "failure_id": str(uuid.uuid4()),
            "instructions": "Add the lead. Always fill the company.",
            "remove_actions": ["object:lead.list"],
            "based_on": gw.read["version"]["content_hash"], **kw}
    return asyncio.run(stools.studio_skill_repair(**args))


@pytest.mark.parametrize("decision", ["revised", "stopped", "no_card"])
def test_only_approved_calls_the_approve_route(monkeypatch, decision: str) -> None:
    """On revised, stopped and no_card the tool calls nothing more and writes
    nothing. Mutation: approve on each decision -> red."""
    gw, cards = _tool_world(monkeypatch, _tried(), decision)
    out = _call_repair(gw)
    assert len(cards) == 1 and out["ok"] is False and out["decision"] == decision
    assert not any(p.endswith("/approve") for _m, p, _b in gw.calls)
    assert ("context" in out) is (decision == "no_card")


def test_approved_calls_the_approve_route_once(monkeypatch) -> None:
    tried = _tried()
    gw, cards = _tool_world(monkeypatch, tried)
    out = _call_repair(gw)
    approves = [b for _m, p, b in gw.calls if p.endswith("/approve")]
    assert approves == [{"expected_version": 2, "instructions": tried["instructions"],
                         "remove_actions": ["object:lead.list"]}]
    assert out["ok"] is True and out["say"].startswith("I saved version 3.")
    assert "Remove: «object:lead.list»" in cards[0]


def test_the_tool_shows_no_card_for_a_try_that_failed(monkeypatch) -> None:
    tried = {"passed": False, "message": skill_repair.WORDS["failed"].format(next=3),
             "results": [{"test_id": "t1", "passed": False, "reason": "extra_write",
                          "words": skill_repair.WORDS["extra_write"]}]}
    gw, cards = _tool_world(monkeypatch, tried)
    out = _call_repair(gw)
    assert cards == [] and out["ok"] is False and out["code"] == "failed"
    assert out["say"].startswith("Version 3 did not pass")
    assert skill_repair.WORDS["extra_write"] in out["say"]


def test_a_refusal_of_the_route_gives_its_words(monkeypatch) -> None:
    gw, cards = _tool_world(monkeypatch, {"detail": {
        "code": "repeat_change", "message": skill_repair.WORDS["repeat_change"]}}, status=409)
    out = _call_repair(gw)
    assert cards == [] and out["code"] == "repeat_change"
    assert out["say"] == skill_repair.WORDS["repeat_change"]
    gw404, _ = _tool_world(monkeypatch, {"detail": "Not found"}, status=404)
    assert _call_repair(gw404)["say"] == skill_repair.WORDS["not_owner"]


def test_the_tool_needs_based_on_for_new_instructions(monkeypatch) -> None:
    gw, cards = _tool_world(monkeypatch, _tried())
    out = _call_repair(gw, based_on="")
    assert out["ok"] is False and cards == []
    assert not any(p.endswith("/repairs") for _m, p, _b in gw.calls)


def test_the_undo_tool_calls_the_f7_route(monkeypatch) -> None:
    gw, _ = _tool_world(monkeypatch, _tried())
    out = asyncio.run(stools.studio_skill_undo(skill_id=R_SID, expected_version=3))
    assert gw.calls == [("POST", f"/studio/skills/{R_SID}/undo", {"expected_version": 3})]
    assert out["say"] == ("Version 2 is back, as version 4. The skill is a draft now. "
                          "Say publish to use it again.")
    assert asyncio.run(stools.studio_skill_undo(skill_id=R_SID, expected_version=1))["ok"] is False


def test_the_result_text_fits() -> None:
    """At most 1188 characters. The outcome and the next step are in the
    first 400, also after a cut, and a cut ends with "and N more"."""
    head = skill_repair.passed_words(3, 4)
    tail = [f"Test t{i}: {skill_repair.WORDS['field_mismatch']}" for i in range(40)]
    said = stools.say_text(head, tail)
    assert len(said) <= stools.MAX_SAY_CHARS and said.startswith(head)
    assert len(head) <= stools.SAY_HEAD_CHARS
    assert said.rstrip(".").split()[-3:-1] == ["and", said.split()[-2]] or "more" in said[-12:]
    assert said.endswith("more")
    for words in (*skill_repair.WORDS.values(), skill_repair.saved_words(12, "published")):
        assert len(words.format(next=12)) <= stools.SAY_HEAD_CHARS
    assert len(stools.say_text("x" * 2000, ["y"])) <= stools.MAX_SAY_CHARS


def test_the_tools_take_no_org_or_member_and_have_one_sentence() -> None:
    import inspect as _inspect

    for fn in stools.STUDIO_REPAIR_TOOLS:
        params = set(_inspect.signature(fn).parameters)
        assert not params & {"org", "organization_id", "member", "user", "owner_email"}
        doc = _inspect.getdoc(fn) or ""
        assert "\n" not in doc and doc.endswith(".") and doc.count(". ") == 0
        assert len(doc.split()) <= 25
    assert {f.__name__ for f in stools.STUDIO_REPAIR_TOOLS} == set(stools.STUDIO_REPAIR_NAMES)


def test_flag_off_offers_no_repair_tool(monkeypatch) -> None:
    """The pack filter drops both tools while the org is off a switch of the
    repair. Mutation: drop the filter -> red."""
    tools = [stools.studio_skill_repair, stools.studio_skill_undo, stools.studio_skill_list]
    settings = get_settings()
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: R_ORG)
    for key in ("studio_orgs", "studio_skill_tests_orgs", "studio_skill_repair_orgs"):
        monkeypatch.setattr(settings, key, "*", raising=False)
    kept, dropped = ti._drop_studio_repair_unless_open(list(tools), [])
    assert kept == tools and dropped == []
    for key in ("studio_skill_repair_orgs", "studio_skill_tests_orgs", "studio_orgs"):
        monkeypatch.setattr(settings, key, "", raising=False)
        kept, dropped = ti._drop_studio_repair_unless_open(list(tools), [])
        assert kept == [stools.studio_skill_list], key
        assert sorted(dropped) == ["studio_skill_repair", "studio_skill_undo"], key
        monkeypatch.setattr(settings, key, "*", raising=False)
    from acb_skills import tool_packs as tp

    assert tp.PACKS["studio-repair"].owner == "studio-builder"
    assert "studio-repair" not in tp.FOLLOWS
    assert tp.with_followers({"studio"}) == {"studio", "studio-more"}
    assert "studio_skill_repair" not in tp.PACKS["studio-more"].tools


def test_a_repair_waits_on_the_slot_of_f4b(repairs) -> None:
    """The repair runs inside the F4b slot of the member, so a suite that
    runs gives 429 ``suite_running`` and spends nothing."""
    from acb_common.tenant_redis import key, organization_scope

    who = hashlib.sha256(ANA.encode("utf-8")).hexdigest()[:24]
    with organization_scope(R_ORG):
        busy = key("studio-skill-test-busy", who)
    repairs.redis.data[busy.value] = "a-suite-runs"
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id)
    assert answer.status_code == 429 and answer.json()["detail"]["code"] == "suite_running"
    assert answer.json()["detail"]["message"] == skill_repair.WORDS["suite_running"]
    assert _spent(repairs) == (0, 0) and repairs.proposals == []


# ── F12c review round 1 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("instructions", "code"), [
    ("Add the lead for {{name}}.", "unresolved_variable"),
    ("Add the lead. " + "Fill each field of the lead with care. " * 900, "too_long")])
def test_a_proposal_that_cannot_load_spends_nothing_and_writes_no_row(
        repairs, instructions: str, code: str) -> None:
    """Review P2: the pack builder refuses it in check 7, so the one repair
    of the run stays unused. Mutation: drop the build check -> the suite runs
    and writes a row -> red."""
    failed = _failed(repairs)
    answer = _repair(repairs, failed.id, instructions=instructions)
    assert answer.status_code == 422, answer.text[:300]
    assert answer.json()["detail"] == {"code": code, "message": skill_repair.WORDS[code]}
    assert _spent(repairs) == (0, 0) and repairs.proposals == []
    assert _repair(repairs, failed.id).status_code == 200, "the one repair is still free"


def test_the_history_marks_the_rows_that_can_start_a_repair(repairs, monkeypatch) -> None:
    from gateway.routes.studio import skills as route

    live = _run_row(run_id="r-live")
    test_row = _run_row(kind="test", reason="missing_write", test_id="t1", run_id="r-t")

    async def history(_db: Any, member: str, sid: str) -> Any:
        return skill_runs.History(rows=(live, test_row), others={})

    monkeypatch.setattr(route.store, "get_version", route.store.get_version)
    monkeypatch.setattr(skill_runs, "history", history)
    out = _app().get(f"/studio/skills/{R_SID}/runs").json()
    assert [r["repairable"] for r in out["runs"]] == [True, False]


async def test_the_skill_read_shows_a_failure_id_only_for_a_repairable_row(monkeypatch) -> None:
    gw = _ToolGateway(_tried())
    runs = {"runs": [{"id": "live-1", "kind": "live", "repairable": True, "reason": "tool_error",
                      "words": "w", "version": 2},
                     {"id": "test-1", "kind": "test", "repairable": False,
                      "reason": "missing_write", "words": "w", "version": 2}],
            "others": {}, "earlier_versions": []}

    async def send(method: str, path: str, body: Any, act: str = "", miss: str = "",
                   timeout: float = 15.0) -> Any:
        gw.calls.append((method, path, body))
        if path.endswith("/runs"):
            return stools._Reply(200, runs)
        return stools._Reply(200, gw.read)

    monkeypatch.setattr(stools, "_send", send)
    monkeypatch.setattr(stools, "_repair_open", lambda: True)
    out = await stools.studio_skill_list(skill_id=R_SID)
    assert [r.get("failure_id") for r in out["failures"]["runs"]] == ["live-1", None]
    assert "test-1" not in repr(out["failures"])


async def test_the_skill_read_asks_for_failures_only_on_the_flag(monkeypatch) -> None:
    """Review P3: no request to the history route while the org is off."""
    gw = _ToolGateway(_tried())
    monkeypatch.setattr(stools, "_send", gw.send)
    monkeypatch.setattr(stools, "_repair_open", lambda: False)
    out = await stools.studio_skill_list(skill_id=R_SID)
    assert "failures" not in out
    assert [p for _m, p, _b in gw.calls] == [f"/studio/skills/{R_SID}"]


def test_the_failures_block_is_cut_and_never_the_read() -> None:
    read = {"ok": True, "instructions": "x" * (stools.MAX_MANIFEST_CHARS - 2000)}
    block = {"runs": [{"reason": "tool_error", "detail": "d" * 290} for _ in range(10)],
             "others": {}, "earlier_versions": [{"version": 1, "instructions": "i" * 400}] * 3,
             "note": "n"}
    out = stools._with_failures(read, block)
    assert out["instructions"] == read["instructions"]
    assert stools._size(out) <= stools.MAX_MANIFEST_CHARS
    assert out["failures"]["truncated"] is True and len(out["failures"]["runs"]) < 10
    tight = {"ok": True, "instructions": "x" * stools.MAX_MANIFEST_CHARS}
    assert stools._with_failures(tight, block)["failures_left_out"] is True


def test_a_held_chat_asks_before_the_try(monkeypatch) -> None:
    """Review P2: in a chat that read outside mail, no credit is spent with
    no act of the member. Mutation: drop the card -> the route runs -> red."""
    gw, cards = _tool_world(monkeypatch, _tried(), "stopped")

    async def held() -> bool:
        return True

    monkeypatch.setattr(stools, "_must_ask", held)
    out = _call_repair(gw)
    assert len(cards) == 1 and out["decision"] == "stopped"
    assert not any(p.endswith("/repairs") for _m, p, _b in gw.calls)
    gw2, cards2 = _tool_world(monkeypatch, _tried(), "approved")
    monkeypatch.setattr(stools, "_must_ask", held)
    assert _call_repair(gw2)["ok"] is True
    assert len(cards2) == 2, "one card before the try, one card of the change"


def test_based_on_is_needed_also_to_remove_an_action(monkeypatch) -> None:
    gw, cards = _tool_world(monkeypatch, _tried())
    out = _call_repair(gw, instructions="", based_on="")
    assert out["ok"] is False and cards == []
    assert not any(p.endswith("/repairs") for _m, p, _b in gw.calls)


def test_the_tool_says_no_failed_run(monkeypatch) -> None:
    gw, _ = _tool_world(monkeypatch, {"detail": {
        "code": "no_failed_run", "message": skill_repair.WORDS["no_failed_run"]}}, status=404)
    out = _call_repair(gw)
    assert out["code"] == "no_failed_run" and out["say"] == skill_repair.WORDS["no_failed_run"]


@pytest.mark.parametrize(("state", "line"), [("published", "The skill becomes a draft."),
                                             ("draft", "The skill keeps its state.")])
def test_the_undo_card_says_draft_only_for_a_published_skill(monkeypatch, state, line) -> None:
    gw, cards = _tool_world(monkeypatch, _tried(), "stopped")
    gw.read["skill"]["state"] = state

    async def held() -> bool:
        return True

    monkeypatch.setattr(stools, "_must_ask", held)
    asyncio.run(stools.studio_skill_undo(skill_id=R_SID, expected_version=3))
    assert line in cards[0]
    other = "The skill keeps its state." if state == "published" else "The skill becomes a draft."
    assert other not in cards[0]


def test_a_base_test_named_input_1_stays_a_test(repairs) -> None:
    """Review P3: the input run is the last result, by position. Mutation:
    find it by its id -> the member's test takes its place -> red."""
    repairs.versions[2]["tests"] = [{"id": "input-1", "input": "Add Ravi from Acme",
                                     "expect": [{"action": "object:lead.create",
                                                 "fields": {"data.company": "Acme"}}]}]
    repairs.runner = _RepairRunner({R_INPUT: [(_mail_tool(), {"to": "x"})]})
    failed = _failed(repairs)
    out = _repair(repairs, failed.id).json()
    assert out["tests"] == 1 and out["reason"] == "other_action"
    assert [(r["test_id"], r["input"]) for r in out["results"]] == [
        ("input-1", False), ("input-1", True)]
    assert stools._failed_lines(out) == [
        f"The request that failed: {skill_repair.WORDS['other_action']}"]
    good = skill_tests.TestResult(test_id="input-1", passed=True, reason="passed",
                                  writes=("object:lead.create",))
    bad = skill_tests.TestResult(test_id="input-1", passed=False, reason="other_action",
                                 writes=("tool:send_email",))
    found = skill_repair.outcome(skill_tests.SuiteResult(
        skill_id=R_SID, version=2, content_hash="c", results=(good, bad)))
    assert found.writes == ("tool:send_email",) and found.tests == 1


def test_same_writes_reads_one_name_for_a_door_op() -> None:
    """Review P2: the live row names a door op ``<app>.<op>``, and the dry run
    names its chat tool. Mutation: compare the raw names -> red."""
    door = "knowledge.list_spaces"
    assert skill_repair.write_name(door) == "list_spaces"
    failed = _run_row(writes=({"action": door, "keys": []},))
    found = skill_repair.Outcome(passed=True, reason="passed", detail="",
                                 writes=("tool:list_spaces",), credits=Decimal(0),
                                 run_id=None, tests=0)
    assert skill_repair.same_writes(found, failed) is True
    other = dataclasses.replace(found, writes=("tool:send_email",))
    assert skill_repair.same_writes(other, failed) is False


def test_a_body_with_no_actions_key_is_no_empty_subset() -> None:
    """Review P3: removing the key is a change of the shape, not a subset."""
    gone = {k: v for k, v in R_BASE.items() if k != "actions"}
    assert skill_repair.check_contract(R_BASE, {**gone, "instructions": "x"}, _CLASSES) == "widened"
    bare = {k: v for k, v in R_BASE.items() if k != "actions"}
    assert skill_repair.check_contract(bare, {**bare, "instructions": "x"}, _CLASSES) is None


@pytest.mark.parametrize(("before", "after"), [(1, True), (1, 1.0), (True, 1), ([1], [True])])
def test_the_other_keys_compare_with_their_types(before: Any, after: Any) -> None:
    base = {**R_BASE, "cards": before}
    assert skill_repair.check_contract(base, {**base, "cards": after, "instructions": "x"},
                                       _CLASSES) == "widened"


def test_the_allow_list_drops_the_repair_pack_off_the_flag(monkeypatch) -> None:
    """Item 3: with the org off a switch, ``choose`` and the loader never see
    the pack, also with a TOOL_PACKS line."""
    settings = get_settings()
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: R_ORG)
    for key in ("studio_orgs", "studio_skill_tests_orgs", "studio_skill_repair_orgs"):
        monkeypatch.setattr(settings, key, "*", raising=False)
    allow = frozenset({"studio", "studio-more", "studio-repair"})
    assert ti._without_closed_repair(allow) == allow
    monkeypatch.setattr(settings, "studio_skill_repair_orgs", "", raising=False)
    assert ti._without_closed_repair(allow) == {"studio", "studio-more"}
    src = (_ROOT / "apps/services/orchestrator/orchestrator/_tool_injection.py").read_text(
        encoding="utf-8")
    body = src[src.index("async def _apply_tool_packs("):]
    assert "allow = _without_closed_repair(allow)" in body[:body.index("\nasync def ", 10)]


@pytest.mark.parametrize("repair_on", [True, False])
async def test_the_repair_pack_builds_through_the_owner_filter(monkeypatch, repair_on) -> None:
    """Review P2: the real builder of a pack (``_pack_tools``) runs the owner
    filter, so an org off STUDIO_SKILL_REPAIR_ORGS with a TOOL_PACKS line gets
    NO repair tool. Mutation: drop the call of
    ``_drop_studio_repair_unless_open`` -> red."""
    from acb_skills import tool_packs as tp

    from tests.unit._native_maf_harness import load_agent_module
    from tests.unit.test_tool_packs_registry import HOLDER, _member_holds

    settings = get_settings()
    monkeypatch.setattr(ti, "_run_org_or_none", lambda: R_ORG)
    monkeypatch.setattr(ti, "_studio_open", lambda: True)
    monkeypatch.setattr(ti, "_studio_requests_open", lambda: True)
    _member_holds(monkeypatch, HOLDER)

    async def clear(_owner: str, _ctx: Any) -> bool:
        return False

    monkeypatch.setattr(ti, "_pack_held", clear)
    for key in ("studio_orgs", "studio_skill_tests_orgs"):
        monkeypatch.setattr(settings, key, "*", raising=False)
    monkeypatch.setattr(settings, "studio_skill_repair_orgs", "*" if repair_on else "",
                        raising=False)
    module = load_agent_module("apps/agents/agent-studio-builder")
    build = await ti._pack_tools("studio-repair", {tp.PACK_RUN_KEY: "run-key-repair"},
                                 module=module)
    assert build is not None and build.owner == "studio-builder"
    names = tuple(tp_name(t) for t in build.tools)
    if repair_on:
        assert names == ("studio_skill_repair", "studio_skill_undo")
    else:
        assert names == () and sorted(build.dropped) == ["studio_skill_repair",
                                                         "studio_skill_undo"]


def tp_name(tool: Any) -> str:
    from acb_skills.tool_guard import tool_name

    return tool_name(tool)
