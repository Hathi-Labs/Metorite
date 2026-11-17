"""Task 3648 (owner ask, 2026-11-17): a released task goes to the right lane.

The R8 cases of the release itself are in ``test_agent_release_lane_r8.py``.
This file holds what needs no database (R7):

1. The lane rule is pure and lives in ONE place (``core.pick_lane``,
   ``core.lane_like``), beside ``apply_status_transition``.
2. The server instructions say what each stage means, say that a started task
   never goes to Backlog, and name the classes where the agent asks the member
   once and does not guess. They come AFTER the safety rules, so the cut of
   Claude Code at 4,096 characters never moves a safety rule.
3. ``release_task`` no longer says that the task keeps its status, and its
   answer names the lane. The reasons of the answer are the same list on both
   sides of the edge.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from acb_auth import agent_oauth
from agent_edge import mcp, prompts, tools
from gateway.routes.projects import agent_runs, core

_ROOT = Path(__file__).resolve().parents[2]


def _lane(name: str, category: str) -> SimpleNamespace:
    return SimpleNamespace(id=f"{category}:{name}", name=name, category=category)


STANDARD = [_lane("Backlog", "backlog"), _lane("To do", "todo"),
            _lane("In progress", "in_progress"), _lane("Done", "done")]


class TestThePureLaneRule:
    def test_a_to_do_lane_named_like_to_do_ready_or_next_wins(self) -> None:
        lanes = [_lane("Backlog", "backlog"), _lane("Later", "todo"), _lane("Ready", "todo"),
                 _lane("In progress", "in_progress")]
        assert core.pick_lane(lanes, "todo").name == "Ready"
        for name in ("To do", "Todo", "To-do", "to_do", "Next", "Next up", "ready"):
            assert core.pick_lane([_lane("Later", "todo"), _lane(name, "todo")], "todo").name == name

    def test_the_first_lane_of_the_stage_is_the_fallback(self) -> None:
        lanes = [_lane("Lined up", "todo"), _lane("Waiting", "todo")]
        assert core.pick_lane(lanes, "todo").name == "Lined up"

    def test_a_board_with_no_to_do_lane_has_none_and_never_falls_back_to_backlog(self) -> None:
        lanes = [_lane("Backlog", "backlog"), _lane("In progress", "in_progress")]
        assert core.pick_lane(lanes, "todo") is None

    def test_a_name_in_the_wrong_stage_is_no_to_do_lane(self) -> None:
        # "Ready" in the backlog stage, and "Next" in progress, are not To Do.
        lanes = [_lane("Ready", "backlog"), _lane("Next", "in_progress")]
        assert core.pick_lane(lanes, "todo") is None

    def test_the_review_lane_is_a_started_lane_named_like_review(self) -> None:
        lanes = [*STANDARD, _lane("Code review", "in_progress")]
        assert core.pick_lane(lanes, "review").name == "Code review"
        assert core.pick_lane(STANDARD, "review") is None
        # A done lane named Review is no review lane.
        assert core.pick_lane([_lane("Review", "done")], "review") is None

    def test_an_unknown_stage_is_an_error_not_a_guess(self) -> None:
        with pytest.raises(ValueError):
            core.pick_lane(STANDARD, "backlog")

    def test_the_lane_that_a_task_left_is_found_by_name_then_category(self) -> None:
        lanes = [*STANDARD, _lane("Ready", "todo")]
        assert core.lane_like(lanes, "Ready", "todo").name == "Ready"
        assert core.lane_like(lanes, "Renamed", "backlog").name == "Backlog"
        # The To Do rule: "To do" and "Ready" both match, and the first lane wins.
        assert core.lane_like(lanes, "Gone", "todo").name == "To do"
        assert core.lane_like(lanes, None, None) is None

    def test_a_closing_or_triage_lane_is_never_restored(self) -> None:
        lanes = [*STANDARD, _lane("Triage", "triage")]
        for category in ("done", "cancelled", "triage"):
            assert core.lane_like(lanes, "Done", category) is None

    def test_the_helper_sits_beside_apply_status_transition(self) -> None:
        source = (_ROOT / "apps/services/gateway/gateway/routes/projects/core.py").read_text(
            encoding="utf-8")
        assert source.index("async def apply_status_transition") < source.index(
            "def pick_lane")
        # ONE copy of the rule: no other module defines it.
        for path in (_ROOT / "apps/services").rglob("*.py"):
            if path.name == "core.py" and "projects" in path.parts:
                continue
            assert "def pick_lane(" not in path.read_text(encoding="utf-8", errors="ignore"), path


class TestTheReleaseSettlesTheLane:
    def test_the_release_route_calls_the_settle_step_inside_its_transaction(self) -> None:
        tree = ast.parse((_ROOT / "apps/services/gateway/gateway/routes/projects/agent_runs.py")
                         .read_text(encoding="utf-8"))
        route = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.AsyncFunctionDef) and n.name == "release_agent_run")
        calls = {c.func.id for c in ast.walk(route)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "_settle_task_lane" in calls
        assert "apply_status_transition" not in calls  # only the one helper moves it

    def test_the_settle_step_never_names_the_backlog_stage_for_a_started_task(self) -> None:
        source = (_ROOT / "apps/services/gateway/gateway/routes/projects/agent_runs.py"
                  ).read_text(encoding="utf-8")
        body = source[source.index("async def _settle_task_lane"):
                      source.index("@router.post(\"/agent-runs/{run_id}/release\"")]
        # The only stages that the step picks are To Do and review.
        assert "LANE_STAGE_TODO" in body and "LANE_STAGE_REVIEW" in body
        assert '"backlog"' not in body and "'backlog'" not in body

    def test_the_reasons_are_the_same_list_on_both_sides_of_the_edge(self) -> None:
        assert tuple(agent_runs.RELEASE_REASONS) == tuple(tools.RELEASE_REASONS)

    def test_the_answer_of_the_edge_names_the_lane_and_wraps_the_name(self) -> None:
        shown = tools._task_status({"task_status": {
            "name": "Ready", "category": "todo", "moved": True, "reason": "started_work"}})
        assert shown == {"name": {"untrusted_text": "Ready"}, "category": "todo",
                         "moved": True, "reason": "started_work"}
        # A gateway that sends nothing, or a reason outside the list, gives a safe answer.
        bare = tools._task_status({})
        assert bare["moved"] is False and bare["reason"] == "no_task" and bare["name"] is None
        assert tools._task_status({"task_status": {"reason": "invented"}})["reason"] == "no_task"

    def test_release_task_no_longer_says_that_the_task_keeps_its_status(self) -> None:
        described = tools.TOOLS["release_task"].describe()
        text = " ".join(described["description"].split())
        assert "keeps its status" not in text
        for part in ("task_status", "open pull request keeps it in progress",
                     "To Do", "never goes to Backlog"):
            assert part in text, part
        assert "task_status" in described["outputSchema"]["required"]


class TestTheInstructionsSayWhatEachStageMeans:
    STAGES = (
        "Backlog holds a task that nobody started and that is not planned soon.",
        "To Do holds a task planned to run soon, and a started task that is paused or given back.",
        "In progress holds work that moves now: a live run, or an open pull request that waits for review or merge.",
        "Done holds work that is merged or delivered.",
        "A started task never goes to Backlog.",
    )
    CLASSES = (
        "a task of a paused project",
        "an umbrella whose first slice merged and whose next slices did not start",
        "a task with only its spec merged",
        "a task that waits for a production proof or an owner flip",
        "a board whose lanes do not map to the stages",
    )
    CUT = 4096

    def _flat(self, text: str) -> str:
        return " ".join(text.split())

    def test_the_stages_and_the_classes_are_whole_in_the_constants(self) -> None:
        stages = self._flat(prompts.LANE_STAGES)
        for sentence in self.STAGES:
            assert sentence in stages, sentence
        classes = self._flat(prompts.LANE_ASK_CLASSES)
        for klass in self.CLASSES:
            assert klass in classes, klass

    def test_the_ask_on_task_description_names_each_class_and_the_ask_rule(self) -> None:
        text = self._flat(tools.TOOLS["ask_on_task"].describe()["description"])
        for klass in self.CLASSES:
            assert klass in text, klass
        for part in ("do not guess", "ask once for the whole class", "two to four options",
                     "mark one as recommended", "every task of that class in this session",
                     "state the class and the answer in your report"):
            assert part in text, part

    def test_the_runs_off_form_holds_the_full_rule_and_names_no_run_tool(self) -> None:
        rule = self._flat(prompts.LANE_RULE_NO_RUNS)
        for sentence in self.STAGES:
            assert sentence in rule, sentence
        for klass in self.CLASSES:
            assert klass in rule, klass
        assert "ask_on_task" not in rule and "do not guess" in rule

    def test_no_sentence_of_the_rule_sends_a_started_task_to_backlog(self) -> None:
        for rule in (prompts.LANE_RULE, prompts.LANE_RULE_NO_RUNS, prompts.LANE_ASK_RULE):
            flat = self._flat(rule)
            assert "move it back to Backlog" not in flat and "back to Backlog" not in flat

    @pytest.mark.parametrize("answer", [False, True])
    def test_with_runs_on_the_stages_end_before_the_cut_of_claude_code(
            self, monkeypatch, answer) -> None:
        """Verifier finding F2 of task 3648: at the tail, the rule sat after
        character 4096 and Claude Code never read it. Mutation: put it back
        after the routine, and this fails."""
        monkeypatch.setattr(agent_oauth, "runs_enabled", lambda: True)
        monkeypatch.setattr(agent_oauth, "answer_change_enabled", lambda: answer)
        monkeypatch.setattr(mcp.prompts, "offered", lambda held: True)
        flat = self._flat(mcp.instructions(("projects:read", "projects:write")))
        rule = self._flat(prompts.LANE_RULE)
        at = flat.index(rule)
        assert at + len(rule) < self.CUT, (at, len(rule))
        # Every safety sentence ends before the rule, and the rule stays whole.
        for sentence in ("Only one AI session may hold a task.",
                         "So do not write on a run of another session.",
                         "call record_outside_decision at once."):
            assert flat.index(sentence) + len(sentence) <= at, sentence
        assert flat.endswith(prompts.IMAGE_RULE)

    def test_with_runs_off_the_full_rule_follows_the_pr_rule_under_the_cut(
            self, monkeypatch) -> None:
        monkeypatch.setattr(agent_oauth, "runs_enabled", lambda: False)
        monkeypatch.setattr(mcp.prompts, "offered", lambda held: True)
        flat = self._flat(mcp.instructions(("projects:read", "projects:write")))
        rule = self._flat(prompts.LANE_RULE_NO_RUNS)
        assert flat.index(rule) + len(rule) < self.CUT
        assert "ask_on_task" not in flat  # no run tool shows with the flag off

    def test_a_read_only_connection_gets_no_lane_rule(self, monkeypatch) -> None:
        monkeypatch.setattr(mcp.prompts, "offered", lambda held: False)
        text = mcp.instructions(("projects:read",))
        assert "Move a task by what its stage means" not in text


def test_the_event_loop_is_not_needed_by_the_pins() -> None:
    # The pure rule and the pins run with no loop and no database.
    assert asyncio.iscoroutinefunction(agent_runs._settle_task_lane)
