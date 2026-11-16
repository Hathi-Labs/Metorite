"""HRD-5 (task 2984): a per-turn budget for each read tool.

Spec: ``project-docs/specs/chat_failure_modes.md`` §4 row B5 and §6 HRD-5.
Modules: ``orchestrator.watchdog`` (the rule and the counter) and
``orchestrator.tool_turn_budget`` (the middleware).

1. The predicate reads the REAL texts of the chat tools, and stays narrow.
2. After 3 empty or refused answers from one tool, the 4th call is stopped.
   A real answer does not count and does not reset. Tools have separate
   budgets, and a new budget (a new turn) starts at zero.
3. The stop text is Simplified Technical English, lists at most 3 bounded
   argument forms, and holds no result text.
4. Through a real ``Agent``: the 4th call does not run, and the model reads
   the stop text. A write tool is never stopped. A new turn resets.
5. The flag is dark: ``TOOL_TURN_BUDGET_ORGS`` empty attaches nothing.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

import pytest
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)
from orchestrator import _tool_injection, watchdog
from orchestrator.tool_turn_budget import tool_turn_budget_on
from orchestrator.watchdog import (
    TOOL_TURN_LIMIT,
    ToolTurnBudget,
    is_empty_or_refused,
    stop_text,
    tried_form,
)

ROOT = Path(__file__).resolve().parents[2]
ORG = "org-hrd5"

EMPTY_TASKS = "No task matches «x». Try a shorter fragment."
EMPTY_LIST = "No task matches those filters."
SHORT_QUERY = "Give at least 3 characters to search, or a task number such as #7."
REAL_HIT = "Tasks matching x in title or description (2):\n- #1 Alpha\n- #2 Beta"


# ── 1. The predicate ─────────────────────────────────────────────────────────


def test_the_predicate_reads_the_real_search_company_zero_answers():
    from acb_skills.company_search_tools import format_answer

    assert is_empty_or_refused(format_answer({}))
    assert is_empty_or_refused(format_answer({"query": "Acme", "broadened": True}, dated=True))
    hit = {"hits": [{"full_id": "deal:1", "title": "Acme", "snippet": "x"}]}
    assert not is_empty_or_refused(format_answer(hit))


@pytest.mark.parametrize("text", [
    EMPTY_TASKS, EMPTY_LIST, SHORT_QUERY, "Give 2 to 300 characters to find.",
    "Refused: path 'a' names no file.", "Not run: no tool is named x.",
    "Error: tasks must be a non-empty JSON array", "Nothing found.",
])
def test_empty_and_refused_texts_count(text):
    assert is_empty_or_refused(text)


@pytest.mark.parametrize("text", [
    REAL_HIT, "", "   ", None, 7, {"rows": []},
    "No task has all the words. These have most of them (2), for x:\n- #1 A",
    "Done. I created the task.",
    "x" * 2000,
])
def test_real_answers_and_odd_values_do_not_count(text):
    assert not is_empty_or_refused(text)


# ── 2. The budget ────────────────────────────────────────────────────────────


def test_three_empty_answers_then_the_fourth_call_is_stopped():
    assert TOOL_TURN_LIMIT == 3
    budget = ToolTurnBudget()
    for word in ("alpha", "beta", "gamma"):
        assert budget.stop_for("find_tasks") is None
        assert budget.record("find_tasks", {"query": word}, EMPTY_TASKS)
    stop = budget.stop_for("find_tasks")
    assert stop is not None
    assert stop.startswith("Stop. You tried: ")
    assert stop.endswith("Tell the member what you tried and ask one question.")
    for word in ("alpha", "beta", "gamma"):
        assert f"query={word}" in stop


def test_two_empty_answers_do_not_stop():
    budget = ToolTurnBudget()
    budget.record("find_tasks", {"query": "a1"}, EMPTY_TASKS)
    budget.record("find_tasks", {"query": "a2"}, EMPTY_TASKS)
    assert budget.stop_for("find_tasks") is None


def test_a_real_answer_does_not_count_and_does_not_reset():
    budget = ToolTurnBudget()
    budget.record("find_tasks", {"query": "a1"}, EMPTY_TASKS)
    budget.record("find_tasks", {"query": "a2"}, EMPTY_TASKS)
    assert not budget.record("find_tasks", {"query": "a3"}, REAL_HIT)
    assert budget.stop_for("find_tasks") is None  # the real answer did not count
    budget.record("find_tasks", {"query": "a4"}, EMPTY_TASKS)
    assert budget.stop_for("find_tasks") is not None  # and it did not reset


def test_each_tool_has_its_own_budget():
    budget = ToolTurnBudget()
    for word in ("a1", "a2", "a3"):
        budget.record("find_tasks", {"query": word}, EMPTY_TASKS)
    assert budget.stop_for("find_tasks") is not None
    assert budget.stop_for("search_company") is None
    assert budget.stop_for("list_tasks") is None


def test_a_new_budget_is_a_new_turn_and_nothing_is_shared():
    first, second = ToolTurnBudget(), ToolTurnBudget()
    for word in ("a1", "a2", "a3"):
        first.record("find_tasks", {"query": word}, EMPTY_TASKS)
    assert first.stop_for("find_tasks") is not None
    assert second.stop_for("find_tasks") is None
    assert not [n for n in vars(watchdog) if n.startswith("_budget")], "no module state"


def test_the_budget_tracks_a_bounded_number_of_tools():
    budget = ToolTurnBudget()
    for i in range(200):
        budget.record(f"tool_{i}", {"query": "q"}, EMPTY_TASKS)
    assert len(budget._tried) <= watchdog._MAX_TOOLS


# ── 3. The text ──────────────────────────────────────────────────────────────


def test_the_tried_form_is_bounded_and_keeps_no_quote():
    long = {"query": 'a "secret" ' + "z" * 200, "limit": 10, "apps": None}
    form = tried_form(long)
    assert len(form) <= 40
    assert '"' not in form
    assert "None" not in tried_form(long)


def test_the_stop_text_lists_three_forms_at_most_and_is_short_sentences():
    text = stop_text(["query=a", "query=b", "query=c", "query=d"])
    assert "query=a" in text and "query=c" in text and "query=d" not in text
    for sentence in re.split(r"(?<=[.?!])\s+", text):
        assert len(sentence.split()) <= 20, sentence
    assert ";" not in text
    assert "No task matches" not in text, "no result text in the stop"


def test_the_stop_text_holds_no_result_text():
    budget = ToolTurnBudget()
    leak = "No task matches PRIVATE-NAME-IN-RESULT."
    for word in ("a1", "a2", "a3"):
        budget.record("find_tasks", {"query": word}, leak)
    assert "PRIVATE-NAME" not in (budget.stop_for("find_tasks") or "")


# ── 4. Through a real agent ──────────────────────────────────────────────────


class ScriptedClient(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    """Answers each request from a step: ``("calls", [(name, args), ...])`` or
    ``("text", words)``. ``results`` keeps each tool result that a request held."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self.script = list(script)
        self.n = 0
        self.results: list[str] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        self.n += 1
        self.results = []
        for message in messages:
            for content in getattr(message, "contents", None) or []:
                if getattr(content, "type", "") == "function_result":
                    self.results.append(str(getattr(content, "result", "")))
        step = self.script.pop(0)
        if step[0] == "calls":
            contents = [
                Content.from_function_call(f"c{self.n}-{i}", name, arguments=json.dumps(args))
                for i, (name, args) in enumerate(step[1])
            ]
        else:
            contents = [Content.from_text(step[1])]
        n = self.n
        if stream:
            async def _gen() -> AsyncIterable[ChatResponseUpdate]:
                for content in contents:
                    yield ChatResponseUpdate(contents=[content], role="assistant",
                                             response_id=f"r{n}", message_id="m")
            return self._build_response_stream(_gen())

        async def _one() -> ChatResponse:
            return ChatResponse(messages=[Message("assistant", contents)], response_id=f"r{n}")
        return _one()


