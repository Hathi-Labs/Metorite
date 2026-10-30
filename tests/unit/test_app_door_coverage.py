"""MCP-P1a — the app manifests of the door: AD-2, AD-3, the builder and the schema.

Spec: ``project-docs/specs/external_agent_access.md`` §19.3, §19.4, §19.6,
§19.11 and AD-1 to AD-11 of §19.12. Decision D136: one manifest, two doors.

Database-free. It reads the real manifests of ``acb_common.app_manifests``
and the real routers of the gateway.

What it holds:

* **AD-2.** Each A, B or C row names a door ``op`` or an ``off_door``
  reason, and an :class:`App` with a row of neither fails when it loads.
* **AD-3.** ``door_routes()`` comes from the manifests. No door pair is a
  pair of ``MCP_ROUTES``, and each door pair is a route that the app's own
  router serves.
* **The P1a door.** The door serves reads and proposals only. No class X row
  and no route of §19.11 has an op. The Knowledge search calls a model, so
  it is off the door (AD-11). A proposal is class B, and its route publishes
  and sends nothing (AD-8).
* **The builder (§19.3).** ``build_call`` fills the path, refuses a path
  value of the wrong shape, puts each other key of a read in the query and
  each other key of a write in a compact JSON body with sorted keys, and
  makes ``expected_version`` the ``If-Match``. No op has a body field with
  the name of a path or query parameter.
* **The schema (AD-9).** ``describe_app`` builds each args schema from
  ``route.dependant``: the path, query and body fields of the live route, and
  no identity header.
* **The scopes (§19.6).** ``knowledge:read`` and ``knowledge:write`` come
  from the manifest, after the three fixed scopes. They are advertised only
  while the door flag names Knowledge.

Mutations this file catches (R7):

* a row of class A with neither op nor off_door ->
  ``test_ad2_a_row_with_neither_fails_at_import``;
* an op on ``POST /knowledge/pages/{page_id}/publish`` ->
  ``test_no_door_op_on_a_route_of_19_11`` and
  ``test_p1a_opens_no_write_but_a_proposal``;
* an op on the search -> ``test_ad11_the_search_is_off_the_door``;
* a door pair added to ``MCP_ROUTES`` -> ``test_ad3_no_pair_is_in_both``;
* ``sort_keys`` dropped from the builder ->
  ``test_the_body_bytes_are_stable``.
"""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("gateway.routes.knowledge", reason="gateway not installed")

from acb_auth import agent_oauth
from acb_common import app_manifests as am
from acb_common.app_manifests import base

PAGE = "0f8fad5b-d9cb-469f-a165-70867728950e"

#: The route patterns of §19.11 that no manifest may give an op, as they
#: apply to Knowledge: a grant, a person's decision, a publish, an archive,
#: a restore, a verify, a favourite, a provider consent or callback.
NEVER_ON_THE_DOOR = re.compile(
    r"/grants$|/proposals/\{[^}]+\}/(accept|reject)$|/publish$|/archive$|/restore$"
    r"|/verify$|/favorite|/connect$|/callback$|/gaps/\{[^}]+\}/")


def _router_routes(app: am.App) -> set[tuple[str, str]]:
    import importlib

    router = importlib.import_module(app.router).router
    out: set[tuple[str, str]] = set()
    for route in router.routes:
        for method in getattr(route, "methods", None) or ():
            if method not in ("HEAD", "OPTIONS"):
                out.add((method, getattr(route, "path", "")))
    return out


def _live_route(app: am.App, method: str, path: str):
    import importlib

    router = importlib.import_module(app.router).router
    for route in router.routes:
        if getattr(route, "path", None) == path and method in (route.methods or ()):
            return route
    raise AssertionError(f"{method} {path} is not a route of {app.router}")


# ── The registry ────────────────────────────────────────────────────────────


