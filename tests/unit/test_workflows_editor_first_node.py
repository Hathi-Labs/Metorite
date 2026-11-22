"""Task 3382 (RUNG 0): the workflow editor names its first node with a free id.

The engine keeps ``trigger`` and ``vars`` as the roots of its state
(``templating.RESERVED_ROOTS``). ``graph._parse`` refuses a node with one of
those ids, and ``validate_graph`` then stops with ``reserved_id``. The editor
named the first node of a new workflow ``trigger``, so no workflow from New
workflow passed Validate, Test or Publish.

The editor now takes the id of the first node from ``FIRST_NODE_ID`` in
``workbench/control_plane/src/app/workflows/lib/triggerId.ts``. This file reads
that id as text, holds it against the real ``RESERVED_ROOTS``, and runs
``validate_graph`` on the graph that the editor sends for a new workflow with
one Output step. ``triggerId.test.ts`` holds the rename of an old draft.

**Mutations (each was run and turned this file red):**

* M1. Put ``id: "trigger"`` back in the first node of ``page.tsx``: the page
  case turns red.
* M2. Set ``FIRST_NODE_ID`` back to ``"trigger"``: the id and the graph cases
  turn red.
"""
from __future__ import annotations

import re
from pathlib import Path

from gateway.routes.workflows.engine.graph import compile_graph, validate_graph
from gateway.routes.workflows.engine.templating import RESERVED_ROOTS

_ROOT = Path(__file__).resolve().parents[2]
_APP = _ROOT / "workbench" / "control_plane" / "src" / "app"
_RULES = _APP / "workflows" / "lib" / "triggerId.ts"
_PAGE = _APP / "studio" / "workflows" / "[id]" / "page.tsx"


def _first_node_id() -> str:
    text = _RULES.read_text(encoding="utf-8")
    match = re.search(r'export const FIRST_NODE_ID = "([^"]+)"', text)
    assert match, "triggerId.ts must declare FIRST_NODE_ID as a string literal"
    return match.group(1)


def _editor_graph(first: str) -> dict:
    """The graph that the editor sends for a new workflow with one Output step.

    It has the shape of ``toGraph`` in ``page.tsx``: the node of
    ``newWorkflowGraph()``, an Output from the palette, and the edge that
    ``onConnect`` names.
    """
    out = "output_1abc"
    return {
        "nodes": [
            {
                "id": first,
                "type": "trigger",
                "position": {"x": 120, "y": 60},
                "data": {"label": "Trigger", "config": {"kind": "manual"}},
            },
            {
                "id": out,
                "type": "output",
                "position": {"x": 140, "y": 220},
                "data": {"label": "Output", "config": {"value": ""}},
            },
        ],
        "edges": [
            {
                "id": f"e_{first}_out_{out}",
                "source": first,
                "target": out,
                "sourceHandle": None,
            }
        ],
    }


def test_the_first_node_id_is_not_a_reserved_root() -> None:
    first = _first_node_id()
    assert first not in RESERVED_ROOTS
    assert set(RESERVED_ROOTS) == {"trigger", "vars"}


def test_the_editor_graph_of_a_new_workflow_validates() -> None:
    graph = _editor_graph(_first_node_id())
    assert validate_graph(graph) == []
    compiled = compile_graph(graph)
    assert compiled["entry"] == _first_node_id()


def test_the_old_first_node_id_is_refused() -> None:
    """The reason of the fault, pinned: the old id fails at the parse."""
    issues = validate_graph(_editor_graph("trigger"))
    assert [(i.code, i.node_id) for i in issues] == [("reserved_id", "trigger")]


def test_the_page_builds_the_first_node_from_the_rules() -> None:
    page = _PAGE.read_text(encoding="utf-8")
    assert re.search(r"""\bid:\s*["']trigger["']""", page) is None, (
        "page.tsx names a node 'trigger'. Take the first node from newWorkflowGraph()."
    )
    assert "newWorkflowGraph()" in page
    assert "normalizeTriggerId(" in page