def _read_tool(ran: list[str], answer: str = EMPTY_TASKS):
    async def find_tasks(query: str) -> str:
        """Find tasks."""
        ran.append(query)
        return answer
    find_tasks.__tool_risk__ = {"read_only": True}  # type: ignore[attr-defined]
    return find_tasks


def _write_tool(ran: list[str]):
    async def create_task(title: str) -> str:
        """Create a task."""
        ran.append(title)
        return "No task matches that. Refused: nothing was made."
    create_task.__tool_risk__ = {"read_only": False}  # type: ignore[attr-defined]
    return create_task


@pytest.fixture
def flag_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "tool_turn_budget_orgs", ORG)
    from acb_common.db import bind_tenant, release_tenant

    token = bind_tenant(ORG)
    try:
        yield
    finally:
        release_tenant(token)


def _view(agent: Agent) -> Any:
    agents: list[Any] = [agent]
    _tool_injection._apply_tool_turn_budget(agents)
    assert agents[0] is not agent, "a native agent gets a per-run view"
    return agents[0]


def test_the_fourth_call_does_not_run_and_the_model_reads_the_stop(flag_on):
    ran: list[str] = []
    calls = [("calls", [("find_tasks", {"query": q})]) for q in ("aa1", "bb2", "cc3", "dd4")]
    client = ScriptedClient([*calls, ("text", "Which project?")])
    view = _view(Agent(client=client, name="probe", tools=[_read_tool(ran)]))
    asyncio.run(view.run("find it"))
    assert ran == ["aa1", "bb2", "cc3"], "the 4th call never ran the tool"
    assert any(r.startswith("Stop. You tried: ") for r in client.results)
    assert "query=aa1" in "".join(client.results)


