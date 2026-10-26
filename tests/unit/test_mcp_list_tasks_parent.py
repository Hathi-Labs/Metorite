"""MCP-G5 - the ``list_tasks`` inputs ``parent_task_id`` and ``include_descendants``.

The edge sends both to ``GET /projects/tasks`` and shows ``parent_task_id`` and
``depth`` on each row. The gateway side is ``test_projects_task_subtree_r8.py``.

R7, the mutations (after a commit): **M3** the edge drops ``parent_task_id`` from
its query -> ``test_the_parent_goes_to_the_route``.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("agent_edge")
from agent_edge import tools

PARENT = str(uuid.uuid4())
_CTX = SimpleNamespace(caller=SimpleNamespace(lookup=SimpleNamespace(connection_id="c")))


def _row(depth: int | None = None) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "project_id": str(uuid.uuid4()),
            "parent_task_id": PARENT, "depth": depth}


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict[str, Any]] = []

    async def fake_get(ctx, template, not_found, *, query=(), **kwargs):
        calls.append({"query": list(query), "not_found": not_found})
        return {"rows": [_row(1)], "total": 1}

    async def no_names(ctx, projects):
        return {}

    monkeypatch.setattr(tools, "_get", fake_get)
    monkeypatch.setattr(tools, "_status_names", no_names)
    return calls


def test_both_inputs_are_in_the_schema() -> None:
    props = tools._LIST_TASKS_INPUT["properties"]
    assert props["parent_task_id"]["pattern"] == tools._UUID_PATTERN
    assert props["include_descendants"]["type"] == "boolean"
    assert tools._LIST_TASKS_PARAMS["parent_task_id"] == "parent_task_id"
    assert tools._LIST_TASKS_PARAMS["include_descendants"] == "include_descendants"
    assert {"parent_task_id", "depth"} <= set(tools.LIST_TASK_KEYS)
    text = tools.LIST_TASKS.describe()["description"]
    assert "parent_task_id" in text and "include_descendants" in text


async def test_the_parent_goes_to_the_route(sent) -> None:
    out = await tools._list_tasks(_CTX, {"parent_task_id": PARENT})
    query = dict(sent[0]["query"])
    assert query["parent_task_id"] == PARENT
    assert "include_descendants" not in query
    assert sent[0]["not_found"] == tools.TEXT_TASK_NOT_FOUND
    assert out["tasks"][0]["parent_task_id"] == PARENT
    assert out["tasks"][0]["depth"] == 1


async def test_descendants_go_to_the_route_with_the_parent(sent) -> None:
    await tools._list_tasks(
        _CTX, {"parent_task_id": PARENT, "include_descendants": True})
    assert dict(sent[0]["query"])["include_descendants"] == "true"


async def test_descendants_without_a_parent_is_refused(sent) -> None:
    with pytest.raises(tools.ToolError) as err:
        await tools._list_tasks(_CTX, {"include_descendants": True})
    assert err.value.text == tools.TEXT_DESCENDANTS_NEED_PARENT
    assert not sent
