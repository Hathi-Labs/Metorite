"""SBX-F2: the allowlist of the sandbox door (SBX-2, Metorite task 3801).

Spec: ``project-docs/specs/agent_sandbox.md`` §12.2 decisions 2 and 11, and
done-when 11.

* ``SANDBOX_ROUTES`` is exactly the 68 pairs that decision 2 names: 30 of
  CRM and 38 of Projects. The lists below are written by hand, so a row that
  sets ``sandbox=True`` with no edit here fails, and so does a row that
  drops it.
* Each pair is a GET row of class A of CRM or Projects, that the live
  gateway serves at that template. No pair is under ``/agent-access``, is a
  download, or is one of the rows that decision 2 keeps off.
* ``Route.__post_init__`` refuses ``sandbox=True`` on a row that is not
  class A, or not GET.
* Deny by default (decision 11): a row that does not say ``sandbox=True`` is
  off the door, so a new route needs no entry in an exclusion list.
"""
from __future__ import annotations

import pytest
from acb_common import app_manifests
from acb_common.app_manifests import base
from acb_common.app_manifests.crm import CRM
from acb_common.app_manifests.projects import PROJECTS_DOOR

#: Decision 2, the CRM rows that SBX-2 opens (30).
CRM_OPEN: frozenset[str] = frozenset({
    # Records (8)
    "/crm/leads", "/crm/leads/{record_id}",
    "/crm/deals", "/crm/deals/{record_id}",
    "/crm/contacts", "/crm/contacts/{record_id}",
    "/crm/organizations", "/crm/organizations/{record_id}",
    # Modules (4)
    "/crm/modules", "/crm/modules/{module}/records",
    "/crm/modules/{module}/records/{record_id}",
    "/crm/modules/{module}/records/{record_id}/timeline",
    # Reports (4)
    "/crm/reports/pipeline", "/crm/reports/funnel", "/crm/reports/win-loss",
    "/crm/reports/owners",
    # Pipeline (3)
    "/crm/pipeline", "/crm/pipelines", "/crm/aggregate/stage-dwell",
    # Shape (6)
    "/crm/fields/{entity}", "/crm/fields-search", "/crm/catalogue",
    "/crm/statuses/{kind}", "/crm/lost-reasons", "/crm/mode",
    # Other (5)
    "/crm/owners", "/crm/activities/{activity_id}", "/crm/change-history",
    "/crm/deals/{deal_id}/contacts", "/crm/views",
})

#: Decision 2, the Projects rows that SBX-2 opens (38).
PROJECTS_OPEN: frozenset[str] = frozenset({
    # In MCP_ROUTES (17)
    "/projects/tree", "/projects/nodes/{project_id}", "/projects/nodes/{project_id}/updates",
    "/projects/tasks", "/projects/tasks/stats", "/projects/tasks/{task_id}",
    "/projects/tasks/{task_id}/timeline", "/projects/tasks/{task_id}/attachments",
    "/projects/tasks/{task_id}/relations", "/projects/assignable-members",
    "/projects/nodes/{project_id}/statuses", "/projects/nodes/{project_id}/types",
    "/projects/nodes/{project_id}/fields", "/projects/nodes/{project_id}/tags",
    "/projects/plan/order", "/projects/plan/analysis", "/projects/search",
    # Summaries and views (5)
    "/projects/summary", "/projects/nodes/{project_id}/summary",
    "/projects/nodes/{project_id}/views", "/projects/calendar", "/projects/intake",
    # The member's own (6)
    "/projects/my/project", "/projects/my/contexts", "/projects/my/areas",
    "/projects/my/reminders", "/projects/my/today", "/projects/notifications",
    # Analytics (4)
    "/projects/analytics/stuck", "/projects/analytics/throughput",
    "/projects/analytics/finished", "/projects/analytics/load",
    # Reports (5)
    "/projects/reports", "/projects/reports/templates", "/projects/reports/subjects",
    "/projects/reports/{report_id}", "/projects/reports/{report_id}/render",
    # Recurrence (1)
    "/projects/tasks/{task_id}/recurrence",
})

