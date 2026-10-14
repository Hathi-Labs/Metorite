"""WS-55 EA-6d — ``create_task``, ``update_task`` and the task source ``mcp``.

Spec: ``project-docs/specs/external_agent_access.md`` §9.2 (the EA-6d rows and
their rules), §9.5 rule 4, §9.6 and §15 EA-6d.

Three halves, as in ``test_mcp_edge_writes.py``, whose helpers this file
reuses.

**Database-free (always runs).** The edge app runs in process with a fake
token store, a fake gateway and a fake Redis under the REAL ``TenantRedis``.
It proves the two tools: the body that each one builds, the refusals before a
write, the answer map, and the ``idempotency_key`` of ``create_task``. That
includes the race of two calls with one key, a refusal that frees the key, and
a POST with no answer that keeps it.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app. It proves that a
task an agent makes reads ``mcp``, that one key makes one row (also under a
race), the gateway's narrowing of a signed agent write, that a member cannot
send the source ``mcp``, and the widened CHECK of the EA-6d migration.

**A real Redis 7 (optional).** :class:`TestTheSlotOnARealRedis` runs the slot
of ``agent_edge.idempotency`` on a real server when ``AGENT_WRITES_REDIS_URL``
names one. CI has no Redis service, so it skips there. A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_task_writes.py tests/unit/test_mcp_edge_writes.py -v -rs
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import agent_signing
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis
from agent_edge import gateway_client, idempotency, tools
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_DB_GATE = w._DB_GATE
_REDIS_URL = os.environ.get("AGENT_WRITES_REDIS_URL", "").strip()
_REDIS_GATE = pytest.mark.skipif(
    not _REDIS_URL,
    reason=(
        "AGENT_WRITES_REDIS_URL unset — this class runs the idempotency slot on "
        "a REAL Redis 7. CI has no Redis service, so it skips there. A skip is "
        "not a pass."
    ),
)

_uuid = w._uuid
GOOD, KEY, ORG, CONN, VIA = w.GOOD, w.KEY, w.ORG, w.CONN, w.VIA
TASK, PROJECT = w.TASK, w.PROJECT
S_TODO, S_DOING, S_DONE, S_CANCELLED = w.S_TODO, w.S_DOING, w.S_DONE, w.S_CANCELLED
V1, V2 = w.V1, w.V2
V3 = "2026-10-14T00:00:02.000002+00:00"
TYPE = _uuid(8001)
NEW = _uuid(5001)
OTHER_PROJECT = _uuid(2)
OTHER_TASK = _uuid(1002)
IDEM = "retry-key-0001"
CHALLENGE_403_WRITE = w.CHALLENGE_403_WRITE
WRITES_OFF = "Changes are turned off for agents in this organization."


# ═══════════════════════════════════════════════════════════════════════════
# The Redis fake: the EA-6a fake, with GET and DEL for the idempotency slot
# ═══════════════════════════════════════════════════════════════════════════


class _FakeRedis(w._FakeRedis):
    """``SET NX EX``, ``GET``, ``DEL``, ``INCR``, ``EXPIRE`` and ``TTL``."""

    async def get(self, name: str) -> str | None:
        self._check("get", name)
        found = self._live(name)
        return found[0] if found else None

    async def delete(self, *names: str) -> int:
        gone = 0
        for name in names:
            self._check("delete", name)
            gone += int(self.store.pop(name, None) is not None)
        return gone

    def slots(self) -> dict[str, dict[str, Any]]:
        """Every idempotency slot, decoded."""
        return {k: json.loads(v[0]) for k, v in self.store.items()
                if f":{idempotency.NAMESPACE}:" in k}


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free: a fake gateway
# ═══════════════════════════════════════════════════════════════════════════


class _Gateway(w._Gateway):
    """The EA-6a fake gateway, with the create route and a second project."""

    def __init__(self) -> None:
        super().__init__()
        self.created: list[dict[str, Any]] = []
        #: When set, a POST of a task waits for it (the race test).
        self.hold: asyncio.Event | None = None
        self.posting = asyncio.Event()
        #: What a read of the new task adds, as if the links of its text
        #: touched it after the commit (fix round 1): a later version.
        self.after_create: dict[str, Any] = {"updated_at": V3}
        #: The task that `update_task` changes. A PATCH answers V2, and then
        #: the links of the text touch it again, so a read gives V3.
        self.task: dict[str, Any] = {
            "id": TASK, "project_id": PROJECT, "status_id": S_TODO, "updated_at": V1,
            "title": "old title", "custom_fields": {}, "tags": [],
            "assignees": ["m@x.test"]}
        self.types = [{"id": TYPE, "name": "Bug", "is_epic": False}]

    async def __call__(self, scope, receive, send) -> None:
        if (scope["method"], scope["path"]) == ("POST", "/projects/tasks"):
            self.posting.set()
            if self.hold is not None:
                await self.hold.wait()
        await super().__call__(scope, receive, send)

    def _answer(self, r: SimpleNamespace) -> tuple[int, Any]:
        if (r.method, r.path) in self.answers:
            return self.answers[(r.method, r.path)]
        if r.method == "POST" and r.path == "/projects/tasks":
            sent = json.loads(r.body)
            row = {"id": NEW, "project_id": sent["project_id"],
                   "root_project_id": sent["project_id"],
                   "parent_task_id": sent.get("parent_task_id"),
                   "status_id": sent.get("status_id", S_TODO), "title": sent["title"],
                   "description": sent.get("description"), "tags": sent.get("tags", []),
                   "due_at": sent.get("due_at"), "task_number": 7,
                   "created_by": "m@x.test", "updated_at": V1, "source": "mcp",
                   "clickup_id": "CU-9", "custom_fields": {}}
            self.created.append(row)
            return 201, row
        if r.method == "GET" and r.path == f"/projects/tasks/{NEW}" and self.created:
            return 200, {**self.created[0], "assignees": [], **self.after_create}
        if r.method == "GET" and r.path == f"/projects/tasks/{OTHER_TASK}":
            return 200, {"id": OTHER_TASK, "project_id": OTHER_PROJECT, "updated_at": V1}
        if r.method == "GET" and r.path == f"/projects/tasks/{TASK}":
            return 200, dict(self.task)
        if r.method == "GET" and r.path == f"/projects/nodes/{PROJECT}/types":
            return 200, {"rows": self.types, "total": len(self.types)}
        if r.method == "PATCH" and r.path == f"/projects/tasks/{TASK}":
            sent = json.loads(r.body)
            self.task.update(sent)
            answer = {**self.task, "updated_at": V2, "clickup_id": "CU-1"}
            answer.pop("assignees")
            self.task["updated_at"] = V3
            return 200, answer
        return super()._answer(r)

    def posts(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if (r.method, r.path) == ("POST", "/projects/tasks")]


@pytest.fixture
def redis_fake(monkeypatch) -> _FakeRedis:
    """The slot of the edge and branch 0's nonces, on the fake under the
    real wrapper."""
    fake = _FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def edge(monkeypatch, redis_fake) -> SimpleNamespace:
    """``test_mcp_edge_writes.edge`` with this file's fake gateway."""
    found: dict[str, Any] = {GOOD: w._lookup()}

    async def lookup(raw: str, *, kind: str = "access"):
        return found.get(raw)

    from acb_auth import agent_tokens

    monkeypatch.setattr(agent_tokens, "lookup_token", lookup)
    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE), ("agent_edge_gateway_url", "http://gw.test")):
        monkeypatch.setattr(settings, name, value)
    fake = _Gateway()
    with gateway_client.use_transport(httpx.ASGITransport(app=fake)):
        yield SimpleNamespace(gateway=fake, tokens=found, redis=redis_fake)


