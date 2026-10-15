"""WS-55 EA-3 — the ``MCP_ROUTES`` allowlist fence (EA3-6, R7).

Spec: ``project-docs/specs/external_agent_access.md`` §9.5 and §15.2 D15.

``acb_auth.agent_oauth.MCP_ROUTES`` is the ONE list of gateway routes an
outside agent may reach. Branch 0 of ``get_current_user`` refuses every other
pair. This file holds the list to the gateway and to the Projects chat
manifest, so a change to any of the three cannot drift silently.

The four rules of §9.5. EA-3a built rules 1 and 2, the gateway half. EA-3b-1
adds rules 3 and 4, which read the edge's tool records
(``agent_edge/tools.py``). EA-6a widens each rule for a listed write pair.
EA-6d adds the task create pair, one non-idempotent write (``create_task``)
and one edge input (its ``idempotency_key``). EA-6c adds the comment pair and
the sixth edge input, the ``idempotency_key`` of ``add_comment``.

1. Each pair in ``MCP_ROUTES`` is a route the gateway serves. Its method is
   ``GET``, or ``PATCH`` or ``POST`` for a pair of ``MCP_WRITE_ROUTES``, which
   is a subset of ``MCP_ROUTES`` and holds no GET (EA-6a).
2. Each pair is class A in ``skill_projects.manifest.MANIFEST``, or it is a
   named exception. EA-3 has one exception, ``GET /auth/me``. A pair of
   ``MCP_WRITE_ROUTES`` may be class B (EA-6a). No class C or X route appears.
3. Each tool of ``agent_edge.tools.TOOLS`` declares a ``required_scope`` (a
   resource scope, or ``None`` for any live token), the four annotations, an
   ``inputSchema`` and an ``outputSchema`` (EA-3b-1). A read has the hints of
   a read. A write has ``readOnlyHint: false`` and needs ``projects:write``.
4. Each tool input maps to a parameter of its route: a path, query or
   header parameter, or a field of a body model in ``dependant.body_params``
   (EA-6a). The parameters come from ``route.dependant``, as
   ``test_projects_field_parity.py`` reads them. Each route a tool reads or
   writes is in ``MCP_ROUTES`` (EA-3b-1). An input that the edge applies
   itself is a named exception, listed in the tool record's ``edge_inputs``
   and in :data:`NAMED_EDGE_INPUTS` (EA-3b-2).

Database-free. The gateway app imports, and the manifest is a tuple.
"""
from __future__ import annotations

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_auth.agent_oauth import MCP_ROUTES, MCP_WRITE_ROUTES
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


#: The methods a write pair may have (EA-6a, §9.5 rule 1).
WRITE_METHODS = frozenset({"PATCH", "POST"})


def test_the_allowlist_is_not_empty_and_is_get_only_outside_the_write_pairs():
    """Rule 1, the method half. Every pair outside ``MCP_WRITE_ROUTES`` is a
    GET. Every write pair is a PATCH or a POST, and sits in ``MCP_ROUTES``."""
    assert MCP_ROUTES, "an empty allowlist would make every rule below vacuous"
    assert MCP_WRITE_ROUTES <= MCP_ROUTES
    assert {method for method, _ in MCP_ROUTES - MCP_WRITE_ROUTES} == {"GET"}
    assert {method for method, _ in MCP_WRITE_ROUTES} <= WRITE_METHODS


