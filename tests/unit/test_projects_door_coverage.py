"""MCP-G24 — the Projects door table: every route, its class and its door.

Spec: ``project-docs/specs/external_agent_access.md`` §19.16, §19.3, §19.11,
and AD-2, AD-3, AD-9 and AD-18 of §19.12. Decision D208. Metorite task 2520.

``acb_common.app_manifests.projects.PROJECTS_DOOR`` is the door table of
Projects. Database-free. This file walks the REAL
``gateway.routes.projects.router`` and reads the real chat manifest
(``skill_projects.manifest.MANIFEST``) and ``agent_oauth.MCP_ROUTES``.

What it holds:

1. **The router, both ways.** A served route with no row fails, and so does a
   row that names a route the router does not serve.
2. **The chat manifest, row for row.** The table holds the same pairs, with
   the same class and the same chat tool. The table adds the door half only,
   so it is not a second list (D136.1).
3. **AD-2.** Each A, B or C row names a door ``op`` or an ``off_door``
   reason. A class X row has a reason and neither.
4. **AD-3.** A row whose route is in ``MCP_ROUTES`` names no op, and it says
   that a typed tool serves it. A row that says so is in ``MCP_ROUTES``.
5. **§19.11.** No op on a grant, an authority pin, the answer of a person,
   an import, an export, a request decision, the agent delete, or an act
   that reaches another person. No op deletes a row for good.
6. **The first acts of §19.16.** The ops of each act are pinned. Import,
   Goals and teams have none, and the reason is written.
7. **The approval path (§19.5).** Each B or C op asks the member for each
   call. No op is a proposal, and no op sends. A read is a GET.
8. **The builder and the schema (§19.3, AD-9).** ``build_call`` fills each
   path. No op has a body field with the name of a path or query parameter.
   Each args schema builds from ``route.dependant``, with no identity header.
9. **Dark.** The table is not in ``app_manifests.APPS``. The door serves no
   Projects op, and the scopes do not change.

Mutations this file catches (R7):

* a new Projects route with no row -> ``test_every_served_route_has_a_row``;
* a row for a route that is gone -> ``test_no_row_names_a_route_the_router_does_not_serve``;
* a class or a tool that differs from the chat manifest ->
  ``test_each_row_matches_the_chat_manifest``;
* an op on a pair of ``MCP_ROUTES`` -> ``test_ad3_a_typed_pair_names_no_op``;
* an op on ``POST /projects/nodes/{project_id}/grants`` ->
  ``test_no_op_on_a_route_of_19_11``;
* an op on ``DELETE /projects/views/{view_id}`` ->
  ``test_no_op_deletes_a_row_for_good``;
* ``PROJECTS_DOOR`` added to ``APPS`` -> ``test_the_table_ships_dark``.
"""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("skill_projects", reason="skill-projects not installed")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from acb_auth import agent_oauth
from acb_common import app_manifests as am
from acb_common.app_manifests import base
from acb_common.app_manifests import projects as p
from skill_projects import manifest as chat

DOOR = p.PROJECTS_DOOR
ID = "0f8fad5b-d9cb-469f-a165-70867728950e"

#: §19.11 as it applies to Projects: a grant and an authority pin (a
#: grant-class act), the answer of a decision, the decision of an agent
#: request, the agent delete, an import, an export, and the acts that reach
#: another person (an ask, a nudge, a report recipient and a schedule).
NEVER_ON_THE_DOOR = re.compile(
    r"/grants|/audience$|/authorities|/decisions/\{[^}]+\}/answer$|/archive-approval$"
    r"|/agent-delete$|/import/|/export/|/asks$|/nudge$|/recipients|/schedule$")

#: The one DELETE that the door carries. It takes a task out of a cycle, and
#: the task stays. Each other DELETE op would delete a row for good.
DOOR_DELETES = {("DELETE", "/projects/cycles/{cycle_id}/tasks/{task_id}")}

#: The headers of the identity seam. No args schema shows one.
IDENTITY_HEADERS = ("x-user-email", "x-user-role", "x-actor-via", "authorization")


def _router_routes() -> set[tuple[str, str]]:
    from gateway.routes.projects import router

    out: set[tuple[str, str]] = set()
    for route in router.routes:
        for method in getattr(route, "methods", None) or ():
            if method not in ("HEAD", "OPTIONS"):
                out.add((method, getattr(route, "path", "")))
    return out


def _table() -> dict[tuple[str, str], base.Route]:
    return {(r.method, r.path): r for r in DOOR.routes}


def _live_route(method: str, path: str):
    from gateway.routes.projects import router

    for route in router.routes:
        if getattr(route, "path", None) == path and method in (route.methods or ()):
            return route
    raise AssertionError(f"{method} {path} is not a route of {DOOR.router}")


# ── 1. The router, both ways ────────────────────────────────────────────────