_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text


def _create_args(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"project_id": PROJECT, "title": "Ship the fix"}
    args.update(kw)
    return args


def _update_args(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"task_id": TASK, "expected_version": V1}
    args.update(kw)
    return args


class TestTheToolRecords:
    """§9.2 and §9.5, database-free."""

    async def test_tools_list_shows_both_tools_with_their_hints(self, edge):
        r = await _post(_rpc("tools/list"))
        listed = {t["name"]: t for t in r.json()["result"]["tools"]}
        create, update = listed["create_task"], listed["update_task"]
        assert create["annotations"] == {"title": "Create a task", "readOnlyHint": False,
                                         "destructiveHint": False, "idempotentHint": False,
                                         "openWorldHint": False}
        assert update["annotations"]["idempotentHint"] is True
        assert update["annotations"]["readOnlyHint"] is False

    def test_the_descriptions_teach_an_agent_the_way_in(self):
        create = tools.CREATE_TASK.description
        assert "A sub-project is a project" in create and "list_projects" in create
        assert "get_project" in create and "status ids" in create
        assert "idempotency_key" in create and "untrusted_text" in create
        update = tools.UPDATE_TASK.description
        assert "expected_version" in update and "set_task_status" in update

    @pytest.mark.parametrize("name,args", [
        ("create_task", _create_args()), ("update_task", _update_args(title="x"))])
    async def test_check_8_refuses_without_projects_write(self, edge, name, args):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call(name, args))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("name,args", [
        ("create_task", _create_args()), ("update_task", _update_args(title="x"))])
    async def test_with_the_writes_flag_off_a_call_sends_nothing(
            self, edge, monkeypatch, name, args):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        listed = (await _post(_rpc("tools/list"))).json()["result"]["tools"]
        assert name not in {t["name"] for t in listed}
        assert _text(await _tool(name, args)) == WRITES_OFF
        assert edge.gateway.requests == []
        assert edge.redis.calls == []


