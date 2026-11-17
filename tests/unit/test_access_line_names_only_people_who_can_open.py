"""Task 3633 — ``gives access to`` names a person who can open the task, and no outsider.

Code: ``skill_projects.writes._access_lines`` and ``_openable``, and
``forms._plan_access``. In Mahesh's run the card held ``gives access to:
«pankajg@…»`` beside ``not in the directory: «pankajg@…»``. The first line
read as if access is given. These tests pin the real text for 1, 2 and 3
outsiders. Siblings: ``test_card_stranger_warning.py`` (the client shares its
fixtures) and ``test_assignee_reach_rows.py``.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import writes

from tests.unit._projects_agent_fakes import approve, fake_gateway
from tests.unit.test_projects_agent_writes import UUID, responder

KNOWN, OFF, NOLOGIN = "k@y.test", "off@y.test", "nl@y.test"
OUT1, OUT2, OUT3 = "x1@y.test", "x2@y.test", "x3@y.test"


def _gateway(audience_seen: list[str] | None = None):
    def respond(call: dict) -> Any:
        path = call["path"]
        if path == "/projects/people/names":
            asked = str(call["params"].get("emails", "")).split(",")
            return {"names": {a: "N" for a in asked if a in (KNOWN, OFF, NOLOGIN)}}
        if path == "/projects/people/reach":
            return {"not_reachable": {OFF: "inactive", NOLOGIN: "no_login"}}
        if path.endswith("/audience"):
            people = str(call["params"].get("people", ""))
            if audience_seen is not None:
                audience_seen.append(people)
            return {"count": 3, "everyone": False, "sample": ["a@y.test"], "gains": [],
                    "gains_count": 0, "without_access": [p for p in people.split(",") if p]}
        return responder(call)
    return respond


async def test_the_audience_read_asks_only_for_people_who_can_open(monkeypatch) -> None:
    seen: list[str] = []
    fake_gateway(monkeypatch, _gateway(seen))
    lines = await writes._access_lines(UUID, [KNOWN, OFF, NOLOGIN, OUT1, "agent:x"])
    assert seen == [KNOWN]
    assert lines["gives access to"] == KNOWN


@pytest.mark.parametrize("outsiders", [[OUT1], [OUT1, OUT2], [OUT1, OUT2, OUT3]])
async def test_no_outsider_is_under_gives_access_to(monkeypatch, outsiders) -> None:
    seen: list[str] = []
    fake_gateway(monkeypatch, _gateway(seen))
    lines = await writes._access_lines(UUID, outsiders)
    assert "gives access to" not in lines
    assert seen == [""], "no person is left to ask the audience about"


async def test_a_mixed_list_keeps_the_member_and_drops_the_rest(monkeypatch) -> None:
    fake_gateway(monkeypatch, _gateway())
    lines = await writes._access_lines(UUID, [OUT1, KNOWN, OUT2])
    assert lines["gives access to"] == KNOWN


async def test_the_create_card_has_the_warning_and_not_the_access_line(monkeypatch) -> None:
    import acb_skills.egress as eg

    async def held(_ctx: Any = None) -> bool:
        return True

    monkeypatch.setattr(eg, "chat_is_held", held)
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_task(UUID, "Survey", assignees=f"{OUT1},{OUT2}")
    context = asked[0]["context"]
    assert f"not in the directory: «{OUT1}, {OUT2}»" in context
    assert "gives access to" not in context


async def test_an_open_chat_reads_no_audience(monkeypatch) -> None:
    """Nothing changes for a chat that is not held: no access line, no read."""
    import acb_skills.egress as eg

    async def open_chat(_ctx: Any = None) -> bool:
        return False

    monkeypatch.setattr(eg, "chat_is_held", open_chat)
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.create_task(UUID, "Survey", assignees=KNOWN)
    assert not any(c["path"].endswith("/audience") for c in calls)