#: Decision 2, the class A GET rows that stay off, with the reason of each
#: group. Each must be a real class A GET row, and none may be on the list.
KEPT_OFF: frozenset[str] = frozenset({
    # CRM: a write, a model call or the evidence cache
    "/crm/aggregate", "/crm/terms/resolve", "/crm/mapping/plans", "/crm/notes/search",
    "/crm/questions", "/crm/questions/ask",
    # CRM: a file download
    "/crm/export/leads.csv", "/crm/export/deals.csv", "/crm/export/contacts.csv",
    "/crm/export/organizations.csv",
    # CRM: the timelines that hold mail (SBX-2b)
    "/crm/leads/{record_id}/timeline", "/crm/deals/{record_id}/timeline",
    "/crm/contacts/{record_id}/timeline", "/crm/organizations/{record_id}/timeline",
    # CRM: connection and sync state
    "/crm/connection", "/crm/sync/status", "/crm/sync/health", "/crm/cutover/status",
    # CRM: the admin rows
    "/crm/field-rules", "/crm/conversion-mapping", "/crm/analysis/status",
    "/crm/cutover/preview", "/crm/mapping/plans/{version}", "/crm/pipelines/preview",
    "/crm/source-profile", "/crm/questions/{question_id}/sum-check", "/crm/settings",
    "/crm/settings/counts", "/crm/mapping/suggestions",
    # CRM: the assistant's questions, rules and digests, and sync counts
    "/crm/questions/mine", "/crm/rules", "/crm/digests", "/crm/verified-queries/{key}",
    "/crm/sync/short-reads", "/crm/sync/mirror-age",
    # Projects: a GitHub call, grant data and names with no visibility check
    "/projects/tasks/{task_id}/refs", "/projects/nodes/{project_id}/grants",
    "/projects/nodes/{project_id}/audience", "/projects/nodes/{project_id}/authorities",
    "/projects/nodes/{project_id}/status-set",
    # Projects: reads by assignment with no call of the visibility seam
    "/projects/assigned-to-me", "/projects/my/inbox", "/projects/my/tasks/{task_id}",
    "/projects/my/tasks/{task_id}/lanes", "/projects/my/calendar", "/projects/my/led",
    # Projects: other people, their skills or their absences
    "/projects/assignees", "/projects/decisions", "/projects/people/names",
    "/projects/people/reach", "/projects/tasks/{task_id}/candidates", "/projects/candidates",
    "/projects/reports/{report_id}/recipients", "/projects/tasks/{task_id}/watchers",
    "/projects/nodes/{project_id}/watchers", "/projects/templates",
    # Projects: HR data
    "/projects/analytics/capacity", "/projects/analytics/outlook",
    "/projects/analytics/conflicts", "/projects/analytics/rebalance",
    "/projects/analytics/dataset",
    # Projects: counts of hidden items
    "/projects/nodes/{project_id}/cycles", "/projects/cycles/{cycle_id}",
    "/projects/nodes/{project_id}/vocabulary/reach",
    # Projects: the surface of agent runs and questions
    "/projects/tasks/{task_id}/agent-runs", "/projects/agent-runs",
    "/projects/agent-runs/{run_id}", "/projects/{project_id}/agent-summary",
    "/projects/agents/overview", "/projects/agents/{agent_key}/runs",
    "/projects/needs-attention", "/projects/agent-questions",
    # Projects: a file download
    "/projects/export/tasks.csv",
})


def _expected() -> frozenset[tuple[str, str]]:
    return frozenset(("GET", p) for p in CRM_OPEN | PROJECTS_OPEN)


def _rows() -> dict[tuple[str, str], tuple[str, base.Route]]:
    return {(r.method, r.path): (one.id, r) for one in (CRM, PROJECTS_DOOR) for r in one.routes}


# ── done-when 11: the 68 rows, and only them ────────────────────────────────


