"""WS-55 MCP-G2 — more fields on ``create_task`` and ``update_task``, and ``set_assignees``.

Spec: ``project-docs/specs/external_agent_access.md`` §9.2 (the MCP-G2 rows
and their rules), §9.5 rule 4 and §15 MCP-G2.

Two halves, as in ``test_mcp_edge_task_writes.py``, whose helpers this file
reuses.

**Database-free (always runs).** The edge app runs in process with a fake
token store and a fake gateway. It proves the tool records in ``tools/list``,
the limits of each new input, the body of each write, and the text of each
refusal. No refusal sends a request.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app. It proves the
rules of §9.2: one bell for each new assignee, which names the client; the
refusal of a new person who cannot already open the task through its grants
(an agent never widens who sees a task), with one text for a hidden member
and a made-up address; the strict version rule of the agent's PUT and the
version rule of the move; a move to another project and a cycle refused; the
caps; and the write scope.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_assignees.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``E`` is
``apps/services/agent_edge/agent_edge``, ``G`` is
``apps/services/gateway/gateway/routes/projects`` and ``A`` is
``packages/acb_auth/acb_auth``.

* A1. ``G/tasks.py``: ``_refuse_agent_assignees`` returns at once. ``test_r3_a_hidden_member_and_a_made_up_address_get_one_text`` and ``test_r1_create_with_an_assignee_and_the_planning_fields``.
* A2. ``G/tasks.py``: ``_refuse_agent_assignees`` checks a member caller too. ``test_r10_a_members_put_is_not_narrowed``.
* A3. ``G/tasks.py``: ``_write_assignees`` notifies ``wanted``, not ``added``. ``test_r2_one_bell_for_the_added_person_only``.
* A4. ``G/tasks.py``: ``_agent_via_note`` returns None. ``test_r1_create_with_an_assignee_and_the_planning_fields`` and ``test_r2_one_bell_for_the_added_person_only``.
* A5. ``G/tasks.py``: ``set_assignees`` drops the agent's ``_require_exact_version``. ``test_r4_a_stale_version_is_refused_for_set_assignees`` and ``test_r14_an_agent_put_keeps_strict_equality``.
* A6. ``G/tasks.py``: ``move_task`` drops ``_check_version``. ``test_r5_a_stale_version_is_refused_for_the_move``.
* A7. ``G/tasks.py``: ``move_task_in`` drops ``_refuse_agent_parent``. ``test_r6_a_move_to_another_project_is_refused``.
* A8. ``G/tasks.py``: ``AGENT_MOVE_FIELDS`` gains ``project_id``. ``test_r6_a_move_to_another_project_is_refused``.
* A9. ``E/tools.py``: ``_MOVE_DETAILS`` loses the cycle row. ``test_r7_a_cycle_is_refused``.
* A10. ``A/agent_oauth.py``: the PUT pair leaves ``MCP_WRITE_ROUTES``. ``test_mcp_edge_coverage.py::test_the_write_pairs_are_those_of_ea_6a_ea_6d_and_ea_12a`` and ``test_r2_one_bell_for_the_added_person_only``.
* A11. ``E/tools.py``: ``_create`` sends no ``coded`` map. ``test_create_names_the_refused_entries``.
* A12. ``E/tools.py``: ``check_input`` drops a null of ``parent_task_id``. ``test_null_moves_the_task_to_the_top_level``.
* A13. ``G/tasks.py``: ``_refuse_agent_values`` returns at once. ``test_mcp_edge_task_writes.py::test_a_create_with_another_key_is_422`` (``importance`` 3 and ``estimate_mins`` -1).
* A14. ``E/tools.py``: ``IMPORTANCE_VALUES`` holds 0 to 3. ``test_the_planning_limits_are_refused_with_no_request``.
* A15. ``E/tools.py``: ``_update_task`` sends a PATCH and a move in one call. ``test_parent_and_fields_together_are_refused``.
* A16. ``E/tools.py``: ``_assignee_codes`` names the gateway's entries too. ``test_a_refused_entry_is_named_and_only_from_the_call``.
* A17. ``E/tools.py``: ``_create_task`` drops ``_refuse_bad_start_date``. ``test_the_planning_limits_are_refused_with_no_request``.

Fix round 1 adds these.

* A18. ``G/tasks.py``: ``_refuse_agent_assignees`` as the first build had it, after the insert and with the assignee arm. ``test_r13_an_agent_cannot_widen_who_sees_a_task``.
* A19. ``G/notifications.py``: ``grant_holders`` reads ``task_visibility_clause``. ``test_r19_the_grant_only_seam_leaves_out_the_assignee_arm``.
* A20. ``G/tasks.py``: the agent's PUT uses ``_check_version`` (``precondition_holds``). ``test_r14_an_agent_put_keeps_strict_equality``.
* A21. ``G/tasks.py``: the member's PUT drops ``_check_version``. ``test_r10_a_members_put_is_not_narrowed``.
* A22. ``E/tools.py``: ``ASSIGN_ANNOTATIONS`` loses ``destructiveHint``. ``test_set_assignees_is_listed_with_the_hints_of_a_write``.
* A23. ``G/tasks.py``: ``_assert_agent_may_assign_here`` gives the plain 422. ``test_r18_a_personal_project_gets_its_own_text``.
* A24. ``G/tasks.py``: ``_write_assignees`` drops the cap of an agent. ``test_r15_the_gateway_caps_an_agent_at_50_assignees``.
* A25. ``G/tasks.py``: ``_refuse_agent_values`` drops the upper bound of ``estimate_mins``. ``test_r16_the_gateway_holds_the_estimate_ceiling``.
* A26. ``G/tasks.py``: ``_refuse_agent_assignees`` checks ``wanted``, not ``added``. ``test_r17_a_person_who_stays_is_not_checked_or_notified``.

Fix round 2 adds these.

* A27 (the verifier's M2). ``G/notifications.py``: ``grant_holders`` resolves no groups (``groups=()``). ``test_r20_each_grant_path_lets_an_agent_assign``.
* A28. ``G/notifications.py``: ``grant_holders`` resolves every person as restricted. ``test_r20_each_grant_path_lets_an_agent_assign``.
* A29. ``G/tasks.py``: ``_write_assignees`` compares ``wanted`` with the stored rows as they are. ``test_r21_a_row_in_another_case_is_the_same_person``.
"""
from __future__ import annotations

