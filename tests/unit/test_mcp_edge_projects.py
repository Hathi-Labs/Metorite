"""WS-55 MCP-G1 — ``create_project``, ``update_project``, ``preview_move_tasks`` and ``move_tasks``.

Spec: ``project-docs/specs/external_agent_access.md`` §9.2 (the MCP-G1 rows
and their rules), §9.5 rule 4 and §15 MCP-G1.

Two halves, as in ``test_mcp_edge_assignees.py``, whose helpers this file
reuses.

**Database-free (always runs).** The edge app runs in process with a fake
token store and a fake gateway. It proves the tool records, the body of each
write, the level check, the pin refusal of a space, the idempotency replay,
the version conflict, the preview id and the text of each refusal. No
refusal sends a write.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app. It proves who
sees a new node: a sub-project has the access of its parent, and a space has
the one ``org`` grant that the app gives a space that a member makes. It
proves the pins, the 404 of another organization, the move of an epic with
its subtasks, and the refusal of a move with no preview.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_projects.py -v -rs

**The mutation (R7).** ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* P1. ``G/tree.py``: ``create_node`` drops ``_refuse_pinned_space``.
  ``test_r3_a_pinned_connection_makes_no_space`` fails: the signed POST of a
  pinned connection answers 201.
"""
from __future__ import annotations

import dataclasses
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_common import get_settings
from agent_edge import gateway_client, idempotency, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
_uuid = w._uuid
GOOD = w.GOOD
TASK, PROJECT = w.TASK, w.PROJECT
S_TODO = w.S_TODO
V1, V2, V3 = w.V1, w.V2, t.V3
WRITES_OFF = t.WRITES_OFF
CHALLENGE_403_WRITE = w.CHALLENGE_403_WRITE

SPACE = _uuid(6001)
SUBPROJECT = _uuid(6003)
FOLDER = _uuid(6004)
FOLDER_IN_PROJECT = _uuid(6005)
ARCHIVED = _uuid(6006)
NEW_NODE = _uuid(6100)
DEST = _uuid(6200)
DEST_FOLDER = _uuid(6201)
S_OPEN = _uuid(6301)
TASK2 = _uuid(6401)
TYPE = _uuid(6501)
KEY = "g1-project-key-0001"

_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text
redis_fake = t.redis_fake


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free: a fake gateway with the node routes and the move
# ═══════════════════════════════════════════════════════════════════════════


def _node(node_id: str, level: str, parent: str | None, **kw: Any) -> dict[str, Any]:
    kind = "folder" if level == "folder" else "project"
    row = {"id": node_id, "name": f"Node {node_id[-4:]}", "description": None,
           "parent_project_id": parent, "kind": kind, "level": level,
           "personal": False, "archived_at": None, "status": "active",
           "updated_at": V1, "created_at": V1, "timezone": "UTC", "task_prefix": None,
           "lead": "lead@x.test", "owns_statuses": parent is None}
    row.update(kw)
    return row


def _plan(**kw: Any) -> dict[str, Any]:
    """A preview answer in the shape of ``move._public``."""
    body: dict[str, Any] = {
        "subtasks": {"count": 2, "hidden": 0, "refused": []},
        "required_missing": [],
        "destination_statuses": [{"id": S_OPEN, "name": "Open", "category": "todo"}],
        "source_project_id": PROJECT, "destination_project_id": DEST,
        "source_root_id": SPACE, "destination_root_id": DEST,
        "crosses_status_set": True, "crosses_root": True,
        "statuses": [{"from": {"id": S_TODO, "name": "To do", "category": "todo"},
                      "to": {"id": S_OPEN, "name": "Open", "category": "todo"}}],
        "field_map": {"size": "size"},
        "orphan_fields": [{"field_key": "colour", "name": "Colour", "field_type": "text"}],
        "drops": {"colour": [{"task_id": TASK, "task_number": 7, "value": "red"}]},
        "types": [{"from": {"id": TYPE, "name": "Bug"}, "to": None}],
        "tags": {"carried": ["ui"], "unregistered": ["ui"]},
        "task_count": 1,
    }
    body.update(kw)
    return body


class _Gateway(t._Gateway):
    """The EA-6d fake, with the node routes, the preview and the move."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in (
            _node(SPACE, "space", None),
            _node(PROJECT, "project", SPACE),
            _node(SUBPROJECT, "subproject", PROJECT),
            _node(FOLDER, "folder", SPACE),
            _node(FOLDER_IN_PROJECT, "folder", PROJECT),
            _node(ARCHIVED, "project", SPACE, archived_at=V1),
            _node(DEST, "project", SPACE),
            _node(DEST_FOLDER, "folder", SPACE),
        )}
        self.made: list[dict[str, Any]] = []
        self.preview_body: dict[str, Any] = _plan()

    def _answer(self, r: SimpleNamespace) -> tuple[int, Any]:
        if (r.method, r.path) in self.answers:
            return self.answers[(r.method, r.path)]
        if r.method == "POST" and r.path == "/projects/nodes":
            sent = json.loads(r.body)
            row = {"id": NEW_NODE, "name": sent["name"],
                   "description": sent.get("description"),
                   "parent_project_id": sent.get("parent_project_id"), "kind": "project",
                   "status": "active", "updated_at": V1, "lead": None}
            self.made.append(row)
            parent = sent.get("parent_project_id")
            level = "space" if parent is None else (
                "subproject" if self.nodes[parent]["level"] == "project" else "project")
            self.nodes[NEW_NODE] = {**_node(NEW_NODE, level, parent), "name": sent["name"],
                                    "updated_at": V3}
            return 201, row
        prefix = "/projects/nodes/"
        if r.method == "GET" and r.path.startswith(prefix) and r.path[len(prefix):] in self.nodes:
            return 200, dict(self.nodes[r.path[len(prefix):]])
        if r.method == "PATCH" and r.path == f"{prefix}{PROJECT}":
            sent = json.loads(r.body)
            self.nodes[PROJECT].update(sent)
            self.nodes[PROJECT]["updated_at"] = V3
            return 200, {**self.nodes[PROJECT], "updated_at": V2}
        if r.method == "POST" and r.path == "/projects/tasks/move/preview":
            return 200, dict(self.preview_body)
        if r.method == "POST" and r.path == "/projects/tasks/move":
            sent = json.loads(r.body)
            return 200, {"moved": len(sent["task_ids"]), "task_ids": sent["task_ids"],
                         "destination_project_id": sent["destination_project_id"],
                         "dropped_fields": sent["accepted_drops"], "subtasks_moved": 2,
                         "subtask_ids": [_uuid(6601), _uuid(6602)]}
        return super()._answer(r)


@pytest.fixture
def edge(monkeypatch, redis_fake) -> SimpleNamespace:
    """``test_mcp_edge_task_writes.edge`` with this file's fake gateway."""
    found: dict[str, Any] = {GOOD: w._lookup()}

    async def lookup(raw: str, *, kind: str = "access"):
        return found.get(raw)

    from acb_auth import agent_tokens

    monkeypatch.setattr(agent_tokens, "lookup_token", lookup)
    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", w.KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE), ("agent_edge_gateway_url", "http://gw.test")):
        monkeypatch.setattr(settings, name, value)
    fake = _Gateway()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield SimpleNamespace(gateway=fake, tokens=found, redis=redis_fake)


