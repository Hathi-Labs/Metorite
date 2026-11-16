"""The card of ``create_task`` names an assignee that ``people`` does not know.

Task 3597. The chat card draws this row as a warning (`assigneeWarning.ts`).
The row is the server's, and it must reach the card BEFORE Approve. These tests
pin that. They change no code. Each runs with the gateway stubbed, so no
database is touched.

Fence for the client half: ``src/components/assigneeWarning.test.ts``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_projects_agent_writes import UUID, responder

STRANGER = "x@y.test"


def _no_names(call: dict) -> Any:
    """The directory knows nobody: ``/projects/people/names`` answers no names."""
    if call["path"] == "/projects/people/names":
        return {"names": {}}
    return responder(call)


async def test_the_card_names_a_stranger_before_any_write(monkeypatch) -> None:
    """Mutation: drop the stranger line from `_assignee_card_lines` -> red."""
    asked = approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _no_names)
    await skill_projects.create_task(UUID, "Survey the site", assignees=STRANGER)
    assert len(asked) == 1
    assert f"not in the directory: «{STRANGER}»" in asked[0]["context"]
    # The card was shown before the first write, and the lookup came first.
    paths = [c["path"] for c in calls]
    assert "/projects/people/names" in paths
    posts = [c for c in writes(calls) if c["path"] == "/projects/tasks"]
    assert posts, "the task is written after the approval"
    assert paths.index("/projects/people/names") < calls.index(posts[0])


async def test_a_member_has_no_such_line(monkeypatch) -> None:
    """Mutation: name every address as a stranger -> red."""
    asked = approve(monkeypatch)

    def knows_one(call: dict) -> Any:
        if call["path"] == "/projects/people/names":
            return {"names": {STRANGER: "Xavier"}}
        return responder(call)

    fake_gateway(monkeypatch, knows_one)
    await skill_projects.create_task(UUID, "Survey the site", assignees=STRANGER)
    assert "not in the directory" not in asked[0]["context"]
