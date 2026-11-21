"""Task 3596: one turn never makes two projects of one name under one parent.

Mutation this suite catches: ``_same_name_sibling`` removed from
``create_project``, so the second ask posts again.

Hermetic. The gateway is faked at the client, as in the other Projects files.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from skill_projects import writes as W

from tests.unit._projects_agent_fakes import approve, fake_gateway, writes

PARENT = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _tree(rows):
    return lambda call: (
        {"rows": rows, "total": len(rows)} if call["path"].endswith("/projects/tree") else {}
    )


def test_second_ask_makes_none_and_points_to_the_first(monkeypatch):
    rows = [{"id": "p1", "name": "Alpha", "parent_project_id": None, "children": []}]
    calls = fake_gateway(monkeypatch, _tree(rows))
    approve(monkeypatch)
    out = asyncio.run(W.create_project("  alpha "))
    assert "already exists" in out and "p1" in out
    assert writes(calls) == []


def test_other_parent_or_archived_does_not_block(monkeypatch):
    rows = [
        {"id": "p1", "name": "Alpha", "parent_project_id": PARENT, "children": []},
        {"id": "p2", "name": "Alpha", "parent_project_id": None,
         "archived_at": "2026-01-01", "children": []},
    ]
    assert asyncio.run(_twin(monkeypatch, rows)) is None


async def _twin(monkeypatch, rows):
    fake_gateway(monkeypatch, _tree(rows))
    return await W._same_name_sibling("Alpha", None)