def test_the_hand_lists_have_the_counts_of_the_spec():
    assert len(CRM_OPEN) == 30
    assert len(PROJECTS_OPEN) == 38
    assert not (CRM_OPEN | PROJECTS_OPEN) & KEPT_OFF


def test_sandbox_routes_is_exactly_the_68_rows_of_decision_2():
    assert _expected() == app_manifests.SANDBOX_ROUTES
    assert len(app_manifests.SANDBOX_ROUTES) == 68


def test_each_pair_is_a_get_row_of_class_a_of_crm_or_projects():
    rows = _rows()
    for pair in app_manifests.SANDBOX_ROUTES:
        app_id, row = rows[pair]
        assert app_id in ("crm", "projects"), pair
        assert row.method == "GET" and row.cls == "A" and row.sandbox, pair
        assert not row.api, pair


def test_no_pair_is_class_b_c_or_x_a_download_or_agent_access():
    rows = _rows()
    for pair in app_manifests.SANDBOX_ROUTES:
        _, row = rows[pair]
        assert row.cls not in ("B", "C", "X"), pair
        assert not row.path.startswith("/agent-access"), pair
        assert "/export/" not in row.path, pair
        assert not row.path.endswith((".csv", ".zip")), pair
        assert "{filename}" not in row.path, pair


def test_each_kept_off_row_is_a_real_class_a_get_row_and_stays_off():
    rows = _rows()
    for path in KEPT_OFF:
        _, row = rows[("GET", path)]
        assert row.cls == "A", path
        assert not row.sandbox, path
        assert ("GET", path) not in app_manifests.SANDBOX_ROUTES, path


def test_each_pair_is_served_by_the_live_gateway_at_that_template():
    """The template must be the one that ``deps._matched_template`` gives,
    so the door matches the pair. FastAPI's own route contexts give it."""
    from fastapi.routing import iter_route_contexts
    from gateway.main import app

    served: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        for method in getattr(context, "methods", None) or ():
            served.add((method, getattr(context, "path", "")))
    missing = sorted(app_manifests.SANDBOX_ROUTES - served)
    assert not missing, missing


def test_every_sandbox_app_is_crm_or_projects_and_neither_is_on_the_mcp_door():
    assert [one.id for one in app_manifests.SANDBOX_APPS] == ["crm", "projects"]
    assert {"crm:read", "projects:read"} == app_manifests.SANDBOX_READ_SCOPES
    for one in app_manifests.SANDBOX_APPS:
        assert one not in app_manifests.APPS


def test_each_pair_needs_the_read_scope_of_its_own_app():
    for path in CRM_OPEN:
        assert app_manifests.sandbox_scope(("GET", path)) == "crm:read", path
    for path in PROJECTS_OPEN:
        assert app_manifests.sandbox_scope(("GET", path)) == "projects:read", path
    assert app_manifests.sandbox_scope(("GET", "/crm/aggregate")) is None
    assert app_manifests.sandbox_scope(("POST", "/crm/leads")) is None
    assert app_manifests.sandbox_scope(("GET", None)) is None


def test_a_class_x_row_of_a_sandbox_app_is_excluded():
    assert app_manifests.sandbox_excluded(("GET", "/crm/export/all.zip"))
    assert app_manifests.sandbox_excluded(
        ("GET", "/projects/attachments/{attachment_id}/{filename}"))
    assert not app_manifests.sandbox_excluded(("GET", "/crm/aggregate"))
    assert not app_manifests.sandbox_excluded(("GET", "/crm/leads"))
    assert not app_manifests.sandbox_excluded(("GET", None))


# ── decision 2: the rule of ``Route.sandbox`` ───────────────────────────────


