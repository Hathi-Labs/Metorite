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
    """The directory answers only the addresses that it is asked about."""
    asked = {a.lower() for a in str(call.get("params", {}).get("emails", "")).split(",") if a}
    if call["path"] == "/projects/people/names":
        every = {KNOWN: "Kim", OFF: "Olga", NOLOGIN: "Nat"}
        return {"names": {a: n for a, n in every.items() if a in asked}}
    if call["path"] == "/projects/people/reach":
        return {"not_reachable": [a for a in (OFF, NOLOGIN) if a in asked]}
    return responder(call)


async def test_the_check_splits_strangers_from_the_unreachable(monkeypatch) -> None:
    fake_gateway(monkeypatch, _directory)
    strangers, cannot = await writes._address_check([KNOWN, OFF.upper(), NOLOGIN, STRANGER, "agent:x"])
    assert strangers == [STRANGER]
    assert cannot == [OFF.upper(), NOLOGIN]  # the card keeps the case that the member typed
    assert await writes._unknown_addresses([KNOWN, STRANGER]) == [STRANGER]


async def test_the_rows_name_each_reason_once(monkeypatch) -> None:
    fake_gateway(monkeypatch, _directory)
    rows = await writes._assignee_rows([KNOWN, OFF, NOLOGIN, STRANGER, STRANGER])
    assert rows == {"not in the directory": STRANGER, "cannot open the task": f"{OFF}, {NOLOGIN}"}
    assert await writes._assignee_rows([KNOWN]) == {}
    assert await writes._assignee_rows(["agent:x"]) == {}


async def test_the_reach_read_runs_only_for_known_people(monkeypatch) -> None:
    calls = fake_gateway(monkeypatch, _directory)
    await writes._assignee_rows([STRANGER])
    assert "/projects/people/reach" not in [c["path"] for c in calls]
    await writes._assignee_rows([KNOWN, STRANGER])
    reach = [c for c in calls if c["path"] == "/projects/people/reach"]
    assert len(reach) == 1 and reach[0]["params"] == {"emails": KNOWN}


async def test_a_unknown_only_check_pays_no_reach_read(monkeypatch) -> None:
    """``_unknown_addresses`` is the names read only (the edit card uses it)."""
    calls = fake_gateway(monkeypatch, _directory)
    assert await writes._unknown_addresses([KNOWN, STRANGER]) == [STRANGER]
    assert "/projects/people/reach" not in [c["path"] for c in calls]


async def test_a_failed_reach_read_shows_no_card(monkeypatch) -> None:
    """A warning that cannot be read is not skipped: the card does not show."""
    from acb_common.tool_refusals import GatewayRefusal

    from tests.unit._projects_agent_fakes import FakeResponse

    def broken(call: dict) -> Any:
        if call["path"] == "/projects/people/reach":
            return FakeResponse({"detail": "no"}, 500)
        return _directory(call)

    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, broken)
    with pytest.raises(GatewayRefusal):
        await skill_projects.create_task(UUID, "Survey", assignees=KNOWN)
    assert asked == []


async def test_the_batch_card_names_who_cannot_open(monkeypatch) -> None:
    """``create_tasks``: the batch card carries the same row (task 3636)."""
    import json

    import acb_skills.egress as eg

    async def open_chat(_ctx: Any = None) -> bool:
        return False

    monkeypatch.setattr(eg, "chat_is_held", open_chat)
    cards: list[dict] = []

    async def ticks_all(*_a: Any, **kw: Any) -> frozenset[str]:
        cards.append(kw)
        return frozenset(r["id"] for r in kw["rows"])

    from skill_projects import forms
    monkeypatch.setattr(forms, "_confirm", ticks_all)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.create_tasks(UUID, json.dumps([
        {"title": "One", "assignees": f"{OFF},{STRANGER}"}, {"title": "Two", "assignees": KNOWN}]))
    context = cards[-1]["context"]
    assert f"cannot open the task: «{OFF}»" in context
    assert f"not in the directory: «{STRANGER}»" in context


async def test_the_create_card_names_a_suspended_person(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.create_task(UUID, "Survey the site", assignees=f"{OFF},{NOLOGIN}")
    context = asked[0]["context"]
    assert f"cannot open the task: «{OFF}, {NOLOGIN}»" in context
    assert "not in the directory" not in context


async def test_the_bulk_card_names_each_reason(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.bulk_update(UUID, assignees_add=f"{KNOWN},{OFF},{STRANGER}")
    context = asked[0]["context"]
    assert f"cannot open the task: «{OFF}»" in context
    assert f"not in the directory: «{STRANGER}»" in context
    assert NOLOGIN not in context and KNOWN not in context.split("cannot open the task")[1]


async def test_a_member_who_can_open_the_task_has_no_row(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _directory)
    await skill_projects.create_task(UUID, "Survey the site", assignees=KNOWN)
    for key in ("cannot open the task", "not in the directory"):
        assert key not in asked[0]["context"]


async def test_the_plan_card_names_an_owner_who_cannot_open(monkeypatch) -> None:
    """``propose_plan``: the plan card carries the same row (task 3636)."""
    import json

    from tests.unit._projects_agent_fakes import form_stub
    from tests.unit.test_projects_agent_writes import _plan_gateway, _plan_submit

    rows = [{"key": "t1", "title": "Order the steel", "owner": OFF, "effort_mins": 60,
             "due": "2026-10-10"},
            {"key": "t2", "title": "Weld the frame", "owner": KNOWN, "effort_mins": 60,
             "due": "2026-10-12"},
            {"key": "t3", "title": "Paint the frame", "owner": STRANGER, "effort_mins": 60,
             "due": "2026-10-14"}]
    form_stub(monkeypatch, _plan_submit(rows))
    asked = approve(monkeypatch)
    plan = _plan_gateway()

    def both(call: dict) -> Any:
        if call["path"].startswith("/projects/people/"):
            return _directory(call)
        return plan(call)

    fake_gateway(monkeypatch, both)
    await skill_projects.propose_plan("Frame", json.dumps(rows))
    context = asked[0]["context"]
    assert f"cannot open the task: «{OFF}»" in context
    assert f"not in the directory: «{STRANGER}»" in context
    assert KNOWN not in context.split("cannot open the task")[1].split("»")[0]