import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth
from acb_common import get_settings
from agent_edge import gateway_client, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
_uuid = w._uuid
GOOD = w.GOOD
TASK, PROJECT = w.TASK, w.PROJECT
OTHER_PROJECT, OTHER_TASK = t.OTHER_PROJECT, t.OTHER_TASK
V1, V2, V3 = w.V1, w.V2, t.V3
R8_VIA = w.R8_VIA
PARENT = _uuid(1003)
ANN_FAKE = "ann@x.test"
BOB_FAKE = "bob@x.test"
STALE = "The task changed. Read it again."
WRITES_OFF = t.WRITES_OFF

_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text
redis_fake = t.redis_fake


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free: a fake gateway with the PUT and the move
# ═══════════════════════════════════════════════════════════════════════════


class _Gateway(t._Gateway):
    """The EA-6d fake, with the assignee PUT, the move and a parent task."""

    def _answer(self, r: SimpleNamespace) -> tuple[int, Any]:
        if (r.method, r.path) in self.answers:
            return self.answers[(r.method, r.path)]
        if r.method == "PUT" and r.path == f"/projects/tasks/{TASK}/assignees":
            sent = json.loads(r.body)
            self.task["assignees"] = sorted(sent["assignees"])
            self.task["updated_at"] = V3
            return 200, {"task_id": TASK, "assignees": sorted(sent["assignees"]),
                         "not_notified": []}
        if r.method == "POST" and r.path == f"/projects/tasks/{TASK}/move":
            sent = json.loads(r.body)
            self.task["parent_task_id"] = sent["parent_task_id"]
            answer = {**self.task, "updated_at": V2}
            answer.pop("assignees")
            self.task["updated_at"] = V3
            return 200, answer
        if r.method == "GET" and r.path == f"/projects/tasks/{PARENT}":
            return 200, {"id": PARENT, "project_id": PROJECT, "updated_at": V1}
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


def _assign_args(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"task_id": TASK, "assignees": [ANN_FAKE],
                            "expected_version": V1}
    args.update(kw)
    return args


async def _listed(name: str) -> dict[str, Any]:
    r = await _post(_rpc("tools/list"))
    return {x["name"]: x for x in r.json()["result"]["tools"]}[name]


