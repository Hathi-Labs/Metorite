"""Task 3636 — the card rows for a known person who cannot open the task (hermetic).

Code: ``skill_projects.writes._address_check``, ``_reach_rows``,
``_assignee_rows`` and their callers (the create card, the bulk card and the
plan card). R8 twin: ``test_people_not_reachable_r8.py``.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects
from skill_projects import writes

from tests.unit._projects_agent_fakes import approve, fake_gateway
from tests.unit.test_projects_agent_writes import UUID, responder

KNOWN, OFF, NOLOGIN, STRANGER = "k@y.test", "off@y.test", "nl@y.test", "x@y.test"


def _directory(call: dict) -> Any:
    if call["path"] == "/projects/people/names":
        return {"names": {KNOWN: "Kim", OFF: "Olga", NOLOGIN: "Nat"}}
    if call["path"] == "/projects/people/reach":
        return {"not_reachable": {OFF: "inactive", NOLOGIN: "no_login"}}
    return responder(call)


async def test_the_check_splits_strangers_from_the_unreachable(monkeypatch) -> None:
    fake_gateway(monkeypatch, _directory)
    strangers, cannot = await writes._address_check([KNOWN, OFF.upper(), NOLOGIN, STRANGER, "agent:x"])
    assert strangers == [STRANGER]
    assert cannot == {OFF.upper(): "inactive", NOLOGIN: "no_login"}
    assert await writes._unknown_addresses([KNOWN, STRANGER]) == [STRANGER]


async def test_the_rows_name_each_reason_once(monkeypatch) -> None:
    fake_gateway(monkeypatch, _directory)
    rows = await writes._assignee_rows([KNOWN, OFF, NOLOGIN, STRANGER, STRANGER])
    assert rows == {"not in the directory": STRANGER, "suspended": OFF, "no login yet": NOLOGIN}
    assert await writes._assignee_rows([KNOWN]) == {}
    assert await writes._assignee_rows(["agent:x"]) == {}


async def test_the_reach_read_runs_only_for_known_people(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _directory)
    await writes._assignee_rows([STRANGER])
    assert "/projects/people/reach" not in [c["path"] for c in calls]
    await writes._assignee_rows([KNOWN, STRANGER])
    reach = [c for c in calls if c["path"] == "/projects/people/reach"]
    assert len(reach) == 1 and reach[0]["params"] == {"emails": KNOWN}


async def test_the_create_card_names_a_suspended_person(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.create_task(UUID, "Survey the site", assignees=f"{OFF},{NOLOGIN}")
    context = asked[0]["context"]
    assert f"suspended: «{OFF}»" in context and f"no login yet: «{NOLOGIN}»" in context
    assert "not in the directory" not in context


async def test_the_bulk_card_names_each_reason(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.bulk_update(UUID, assignees_add=f"{KNOWN},{OFF},{STRANGER}")
    context = asked[0]["context"]
    assert f"suspended: «{OFF}»" in context
    assert f"not in the directory: «{STRANGER}»" in context
    assert "no login yet" not in context and f"suspended: «{KNOWN}»" not in context


async def test_a_member_who_can_open_the_task_has_no_row(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.create_task(UUID, "Survey the site", assignees=KNOWN)
    for key in ("suspended", "no login yet", "not in the directory"):
        assert key not in asked[0]["context"]
