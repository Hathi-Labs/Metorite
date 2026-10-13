"""WS-55 EA-3 — the ``MCP_ROUTES`` allowlist fence (EA3-6, R7).

Spec: ``project-docs/specs/external_agent_access.md`` §9.5 and §15.2 D15.

``acb_auth.agent_oauth.MCP_ROUTES`` is the ONE list of gateway routes an
outside agent may reach. Branch 0 of ``get_current_user`` refuses every other
pair. This file holds the list to the gateway and to the Projects chat
manifest, so a change to any of the three cannot drift silently.

The four rules of §9.5. EA-3a builds rules 1 and 2, the gateway half. EA-3b
adds rules 3 and 4, which read the edge's tool records
(``agent_edge/tools.py``), when that module exists.

1. Each pair in ``MCP_ROUTES`` is a route the gateway serves, and its method
   is ``GET``.
2. Each pair is class A in ``skill_projects.manifest.MANIFEST``, or it is a
   named exception. EA-3 has one exception, ``GET /auth/me``. No class B, C
   or X route appears.

Database-free. The gateway app imports, and the manifest is a tuple.
"""
from __future__ import annotations

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_auth.agent_oauth import MCP_ROUTES
from skill_projects import manifest as m

from tests.unit._routes import served_routes

#: The routes in ``MCP_ROUTES`` that the Projects manifest does not carry,
#: each with its reason. A new entry needs a spec amendment (§9.5).
NAMED_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("GET", "/auth/me"): (
        "`whoami` reads the member's own identity. It is not a Projects route,"
        " so the Projects manifest does not carry it (spec §9.1)."
    ),
}


def _gateway_routes() -> set[tuple[str, str]]:
    from gateway import main

    out: set[tuple[str, str]] = set()
    for route in served_routes(main.app.routes):
        for method in getattr(route, "methods", None) or ():
            out.add((method, getattr(route, "path", "")))
    return out


def test_the_allowlist_is_not_empty_and_is_get_only():
    assert MCP_ROUTES, "an empty allowlist would make every rule below vacuous"
    assert {method for method, _ in MCP_ROUTES} == {"GET"}


def test_rule_1_each_pair_is_a_route_the_gateway_serves():
    served = _gateway_routes()
    missing = sorted(pair for pair in MCP_ROUTES if pair not in served)
    assert not missing, (
        "These MCP_ROUTES pairs name no route the gateway serves. A stale pair "
        "is a tool that will fail. Fix agent_oauth.MCP_ROUTES:\n  "
        + "\n  ".join(f"{method} {path}" for method, path in missing)
    )


def test_rule_2_each_pair_is_class_a_or_a_named_exception():
    classes = {(r.method, r.path): r.cls for r in m.MANIFEST}
    wrong: list[str] = []
    for pair in sorted(MCP_ROUTES):
        if pair in NAMED_EXCEPTIONS:
            assert pair not in classes or classes[pair] == "A", pair
            continue
        cls = classes.get(pair)
        if cls != "A":
            wrong.append(f"{pair[0]} {pair[1]} (class {cls or 'none'})")
    assert not wrong, (
        "MCP_ROUTES may hold only class A reads of the Projects manifest, or a "
        "named exception. EA-3 is read-only:\n  " + "\n  ".join(wrong)
    )


def test_every_named_exception_is_still_in_the_allowlist():
    stale = sorted(set(NAMED_EXCEPTIONS) - set(MCP_ROUTES))
    assert not stale, stale
