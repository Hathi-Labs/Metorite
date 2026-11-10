"""WS-46 P11 (G22): ``show_view`` changes what the open Projects page shows.

Owner, 2026-10-22, reversing Q4 of 2026-10-06. The tool reads, resolves and
dispatches ``projects.show_view``. It writes nothing and shows no card.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import skill_projects
from skill_projects import nav

ROOT = Path(__file__).resolve().parents[2]
UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
VIEW = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def _gateway(monkeypatch, calls: list[dict[str, Any]]) -> None:
    async def fake_get(path: str, params: dict | None = None) -> Any:
        calls.append({"method": "GET", "path": path})
        if path.endswith("/views"):
            return {"rows": [{"id": VIEW, "name": "Q4", "config": {"group_by": "tag"}}]}
        if path.startswith("/projects/nodes/"):
            return {"id": UUID, "name": "Launch"}
        if path.startswith("/projects/people"):
            return {"rows": [{"email": "priya@x.in", "name": "Priya"}]}
        return {}

    monkeypatch.setattr(nav, "get", fake_get)
    monkeypatch.setattr(nav, "headers", lambda: {})


def _sent(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    sent: list[tuple[str, dict[str, Any]]] = []

    async def record(name: str, args: dict[str, Any]) -> bool:
        sent.append((name, args))
        return True

    monkeypatch.setattr(nav, "_dispatch", record)
    return sent


def test_the_canvases_are_the_pages_own() -> None:
    src = (ROOT / "workbench/control_plane/src/app/projects/lib/commands.ts").read_text(
        encoding="utf-8"
    )
    block = src[src.index("export const VIEW_MODES") :]
    block = block[: block.index("] as const") if "] as const" in block else block.index("];")]
    assert tuple(re.findall(r'id: "([a-z]+)"', block)) == nav.CANVASES


async def test_a_mode_and_a_grouping_dispatch_and_read_only_the_node(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    _gateway(monkeypatch, calls)
    sent = _sent(monkeypatch)
    out = await skill_projects.show_view(UUID, mode="Timeline", group_by="assignee")
    assert sent == [
        ("projects.show_view", {"project_id": UUID, "mode": "timeline", "group_by": "assignee"})
    ]
    assert [c["path"] for c in calls] == [f"/projects/nodes/{UUID}"]
    assert all(c["method"] == "GET" for c in calls)
    assert f"link: /projects?project={UUID}" in out


async def test_a_saved_view_goes_by_name_with_its_config(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    _gateway(monkeypatch, calls)
    sent = _sent(monkeypatch)
    await skill_projects.show_view(UUID, view_name="q4")
    assert sent[0][1]["view_id"] == VIEW
    assert sent[0][1]["view_config"] == {"group_by": "tag"}


async def test_filters_go_in_the_stored_shape_and_empty_clears(monkeypatch) -> None:
    _gateway(monkeypatch, [])
    sent = _sent(monkeypatch)
    await skill_projects.show_view(UUID, filters=json.dumps({"overdue": True, "tags": "bug"}))
    assert sent[0][1]["filters"] == {"overdue": True, "tags": "bug"}
    out = await skill_projects.show_view(UUID, filters="{}")
    assert sent[1][1]["filters"] == {}
    assert "cleared" in out


@pytest.mark.parametrize(
    ("kwargs", "words"),
    [
        ({}, "Say what to show"),
        ({"mode": "gantt"}, "mode is one of"),
        ({"group_by": "colour"}, "group_by is one of"),
        ({"group_by": "tag", "sub_group_by": "tag"}, "must differ"),
        ({"filters": '{"colour": "red"}'}, "not colour"),
    ],
)
async def test_a_bad_ask_is_refused_before_any_dispatch(monkeypatch, kwargs, words) -> None:
    _gateway(monkeypatch, [])
    sent = _sent(monkeypatch)
    out = await skill_projects.show_view(UUID, **kwargs)
    assert words in out
    assert sent == []


def test_it_is_a_read_and_has_no_card() -> None:
    src = (ROOT / "apps/skills/skill-projects/skill_projects/nav.py").read_text(encoding="utf-8")
    body = src[src.index("async def show_view") :]
    assert "_confirm" not in body
    assert not re.search(r"(?<![_a-z])(post|patch|put|delete)\(", body)