class TestTheToolRecords:
    """§9.2 and §9.5: the records in ``tools/list``, database-free."""

    async def test_set_assignees_is_listed_with_the_hints_of_a_write(self, edge):
        """Fix round 1: ``destructiveHint`` is true, because an empty list
        removes every assignee."""
        listed = await _listed("set_assignees")
        assert listed["annotations"] == {
            "title": "Set the assignees of a task", "readOnlyHint": False,
            "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}
        schema = listed["inputSchema"]
        assert schema["required"] == ["task_id", "assignees", "expected_version"]
        assert schema["additionalProperties"] is False
        assignees = schema["properties"]["assignees"]
        assert assignees["type"] == "array" and assignees["maxItems"] == tools.MAX_ASSIGNEES
        assert assignees["items"]["pattern"] == tools._ASSIGNEE_PATTERN
        assert set(listed["outputSchema"]["properties"]) == set(tools.SET_ASSIGNEES_KEYS)

    async def test_create_and_update_list_the_planning_fields_with_their_limits(self, edge):
        create = (await _listed("create_task"))["inputSchema"]["properties"]
        update = (await _listed("update_task"))["inputSchema"]["properties"]
        for props in (create, update):
            assert props["importance"] == {**tools._IMPORTANCE_INPUT, "enum": [0, 2]}
            assert props["leveraged"]["type"] == "boolean"
            assert (props["estimate_mins"]["minimum"], props["estimate_mins"]["maximum"]) == (
                0, 2_147_483_647)
            assert props["start_date"]["pattern"] == "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
        assert create["assignees"]["items"]["pattern"] == tools._ASSIGNEE_PATTERN
        assert "assignees" not in update
        assert update["parent_task_id"]["type"] == ["string", "null"]
        assert "parent_task_id" in create  # the subtask input of EA-6d

    def test_the_edge_copies_equal_their_gateway_sources(self):
        from acb_common.priority import IMPORTANT_AT
        from gateway.routes.projects import tasks as gw

        assert tools.ASSIGNEE_REFUSED_CODE == gw.AGENT_ASSIGNEE_REFUSED
        assert tools.ASSIGNEE_PERSONAL_CODE == gw.AGENT_ASSIGNEE_PERSONAL
        assert tools.MAX_ASSIGNEES == gw.AGENT_MAX_ASSIGNEES == 50
        assert set(tools.IMPORTANCE_VALUES) == set(gw.AGENT_IMPORTANCE_VALUES) == {0, IMPORTANT_AT}
        assert tools.MAX_ESTIMATE_MINS == gw.AGENT_MAX_ESTIMATE_MINS
        assert set(tools.CREATE_BODY_KEYS) == set(gw.AGENT_CREATE_FIELDS)
        assert set(tools.UPDATE_BODY_KEYS) | {"status_id"} == set(gw.AGENT_PATCH_FIELDS)
        assert {"parent_task_id"} == set(gw.AGENT_MOVE_FIELDS)

    def test_the_descriptions_teach_the_rules(self):
        assign = tools.SET_ASSIGNEES.description
        assert "Replace the whole assignee set" in assign and "agent:<name>" in assign
        assert "already open" in assign and "anybody new" in assign
        assert "a comment too" in assign and "release_task" in assign
        assert "An empty list removes every assignee" in assign
        assert "expected_version" in assign and "untrusted_text" in assign
        update = tools.UPDATE_TASK.description
        assert "parent_task_id" in update and "set_assignees" in update
        assert "Metorite app" in update and "2 for Important" in update
        create = tools.CREATE_TASK.description
        assert "assignees" in create and "no task is made" in create
        assert "anybody new" in create
        # Fix round 1: no text claims that the host always asks first.
        for described in (assign, create):
            assert "asks the person" not in described and "active member" not in described

    @pytest.mark.parametrize("name,args", [
        ("set_assignees", _assign_args()),
        ("update_task", {"task_id": TASK, "expected_version": V1, "parent_task_id": PARENT})])
    async def test_check_8_refuses_without_projects_write(self, edge, name, args):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call(name, args))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    async def test_with_the_writes_flag_off_set_assignees_sends_nothing(self, edge, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        listed = (await _post(_rpc("tools/list"))).json()["result"]["tools"]
        assert "set_assignees" not in {x["name"] for x in listed}
        assert _text(await _tool("set_assignees", _assign_args())) == WRITES_OFF
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("name,args", [
        ("set_assignees", _assign_args(notify=True)),
        ("set_assignees", _assign_args(project_id=PROJECT)),
        ("update_task", {"task_id": TASK, "expected_version": V1, "assignees": [ANN_FAKE]}),
        ("create_task", {"project_id": PROJECT, "title": "x", "custom_fields": {}}),
    ])
    async def test_an_unknown_field_is_refused(self, edge, name, args):
        extra = next(k for k in args if k not in tools.TOOLS[name].input_schema["properties"])
        assert _text(await _tool(name, args)) == f"The input {extra!r} is not known."
        assert edge.gateway.requests == []


class TestTheInputLimits:
    """The limits of §9.2. Each refusal comes before any request."""

    @pytest.mark.parametrize("field,value,text", [
        ("importance", 1, "The input 'importance' must be one of 0, 2."),
        ("importance", 3, "The input 'importance' must be one of 0, 2."),
        ("importance", 4, "The input 'importance' must be one of 0, 2."),
        ("importance", True, "The input 'importance' must be a integer."),
        ("estimate_mins", -1, "The input 'estimate_mins' must be at least 0."),
        ("estimate_mins", 2_147_483_648,
         "The input 'estimate_mins' must be at most 2147483647."),
        ("leveraged", "yes", "The input 'leveraged' must be a boolean."),
        ("start_date", "2026-1-5", "The input 'start_date' has the wrong form."),
        ("start_date", "2026-02-30", tools.TEXT_START_DATE),
        ("start_date", "2026-13-01", tools.TEXT_START_DATE),
    ])
    @pytest.mark.parametrize("tool", ["create_task", "update_task"])
    async def test_the_planning_limits_are_refused_with_no_request(
            self, edge, tool, field, value, text):
        base = ({"project_id": PROJECT, "title": "x"} if tool == "create_task"
                else {"task_id": TASK, "expected_version": V1})
        assert _text(await _tool(tool, {**base, field: value})) == text
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("assignees,text", [
        (["not an address"], "The input 'assignees[0]' has the wrong form."),
        (["agent:"], "The input 'assignees[0]' has the wrong form."),
        (["agent:-x"], "The input 'assignees[0]' has the wrong form."),
        ([ANN_FAKE, "a@b"], "The input 'assignees[1]' has the wrong form."),
        ([f"p{n}@x.test" for n in range(51)], "The input 'assignees' takes at most 50 items."),
        ([None], "The input 'assignees' holds an item that is not valid."),
    ])
    async def test_an_assignee_of_the_wrong_shape_is_refused(self, edge, assignees, text):
        assert _text(await _tool("set_assignees", _assign_args(assignees=assignees))) == text
        assert _text(await _tool("create_task", {
            "project_id": PROJECT, "title": "x", "assignees": assignees})) == text
        assert edge.gateway.requests == []

    async def test_the_assignee_set_is_required_and_may_be_empty(self, edge):
        args = _assign_args()
        del args["assignees"]
        assert _text(await _tool("set_assignees", args)) == "The input 'assignees' is required."
        assert edge.gateway.requests == []
        result = await _tool("set_assignees", _assign_args(assignees=[]))
        assert result["isError"] is False, result
        assert json.loads(_writes(edge.gateway)[0].body) == {"assignees": []}


class TestCreateTaskFields:
    """The create body, database-free."""

    async def test_it_posts_the_planning_fields_and_the_assignees(self, edge):
        result = await _tool("create_task", {
            "project_id": PROJECT, "title": "Plan", "importance": 2, "leveraged": True,
            "estimate_mins": 45, "start_date": "2026-11-02",
            "assignees": [ANN_FAKE, "agent:builder"]})
        assert result["isError"] is False, result
        (post,) = _writes(edge.gateway)
        assert json.loads(post.body) == {
            "project_id": PROJECT, "title": "Plan", "importance": 2, "leveraged": True,
            "estimate_mins": 45, "start_date": "2026-11-02",
            "assignees": [ANN_FAKE, "agent:builder"]}

    async def test_create_names_the_refused_entries(self, edge):
        """The text names only entries of the call's own list. A name that the
        gateway adds never reaches the agent."""
        edge.gateway.answers[("POST", "/projects/tasks")] = (422, {"detail": {
            "error": tools.ASSIGNEE_REFUSED_CODE,
            "assignees": ["eve@x.test", "planted@evil.test"], "message": "m"}})
        result = await _tool("create_task", {
            "project_id": PROJECT, "title": "x", "assignees": [ANN_FAKE, "Eve@x.test"],
            "idempotency_key": "g2-refused-key-1"})
        assert _text(result) == tools.TEXT_ASSIGNEES_REFUSED.format(names="eve@x.test")
        # A refusal before any row frees the key (EA-6d).
        assert edge.redis.slots() == {}

    async def test_create_in_a_personal_project_gets_the_personal_text(self, edge):
        """Fix round 1 (P3): not the bad-request text."""
        edge.gateway.answers[("POST", "/projects/tasks")] = (422, {"detail": {
            "error": tools.ASSIGNEE_PERSONAL_CODE, "message": "m"}})
        result = await _tool("create_task", {
            "project_id": PROJECT, "title": "x", "assignees": [ANN_FAKE]})
        assert _text(result) == tools.TEXT_ASSIGNEES_PERSONAL


class TestUpdateTaskFields:
    """The PATCH and the move of ``update_task``, database-free."""

    async def test_it_patches_the_planning_fields(self, edge):
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "importance": 0, "leveraged": False,
            "estimate_mins": 0, "start_date": "2026-11-02"})
        assert result["isError"] is False, result
        (patch,) = _writes(edge.gateway)
        assert (patch.method, patch.path) == ("PATCH", f"/projects/tasks/{TASK}")
        assert json.loads(patch.body) == {"importance": 0, "leveraged": False,
                                          "estimate_mins": 0, "start_date": "2026-11-02"}
        assert patch.headers["if-match"] == V1

    async def test_parent_task_id_is_a_move_with_if_match(self, edge):
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": PARENT})
        assert result["isError"] is False, result
        assert [(r.method, r.path) for r in edge.gateway.requests][:2] == [
            ("GET", f"/projects/tasks/{PARENT}"), ("POST", f"/projects/tasks/{TASK}/move")]
        (move,) = _writes(edge.gateway)
        assert json.loads(move.body) == {"parent_task_id": PARENT}
        assert move.headers["if-match"] == V1
        task = result["structuredContent"]["task"]
        assert task["parent_task_id"] == PARENT and task["version"] == V3
        assert "assignees" not in task

    async def test_null_moves_the_task_to_the_top_level(self, edge):
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": None})
        assert result["isError"] is False, result
        (move,) = _writes(edge.gateway)
        assert json.loads(move.body) == {"parent_task_id": None}
        # A null parent reads no parent first.
        assert all(r.path != "/projects/tasks/None" for r in edge.gateway.requests)

    async def test_parent_and_fields_together_are_refused(self, edge):
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": PARENT, "title": "x"})
        assert _text(result) == tools.TEXT_MOVE_ALONE
        assert edge.gateway.requests == []

    async def test_a_hidden_parent_is_not_found_and_sends_no_move(self, edge):
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": _uuid(4040)})
        assert _text(result) == tools.TEXT_PARENT_NOT_FOUND
        assert _writes(edge.gateway) == []

    @pytest.mark.parametrize("detail,text", [
        ("The parent task is in another project.", tools.TEXT_MOVE_OTHER_PROJECT),
        ("A task cannot be its own parent.", tools.TEXT_MOVE_OWN_PARENT),
        ("That move would put the task inside its own subtree.", tools.TEXT_MOVE_CYCLE),
        ("Task hierarchy is deeper than the supported maximum.", tools.TEXT_MOVE_TOO_DEEP),
        ("An Epic cannot have a parent task; it is the top level.", tools.TEXT_MOVE_EPIC),
        ("Something else.", tools.TEXT_BAD_REQUEST),
    ])
    async def test_each_refusal_of_a_move_has_its_text(self, edge, detail, text):
        edge.gateway.answers[("POST", f"/projects/tasks/{TASK}/move")] = (
            422, {"detail": detail})
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": PARENT})
        assert _text(result) == text

    async def test_a_stale_move_is_the_stale_text(self, edge):
        edge.gateway.answers[("POST", f"/projects/tasks/{TASK}/move")] = (412, {"detail": {}})
        result = await _tool("update_task", {
            "task_id": TASK, "expected_version": V1, "parent_task_id": PARENT})
        assert _text(result) == STALE