def test_the_write_pairs_are_those_of_ea_6a_ea_6d_and_ea_12a():
    """A new write pair needs a spec amendment (§9.2, EA3-31). EA-6a listed
    the PATCH and the references POST. EA-6d adds the task create. EA-12a
    adds the three POST pairs of the agent runs (§12.1, EA12-9). EA-6c adds
    the comment POST (EA3-35)."""
    expected = {
        ("PATCH", "/projects/tasks/{task_id}"),
        ("POST", "/projects/tasks/{task_id}/refs"),
        ("POST", "/projects/tasks"),
        ("POST", "/projects/tasks/{task_id}/comments"),
        ("POST", "/projects/tasks/{task_id}/agent-runs"),
        ("POST", "/projects/agent-runs/{run_id}/activities"),
        ("POST", "/projects/agent-runs/{run_id}/finish"),
    }
    assert expected == set(MCP_WRITE_ROUTES)


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
        allowed = {"A", "B"} if pair in MCP_WRITE_ROUTES else {"A"}
        if cls not in allowed:
            wrong.append(f"{pair[0]} {pair[1]} (class {cls or 'none'})")
    assert not wrong, (
        "MCP_ROUTES may hold only class A reads of the Projects manifest, a "
        "class B write of MCP_WRITE_ROUTES, or a named exception:\n  "
        + "\n  ".join(wrong)
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

#: The four annotations of a write tool of EA-6a (§9.2).
WRITE_HINTS = {**READ_HINTS, "readOnlyHint": False}

#: The write tools whose ``idempotentHint`` is false, each with its reason. A
#: new entry needs a spec amendment (§9.2).
NON_IDEMPOTENT_WRITES: dict[str, str] = {
    "create_task": (
        "EA-6d. The hint is one value for every call, and a call with no"
        " idempotency_key makes a new task each time (spec §9.2)."
    ),
}


def _tools() -> list:
    from agent_edge.tools import TOOLS

    assert TOOLS, "an empty tool list would make rules 3 and 4 vacuous"
    return list(TOOLS.values())


def _body_fields(dependant) -> set[str]:
    """The names a body may carry: each field of each body MODEL, or the
    alias of a body parameter that is not a model (EA-6a, rule 4)."""
    from pydantic import BaseModel

    names: set[str] = set()
    for field in dependant.body_params:
        kind = getattr(field, "type_", None) or field.field_info.annotation
        if isinstance(kind, type) and issubclass(kind, BaseModel):
            names |= {f.alias or n for n, f in kind.model_fields.items()}
        else:
            names.add(field.alias)
    return names


def _route_parameters() -> dict[tuple[str, str], set[str]]:
    """``{(method, template): names}`` of each route, from FastAPI's
    ``dependant``: the path, query and header parameters, its dependencies
    too, and the fields of its body models (EA-6a)."""
    from gateway import main

    def nested(dependant) -> set[str]:
        names = {q.alias for q in dependant.query_params}
        names |= {h.alias for h in dependant.header_params}
        for sub in dependant.dependencies:
            names |= nested(sub)
        return names

    out: dict[tuple[str, str], set[str]] = {}
    for route in served_routes(main.app.routes):
        dependant = getattr(route, "dependant", None)
        if dependant is None:  # a route with no FastAPI dependant, such as a mount
            continue
        names = ({p.alias for p in dependant.path_params} | nested(dependant)
                 | _body_fields(dependant))
        for method in getattr(route, "methods", None) or ():
            out[(method, route.path)] = names
    return out


def test_rule_3_each_tool_declares_its_scope_hints_and_schemas():
    from acb_auth.agent_oauth import PROJECTS_WRITE, RESOURCE_SCOPES

    wrong: list[str] = []
    for tool in _tools():
        if not hasattr(tool, "required_scope"):
            wrong.append(f"{tool.name}: no required_scope")
        elif tool.required_scope is not None and tool.required_scope not in RESOURCE_SCOPES:
            wrong.append(f"{tool.name}: required_scope {tool.required_scope!r}")
        write = tool.method != "GET"
        hints = WRITE_HINTS if write else READ_HINTS
        if write and tool.name in NON_IDEMPOTENT_WRITES:
            hints = {**WRITE_HINTS, "idempotentHint": False}
        if dict(tool.annotations) != hints:
            wrong.append(f"{tool.name}: annotations {dict(tool.annotations)}")
        if write and tool.required_scope != PROJECTS_WRITE:
            wrong.append(f"{tool.name}: a write needs {PROJECTS_WRITE}")
        for name in ("input_schema", "output_schema"):
            schema = getattr(tool, name, None)
            if not isinstance(schema, dict) or schema.get("type") != "object":
                wrong.append(f"{tool.name}: {name} is not an object schema")
        listed = tool.describe()
        for key in ("inputSchema", "outputSchema", "annotations", "title"):
            if not listed.get(key):
                wrong.append(f"{tool.name}: tools/list shows no {key}")
    assert not wrong, "\n  ".join(["A tool record breaks §9.5 rule 3:", *wrong])


#: The inputs that the edge applies itself, because the route takes no such
#: parameter. Each one is a named exception of rule 4, and a new one needs a
#: spec amendment (§9.5 rule 4). ``GET /projects/tree`` takes no parameter.
NAMED_EDGE_INPUTS: frozenset[tuple[str, str]] = frozenset({
    ("list_projects", "parent_id"),
    ("list_projects", "text"),
    ("list_projects", "limit"),
    ("list_projects", "cursor"),
    # EA-6d. The edge keeps the key in its own slot (`agent_edge.idempotency`),
    # and the route takes no such field (spec §9.2 and §9.5 rule 4).
    ("create_task", "idempotency_key"),
    # EA-6c (EA3-35). The same slot, keyed by the tool name too. The comment
    # route takes no such field either.
    ("add_comment", "idempotency_key"),
})


def test_rule_4_each_input_maps_to_a_parameter_of_its_route():
    params = _route_parameters()
    wrong: list[str] = []
    for tool in _tools():
        inputs = set(tool.input_schema.get("properties", {}))
        edge = set(tool.edge_inputs)
        if set(tool.params) & edge:
            wrong.append(f"{tool.name}: {sorted(set(tool.params) & edge)} is both a "
                         f"route parameter and an edge input")
        if set(tool.params) | edge != inputs:
            wrong.append(f"{tool.name}: inputs {sorted(inputs)} but params "
                         f"{sorted(tool.params)} and edge inputs {sorted(edge)}")
        served = params.get((tool.method, tool.route))
        if served is None:
            wrong.append(f"{tool.name}: {tool.method} {tool.route} is not a gateway route")
            continue
        # EA-12b: an input may fill a parameter of a write that the tool sends
        # before its own (``prior_writes``). Only ``ask_on_task`` has one.
        served = set(served)
        for prior in tool.prior_writes:
            more = params.get((tool.method, prior))
            if more is None:
                wrong.append(f"{tool.name}: {tool.method} {prior} is not a gateway route")
                continue
            served |= more
        for name, parameter in sorted(tool.params.items()):
            if parameter not in served:
                wrong.append(f"{tool.name}: {name} maps to {parameter!r}, which "
                             f"{tool.method} {tool.route} does not take")
    assert not wrong, "\n  ".join(["A tool input breaks §9.5 rule 4:", *wrong])


def test_rule_4_reads_the_body_fields_of_a_write():
    """EA-6a. Rule 4 sees the body model of each write route, so a write
    input that names no field of the body fails it."""
    params = _route_parameters()
    assert "status_id" in params[("PATCH", "/projects/tasks/{task_id}")]
    assert "If-Match" in params[("PATCH", "/projects/tasks/{task_id}")]
    assert {"url", "title"} <= params[("POST", "/projects/tasks/{task_id}/refs")]
    assert "no_such_field" not in params[("PATCH", "/projects/tasks/{task_id}")]
    # EA-6d: the create body and the fields of `update_task`.
    assert {"project_id", "title", "parent_task_id", "status_id", "tags",
            "due_at"} <= params[("POST", "/projects/tasks")]
    assert {"title", "description", "tags", "due_at", "custom_fields"} <= (
        params[("PATCH", "/projects/tasks/{task_id}")])
    assert "idempotency_key" not in params[("POST", "/projects/tasks")]


def test_rule_4_the_edge_inputs_are_exactly_the_named_exceptions():
    """An edge input is a hole in rule 4, so the spec names each one."""
    declared = {(tool.name, name) for tool in _tools() for name in tool.edge_inputs}
    assert declared == NAMED_EDGE_INPUTS, (
        "The edge inputs of the tool records differ from the named exceptions of"
        f" spec §9.5 rule 4. Extra: {sorted(declared - NAMED_EDGE_INPUTS)}."
        f" Missing: {sorted(NAMED_EDGE_INPUTS - declared)}.")


def test_rule_4_each_route_a_tool_reads_is_in_the_allowlist():
    for tool in _tools():
        pairs = [(tool.method, tool.route), *(("GET", r) for r in tool.routes)]
        missing = sorted(p for p in pairs if p not in MCP_ROUTES)
        assert not missing, f"{tool.name} reaches pairs outside MCP_ROUTES: {missing}"
        if tool.method != "GET":
            assert (tool.method, tool.route) in MCP_WRITE_ROUTES, tool.name
        for prior in tool.prior_writes:
            assert (tool.method, prior) in MCP_WRITE_ROUTES, (tool.name, prior)
        if tool.method == "GET":
            assert not tool.prior_writes, f"{tool.name} is a read and sends a write"


def test_rule_4_only_ask_on_task_sends_a_prior_write():
    """EA-12b. A prior write widens rule 4 for its tool, so the spec names
    each one (§12.1): ``ask_on_task`` starts the run, then writes the
    question."""
    declared = {(tool.name, prior) for tool in _tools() for prior in tool.prior_writes}
    assert declared == {("ask_on_task", "/projects/tasks/{task_id}/agent-runs")}