class TestCreateTask:
    """``create_task`` at the edge, database-free."""

    async def test_it_posts_the_named_keys_only_and_maps_the_task(self, edge):
        result = await _tool("create_task", _create_args(
            description="Body", status_id=S_DOING, tags=["api", "bug"],
            due_at="2026-10-31", type_id=_uuid(8001)))
        assert result["isError"] is False, result
        [post] = edge.gateway.posts()
        assert (post.query, post.headers.get("if-match")) == ("", None)
        assert json.loads(post.body) == {
            "project_id": PROJECT, "type_id": _uuid(8001), "status_id": S_DOING,
            "title": "Ship the fix", "description": "Body", "tags": ["api", "bug"],
            "due_at": "2026-10-31"}
        body = result["structuredContent"]
        assert body["created"] is True
        task = body["task"]
        assert tuple(task) == tools.GET_TASK_KEYS
        assert task["title"] == {"untrusted_text": "Ship the fix"}
        assert task["tags"] == [{"untrusted_text": "api"}, {"untrusted_text": "bug"}]
        assert task["status"] == {"id": S_DOING, "name": {"untrusted_text": "Doing"}}
        # Fix round 1: the version of the re-read, after the links of the
        # text touched the task, not the V1 of the POST answer.
        assert task["version"] == V3 and task["assignees"] == []
        assert "clickup_id" not in json.dumps(body) and "source" not in task
        assert [r.path for r in edge.gateway.requests if r.method == "GET"][-1] == (
            f"/projects/tasks/{NEW}")

    async def test_a_failed_re_read_still_reports_the_task(self, edge):
        """Fix round 1. The write happened, so a failed re-read gives the
        POST's own answer and never an error."""
        edge.gateway.answers[("GET", f"/projects/tasks/{NEW}")] = (503, {})
        result = await _tool("create_task", _create_args())
        assert result["isError"] is False, result
        assert result["structuredContent"]["task"]["version"] == V1

    @pytest.mark.parametrize("type_id", [_uuid(8999), _uuid(8002)])
    async def test_a_type_outside_the_project_is_refused_before_the_write(
            self, edge, type_id):
        """Fix round 1 (P1). The edge reads the project's types first."""
        result = await _tool("create_task", _create_args(type_id=type_id,
                                                         idempotency_key=IDEM))
        assert _text(result) == tools.TEXT_BAD_REQUEST
        assert edge.gateway.writes() == []
        assert edge.redis.slots() == {}, "a refusal before the POST frees the key"

    @pytest.mark.parametrize("answer,expected", [
        (404, "Project not found."), (403, "This member cannot change that."),
        (503, tools.TEXT_BUSY)])
    async def test_a_types_read_that_fails_sends_no_write(self, edge, answer, expected):
        edge.gateway.answers[("GET", f"/projects/nodes/{PROJECT}/types")] = (answer, {})
        assert _text(await _tool("create_task", _create_args(type_id=TYPE))) == expected
        assert edge.gateway.writes() == []

    async def test_a_call_with_no_key_touches_no_store(self, edge):
        await _tool("create_task", _create_args())
        assert edge.redis.calls == []

    @pytest.mark.parametrize("status", [S_DONE, S_CANCELLED])
    async def test_a_closing_status_is_refused_before_the_write(self, edge, status):
        assert _text(await _tool("create_task", _create_args(status_id=status))) == (
            "Moving a task to done or cancelled stays a human step.")
        assert edge.gateway.writes() == []

    async def test_a_status_outside_the_set_is_a_bad_request(self, edge):
        assert _text(await _tool("create_task", _create_args(status_id=_uuid(9999)))) == (
            tools.TEXT_BAD_REQUEST)
        assert edge.gateway.writes() == []

    @pytest.mark.parametrize("answer,expected", [
        (404, "Project not found."), (403, "This member cannot change that.")])
    async def test_a_project_the_member_cannot_open_sends_no_write(
            self, edge, answer, expected):
        edge.gateway.answers[("GET", f"/projects/nodes/{PROJECT}/statuses")] = (
            answer, {"detail": "SECRET"})
        assert _text(await _tool("create_task", _create_args())) == expected
        assert edge.gateway.writes() == []

    async def test_a_parent_in_the_same_project_makes_a_subtask(self, edge):
        result = await _tool("create_task", _create_args(parent_task_id=TASK))
        assert result["isError"] is False, result
        [post] = edge.gateway.posts()
        assert json.loads(post.body)["parent_task_id"] == TASK

    async def test_a_parent_in_another_project_is_refused(self, edge):
        assert _text(await _tool("create_task", _create_args(parent_task_id=OTHER_TASK))) == (
            "The parent task is in another project. Send the project_id of the parent task.")
        assert edge.gateway.writes() == []

    async def test_a_parent_the_member_cannot_see_is_not_found(self, edge):
        assert _text(await _tool("create_task", _create_args(parent_task_id=_uuid(1999)))) == (
            "Parent task not found.")
        assert edge.gateway.writes() == []

    @pytest.mark.parametrize("status,expected", [
        (403, "This member cannot change that."),
        (404, "Project not found."),
        (422, tools.TEXT_BAD_REQUEST),
        (429, "Too many changes. Wait a few minutes."),
        (503, tools.TEXT_BUSY),
    ])
    async def test_the_answer_table_of_the_post(self, edge, status, expected):
        edge.gateway.answers[("POST", "/projects/tasks")] = (status, {"detail": "SECRET"})
        result = await _tool("create_task", _create_args())
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    @pytest.mark.parametrize("args,name", [
        ({"assignees": ["x@y.z"]}, "assignees"),
        ({"source": "manual"}, "source"),
        ({"tags": ["a,b"]}, "tags"),
        ({"tags": ["t"] * 21}, "tags"),
        ({"due_at": "next friday"}, "due_at"),
        ({"title": ""}, "title"),
        ({"idempotency_key": "short"}, "idempotency_key"),
        ({"idempotency_key": "has space in it"}, "idempotency_key"),
    ])
    async def test_an_input_outside_the_schema_is_an_input_error(self, edge, args, name):
        """No assignee and no source: the member names neither (§9.2)."""
        result = await _tool("create_task", _create_args(**args))
        assert name in _text(result)
        assert edge.gateway.requests == []


