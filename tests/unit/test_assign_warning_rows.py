"""Task 3635 — ``assign`` warns with the keyed rows, and its receipt names who got no notice.

Code: ``skill_projects.writes.assign``. Sibling: ``test_card_stranger_warning.py``
and ``test_assignee_reach_rows.py``.

a. The card of a new holder who is a stranger carries ``not in the directory``
   as a keyed row, and no free line.
b. A holder who stays is not checked.
c. The receipt names the addresses in ``not_notified``.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

import skill_projects

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes
from tests.unit.test_projects_agent_writes import UUID, responder

STRANGER, KNOWN, OFF = "z@y.test", "k@y.test", "off@y.test"


@pytest.fixture(autouse=True)
def _fresh_task():
    """``assign`` writes the new holders onto the task dict that the fake
    gateway hands out, and the dict is shared by the whole module. Each test
    starts from the holder of the fixture."""
    from tests.unit.test_projects_agent_writes import TASK

    TASK["assignees"] = ["a@x.io"]
    yield
    TASK["assignees"] = ["a@x.io"]


def _gateway(not_notified: list[str] | None = None):
    def respond(call: dict) -> Any:
        if call["path"] == "/projects/people/names":
            asked = str(call["params"].get("emails", "")).split(",")
            return {"names": {a: "Kim" for a in asked if a in (KNOWN, OFF)}}
        if call["path"] == "/projects/people/reach":
            return {"not_reachable": [OFF]}
        if call["method"] == "PUT" and call["path"].endswith("/assignees"):
            return {"assignees": [], "not_notified": not_notified or []}
        return responder(call)
    return respond


async def test_the_card_names_a_stranger_as_a_keyed_row(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway())
    await skill_projects.assign(UUID, STRANGER)
    context = asked[0]["context"]
    assert f"not in the directory: «{STRANGER}»" in context
    assert "The people directory does not know" not in context


async def test_the_card_names_a_person_who_cannot_open_the_task(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway())
    await skill_projects.assign(UUID, f"{KNOWN},{OFF},{STRANGER}")
    context = asked[0]["context"]
    assert f"cannot open the task: «{OFF}»" in context
    assert f"not in the directory: «{STRANGER}»" in context
    assert KNOWN not in context.split("cannot open the task")[1]


async def test_a_clear_has_no_warning_and_no_lookup(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.assign(UUID, "")
    assert "/projects/people/names" not in [c["path"] for c in calls]


async def test_the_receipt_names_who_got_no_notice(monkeypatch) -> None:
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway([STRANGER, STRANGER, KNOWN]))
    out = await skill_projects.assign(UUID, f"{KNOWN},{STRANGER}")
    assert out.startswith("Assigned:")
    assert f"«{STRANGER}, {KNOWN}» will not get a notice, and cannot open this task yet." in out
    assert [c for c in writes(calls) if c["path"].endswith("/assignees")]


async def test_a_receipt_with_everybody_notified_is_as_before(monkeypatch) -> None:
    approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway([]))
    out = await skill_projects.assign(UUID, KNOWN)
    assert out.startswith("Assigned:") and "will not get a notice" not in out


async def test_a_holder_who_stays_is_not_checked(monkeypatch) -> None:
    """Only the NEW holders are asked about. The fake task holds a@x.io."""
    approve(monkeypatch)
    calls = fake_gateway(monkeypatch, _gateway())
    await skill_projects.assign(UUID, "a@x.io")
    # Nobody is added, so the directory is not read at all.
    assert "/projects/people/names" not in [c["path"] for c in calls]
    calls.clear()
    await skill_projects.assign(UUID, f"a@x.io,{KNOWN}")
    asked = [c for c in calls if c["path"] == "/projects/people/names"]
    assert len(asked) == 1 and asked[0]["params"] == {"emails": KNOWN}
