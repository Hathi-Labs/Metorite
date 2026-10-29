"""WS-67 OA-8h: the Projects pack rules that OA-8e run 3 found.

``project-docs/specs/one_assistant_context.md`` §4.3.2 ("As built, OA-8h").
Each case names the mutation that turns it red (R7).

Run: ``uv run pytest tests/unit/test_pack_rules_oa8h.py -q``.
"""
from __future__ import annotations

from acb_skills import tool_packs as tp

from evals.one_assistant_measure.scenarios import scenario


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_a_task_in_the_tasks_or_calendar_app_is_private() -> None:
    """Mutation: drop the sentence, or write "ask which project" -> red."""
    rules = _flat(tp._PROJECTS_RULES)
    assert ("In the My Tasks or Calendar app, a new task with no project named is private."
            in rules)


def test_a_task_made_from_a_mail_a_call_or_a_chat_is_private() -> None:
    """Mutation: drop "So is one made from a mail, a call or a chat." -> red."""
    rules = _flat(tp._PROJECTS_RULES)
    assert "So is one made from a mail, a call or a chat. Call it at once there." in rules
    assert "Do not ask which project." in rules


def test_the_core_rules_keep_the_oa8g_lines_and_the_size_pin() -> None:
    """Mutation: drop the no-agent line or the hand-off bullet, or grow the
    rules past 3400 characters -> red."""
    rules = _flat(tp._PROJECTS_RULES)
    assert "No Projects agent exists to hand a task to." in rules
    assert "While a Projects pack is held, do its work with its tools." in rules
    assert len(tp.rules_for("projects")) < 3400


def test_the_route_bullet_still_names_all_three_other_packs() -> None:
    """Mutation: drop projects-bulk from the shortened bullet -> red."""
    rules = _flat(tp._PROJECTS_RULES)
    for slug in ("projects-plan", "projects-structure"):
        assert f"{slug} holds" in rules
    assert "projects-bulk holds many tasks at once" in rules
    assert 'When load_skill_tools offers one, load it. Else call call_agent with agent_name "projects-assistant" and the task.' in rules


def test_archive_means_archive_and_many_tasks_are_one_bulk_call() -> None:
    """Mutation: drop the bullet, or let archive_task run once for each task -> red."""
    rules = _flat(tp._PROJECTS_BULK_RULES)
    assert 'when the member says "archive", do not ask whether they mean delete.' in rules
    assert ("For two or more tasks, call bulk_update ONCE with action archive and every id"
            in rules)
    assert "never once for each task" in rules


def test_the_archive_or_delete_question_is_for_the_vague_words_only() -> None:
    """Mutation: restore "When the member says" for the vague words -> red."""
    rules = _flat(tp._PROJECTS_BULK_RULES)
    assert ('Only when the member says "remove", "get rid of" or "clean up", ask '
            "whether they mean archive or delete.") in rules


def test_the_projects_when_line_covers_a_named_project() -> None:
    """Mutation: drop the sentence -> red. A bare "up to" is no load word."""
    when = tp.PACKS["projects"].when
    assert "Also for a question on a named project or task." in when
    assert len(when) <= 130
    assert tp.matched_layer("projects", "What is the Mobile App up to?") is None


def test_fan_tasks_names_the_project_it_writes_to() -> None:
    """Mutation: remove the project from the words -> red."""
    turn = scenario("fan_tasks").turns[0]
    assert "Website Relaunch" in turn.words
    assert tp.matched_layer("projects-bulk", turn.words) == "words"