class TestTheIdempotencyKey:
    """§9.2, ``create_task`` with an ``idempotency_key``, database-free."""

    async def test_the_same_key_twice_makes_one_task(self, edge):
        first = (await _tool("create_task", _create_args(idempotency_key=IDEM)))
        edge.gateway.after_create["assignees"] = ["m@x.test"]
        again = (await _tool("create_task", _create_args(idempotency_key=IDEM)))
        assert first["structuredContent"]["created"] is True
        assert again["structuredContent"]["created"] is False
        assert again["structuredContent"]["task"]["id"] == NEW
        # The second call reads the task, so it shows what the task is NOW.
        assert again["structuredContent"]["task"]["assignees"] == ["m@x.test"]
        assert len(edge.gateway.posts()) == 1

    async def test_the_slot_is_tenant_prefixed_and_holds_no_raw_key(self, edge):
        await _tool("create_task", _create_args(idempotency_key=IDEM))
        [(name, value)] = edge.redis.slots().items()
        assert name.startswith(f"cc:{ORG}:{idempotency.NAMESPACE}:{CONN}:")
        assert IDEM not in name and IDEM not in json.dumps(value)
        assert value["state"] == "done" and value["task"] == NEW
        assert edge.redis.store[name][1] == edge.redis.now + 24 * 60 * 60

    async def test_the_same_key_with_other_inputs_is_refused(self, edge):
        await _tool("create_task", _create_args(idempotency_key=IDEM))
        other = await _tool("create_task", _create_args(idempotency_key=IDEM, title="Other"))
        assert _text(other) == (
            "This idempotency_key was used for a different task. Send a new key.")
        assert len(edge.gateway.posts()) == 1

    async def test_the_key_belongs_to_one_connection(self, edge):
        await _tool("create_task", _create_args(idempotency_key=IDEM))
        edge.tokens[GOOD] = dataclasses.replace(w._lookup(), connection_id=_uuid(4242))
        second = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert second["structuredContent"]["created"] is True
        assert len(edge.gateway.posts()) == 2

    async def test_two_calls_at_once_make_one_task(self, edge):
        """The race. The first call holds the slot while its POST is in
        flight. The second call reads ``pending`` and sends nothing."""
        edge.gateway.hold = asyncio.Event()
        first = asyncio.create_task(_tool("create_task", _create_args(idempotency_key=IDEM)))
        await asyncio.wait_for(edge.gateway.posting.wait(), 5)
        second = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert _text(second) == (
            "A call with this idempotency_key is still running. Wait a few seconds,"
            " then send the same call again.")
        edge.gateway.hold.set()
        assert (await first)["structuredContent"]["created"] is True
        third = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert third["structuredContent"]["created"] is False
        assert len(edge.gateway.posts()) == 1

    @pytest.mark.parametrize("status", [403, 404, 422, 429])
    async def test_a_clear_refusal_frees_the_key(self, edge, status):
        edge.gateway.answers[("POST", "/projects/tasks")] = (status, {"detail": "x"})
        assert (await _tool("create_task", _create_args(idempotency_key=IDEM)))["isError"]
        assert edge.redis.slots() == {}
        del edge.gateway.answers[("POST", "/projects/tasks")]
        again = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert again["structuredContent"]["created"] is True

    async def test_a_refusal_before_the_post_frees_the_key(self, edge):
        refused = await _tool("create_task", _create_args(
            idempotency_key=IDEM, status_id=S_DONE))
        assert _text(refused) == "Moving a task to done or cancelled stays a human step."
        assert edge.redis.slots() == {}

    @pytest.mark.parametrize("status", [500, 503])
    async def test_a_post_with_no_clear_answer_keeps_the_key(self, edge, status):
        """The gateway may have made the task, so a retry with the key must not
        make a second one. It names the way out instead."""
        edge.gateway.answers[("POST", "/projects/tasks")] = (status, {"detail": "x"})
        assert _text(await _tool("create_task", _create_args(idempotency_key=IDEM))) == (
            tools.TEXT_BUSY)
        del edge.gateway.answers[("POST", "/projects/tasks")]
        again = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert _text(again) == tools.TEXT_KEY_UNKNOWN
        assert len(edge.gateway.posts()) == 1

    @pytest.mark.parametrize("value", [
        "not json",
        json.dumps({"state": "unknown", "fingerprint": "other", "task": None})])
    async def test_an_unknown_or_unreadable_slot_always_gives_the_unknown_text(
            self, edge, value):
        """Fix round 1 (the verifier). The state is read first, so a slot the
        edge cannot read is never "used for a different task"."""
        with tenant_redis.organization_scope(ORG):
            name = idempotency.Slot(w._lookup(), IDEM)._key().value
        edge.redis.store[name] = (value, edge.redis.now + 100)
        result = await _tool("create_task", _create_args(idempotency_key=IDEM, title="Other"))
        assert _text(result) == tools.TEXT_KEY_UNKNOWN
        assert edge.gateway.requests == []

    async def test_a_post_that_never_left_the_edge_frees_the_key(self, edge):
        """Fix round 1 (the reviewer). A connect error means no byte of the
        POST went out, so the gateway made nothing and the key stays free."""
        inner = httpx.ASGITransport(app=edge.gateway)

        class _Refusing(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if (request.method, request.url.path) == ("POST", "/projects/tasks"):
                    raise httpx.ConnectError("refused", request=request)
                return await inner.handle_async_request(request)

        with gateway_client.use_transport(_Refusing()):
            refused = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert _text(refused) == tools.TEXT_BUSY
        assert edge.redis.slots() == {}
        again = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert again["structuredContent"]["created"] is True

    async def test_a_read_timeout_after_the_send_keeps_the_key(self, edge):
        """The other side of the rule: a POST that went out and got no answer
        may have made the task, so the key goes `unknown`."""
        inner = httpx.ASGITransport(app=edge.gateway)

        class _Silent(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if (request.method, request.url.path) == ("POST", "/projects/tasks"):
                    raise httpx.ReadTimeout("no answer", request=request)
                return await inner.handle_async_request(request)

        with gateway_client.use_transport(_Silent()):
            assert _text(await _tool("create_task", _create_args(idempotency_key=IDEM))) == (
                tools.TEXT_BUSY)
        [slot] = edge.redis.slots().values()
        assert slot["state"] == "unknown"

    async def test_a_store_outage_sends_no_write(self, edge):
        edge.redis.fail = RedisConnectionError("refused")
        result = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert _text(result) == tools.TEXT_BUSY
        assert edge.gateway.requests == []

    async def test_a_store_outage_after_the_post_still_reports_the_task(self, edge):
        """The task exists. A failed write of the result is logged, and the
        tool still says what happened."""
        real_set = edge.redis.set

        async def set_once(name, value, **kw):
            if json.loads(value)["state"] == "done":
                raise RedisConnectionError("refused")
            return await real_set(name, value, **kw)

        edge.redis.set = set_once
        result = await _tool("create_task", _create_args(idempotency_key=IDEM))
        assert result["structuredContent"]["created"] is True

    def test_a_slot_value_the_edge_cannot_read_is_unknown(self):
        record = idempotency._decode("not json")
        assert record.state == idempotency.UNKNOWN

    def test_the_lives_of_a_slot(self):
        assert idempotency.PENDING_TTL_S > tools.TOOL_TIMEOUT_S
        assert idempotency.RESULT_TTL_S == 24 * 60 * 60


class TestUpdateTask:
    """``update_task`` at the edge, database-free."""

    async def test_it_patches_the_named_fields_only_with_if_match(self, edge):
        result = await _tool("update_task", _update_args(
            title="New", description="D", tags=["x"], due_at="2026-11-01T09:00:00Z",
            custom_fields={"severity": "high", "points": None}))
        assert result["isError"] is False, result
        [patch] = edge.gateway.writes()
        assert (patch.method, patch.path, patch.query) == ("PATCH", f"/projects/tasks/{TASK}", "")
        assert patch.headers["if-match"] == V1
        assert json.loads(patch.body) == {
            "title": "New", "description": "D", "tags": ["x"],
            "due_at": "2026-11-01T09:00:00Z",
            "custom_fields": {"severity": "high", "points": None}}
        task = result["structuredContent"]["task"]
        assert tuple(task) == tools.UPDATED_TASK_KEYS
        assert "assignees" not in task
        # Fix round 1: the re-read's version (V3), not the PATCH answer's (V2).
        assert task["version"] == V3
        assert next(r.path for r in edge.gateway.requests if r.method == "GET") == (
            f"/projects/tasks/{TASK}")
        # The fake echoes the patch. A string value is (U), and null stays null.
        assert task["custom_fields"] == {"severity": {"untrusted_text": "high"},
                                         "points": None}
        assert task["status"]["name"] == {"untrusted_text": "To do"}

    @pytest.mark.parametrize("name", [
        "status_id", "project_id", "parent_task_id", "assignees", "archived_at",
        "source", "importance", "include_subtasks"])
    async def test_it_cannot_change_anything_else(self, edge, name):
        result = await _tool("update_task", _update_args(title="x", **{name: "v"}))
        assert _text(result) == f"The input {name!r} is not known."
        assert edge.gateway.requests == []

    async def test_no_field_is_an_input_error_with_no_request(self, edge):
        assert _text(await _tool("update_task", _update_args())) == tools.TEXT_UPDATE_EMPTY
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("fields", [
        {"bad key!": 1}, {f"k{n}": n for n in range(51)}])
    async def test_custom_fields_of_the_wrong_form_send_nothing(self, edge, fields):
        result = await _tool("update_task", _update_args(custom_fields=fields))
        assert _text(result) == "The input 'custom_fields' has the wrong form."
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("status,expected", [
        (412, "The task changed. Read it again."),
        (404, "Task not found."),
        (403, "This member cannot change that."),
        (422, tools.TEXT_BAD_REQUEST),
        (429, "Too many changes. Wait a few minutes."),
        (503, tools.TEXT_BUSY),
    ])
    async def test_the_answer_table_of_the_patch(self, edge, status, expected):
        edge.gateway.answers[("PATCH", f"/projects/tasks/{TASK}")] = (
            status, {"detail": "SECRET-DETAIL"})
        result = await _tool("update_task", _update_args(title="x"))
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    async def test_a_failed_re_read_gives_the_patch_answer(self, edge):
        """Fix round 1. The PATCH landed, so a failed re-read gives its own
        answer (V2) and never an error."""
        edge.gateway.answers[("GET", f"/projects/tasks/{TASK}")] = (503, {})
        result = await _tool("update_task", _update_args(title="x"))
        assert result["isError"] is False, result
        assert result["structuredContent"]["task"]["version"] == V2

    async def test_a_failed_name_read_after_the_write_still_reports_it(self, edge):
        edge.gateway.answers[("GET", f"/projects/nodes/{PROJECT}/statuses")] = (503, {})
        result = await _tool("update_task", _update_args(title="x"))
        assert result["isError"] is False, result
        assert result["structuredContent"]["task"]["status"]["name"] is None


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcptaskw"
_APP_PW = "mcptaskw_scratch_pw"
MEMBER = w.MEMBER
OWNER = w.OWNER
INTERNAL = w.INTERNAL


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, one member, two projects."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcptaskw"
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcptaskw', 'mcptaskw')"), {"id": org})
    a = w._Seeder(eng, org)
    # The gateway numbers the tasks that it makes from 1 (`pm_task_counters`).
    # The seeded tasks start far above, so the two never meet.
    a.number = 100_000
    a.member(MEMBER, ["feature:projects"])
    team, other = a.project("Team"), a.project("Other team")
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
        done=a.status(team, "Done", "done", 3),
        cancelled=a.status(team, "Dropped", "cancelled", 4),
        other=a.status(other, "To do", "todo", 0),
    )
    types = SimpleNamespace(**{
        name: str(a.one("INSERT INTO pm_task_types (project_id, name, organization_id)"
                        " VALUES (CAST(:p AS uuid), :n, CAST(:o AS uuid)) RETURNING id",
                        p=project, n="Bug", o=org))
        for name, project in (("team", team), ("other", other))})
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, other=other, s=s,
                             c={}, tok={}, task=a.task, types=types)
    for name, scopes in (("write", w.WRITE_SCOPES), ("read", ("projects:read",))):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def live(monkeypatch, world, redis_fake):
    """The real gateway app at ``agent_edge_gateway_url`` (EA3-20), writes on."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE),
            ("agent_edge_gateway_url", "http://gateway.test")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "mcpwrites.test")
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    world.redis = redis_fake
    with gateway_client.use_transport(httpx.ASGITransport(app=gateway_app)):
        try:
            yield world
        finally:
            invalidate_access()


async def _in_scope(world, work) -> Any:
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _mcp_unscoped(world, name: str, tool: str,
                        arguments: dict[str, Any]) -> dict[str, Any]:
    """One ``tools/call``. The caller holds the engine scope."""
    from agent_edge import app as edge_app_module

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=w.ISSUER) as client:
        r = await client.post("/mcp", content=json.dumps(_call(tool, arguments)),
                              headers={"authorization": f"Bearer {world.tok[name]}",
                                       "content-type": "application/json",
                                       "mcp-protocol-version": "2025-11-25"})
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def _mcp(world, name: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        return await _mcp_unscoped(world, name, tool, arguments)
    return await _in_scope(world, go)


async def _send(world, method: str, path: str, *, body: dict[str, Any] | None = None,
                query: str = "", headers: dict[str, str] | None = None,
                signed: str | None = "write") -> httpx.Response:
    """One request to the gateway app: signed for connection *signed*, or as
    the member through the internal bearer when *signed* is None."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    sent = dict(headers or {})
    if signed is not None:
        sent.update(agent_signing.signed_headers(
            KEY, organization_id=world.org, connection_id=world.c[signed], actor_via=VIA,
            method=method, path=path, query=query, body=raw))
    else:
        sent.update({"Authorization": f"Bearer {INTERNAL}", "X-User-Email": MEMBER})
    if body is not None:
        sent["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path + (f"?{query}" if query else ""),
                                        headers=sent, content=raw)
    return await _in_scope(world, go)