def test_a_write_tool_is_never_stopped(flag_on):
    ran: list[str] = []
    calls = [("calls", [("create_task", {"title": t})]) for t in ("t1", "t2", "t3", "t4", "t5")]
    client = ScriptedClient([*calls, ("text", "Done.")])
    view = _view(Agent(client=client, name="probe", tools=[_write_tool(ran)]))
    asyncio.run(view.run("make"))
    assert ran == ["t1", "t2", "t3", "t4", "t5"]
    assert not any(r.startswith("Stop.") for r in client.results)


def test_a_new_turn_starts_at_zero(flag_on):
    ran: list[str] = []
    tool = _read_tool(ran)
    first = [("calls", [("find_tasks", {"query": q})]) for q in ("aa1", "bb2", "cc3")]
    second = [("calls", [("find_tasks", {"query": q})]) for q in ("dd4", "ee5")]
    client = ScriptedClient([*first, ("text", "One."), *second, ("text", "Two.")])
    view = _view(Agent(client=client, name="probe", tools=[tool]))
    asyncio.run(view.run("one"))
    asyncio.run(view.run("two"))
    assert ran == ["aa1", "bb2", "cc3", "dd4", "ee5"], "the second turn runs its calls"


# ── 5. The flag ──────────────────────────────────────────────────────────────


def test_the_flag_is_empty_by_default_and_reads_the_bound_tenant(monkeypatch):
    from acb_common import get_settings
    from acb_common.settings import Settings

    assert Settings.model_fields["tool_turn_budget_orgs"].default == ""
    monkeypatch.setattr(get_settings(), "tool_turn_budget_orgs", "")
    assert not tool_turn_budget_on(ORG)
    monkeypatch.setattr(get_settings(), "tool_turn_budget_orgs", f"x, {ORG.upper()}")
    assert tool_turn_budget_on(ORG)
    assert not tool_turn_budget_on("org-other")
    monkeypatch.setattr(get_settings(), "tool_turn_budget_orgs", "*")
    assert tool_turn_budget_on("org-other")


def test_flag_off_attaches_nothing(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "tool_turn_budget_orgs", "")
    agent = Agent(client=ScriptedClient([]), name="probe")
    agents: list[Any] = [agent]
    _tool_injection._apply_tool_turn_budget(agents)
    assert agents[0] is agent


def test_the_flag_is_named_in_the_places_of_a_flag():
    from acb_common import env_guard
    from gateway import flag_reload

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nTOOL_TURN_BUDGET_ORGS=\n" in env
    assert env_guard.is_platform_env("TOOL_TURN_BUDGET_ORGS")
    assert flag_reload.reloadable("TOOL_TURN_BUDGET_ORGS")
    doc = (ROOT / "docs" / "flag_reload.md").read_text(encoding="utf-8")
    assert "TOOL_TURN_BUDGET_ORGS" in doc


def test_the_seam_is_wired_in_the_injection_order():
    src = (ROOT / "apps" / "services" / "orchestrator" / "orchestrator"
           / "_tool_injection.py").read_text(encoding="utf-8")
    head = src.split("    _apply_progress_notice(agents)\n", 1)[0]
    assert "    _apply_tool_turn_budget(agents)\n" in head.rsplit("_apply_steer_turns(agents)", 1)[1]