def test_the_registry_holds_knowledge_and_the_projects_header():
    assert [a.id for a in am.APPS] == ["knowledge"]
    assert [a.id for a in am.TOOL_APPS] == ["projects"]
    assert am.PROJECTS.door == "tools" and am.PROJECTS.routes == ()
    assert am.app("knowledge") is am.KNOWLEDGE
    assert am.door_app("projects") is None
    assert am.app("nothing") is None and am.app(None) is None


def test_the_chat_reads_the_same_rows():
    """AD-1: the re-export of acb_skills names the same tuple, so Mira's
    client and the door read ONE table."""
    from acb_skills import knowledge_manifest as m

    assert m.MANIFEST is am.KNOWLEDGE.routes
    assert m.Route is base.Route


def test_each_app_names_a_real_feature_and_router():
    from acb_auth.permissions import FEATURES

    for app in am.every_app():
        assert app.feature in FEATURES, app.id
        assert app.pane.startswith("/"), app.id
        if app.door == "ops":
            assert _router_routes(app), app.id


def test_the_manifest_module_imports_the_standard_library_only():
    """AD-1: pure data. The edge, acb_auth and the gateway import it."""
    import ast
    from pathlib import Path

    folder = Path(base.__file__).parent
    for path in sorted(folder.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] in {"__future__", "acb_common", "collections",
                                                     "dataclasses", "typing", "functools",
                                                     "json", "re"}, (path.name, node.module)
                if node.module.startswith("acb_common"):
                    assert node.module.startswith("acb_common.app_manifests"), node.module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in {"functools", "json", "re"}, (path.name, alias.name)


# ── AD-2 ────────────────────────────────────────────────────────────────────


def test_ad2_each_a_b_or_c_row_names_an_op_or_an_off_door_reason():
    for app in am.APPS:
        for row in app.routes:
            if row.cls == "X":
                assert not row.op and not row.off_door, (row.method, row.path)
            else:
                assert bool(row.op) != bool(row.off_door), (row.method, row.path)


@pytest.mark.parametrize("cls", ["A", "B", "C"])
def test_ad2_a_row_with_neither_fails_at_import(cls):
    row = base.Route("GET" if cls == "A" else "POST", "/x/y", "", cls, planned="later")
    with pytest.raises(ValueError, match="AD-2"):
        base.App(id="x", name="X", reach="r", pane="/x", feature="x", router="r",
                 routes=(row,))


@pytest.mark.parametrize("kwargs", [
    {"cls": "X", "reason": "why", "op": "an_op", "title": "t"},
    {"cls": "X", "reason": "why", "off_door": "why"},
    {"cls": "A", "tool": "t", "op": "Bad-Name", "title": "t"},
    {"cls": "A", "tool": "t", "op": "an_op"},
    {"cls": "A", "tool": "t", "op": "an_op", "title": "t", "off_door": "why"},
    {"cls": "A", "tool": "t", "op": "an_op", "title": "t", "proposal": True},
    {"cls": "B", "tool": "t", "op": "an_op", "title": "t", "sends": True},
    {"cls": "A", "tool": "t", "off_door": "why", "plain": ("id",)},
    {"cls": "A", "tool": "t", "op": "an_op", "title": "t",
     "path_shapes": (("nope", "[0-9]+"),)},
])
def test_a_bad_door_row_fails_at_import(kwargs):
    cls = kwargs.pop("cls")
    tool = kwargs.pop("tool", "")
    method = "GET" if cls in ("A", "X") else "POST"
    with pytest.raises(ValueError):
        base.Route(method, "/x/{id}", tool, cls, **kwargs)


def test_a_door_read_is_a_get():
    with pytest.raises(ValueError, match="a door read is a GET"):
        base.Route("POST", "/x", "t", "A", op="an_op", title="t")


def test_an_op_name_is_used_once_in_an_app():
    one = base.Route("GET", "/x", "t", "A", op="same", title="t")
    two = base.Route("GET", "/y", "t", "A", op="same", title="t")
    with pytest.raises(ValueError, match="twice"):
        base.App(id="x", name="X", reach="r", pane="/x", feature="x", router="r",
                 routes=(one, two))


# ── AD-3 and the P1a door ───────────────────────────────────────────────────


