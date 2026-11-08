"""WS-55 MCP-G9 — ``update_comment`` and ``delete_comment`` (Metorite task 843).

Spec: ``project-docs/specs/external_agent_access.md`` §9.2 ("Rules of
``update_comment`` and ``delete_comment``"), §9.5 rules 2 and 4, and §15 row
MCP-G9.

**The rule.** A person changes a comment as before: the author only, and any
other comment is the one 404 (``activities._load_own_comment``). A SIGNED agent
changes a comment only when the member wrote it AND its ``meta.via`` is the
stamp of the connection's own client (``AgentScope.actor_via``, the stamp that
``record_activity`` wrote on the agent's own comment). The comment must sit on
a task in the reach of the connection. Each change writes one ``system`` row
on the task with the actor and the stamp, and with no text of the comment.

**R8.** The real edge app takes a real token from the real store, signs each
call, and branch 0 of the REAL gateway app admits it as the member. The
routes run as a NON-privileged role on a promoted catalog of their own, so
FORCE RLS binds every read. One organization holds MEMBER and PEER, a team
project that both see, and a second project that MEMBER sees and the pinned
connection does not reach. MEMBER has three connections: ``write`` and
``pinned`` (one client, so one stamp) and ``other`` (another client). A
second organization holds a comment of MEMBER with the stamp of ``write``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_comment_edit_r8.py -v -rs

**The mutations (R7).** ``G`` is ``apps/services/gateway/gateway/routes/projects``.

* G1. ``G/activities.py``: ``_load_agent_comment`` drops ``or stamp != via``.
  ``test_r2_a_comment_that_the_member_typed_is_refused`` and
  ``test_r3_a_comment_of_another_client_is_refused`` turn red.
* G2. ``G/activities.py``: ``_load_agent_comment`` drops the
  ``load_visible_task`` check. ``test_p1_a_comment_outside_the_pin_is_not_found``
  turns red.
* G3. ``G/activities.py``: ``_load_agent_comment`` drops the author check.
  ``test_r1_a_comment_of_another_member_is_refused`` (the case with the
  same stamp) turns red.
* G4. ``G/activities.py``: the edit and the delete skip
  ``_record_agent_change``. ``test_c1_an_edit_of_its_own_comment_writes_one_row``
  and ``test_c2_a_delete_of_its_own_comment_writes_one_row`` turn red.

Fix round 1 adds rules 1 and 2 (spec §9.2) and three more mutations.

* G5. ``G/activities.py``: ``edit_comment`` skips ``_mark_person_edit``.
  ``test_e1_a_person_edit_ends_the_agent_change`` (both tools) and
  ``test_e2_a_person_edit_of_a_person_comment_keeps_its_keys`` turn red.
* G6. ``G/activities.py``: ``_load_agent_comment`` drops the check of
  ``PERSON_EDITED_KEY``. ``test_e1_a_person_edit_ends_the_agent_change``
  turns red.
* G7. ``G/activities.py``: ``_load_agent_comment`` drops the check of
  ``RUN_COMMENT_KEY``. ``test_q1_a_comment_of_an_agent_run_stays_as_written``
  turns red.

Fix round 2 adds rule 3 (a reply of somebody else) and the tests of the
order (O1 to O4, which are 404 by the checks that come first). Fix round 3
makes rule 3 the last word: any later comment on the task that is not this
client's own (L1 to L6).

* G8. ``G/activities.py``: ``_load_agent_comment`` drops the call of
  ``_has_later_comment``. ``test_y1_a_reply_of_somebody_else_ends_the_agent_change``,
  ``test_y2_an_edited_or_a_deleted_reply_counts`` and L1, L2, L4 and L5 turn
  red.
* G9. ``G/activities.py``: ``_load_agent_comment`` drops ``lock_task``.
  ``test_l6_the_task_lock_comes_before_the_last_word_check`` turns red.
* G10. ``G/activities.py``: the rule of fix round 2 (direct replies only).
  ``test_l1_a_reply_beside_the_agent_reply_refuses``,
  ``test_l2_a_new_comment_of_a_peer_refuses``,
  ``test_l4_a_later_comment_of_the_member_in_the_app_refuses`` and
  ``test_l5_a_later_comment_that_the_peer_deleted_refuses`` turn red.

Fix round 4: a comment of an agent run is never the agent's own (L7), and a
transient SQLSTATE of the agent path answers 409.

* G11. ``G/activities.py``: ``_LATER_COMMENT_SQL`` drops the clause on
  ``RUN_COMMENT_KEY``. ``test_l7_an_outside_decision_after_the_comment_refuses``
  turns red.
* G12. ``G/activities.py``: ``_agent_comment_race_as_409`` re-raises a
  transient error. ``test_projects_comments.py``
  ``test_the_agent_race_guard_answers_each_transient_sqlstate_with_409``
  turns red.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_signing, agent_tokens
from acb_common import get_settings
from agent_edge import tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_comments as cm
from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w

_DB_GATE = w._DB_GATE
# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_APP_ROLE = "acb_app_mcpg9"
_APP_PW = "mcpg9_scratch_pw"
MEMBER = w.MEMBER
OWNER = w.OWNER
PEER = "peer@mcpwrites.test"
R8_VIA = w.R8_VIA
OTHER_ORIGIN = "https://other.example"
OTHER_CLIENT = "https://other.example/tool.json"
OTHER_VIA = agent_oauth.actor_via_for(OTHER_ORIGIN, client_id=OTHER_CLIENT)
CHAT_VIA = "chat:projects-assistant"
NOT_OWN = tools.TEXT_COMMENT_NOT_OWN
NOT_FOUND = tools.TEXT_COMMENT_NOT_FOUND

redis_fake = tw.redis_fake
live_app = tw.live_app
edge = cm.edge


def _other_client_connection(seeder: Any) -> tuple[str, str]:
    """A live connection of MEMBER through ANOTHER client, and one token."""
    cid = str(seeder.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, :client, 'Other', :origin,"
        " CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
        " now() + interval '1 day') RETURNING id",
        o=seeder.org, m=MEMBER, client=OTHER_CLIENT, origin=OTHER_ORIGIN,
        scopes=list(w.WRITE_SCOPES), pins=[]))
    raw = agent_tokens.new_token()
    seeder.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
               " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
               " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour', :client,"
               " :r, :s) RETURNING id",
               o=seeder.org, c=cid, h=agent_tokens.hash_token(raw), client=OTHER_CLIENT,
               r=w.RESOURCE, s=" ".join(w.WRITE_SCOPES))
    return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog with the two tenants of the docstring."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpg9"
    w._clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
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

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "mcpg9"), (org_b, "mcpg9-other")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    # The gateway numbers the tasks that it makes from 1. The seeded tasks
    # start far above, so the two never meet (as in EA-6d).
    a.number = 100_000
    a.member(MEMBER, ["feature:projects"])
    a.member(PEER, ["feature:projects"])
    team = a.project("Team", in_space=True)
    a.one("INSERT INTO pm_project_grants (project_id, subject, created_by, organization_id)"
          " VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid)) RETURNING project_id",
          p=team, s=PEER, a=OWNER, o=org)
    elsewhere = a.project("Elsewhere", in_space=True)
    todo = a.status(team, "To do", "todo", 0)
    # Fix round 1: an agent run asks on a task that is in progress.
    doing = a.status(team, "Doing", "in_progress", 1)
    elsewhere_todo = a.status(elsewhere, "To do", "todo", 0)
    elsewhere_doing = a.status(elsewhere, "Doing", "in_progress", 1)

    # The second tenant: a task and a comment of MEMBER with the stamp of
    # `write`. Only the tenant differs from a comment that the agent may change.
    b = w._Seeder(eng, org_b)
    b.number = 200_000
    b_project = b.project("Their team")
    b_task = b.task(b_project, b.status(b_project, "To do", "todo", 0), "theirs")
    foreign = str(b.one(
        "INSERT INTO pm_activities (type, task_id, body, meta, created_by, organization_id)"
        " VALUES ('comment', CAST(:t AS uuid), 'their words', CAST(:m AS jsonb), :a,"
        " CAST(:o AS uuid)) RETURNING id",
        t=b_task, m=json.dumps({"via": R8_VIA}), a=MEMBER, o=org_b))

    world_ = SimpleNamespace(
        admin=eng, org=org, seeder=a, team=team, elsewhere=elsewhere, c={}, tok={},
        task=lambda title: a.task(team, todo, title),
        doing_task=lambda title: a.task(team, doing, title),
        elsewhere_task=lambda title: a.task(elsewhere, elsewhere_todo, title),
        elsewhere_doing_task=lambda title: a.task(elsewhere, elsewhere_doing, title),
        foreign=foreign)
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.c["pinned"], world_.tok["pinned"] = a.connection(
        scopes=w.WRITE_SCOPES, pins=(team,))
    world_.c["other"], world_.tok["other"] = _other_client_connection(a)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
async def live(live_app):
    """:func:`live_app`, after one warm-up GET (see the EA-6d module)."""
    if not getattr(live_app, "warmed", False):
        r = await tw._send(live_app, "GET", "/projects/tree")
        assert r.status_code == 200, r.text
        live_app.warmed = True
    return live_app


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _tool(world, name: str, tool: str, **arguments: Any) -> dict[str, Any]:
    return await tw._mcp(world, name, tool, arguments)


async def _agent_comment(world, task: str, body: str, name: str = "write") -> str:
    """A comment that the connection *name* writes through ``add_comment``."""
    said = await _tool(world, name, "add_comment", task_id=task, body=body,
                       idempotency_key=f"add-{uuid.uuid4().hex[:12]}")
    assert said["isError"] is False, said
    return said["structuredContent"]["id"]


async def _person(world, email: str, method: str, path: str,
                  body: dict[str, Any] | None = None, *,
                  via: str | None = None) -> httpx.Response:
    """One request as the person *email*, through the internal bearer, as
    the web app and the chat send it. *via* is the chat's ``X-Actor-Via``."""
    from gateway.main import app as gateway_app

    sent = {"Authorization": f"Bearer {w.INTERNAL}", "X-User-Email": email}
    if via is not None:
        sent["X-Actor-Via"] = via
    payload = b""
    if body is not None:
        sent["Content-Type"] = "application/json"
        payload = json.dumps(body).encode()

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=sent, content=payload)
    return await tw._in_scope(world, go)


