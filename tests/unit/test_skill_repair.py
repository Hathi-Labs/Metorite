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

import contextlib
import dataclasses
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_common import get_settings
from acb_skills import ask_tools
from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
from gateway.studio import skill_repair, skill_runs, skill_tests
from orchestrator import _tool_injection as ti

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

    async def test_an_unsaved_body_takes_its_own_key(self, monkeypatch) -> None:
        _flags(monkeypatch)
        route, seen, versions = _route(monkeypatch)
        body = {"instructions": "Other.", "actions": ["tool:create_task"]}
        await route._record_test_failures(ORG, MEMBER, SKILL, _suite("extra_write"), body)
        assert versions == []
        assert seen[0]["kw"]["body_key"] == skill_repair.body_key(body)

    async def test_the_test_writer_with_the_flag_off_opens_no_session(self, monkeypatch) -> None:
        route, seen, versions = _route(monkeypatch)
        opened: list[Any] = []
        monkeypatch.setattr(route, "_tenant_session", lambda *a, **k: opened.append(a))
        for studio, repair in (("*", ""), ("", "*")):
            _flags(monkeypatch, studio=studio, repair=repair)
            await route._record_test_failures(ORG, MEMBER, SKILL, _suite("missing_write"), None)
        assert seen == [] and versions == [] and opened == []

    async def test_a_test_store_fault_is_swallowed(self, monkeypatch) -> None:
        _flags(monkeypatch)
        route, seen, _versions = _route(monkeypatch, fail=RuntimeError(WORDS))
        await route._record_test_failures(
            ORG, MEMBER, SKILL, _suite("missing_write", "call_cap"), None)
        assert len(seen) == 1, "a fault that is no refusal ends the write"
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