def test_ad3_the_door_pairs_come_from_the_manifests():
    pairs = set(am.door_routes())
    expected = {(r.method, r.path) for a in am.APPS for r in a.routes if r.op}
    assert pairs == expected and len(pairs) >= 10
    for pair, door in am.door_routes().items():
        assert agent_oauth.door_route(pair) is door


def test_ad3_no_pair_is_in_both():
    assert not set(am.door_routes()) & agent_oauth.MCP_ROUTES


def test_each_door_pair_is_a_route_that_the_app_serves():
    for app in am.APPS:
        served = _router_routes(app)
        for door in am.door_ops(app).values():
            assert (door.route.method, door.route.path) in served, door.name


def test_no_door_op_on_a_route_of_19_11():
    for app in am.APPS:
        for row in app.routes:
            if NEVER_ON_THE_DOOR.search(row.path):
                assert not row.op, (row.method, row.path)


def test_p1a_opens_no_write_but_a_proposal():
    """The dispatchable scope of MCP-P1a: reads and proposals only. A B or C
    row with an op is a proposal. Every other change waits for MCP-P1b."""
    for app in am.APPS:
        for door in am.door_ops(app).values():
            assert door.kind in ("read", "proposal"), door.name
            assert door.asks == "never", door.name
            if door.kind == "proposal":
                assert door.route.cls == "B" and not door.route.sends, door.name


def test_the_knowledge_door_ops_are_pinned():
    assert sorted(am.door_ops(am.KNOWLEDGE)) == [
        "get_page", "get_page_version", "get_source_tree", "list_backlinks", "list_gaps",
        "list_maintenance", "list_pages", "list_proposals", "list_recent", "list_sources",
        "list_spaces", "propose"]
    propose = am.door_ops(am.KNOWLEDGE)["propose"]
    assert (propose.route.method, propose.route.path) == ("POST", "/knowledge/proposals")
    assert propose.kind == "proposal" and am.KNOWLEDGE.finish_in


def test_ad11_the_search_is_off_the_door():
    """The search embeds the query (``kb_query.embed_query``). The door makes
    no model call, so the row names no op."""
    row = am.route_for(am.KNOWLEDGE, "GET", "/knowledge/search")
    assert row is not None and not row.op and "AD-11" in row.off_door


def test_ad8_the_proposal_route_publishes_and_sends_nothing():
    """The handler of the proposal op files a draft or an edit row. It never
    publishes a page, starts an embed job or bumps the feed."""
    import inspect

    route = _live_route(am.KNOWLEDGE, "POST", "/knowledge/proposals")
    source = inspect.getsource(route.endpoint)
    from gateway.routes.knowledge import proposals

    body = inspect.getsource(proposals._propose_page) + inspect.getsource(
        proposals._propose_edit) + source
    for word in ("PUBLISH_SETS", "bump_feed_after_publish", "embed_pending", "send"):
        assert word not in body, word


def test_a_write_has_the_write_scope_and_a_read_the_read_scope():
    for door in am.door_ops(am.KNOWLEDGE).values():
        assert door.scope == ("knowledge:read" if door.kind == "read" else "knowledge:write")


# ── The builder (§19.3) ─────────────────────────────────────────────────────


def test_a_read_fills_the_path_and_puts_the_rest_in_the_query():
    call = am.build_call(am.KNOWLEDGE, "get_page_version", {"page_id": PAGE, "version": 3})
    assert call.path == f"/knowledge/pages/{PAGE}/versions/3"
    assert call.template == "/knowledge/pages/{page_id}/versions/{version}"
    assert call.body == b"" and call.query == () and call.if_match is None
    call = am.build_call(am.KNOWLEDGE, "list_pages",
                         {"state": "draft", "q": "plan", "space": None, "x": True})
    assert call.query == (("q", "plan"), ("state", "draft"), ("x", "true"))