@pytest.mark.parametrize(("method", "cls", "kwargs"), [
    ("POST", "B", {"tool": "t"}),
    ("PATCH", "B", {"tool": "t"}),
    ("DELETE", "C", {"tool": "t"}),
    ("POST", "A", {"tool": "t"}),
    ("GET", "B", {"tool": "t"}),
    ("GET", "X", {"reason": "a file"}),
])
def test_the_route_refuses_sandbox_on_a_row_that_is_not_a_class_a_get(method, cls, kwargs):
    extra = {} if cls == "X" else {"off_door": "later"}
    with pytest.raises(ValueError, match="sandbox"):
        base.Route(method, "/x/{id}", kwargs.pop("tool", ""), cls, sandbox=True,
                   **kwargs, **extra)


def test_the_route_takes_sandbox_on_a_class_a_get_and_is_off_by_default():
    row = base.Route("GET", "/x/{id}", "t", "A", off_door="later", sandbox=True)
    assert row.sandbox
    assert base.Route("GET", "/x/{id}", "t", "A", off_door="later").sandbox is False


def test_deny_by_default_a_row_without_the_mark_is_off_the_door():
    """Decision 11. Every other row of the two apps is off, with no list."""
    for one in app_manifests.SANDBOX_APPS:
        for row in one.routes:
            if not row.sandbox:
                assert (row.method, row.path) not in app_manifests.SANDBOX_ROUTES


# ── SBX-3a (§12.5 decision 3): the write rows ───────────────────────────────

#: Decision 3, the CRM rows that SBX-3 opens (16), with the class of each.
CRM_WRITE: dict[tuple[str, str], str] = {
    # Create (4)
    ("POST", "/crm/leads"): "B", ("POST", "/crm/deals"): "B",
    ("POST", "/crm/contacts"): "B", ("POST", "/crm/organizations"): "B",
    # Change (4)
    ("PATCH", "/crm/leads/{record_id}"): "B", ("PATCH", "/crm/deals/{record_id}"): "B",
    ("PATCH", "/crm/contacts/{record_id}"): "B",
    ("PATCH", "/crm/organizations/{record_id}"): "B",
    # Log an activity (4)
    ("POST", "/crm/leads/{record_id}/activities"): "B",
    ("POST", "/crm/deals/{record_id}/activities"): "B",
    ("POST", "/crm/contacts/{record_id}/activities"): "B",
    ("POST", "/crm/organizations/{record_id}/activities"): "B",
    # Activities, modules, deal contacts (3)
    ("PATCH", "/crm/activities/{activity_id}"): "B",
    ("PATCH", "/crm/modules/{module}/records/{record_id}"): "B",
    ("POST", "/crm/deals/{deal_id}/contacts"): "B",
    # Convert (1)
    ("POST", "/crm/leads/{lead_id}/convert"): "C",
}

#: Decision 3, the Projects rows that SBX-3 opens (13), with the class of each.
PROJECTS_WRITE: dict[tuple[str, str], str] = {
    ("POST", "/projects/tasks"): "B",
    ("PATCH", "/projects/tasks/{task_id}"): "B",
    ("POST", "/projects/tasks/{task_id}/move"): "B",
    ("PUT", "/projects/tasks/{task_id}/assignees"): "B",
    ("POST", "/projects/tasks/{task_id}/comments"): "B",
    ("POST", "/projects/tasks/{task_id}/complete"): "B",
    ("POST", "/projects/tasks/{task_id}/defer"): "B",
    ("POST", "/projects/tasks/{task_id}/links"): "B",
    ("PUT", "/projects/tasks/{task_id}/recurrence"): "B",
    ("POST", "/projects/nodes/{project_id}/updates"): "B",
    ("POST", "/projects/my/tasks"): "B",
    ("PATCH", "/projects/tasks/{task_id}/personal"): "B",
    ("POST", "/projects/tasks/{task_id}/archive"): "C",
}

#: Rows of class B or C that stay off, each named by decision 3.
WRITE_KEPT_OFF: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/projects/tasks/bulk"),
    ("POST", "/projects/tasks/move"),
    ("POST", "/projects/tasks/{task_id}/unarchive"),
    ("POST", "/projects/tasks/{task_id}/merge"),
    ("POST", "/projects/activities/{activity_id}/revert"),
    ("PATCH", "/projects/comments/{activity_id}"),
    ("POST", "/crm/change-history/{change_id}/undo"),
    ("POST", "/crm/statuses/{kind}"),
    ("PATCH", "/crm/settings"),
    ("POST", "/crm/connection/disconnect"),
})


