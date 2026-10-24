"""The class X pin (WS-64 OP-6, D-OP-9, owner 2026-10-23).

Spec: ``project-docs/specs/one_projects_app.md`` §5a and §9 OP-6.

D-OP-9 says every act of the Projects app is reachable from the chat. So the
routes that stay class X are a short, named list, and each has a reason.
``test_projects_chat_coverage.py`` already refuses a route that the manifest
does not carry. This file refuses a route that moves to class X (or leaves it)
without somebody editing this list.

Two dicts hold the whole class X set, keyed ``"METHOD path"``:

* :data:`STAYS_X` is where OP-6 ends. These routes are client plumbing, a
  second door to a read the chat has, another app's feature, or a door for
  another caller. The groups are the three "Stays X" rows of §5a.
* :data:`PENDING_OP6` is the work that later slices of OP-6 still owe, each
  with the slice id. Every slice that ships deletes its lines here. The last
  slice leaves the dict empty, and then the pin is exactly :data:`STAYS_X`.

R7 fence: the test fails on a route in the manifest that no dict names, on a
route a dict names that is no longer class X, and on a route in both dicts.
Mutation: set one ``Route(..., "X")`` row to class B in ``manifest.py`` and
the first test fails. Add a new class X row and the first test fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from skill_projects import manifest as m

SPEC = Path(__file__).resolve().parents[2] / "project-docs/specs/one_projects_app.md"

#: The groups of §5a. A value in the dicts below must be one of these.
GROUPS = frozenset({
    "plumbing",       # view state, positions, delta, raw bytes, /nodes, /search/refs, /vocabulary
    "import",         # the import wizard
    "planner",        # the Calendar planner routes
    "personal-doors", # My Tasks' own capture, organize, checklist, purge and lanes doors
    "admin-count",    # the count shown before an admin deletes a shared entry
    "mcp-door",       # a door for an outside agent only
    "card-answer",    # the answer to an agent request, given on the task card only
})

#: Where OP-6 ends. 28 routes.
STAYS_X: dict[str, str] = {
    # client plumbing, or a second door to a read the chat has
    "GET /projects/nodes": "plumbing",
    "GET /projects/views/{view_id}/state": "plumbing",
    "PUT /projects/views/{view_id}/state": "plumbing",
    "GET /projects/views/{view_id}/positions": "plumbing",
    "PUT /projects/views/{view_id}/positions": "plumbing",
    "GET /projects/search/refs": "plumbing",
    "GET /projects/delta/tasks": "plumbing",
    "GET /projects/vocabulary": "plumbing",
    "GET /projects/attachments/{attachment_id}/{filename}": "plumbing",
    # the import wizard needs the review grid
    "POST /projects/import/runs": "import",
    "GET /projects/import/runs": "import",
    "GET /projects/import/runs/{run_id}": "import",
    "PUT /projects/import/runs/{run_id}/mapping": "import",
    "POST /projects/import/runs/{run_id}/apply": "import",
    "POST /projects/import/runs/{run_id}/discard": "import",
    # the Calendar app owns the planner
    "POST /projects/my/calendar/plan": "planner",
    "POST /projects/my/calendar/replan": "planner",
    "POST /projects/my/calendar/rollover": "planner",
    "GET /projects/my/calendar/estimate-stats": "planner",
    # My Tasks' own doors. The chat has create_personal_task, update_task,
    # add_subtasks and delete_task, which give the same acts.
    "POST /projects/my/project": "personal-doors",
    "POST /projects/my/tasks/batch": "personal-doors",
    "POST /projects/my/tasks/{task_id}/organize": "personal-doors",
    "POST /projects/my/tasks/{task_id}/subtasks": "personal-doors",
    "DELETE /projects/my/tasks/{task_id}": "personal-doors",
    "GET /projects/my/tasks/{task_id}/lanes": "personal-doors",
    # the count an admin reads in Projects settings
    "GET /projects/vocabulary/{kind}/{row_id}/impact": "admin-count",
    # EA3-51, pinned by test_mcp_edge_coverage.py
    "POST /projects/tasks/{task_id}/agent-delete": "mcp-door",
    # WS-64 OP-6e, D-OP-13 and EA3-40: only the run's member answers, on the card
    "POST /projects/agent-runs/{run_id}/archive-approval": "card-answer",
}

#: Routes that later slices of OP-6 move out of class X. Each value is the slice.
#: Empty since WS-64 OP-6d, the last slice that owned a route. The pin is now
#: exactly :data:`STAYS_X`.
PENDING_OP6: dict[str, str] = {}

#: The slices that may own a pending route. OP-6e (agent runs) owns no route.
SLICES: frozenset[str] = frozenset()


def _class_x() -> dict[str, str]:
    return {f"{r.method} {r.path}": r.reason for r in m.MANIFEST if r.cls == "X"}


def test_the_class_x_routes_are_exactly_the_two_lists() -> None:
    now = set(_class_x())
    pinned = set(STAYS_X) | set(PENDING_OP6)
    assert now - pinned == set(), (
        "a route is class X and no list names it. D-OP-9: give it a chat tool, or "
        f"add it to STAYS_X in this file with a reason in the spec: {sorted(now - pinned)}"
    )
    assert pinned - now == set(), (
        "a pinned route is no longer class X. Delete its line here: "
        f"{sorted(pinned - now)}"
    )


def test_no_route_is_in_both_lists_and_every_value_is_known() -> None:
    assert set(STAYS_X) & set(PENDING_OP6) == set()
    assert set(STAYS_X.values()) <= GROUPS
    assert set(PENDING_OP6.values()) <= SLICES
    # Every group is used, so a group nobody needs leaves the list.
    assert set(STAYS_X.values()) == GROUPS


def test_the_counts_the_spec_states() -> None:
    """39 routes were class X on 2026-10-23. OP-6a took 3, OP-6c took 3, OP-6b
    took 2 and OP-6d took 4. The 27 that remain all stay. OP-6e moved the
    archive approval INTO class X (D-OP-13), so 28 stay."""
    assert len(STAYS_X) == 28
    assert len(PENDING_OP6) == 0
    assert len(_class_x()) == len(STAYS_X) == 28


def test_the_area_routes_left_class_x_for_their_tools() -> None:
    for verb, path, tool, cls in (
        ("POST", "/projects/my/areas", "create_area", "B"),
        ("PATCH", "/projects/my/areas/{area_id}", "rename_area", "C"),
        ("DELETE", "/projects/my/areas/{area_id}", "delete_area", "C"),
    ):
        row = m.route_for(verb, path)
        assert row is not None and (row.tool, row.cls) == (tool, cls)


def test_the_nudge_export_and_attach_routes_left_class_x_for_their_tools() -> None:
    """WS-64 OP-6c. The nudge and the attach take a card. The export is a read."""
    for verb, path, tool, cls in (
        ("POST", "/projects/tasks/{task_id}/nudge", "nudge_task", "C"),
        ("GET", "/projects/export/tasks.csv", "export_tasks", "A"),
        ("POST", "/projects/tasks/{task_id}/attachments", "attach_to_task", "C"),
    ):
        row = m.route_for(verb, path)
        assert row is not None and (row.tool, row.cls) == (tool, cls)


def test_the_grant_routes_left_class_x_for_their_tools() -> None:
    """WS-64 OP-6b (D-OP-9 reverses D-PM-40). Each grant write takes a card, and
    the route decides who may write it (D-OP-11)."""
    for verb, path, tool in (
        ("POST", "/projects/nodes/{project_id}/grants", "grant_access"),
        ("DELETE", "/projects/nodes/{project_id}/grants/{grant_id}", "revoke_access"),
    ):
        row = m.route_for(verb, path)
        assert row is not None and (row.tool, row.cls) == (tool, "C")
    assert not hasattr(m, "_GRANT_REASON")


def test_the_report_delivery_routes_left_class_x_for_their_tools() -> None:
    """WS-64 OP-6d (D-OP-9). The list is a read. The three writes take a card, and
    the route decides who may change a report (the author or an admin)."""
    for verb, path, tool, cls in (
        ("GET", "/projects/reports/{report_id}/recipients", "report_recipients", "A"),
        ("POST", "/projects/reports/{report_id}/recipients", "add_report_recipient", "C"),
        ("DELETE", "/projects/reports/{report_id}/recipients/{email}",
         "remove_report_recipient", "C"),
        ("PATCH", "/projects/reports/{report_id}/schedule", "set_report_schedule", "C"),
    ):
        row = m.route_for(verb, path)
        assert row is not None and (row.tool, row.cls) == (tool, cls)
    assert not hasattr(m, "_DELIVERY_REASON")


def test_the_agent_request_list_is_a_read_and_the_answer_stays_class_x() -> None:
    """WS-64 OP-6e (D-OP-13, EA3-40). The chat lists the waiting requests. The
    answer route is class X, so no chat tool answers one."""
    row = m.route_for("GET", "/projects/agent-runs")
    assert row is not None and (row.tool, row.cls) == ("waiting_agent_requests", "A")
    answer = m.route_for("POST", "/projects/agent-runs/{run_id}/archive-approval")
    assert answer is not None and answer.cls == "X" and "D-OP-13" in answer.reason
    assert "approve_task_archive" not in m.PLANNED
    assert "approve_task_archive" not in {r.tool for r in m.MANIFEST}


def test_op6_is_finished_so_no_route_is_pending() -> None:
    """The last slice that owned a route has shipped (OP-6e owns none)."""
    assert PENDING_OP6 == {}


def test_the_spec_names_every_stays_x_group() -> None:
    """§5a carries the three "Stays X" rows, so a reader finds the reason there."""
    text = SPEC.read_text(encoding="utf-8")
    assert text.count("| Stays X |") == 3