def _row(world, task: str) -> Any:
    with world.admin.begin() as c:
        return c.execute(text("SELECT * FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                         {"t": task}).one()


def _count(world, title: str) -> int:
    with world.admin.begin() as c:
        return int(c.execute(text("SELECT count(*) FROM pm_tasks WHERE title = :t"),
                             {"t": title}).scalar() or 0)


class TestTheSourceAndTheToolsOnARealDatabase:
    """§15 EA-6d: a task that an agent makes reads ``mcp``, and one key makes
    one row."""

    pytestmark = _DB_GATE

    async def test_a_task_an_agent_makes_reads_mcp(self, live):
        result = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "agent made", "tags": ["api"],
            "due_at": "2026-10-31"})
        assert result["isError"] is False, result
        task = result["structuredContent"]["task"]
        row = _row(live, task["id"])
        assert (row.source, row.created_by, str(row.status_id)) == ("mcp", MEMBER, live.s.todo)
        assert row.task_number is not None and list(row.tags) == ["api"]
        with live.admin.begin() as c:
            via = c.execute(text(
                "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                " AND body = 'Task created'"), {"t": task["id"]}).scalar()
        assert via == VIA
        assert task["status"]["name"] == {"untrusted_text": "To do"}

    async def test_a_subtask_in_the_same_project(self, live):
        parent = live.task(live.team, live.s.doing, "parent")
        result = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "child", "parent_task_id": parent})
        row = _row(live, result["structuredContent"]["task"]["id"])
        assert str(row.parent_task_id) == parent
        # The parent's open lane, through the one seam (D-PM-38).
        assert str(row.status_id) == live.s.doing

    async def test_the_same_key_makes_one_row(self, live):
        args = {"project_id": live.team, "title": "once only", "idempotency_key": "r8-once-1"}
        first = await _mcp(live, "write", "create_task", args)
        again = await _mcp(live, "write", "create_task", args)
        assert first["structuredContent"]["created"] is True
        assert again["structuredContent"]["created"] is False
        assert again["structuredContent"]["task"]["id"] == first["structuredContent"]["task"]["id"]
        assert _count(live, "once only") == 1

    async def test_two_calls_at_once_with_one_key_make_one_row(self, live):
        args = {"project_id": live.team, "title": "raced", "idempotency_key": "r8-race-01"}

        async def both() -> list[dict[str, Any]]:
            # ONE engine scope for both calls: a scope resets the process
            # engine, so two scopes at once would close each other's pool.
            return list(await asyncio.gather(*(
                _mcp_unscoped(live, "write", "create_task", args) for _ in range(2))))

        results = await _in_scope(live, both)
        assert _count(live, "raced") == 1
        assert sorted(r["isError"] for r in results) == [False, True]
        loser = next(r for r in results if r["isError"])
        assert loser["content"][0]["text"] == tools.TEXT_KEY_RUNNING

    async def test_a_type_of_the_project_lands_and_another_is_refused(self, live):
        """Fix round 1 (P1). The edge reads the project's types first."""
        ok = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "typed", "type_id": live.types.team})
        assert ok["isError"] is False, ok
        assert str(_row(live, ok["structuredContent"]["task"]["id"]).type_id) == (
            live.types.team)
        for type_id in (live.types.other, str(uuid.uuid4())):
            refused = await _mcp(live, "write", "create_task", {
                "project_id": live.team, "title": "mistyped", "type_id": type_id})
            assert refused["content"][0]["text"] == tools.TEXT_BAD_REQUEST
        assert _count(live, "mistyped") == 0

    async def test_the_returned_version_survives_the_links_of_the_text(self, live):
        """Fix round 1 (P2). A GitHub URL or a `#N` in the text makes the
        gateway touch the task after the commit. The version that each tool
        returns must still work as the next `expected_version`."""
        made = await _mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "linked",
            "description": "Fixes https://github.com/acme/widgets/pull/12"})
        task = made["structuredContent"]["task"]
        changed = await _mcp(live, "write", "update_task", {
            "task_id": task["id"], "expected_version": task["version"],
            "description": "Also https://github.com/acme/widgets/pull/13"})
        assert changed["isError"] is False, changed
        again = await _mcp(live, "write", "update_task", {
            "task_id": task["id"],
            "expected_version": changed["structuredContent"]["task"]["version"],
            "title": "linked twice"})
        assert again["isError"] is False, again
        assert _row(live, task["id"]).title == "linked twice"

    async def test_update_task_changes_the_named_fields_and_not_the_status(self, live):
        task = live.task(live.team, live.s.doing, "before")
        read = await _mcp(live, "write", "get_task", {"task_id": task})
        version = read["structuredContent"]["task"]["version"]
        result = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": version, "title": "after",
            "tags": ["one"], "due_at": "2026-12-01"})
        assert result["isError"] is False, result
        row = _row(live, task)
        assert (row.title, list(row.tags), str(row.status_id)) == ("after", ["one"], live.s.doing)
        assert result["structuredContent"]["task"]["version"] != version
        with live.admin.begin() as c:
            via = c.execute(text(
                "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                " AND type = 'field_change'"), {"t": task}).scalar()
        assert via == VIA
        stale = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": version, "title": "again"})
        assert stale["content"][0]["text"] == "The task changed. Read it again."
        assert _row(live, task).title == "after"


class TestTheGatewayNarrowsAnAgentWrite:
    """§9.2 and §17: a signed agent write that the edge would never build."""

    pytestmark = _DB_GATE

    @pytest.mark.parametrize("extra", [
        {"source": "manual"}, {"importance": 3}, {"custom_fields": {}},
        {"estimate_mins": 30}])
    async def test_a_create_with_another_key_is_422(self, live, extra):
        title = f"narrow {sorted(extra)[0]}"
        r = await _send(live, "POST", "/projects/tasks",
                        body={"project_id": live.team, "title": title, **extra})
        assert r.status_code == 422, r.text
        assert _count(live, title) == 0

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    async def test_a_create_into_a_closing_status_is_422(self, live, status):
        title = f"closing {status}"
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": title, "status_id": getattr(live.s, status)})
        assert r.status_code == 422, r.text
        assert _count(live, title) == 0

    @pytest.mark.parametrize("which", ["other", "made_up"])
    async def test_a_create_with_a_type_outside_the_project_is_422(self, live, which):
        """Fix round 1 (P1). A made-up id is a 422, never the 500 of the
        foreign key."""
        type_id = live.types.other if which == "other" else str(uuid.uuid4())
        title = f"type {which}"
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": title, "type_id": type_id})
        assert r.status_code == 422, r.text
        assert _count(live, title) == 0

    async def test_a_create_under_a_parent_of_another_project_is_422(self, live):
        parent = live.task(live.other, live.s.other, "foreign parent")
        r = await _send(live, "POST", "/projects/tasks", body={
            "project_id": live.team, "title": "cross", "parent_task_id": parent})
        assert r.status_code == 422, r.text
        assert _count(live, "cross") == 0

    async def test_a_signed_create_with_the_named_keys_lands_as_mcp(self, live):
        """The control for the refusals above."""
        r = await _send(live, "POST", "/projects/tasks",
                        body={"project_id": live.team, "title": "signed control"})
        assert r.status_code == 201, r.text
        assert _row(live, r.json()["id"]).source == "mcp"

    @pytest.mark.parametrize("body,query", [
        ({"importance": 3}, ""),
        ({"leveraged": True}, ""),
        ({"source": "manual"}, ""),
        ({"title": "x"}, "include_subtasks=true"),
    ])
    async def test_a_patch_with_another_key_or_the_cascade_is_422(self, live, body, query):
        task = live.task(live.team, live.s.todo, "patch narrow")
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", body=body, query=query)
        assert r.status_code == 422, r.text
        assert _row(live, task).title == "patch narrow"

    @pytest.mark.parametrize("status", ["done", "cancelled"])
    async def test_a_patch_into_a_closing_status_rolls_back_all_of_it(self, live, status):
        task = live.task(live.team, live.s.todo, "keep me")
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", body={
            "title": "changed", "status_id": getattr(live.s, status)})
        assert r.status_code == 422, r.text
        row = _row(live, task)
        assert (row.title, str(row.status_id), row.completed_at) == (
            "keep me", live.s.todo, None)

    async def test_a_signed_patch_with_the_named_keys_lands(self, live):
        task = live.task(live.team, live.s.todo, "patch control")
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", body={
            "title": "patched", "status_id": live.s.doing})
        assert r.status_code == 200, r.text
        assert (_row(live, task).title, str(_row(live, task).status_id)) == (
            "patched", live.s.doing)