@pytest.mark.parametrize(("method", "cls", "extra"), [
    ("POST", "A", {}),
    ("POST", "X", {"reason": "a file"}),
    ("GET", "B", {}),
    ("DELETE", "C", {}),
    ("DELETE", "B", {}),
    ("POST", "C", {"op": "send_it", "title": "Send it", "sends": True}),
    ("PATCH", "B", {"sandbox": True}),
])
def test_a_write_row_is_class_b_or_c_and_no_delete(method, cls, extra):
    """Done-when 1. ``Route(sandbox_write=True)`` raises on class A or X, on a
    GET or a DELETE, with ``sends=True``, or with ``sandbox=True``."""
    kwargs = dict(extra)
    tool = "" if cls == "X" else "t"
    if cls != "X" and "op" not in kwargs:
        kwargs["off_door"] = "later"
    with pytest.raises(ValueError, match="sandbox"):
        base.Route(method, "/x/{id}", tool, cls, sandbox_write=True, **kwargs)


def test_a_write_row_is_off_by_default_and_takes_a_class_b_or_c_write():
    for method in ("POST", "PUT", "PATCH"):
        for cls in ("B", "C"):
            row = base.Route(method, "/x/{id}", "t", cls, off_door="later", sandbox_write=True)
            assert row.sandbox_write
    assert base.Route("POST", "/x/{id}", "t", "B", off_door="later").sandbox_write is False


def test_the_write_rows_are_the_29_of_sbx3():
    """Done-when 2. Exactly the 29 rows of decision 3, 16 of CRM and 13 of
    Projects, each with the class of its manifest row, and each served by
    the live gateway. ``POST /projects/tasks/bulk`` is not one of them."""
    from fastapi.routing import iter_route_contexts
    from gateway.main import app

    assert len(CRM_WRITE) == 16 and len(PROJECTS_WRITE) == 13
    expected = {**CRM_WRITE, **PROJECTS_WRITE}
    assert dict(app_manifests.SANDBOX_WRITE_ROUTES) == expected
    assert ("POST", "/projects/tasks/bulk") not in app_manifests.SANDBOX_WRITE_ROUTES
    rows = _rows()
    for pair, cls in expected.items():
        app_id, row = rows[pair]
        assert row.cls == cls and row.sandbox_write and not row.sandbox, pair
        assert row.method != "DELETE" and not row.sends, pair
        scope = "crm:write" if app_id == "crm" else "projects:write"
        assert app_manifests.sandbox_write_scope(pair) == scope, pair
    served: set[tuple[str, str]] = set()
    for context in iter_route_contexts(app.routes):
        for method in getattr(context, "methods", None) or ():
            served.add((method, getattr(context, "path", "")))
    assert not sorted(set(expected) - served)
    assert app_manifests.SANDBOX_BATCH_PAIR in served


def test_each_kept_off_write_row_is_a_real_row_and_stays_off():
    rows = _rows()
    for pair in WRITE_KEPT_OFF:
        _, row = rows[pair]
        assert row.cls in ("B", "C"), pair
        assert not row.sandbox_write, pair
        assert pair not in app_manifests.SANDBOX_WRITE_ROUTES, pair


def test_no_delete_and_no_class_x_row_is_a_write_row():
    for one in app_manifests.SANDBOX_APPS:
        for row in one.routes:
            if row.method == "DELETE" or row.cls in ("A", "X"):
                assert not row.sandbox_write, (row.method, row.path)
    assert {"crm:write", "projects:write"} == app_manifests.SANDBOX_WRITE_SCOPES
    assert app_manifests.sandbox_write_scope(("POST", None)) is None
    assert app_manifests.sandbox_write_scope(("GET", "/crm/leads")) is None