@pytest.mark.parametrize(("op", "args", "name", "why"), [
    ("get_page", {}, "page_id", "missing"),
    ("get_page", {"page_id": "../../admin"}, "page_id", "shape"),
    ("get_page", {"page_id": PAGE + "\n"}, "page_id", "shape"),
    ("get_page_version", {"page_id": PAGE, "version": "1;"}, "version", "shape"),
    ("get_page_version", {"page_id": PAGE, "version": True}, "version", "missing"),
    ("list_pages", {"q": ["a", "b"]}, "q", "type"),
    ("list_pages", {"q": {"a": 1}}, "q", "type"),
    ("list_pages", {"a b": 1}, "a b", "key"),
    ("no_such_op", {}, "op", "unknown"),
])
def test_the_builder_refuses_a_value_it_cannot_use(op, args, name, why):
    with pytest.raises(am.BadCall) as caught:
        am.build_call(am.KNOWLEDGE, op, args)
    assert (caught.value.name, caught.value.why) == (name, why)


def test_the_body_bytes_are_stable():
    """AD-17 needs the same bytes for the same call: compact JSON, sorted."""
    one = am.build_call(am.KNOWLEDGE, "propose",
                        {"title": "T", "kind": "page", "body_md": "é", "summary": None})
    two = am.build_call(am.KNOWLEDGE, "propose",
                        {"body_md": "é", "kind": "page", "title": "T"})
    assert one == two
    assert one.body == '{"body_md":"é","kind":"page","title":"T"}'.encode()
    assert one.method == "POST" and one.query == ()


def test_expected_version_becomes_if_match_and_query_keys_stay_in_the_query():
    row = base.Route("PATCH", "/x/{id}", "t", "B", op="change", title="t",
                     query=("notify",))
    app = base.App(id="testdoor", name="T", reach="r", pane="/t", feature="t",
                   router="r", routes=(row,))
    call = am.build_call(app, "change", {"id": PAGE, "expected_version": "2026-10-30T00:00:00Z",
                                         "notify": False, "title": "A"})
    assert call.if_match == "2026-10-30T00:00:00Z"
    assert call.query == (("notify", "false"),)
    assert json.loads(call.body) == {"title": "A"}
    with pytest.raises(am.BadCall):
        am.build_call(app, "change", {"id": PAGE, "expected_version": 7})


def test_no_op_has_a_body_field_with_the_name_of_a_path_or_query_parameter():
    from pydantic import BaseModel

    for app in am.APPS:
        for door in am.door_ops(app).values():
            route = _live_route(app, door.route.method, door.route.path)
            names = {p.alias for p in route.dependant.path_params} | set(door.route.query)
            for param in route.dependant.body_params:
                kind = param.field_info.annotation
                fields = (set(kind.model_fields) if isinstance(kind, type)
                          and issubclass(kind, BaseModel) else {param.alias})
                assert not fields & names, (door.name, fields & names)


# ── The schema (AD-9) ───────────────────────────────────────────────────────


def _route_inputs(route, door) -> set[str]:
    from pydantic import BaseModel

    def nested(dep, kind):
        out = list(getattr(dep, kind))
        for sub in dep.dependencies:
            out += nested(sub, kind)
        return out

    page = door.route.page
    page_keys = {page.limit, page.cursor} if page else set()
    names = {p.alias for p in route.dependant.path_params}
    names |= {q.alias for q in nested(route.dependant, "query_params")} - page_keys
    for param in route.dependant.body_params:
        kind = param.field_info.annotation
        if isinstance(kind, type) and issubclass(kind, BaseModel):
            names |= {f.alias or n for n, f in kind.model_fields.items()}
    if any(h.alias.lower() == "if-match" for h in nested(route.dependant, "header_params")):
        names.add("expected_version")
    return names


def test_ad9_each_schema_holds_the_inputs_of_the_live_route():
    from gateway.routes.agent_apps import args_schema

    for door in am.door_ops(am.KNOWLEDGE).values():
        route = _live_route(am.KNOWLEDGE, door.route.method, door.route.path)
        schema = args_schema("knowledge", door.name)
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema.get("properties", {})) == _route_inputs(route, door), door.name