class TestAMemberCannotNameTheServerSource:
    """§9.2: only the server stamps ``mcp``, and a member's write is not
    narrowed."""

    pytestmark = _DB_GATE

    async def test_a_member_create_that_names_mcp_is_422(self, live):
        r = await _send(live, "POST", "/projects/tasks", signed=None, body={
            "project_id": live.team, "title": "forged source", "source": "mcp"})
        assert r.status_code == 422, r.text
        assert _count(live, "forged source") == 0

    async def test_a_member_create_reads_manual_and_is_not_narrowed(self, live):
        r = await _send(live, "POST", "/projects/tasks", signed=None, body={
            "project_id": live.team, "title": "by hand", "importance": 3})
        assert r.status_code == 201, r.text
        row = _row(live, r.json()["id"])
        assert (row.source, row.importance) == ("manual", 3)

    async def test_a_member_patch_that_names_mcp_is_422(self, live):
        task = live.task(live.team, live.s.todo, "member patch")
        r = await _send(live, "PATCH", f"/projects/tasks/{task}", signed=None,
                        body={"source": "mcp"})
        assert r.status_code == 422, r.text
        assert _row(live, task).source == "manual"


class TestTheMigration:
    """The EA-6d migration on a real database (R8)."""

    pytestmark = _DB_GATE

    def test_the_check_accepts_exactly_the_task_sources(self, live):
        from gateway.routes.projects.core import TASK_SOURCES

        with live.admin.begin() as c:
            definition = c.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conname = 'pm_tasks_source_check'")).scalar()
        assert set(re.findall(r"'([a-z_]+)'", definition)) == set(TASK_SOURCES)

    def test_the_check_still_refuses_an_unknown_source(self, live):
        task = live.task(live.team, live.s.todo, "check")
        with pytest.raises(Exception, match="pm_tasks_source_check"), live.admin.begin() as c:
            c.execute(text("UPDATE pm_tasks SET source = 'bogus'"
                           " WHERE id = CAST(:t AS uuid)"), {"t": task})

    def test_the_migration_replays_as_a_no_op(self, live):
        path = next(p for p in Path(w._LADDER_DIR).glob("*.sql")
                    if "pm_tasks_source_check" in p.read_text(encoding="utf-8")
                    and "'mcp'" in p.read_text(encoding="utf-8"))
        with live.admin.begin() as c:
            w._exec_sql_file(c, path.read_text(encoding="utf-8"))
        self.test_the_check_accepts_exactly_the_task_sources(live)


