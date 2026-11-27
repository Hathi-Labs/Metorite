"""WS-61 KB-4a — every Knowledge route is mapped or excluded, and this test says so.

Spec: ``project-docs/specs/knowledge_base.md`` §5.3 and §8.1. The model is
``tests/unit/test_projects_chat_coverage.py`` (D-PM-37).

This file walks the REAL ``gateway.routes.knowledge.router`` and holds
``acb_skills.knowledge_manifest`` to it in both directions:

1. a route the manifest does not carry fails, naming the route, so a pull
   request that adds a Knowledge route cannot merge until its author decides
   what the chat does with it;
2. a manifest row that names a route the router does not serve fails,
   because a stale tool is a lie the agent will tell.

Then the manifest is held to the tools: each read tool is in the manifest
with class A and a read-only annotation. The writes with a tool are
``POST /knowledge/proposals`` (KB-5a), and the page and space create and edit
of PAR-3 (task 3105). The client refuses a route the manifest does not give
to the tool.

Mutations this file catches (R7):

* a new route in the router and not in the manifest -> the first test;
* a manifest row for a route that is gone -> the second test;
* a write route other than the proposals and the PAR-3 writes that gets a
  tool -> ``test_no_write_has_a_tool``, and a publish, accept, reject, restore or
  archive route with a tool -> ``test_kb_proposals.py``;
* the client sends a route that the manifest does not map to the tool ->
  ``test_the_client_refuses_an_unmapped_route`` in ``test_knowledge_tools.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_skills import knowledge_manifest as m
from acb_skills import knowledge_tools as kt


def _router_routes() -> set[tuple[str, str]]:
    from gateway.routes.knowledge import router

    out: set[tuple[str, str]] = set()
    for route in router.routes:
        for method in getattr(route, "methods", None) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            out.add((method, getattr(route, "path", "")))
    return out


def _manifest_routes() -> set[tuple[str, str]]:
    return {(r.method, r.path) for r in m.MANIFEST}


def test_every_knowledge_route_is_in_the_manifest() -> None:
    missing = sorted(_router_routes() - _manifest_routes())
    assert not missing, (
        "These Knowledge routes are not in the chat manifest. Add one row each "
        "to packages/acb_skills/acb_skills/knowledge_manifest.py: a tool and a "
        "class, the slice that plans the tool, or class X with a reason:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in missing)
    )


def test_no_manifest_row_names_a_route_the_router_does_not_serve() -> None:
    stale = sorted(_manifest_routes() - _router_routes())
    assert not stale, (
        "These manifest rows name routes the Knowledge router does not serve:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in stale)
    )


def test_the_router_is_not_empty_and_has_no_duplicate_rows() -> None:
    assert len(_router_routes()) >= 14, "non-vacuity: the router carries the KB-1 and KB-3 routes"
    seen = [(r.method, r.path) for r in m.MANIFEST]
    assert len(seen) == len(set(seen))


#: The reads of KB-4a, the eight reads of PAR-3b (task 3901), and the two
#: proposals of KB-5a.
READ_TOOLS = {
    "list_spaces", "read_page", "list_pages", "list_recent", "read_page_version",
    "list_backlinks", "list_proposals", "list_sources", "list_gaps", "list_maintenance",
}
PROPOSE_TOOLS = {"propose_page", "propose_edit"}
#: PAR-3 (task 3105): the write route of each of the member's own writes.
WRITE_TOOLS = {
    "create_page": ("POST", "/knowledge/pages"),
    "edit_page": ("PATCH", "/knowledge/pages/{page_id}"),
    "create_space": ("POST", "/knowledge/spaces"),
    "edit_space": ("PATCH", "/knowledge/spaces/{space_id}"),
    # PAR-3b (task 3901): the move and the revert of a draft.
    "move_page": ("POST", "/knowledge/pages/{page_id}/move"),
    "revert_page": ("POST", "/knowledge/pages/{page_id}/revert"),
}


def test_the_tools_map_to_their_routes() -> None:
    assert m.route_for("GET", "/knowledge/spaces").tool == "list_spaces"
    row = m.route_for("GET", "/knowledge/pages/0f8fad5b-d9cb-469f-a165-70867728950e")
    assert row is not None and row.tool == "read_page" and row.cls == "A"
    propose = m.route_for("POST", "/knowledge/proposals")
    assert propose is not None and propose.cls == "B"
    assert {propose.tool, *propose.also} == PROPOSE_TOOLS
    for tool, (method, path) in WRITE_TOOLS.items():
        row = next(r for r in m.MANIFEST if (r.method, r.path) == (method, path))
        assert row.tool == tool and not row.also and row.cls in ("B", "C"), tool
    assert m.tools() == READ_TOOLS | PROPOSE_TOOLS | set(WRITE_TOOLS)


def test_no_write_has_a_tool() -> None:
    """KB-4a builds reads. KB-5a gives one write route to the proposals. PAR-3
    and PAR-3b give the six routes of :data:`WRITE_TOOLS`, and no other."""
    built = {("POST", "/knowledge/proposals"), *WRITE_TOOLS.values()}
    for row in m.MANIFEST:
        if row.method != "GET":
            assert row.cls in ("B", "C", "X"), (row.method, row.path)
            if (row.method, row.path) in built:
                continue
            assert not row.tool and not row.also, f"{row.method} {row.path} has a tool"
            assert row.planned or row.reason, (row.method, row.path)


def test_the_grants_route_is_never_on_chat() -> None:
    row = m.route_for("PUT", "/knowledge/spaces/x/grants")
    assert row is not None and row.cls == "X" and "D-PM-40" in row.reason


def test_each_built_tool_is_exported_with_its_risk() -> None:
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    for name in m.tools():
        assert name in kt.__all__, name
    for name in READ_TOOLS:
        assert m.tool_class(name) == "A"
        assert TOOL_ANNOTATIONS[name] == {
            "read_only": True, "destructive": False, "idempotent": True, "open_world": False,
        }
    for name in PROPOSE_TOOLS | set(WRITE_TOOLS):
        assert m.tool_class(name) in ("B", "C")
        assert TOOL_ANNOTATIONS[name] == {
            "read_only": False, "destructive": False, "idempotent": False, "open_world": False,
        }


@pytest.mark.parametrize(("cls", "tool", "reason", "planned"), [
    ("A", "", "", ""),
    ("X", "read_page", "why", ""),
    ("X", "", "", ""),
    ("B", "a_tool", "", "KB-5"),
    ("Q", "a_tool", "", ""),
])
def test_a_bad_row_fails_at_import(cls: str, tool: str, reason: str, planned: str) -> None:
    with pytest.raises(ValueError):
        m.Route("GET", "/knowledge/x", tool, cls, reason=reason, planned=planned)


def test_allowed_refuses_a_route_of_another_tool_and_an_unplanned_one() -> None:
    page = "/knowledge/pages/0f8fad5b-d9cb-469f-a165-70867728950e"
    assert m.allowed("GET", page, "read_page") == (True, "")
    assert m.allowed("GET", page, "list_spaces")[0] is False
    assert m.allowed("PATCH", page, "read_page")[0] is False
    assert m.allowed("GET", "/knowledge/search", "read_page")[0] is False
    assert m.allowed("GET", "/admin/members", "read_page")[0] is False
