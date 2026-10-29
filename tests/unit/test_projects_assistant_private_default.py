"""The Projects assistant (specialist) makes a new task private by default.

Owner-approved 2026-10-29 (OA-8h follow-up). It matches the orchestrator pack
rule in tool_packs._PROJECTS_RULES and projects_ai_chat.md section 3.2.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIR = REPO / "apps" / "agents" / "agent-projects"


def _text() -> str:
    raw = (DIR / "instructions.md").read_text(encoding="utf-8")
    return " ".join(raw.split())


def test_a_new_task_with_no_project_named_is_private() -> None:
    """Mutation: drop the sentence, or say "ask which project" -> red."""
    t = _text()
    assert ("A new task with no project named is the member's private task. "
            "So is a task made from a mail, a call or a chat.") in t
    assert "Call `create_personal_task` at once." in t
    assert "Do not ask which project." in t


def test_the_private_tool_is_one_the_specialist_holds() -> None:
    cfg = json.loads((DIR / "config.json").read_text(encoding="utf-8"))
    assert "create_personal_task" in cfg["own_tool_scope"]
    assert "bulk_update" in cfg["own_tool_scope"]


def test_an_open_project_still_takes_the_new_task() -> None:
    """Mutation: drop the open-project bullet -> red."""
    t = _text()
    assert ("When your context holds an open project, a new task goes in "
            "that project, as before.") in t
    assert "A project the member's words name wins over both." in t


def test_many_tasks_go_in_one_bulk_update_and_archive_means_archive() -> None:
    """Mutation: drop the bullet -> red."""
    t = _text()
    assert "For two or more tasks, call `bulk_update` once with every id." in t
    assert 'do not ask "archive or delete"' in t
    assert "many tasks go in one `bulk_update` card" in t