class TestSetAssigneesAtTheEdge:
    """``set_assignees`` against the fake gateway."""

    async def test_it_puts_the_whole_set_with_if_match(self, edge):
        result = await _tool("set_assignees", _assign_args(assignees=[BOB_FAKE, ANN_FAKE]))
        assert result["isError"] is False, result
        (put,) = _writes(edge.gateway)
        assert (put.method, put.path) == ("PUT", f"/projects/tasks/{TASK}/assignees")
        assert json.loads(put.body) == {"assignees": [BOB_FAKE, ANN_FAKE]}
        assert put.headers["if-match"] == V1
        assert result["structuredContent"] == {
            "id": TASK, "assignees": [ANN_FAKE, BOB_FAKE], "version": V3}

    async def test_a_failed_re_read_gives_the_put_set_and_no_version(self, edge):
        edge.gateway.answers[("GET", f"/projects/tasks/{TASK}")] = (503, {})
        result = await _tool("set_assignees", _assign_args())
        assert result["structuredContent"] == {
            "id": TASK, "assignees": [ANN_FAKE], "version": None}

    async def test_a_refused_entry_is_named_and_only_from_the_call(self, edge):
        edge.gateway.answers[("PUT", f"/projects/tasks/{TASK}/assignees")] = (422, {
            "detail": {"error": tools.ASSIGNEE_REFUSED_CODE,
                       "assignees": [BOB_FAKE, "planted@evil.test"], "message": "m"}})
        result = await _tool("set_assignees", _assign_args(assignees=[ANN_FAKE, BOB_FAKE]))
        assert _text(result) == tools.TEXT_ASSIGNEES_REFUSED.format(names=BOB_FAKE)

    async def test_a_refusal_that_names_nothing_of_the_call_names_nobody(self, edge):
        edge.gateway.answers[("PUT", f"/projects/tasks/{TASK}/assignees")] = (422, {
            "detail": {"error": tools.ASSIGNEE_REFUSED_CODE, "assignees": ["x@y.test"]}})
        assert _text(await _tool("set_assignees", _assign_args())) == (
            tools.TEXT_ASSIGNEES_REFUSED.format(names="one or more of these people"))

    @pytest.mark.parametrize("status,body,text", [
        (422, {"detail": {"error": tools.ASSIGNEE_PERSONAL_CODE, "message": "m"}},
         tools.TEXT_ASSIGNEES_PERSONAL),
        (422, {"detail": "Some other refusal."}, tools.TEXT_BAD_REQUEST),
        (412, {"detail": {}}, STALE),
        (404, {"detail": "Task not found"}, tools.TEXT_TASK_NOT_FOUND),
        (403, {"detail": "no"}, tools.TEXT_WRITE_FORBIDDEN),
    ])
    async def test_the_other_answers(self, edge, status, body, text):
        edge.gateway.answers[("PUT", f"/projects/tasks/{TASK}/assignees")] = (status, body)
        assert _text(await _tool("set_assignees", _assign_args())) == text


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpassign"
_APP_PW = "mcpassign_scratch_pw"
MEMBER = w.MEMBER
ANN = "ann@mcpwrites.test"
BOB = "bob@mcpwrites.test"
CAROL = "carol@mcpwrites.test"  # a grant on the team project only
GRETA = "greta@mcpwrites.test"  # reaches a project through a group grant only
OLGA = "olga@mcpwrites.test"    # reaches a project through an org grant only
ADA = "ada@mcpwrites.test"      # data:org:read, and no grant at all
SUE = "sue@mcpwrites.test"      # a grant on a parent project only
GROUP_SLUG = "g2crew"
SAM = "sam@mcpwrites.test"     # a suspended member of this organization
EVE = "eve@mcpwrites.test"     # an active member of ANOTHER organization
GHOST = "ghost@mcpwrites.test"  # no member at all


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two tenants. The first holds the member of
    the connection, three more members, a suspended one, two team projects
    and a personal project of the member.

    Ann, Bob and Carol hold a grant on the team project, and none on the
    other project. So an agent may assign them on a team task, and may not
    assign them on a task of the other project (fix round 1)."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpassign"
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
        for oid, slug in ((org, "mcpassign"), (org2, "mcpassign2")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.number = 100_000
    for who in (MEMBER, ANN, BOB, CAROL, SAM, GRETA, OLGA, SUE):
        a.member(who, ["feature:projects"])
    a.member(ADA, ["feature:projects", "data:org:read"])
    a.one("UPDATE app_user SET status = 'suspended' WHERE email = :e RETURNING id", e=SAM)
    w._Seeder(eng, org2).member(EVE, ["feature:projects"])
    team, other = a.project("Team"), a.project("Other team")
    for who in (ANN, BOB, CAROL):
        a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
              " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
              " RETURNING project_id", p=team, s=who, a=w.OWNER, o=org)
    # Fix round 2: one project for each other way in (P3-1).
    group = a.one("INSERT INTO org_group (organization_id, slug, display_name)"
                  " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id", o=org, s=GROUP_SLUG)
    a.one("INSERT INTO org_group_member (group_id, user_id, organization_id)"
          " SELECT CAST(:g AS uuid), id, CAST(:o AS uuid) FROM app_user WHERE email = :e"
          " RETURNING group_id", g=str(group), o=org, e=GRETA)
    grouped, everyone = a.project("Grouped"), a.project("Everyone")
    parent, child = a.project("Parent"), a.project("Child")
    a.one("UPDATE pm_projects SET parent_project_id = CAST(:p AS uuid)"
          " WHERE id = CAST(:c AS uuid) RETURNING id", p=parent, c=child)
    for project, subject in ((grouped, f"group:{GROUP_SLUG}"), (everyone, "org"),
                             (parent, SUE)):
        a.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
              " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
              " RETURNING project_id", p=project, s=subject, a=w.OWNER, o=org)
    mine = a.project("Mine")
    a.one("UPDATE pm_projects SET personal_owner = :m WHERE id = CAST(:p AS uuid)"
          " RETURNING id", m=MEMBER, p=mine)
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
        other=a.status(other, "To do", "todo", 0),
        mine=a.status(mine, "To do", "todo", 0),
        grouped=a.status(grouped, "To do", "todo", 0),
        everyone=a.status(everyone, "To do", "todo", 0),
        child=a.status(child, "To do", "todo", 0),
    )
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, other=other,
                             mine=mine, grouped=grouped, everyone=everyone, child=child,
                             s=s, c={}, tok={}, task=a.task)
    for name, scopes in (("write", w.WRITE_SCOPES), ("read", ("projects:read",))):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


