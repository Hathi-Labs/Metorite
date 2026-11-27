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