def test_ad9_no_identity_header_and_no_page_key_shows():
    from gateway.routes.agent_apps import args_schema

    for door in am.door_ops(am.KNOWLEDGE).values():
        text = json.dumps(args_schema("knowledge", door.name)).lower()
        for name in ("x-user-email", "x-user-role", "x-actor-via", "authorization"):
            assert name not in text, (door.name, name)
    recent = args_schema("knowledge", "list_recent")["properties"]
    assert "cursor" not in recent and "limit" not in recent and "mine" in recent
    propose = args_schema("knowledge", "propose")
    assert propose["properties"]["kind"]["enum"] == ["page", "edit"]
    assert set(propose["required"]) == {"kind"}


def test_ad9_a_schema_over_16_kib_keeps_its_names_only():
    from gateway.routes import agent_apps

    big = {"type": "object", "properties": {f"f{i}": {"description": "x" * 400}
                                            for i in range(60)}}
    cut = agent_apps._cut(big)
    assert set(cut["properties"]) == set(big["properties"])
    assert all(v == {} for v in cut["properties"].values())
    assert cut["description"] == agent_apps.CUT_NOTE


# ── The scopes (§19.6) ──────────────────────────────────────────────────────


def test_the_app_scopes_come_from_the_manifests_after_the_fixed_three():
    assert agent_oauth.APP_SCOPES == ("knowledge:read", "knowledge:write")
    assert agent_oauth.SUPPORTED_SCOPES == (
        "projects:read", "projects:write", "context:read", "knowledge:read",
        "knowledge:write", "offline_access")
    assert agent_oauth.SUPPORTED_SCOPES[:-1] == agent_oauth.RESOURCE_SCOPES
    assert agent_oauth.parse_scope("knowledge:write knowledge:read") == (
        "knowledge:read", "knowledge:write")


def test_the_cover_rule_holds_for_each_app():
    assert agent_oauth.covered_by("knowledge:write") == "knowledge:read"
    assert agent_oauth.covered_by("projects:write") == "projects:read"
    assert agent_oauth.covered_by("context:read") is None
    assert agent_oauth.covered_by(":write") is None
    assert agent_oauth.covered_scopes(["knowledge:write"]) == {
        "knowledge:write", "knowledge:read"}
    assert agent_oauth.scope_holds(("knowledge:write",), "knowledge:read")
    assert not agent_oauth.scope_holds(("knowledge:read",), "knowledge:write")
    assert not agent_oauth.scope_holds(("projects:write",), "knowledge:read")


def test_the_scope_of_a_dark_app_is_not_advertised(monkeypatch):
    from acb_common import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "agent_door_apps", "")
    assert agent_oauth.door_apps() == frozenset()
    assert agent_oauth.advertised_resource_scopes() == agent_oauth.FIXED_RESOURCE_SCOPES
    assert agent_oauth.advertised_scopes()[-1] == "offline_access"
    monkeypatch.setattr(settings, "agent_door_apps", " Knowledge , crm,nothing ")
    assert agent_oauth.door_apps() == frozenset({"knowledge"})
    assert agent_oauth.advertised_resource_scopes() == (
        *agent_oauth.FIXED_RESOURCE_SCOPES, "knowledge:read", "knowledge:write")


def test_the_door_flag_defaults_off():
    from acb_common.settings import Settings

    assert Settings.model_fields["agent_door_apps"].default == ""


def test_the_shared_rule_of_availability(monkeypatch):
    from acb_common import get_settings

    write = base.DoorOp(am.KNOWLEDGE, base.Route("POST", "/x", "t", "B", op="w", title="t"))
    assert agent_oauth.door_available(write) == (False, agent_oauth.APPROVAL_REQUIRED_CODE)
    propose = am.door_ops(am.KNOWLEDGE)["propose"]
    monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
    assert agent_oauth.door_available(propose) == (False, agent_oauth.DOOR_WRITES_OFF_CODE)
    monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", True)
    assert agent_oauth.door_available(propose) == (True, None)
    assert agent_oauth.door_available(am.door_ops(am.KNOWLEDGE)["get_page"]) == (True, None)