async def _person_comment(world, email: str, task: str, body: str,
                          via: str | None = None) -> str:
    r = await _person(world, email, "POST", f"/projects/tasks/{task}/comments",
                      {"body": body}, via=via)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _signed(world, name: str, method: str, path: str,
                  body: dict[str, Any] | None = None) -> httpx.Response:
    """One signed request of the connection *name*, with its own stamp."""
    from gateway.main import app as gateway_app

    via = OTHER_VIA if name == "other" else R8_VIA
    raw = json.dumps(body).encode() if body is not None else b"{}"
    sent = agent_signing.signed_headers(
        w.KEY, organization_id=world.org, connection_id=world.c[name], actor_via=via,
        method=method, path=path, query="", body=raw)
    sent["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=sent, content=raw)
    return await tw._in_scope(world, go)


def _comment(world, comment_id: str) -> Any:
    with world.admin.begin() as c:
        return c.execute(text("SELECT * FROM pm_activities WHERE id = CAST(:i AS uuid)"),
                         {"i": comment_id}).one()


def _change_rows(world, comment_id: str) -> list[Any]:
    """The ``system`` rows that name *comment_id* in their ``meta``."""
    with world.admin.begin() as c:
        return list(c.execute(text(
            "SELECT * FROM pm_activities WHERE type = 'system'"
            " AND meta->>'comment_id' = :i ORDER BY created_at"), {"i": comment_id}))


def _write_count(world, connection: str) -> int:
    """The counter of the write cap of *connection* in the Redis fake."""
    for key, (value, _) in world.redis.store.items():
        if agent_oauth.WRITE_CAP_NAMESPACE in key and key.endswith(connection):
            return int(value)
    return 0


def _key(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ── The tool records and the pinned copy ───────────────────────────────────


class TestTheToolRecords:
    """§9.2 and §9.5. The edge half of the slice."""

    def test_the_refusal_text_is_the_one_of_the_gateway(self):
        """The edge matches the gateway's 403 and 409 sentences and sends its
        own copy."""
        from gateway.routes.projects.activities import (
            AGENT_COMMENT_NOT_OWN,
            AGENT_COMMENT_RACE,
        )

        assert tools.COMMENT_NOT_OWN_DETAIL == AGENT_COMMENT_NOT_OWN
        assert tools.TEXT_COMMENT_NOT_OWN.startswith(AGENT_COMMENT_NOT_OWN)
        assert tools.COMMENT_RACE_DETAIL == AGENT_COMMENT_RACE

    def test_both_pairs_are_write_pairs_and_both_tools_need_projects_write(self):
        for method in ("PATCH", "DELETE"):
            assert (method, tools.COMMENT_ROUTE) in agent_oauth.MCP_WRITE_ROUTES
        for name in ("update_comment", "delete_comment"):
            tool = tools.TOOLS[name]
            assert tool.required_scope == agent_oauth.PROJECTS_WRITE, name
            assert tool.annotations["destructiveHint"] is True, name
            assert tool.edge_inputs == frozenset({"idempotency_key"}), name
        assert tools.UPDATE_COMMENT.method == "PATCH"
        assert tools.DELETE_COMMENT.method == "DELETE"
        assert tools.UPDATE_COMMENT.input_schema["required"] == [
            "comment_id", "body", "idempotency_key"]
        assert tools.DELETE_COMMENT.input_schema["required"] == [
            "comment_id", "idempotency_key"]
        assert tools.UPDATE_COMMENT.input_schema["properties"]["body"]["maxLength"] == (
            tools.MAX_COMMENT_CHARS)

    def test_the_descriptions_state_the_rule(self):
        for tool in (tools.UPDATE_COMMENT, tools.DELETE_COMMENT):
            text_ = tool.describe()["description"]
            for needed in ("this client wrote", "meta.via", "another member",
                           "another agent client", "Comment not found",
                           "idempotency_key", tools.write_limit_line().strip()):
                assert needed in text_, (tool.name, needed)
        assert "keeps no copy of the old text" in tools.UPDATE_COMMENT.description
        assert "cannot undo a delete" in tools.DELETE_COMMENT.description

    def test_this_suite_is_named_in_the_ci_skip_guard(self) -> None:
        """An R8 suite that CI does not list still skips in silence when the
        database gate is disarmed. Match the argument line, not a mention."""
        for workflow in ("pr-check.yml", "deploy.yml"):
            body = (_ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            assert re.search(r"^\s+tests/unit/test_mcp_edge_comment_edit_r8\.py \\?$",
                             body, re.M), workflow


class TestTheEdgeWithAFakeGateway:
    """The body that the edge sends, the pinned output and the key."""

    CID = w._uuid(6101)

    def _answers(self, edge) -> None:
        path = f"/projects/comments/{self.CID}"
        edge.gateway.answers[("PATCH", path)] = (200, {
            "id": self.CID, "task_id": w.TASK, "project_id": None, "parent_id": None,
            "type": "comment", "body": "fixed note", "meta": {"via": w.VIA},
            "created_by": "m@x.test", "created_at": w.V1, "updated_at": w.V2,
            "not_notified": ["hidden@x.test"]})
        edge.gateway.answers[("DELETE", path)] = (200, {"deleted": self.CID})

    async def test_update_sends_the_body_only_and_pins_the_output(self, edge):
        self._answers(edge)
        result = await w._tool("update_comment", {
            "comment_id": self.CID, "body": "fixed note", "idempotency_key": "upd-key-0001"})
        assert result["isError"] is False, result
        [sent] = edge.gateway.writes()
        assert (sent.method, sent.path) == ("PATCH", f"/projects/comments/{self.CID}")
        assert json.loads(sent.body) == {"body": "fixed note"}
        assert result["structuredContent"] == {
            "id": self.CID, "task_id": w.TASK, "parent_id": None,
            "body": {"untrusted_text": "fixed note"}, "created_by": "m@x.test",
            "created_at": w.V1, "updated_at": w.V2, "updated": True}

    async def test_delete_sends_an_empty_body(self, edge):
        self._answers(edge)
        result = await w._tool("delete_comment", {
            "comment_id": self.CID, "idempotency_key": "del-key-0001"})
        assert result["structuredContent"] == {"id": self.CID, "deleted": True}
        [sent] = edge.gateway.writes()
        assert (sent.method, sent.path, sent.body) == (
            "DELETE", f"/projects/comments/{self.CID}", b"{}")

    async def test_the_replay_sends_nothing(self, edge):
        self._answers(edge)
        args = {"comment_id": self.CID, "body": "fixed note",
                "idempotency_key": "upd-key-0002"}
        await w._tool("update_comment", args)
        again = await w._tool("update_comment", args)
        assert again["structuredContent"] == {"id": self.CID, "updated": False}
        other = await w._tool("update_comment", {**args, "body": "another text"})
        assert w._text(other) == tools.TEXT_COMMENT_CHANGE_KEY_REUSED
        gone = {"comment_id": self.CID, "idempotency_key": "del-key-0002"}
        await w._tool("delete_comment", gone)
        assert (await w._tool("delete_comment", gone))["structuredContent"] == {
            "id": self.CID, "deleted": False}
        assert len(edge.gateway.writes()) == 2

    @pytest.mark.parametrize("body,expected", [
        ("   ", tools.TEXT_COMMENT_BLANK),
        ("cut\x00off", tools.TEXT_COMMENT_NUL),
    ])
    async def test_a_blank_or_nul_body_sends_nothing(self, edge, body, expected):
        result = await w._tool("update_comment", {
            "comment_id": self.CID, "body": body, "idempotency_key": "upd-key-0003"})
        assert w._text(result) == expected
        assert edge.gateway.requests == []
        assert edge.redis.slots() == {}

    @pytest.mark.parametrize("status,detail,expected", [
        (403, tools.COMMENT_NOT_OWN_DETAIL, tools.TEXT_COMMENT_NOT_OWN),
        (403, "something else", tools.TEXT_WRITE_FORBIDDEN),
        (404, "Comment not found", tools.TEXT_COMMENT_NOT_FOUND),
        (409, tools.COMMENT_RACE_DETAIL, tools.TEXT_COMMENT_RACE),
    ])
    async def test_each_refusal_has_its_text_and_frees_the_key(
            self, edge, status, detail, expected):
        for method in ("PATCH", "DELETE"):
            edge.gateway.answers[(method, f"/projects/comments/{self.CID}")] = (
                status, {"detail": detail})
        upd = await w._tool("update_comment", {
            "comment_id": self.CID, "body": "x", "idempotency_key": "upd-key-0004"})
        dele = await w._tool("delete_comment", {
            "comment_id": self.CID, "idempotency_key": "del-key-0004"})
        assert (w._text(upd), w._text(dele)) == (expected, expected)
        assert "something else" not in json.dumps([upd, dele])
        assert edge.redis.slots() == {}


# ── R8: the real gateway app ────────────────────────────────────────────────


class TestAnAgentChangesItsOwnComment:
    """C1 to C5: the comment of the agent's own client."""

    async def test_c1_an_edit_of_its_own_comment_writes_one_row(self, live):
        task = live.task("c1")
        cid = await _agent_comment(live, task, "c1 cut off by a rate li")
        before = _write_count(live, live.c["write"])
        said = await _tool(live, "write", "update_comment", comment_id=cid,
                           body="c1 the whole note", idempotency_key=_key("c1"))
        assert said["isError"] is False, said
        out = said["structuredContent"]
        assert (out["id"], out["task_id"], out["updated"]) == (cid, task, True)
        assert out["body"] == {"untrusted_text": "c1 the whole note"}
        assert _write_count(live, live.c["write"]) == before + 1
        row = _comment(live, cid)
        assert (row.body, row.created_by) == ("c1 the whole note", MEMBER)
        assert (row.meta or {}).get("via") == R8_VIA
        [change] = _change_rows(live, cid)
        assert change.body == "Edited a comment"
        assert change.created_by == MEMBER
        assert str(change.task_id) == task
        assert change.meta == {"via": R8_VIA, "action": "comment_edited",
                               "comment_id": cid}
        assert "rate li" not in json.dumps([change.body, change.meta])

    async def test_c2_a_delete_of_its_own_comment_writes_one_row(self, live):
        task = live.task("c2")
        cid = await _agent_comment(live, task, "c2 words to remove")
        said = await _tool(live, "write", "delete_comment", comment_id=cid,
                           idempotency_key=_key("c2"))
        assert said["structuredContent"] == {"id": cid, "deleted": True}, said
        row = _comment(live, cid)
        assert row.deleted_at is not None and row.body is None
        [change] = _change_rows(live, cid)
        assert change.body == "Deleted a comment"
        assert change.created_by == MEMBER
        assert change.meta == {"via": R8_VIA, "action": "comment_deleted",
                               "comment_id": cid}
        with live.admin.begin() as c:
            words = c.execute(text(
                "SELECT count(*) FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                " AND (body LIKE '%words to remove%' OR meta::text LIKE '%words to remove%')"),
                {"t": task}).scalar()
        assert words == 0, "a deleted comment's words must stay gone"

    async def test_c3_the_same_key_changes_nothing_more(self, live):
        task = live.task("c3")
        cid = await _agent_comment(live, task, "c3 first")
        key = _key("c3")
        first = await _tool(live, "write", "update_comment", comment_id=cid,
                            body="c3 second", idempotency_key=key)
        again = await _tool(live, "write", "update_comment", comment_id=cid,
                            body="c3 second", idempotency_key=key)
        assert first["structuredContent"]["updated"] is True
        assert again["structuredContent"] == {"id": cid, "updated": False}
        assert len(_change_rows(live, cid)) == 1
        gone = _key("c3d")
        await _tool(live, "write", "delete_comment", comment_id=cid, idempotency_key=gone)
        replay = await _tool(live, "write", "delete_comment", comment_id=cid,
                             idempotency_key=gone)
        assert replay["structuredContent"] == {"id": cid, "deleted": False}
        assert [r.body for r in _change_rows(live, cid)] == [
            "Edited a comment", "Deleted a comment"]

    async def test_c4_the_timeline_gives_the_id_and_the_stamp(self, live):
        """The agent finds its own comment: the id and ``meta.via``."""
        task = live.task("c4")
        cid = await _agent_comment(live, task, "c4 note")
        read = await _tool(live, "write", "get_task_timeline", task_id=task, kind="comments")
        [row] = [r for r in read["structuredContent"]["activities"] if r["id"] == cid]
        assert row["meta"]["via"] == R8_VIA
        assert row["body"] == {"untrusted_text": "c4 note"}

    async def test_c5_a_mention_added_by_the_edit_notifies(self, live):
        task = live.task("c5")
        cid = await _agent_comment(live, task, "c5 note")
        said = await _tool(live, "write", "update_comment", comment_id=cid,
                           body=f"c5 note, see @{PEER}", idempotency_key=_key("c5"))
        assert said["isError"] is False, said
        assert "not_notified" not in json.dumps(said)
        with live.admin.begin() as c:
            rows = list(c.execute(text(
                "SELECT recipient, kind, actor FROM pm_notifications"
                " WHERE task_id = CAST(:t AS uuid) AND kind = 'mention'"), {"t": task}))
        assert [(r.recipient, r.kind, r.actor) for r in rows] == [(PEER, "mention", MEMBER)]

    async def test_a_pinned_connection_changes_its_own_comment_in_the_pin(self, live):
        """The control of P1: the same client, a task inside the pin."""
        task = live.task("p0")
        cid = await _agent_comment(live, task, "p0 note", name="pinned")
        said = await _tool(live, "pinned", "update_comment", comment_id=cid,
                           body="p0 fixed", idempotency_key=_key("p0"))
        assert said["isError"] is False, said
        assert _comment(live, cid).body == "p0 fixed"


class TestAnAgentIsRefusedAnyOtherComment:
    """R1 to R3 (403) and P1, T1 (404). Each changes nothing and writes no row."""

    async def _refused(self, live, cid: str, expected: str, name: str = "write") -> None:
        before = _comment(live, cid)
        upd = await _tool(live, name, "update_comment", comment_id=cid,
                          body="an agent's rewrite", idempotency_key=_key("ref-u"))
        dele = await _tool(live, name, "delete_comment", comment_id=cid,
                           idempotency_key=_key("ref-d"))
        assert (w._text(upd), w._text(dele)) == (expected, expected)
        after = _comment(live, cid)
        assert (after.body, after.deleted_at, after.updated_at) == (
            before.body, before.deleted_at, before.updated_at)
        assert _change_rows(live, cid) == []
        # Each refusal frees its key: no slot of the two tools stays.
        assert not [k for k in live.redis.slots()
                    if ":update_comment:" in k or ":delete_comment:" in k]

    @pytest.mark.parametrize("via", [None, R8_VIA])
    async def test_r1_a_comment_of_another_member_is_refused(self, live, via):
        """With no stamp, and with the agent's own stamp: the author decides."""
        task = live.task("r1")
        cid = await _person_comment(live, PEER, task, "r1 peer words")
        if via is not None:
            live.seeder.one("UPDATE pm_activities SET meta = CAST(:m AS jsonb)"
                            " WHERE id = CAST(:i AS uuid) RETURNING id",
                            m=json.dumps({"via": via}), i=cid)
        await self._refused(live, cid, NOT_OWN)

    @pytest.mark.parametrize("via", [None, CHAT_VIA])
    async def test_r2_a_comment_that_the_member_typed_is_refused(self, live, via):
        """The member's own words in the app, and through the chat."""
        task = live.task("r2")
        cid = await _person_comment(live, MEMBER, task, "r2 typed by hand", via=via)
        assert (_comment(live, cid).meta or {}).get("via") == via
        await self._refused(live, cid, NOT_OWN)

    async def test_r3_a_comment_of_another_client_is_refused(self, live):
        assert OTHER_VIA not in ("", R8_VIA)
        task = live.task("r3")
        cid = await _agent_comment(live, task, "r3 other client", name="other")
        assert (_comment(live, cid).meta or {}).get("via") == OTHER_VIA
        await self._refused(live, cid, NOT_OWN)
        # The other client changes its own comment.
        ok = await _tool(live, "other", "update_comment", comment_id=cid,
                         body="r3 fixed by its client", idempotency_key=_key("r3"))
        assert ok["isError"] is False, ok
        [change] = _change_rows(live, cid)
        assert change.meta["via"] == OTHER_VIA

    async def test_r4_the_gateway_answers_403_with_the_sentence(self, live):
        task = live.task("r4")
        cid = await _person_comment(live, PEER, task, "r4 peer words")
        for method, body in (("PATCH", {"body": "x"}), ("DELETE", None)):
            r = await _signed(live, "write", method, f"/projects/comments/{cid}", body)
            assert r.status_code == 403, r.text
            assert r.json()["detail"] == tools.COMMENT_NOT_OWN_DETAIL

    async def test_p1_a_comment_outside_the_pin_is_not_found(self, live):
        """The same client wrote it, so only the pin refuses."""
        task = live.elsewhere_task("p1")
        cid = await _agent_comment(live, task, "p1 outside the pin")
        await self._refused(live, cid, NOT_FOUND, name="pinned")

    async def test_t1_a_comment_of_another_tenant_is_not_found(self, live):
        before = _comment(live, live.foreign)
        await self._refused(live, live.foreign, NOT_FOUND)
        assert _comment(live, live.foreign).body == before.body == "their words"

    async def test_a_missing_or_deleted_comment_is_not_found(self, live):
        task = live.task("gone")
        cid = await _agent_comment(live, task, "gone soon")
        await _tool(live, "write", "delete_comment", comment_id=cid,
                    idempotency_key=_key("gone"))
        for target in (cid, str(uuid.uuid4())):
            upd = await _tool(live, "write", "update_comment", comment_id=target,
                              body="x", idempotency_key=_key("gone-u"))
            assert w._text(upd) == NOT_FOUND

    async def test_the_gateway_caps_an_agent_edit(self, live):
        task = live.task("cap")
        cid = await _agent_comment(live, task, "cap note")
        path = f"/projects/comments/{cid}"
        long_ = "g" * (tools.MAX_COMMENT_CHARS + 1)
        r = await _signed(live, "write", "PATCH", path, {"body": long_})
        assert r.status_code == 422, r.text
        assert _comment(live, cid).body == "cap note"
        member = await _person(live, MEMBER, "PATCH", path, {"body": long_})
        assert member.status_code == 200, "a member's edit has no cap"

    async def test_the_tools_are_hidden_and_refused_while_writes_are_off(
            self, live, monkeypatch):
        task = live.task("off")
        cid = await _agent_comment(live, task, "off note")
        monkeypatch.setattr(get_settings(), "agent_access_writes_enabled", False)
        listed = await tw._in_scope(live, lambda: _listed(live))
        assert not {"update_comment", "delete_comment"} & set(listed)
        for tool, extra in (("update_comment", {"body": "x"}), ("delete_comment", {})):
            refused = await _tool(live, "write", tool, comment_id=cid,
                                  idempotency_key=_key("off"), **extra)
            assert w._text(refused) == cm.WRITES_OFF
        assert _comment(live, cid).body == "off note"
        assert _change_rows(live, cid) == []


async def _listed(world) -> list[str]:
    from agent_edge import app as edge_app_module

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=w.ISSUER) as client:
        r = await client.post("/mcp", content=json.dumps(w._rpc("tools/list")),
                              headers={"authorization": f"Bearer {world.tok['write']}",
                                       "content-type": "application/json",
                                       "mcp-protocol-version": "2025-11-25"})
    return [t["name"] for t in r.json()["result"]["tools"]]


class TestAPersonIsUnchanged:
    """The web and the chat keep the rule of the author, and write no row."""

    async def test_a_person_edits_and_deletes_an_own_comment_with_no_row(self, live):
        task = live.task("h1")
        cid = await _person_comment(live, MEMBER, task, "h1 typed")
        path = f"/projects/comments/{cid}"
        edited = await _person(live, MEMBER, "PATCH", path, {"body": "h1 retyped"})
        assert edited.status_code == 200, edited.text
        assert _comment(live, cid).body == "h1 retyped"
        gone = await _person(live, MEMBER, "DELETE", path)
        assert gone.status_code == 200 and gone.json() == {"deleted": cid}
        assert _change_rows(live, cid) == []

    async def test_a_person_changes_a_comment_that_the_agent_wrote(self, live):
        """The member is the author of the agent's comment, as before."""
        task = live.task("h2")
        cid = await _agent_comment(live, task, "h2 by the agent")
        r = await _person(live, MEMBER, "PATCH", f"/projects/comments/{cid}",
                          {"body": "h2 by the member"})
        assert r.status_code == 200, r.text
        assert _change_rows(live, cid) == []

    async def test_a_person_gets_404_for_a_comment_of_another_member(self, live):
        task = live.task("h3")
        cid = await _person_comment(live, PEER, task, "h3 peer")
        for method, body in (("PATCH", {"body": "x"}), ("DELETE", None)):
            r = await _person(live, MEMBER, method, f"/projects/comments/{cid}", body)
            assert r.status_code == 404, r.text
        assert _comment(live, cid).body == "h3 peer"

    @pytest.mark.parametrize("via", [None, CHAT_VIA])
    async def test_e2_a_person_edit_of_a_person_comment_keeps_its_keys(self, live, via):
        """Rule 1 changes nothing else for a person. The edit works, again and
        again, and the jsonb merge keeps the chat's ``via``."""
        task = live.task("e2")
        cid = await _person_comment(live, MEMBER, task, "e2 typed", via=via)
        path = f"/projects/comments/{cid}"
        for words in ("e2 retyped", "e2 retyped again"):
            r = await _person(live, MEMBER, "PATCH", path, {"body": words}, via=via)
            assert r.status_code == 200, r.text
            assert _comment(live, cid).body == words
        kept = {"via": via} if via is not None else {}
        assert _comment(live, cid).meta == {**kept, "person_edited": True}
        assert _change_rows(live, cid) == []


# ── Fix round 1: rules 1 and 2 ──────────────────────────────────────────────


async def _refused_one(live, cid: str, tool: str, name: str = "write",
                       expected: str = NOT_OWN) -> None:
    """*tool* gets *expected* (the 403 text by default), and the comment and
    its timeline stay."""
    before = _comment(live, cid)
    extra = {"body": "an agent's rewrite"} if tool == "update_comment" else {}
    said = await _tool(live, name, tool, comment_id=cid,
                       idempotency_key=_key(f"fr1-{tool[:3]}"), **extra)
    assert w._text(said) == expected, said
    after = _comment(live, cid)
    assert (after.body, after.deleted_at, after.updated_at, after.meta) == (
        before.body, before.deleted_at, before.updated_at, before.meta)
    assert _change_rows(live, cid) == []
    assert not [k for k in live.redis.slots() if f":{tool}:" in k]


class TestFixRoundOne:
    """Rule 1: a person's edit ends the agent's rights over the comment.
    Rule 2: a comment of an agent run stays as written."""

    @pytest.mark.parametrize("tool", ["update_comment", "delete_comment"])
    @pytest.mark.parametrize("via", [None, CHAT_VIA])
    async def test_e1_a_person_edit_ends_the_agent_change(self, live, tool, via):
        """The agent writes, the member edits in the app (or the chat card
        ``edit_comment``), and a later agent call gets the 403."""
        task = live.task("e1")
        cid = await _agent_comment(live, task, "e1 cut off by a st")
        r = await _person(live, MEMBER, "PATCH", f"/projects/comments/{cid}",
                          {"body": "e1 the member's own words"}, via=via)
        assert r.status_code == 200, r.text
        row = _comment(live, cid)
        # The stamp of the agent stays. Only the mark refuses the agent.
        assert (row.created_by, row.meta) == (
            MEMBER, {"via": R8_VIA, "person_edited": True})
        await _refused_one(live, cid, tool)
        assert _comment(live, cid).body == "e1 the member's own words"
        signed = await _signed(live, "write",
                               "PATCH" if tool == "update_comment" else "DELETE",
                               f"/projects/comments/{cid}",
                               {"body": "x"} if tool == "update_comment" else None)
        assert signed.status_code == 403, signed.text
        assert signed.json()["detail"] == tools.COMMENT_NOT_OWN_DETAIL

    async def test_q1_a_comment_of_an_agent_run_stays_as_written(self, live, monkeypatch):
        """The agent asks, the member's answer is recorded, and the agent may
        not reword the question or the answer. Both carry the agent's own
        stamp, so only rule 2 refuses them."""
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        task = live.doing_task("q1")
        asked = await _tool(live, "write", "ask_on_task", task_id=task,
                            question="May I drop the staging rows?",
                            idempotency_key=_key("q1-ask"))
        assert asked["isError"] is False, asked
        run_id = asked["structuredContent"]["run"]["id"]
        decided = await _tool(live, "write", "record_outside_decision", run_id=run_id,
                              decision="Yes, drop the staging rows.",
                              where="Claude Code chat", idempotency_key=_key("q1-yes"))
        assert decided["isError"] is False, decided
        with live.admin.begin() as c:
            rows = list(c.execute(text(
                "SELECT id, body, meta, created_by FROM pm_activities"
                " WHERE type = 'comment' AND meta->>'agent_run_id' = :r"
                " ORDER BY created_at"), {"r": run_id}))
        assert [r.meta["agent_kind"] for r in rows] == ["elicitation", "response"]
        for row in rows:
            assert (row.created_by, row.meta["via"]) == (MEMBER, R8_VIA)
            for tool in ("update_comment", "delete_comment"):
                await _refused_one(live, str(row.id), tool)
        assert "staging rows" in _comment(live, str(rows[0].id)).body


# ── Fix round 2: rule 3 and the order ───────────────────────────────────────

COMMENT_TOOLS = ["update_comment", "delete_comment"]


async def _reply(live, root: str, task: str, who: str, words: str) -> str:
    """A reply to *root*: ``member`` and ``chat`` through the person path,
    ``peer`` as another member, ``other`` and ``own`` through ``add_comment``
    of the connection ``other`` or ``write``."""
    if who in ("member", "chat", "peer"):
        r = await _person(live, PEER if who == "peer" else MEMBER, "POST",
                          f"/projects/tasks/{task}/comments",
                          {"body": words, "parent_id": root},
                          via=CHAT_VIA if who == "chat" else None)
        assert r.status_code == 201, r.text
        return r.json()["id"]
    said = await _tool(live, "write" if who == "own" else "other", "add_comment",
                       task_id=task, body=words, parent_id=root,
                       idempotency_key=_key(f"reply-{who}"))
    assert said["isError"] is False, said
    return said["structuredContent"]["id"]


async def _run_question(live, task: str) -> str:
    """The comment of a question that the run of ``write`` asks on *task*."""
    asked = await _tool(live, "write", "ask_on_task", task_id=task,
                        question="May I drop the staging rows?",
                        idempotency_key=_key("ask"))
    assert asked["isError"] is False, asked
    run_id = asked["structuredContent"]["run"]["id"]
    with live.admin.begin() as c:
        [row] = list(c.execute(text(
            "SELECT id FROM pm_activities WHERE type = 'comment'"
            " AND meta->>'agent_run_id' = :r"), {"r": run_id}))
    return str(row.id)


class TestFixRoundTwo:
    """Rule 3: a comment with a reply that this client did not write stays as
    written. The order: a marked comment and a run comment answer 404, not
    403, after a delete or outside the pin."""

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    @pytest.mark.parametrize("who", ["member", "chat", "peer", "other"])
    async def test_y1_a_reply_of_somebody_else_ends_the_agent_change(
            self, live, tool, who):
        """The agent asks, somebody replies "Yes", and the agent may not
        reword the question to "production rows"."""
        task = live.task(f"y1 {who}")
        root = await _agent_comment(live, task, "y1 May I drop the staging rows?")
        await _reply(live, root, task, who, "Yes")
        await _refused_one(live, root, tool)
        assert _comment(live, root).body == "y1 May I drop the staging rows?"

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    @pytest.mark.parametrize("case", ["edited own reply", "deleted member reply"])
    async def test_y2_an_edited_or_a_deleted_reply_counts(self, live, tool, case):
        """An own reply that the member edited holds the member's words, and a
        deleted reply keeps its row: the check fails closed."""
        task = live.task("y2")
        root = await _agent_comment(live, task, "y2 May I drop the staging rows?")
        if case == "edited own reply":
            reply = await _reply(live, root, task, "own", "y2 own note")
            r = await _person(live, MEMBER, "PATCH", f"/projects/comments/{reply}",
                              {"body": "Yes"})
        else:
            reply = await _reply(live, root, task, "member", "Yes")
            r = await _person(live, MEMBER, "DELETE", f"/projects/comments/{reply}")
        assert r.status_code == 200, r.text
        await _refused_one(live, root, tool)

    async def test_y3_a_comment_with_only_its_own_replies_stays_open(self, live):
        """The control of Y1: the replies of this client do not refuse it."""
        task = live.task("y3")
        root = await _agent_comment(live, task, "y3 note")
        await _reply(live, root, task, "own", "y3 more")
        await _reply(live, root, task, "own", "y3 and more")
        upd = await _tool(live, "write", "update_comment", comment_id=root,
                          body="y3 fixed note", idempotency_key=_key("y3u"))
        assert upd["isError"] is False, upd
        assert _comment(live, root).body == "y3 fixed note"
        gone = await _tool(live, "write", "delete_comment", comment_id=root,
                           idempotency_key=_key("y3d"))
        assert gone["structuredContent"] == {"id": root, "deleted": True}, gone

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_o1_a_marked_comment_outside_the_pin_is_not_found(self, live, tool):
        task = live.elsewhere_task("o1")
        cid = await _agent_comment(live, task, "o1 note")
        r = await _person(live, MEMBER, "PATCH", f"/projects/comments/{cid}",
                          {"body": "o1 the member's words"})
        assert r.status_code == 200, r.text
        assert "person_edited" in _comment(live, cid).meta
        await _refused_one(live, cid, tool, name="pinned", expected=NOT_FOUND)

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_o2_a_marked_comment_after_a_delete_is_not_found(self, live, tool):
        task = live.task("o2")
        cid = await _agent_comment(live, task, "o2 note")
        path = f"/projects/comments/{cid}"
        assert (await _person(live, MEMBER, "PATCH", path,
                              {"body": "o2 the member's words"})).status_code == 200
        assert (await _person(live, MEMBER, "DELETE", path)).status_code == 200
        await _refused_one(live, cid, tool, expected=NOT_FOUND)

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_o3_a_run_comment_outside_the_pin_is_not_found(
            self, live, tool, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        cid = await _run_question(live, live.elsewhere_doing_task(f"o3 {tool}"))
        await _refused_one(live, cid, tool, name="pinned", expected=NOT_FOUND)

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_o4_a_run_comment_after_a_delete_is_not_found(
            self, live, tool, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        cid = await _run_question(live, live.doing_task(f"o4 {tool}"))
        r = await _person(live, MEMBER, "DELETE", f"/projects/comments/{cid}")
        assert r.status_code == 200, r.text
        await _refused_one(live, cid, tool, expected=NOT_FOUND)


# ── Fix round 3: the last word ──────────────────────────────────────────────


class TestFixRoundThree:
    """Rule 3 is the last word: the agent changes its own comment only while
    nobody else has written a comment on the same task after it."""

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_l1_a_reply_beside_the_agent_reply_refuses(self, live, tool):
        """The agent asks in a REPLY, and a peer answers beside it. The thread
        is one level deep, so the answer is no child of the question."""
        task = live.task("l1")
        root = await _person_comment(live, MEMBER, task, "l1 the plan for the rows")
        ask = await _reply(live, root, task, "own", "l1 May I drop the staging rows?")
        await _reply(live, root, task, "peer", "Yes")
        await _refused_one(live, ask, tool)
        assert _comment(live, ask).body == "l1 May I drop the staging rows?"

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_l2_a_new_comment_of_a_peer_refuses(self, live, tool):
        """The agent asks at the top level, and a peer answers with a new
        top-level comment."""
        task = live.task("l2")
        ask = await _agent_comment(live, task, "l2 May I drop the staging rows?")
        await _person_comment(live, PEER, task, "Yes")
        await _refused_one(live, ask, tool)
        assert _comment(live, ask).body == "l2 May I drop the staging rows?"

    async def test_l3_only_its_own_later_comments_keep_it_open(self, live):
        """The control. A peer wrote BEFORE the comment, and only this client
        wrote after it, at the top level and in a reply."""
        task = live.task("l3")
        await _person_comment(live, PEER, task, "l3 an earlier word of a peer")
        note = await _agent_comment(live, task, "l3 note")
        await _agent_comment(live, task, "l3 a later note")
        await _reply(live, note, task, "own", "l3 a later reply")
        upd = await _tool(live, "write", "update_comment", comment_id=note,
                          body="l3 fixed note", idempotency_key=_key("l3u"))
        assert upd["isError"] is False, upd
        assert _comment(live, note).body == "l3 fixed note"
        gone = await _tool(live, "write", "delete_comment", comment_id=note,
                           idempotency_key=_key("l3d"))
        assert gone["structuredContent"] == {"id": note, "deleted": True}, gone

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_l4_a_later_comment_of_the_member_in_the_app_refuses(self, live, tool):
        """The same member, typed in the app with no stamp, is somebody else."""
        task = live.task("l4")
        note = await _agent_comment(live, task, "l4 note")
        await _person_comment(live, MEMBER, task, "l4 typed by the member")
        await _refused_one(live, note, tool)

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_l5_a_later_comment_that_the_peer_deleted_refuses(self, live, tool):
        """A soft-deleted row keeps its place: the check fails closed."""
        task = live.task("l5")
        note = await _agent_comment(live, task, "l5 note")
        theirs = await _person_comment(live, PEER, task, "Yes")
        r = await _person(live, PEER, "DELETE", f"/projects/comments/{theirs}")
        assert r.status_code == 200, r.text
        await _refused_one(live, note, tool)

    def test_l6_the_task_lock_comes_before_the_last_word_check(self):
        """No new comment on the task commits between the check and the
        change. ``record_activity`` bumps the task row before it inserts a
        comment, so the lock must come first, and the comment lock is NO KEY
        so that a reply's foreign key check never waits on it."""
        import inspect

        from gateway.routes.projects import activities, core

        src = inspect.getsource(activities._load_agent_comment)
        assert "FOR NO KEY UPDATE" in src
        assert src.index("lock_task(") < src.index("_has_later_comment(")
        spine = inspect.getsource(core.record_activity)
        assert spine.index("touch_task(") < spine.index("insert_row(")


# ── Fix round 4: a comment of an agent run is never the agent's own ─────────


class TestFixRoundFour:
    """A later question or answer of a run blocks the earlier comment, also
    when this client wrote it."""

    @pytest.mark.parametrize("tool", COMMENT_TOOLS)
    async def test_l7_an_outside_decision_after_the_comment_refuses(
            self, live, tool, monkeypatch):
        """The agent asks in a plain comment, the member answers in another
        place, and the agent records it with ``record_outside_decision``. The
        run's question and answer carry this client's stamp, and they still
        end the agent's rights over the plain comment."""
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        task = live.doing_task(f"l7 {tool}")
        ask = await _agent_comment(live, task, "l7 May I drop the staging rows?")
        asked = await _tool(live, "write", "ask_on_task", task_id=task,
                            question="See my comment above. May I?",
                            idempotency_key=_key("l7-ask"))
        assert asked["isError"] is False, asked
        decided = await _tool(live, "write", "record_outside_decision",
                              run_id=asked["structuredContent"]["run"]["id"],
                              decision="Yes.", where="Claude Code chat",
                              idempotency_key=_key("l7-yes"))
        assert decided["isError"] is False, decided
        with live.admin.begin() as c:
            stamps = {r.via for r in c.execute(text(
                "SELECT meta->>'via' AS via FROM pm_activities WHERE type = 'comment'"
                " AND task_id = CAST(:t AS uuid)"), {"t": task})}
        assert stamps == {R8_VIA}, "only this client wrote on the task"
        await _refused_one(live, ask, tool)
        assert _comment(live, ask).body == "l7 May I drop the staging rows?"
