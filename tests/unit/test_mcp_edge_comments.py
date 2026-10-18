"""WS-55 EA-6c — ``add_comment``, the comment write pair and the slot by tool.

Spec: ``project-docs/specs/external_agent_access.md`` §6.1 (EA3-35), §9.2
("Rules of ``add_comment``"), §9.5 rule 4 and §15 EA-6c.

Two halves, as in ``test_mcp_edge_task_writes.py``, whose helpers this file
reuses.

**Database-free (always runs).** The edge app runs in process with a fake
token store, a fake gateway and a fake Redis under the REAL ``TenantRedis``.
It proves the body that the tool sends, the pinned output, the refusals
before a write, the answer map and the ``idempotency_key``.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app. It proves K1 to
K9 of the dispatch:

* K1. One key makes one row, also for two calls at once, and the replay
  returns the same id with ``created: false``.
* K2. One key with other inputs gets the reused-key text.
* K3. A key that ``create_task`` used, and then ``add_comment``, does not
  collide.
* K4. ``created_by`` is the member, and ``meta.via`` is ``mcp:<slug>``.
* K5. Each ``parent_id`` refusal maps to a fixed text and frees the slot.
* K6. A hidden task gives "Task not found." and frees the slot.
* K7. A mention of a member who can see the task writes one
  ``pm_notifications`` row, with ``actor`` the member.
* K8. A body over the cap is refused at the edge with no request. The
  gateway refuses it for a signed agent request too, and not for a member.
* K9. The tool is hidden while writes are off, and a call is refused with no
  request.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_comments.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``E`` is
``apps/services/agent_edge/agent_edge``, ``G`` is
``apps/services/gateway/gateway/routes/projects`` and ``A`` is
``packages/acb_auth/acb_auth``.

* C1. ``E/tools.py``: ``_add_comment`` skips the claim. ``test_k1_one_key_makes_one_row_and_the_replay_gives_its_id`` and ``test_the_replay_sends_nothing``.
* C2. ``E/idempotency.py``: the slot key leaves out the tool name. ``test_k3_a_key_of_create_task_does_not_collide`` (both halves).
* C3. ``E/tools.py``: ``_claim_slot`` drops the fingerprint check. ``test_k2_one_key_with_other_inputs_is_refused`` (both halves).
* C4. ``E/tools.py``: ``_keyed_write`` marks a clear refusal ``unknown``. ``test_k5_each_parent_refusal_has_its_text_and_frees_the_slot`` and ``test_k6_a_hidden_task_is_not_found_and_frees_the_slot``.
* C5. ``E/tools.py``: ``_post_comment`` sends no ``details``. The same K5 test.
* C6. ``E/tools.py``: the body schema loses ``maxLength``. ``test_k8_a_body_over_the_cap_sends_nothing`` (both halves).
* C7. ``G/activities.py``: no cap for an agent. ``test_k8_the_gateway_refuses_a_long_agent_body``.
* C8. ``E/tools.py``: ``comment`` copies every key of the row. ``test_it_posts_the_named_keys_and_pins_the_output``.
* C9. ``E/tools.py``: ``offered`` shows ``add_comment`` while writes are off. ``test_k9_the_tool_is_hidden_and_refused_while_writes_are_off`` (both halves).
* C10. ``G/core.py``: ``record_activity`` stamps no ``via``. ``test_k4_the_author_is_the_member_and_via_names_the_client``.
* C11. ``G/activities.py``: the mention ``notify`` names no actor. ``test_k7_a_mention_notifies_a_member_who_can_see_the_task``.
* C12. ``A/agent_oauth.py``: the comment pair leaves ``MCP_WRITE_ROUTES``. ``test_k1_one_key_makes_one_row_and_the_replay_gives_its_id`` and ``test_mcp_edge_coverage.py::test_the_write_pairs_are_those_of_ea_6a_ea_6d_and_ea_12a``.
* C13. ``E/tools.py``: no blank-body check. ``test_a_blank_or_nul_body_sends_nothing``.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth import agent_tokens
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings, tenant_redis
from acb_common.tenant_redis import TenantRedis
from agent_edge import gateway_client, idempotency, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder

_DB_GATE = w._DB_GATE
_uuid = w._uuid
GOOD, KEY, ORG, CONN, VIA = w.GOOD, w.KEY, w.ORG, w.CONN, w.VIA
TASK, V1 = w.TASK, w.V1
COMMENT = _uuid(6001)
ROOT = _uuid(6002)
IDEM = "comment-key-0001"
WRITES_OFF = "Changes are turned off for agents in this organization."

_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text
COMMENTS_PATH = f"/projects/tasks/{TASK}/comments"


def _args(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"task_id": TASK, "body": "Ran the tests. 3 fail.",
                            "idempotency_key": IDEM}
    args.update(kw)
    return args


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free
# ═══════════════════════════════════════════════════════════════════════════


class _Gateway(tw._Gateway):
    """The EA-6d fake gateway, with the comment route."""

    def __init__(self) -> None:
        super().__init__()
        self.comment_hold: asyncio.Event | None = None
        self.commenting = asyncio.Event()

    async def __call__(self, scope, receive, send) -> None:
        if (scope["method"], scope["path"]) == ("POST", COMMENTS_PATH):
            self.commenting.set()
            if self.comment_hold is not None:
                await self.comment_hold.wait()
        await super().__call__(scope, receive, send)

    def _answer(self, r: SimpleNamespace) -> tuple[int, Any]:
        if (r.method, r.path) in self.answers:
            return self.answers[(r.method, r.path)]
        if (r.method, r.path) == ("POST", COMMENTS_PATH):
            sent = json.loads(r.body)
            return 201, {
                "id": COMMENT, "task_id": TASK, "project_id": None,
                "parent_id": sent.get("parent_id"), "type": "comment",
                "body": sent["body"].strip(), "meta": {"via": VIA},
                "created_by": "m@x.test", "created_at": V1, "updated_at": V1,
                "not_notified": ["hidden@x.test"]}
        return super()._answer(r)

    def comment_posts(self) -> list[SimpleNamespace]:
        return [r for r in self.requests if (r.method, r.path) == ("POST", COMMENTS_PATH)]


@pytest.fixture
def redis_fake(monkeypatch) -> tw._FakeRedis:
    fake = tw._FakeRedis()
    monkeypatch.setattr(tenant_redis, "get_tenant_redis", lambda **_: TenantRedis(fake))
    return fake


@pytest.fixture
def edge(monkeypatch, redis_fake) -> SimpleNamespace:
    found: dict[str, Any] = {GOOD: w._lookup()}

    async def lookup(raw: str, *, kind: str = "access"):
        return found.get(raw)

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


def _slot_name(org: str, connection: str, key: str, tool: str = "add_comment") -> str:
    caller = SimpleNamespace(organization_id=org, connection_id=connection)
    with tenant_redis.organization_scope(org):
        return idempotency.Slot(caller, key, tool=tool)._key().value  # type: ignore[arg-type]


class TestTheToolRecord:
    """§9.2 and §9.5, database-free."""

    async def test_tools_list_shows_the_tool_with_the_write_hints(self, edge):
        listed = {t["name"]: t for t in
                  (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
        assert listed["add_comment"]["annotations"] == {
            "title": "Add a comment", "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False}
        assert listed["add_comment"]["inputSchema"]["required"] == [
            "task_id", "body", "idempotency_key"]

    def test_the_description_says_when_to_use_it_and_who_hears(self):
        text_ = tools.ADD_COMMENT.description
        for needed in ("short progress notes", "add_task_reference", "post_agent_activity",
                       "a response", "notifies the watchers and the assignees",
                       "not by email", "pm.task.comment_added", "published workflow",
                       "idempotency_key", "untrusted_text"):
            assert needed in text_, needed

    def test_the_caps_of_the_edge_and_the_gateway_are_equal(self):
        from gateway.routes.projects.activities import AGENT_MAX_COMMENT_CHARS

        assert tools.MAX_COMMENT_CHARS == AGENT_MAX_COMMENT_CHARS == 20_000
        assert tools.ADD_COMMENT.input_schema["properties"]["body"]["maxLength"] == 20_000

    async def test_check_8_refuses_without_projects_write(self, edge):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call("add_comment", _args()))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    async def test_k9_the_tool_is_hidden_and_refused_while_writes_are_off(
            self, edge, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        listed = (await _post(_rpc("tools/list"))).json()["result"]["tools"]
        assert "add_comment" not in {t["name"] for t in listed}
        assert _text(await _tool("add_comment", _args())) == WRITES_OFF
        assert edge.gateway.requests == []
        assert edge.redis.calls == []


class TestAddCommentAtTheEdge:
    """``add_comment``, database-free."""

    async def test_it_posts_the_named_keys_and_pins_the_output(self, edge):
        result = await _tool("add_comment", _args(body="  Done with step 1  ",
                                                  parent_id=ROOT))
        assert result["isError"] is False, result
        [post] = edge.gateway.comment_posts()
        assert (post.query, post.headers.get("if-match")) == ("", None)
        assert json.loads(post.body) == {"body": "  Done with step 1  ", "parent_id": ROOT}
        body = result["structuredContent"]
        assert tuple(body) == tools.COMMENT_KEYS
        assert body == {"id": COMMENT, "task_id": TASK, "parent_id": ROOT,
                        "body": {"untrusted_text": "Done with step 1"},
                        "created_by": "m@x.test", "created_at": V1, "created": True}
        assert "not_notified" not in json.dumps(result)
        assert [r.method for r in edge.gateway.requests] == ["POST"], "no read"

    async def test_k8_a_body_over_the_cap_sends_nothing(self, edge):
        result = await _tool("add_comment", _args(body="x" * 20_001))
        assert _text(result) == "The input 'body' is too long."
        assert edge.gateway.requests == [] and edge.redis.calls == []
        ok = await _tool("add_comment", _args(body="x" * 20_000))
        assert ok["isError"] is False, ok

    @pytest.mark.parametrize("body,expected", [
        ("   \n\t ", tools.TEXT_COMMENT_BLANK),
        ("bad \x00 byte", tools.TEXT_COMMENT_NUL)])
    async def test_a_blank_or_nul_body_sends_nothing(self, edge, body, expected):
        assert _text(await _tool("add_comment", _args(body=body))) == expected
        assert edge.gateway.requests == [] and edge.redis.calls == []

    @pytest.mark.parametrize("args,name", [
        ({"idempotency_key": None}, "idempotency_key"),
        ({"idempotency_key": "short"}, "idempotency_key"),
        ({"parent_id": "not-an-id"}, "parent_id"),
        ({"author": "x@y.z"}, "author"),
        ({"body": ""}, "body"),
    ])
    async def test_an_input_outside_the_schema_is_an_input_error(self, edge, args, name):
        result = await _tool("add_comment", {k: v for k, v in _args(**args).items()
                                             if v is not None})
        assert name in _text(result)
        assert edge.gateway.requests == []

    @pytest.mark.parametrize("status,detail,expected", [
        (404, "Task not found", "Task not found."),
        (404, "Comment not found", tools.TEXT_PARENT_COMMENT_NOT_FOUND),
        (422, "A reply must be on the same task as the comment it answers.",
         tools.TEXT_PARENT_COMMENT_OTHER_TASK),
        (422, "You can only reply to a comment.", tools.TEXT_PARENT_NOT_A_COMMENT),
        (422, "Replies go one level deep. Reply to the first comment instead.",
         tools.TEXT_PARENT_TOO_DEEP),
        (422, "SECRET", tools.TEXT_BAD_REQUEST),
        (403, "SECRET", "This member cannot change that."),
        (429, "SECRET", "Too many changes. Wait a few minutes."),
    ])
    async def test_the_answer_table_frees_the_key(self, edge, status, detail, expected):
        edge.gateway.answers[("POST", COMMENTS_PATH)] = (status, {"detail": detail})
        result = await _tool("add_comment", _args(parent_id=ROOT))
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)
        assert edge.redis.slots() == {}, "a clear refusal frees the key"


class TestTheCommentKey:
    """The ``idempotency_key`` of ``add_comment``, database-free."""

    async def test_the_replay_sends_nothing(self, edge):
        first = await _tool("add_comment", _args())
        again = await _tool("add_comment", _args())
        assert first["structuredContent"]["created"] is True
        assert again["structuredContent"] == {"id": COMMENT, "task_id": TASK,
                                              "created": False}
        assert len(edge.gateway.requests) == 1, "the replay reads nothing"

    async def test_the_slot_names_the_tool_and_holds_no_raw_key(self, edge):
        await _tool("add_comment", _args())
        [(name, value)] = edge.redis.slots().items()
        assert name == _slot_name(ORG, CONN, IDEM)
        assert name.startswith(f"cc:{ORG}:{idempotency.NAMESPACE}:{CONN}:add_comment:")
        assert IDEM not in name and IDEM not in json.dumps(value)
        assert (value["state"], value["id"]) == ("done", COMMENT)

    async def test_k2_one_key_with_other_inputs_is_refused(self, edge):
        await _tool("add_comment", _args())
        other = await _tool("add_comment", _args(body="another note"))
        assert _text(other) == tools.TEXT_COMMENT_KEY_REUSED
        assert len(edge.gateway.comment_posts()) == 1

    async def test_k3_a_key_of_create_task_does_not_collide(self, edge):
        made = await _tool("create_task", {"project_id": w.PROJECT, "title": "t",
                                           "idempotency_key": IDEM})
        said = await _tool("add_comment", _args())
        assert made["structuredContent"]["created"] is True
        assert said["structuredContent"]["created"] is True
        assert len(edge.gateway.comment_posts()) == 1
        assert len(edge.redis.slots()) == 2

    async def test_two_calls_at_once_add_one_comment(self, edge):
        edge.gateway.comment_hold = asyncio.Event()
        first = asyncio.create_task(_tool("add_comment", _args()))
        await asyncio.wait_for(edge.gateway.commenting.wait(), 5)
        second = await _tool("add_comment", _args())
        assert _text(second) == tools.TEXT_KEY_RUNNING
        edge.gateway.comment_hold.set()
        assert (await first)["structuredContent"]["created"] is True
        assert len(edge.gateway.comment_posts()) == 1

    @pytest.mark.parametrize("status", [500, 503])
    async def test_a_post_with_no_clear_answer_keeps_the_key(self, edge, status):
        edge.gateway.answers[("POST", COMMENTS_PATH)] = (status, {"detail": "x"})
        assert _text(await _tool("add_comment", _args())) == tools.TEXT_BUSY
        del edge.gateway.answers[("POST", COMMENTS_PATH)]
        assert _text(await _tool("add_comment", _args())) == tools.TEXT_COMMENT_KEY_UNKNOWN
        assert len(edge.gateway.comment_posts()) == 1

    async def test_a_post_that_never_left_the_edge_frees_the_key(self, edge):
        inner = httpx.ASGITransport(app=edge.gateway)

        class _Refusing(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                if (request.method, request.url.path) == ("POST", COMMENTS_PATH):
                    raise httpx.ConnectError("refused", request=request)
                return await inner.handle_async_request(request)

        with gateway_client.use_transport(_Refusing()):
            assert _text(await _tool("add_comment", _args())) == tools.TEXT_BUSY
        assert edge.redis.slots() == {}
        assert (await _tool("add_comment", _args()))["structuredContent"]["created"] is True

    async def test_a_store_outage_sends_no_write(self, edge):
        from redis.exceptions import ConnectionError as RedisConnectionError

        edge.redis.fail = RedisConnectionError("refused")
        assert _text(await _tool("add_comment", _args())) == tools.TEXT_BUSY
        assert edge.gateway.requests == []


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpcomment"
_APP_PW = "mcpcomment_scratch_pw"
MEMBER = w.MEMBER
PEER = "peer@mcpwrites.test"
STRANGER = "stranger@mcpwrites.test"


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, two members, a project that
    both can see, and a project that the agent's member cannot see."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpcomment"
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
                       "VALUES (:id, 'mcpcomment', 'mcpcomment')"), {"id": org})
    a = w._Seeder(eng, org)
    # The gateway numbers the tasks that it makes from 1 (`pm_task_counters`).
    # The seeded tasks start far above, so the two never meet (as in EA-6d).
    a.number = 100_000
    a.member(MEMBER, ["feature:projects"])
    a.member(PEER, ["feature:projects"])
    a.member(STRANGER, ["feature:projects"])
    team = a.project("Team")
    a.one("INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id)"
          " VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid)) RETURNING project_id",
          p=team, s=PEER, a=w.OWNER, o=org)
    # A project with no grant for the agent's member.
    hidden = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses) VALUES ('Hidden', 'active', 'manual', :a,"
        " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id", a=w.OWNER, o=org))
    todo = a.status(team, "To do", "todo", 0)
    hidden_todo = a.status(hidden, "To do", "todo", 0)
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, hidden=hidden,
                             todo=todo, hidden_todo=hidden_todo, c={}, tok={},
                             task=lambda title: a.task(team, todo, title))
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def live_app(monkeypatch, world, redis_fake):
    """The real gateway app at ``agent_edge_gateway_url``, writes on."""
    from gateway.main import app as gateway_app

    settings = get_settings()
    for name, value in (
            ("agent_access_enabled", True), ("agent_access_writes_enabled", True),
            ("agent_access_orgs", "*"), ("agent_edge_signing_key", KEY),
            ("agent_edge_signing_key_previous", ""), ("agent_issuer", w.ISSUER),
            ("agent_resource", w.RESOURCE),
            ("agent_edge_gateway_url", "http://gateway.test")):
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", w.INTERNAL)
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


@pytest.fixture
async def live(live_app):
    """:func:`live_app`, after one warm-up GET (see the EA-6d module)."""
    if not getattr(live_app, "warmed", False):
        r = await tw._send(live_app, "GET", "/projects/tree")
        assert r.status_code == 200, r.text
        live_app.warmed = True
    return live_app


async def _comment(world, **arguments: Any) -> dict[str, Any]:
    return await tw._mcp(world, "write", "add_comment", arguments)


def _rows(world, body: str) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT * FROM pm_activities WHERE type = 'comment' AND body = :b"
            " ORDER BY created_at"), {"b": body}))


def _key_is_free(world, key: str) -> bool:
    return _slot_name(world.org, world.c["write"], key) not in world.redis.store


class TestAddCommentOnARealDatabase:
    """K1 to K9 through the real gateway app."""

    pytestmark = _DB_GATE

    async def test_k1_one_key_makes_one_row_and_the_replay_gives_its_id(self, live):
        task = live.task("k1")
        first = await _comment(live, task_id=task, body="k1 note", idempotency_key="k1-key-0001")
        again = await _comment(live, task_id=task, body="k1 note", idempotency_key="k1-key-0001")
        assert first["isError"] is False, first
        made = first["structuredContent"]
        assert again["structuredContent"] == {"id": made["id"], "task_id": task,
                                              "created": False}
        [row] = _rows(live, "k1 note")
        assert str(row.id) == made["id"] and str(row.task_id) == task

    async def test_k1_two_calls_at_once_with_one_key_make_one_row(self, live):
        task = live.task("k1 race")
        args = {"task_id": task, "body": "raced note", "idempotency_key": "k1-race-001"}

        async def both() -> list[dict[str, Any]]:
            return list(await asyncio.gather(*(
                tw._mcp_unscoped(live, "write", "add_comment", args) for _ in range(2))))

        results = await tw._in_scope(live, both)
        assert len(_rows(live, "raced note")) == 1
        assert sorted(r["isError"] for r in results) == [False, True]
        loser = next(r for r in results if r["isError"])
        assert loser["content"][0]["text"] == tools.TEXT_KEY_RUNNING

    async def test_k2_one_key_with_other_inputs_is_refused(self, live):
        task = live.task("k2")
        await _comment(live, task_id=task, body="k2 first", idempotency_key="k2-key-0001")
        other = await _comment(live, task_id=task, body="k2 other",
                               idempotency_key="k2-key-0001")
        assert other["content"][0]["text"] == tools.TEXT_COMMENT_KEY_REUSED
        assert (len(_rows(live, "k2 first")), len(_rows(live, "k2 other"))) == (1, 0)

    async def test_k3_a_key_of_create_task_does_not_collide(self, live):
        made = await tw._mcp(live, "write", "create_task", {
            "project_id": live.team, "title": "k3 task", "idempotency_key": "k3-shared-01"})
        assert made["structuredContent"]["created"] is True, made
        task = made["structuredContent"]["task"]["id"]
        said = await _comment(live, task_id=task, body="k3 note",
                              idempotency_key="k3-shared-01")
        assert said["structuredContent"]["created"] is True, said
        assert len(_rows(live, "k3 note")) == 1

    async def test_k4_the_author_is_the_member_and_via_names_the_client(self, live):
        task = live.task("k4")
        said = await _comment(live, task_id=task, body="k4 note", idempotency_key="k4-key-0001")
        assert said["structuredContent"]["created_by"] == MEMBER
        [row] = _rows(live, "k4 note")
        assert row.created_by == MEMBER
        assert (row.meta or {}).get("via") == w.R8_VIA

    async def test_k5_each_parent_refusal_has_its_text_and_frees_the_slot(self, live):
        task, other_task = live.task("k5"), live.task("k5 other")
        root = (await _comment(live, task_id=task, body="k5 root",
                               idempotency_key="k5-root-001"))["structuredContent"]["id"]
        reply = (await _comment(live, task_id=task, body="k5 reply", parent_id=root,
                                idempotency_key="k5-reply-01"))["structuredContent"]
        assert reply["parent_id"] == root
        foreign = (await _comment(live, task_id=other_task, body="k5 foreign",
                                  idempotency_key="k5-foreign1"))["structuredContent"]["id"]
        event = str(live.seeder.one(
            "INSERT INTO pm_activities (type, task_id, body, created_by, organization_id)"
            " VALUES ('status_change', CAST(:t AS uuid), 'moved', :a, CAST(:o AS uuid))"
            " RETURNING id", t=task, a=w.OWNER, o=live.org))
        cases = [
            (str(uuid.uuid4()), tools.TEXT_PARENT_COMMENT_NOT_FOUND),
            (foreign, tools.TEXT_PARENT_COMMENT_OTHER_TASK),
            (event, tools.TEXT_PARENT_NOT_A_COMMENT),
            (reply["id"], tools.TEXT_PARENT_TOO_DEEP),
        ]
        for n, (parent, expected) in enumerate(cases):
            key = f"k5-refused-{n}"
            refused = await _comment(live, task_id=task, body=f"k5 refused {n}",
                                     parent_id=parent, idempotency_key=key)
            assert refused["content"][0]["text"] == expected, (n, refused)
            assert _key_is_free(live, key), n
            assert _rows(live, f"k5 refused {n}") == []
            # The freed key works for a good call.
            ok = await _comment(live, task_id=task, body=f"k5 refused {n}", parent_id=root,
                                idempotency_key=key)
            assert ok["structuredContent"]["created"] is True, (n, ok)

    async def test_k6_a_hidden_task_is_not_found_and_frees_the_slot(self, live):
        hidden = live.seeder.task(live.hidden, live.hidden_todo, "k6 hidden")
        refused = await _comment(live, task_id=hidden, body="k6 note",
                                 idempotency_key="k6-key-0001")
        assert refused["content"][0]["text"] == "Task not found."
        assert _key_is_free(live, "k6-key-0001")
        assert _rows(live, "k6 note") == []

    async def test_k7_a_mention_notifies_a_member_who_can_see_the_task(self, live):
        task = live.task("k7")
        said = await _comment(live, task_id=task, body=f"k7 see @{PEER} and @{STRANGER}",
                              idempotency_key="k7-key-0001")
        assert said["isError"] is False, said
        assert "not_notified" not in json.dumps(said)
        with live.admin.begin() as c:
            rows = list(c.execute(text(
                "SELECT recipient, kind, actor FROM pm_notifications"
                " WHERE task_id = CAST(:t AS uuid)"), {"t": task}))
        assert [(r.recipient, r.kind, r.actor) for r in rows] == [(PEER, "mention", MEMBER)]

    async def test_k8_a_body_over_the_cap_sends_nothing(self, live):
        task = live.task("k8")
        body = "k8" + "x" * 19_999
        refused = await _comment(live, task_id=task, body=body, idempotency_key="k8-key-0001")
        assert refused["content"][0]["text"] == "The input 'body' is too long."
        assert _rows(live, body) == []
        assert _key_is_free(live, "k8-key-0001")

    async def test_k8_the_gateway_refuses_a_long_agent_body(self, live):
        task = live.task("k8 gateway")
        path = f"/projects/tasks/{task}/comments"
        long_ = "g" * 20_001
        signed = await tw._send(live, "POST", path, body={"body": long_})
        assert signed.status_code == 422, signed.text
        assert _rows(live, long_) == []
        at_cap = await tw._send(live, "POST", path, body={"body": "g" * 20_000})
        assert at_cap.status_code == 201, at_cap.text
        member = await tw._send(live, "POST", path, body={"body": long_}, signed=None)
        assert member.status_code == 201, "a member's comment has no cap"

    async def test_k9_the_tool_is_hidden_and_refused_while_writes_are_off(
            self, live, monkeypatch):
        task = live.task("k9")
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)

        async def listed() -> list[str]:
            from agent_edge import app as edge_app_module

            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                    base_url=w.ISSUER) as client:
                r = await client.post("/mcp", content=json.dumps(_rpc("tools/list")),
                                      headers={"authorization": f"Bearer {live.tok['write']}",
                                               "content-type": "application/json",
                                               "mcp-protocol-version": "2025-11-25"})
            return [t["name"] for t in r.json()["result"]["tools"]]

        assert "add_comment" not in await tw._in_scope(live, listed)
        refused = await _comment(live, task_id=task, body="k9 note",
                                 idempotency_key="k9-key-0001")
        assert refused["content"][0]["text"] == WRITES_OFF
        assert _rows(live, "k9 note") == []
        assert live.redis.slots() == {}