live_app = t.live_app
live = t.live
_mcp, _send, _row, _in_scope = t._mcp, t._send, t._row, t._in_scope


def _scalar(world, sql: str, **params: Any) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(sql), params).scalar()


def _assignees(world, task: str) -> list[str]:
    with world.admin.begin() as c:
        return sorted(r[0] for r in c.execute(text(
            "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)"),
            {"t": task}))


def _bells(world, task: str, who: str) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT actor, excerpt FROM pm_notifications WHERE task_id = CAST(:t AS uuid)"
            " AND recipient = :r AND kind = 'assigned'"), {"t": task, "r": who}))


def _assign(world, task: str, who: str) -> None:
    world.seeder.one(
        "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, organization_id)"
        " VALUES (CAST(:t AS uuid), :w, :b, CAST(:o AS uuid)) RETURNING task_id",
        t=task, w=who, b=w.OWNER, o=world.org)


async def _version(world, task: str) -> str:
    read = await _mcp(world, "write", "get_task", {"task_id": task})
    return read["structuredContent"]["task"]["version"]


def _via_label() -> str:
    return f"via {agent_oauth.client_label(R8_VIA)}"


class TestTheRulesOnARealDatabase:
    """§15 MCP-G2, through the real gateway app."""

    pytestmark = _DB_GATE

    async def test_r1_create_with_an_assignee_and_the_planning_fields(self, live):
        result = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "g2 planned", "importance": 2,
            "leveraged": True, "estimate_mins": 45, "start_date": "2026-11-02",
            "assignees": [ANN]})
        assert result["isError"] is False, result
        task = result["structuredContent"]["task"]
        row = _row(live, task["id"])
        assert (row.importance, row.leveraged, row.estimate_mins, str(row.start_date)) == (
            2, True, 45, "2026-11-02")
        assert row.source == "mcp" and _assignees(live, task["id"]) == [ANN]
        assert task["assignees"] == [ANN]
        # Exactly one bell for Ann. It names the member and the client.
        bells = _bells(live, task["id"], ANN)
        assert [(b.actor, b.excerpt) for b in bells] == [(MEMBER, _via_label())]
        assert _via_label() == "via client.example"
        # The timeline names the client too, through `meta.via`.
        assert _scalar(live, "SELECT meta->>'via' FROM pm_activities WHERE task_id ="
                       " CAST(:t AS uuid) AND type = 'assignment'", t=task["id"]) == R8_VIA

    async def test_r2_one_bell_for_the_added_person_only(self, live):
        task = live.task(live.team, live.s.todo, "g2 swap")
        _assign(live, task, ANN)
        result = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [BOB], "expected_version": await _version(live, task)})
        assert result["isError"] is False, result
        assert result["structuredContent"]["assignees"] == [BOB]
        assert _assignees(live, task) == [BOB]
        assert [(b.actor, b.excerpt) for b in _bells(live, task, BOB)] == [
            (MEMBER, _via_label())]
        assert _bells(live, task, ANN) == []
        meta = _scalar(live, "SELECT meta FROM pm_activities WHERE task_id ="
                       " CAST(:t AS uuid) AND type = 'assignment'", t=task)
        assert (meta["added"], meta["removed"], meta["via"]) == ([BOB], [ANN], R8_VIA)
        # The version that the tool returned works as the next one.
        again = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [BOB, ANN],
            "expected_version": result["structuredContent"]["version"]})
        assert again["isError"] is False, again
        assert len(_bells(live, task, BOB)) == 1  # a re-assert pings nobody

    async def test_r3_a_hidden_member_and_a_made_up_address_get_one_text(self, live):
        task = live.task(live.team, live.s.todo, "g2 hidden")
        texts = {}
        for who in (EVE, GHOST, SAM):
            refused = await _mcp(live, "write", "set_assignees", {
                "task_id": task, "assignees": [ANN, who],
                "expected_version": await _version(live, task)})
            texts[who] = _text(refused)
        # One text, which differs only in the address the caller sent.
        assert {who: txt.replace(who, "<who>") for who, txt in texts.items()} == {
            who: tools.TEXT_ASSIGNEES_REFUSED.format(names="<who>") for who in texts}
        # Nothing changed: no assignee and no bell, Ann's neither.
        assert _assignees(live, task) == []
        assert _scalar(live, "SELECT count(*) FROM pm_notifications WHERE task_id ="
                       " CAST(:t AS uuid)", t=task) == 0
        # A create with a hidden member makes no task.
        made = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "g2 never made", "assignees": [EVE]})
        assert _text(made) == tools.TEXT_ASSIGNEES_REFUSED.format(names=EVE)
        assert t._count(live, "g2 never made") == 0

    async def test_r4_a_stale_version_is_refused_for_set_assignees(self, live):
        task = live.task(live.team, live.s.todo, "g2 stale put")
        old = await _version(live, task)
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", signed=None,
                        body={"title": "g2 stale put, edited"})
        assert r.status_code == 200, r.text
        stale = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [ANN], "expected_version": old})
        assert _text(stale) == STALE
        assert _assignees(live, task) == [] and _bells(live, task, ANN) == []

    async def test_r5_a_stale_version_is_refused_for_the_move(self, live):
        parent = live.task(live.team, live.s.todo, "g2 parent")
        task = live.task(live.team, live.s.todo, "g2 stale move")
        old = await _version(live, task)
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", signed=None,
                        body={"title": "g2 stale move, edited"})
        assert r.status_code == 200, r.text
        stale = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": old, "parent_task_id": parent})
        assert _text(stale) == STALE
        assert _row(live, task).parent_task_id is None

    async def test_r6_a_move_to_another_project_is_refused(self, live):
        task = live.task(live.team, live.s.todo, "g2 stay home")
        foreign = live.task(live.other, live.s.other, "g2 foreign parent")
        refused = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": await _version(live, task),
            "parent_task_id": foreign})
        assert _text(refused) == tools.TEXT_MOVE_OTHER_PROJECT
        # The gateway's own copy, for a signed write that skips the edge.
        for body in ({"project_id": live.other}, {"parent_task_id": foreign},
                     {"parent_task_id": None, "include_subtasks": False}):
            r = await _send(live, "POST", f"/projects/tasks/{task}/move", body=body)
            assert r.status_code == 422, (body, r.text)
        row = _row(live, task)
        assert (str(row.project_id), row.parent_task_id) == (live.team, None)

    async def test_r7_a_cycle_is_refused(self, live):
        top = live.task(live.team, live.s.todo, "g2 top")
        child = live.task(live.team, live.s.todo, "g2 child")
        moved = await _mcp(live, "write", "update_task", {
            "task_id": child, "expected_version": await _version(live, child),
            "parent_task_id": top})
        assert moved["isError"] is False, moved
        cycle = await _mcp(live, "write", "update_task", {
            "task_id": top, "expected_version": await _version(live, top),
            "parent_task_id": child})
        assert _text(cycle) == tools.TEXT_MOVE_CYCLE
        own = await _mcp(live, "write", "update_task", {
            "task_id": top, "expected_version": await _version(live, top),
            "parent_task_id": top})
        assert _text(own) == tools.TEXT_MOVE_OWN_PARENT
        assert _row(live, top).parent_task_id is None

    async def test_r8_a_move_lands_and_null_goes_to_the_top_level(self, live):
        parent = live.task(live.team, live.s.todo, "g2 new parent")
        task = live.task(live.team, live.s.todo, "g2 moving")
        moved = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": await _version(live, task),
            "parent_task_id": parent})
        assert moved["isError"] is False, moved
        assert str(_row(live, task).parent_task_id) == parent
        assert moved["structuredContent"]["task"]["parent_task_id"] == parent
        assert _scalar(live, "SELECT meta->>'via' FROM pm_activities WHERE task_id ="
                       " CAST(:t AS uuid) AND body = 'Task moved'", t=task) == R8_VIA
        top = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": moved["structuredContent"]["task"]["version"],
            "parent_task_id": None})
        assert top["isError"] is False, top
        assert _row(live, task).parent_task_id is None

    async def test_r9_a_connection_without_the_write_scope_is_refused(self, live):
        from agent_edge import app as edge_app_module

        task = live.task(live.team, live.s.todo, "g2 read only")

        async def call() -> httpx.Response:
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                    base_url=w.ISSUER) as client:
                return await client.post(
                    "/mcp", content=json.dumps(_call("set_assignees", {
                        "task_id": task, "assignees": [ANN], "expected_version": V1})),
                    headers={"authorization": f"Bearer {live.tok['read']}",
                             "content-type": "application/json",
                             "mcp-protocol-version": "2025-11-25"})

        r = await _in_scope(live, call)
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        # Branch 0 refuses a signed write of a read connection, the second guard.
        for method, path, body in (
                ("PUT", f"/projects/tasks/{task}/assignees", {"assignees": [ANN]}),
                ("POST", f"/projects/tasks/{task}/move", {"parent_task_id": None})):
            refused = await _send(live, method, path, body=body, signed="read")
            assert refused.status_code == 401, refused.text
        assert _assignees(live, task) == []

    async def test_r10_a_members_put_is_not_narrowed(self, live):
        """The app's own calls send no ``If-Match`` and keep the app's rules:
        an address that no member holds is assigned and not notified, and a
        member may delegate outward. A member's ``If-Match`` follows
        ``precondition_holds``: an assignment does not stale it, and a field
        change does (fix round 1)."""
        task = live.task(live.other, live.s.other, "g2 member put")
        old = await _version(live, task)
        r = await _send(live, "PUT", f"/projects/tasks/{task}/assignees", signed=None,
                        body={"assignees": [GHOST, ANN]})
        assert r.status_code == 200, r.text
        # Ann holds no grant on this project: the assignee arm opens it to her.
        assert r.json()["not_notified"] == [GHOST]
        assert _assignees(live, task) == [ANN, GHOST]
        assert [b.excerpt for b in _bells(live, task, ANN)] == [None]
        held = await _send(live, "PUT", f"/projects/tasks/{task}/assignees", signed=None,
                           body={"assignees": [GHOST, ANN, BOB]}, headers={"If-Match": old})
        assert held.status_code == 200, held.text
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", signed=None,
                        body={"title": "g2 member put, edited"})
        assert r.status_code == 200, r.text
        stale = await _send(live, "PUT", f"/projects/tasks/{task}/assignees", signed=None,
                            body={"assignees": []}, headers={"If-Match": old})
        assert stale.status_code == 412, stale.text
        assert _assignees(live, task) == [ANN, BOB, GHOST]
        moved = await _send(live, "POST", f"/projects/tasks/{task}/move", signed=None,
                            body={"parent_task_id": None})
        assert moved.status_code == 200, moved.text

    async def test_r11_an_agent_assignee_follows_the_app(self, live):
        task = live.task(live.team, live.s.todo, "g2 to an agent")
        result = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": ["agent:builder"],
            "expected_version": await _version(live, task)})
        assert result["isError"] is False, result
        assert _assignees(live, task) == ["agent:builder"]
        # Rule 2 of `notifications.py`: an agent gets no bell.
        assert _scalar(live, "SELECT count(*) FROM pm_notifications WHERE task_id ="
                       " CAST(:t AS uuid)", t=task) == 0

    async def test_r12_removing_the_caller_does_not_end_its_run(self, live, monkeypatch):
        """§9.2: the hold of a task is its agent run, not the assignee row."""
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        task = live.task(live.team, live.s.todo, "g2 held")
        claimed = await _mcp(live, "write", "claim_task", {
            "task_id": task, "expected_version": await _version(live, task)})
        assert claimed["isError"] is False, claimed
        run = claimed["structuredContent"]["agent_run_id"]
        assert run is not None
        status = "SELECT status FROM pm_agent_runs WHERE id = CAST(:r AS uuid)"
        before = _scalar(live, status, r=run)
        result = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [],
            "expected_version": claimed["structuredContent"]["task"]["version"]})
        assert result["isError"] is False, result
        assert _assignees(live, task) == []
        assert _scalar(live, status, r=run) == before

    async def test_r13_an_agent_cannot_widen_who_sees_a_task(self, live):
        """Fix round 1 (P1, EA3-52). Carol is an active member with no grant
        on the other project. An agent cannot assign her there, because the
        assignment would open the task to her. The text is the text of a
        made-up address. On a project she can open, the same call lands."""
        hidden = live.task(live.other, live.s.other, "g2 restricted")
        refused = await _mcp(live, "write", "set_assignees", {
            "task_id": hidden, "assignees": [CAROL],
            "expected_version": await _version(live, hidden)})
        assert _text(refused) == tools.TEXT_ASSIGNEES_REFUSED.format(names=CAROL)
        ghost = await _mcp(live, "write", "set_assignees", {
            "task_id": hidden, "assignees": [GHOST],
            "expected_version": await _version(live, hidden)})
        assert _text(ghost).replace(GHOST, CAROL) == _text(refused)
        assert _assignees(live, hidden) == [] and _bells(live, hidden, CAROL) == []
        made = await _mcp(live, "write", "create_task", {
            "project_id": live.other, "title": "g2 restricted create", "assignees": [CAROL]})
        assert _text(made) == tools.TEXT_ASSIGNEES_REFUSED.format(names=CAROL)
        assert t._count(live, "g2 restricted create") == 0
        # The same person on a task that she can open already.
        open_task = live.task(live.team, live.s.todo, "g2 open to carol")
        landed = await _mcp(live, "write", "set_assignees", {
            "task_id": open_task, "assignees": [CAROL],
            "expected_version": await _version(live, open_task)})
        assert landed["isError"] is False, landed
        assert [(b.actor, b.excerpt) for b in _bells(live, open_task, CAROL)] == [
            (MEMBER, _via_label())]

    async def test_r14_an_agent_put_keeps_strict_equality(self, live):
        """Fix round 1 (P2). An assignment does not move ``fields_at``, so
        ``precondition_holds`` would pass the agent's old version, and the
        replaced set would drop Bob in silence. Any change after the read,
        a comment too, refuses the agent's PUT."""
        task = live.task(live.team, live.s.todo, "g2 strict put")
        old = await _version(live, task)
        r = await _send(live, "PUT", f"/projects/tasks/{task}/assignees", signed=None,
                        body={"assignees": [BOB]})
        assert r.status_code == 200, r.text
        stale = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [MEMBER, CAROL], "expected_version": old})
        assert _text(stale) == STALE
        assert _assignees(live, task) == [BOB]
        # A comment stales the version too.
        fresh = await _version(live, task)
        c = await _send(live, "POST", f"/projects/tasks/{task}/comments", signed=None,
                        body={"body": "a note"})
        assert c.status_code in (200, 201), c.text
        again = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [BOB, CAROL], "expected_version": fresh})
        assert _text(again) == STALE
        # Read again and resend the full set: it lands.
        done = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [BOB, CAROL],
            "expected_version": await _version(live, task)})
        assert done["isError"] is False, done
        assert _assignees(live, task) == [BOB, CAROL]

    async def test_r15_the_gateway_caps_an_agent_at_50_assignees(self, live):
        """Fix round 1 (P3). The gateway's own copy of the edge's cap. The
        entries are agents, which the grant rule does not check, so only the
        cap can refuse them."""
        task = live.task(live.team, live.s.todo, "g2 capped")
        many = [f"agent:bot{n}" for n in range(51)]
        r = await _send(live, "PUT", f"/projects/tasks/{task}/assignees",
                        body={"assignees": many})
        assert r.status_code == 422, r.text
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": "g2 capped create", "assignees": many})
        assert r.status_code == 422, r.text
        assert _assignees(live, task) == [] and t._count(live, "g2 capped create") == 0

    async def test_r16_the_gateway_holds_the_estimate_ceiling(self, live):
        """Fix round 1 (P3, the verifier's mutation C). One more than the INT
        limit is a 422, never the 500 of an overflow. The limit itself lands."""
        top = 2_147_483_647
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": "g2 too long", "estimate_mins": top + 1})
        assert r.status_code == 422, r.text
        assert t._count(live, "g2 too long") == 0
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": "g2 longest", "estimate_mins": top})
        assert r.status_code == 201, r.text
        assert _row(live, r.json()["id"]).estimate_mins == top

    async def test_r17_a_person_who_stays_is_not_checked_or_notified(self, live):
        """Fix round 1 (P3, the verifier's mutation D). A member put Ghost (no
        member) and Bob on the task. The agent keeps both and adds Ann. Only
        the NEW person is checked and notified."""
        task = live.task(live.team, live.s.todo, "g2 stays")
        _assign(live, task, GHOST)
        _assign(live, task, BOB)
        result = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [GHOST, BOB, ANN],
            "expected_version": await _version(live, task)})
        assert result["isError"] is False, result
        assert _assignees(live, task) == [ANN, BOB, GHOST]
        assert len(_bells(live, task, ANN)) == 1
        assert _bells(live, task, BOB) == [] and _bells(live, task, GHOST) == []

    async def test_r18_a_personal_project_gets_its_own_text(self, live):
        """Fix round 1 (P3). A task in the member's personal project takes
        only its owner, for a create and for a PUT."""
        made = await _mcp(live, "write", "create_task", {
            "project_id": live.mine, "title": "g2 mine", "assignees": [ANN]})
        assert _text(made) == tools.TEXT_ASSIGNEES_PERSONAL
        assert t._count(live, "g2 mine") == 0
        mine = live.task(live.mine, live.s.mine, "g2 mine already")
        put = await _mcp(live, "write", "set_assignees", {
            "task_id": mine, "assignees": [ANN],
            "expected_version": await _version(live, mine)})
        assert _text(put) == tools.TEXT_ASSIGNEES_PERSONAL
        assert _assignees(live, mine) == []

    async def test_r19_the_grant_only_seam_leaves_out_the_assignee_arm(self, live):
        """Fix round 1 (P1). ``notifications.grant_holders`` is the seam of
        the agent's rule. Ann is an assignee of a task in a project where she
        holds no grant: ``deliverable`` counts her, and the grant arm does
        not."""
        from gateway.routes.projects import core
        from gateway.routes.projects.notifications import deliverable, grant_holders

        task = live.task(live.other, live.s.other, "g2 seam")
        _assign(live, task, ANN)

        async def both() -> tuple[list[str], list[str]]:
            async with core._tenant_session(live.org) as db:
                return (await deliverable(db, [ANN, MEMBER], task),
                        await grant_holders(db, [ANN, MEMBER], task))

        default, grants = await _in_scope(live, both)
        assert default == [ANN, MEMBER]
        assert grants == [MEMBER]


    async def test_r20_each_grant_path_lets_an_agent_assign(self, live):
        """Fix round 2 (P3-1, the verifier's mutation M2). The rule counts
        every way in that a grant gives, and the admin read right: a
        ``group:<slug>`` grant, an ``org`` grant, ``data:org:read`` with no
        grant, and a grant on a parent project. Each person here has exactly
        one of them. The control: none of the three without the admin right
        reaches the other project, so each one is refused there."""
        cases = (
            (live.grouped, live.s.grouped, GRETA),
            (live.everyone, live.s.everyone, OLGA),
            (live.other, live.s.other, ADA),
            (live.child, live.s.child, SUE),
        )
        for project, status, who in cases:
            task = live.task(project, status, f"g2 path {who}")
            result = await _mcp(live, "write", "set_assignees", {
                "task_id": task, "assignees": [who],
                "expected_version": await _version(live, task)})
            assert result["isError"] is False, (who, result)
            assert _assignees(live, task) == [who]
            assert len(_bells(live, task, who)) == 1, who
        hidden = live.task(live.other, live.s.other, "g2 path control")
        refused = await _mcp(live, "write", "set_assignees", {
            "task_id": hidden, "assignees": [GRETA, OLGA, SUE],
            "expected_version": await _version(live, hidden)})
        assert _text(refused) == tools.TEXT_ASSIGNEES_REFUSED.format(
            names=", ".join(sorted([GRETA, OLGA, SUE])))
        assert _assignees(live, hidden) == []

    async def test_r21_a_row_in_another_case_is_the_same_person(self, live):
        """Fix round 2 (P3-2). A legacy row, or a copy that ``recurrence.py``
        made as it found it, can hold an address in another case. A re-send
        of the same set keeps that person: no check, no bell, and the row
        stays as it is. Before the fix the address read as a NEW person and
        the agent got the refusal."""
        task = live.task(live.team, live.s.todo, "g2 legacy case")
        legacy = "Ghost@McpWrites.Test"
        _assign(live, task, legacy)
        result = await _mcp(live, "write", "set_assignees", {
            "task_id": task, "assignees": [GHOST, BOB],
            "expected_version": await _version(live, task)})
        assert result["isError"] is False, result
        assert sorted(_assignees(live, task)) == sorted([legacy, BOB])
        assert _bells(live, task, GHOST) == [] and len(_bells(live, task, BOB)) == 1
        meta = _scalar(live, "SELECT meta FROM pm_activities WHERE task_id ="
                       " CAST(:t AS uuid) AND type = 'assignment'", t=task)
        assert (meta["added"], meta["removed"]) == ([BOB], [])