def test_every_served_route_has_a_row():
    missing = sorted(_router_routes() - set(_table()))
    assert not missing, (
        "These Projects routes have no row in "
        "packages/acb_common/acb_common/app_manifests/projects.py. Add one row "
        "each, with the class and the tool of the chat manifest, and a door op "
        "or an off_door reason (§19.16):\n  "
        + "\n  ".join(f"{method} {path}" for method, path in missing)
    )


def test_no_row_names_a_route_the_router_does_not_serve():
    stale = sorted(set(_table()) - _router_routes())
    assert not stale, (
        "These rows of the Projects door table name routes that the router "
        "does not serve:\n  " + "\n  ".join(f"{method} {path}" for method, path in stale)
    )


def test_the_table_is_not_vacuous_and_has_one_row_for_each_pair():
    assert len(_router_routes()) >= 200, "non-vacuity: the Projects router is large"
    pairs = [(r.method, r.path) for r in DOOR.routes]
    assert len(pairs) == len(set(pairs))
    assert DOOR.router == "gateway.routes.projects" and DOOR.id == "projects"


# ── 2. The chat manifest, row for row ───────────────────────────────────────


def test_each_row_matches_the_chat_manifest():
    """D136.1: one manifest. The door table repeats the pairs, the class and
    the tool of the chat manifest, and the fence holds each one."""
    rows = {(r.method, r.path): r for r in chat.MANIFEST}
    assert set(rows) == set(_table())
    differ = sorted(
        (pair, (c.cls, c.tool), (d.cls, d.tool))
        for pair, d in _table().items() if (c := rows[pair]).cls != d.cls or c.tool != d.tool
    )
    assert not differ, differ


# ── 3. AD-2 ─────────────────────────────────────────────────────────────────


def test_ad2_each_row_names_an_op_or_an_off_door_reason():
    for row in DOOR.routes:
        if row.cls == "X":
            assert row.reason and not row.op and not row.off_door, (row.method, row.path)
        else:
            assert bool(row.op) != bool(row.off_door), (row.method, row.path)


# ── 4. AD-3 ─────────────────────────────────────────────────────────────────


def test_ad3_a_typed_pair_names_no_op():
    for pair, row in _table().items():
        if pair in agent_oauth.MCP_ROUTES:
            assert not row.op, pair
            assert row.cls == "X" or row.off_door == p.TYPED, pair


def test_ad3_a_row_that_names_the_typed_tools_is_a_typed_pair():
    typed = {pair for pair, row in _table().items() if row.off_door == p.TYPED}
    assert typed, "non-vacuity: the typed tools of §9 serve some routes"
    assert typed <= agent_oauth.MCP_ROUTES, sorted(typed - agent_oauth.MCP_ROUTES)


def test_ad3_no_door_op_is_a_pair_of_mcp_routes():
    pairs = {(d.route.method, d.route.path) for d in am.door_ops(DOOR).values()}
    assert not pairs & agent_oauth.MCP_ROUTES


# ── 5. §19.11 ───────────────────────────────────────────────────────────────


def test_no_op_on_a_route_of_19_11():
    hits = [(r.method, r.path) for r in DOOR.routes if NEVER_ON_THE_DOOR.search(r.path)]
    assert len(hits) >= 15, "non-vacuity: the pattern matches the Projects routes of §19.11"
    for method, path in hits:
        assert not _table()[(method, path)].op, (method, path)


def test_no_op_deletes_a_row_for_good():
    deletes = {(d.route.method, d.route.path)
               for d in am.door_ops(DOOR).values() if d.route.method == "DELETE"}
    assert deletes <= DOOR_DELETES, sorted(deletes - DOOR_DELETES)
    for row in DOOR.routes:
        if row.off_door == p.HARD_DELETE:
            assert row.method == "DELETE", (row.method, row.path)


def test_the_import_stays_off_both_doors():
    """§19.11: an import needs admin:access:manage, an admin's right."""
    rows = [r for r in DOOR.routes if r.path.startswith("/projects/import/")]
    assert len(rows) == 6
    assert all(r.cls == "X" and "admin:access:manage" in r.reason for r in rows)


# ── 6. The first acts of §19.16 ─────────────────────────────────────────────


def test_the_first_acts_are_pinned():
    named = {op for ops in p.FIRST_ACTS.values() for op in ops}
    assert set(p.FIRST_ACTS) == {
        "cycles", "saved views", "import", "status editing", "template apply", "goals",
        "teams"}
    assert set(am.door_ops(DOOR)) == named
    assert p.FIRST_ACTS["import"] == p.FIRST_ACTS["goals"] == p.FIRST_ACTS["teams"] == ()
    ops = am.door_ops(DOOR)
    for op in p.FIRST_ACTS["cycles"]:
        assert "/cycles" in ops[op].route.path, op
    for op in p.FIRST_ACTS["saved views"]:
        assert "/views" in ops[op].route.path, op
    for op in p.FIRST_ACTS["status editing"]:
        assert "/status" in ops[op].route.path, op
    assert ops["apply_template"].route.path == "/projects/templates/{template_id}/apply"


