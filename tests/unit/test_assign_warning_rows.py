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


def _gateway(not_notified: list[str] | None = None):
    def respond(call: dict) -> Any:
        if call["path"] == "/projects/people/names":
            asked = str(call["params"].get("emails", "")).split(",")
            return {"names": {a: "Kim" for a in asked if a in (KNOWN, OFF)}}
        if call["path"] == "/projects/people/reach":
            return {"not_reachable": {OFF: "inactive"}}
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


async def test_the_card_names_a_suspended_person(monkeypatch) -> None:
    asked = approve(monkeypatch)
    fake_gateway(monkeypatch, _gateway())
    await skill_projects.assign(UUID, f"{KNOWN},{OFF},{STRANGER}")
    context = asked[0]["context"]
    assert f"suspended: «{OFF}»" in context
    assert f"not in the directory: «{STRANGER}»" in context
    assert f"suspended: «{KNOWN}»" not in context


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