def test_only_the_slot_module_reaches_redis():
    """Edge rule 17 (``apps/services/agent_edge/AGENTS.md``). One edge module
    reaches Redis, through the tenant wrapper, and no edge module imports a
    Redis client of its own."""
    edge = Path(__file__).resolve().parents[2] / "apps/services/agent_edge/agent_edge"
    reach = sorted(p.name for p in edge.glob("*.py")
                   if re.search(r"\btenant_redis\b|\bimport redis\b|\bfrom redis\b",
                                p.read_text(encoding="utf-8")))
    assert reach == ["idempotency.py"], reach
    source = (edge / "idempotency.py").read_text(encoding="utf-8")
    assert "import redis" not in source and "from redis" not in source


def test_every_route_checks_a_request_source_against_the_client_tuple():
    """§9.2. A route that reads a task source from a request checks it
    against ``CLIENT_TASK_SOURCES``. ``TASK_SOURCES`` itself is the mirror of
    the CHECK, and only ``core.py`` names it."""
    package = Path(__file__).resolve().parents[2] / (
        "apps/services/gateway/gateway/routes/projects")
    named = sorted(p.name for p in package.rglob("*.py")
                   if p.name != "core.py"
                   and re.search(r"(?<![A-Z_])TASK_SOURCES\b", p.read_text(encoding="utf-8")))
    assert named == [], named