def test_goals_and_teams_have_no_route_yet():
    """§19.16 names them, and the router serves no route of either. When one
    comes, its row needs a door op, and this test fails as a reminder."""
    hits = sorted(pair for pair in _router_routes()
                  if re.search(r"/goals?\b|/teams?\b", pair[1]))
    assert not hits, f"give these routes a door op in FIRST_ACTS: {hits}"


# ── 7. The approval path (§19.5) ────────────────────────────────────────────


def test_each_change_asks_the_member_for_each_call():
    for door in am.door_ops(DOOR).values():
        assert not door.route.proposal and not door.route.sends, door.name
        if door.route.cls == "A":
            assert door.kind == "read" and door.route.method == "GET", door.name
            assert door.asks == "never" and door.scope == "projects:read", door.name
        else:
            assert door.kind == "write" and door.asks == "each_call", door.name
            assert door.scope == "projects:write", door.name


def test_ad18_each_change_fails_closed_today():
    """Before MCP-P1b-2 no approval can exist, so the shared rule of
    availability refuses each change (AD-18)."""
    for door in am.door_ops(DOOR).values():
        available, code = agent_oauth.door_available(door)
        if door.kind == "write":
            assert (available, code) == (False, agent_oauth.APPROVAL_REQUIRED_CODE), door.name


def test_the_scopes_are_the_two_fixed_projects_scopes():
    assert DOOR.scopes == (agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE)


# ── 8. The builder and the schema (§19.3, AD-9) ─────────────────────────────


def test_build_call_fills_each_path():
    for door in am.door_ops(DOOR).values():
        args = {name: ID for name in base.path_params(door.route.path)}
        call = am.build_call(DOOR, door.name, args)
        assert "{" not in call.path and call.template == door.route.path, door.name
        assert call.method == door.route.method, door.name
        assert call.body == (b"" if door.route.method == "GET" else b"{}"), door.name


def test_build_call_refuses_a_path_value_of_the_wrong_shape():
    with pytest.raises(am.BadCall) as caught:
        am.build_call(DOOR, "get_cycle", {"cycle_id": "../../admin"})
    assert (caught.value.name, caught.value.why) == ("cycle_id", "shape")


def test_expected_version_becomes_if_match_for_a_cycle_edit():
    call = am.build_call(DOOR, "update_cycle",
                         {"cycle_id": ID, "expected_version": "3", "name": "Week 46"})
    assert call.if_match == "3" and json.loads(call.body) == {"name": "Week 46"}


def test_no_op_has_a_body_field_with_the_name_of_a_path_or_query_parameter():
    from pydantic import BaseModel

    for door in am.door_ops(DOOR).values():
        route = _live_route(door.route.method, door.route.path)
        names = {q.alias for q in route.dependant.path_params} | set(door.route.query)
        for param in route.dependant.body_params:
            kind = param.field_info.annotation
            fields = (set(kind.model_fields) if isinstance(kind, type)
                      and issubclass(kind, BaseModel) else {param.alias})
            assert not fields & names, (door.name, fields & names)


def test_ad9_each_args_schema_builds_from_the_live_route():
    """``describe_app`` builds each schema from ``route.dependant``. This
    builds it here, before the table joins the door."""
    from gateway.routes import agent_apps
    from pydantic import ConfigDict, create_model

    for door in am.door_ops(DOOR).values():
        route = agent_apps._route_of(DOOR, door)
        assert route is not None, door.name
        fields = agent_apps._fields_of(route, door)
        model = create_model(f"projects_{door.name}_args",
                             __config__=ConfigDict(extra="forbid"), **fields)
        schema = model.model_json_schema()
        assert agent_apps._size(schema) <= agent_apps.MAX_SCHEMA_BYTES, door.name
        assert set(base.path_params(door.route.path)) <= set(schema["properties"]), door.name
        text = json.dumps(schema).lower()
        for header in IDENTITY_HEADERS:
            assert header not in text, (door.name, header)
    assert "expected_version" in agent_apps._fields_of(
        agent_apps._route_of(DOOR, am.door_ops(DOOR)["update_cycle"]),
        am.door_ops(DOOR)["update_cycle"])


# ── 9. Dark ─────────────────────────────────────────────────────────────────


def test_the_table_ships_dark():
    """§19.16: a later slice registers the table, after MCP-P1b-2. Until then
    no reader of the registry serves a Projects op."""
    assert DOOR not in am.APPS
    assert am.door_app("projects") is None
    assert am.app("projects") is am.PROJECTS and am.PROJECTS.door == "tools"
    assert not [pair for pair in am.door_routes() if pair[1].startswith("/projects")]
    assert am.app_scopes() == agent_oauth.APP_SCOPES
    assert "projects:read" not in agent_oauth.APP_SCOPES