def _writes(gateway: _Gateway) -> list[SimpleNamespace]:
    return [r for r in gateway.requests if r.method != "GET"]


def _pinned() -> Any:
    return dataclasses.replace(w._lookup(), pinned_projects=(PROJECT,))


async def _listed() -> dict[str, dict[str, Any]]:
    r = await _post(_rpc("tools/list"))
    return {x["name"]: x for x in r.json()["result"]["tools"]}


def _move_args(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"task_ids": [TASK], "destination_project_id": DEST}
    args.update(kw)
    return args


async def _preview_id(**kw: Any) -> str:
    result = await _tool("preview_move_tasks", _move_args(**kw))
    assert result["isError"] is False, result
    return result["structuredContent"]["preview_id"]


NEW_TOOLS = ("create_project", "update_project", "preview_move_tasks", "move_tasks")


class TestTheToolRecords:
    """§9.2 and §9.5: the records in ``tools/list``, database-free."""

    async def test_the_four_tools_are_listed_with_their_hints(self, edge):
        listed = await _listed()
        hints = {name: {k: v for k, v in listed[name]["annotations"].items() if k != "title"}
                 for name in NEW_TOOLS}
        assert hints["create_project"] == {"readOnlyHint": False, "destructiveHint": False,
                                           "idempotentHint": False, "openWorldHint": False}
        assert hints["update_project"] == dict(tools.WRITE_ANNOTATIONS)
        # The preview writes nothing, and its client need not ask the person.
        assert hints["preview_move_tasks"] == dict(tools.READ_ANNOTATIONS)
        # A move drops values and can change who sees a task.
        assert hints["move_tasks"]["destructiveHint"] is True
        assert listed["move_tasks"]["inputSchema"]["required"] == [
            "task_ids", "destination_project_id", "preview_id"]
        assert listed["create_project"]["inputSchema"]["required"] == ["name", "level"]
        assert listed["create_project"]["inputSchema"]["properties"]["level"]["enum"] == [
            "space", "project", "subproject"]
        assert listed["update_project"]["inputSchema"]["required"] == [
            "project_id", "expected_version"]
        assert set(listed["preview_move_tasks"]["outputSchema"]["properties"]) == set(
            tools.PREVIEW_KEYS)
        assert set(listed["move_tasks"]["outputSchema"]["properties"]) == set(tools.MOVED_KEYS)

    def test_the_edge_copies_equal_their_gateway_sources(self):
        from gateway.routes.projects import authority, bulk, core, move, tree

        assert tools.AGENT_SPACE_PINNED == tree.AGENT_SPACE_PINNED
        assert tools.TREE_RACE_DETAIL == tree.TREE_RACE_DETAIL
        assert tools.RUN_STATES == core.RUN_STATES
        assert tools.TEXT_MOVE_NOT_ALLOWED == authority.TASK_MOVE_REFUSED
        assert set(tools.PROJECT_CREATE_BODY_KEYS) == set(tree.AGENT_NODE_CREATE_FIELDS)
        assert set(tools.PROJECT_UPDATE_BODY_KEYS) == set(tree.AGENT_NODE_PATCH_FIELDS)
        assert tools.MAX_MOVE_TASKS <= bulk.MAX_BULK
        assert set(tools._MOVE_SELECTION_INPUT) | {"accept_drops", "accepted_drops"} == set(
            move.MoveIn.model_fields)
        # No tool names a lead: the lead is a grant write (D-OP-11).
        for name in NEW_TOOLS:
            assert "lead" not in tools.TOOLS[name].input_schema["properties"], name

    def test_the_descriptions_teach_the_rules(self):
        create = tools.CREATE_PROJECT.description
        assert "whole organization" in create and "access of its parent" in create
        assert "cannot change who sees" in create and "no space" in create
        move = tools.MOVE_TASKS.description
        assert "preview_move_tasks" in move and "get a yes" in move
        assert "who can see a task" in move
        preview = tools.PREVIEW_MOVE_TASKS.description
        assert "change nothing" in preview and "show the result to the person" in preview
        assert "untrusted_text" in move and "untrusted_text" in create

    @pytest.mark.parametrize("name,args", [
        ("create_project", {"name": "x", "level": "space"}),
        ("update_project", {"project_id": PROJECT, "expected_version": V1, "name": "x"}),
        ("preview_move_tasks", _move_args()),
        ("move_tasks", _move_args(preview_id="a" * 43)),
    ])
    async def test_check_8_refuses_without_projects_write(self, edge, name, args):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call(name, args))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    async def test_with_the_writes_flag_off_the_four_tools_send_nothing(
            self, edge, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        assert not set(NEW_TOOLS) & set(await _listed())
        assert _text(await _tool("create_project", {"name": "x", "level": "space"})) == WRITES_OFF
        assert _text(await _tool("preview_move_tasks", _move_args())) == WRITES_OFF
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("name,args", [
        ("create_project", {"name": "x", "level": "space", "lead": "a@x.test"}),
        ("create_project", {"name": "x", "level": "space", "kind": "folder"}),
        ("update_project", {"project_id": PROJECT, "expected_version": V1, "lead": "a@x.test"}),
        ("update_project", {"project_id": PROJECT, "expected_version": V1,
                            "parent_project_id": SPACE}),
        ("move_tasks", _move_args(preview_id="a" * 43, accept_drops=True)),
    ])
    async def test_a_grant_or_a_setting_is_not_an_input(self, edge, name, args):
        extra = next(k for k in args if k not in tools.TOOLS[name].input_schema["properties"])
        assert _text(await _tool(name, args)) == f"The input {extra!r} is not known."
        assert edge.gateway.requests == []


class TestCreateProject:
    """The body, the level check and the pins, database-free."""

    async def test_a_subproject_posts_the_named_keys_after_the_level_check(self, edge):
        result = await _tool("create_project", {
            "name": " Wiring ", "description": "The loom", "level": "subproject",
            "parent_id": PROJECT})
        assert result["isError"] is False, result
        assert [(r.method, r.path) for r in edge.gateway.requests][:2] == [
            ("GET", f"/projects/nodes/{PROJECT}"), ("POST", "/projects/nodes")]
        (post,) = _writes(edge.gateway)
        assert json.loads(post.body) == {"name": "Wiring", "description": "The loom",
                                         "parent_project_id": PROJECT}
        out = result["structuredContent"]
        assert out["created"] is True and out["version"] == V3
        assert out["project"]["id"] == NEW_NODE and out["project"]["level"] == "subproject"
        assert out["project"]["name"] == {"untrusted_text": "Wiring"}
        assert set(out["project"]) == set(tools.PROJECT_KEYS)

    async def test_a_space_posts_the_name_only_and_reads_no_parent(self, edge):
        result = await _tool("create_project", {"name": "Ops", "level": "space"})
        assert result["isError"] is False, result
        (post,) = _writes(edge.gateway)
        assert json.loads(post.body) == {"name": "Ops"}
        assert [r.path for r in edge.gateway.requests if r.method == "GET"] == [
            f"/projects/nodes/{NEW_NODE}"]

    @pytest.mark.parametrize("parent,level,text", [
        (SPACE, "subproject", tools.TEXT_LEVEL_MISMATCH.format(made="project")),
        (PROJECT, "project", tools.TEXT_LEVEL_MISMATCH.format(made="subproject")),
        (SUBPROJECT, "subproject", tools.TEXT_LEVEL_FLOOR),
        (FOLDER_IN_PROJECT, "project", tools.TEXT_LEVEL_MISMATCH.format(made="subproject")),
        (ARCHIVED, "subproject", tools.TEXT_PARENT_ARCHIVED),
        (_uuid(4040), "project", tools.TEXT_PROJECT_NOT_FOUND),
    ])
    async def test_a_wrong_level_is_refused_with_no_write(self, edge, parent, level, text):
        result = await _tool("create_project", {"name": "x", "level": level,
                                                "parent_id": parent})
        assert _text(result) == text
        assert _writes(edge.gateway) == []

    async def test_a_folder_takes_the_level_of_the_node_above_it(self, edge):
        result = await _tool("create_project", {"name": "x", "level": "project",
                                                "parent_id": FOLDER})
        assert result["isError"] is False, result
        assert [r.path for r in edge.gateway.requests][:2] == [
            f"/projects/nodes/{FOLDER}", f"/projects/nodes/{SPACE}"]

    @pytest.mark.parametrize("args,text", [
        ({"name": "x", "level": "space", "parent_id": SPACE}, tools.TEXT_SPACE_NO_PARENT),
        ({"name": "x", "level": "project"}, tools.TEXT_LEVEL_NEEDS_PARENT),
        ({"name": "   ", "level": "space"}, tools.TEXT_NAME_BLANK),
        ({"name": "a\x00b", "level": "space"}, tools.text_nul("name")),
        ({"name": "x", "level": "folder"},
         "The input 'level' must be one of space, project, subproject."),
    ])
    async def test_a_bad_call_sends_no_request(self, edge, args, text):
        assert _text(await _tool("create_project", args)) == text
        assert edge.gateway.requests == []

    async def test_a_pinned_connection_makes_no_space_and_sends_nothing(self, edge):
        """The edge copy of the gateway's 403. The pins come from the token row."""
        edge.tokens[GOOD] = _pinned()
        result = await _tool("create_project", {"name": "x", "level": "space",
                                                "idempotency_key": KEY})
        assert _text(result) == tools.TEXT_SPACE_PINNED
        assert edge.gateway.requests == [] and edge.redis.slots() == {}

    async def test_a_pinned_connection_may_make_a_subproject(self, edge):
        edge.tokens[GOOD] = _pinned()
        result = await _tool("create_project", {"name": "x", "level": "subproject",
                                                "parent_id": PROJECT})
        assert result["isError"] is False, result

    @pytest.mark.parametrize("status,detail,text", [
        (403, tools.AGENT_SPACE_PINNED, tools.TEXT_SPACE_PINNED),
        (403, "Something else", tools.TEXT_WRITE_FORBIDDEN),
        (409, tools.TREE_RACE_DETAIL, tools.TEXT_TREE_RACE),
        (404, "Project not found", tools.TEXT_PROJECT_NOT_FOUND),
        (422, "A subproject is the lowest level.", tools.TEXT_BAD_REQUEST),
    ])
    async def test_the_answers_of_the_post(self, edge, status, detail, text):
        edge.gateway.answers[("POST", "/projects/nodes")] = (status, {"detail": detail})
        assert _text(await _tool("create_project", {"name": "x", "level": "space"})) == text


class TestTheKeyOfCreateProject:
    """The slot of ``create_task`` (EA-6d), keyed by this tool's name."""

    async def test_the_same_key_twice_makes_one_node(self, edge):
        args = {"name": "Ops", "level": "space", "idempotency_key": KEY}
        first = await _tool("create_project", args)
        second = await _tool("create_project", args)
        assert first["structuredContent"]["created"] is True
        assert second["structuredContent"]["created"] is False
        assert second["structuredContent"]["project"]["id"] == NEW_NODE
        assert len(edge.gateway.made) == 1
        (slot,) = edge.redis.slots().values()
        assert slot["state"] == idempotency.DONE and slot["id"] == NEW_NODE
        (name,) = edge.redis.slots()
        assert ":create_project:" in name and KEY not in name

    async def test_the_same_key_with_other_inputs_is_refused(self, edge):
        await _tool("create_project", {"name": "Ops", "level": "space", "idempotency_key": KEY})
        result = await _tool("create_project", {"name": "Other", "level": "space",
                                                "idempotency_key": KEY})
        assert _text(result) == tools.TEXT_PROJECT_KEY_REUSED
        assert len(edge.gateway.made) == 1

    async def test_a_refusal_before_the_post_frees_the_key(self, edge):
        result = await _tool("create_project", {"name": "x", "level": "project",
                                                "parent_id": _uuid(4041),
                                                "idempotency_key": KEY})
        assert _text(result) == tools.TEXT_PROJECT_NOT_FOUND
        assert edge.redis.slots() == {}

    async def test_a_post_with_no_clear_answer_keeps_the_key(self, edge):
        edge.gateway.answers[("POST", "/projects/nodes")] = (503, {})
        args = {"name": "Ops", "level": "space", "idempotency_key": KEY}
        assert _text(await _tool("create_project", args)) == tools.TEXT_BUSY
        assert _text(await _tool("create_project", args)) == tools.TEXT_PROJECT_KEY_UNKNOWN


class TestUpdateProject:
    """The PATCH with ``If-Match``, database-free."""

    async def test_it_patches_the_named_fields_only_with_if_match(self, edge):
        result = await _tool("update_project", {
            "project_id": PROJECT, "expected_version": V1, "name": " Wiring v2 ",
            "status": "on_hold"})
        assert result["isError"] is False, result
        (patch,) = _writes(edge.gateway)
        assert (patch.method, patch.path, patch.query) == (
            "PATCH", f"/projects/nodes/{PROJECT}", "")
        assert json.loads(patch.body) == {"name": "Wiring v2", "status": "on_hold"}
        assert patch.headers["if-match"] == V1
        out = result["structuredContent"]
        assert out["version"] == V3 and out["project"]["status"] == "on_hold"

    async def test_a_stale_version_is_the_stale_text(self, edge):
        edge.gateway.answers[("PATCH", f"/projects/nodes/{PROJECT}")] = (
            412, {"detail": {"error": "precondition_failed"}})
        result = await _tool("update_project", {"project_id": PROJECT,
                                                "expected_version": V1, "name": "x"})
        assert _text(result) == tools.TEXT_PROJECT_STALE

    @pytest.mark.parametrize("detail", [
        "A space has no run state. It summarises the work below it, and only a project"
        " or a subproject can be started or paused.",
        "A folder has no run state. It groups the projects below it, and only a"
        " project or a subproject can be started or paused.",
    ])
    async def test_a_status_on_a_space_has_its_text(self, edge, detail):
        edge.gateway.answers[("PATCH", f"/projects/nodes/{PROJECT}")] = (
            422, {"detail": detail})
        result = await _tool("update_project", {"project_id": PROJECT,
                                                "expected_version": V1, "status": "active"})
        assert _text(result) == tools.TEXT_NO_RUN_STATE

    @pytest.mark.parametrize("args,text", [
        ({}, tools.TEXT_PROJECT_UPDATE_EMPTY),
        ({"name": " "}, tools.TEXT_NAME_BLANK),
        ({"status": "archived"},
         "The input 'status' must be one of queued, active, on_hold, stopped, done."),
        ({"description": "\x00"}, tools.text_nul("description")),
    ])
    async def test_a_bad_call_sends_no_request(self, edge, args, text):
        result = await _tool("update_project", {"project_id": PROJECT,
                                                "expected_version": V1, **args})
        assert _text(result) == text
        assert edge.gateway.requests == []


class TestPreviewMoveTasks:
    """The preview, database-free."""

    async def test_it_posts_the_selection_and_maps_the_plan(self, edge):
        result = await _tool("preview_move_tasks", _move_args(
            task_ids=[TASK, TASK.upper(), TASK2], status_map={S_TODO: S_OPEN}))
        assert result["isError"] is False, result
        assert [(r.method, r.path) for r in edge.gateway.requests] == [
            ("GET", f"/projects/nodes/{DEST}"), ("POST", "/projects/tasks/move/preview")]
        (post,) = _writes(edge.gateway)
        assert json.loads(post.body) == {
            "task_ids": [TASK, TASK2], "destination_project_id": DEST,
            "include_subtasks": True, "status_map": {S_TODO: S_OPEN}}
        out = result["structuredContent"]
        assert set(out) == set(tools.PREVIEW_KEYS)
        assert out["can_move"] is True and out["blocked"] == []
        assert out["task_ids"] == [TASK, TASK2]
        assert out["statuses"][0]["to"]["name"] == {"untrusted_text": "Open"}
        assert out["drops"] == [{"field_key": "colour", "task_id": TASK, "task_number": 7,
                                 "value": {"untrusted_text": "red"}}]
        assert out["tags"]["carried"] == [{"untrusted_text": "ui"}]
        assert out["orphan_fields"][0]["name"] == {"untrusted_text": "Colour"}
        assert out["types"] == [{"from": {"id": TYPE, "name": {"untrusted_text": "Bug"}},
                                 "to": None}]
        assert out["subtasks"] == {"count": 2, "hidden": 0, "refused": []}
        assert len(out["preview_id"]) == 43

    @pytest.mark.parametrize("plan,args,code", [
        ({"source_project_id": DEST}, {}, "already_there"),
        ({"required_missing": ["Budget"]}, {}, "required_missing"),
        ({"subtasks": {"count": 2, "hidden": 1, "refused": []}}, {}, "subtasks_hidden"),
        ({"subtasks": {"count": 1, "hidden": 0, "refused": [
            {"task_id": TASK2, "ref": "#9", "reason": "r"}]}}, {}, "subtask_private"),
        ({}, {"status_map": {S_TODO: _uuid(4042)}}, "status_map_unknown"),
        ({"statuses": [{"from": {"id": S_TODO, "name": "To do", "category": "todo"},
                        "to": None}]}, {}, "status_unmapped"),
    ])
    async def test_each_block_is_named(self, edge, plan, args, code):
        edge.gateway.preview_body = _plan(**plan)
        out = (await _tool("preview_move_tasks", _move_args(**args)))["structuredContent"]
        assert out["blocked"] == [code] and out["can_move"] is False
        assert code in tools.MOVE_BLOCKED_TEXTS

    async def test_without_its_subtasks_a_hidden_subtask_does_not_block(self, edge):
        edge.gateway.preview_body = _plan(subtasks={"count": 2, "hidden": 0, "refused": []})
        out = (await _tool("preview_move_tasks",
                           _move_args(include_subtasks=False)))["structuredContent"]
        assert out["include_subtasks"] is False and out["can_move"] is True
        assert json.loads(_writes(edge.gateway)[0].body)["include_subtasks"] is False

    @pytest.mark.parametrize("dest,text", [
        (DEST_FOLDER, tools.TEXT_MOVE_INTO_FOLDER),
        (SPACE, tools.TEXT_MOVE_INTO_SPACE),
        (ARCHIVED, tools.TEXT_MOVE_INTO_ARCHIVED),
        (_uuid(4043), tools.TEXT_PROJECT_NOT_FOUND),
    ])
    async def test_a_destination_that_holds_no_tasks_is_refused_first(self, edge, dest, text):
        assert _text(await _tool("preview_move_tasks", _move_args(
            destination_project_id=dest))) == text
        assert _writes(edge.gateway) == []

    @pytest.mark.parametrize("status,detail,text", [
        (422, "A bulk move needs one source project, so that one mapping applies to every"
              " task. Deselect #4, #5.", tools.TEXT_MOVE_ONE_SOURCE),
        (422, "That project is personal, so a task cannot be moved into it from outside."
              " More words.", tools.TEXT_MOVE_INTO_PERSONAL),
        (422, "Cannot map 'a' to 'b': one of them is not a field of its project.",
         tools.TEXT_MOVE_FIELD_MAP),
        (403, tools.TEXT_MOVE_NOT_ALLOWED, tools.TEXT_MOVE_NOT_ALLOWED),
        (404, "Task not found", tools.TEXT_TASK_NOT_FOUND),
        (404, "Project not found", tools.TEXT_PROJECT_NOT_FOUND),
        (422, "Some planted text the agent must not see.", tools.TEXT_BAD_REQUEST),
    ])
    async def test_each_refusal_has_a_fixed_text(self, edge, status, detail, text):
        edge.gateway.answers[("POST", "/projects/tasks/move/preview")] = (
            status, {"detail": detail})
        assert _text(await _tool("preview_move_tasks", _move_args())) == text

    @pytest.mark.parametrize("args,text", [
        ({"status_map": {"not-an-id": S_OPEN}},
         tools.TEXT_MOVE_MAP_FORM.format(name="status_map")),
        ({"field_map": {"bad key!": "size"}}, tools.TEXT_MOVE_MAP_FORM.format(name="field_map")),
        ({"task_ids": []}, "The input 'task_ids' needs at least 1 items."),
        ({"task_ids": [_uuid(n) for n in range(51)]},
         "The input 'task_ids' takes at most 50 items."),
    ])
    async def test_a_bad_selection_sends_no_request(self, edge, args, text):
        assert _text(await _tool("preview_move_tasks", _move_args(**args))) == text
        assert edge.gateway.requests == []


class TestMoveTasks:
    """No move commits without a preview of the same selection (D-PM-29)."""

    async def test_a_move_with_the_preview_id_sends_the_shown_drops(self, edge):
        shown = await _preview_id()
        edge.gateway.requests.clear()
        result = await _tool("move_tasks", _move_args(preview_id=shown))
        assert result["isError"] is False, result
        assert [(r.method, r.path) for r in edge.gateway.requests] == [
            ("GET", f"/projects/nodes/{DEST}"), ("POST", "/projects/tasks/move/preview"),
            ("POST", "/projects/tasks/move")]
        move = _writes(edge.gateway)[-1]
        assert json.loads(move.body) == {
            "task_ids": [TASK], "destination_project_id": DEST, "include_subtasks": True,
            "accept_drops": True, "accepted_drops": ["colour"]}
        assert result["structuredContent"] == {
            "moved": 1, "task_ids": [TASK], "destination_project_id": DEST,
            "dropped_fields": ["colour"], "subtasks_moved": 2,
            "subtask_ids": [_uuid(6601), _uuid(6602)]}

    async def test_a_missing_preview_is_refused_with_no_request(self, edge):
        assert _text(await _tool("move_tasks", _move_args())) == (
            "The input 'preview_id' is required.")
        assert edge.gateway.requests == []

    async def test_a_made_up_preview_id_moves_nothing(self, edge):
        result = await _tool("move_tasks", _move_args(preview_id="A" * 43))
        assert _text(result) == tools.TEXT_MOVE_CHANGED
        assert ("POST", "/projects/tasks/move") not in {
            (r.method, r.path) for r in edge.gateway.requests}

    @pytest.mark.parametrize("change", [
        {"drops": {"colour": [{"task_id": TASK, "task_number": 7, "value": "blue"}]}},
        {"subtasks": {"count": 3, "hidden": 0, "refused": []}},
        {"destination_statuses": []},
    ])
    async def test_a_preview_that_changed_moves_nothing(self, edge, change):
        shown = await _preview_id()
        edge.gateway.preview_body = _plan(**change)
        result = await _tool("move_tasks", _move_args(preview_id=shown))
        assert _text(result) == tools.TEXT_MOVE_CHANGED
        assert [r for r in _writes(edge.gateway) if r.path == "/projects/tasks/move"] == []

    async def test_the_id_binds_the_selection(self, edge):
        shown = await _preview_id()
        for other in (_move_args(task_ids=[TASK2]), _move_args(include_subtasks=False),
                      _move_args(field_map={"size": "size"})):
            result = await _tool("move_tasks", {**other, "preview_id": shown})
            assert _text(result) == tools.TEXT_MOVE_CHANGED
        # The order of rows that the gateway gives does not change the id.
        body = _plan()
        body["statuses"] = list(reversed(body["statuses"] + [
            {"from": {"id": S_OPEN, "name": "Open", "category": "todo"},
             "to": {"id": S_OPEN, "name": "Open", "category": "todo"}}]))
        edge.gateway.preview_body = body
        first = await _preview_id()
        body["statuses"] = list(reversed(body["statuses"]))
        assert await _preview_id() == first

    async def test_another_connection_cannot_use_the_id(self, edge):
        shown = await _preview_id()
        edge.tokens[GOOD] = dataclasses.replace(w._lookup(), connection_id=_uuid(7777))
        assert _text(await _tool("move_tasks", _move_args(preview_id=shown))) == (
            tools.TEXT_MOVE_CHANGED)

    async def test_a_blocked_move_has_its_text_and_moves_nothing(self, edge):
        edge.gateway.preview_body = _plan(source_project_id=DEST)
        shown = await _preview_id()
        result = await _tool("move_tasks", _move_args(preview_id=shown))
        assert _text(result) == tools.MOVE_BLOCKED_TEXTS["already_there"]
        assert [r for r in _writes(edge.gateway) if r.path == "/projects/tasks/move"] == []

    async def test_a_move_with_no_drops_accepts_none(self, edge):
        edge.gateway.preview_body = _plan(drops={}, orphan_fields=[])
        shown = await _preview_id()
        assert (await _tool("move_tasks", _move_args(preview_id=shown)))["isError"] is False
        body = json.loads(_writes(edge.gateway)[-1].body)
        assert body["accept_drops"] is False and body["accepted_drops"] == []

    @pytest.mark.parametrize("status,detail,text", [
        (409, "The destination changed since you looked.", tools.TEXT_MOVE_CHANGED),
        (403, tools.TEXT_MOVE_NOT_ALLOWED, tools.TEXT_MOVE_NOT_ALLOWED),
        (422, "That status does not belong to this project.", tools.TEXT_MOVE_STATUS_MAP),
    ])
    async def test_the_answers_of_the_move(self, edge, status, detail, text):
        shown = await _preview_id()
        edge.gateway.answers[("POST", "/projects/tasks/move")] = (status, {"detail": detail})
        assert _text(await _tool("move_tasks", _move_args(preview_id=shown))) == text


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpproj"
_APP_PW = "mcpproj_scratch_pw"
MEMBER = w.MEMBER
OWNER = w.OWNER
INTERNAL = w.INTERNAL
ANN = "ann@mcpwrites.test"   # a grant on the restricted space
BOB = "bob@mcpwrites.test"   # a member with no grant
EVE = "eve@mcpwrites.test"   # a member of ANOTHER organization


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two tenants.

    The first holds the member of the connection, Ann and Bob. The space
    ``restricted`` has a grant for the member and for Ann only, and the
    project ``p1`` sits in it. The move has a source project and a
    destination project, each in a space of its own. The second tenant holds
    Eve and the project ``foreign``.
    """
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpproj"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org, org2 = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "mcpproj"), (org2, "mcpproj2")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.number = 100_000
    for who in (MEMBER, ANN, BOB):
        a.member(who, ["feature:projects"])
    b = w._Seeder(eng, org2)
    b.member(EVE, ["feature:projects"])
    foreign = b.project("Foreign")

    p1 = a.project("P1", in_space=True)
    restricted = a.root(p1)
    a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
          " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
          " RETURNING project_id", p=restricted, s=ANN, a=OWNER, o=org)
    source, dest = a.project("Source", in_space=True), a.project("Destination", in_space=True)
    s = SimpleNamespace(
        p1=a.status(p1, "To do", "todo", 0),
        source=a.status(source, "To do", "todo", 0),
        dest=a.status(dest, "To do", "todo", 0),
    )
    world_ = SimpleNamespace(admin=eng, org=org, org2=org2, seeder=a, p1=p1,
                             restricted=restricted, source=source, dest=dest,
                             foreign=foreign, s=s, c={}, tok={}, task=a.task)
    for name, scopes, pins in (("write", w.WRITE_SCOPES, ()),
                               ("read", ("projects:read",), ()),
                               ("pinned", w.WRITE_SCOPES, (p1,))):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes, pins=pins)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


live_app = t.live_app
live = t.live
_mcp, _send, _in_scope = t._mcp, t._send, t._in_scope


async def _as(world, who: str, method: str, path: str,
              body: dict[str, Any] | None = None,
              headers: dict[str, str] | None = None) -> httpx.Response:
    """One request to the gateway app as the member *who*, through the
    internal bearer, as the app sends it."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    sent = {"Authorization": f"Bearer {INTERNAL}", "X-User-Email": who, **(headers or {})}
    if body is not None:
        sent["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=sent, content=raw)
    return await _in_scope(world, go)


def _scalar(world, sql: str, **params: Any) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(sql), params).scalar()


def _grants(world, project: str) -> list[str]:
    with world.admin.begin() as c:
        return sorted(r[0] for r in c.execute(text(
            "SELECT subject FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"),
            {"p": project}))


def _project_of(world, task: str) -> str:
    return str(_scalar(world, "SELECT project_id FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                       t=task))


def _mine(world, task: str) -> None:
    """The member made *task*, so D-OP-14 lets the member move it."""
    world.seeder.one("UPDATE pm_tasks SET created_by = :m WHERE id = CAST(:t AS uuid)"
                     " RETURNING id", m=MEMBER, t=task)


async def _version(world, project: str) -> str:
    read = await _mcp(world, "write", "get_project", {"project_id": project})
    assert read["isError"] is False, read
    return read["structuredContent"]["project"]["updated_at"]


def _msg(result: dict[str, Any]) -> str:
    assert result["isError"] is True, result
    return result["content"][0]["text"]


class TestTheRulesOnARealDatabase:
    """§15 MCP-G1, through the real gateway app."""

    pytestmark = _DB_GATE

    async def test_r1_a_subproject_has_the_access_of_its_parent(self, live):
        result = await _mcp(live, "write", "create_project", {
            "name": "g1 wiring", "level": "subproject", "parent_id": live.p1})
        assert result["isError"] is False, result
        made = result["structuredContent"]["project"]
        assert made["level"] == "subproject" and made["parent_id"] == live.p1
        sub = made["id"]
        # The edge writes no grant: the node inherits through the closure.
        assert _grants(live, sub) == []
        for who, seen in ((ANN, 200), (BOB, 404)):
            for node in (live.p1, sub):
                r = await _as(live, who, "GET", f"/projects/nodes/{node}")
                assert r.status_code == seen, (who, node, r.text)

    async def test_r2_a_space_has_the_access_that_the_app_gives_it(self, live):
        result = await _mcp(live, "write", "create_project", {
            "name": "g1 agent space", "level": "space"})
        assert result["isError"] is False, result
        space = result["structuredContent"]["project"]["id"]
        assert result["structuredContent"]["project"]["level"] == "space"
        # The app's own create, by the member, for the comparison.
        r = await _as(live, MEMBER, "POST", "/projects/nodes", body={"name": "g1 app space"})
        assert r.status_code == 201, r.text
        app_space = r.json()["id"]
        assert _grants(live, space) == _grants(live, app_space) == ["org"]
        assert _scalar(live, "SELECT count(*) FROM pm_task_statuses WHERE project_id ="
                       " CAST(:p AS uuid)", p=space) == _scalar(
            live, "SELECT count(*) FROM pm_task_statuses WHERE project_id ="
            " CAST(:p AS uuid)", p=app_space) > 0
        # Bob holds no grant and sees it, as he sees the app's space.
        assert (await _as(live, BOB, "GET", f"/projects/nodes/{space}")).status_code == 200
        # Eve, of another organization, does not.
        assert (await _as(live, EVE, "GET", f"/projects/nodes/{space}")).status_code == 404
        # A pinned connection of the same member does not reach it.
        pinned = await _mcp(live, "pinned", "get_project", {"project_id": space})
        assert _msg(pinned) == tools.TEXT_PROJECT_NOT_FOUND

    async def test_r3_a_pinned_connection_makes_no_space(self, live):
        result = await _mcp(live, "pinned", "create_project", {
            "name": "g1 pinned space", "level": "space"})
        assert _msg(result) == tools.TEXT_SPACE_PINNED
        # The gateway's own guard, for a signed request that skips the edge.
        r = await _send(live, "POST", "/projects/nodes", body={"name": "g1 pinned space"},
                        signed="pinned")
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == tools.AGENT_SPACE_PINNED
        assert _scalar(live, "SELECT count(*) FROM pm_projects WHERE name ="
                       " 'g1 pinned space'") == 0
        # Under a pinned project it may make a node. Outside the pins the
        # parent is "not found", as a hidden record is (§7.1).
        ok = await _mcp(live, "pinned", "create_project", {
            "name": "g1 pinned sub", "level": "subproject", "parent_id": live.p1})
        assert ok["isError"] is False, ok
        outside = await _mcp(live, "pinned", "create_project", {
            "name": "g1 outside", "level": "subproject", "parent_id": live.source})
        assert _msg(outside) == tools.TEXT_PROJECT_NOT_FOUND

    async def test_r4_update_project_keeps_the_version_rule(self, live):
        old = await _version(live, live.p1)
        result = await _mcp(live, "write", "update_project", {
            "project_id": live.p1, "expected_version": old, "name": "P1 renamed",
            "status": "on_hold"})
        assert result["isError"] is False, result
        new = result["structuredContent"]["version"]
        assert new and new != old
        assert _scalar(live, "SELECT name FROM pm_projects WHERE id = CAST(:p AS uuid)",
                       p=live.p1) == "P1 renamed"
        stale = await _mcp(live, "write", "update_project", {
            "project_id": live.p1, "expected_version": old, "name": "P1 lost"})
        assert _msg(stale) == tools.TEXT_PROJECT_STALE
        assert _scalar(live, "SELECT name FROM pm_projects WHERE id = CAST(:p AS uuid)",
                       p=live.p1) == "P1 renamed"
        space = await _mcp(live, "write", "update_project", {
            "project_id": live.restricted, "expected_version": await _version(
                live, live.restricted), "status": "active"})
        assert _msg(space) == tools.TEXT_NO_RUN_STATE
        # The gateway narrows a signed PATCH: a lead is a grant write.
        r = await _send(live, "PATCH", f"/projects/nodes/{live.p1}",
                        body={"lead": ANN}, signed="write")
        assert r.status_code == 422, r.text
        # A member's PATCH with no If-Match is the app's call, unchanged. A
        # stale If-Match from a member is refused too.
        r = await _as(live, MEMBER, "PATCH", f"/projects/nodes/{live.p1}",
                      body={"description": "member edit"})
        assert r.status_code == 200, r.text
        r = await _as(live, MEMBER, "PATCH", f"/projects/nodes/{live.p1}",
                      body={"description": "late"}, headers={"If-Match": new})
        assert r.status_code == 412, r.text

    async def test_r5_an_epic_moves_with_its_subtasks(self, live):
        epic = live.task(live.source, live.s.source, "g1 epic")
        subs = [live.task(live.source, live.s.source, f"g1 sub {n}") for n in (1, 2)]
        for sub in subs:
            live.seeder.one("UPDATE pm_tasks SET parent_task_id = CAST(:e AS uuid)"
                            " WHERE id = CAST(:s AS uuid) RETURNING id", e=epic, s=sub)
        _mine(live, epic)
        args = {"task_ids": [epic], "destination_project_id": live.dest}
        preview = await _mcp(live, "write", "preview_move_tasks", args)
        assert preview["isError"] is False, preview
        shown = preview["structuredContent"]
        assert shown["can_move"] is True and shown["subtasks"]["count"] == 2
        assert shown["crosses_root"] is True
        # The preview wrote nothing.
        assert _project_of(live, epic) == live.source
        moved = await _mcp(live, "write", "move_tasks", {**args,
                                                        "preview_id": shown["preview_id"]})
        assert moved["isError"] is False, moved
        assert moved["structuredContent"]["moved"] == 1
        assert moved["structuredContent"]["subtasks_moved"] == 2
        for task in (epic, *subs):
            assert _project_of(live, task) == live.dest
        # The same call again finds the tasks there and moves nothing more.
        again = await _mcp(live, "write", "move_tasks", {**args,
                                                        "preview_id": shown["preview_id"]})
        assert _msg(again) == tools.MOVE_BLOCKED_TEXTS["already_there"]

    async def test_r6_no_move_commits_without_the_preview(self, live):
        task = live.task(live.source, live.s.source, "g1 no preview")
        _mine(live, task)
        args = {"task_ids": [task], "destination_project_id": live.dest}
        made_up = await _mcp(live, "write", "move_tasks", {**args, "preview_id": "A" * 43})
        assert _msg(made_up) == tools.TEXT_MOVE_CHANGED
        assert _project_of(live, task) == live.source
        # The gateway's own guard: a signed move must send the shown drops.
        r = await _send(live, "POST", "/projects/tasks/move", body=args, signed="write")
        assert r.status_code == 422, r.text
        from gateway.routes.projects import move

        assert r.json()["detail"] == move.AGENT_MOVE_NEEDS_DROPS
        assert _project_of(live, task) == live.source

    async def test_r7_the_move_authority_of_the_app_holds(self, live):
        """D-OP-14: the member did not make the task and leads nothing."""
        task = live.task(live.source, live.s.source, "g1 not mine")
        result = await _mcp(live, "write", "preview_move_tasks", {
            "task_ids": [task], "destination_project_id": live.dest})
        assert _msg(result) == tools.TEXT_MOVE_NOT_ALLOWED

    async def test_r8_another_organization_gets_not_found(self, live):
        for tool, args in (
                ("create_project", {"name": "g1 graft", "level": "project",
                                    "parent_id": live.foreign}),
                ("update_project", {"project_id": live.foreign, "expected_version": V1,
                                    "name": "g1 taken"}),
                ("preview_move_tasks", {"task_ids": [live.task(
                    live.source, live.s.source, "g1 to another org")],
                    "destination_project_id": live.foreign})):
            result = await _mcp(live, "write", tool, args)
            assert _msg(result) == tools.TEXT_PROJECT_NOT_FOUND, tool
        # The signed PATCH that skips the edge gets the gateway's 404 too.
        r = await _send(live, "PATCH", f"/projects/nodes/{live.foreign}",
                        body={"name": "g1 taken"}, signed="write")
        assert r.status_code == 404, r.text
        assert _scalar(live, "SELECT count(*) FROM pm_projects WHERE name IN"
                       " ('g1 graft', 'g1 taken')") == 0

    async def test_r9_a_move_outside_the_pins_is_not_found(self, live):
        task = live.task(live.source, live.s.source, "g1 outside the pins")
        _mine(live, task)
        result = await _mcp(live, "pinned", "preview_move_tasks", {
            "task_ids": [task], "destination_project_id": live.p1})
        assert _msg(result) == tools.TEXT_TASK_NOT_FOUND
        into = await _mcp(live, "pinned", "preview_move_tasks", {
            "task_ids": [task], "destination_project_id": live.dest})
        assert _msg(into) == tools.TEXT_PROJECT_NOT_FOUND

    async def test_r10_a_read_connection_writes_nothing(self, live):
        from agent_edge import app as edge_app_module

        async def call() -> httpx.Response:
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                    base_url=w.ISSUER) as client:
                return await client.post(
                    "/mcp", content=json.dumps(_call("create_project", {
                        "name": "g1 read", "level": "space"})),
                    headers={"authorization": f"Bearer {live.tok['read']}",
                             "content-type": "application/json",
                             "mcp-protocol-version": "2025-11-25"})

        r = await _in_scope(live, call)
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        for method, path, body in (
                ("POST", "/projects/nodes", {"name": "g1 read"}),
                ("PATCH", f"/projects/nodes/{live.p1}", {"name": "g1 read"}),
                ("POST", "/projects/tasks/move/preview",
                 {"task_ids": [_uuid(1)], "destination_project_id": live.dest})):
            refused = await _send(live, method, path, body=body, signed="read")
            assert refused.status_code == 401, (path, refused.text)
        assert _scalar(live, "SELECT count(*) FROM pm_projects WHERE name = 'g1 read'") == 0