# ═══════════════════════════════════════════════════════════════════════════
# A real Redis 7 (optional)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
async def real_redis(monkeypatch):
    import redis.asyncio as aioredis

    client = aioredis.from_url(_REDIS_URL, decode_responses=True)
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(client))
    yield client
    with tenant_redis.organization_scope(ORG):
        async for name in TenantRedis(client).scan_iter(
                tenant_redis.match(idempotency.NAMESPACE)):
            await client.delete(name)
    await client.aclose()


class TestTheSlotOnARealRedis:
    """``SET NX EX``, the race and the lives, on a real server."""

    pytestmark = _REDIS_GATE

    async def test_one_claim_wins_and_the_result_lives_a_day(self, real_redis):
        lookup = w._lookup()
        key = f"real-{uuid.uuid4().hex}"
        slots = [idempotency.Slot(lookup, key) for _ in range(5)]
        claims = await asyncio.gather(*(s.claim("fp") for s in slots))
        assert sum(c is None for c in claims) == 1
        assert {c.state for c in claims if c is not None} == {idempotency.PENDING}
        winner = slots[claims.index(None)]
        await winner.finish("fp", NEW)
        again = await idempotency.Slot(lookup, key).claim("fp")
        assert (again.state, again.task_id) == (idempotency.DONE, NEW)
        with tenant_redis.organization_scope(ORG):
            ttl = await real_redis.ttl(winner._key().value)
        assert 24 * 60 * 60 - 5 <= ttl <= 24 * 60 * 60

    async def test_a_release_frees_the_slot(self, real_redis):
        lookup = w._lookup()
        slot = idempotency.Slot(lookup, f"real-{uuid.uuid4().hex}")
        assert await slot.claim("fp") is None
        await slot.release()
        assert await slot.claim("fp") is None
