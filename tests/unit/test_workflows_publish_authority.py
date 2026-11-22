"""Publish authority (spec workflows_app.md Q3, migration 133).

Drafting and Test runs never need ``workflows:publish``; making a workflow
LIVE — publish, rollback, disable, enable — needs it. These tests pin the
route gates declaratively (no Postgres, no HTTP client) plus the resolved
`capabilities` outcome the browser uses to grey out the button.

STU-J1-3 adds ``feature:studio`` to the writes and to a test run of the draft,
where ``WORKFLOW_STUDIO_GATE_ORGS`` holds the org. That gate and the class of
each route are pinned in ``test_workflows_author_gate.py``. This file pins
the publish verb only.
"""

from __future__ import annotations

from typing import Any

from acb_auth import CAPABILITIES, build_access
from gateway.routes.workflows import router as workflows_router
from gateway.routes.workflows.publish import PUBLISH_PERMISSION

#: Every route that changes what runs UNATTENDED.
LIVE_ROUTES = {
    "/workflows/{workflow_id}/publish",
    "/workflows/{workflow_id}/versions/{version}/rollback",
    "/workflows/{workflow_id}/disable",
    # Re-enable arms the triggers again — same authority as publish, and the
    # escape hatch out of the R2 auto-disable policy.
    "/workflows/{workflow_id}/enable",
    # STB-6c: arming one trigger makes it live (owner decision, 2026-11-13).
    "/workflows/{workflow_id}/triggers/{kind}/state",
}

#: Authoring routes that must never need the publish verb — a draft fires no
#: triggers and its writes are still broker-held. (STU-J1-3 gates the writes
#: on `feature:studio` instead, behind its flag: test_workflows_author_gate.py.)
DRAFTING_ROUTES = {
    "/workflows/{workflow_id}/validate",
    "/workflows/{workflow_id}/run",
    "/workflows/{workflow_id}/duplicate",
    "/workflows/{workflow_id}/copilot",
}


def _required_permissions(route: Any) -> set[str]:
    """The permission strings a route actually enforces.

    Read out of ``require_permission``'s closure (it closes over the required
    tuple) rather than matched on a repr, so the assertion cannot pass just
    because SOME dependency happens to be attached. Router-level gates land
    here too — that is how ``feature:workflows`` shows up on every route.
    """
    found: set[str] = set()
    for dep in getattr(route, "dependencies", []) or []:
        call = getattr(dep, "dependency", None)
        for cell in getattr(call, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover - empty cell
                continue
            if isinstance(value, str):
                found.add(value)
            elif isinstance(value, tuple) and all(isinstance(v, str) for v in value):
                found.update(value)
    return found


def _routes_by_path() -> dict[str, Any]:
    return {r.path: r for r in workflows_router.routes if hasattr(r, "path")}


def test_permission_is_in_the_declared_vocabulary() -> None:
    """A typo'd permission string is silently inert — the closed set catches it."""
    assert PUBLISH_PERMISSION == "workflows:publish"
    assert PUBLISH_PERMISSION in CAPABILITIES


def test_live_routes_all_require_publish_authority() -> None:
    routes = _routes_by_path()
    for path in LIVE_ROUTES:
        assert path in routes, f"{path} is no longer registered"
        assert PUBLISH_PERMISSION in _required_permissions(routes[path]), path


def test_publish_route_gate_rejects_a_member_without_the_permission() -> None:
    """The gate's own decision, exercised through the resolved access set."""
    drafter = build_access(["feature:workflows"], roles=["member"])
    publisher = build_access(["feature:workflows", PUBLISH_PERMISSION], roles=["manager"])
    owner = build_access(["*"], roles=["owner"])

    assert drafter.can_use_feature("workflows")  # reads, and builds where J1-3 is off
    assert not drafter.has(PUBLISH_PERMISSION)
    assert publisher.has(PUBLISH_PERMISSION)
    assert owner.has(PUBLISH_PERMISSION)  # "*" covers it — no literal grant


def test_drafting_routes_are_not_gated_on_publish() -> None:
    """Narrowing publish must not lock members out of authoring."""
    routes = _routes_by_path()
    for path in DRAFTING_ROUTES:
        assert path in routes, f"{path} is no longer registered"
        gates = _required_permissions(routes[path])
        assert PUBLISH_PERMISSION not in gates, path
        # …but the feature gate still applies (the router-level dependency).
        assert "feature:workflows" in gates, path


def test_capabilities_outcome_resolves_wildcards_for_the_browser() -> None:
    """/auth/me returns RESOLVED capabilities: an owner holds "*", never the
    literal string, so the UI must not do `permissions.includes(...)`."""
    owner = build_access(["*"], roles=["owner"])
    member = build_access(["feature:workflows"], roles=["member"])

    def resolved(access) -> list[str]:
        return [c for c in CAPABILITIES if "*" not in c and access.has(c)]

    assert PUBLISH_PERMISSION in resolved(owner)
    assert PUBLISH_PERMISSION not in resolved(member)
    # Wildcard capabilities are answered per-target elsewhere, never flatly.
    assert not any("*" in c for c in resolved(owner))


def test_per_user_override_can_hand_publishing_back() -> None:
    """The escape hatch admins actually need: one member, one exception."""
    access = build_access(
        ["feature:workflows"],
        overrides=[(PUBLISH_PERMISSION, "allow")],
        roles=["member"],
    )
    assert access.has(PUBLISH_PERMISSION)


def test_per_user_override_can_take_publishing_away_from_a_manager() -> None:
    access = build_access(
        ["feature:workflows", PUBLISH_PERMISSION],
        overrides=[(PUBLISH_PERMISSION, "deny")],
        roles=["manager"],
    )
    assert not access.has(PUBLISH_PERMISSION)
