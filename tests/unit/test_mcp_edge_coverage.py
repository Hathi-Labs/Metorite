"""WS-55 EA-3 — the ``MCP_ROUTES`` allowlist fence (EA3-6, R7).

Spec: ``project-docs/specs/external_agent_access.md`` §9.5 and §15.2 D15.

``acb_auth.agent_oauth.MCP_ROUTES`` is the ONE list of gateway routes an
outside agent may reach. Branch 0 of ``get_current_user`` refuses every other
pair. This file holds the list to the gateway and to the Projects chat
manifest, so a change to any of the three cannot drift silently.

The four rules of §9.5. EA-3a built rules 1 and 2, the gateway half. EA-3b-1
adds rules 3 and 4, which read the edge's tool records
(``agent_edge/tools.py``).

1. Each pair in ``MCP_ROUTES`` is a route the gateway serves, and its method
   is ``GET``.
2. Each pair is class A in ``skill_projects.manifest.MANIFEST``, or it is a
   named exception. EA-3 has one exception, ``GET /auth/me``. No class B, C
   or X route appears.
3. Each tool of ``agent_edge.tools.TOOLS`` declares a ``required_scope`` (a
   resource scope, or ``None`` for any live token), the four annotations of
   a read, an ``inputSchema`` and an ``outputSchema`` (EA-3b-1).
4. Each tool input maps to a parameter of its route. The parameters come
   from ``route.dependant``, as ``test_projects_field_parity.py`` reads them.
   Each route a tool reads is in ``MCP_ROUTES`` (EA-3b-1).

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


# ── Rules 3 and 4: the edge's tool records (EA-3b-1) ────────────────────────

#: The four annotations of every EA-3 tool, and their values (§9.5).
READ_HINTS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _tools() -> list:
    from agent_edge.tools import TOOLS

    assert TOOLS, "an empty tool list would make rules 3 and 4 vacuous"
    return list(TOOLS.values())


def _route_parameters() -> dict[str, set[str]]:
    """``{template: names}`` of each GET route, from FastAPI's ``dependant``:
    the path parameters and the query parameters, its dependencies too."""
    from gateway import main

    def query(dependant) -> set[str]:
        names = {q.alias for q in dependant.query_params}
        for sub in dependant.dependencies:
            names |= query(sub)
        return names

    out: dict[str, set[str]] = {}
    for route in served_routes(main.app.routes):
        if "GET" in (getattr(route, "methods", None) or ()):
            dependant = route.dependant
            out[route.path] = {p.alias for p in dependant.path_params} | query(dependant)
    return out


def test_rule_3_each_tool_declares_its_scope_hints_and_schemas():
    from acb_auth.agent_oauth import RESOURCE_SCOPES

    wrong: list[str] = []
    for tool in _tools():
        if not hasattr(tool, "required_scope"):
            wrong.append(f"{tool.name}: no required_scope")
        elif tool.required_scope is not None and tool.required_scope not in RESOURCE_SCOPES:
            wrong.append(f"{tool.name}: required_scope {tool.required_scope!r}")
        if dict(tool.annotations) != READ_HINTS:
            wrong.append(f"{tool.name}: annotations {dict(tool.annotations)}")
        for name in ("input_schema", "output_schema"):
            schema = getattr(tool, name, None)
            if not isinstance(schema, dict) or schema.get("type") != "object":
                wrong.append(f"{tool.name}: {name} is not an object schema")
        listed = tool.describe()
        for key in ("inputSchema", "outputSchema", "annotations", "title"):
            if not listed.get(key):
                wrong.append(f"{tool.name}: tools/list shows no {key}")
    assert not wrong, "\n  ".join(["A tool record breaks §9.5 rule 3:", *wrong])


def test_rule_4_each_input_maps_to_a_parameter_of_its_route():
    params = _route_parameters()
    wrong: list[str] = []
    for tool in _tools():
        inputs = set(tool.input_schema.get("properties", {}))
        if set(tool.params) != inputs:
            wrong.append(f"{tool.name}: inputs {sorted(inputs)} but params "
                         f"{sorted(tool.params)}")
        served = params.get(tool.route)
        if served is None:
            wrong.append(f"{tool.name}: GET {tool.route} is not a gateway route")
            continue
        for name, parameter in sorted(tool.params.items()):
            if parameter not in served:
                wrong.append(f"{tool.name}: {name} maps to {parameter!r}, which "
                             f"GET {tool.route} does not take")
    assert not wrong, "\n  ".join(["A tool input breaks §9.5 rule 4:", *wrong])


def test_rule_4_each_route_a_tool_reads_is_in_the_allowlist():
    for tool in _tools():
        reads = (tool.route, *tool.routes)
        missing = sorted(r for r in reads if ("GET", r) not in MCP_ROUTES)
        assert not missing, f"{tool.name} reads routes outside MCP_ROUTES: {missing}"
