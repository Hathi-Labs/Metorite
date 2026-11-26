"""SBX-F8, the record half (SBX-1a, task 3800): the no-match path of ``find_tools``.

Spec: ``project-docs/specs/agent_sandbox.md`` §6.1, §7.4 and §12.1 (done-when
3, 4, 11 and 13). Code: ``tool_packs.FindTools._no_match``,
``orchestrator/tool_gap_path.py`` and ``gateway/studio/tool_gaps.check_row``.
The R8 half of the store is ``test_user_tool_registry.py``.

What this proves, with fakes at each seam (``tests/unit/_tool_gap_fakes.py``):

1. With no hook, the no-match answer is ``FIND_NONE`` byte for byte.
2. With a hook, its sentence follows ``FIND_NONE``. None, a fault and a
   timeout give ``FIND_NONE``, and a fault logs its class only.
3. A ranked call, and a call that a cap, the budget, the MCP rule or
   "unavailable" stopped, never runs the hook.
4. Each no-match call writes one row, mapped by decision 3.
5. No engine, or a pick below the threshold, gives ``unsure``, ``unsure`` and
   ``other``. The category is a key of ``SKILL_FAMILIES`` or ``other``.
6. Each CHECK of the migration has its check in Python, so no row drops in
   silence.

Mutations (R7), each red here: map ``compose`` to ``no_match`` in
``decide_outcome``; call the hook when ``ranked`` is not empty; drop the
``asyncio.wait_for`` of ``_no_match``; drop the ``candidates`` length check
of ``check_row``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_framework", reason="MAF not installed")

from acb_skills import tool_packs as tp
from acb_skills.skill_families import SKILL_FAMILIES
from agent_framework import FunctionInvocationContext, tool
from gateway.studio import tool_gaps as gaps_store
from orchestrator import tool_gap_path as tgp

from tests.unit import _tool_gap_fakes as fk

_ROOT = Path(__file__).resolve().parents[2]
_MARK = "CREATE TABLE IF NOT EXISTS tool_gaps ("
PKG = "11111111-2222-4333-8444-555555555555"
RUN_KEY = "key-sbx1a"


def _migration() -> str:
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0].read_text(encoding="utf-8")


# ── The frame of a finder ───────────────────────────────────────────────────


def _record() -> tp.PackRun:
    turn = tp.PackTurn(fk.ORG, fk.THREAD, tp._member_part(fk.MEMBER), 3, (), "", True)
    choice = tp.choose(turn, allowed=frozenset(), base_tools=0)
    return tp.PackRun.of(turn, choice, [])


def _tools(names: list[str], prefix: str = "") -> list[Any]:
    def make(name: str) -> Any:
        async def fn() -> str:
            return "ok"

        return tool(fn, name=name, description=f"{prefix}{name} tool",
                    approval_mode="never_require")

    return [make(n) for n in names]


def _dummies(count: int) -> list[Any]:
    return _tools([f"core_{i}" for i in range(count)])


def _finder(record: tp.PackRun, candidates: tp.FindCandidates | None = None, *,
            on_gap: Any = None, build: Any = None, measure: Any = None) -> tp.FindTools:
    async def _no_build(slug: str, ctx: Any, names: Any) -> Any:
        return None

    return tp.FindTools(candidates=candidates or tp.FindCandidates(), run_key=RUN_KEY,
                        record=record, build=build or _no_build,
                        measure=measure or (lambda items: 0), on_gap=on_gap)


async def _find(finder: tp.FindTools, record: tp.PackRun, query: str, live: list[Any]) -> str:
    context = FunctionInvocationContext(function=finder.tool, arguments={"query": query},
                                        tools=live)

    async def call_next() -> None:
        raise AssertionError("the middleware took the call")

    with fk.in_run(**{tp.PACK_RUN_KEY: RUN_KEY, tp.PACKS_KEY: record}):
        await finder.process(context, call_next)
    return str(context.result)


class Hook:
    """A gap hook that records each call."""

    def __init__(self, answer: Any = "The extra sentence.", *, raises: Exception | None = None,
                 sleep: float = 0.0) -> None:
        self.answer, self.raises, self.sleep = answer, raises, sleep
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, query: str, live: Any) -> Any:
        from acb_skills.tool_guard import tool_name

        self.calls.append((query, [tool_name(t) for t in live]))
        if self.sleep:
            await asyncio.sleep(self.sleep)
        if self.raises is not None:
            raise self.raises
        return self.answer


# ── 1, 2. The answer of the no-match branch ────────────────────────────────


async def test_with_no_hook_the_no_match_answer_is_find_none_byte_for_byte() -> None:
    """Done-when 3. Mutation: add a sentence with no hook -> red."""
    record = _record()
    answer = await _find(_finder(record), record, "price list of a supplier", _dummies(3))
    assert answer == tp.FIND_NONE


async def test_the_hook_sentence_follows_find_none_after_a_blank_line() -> None:
    record = _record()
    hook = Hook()
    live = _dummies(2)
    answer = await _find(_finder(record, on_gap=hook), record, "  price list  ", live)
    assert answer == f"{tp.FIND_NONE}\n\nThe extra sentence."
    assert hook.calls == [("price list", ["core_0", "core_1"])]


@pytest.mark.parametrize("told", [None, "", "   ", 7])
async def test_no_sentence_gives_find_none(told: Any) -> None:
    record = _record()
    answer = await _find(_finder(record, on_gap=Hook(told)), record, "price list", _dummies(1))
    assert answer == tp.FIND_NONE


async def test_a_fault_gives_find_none_and_logs_the_class_only(monkeypatch) -> None:
    """Done-when 13. The log line names the class, never the text of the fault."""
    logs = fk.Logs()
    monkeypatch.setattr(tp, "_log", logs)
    record = _record()
    hook = Hook(raises=RuntimeError("the secret need of jane"))
    answer = await _find(_finder(record, on_gap=hook), record, "price list", _dummies(1))
    assert answer == tp.FIND_NONE
    assert logs.events("tool_packs.find_gap_failed") == [{"error": "RuntimeError"}]
    assert "secret" not in repr(logs.lines)


async def test_a_slow_hook_stops_at_the_timeout(monkeypatch) -> None:
    """Done-when 13: the wait is :data:`tool_packs.FIND_GAP_TIMEOUT_S` (8 s).
    Mutation: drop the ``wait_for`` -> the call waits 5 s and answers the
    sentence -> red."""
    assert tp.FIND_GAP_TIMEOUT_S == 8.0
    logs = fk.Logs()
    monkeypatch.setattr(tp, "_log", logs)
    monkeypatch.setattr(tp, "FIND_GAP_TIMEOUT_S", 0.05)
    record = _record()
    hook = Hook(sleep=5.0)
    answer = await _find(_finder(record, on_gap=hook), record, "price list", _dummies(1))
    assert answer == tp.FIND_NONE
    assert logs.events("tool_packs.find_gap_failed") == [{"error": "TimeoutError"}]


# ── 3. A ranked call or a refusal never runs the hook ─────────────────────


def _crm_candidates() -> tp.FindCandidates:
    return tp.FindCandidates(packs=("crm-records",), tools={"create_lead": "crm-records"},
                             descriptions={"create_lead": "Create a lead in the CRM."})


def _builder(names: list[str]) -> Any:
    async def build(slug: str, ctx: Any, held: Any) -> Any:
        return SimpleNamespace(tools=tuple(_tools(names)), held=False)

    return build


async def test_a_ranked_call_that_adds_a_tool_never_runs_the_hook() -> None:
    """Done-when 4. Mutation: run the hook when ``ranked`` is not empty -> red."""
    record = _record()
    hook = Hook()
    finder = _finder(record, _crm_candidates(), on_gap=hook, build=_builder(["create_lead"]))
    answer = await _find(finder, record, "create a lead", _dummies(2))
    assert answer.startswith("Found 1 tools."), answer
    assert hook.calls == []


async def test_an_unavailable_build_never_runs_the_hook() -> None:
    record = _record()
    hook = Hook()
    finder = _finder(record, _crm_candidates(), on_gap=hook)
    answer = await _find(finder, record, "create a lead", _dummies(2))
    assert answer == tp.FIND_UNAVAILABLE
    assert hook.calls == []


async def test_the_search_cap_never_runs_the_hook() -> None:
    record = _record()
    hook = Hook()
    finder = _finder(record, on_gap=hook)
    finder.searches = tp.FIND_SEARCHES
    answer = await _find(finder, record, "price list", _dummies(1))
    assert answer == tp.FIND_PAST_SEARCHES.format(searches=tp.FIND_SEARCHES)
    assert hook.calls == []


async def test_the_budget_and_the_request_cap_never_run_the_hook() -> None:
    record = _record()
    hook = Hook()
    huge = _finder(record, _crm_candidates(), on_gap=hook, build=_builder(["create_lead"]),
                   measure=lambda items: 10 ** 6)
    answer = await _find(huge, record, "create a lead", _dummies(2))
    assert answer == tp.FIND_PAST_BUDGET.format(budget=tp.FIND_SCHEMA_TOKENS)
    full = _finder(record, _crm_candidates(), on_gap=hook, build=_builder(["create_lead"]))
    answer = await _find(full, record, "create a lead", _dummies(tp.MAX_REQUEST_TOOLS))
    assert answer == tp.FIND_PAST_CAP.format(tools=tp.MAX_REQUEST_TOOLS)
    assert hook.calls == []


async def test_the_mcp_rule_never_runs_the_hook() -> None:
    from acb_skills import egress as eg

    record = _record()
    hook = Hook()
    mcp = _tools(["notion_search"])[0]
    mcp.additional_properties["_mcp_is_tool"] = True
    assert eg._from_mcp(mcp)
    candidates = tp.FindCandidates(packs=("email",), tools={"compose_reply": "email"},
                                   descriptions={"compose_reply": "Compose a reply to a mail."})
    finder = _finder(record, candidates, on_gap=hook, build=_builder(["compose_reply"]))
    answer = await _find(finder, record, "compose a reply", [mcp, *_dummies(1)])
    assert answer.startswith("Not added. The "), answer
    assert hook.calls == []


# ── 4. One row for each no-match call, mapped by decision 3 ───────────────


@pytest.mark.parametrize(("pick", "answer", "outcome"), [
    (f"pkg:{PKG}", "tool", "reuse"),
    ("tool:knowledge_search", "tool", "existing"),
    ("compose", "compose", "compose"),
    ("none", "none", "no_match"),
    ("unsure", "unsure", "unsure"),
])
async def test_each_pick_writes_one_row_by_decision_3(monkeypatch, pick, answer, outcome) -> None:
    """Done-when 4 and decision 3. Mutation: map ``compose`` to
    ``no_match`` -> red."""
    world = fk.install(monkeypatch, hits=[fk.hit(PKG)],
                       rows=[fk.knowledge_row("search", "Search the knowledge notes")],
                       picks={"answer": (pick, 0.95), "category": ("documents", 0.95)})
    live = _tools(["knowledge_search"], prefix="search the knowledge notes ")
    with fk.in_run():
        told = await tgp.gap_hook(fk.NEED, live)
    assert len(world.records) == 1
    row = world.records[0]
    assert (row["answer"], row["outcome"]) == (answer, outcome)
    assert row["app_id"] == (PKG if outcome == "reuse" else None)
    assert row["category"] == "documents"
    assert (row["member"], row["thread_id"], row["run_id"]) == (fk.MEMBER, fk.THREAD, fk.RUN)
    assert row["need"] == fk.NEED
    assert told and told.strip()
    assert world.steps == ["gate", "catalog", "library", "ask", "record"]


def test_decide_outcome_is_decision_3() -> None:
    """Pure. A pick that the question did not offer reads as ``unsure``."""
    d = tgp.decide_outcome
    assert d(f"pkg:{PKG}", packages=[PKG], tools=[], composable=False) == tgp.Decision(
        "tool", "reuse", app_id=PKG)
    assert d(f"pkg:{PKG}", packages=[], tools=[], composable=False).outcome == "unsure"
    assert d("tool:read_note", packages=[], tools=["read_note"], composable=False) == (
        tgp.Decision("tool", "existing", name="read_note"))
    assert d("tool:send_email", packages=[], tools=["read_note"], composable=False).outcome == (
        "unsure")
    assert d("compose", packages=[], tools=[], composable=True) == tgp.Decision(
        "compose", "compose")
    assert d("compose", packages=[], tools=[], composable=False).outcome == "unsure"
    assert d("none", packages=[], tools=[], composable=False) == tgp.Decision("none", "no_match")
    for pick in ("unsure", None, "", "NONE", "pkg:", "tool:"):
        assert d(pick, packages=[], tools=[], composable=True) == tgp.Decision("unsure", "unsure")


async def test_an_empty_query_writes_no_row(monkeypatch) -> None:
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)})
    with fk.in_run():
        for query in ("", "   ", "\x00", "​"):
            assert await tgp.gap_hook(query, []) is None
    assert world.records == [] and world.catalog_calls == 0 and world.asks == []


async def test_a_chat_that_the_tenant_cannot_see_writes_no_row_and_no_sentence(
        monkeypatch) -> None:
    """``record_gap`` answers None when the INSERT selects no chat. The hook
    then gives None, so the answer is ``FIND_NONE``, and it logs no row."""
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9)}, record_result=None)
    with fk.in_run():
        assert await tgp.gap_hook(fk.NEED, []) is None
    assert len(world.records) == 1
    assert world.logs.events(tgp.GAP_EVENT) == []


def test_the_need_is_cut_at_280_characters_after_the_strip() -> None:
    assert tgp.clean_need("  " + "a" * 400 + "  ") == "a" * 280
    assert tgp.clean_need("price\x00list ​of  a\tsupplier") == "price list of a supplier"
    assert tgp.clean_need(None) == ""


# ── 5. No engine, the threshold, the category ─────────────────────────────


@pytest.mark.parametrize("given", [
    {"typed_on": False, "picks": {"answer": ("none", 0.99), "category": ("crm", 0.99)}},
    {"picks": None},
    {"picks": {"answer": ("none", 0.5), "category": ("crm", 0.5)}},
    {"picks": {"answer": (None, None), "category": (None, None)}},
])
async def test_no_engine_or_a_low_pick_gives_unsure_unsure_other(monkeypatch, given) -> None:
    """Done-when 11. Mutation: read a pick below the threshold -> red."""
    world = fk.install(monkeypatch, **given)
    with fk.in_run():
        await tgp.gap_hook(fk.NEED, [])
    [row] = world.records
    assert (row["answer"], row["outcome"], row["category"]) == ("unsure", "unsure", "other")
    if given.get("typed_on") is False:
        assert world.asks == [], "no engine: no call"


async def test_a_category_off_the_list_reads_as_other(monkeypatch) -> None:
    world = fk.install(monkeypatch, picks={"answer": ("none", 0.9), "category": ("payroll", 0.99)})
    with fk.in_run():
        await tgp.gap_hook(fk.NEED, [])
    assert world.records[0]["category"] == "other"


def test_the_categories_are_the_pack_families_and_other() -> None:
    """Decision 4: a Python test pins the set, and the CHECK holds the shape."""
    assert tgp.categories() == (*SKILL_FAMILIES, "other")
    shape = re.compile(r"[a-z0-9-]{1,40}")
    assert all(shape.fullmatch(c) for c in tgp.categories())
    assert "CHECK (category ~ '^[a-z0-9-]{1,40}$')" in _migration()


async def test_the_question_has_two_items_and_one_purpose(monkeypatch) -> None:
    """Decision 5: one ``ask_typed`` call, two items, purpose ``tools.gap``."""
    world = fk.install(monkeypatch, hits=[fk.hit(PKG)],
                       rows=[fk.knowledge_row("search", "Search the knowledge notes")],
                       picks={"answer": ("none", 0.9)})
    with fk.in_run():
        await tgp.gap_hook(fk.NEED, _tools(["knowledge_search"], "search notes "))
    [(_context, items, purpose)] = world.asks
    assert purpose == "tools.gap"
    assert [i.id for i in items] == ["answer", "category"]
    assert items[0].options == (f"pkg:{PKG}", "tool:knowledge_search", "compose", "none",
                                "unsure")
    assert items[1].options == tgp.categories()


# ── 6. The checks of the store ────────────────────────────────────────────


def _row(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "member": fk.MEMBER, "thread_id": fk.THREAD, "run_id": fk.RUN, "need": "a need",
        "category": "other", "candidates": [f"pkg:{PKG}", "knowledge.search"],
        "answer": "none", "outcome": "no_match", "app_id": None}
    values.update(over)
    return values


def test_the_store_lists_equal_the_checks_of_the_migration() -> None:
    sql = _migration()
    for value in gaps_store.ANSWERS:
        assert f"'{value}'" in sql.split("tool_gaps_answer", 1)[1].split(")", 1)[0]
    outcome = sql.split("CONSTRAINT tool_gaps_outcome", 1)[1].split("),", 1)[0]
    assert re.findall(r"'([a-z_]+)'", outcome) == list(gaps_store.OUTCOMES)
    assert "BETWEEN 1 AND 280" in sql and gaps_store.NEED_MAX == 280
    assert "cardinality(candidates) <= 10" in sql and gaps_store.CANDIDATES_MAX == 10


@pytest.mark.parametrize(("over", "code"), [
    ({"member": "Jane@Gaps.test"}, "member_email"),
    ({"member": ""}, "member_email"),
    ({"thread_id": ""}, "thread_id"),
    ({"run_id": "r" * 201}, "run_id"),
    ({"need": ""}, "need"),
    ({"need": "n" * 281}, "need"),
    ({"need": "a\x00b"}, "need"),
    ({"category": "Other"}, "category"),
    ({"category": "a" * 41}, "category"),
    ({"candidates": [f"x{i}" for i in range(11)]}, "candidates"),
    ({"candidates": ["two words"]}, "candidates"),
    ({"candidates": ["x" * 200] * 10}, "candidates"),
    ({"candidates": "pkg:1"}, "candidates"),
    ({"answer": "maybe"}, "answer"),
    ({"outcome": "found"}, "outcome"),
    ({"app_id": "not-a-uuid"}, "app_id"),
])
def test_check_row_refuses_each_value_that_a_check_refuses(over, code) -> None:
    """Mutation: drop the length check of ``candidates`` -> the 11 ids pass
    here and drop in silence on the database -> red."""
    with pytest.raises(gaps_store.ToolGapError) as raised:
        gaps_store.check_row(**_row(**over))
    assert raised.value.code == code
    assert "Jane" not in str(raised.value) and "two words" not in str(raised.value)


def test_check_row_passes_each_outcome_of_sbx_1a() -> None:
    for outcome in ("existing", "reuse", "compose", "no_match", "unsure"):
        params = gaps_store.check_row(**_row(outcome=outcome, app_id=PKG.upper()))
        assert params["outcome"] == outcome and params["app"] == PKG


def test_the_candidates_of_a_row_fit_the_check() -> None:
    hits = [fk.hit(f"00000000-0000-4000-8000-00000000000{i}") for i in range(5)]
    rows = [f"knowledge.{'a' * 150}{i}" for i in range(20)]
    tools = [f"tool_{i}" for i in range(5)]
    for decision in (tgp.Decision("compose", "compose"), tgp.Decision("none", "no_match")):
        ids = tgp.candidates_of(decision, hits, tools, rows)
        gaps_store.check_row(**_row(candidates=ids))
        assert len(ids) <= 10
    assert tgp.candidates_of(tgp.Decision("compose", "compose"), hits, tools, rows)[0] == rows[0]
