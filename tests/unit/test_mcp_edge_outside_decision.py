"""WS-55 MCP-G15 — a decision made outside Metorite (Metorite task 1567).

Spec: ``project-docs/specs/external_agent_access.md`` §12.1 ("A decision
made outside Metorite") and the row MCP-G15 of §15.

An agent asks on a task with ``ask_on_task``, and the run waits
(``awaiting``). The member then decides in another place, such as the
agent's own chat. The agent records it with ``record_outside_decision``. The
gateway writes an AGENT ``response``, never a member ``prompt``. Its
``in_reply_to`` is the open question, and ``content_metadata`` carries the
marker ``decided_elsewhere``. The run moves ``awaiting`` → ``in_progress``,
and the task stops waiting on the member.

Two halves, as in ``test_mcp_edge_archive.py``.

**Database-free (always runs).** The status machine, the comment text, the
tool record, the body the tool sends, the text of each refusal code, the
flags, the scope, and the instructions and the prompt.

**R8 (a real database, as a NON-privileged role, on a promoted catalog of
its own).** The edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20), and the members act through the person
path of the same app.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<db>
    uv run pytest tests/unit/test_mcp_edge_outside_decision.py -v -rs

**Each refusal and its test (R7).**

* A run that does not wait (409 ``run_not_awaiting``):
  ``test_d4_a_run_that_does_not_wait_is_refused``.
* A run with no open question (409 ``no_open_question``):
  ``test_d5_a_run_with_no_open_question_is_refused``.
* A typed request (409 ``typed_request``):
  ``test_d6_a_typed_archive_request_is_refused`` and
  ``test_any_server_typed_question_is_typed``.
* Another member's run (403 ``run_not_yours``):
  ``test_d7_another_members_run_is_refused``. Another client stamp:
  ``test_another_client_stamp_is_not_the_agents_own_run``. Both read
  ``may_end_run``, the rule of Release.
* A person caller (403): ``test_d8_a_person_cannot_record_it`` and
  ``test_the_route_refuses_a_person_before_any_session``.
* An option id that the question does not hold (422 ``selected_refused``):
  ``test_d9_selected_must_name_options_of_the_question``.
* A key of another activity (409): ``test_d10_the_same_key_replays_and_a_used_key_is_refused``.

**Needs attention.** ``test_d2_the_task_leaves_every_waiting_list`` reads
each list that shows an awaiting run before and after the act: the derived
state of the run and the pill (``filters.AGENT_RUN_STATE_SQL``), the
"Waiting on me" focus, ``status=awaiting`` (the chat's
``waiting_agent_requests``), the open question of the run, ``needs_agent``
and the Needs attention route. Fix round 1 adds the member's bell and the
reason of the My Day card (``shell.needs._note_signals``).
``test_d2b_the_act_reads_the_members_bell_row_and_no_other`` holds that the
act reads the member's bell row only, and that My Day moves once.
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

from acb_auth import agent_tokens
from acb_common import get_settings
from agent_edge import mcp as edge_mcp
from agent_edge import prompts, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder
from tests.unit.test_mcp_edge_agent_runs import _run_row

_DB_GATE = w._DB_GATE
GOOD, TASK = w.GOOD, w.TASK
MEMBER = w.MEMBER
OTHER = "other-outside@mcpwrites.test"
VIA = w.VIA
TOOL = "record_outside_decision"

RUN = w._uuid(7001)
QUESTION = w._uuid(7002)
DECISION_PATH = f"/projects/agent-runs/{RUN}/outside-decision"

_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text
edge = w.edge


def _key() -> str:
    return f"k-{uuid.uuid4()}"


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    return result["structuredContent"]


def _response_row() -> dict[str, Any]:
    return {
        "id": w._uuid(7003), "run_id": RUN, "task_id": TASK, "seq": 5, "type": "response",
        "content": {"body": "Decided outside Metorite (Claude Code chat): Approve.",
                    "selected": ["approve"], "labels": ["Approve"]},
        "content_metadata": {"decided_elsewhere": {"where": "Claude Code chat",
                                                   "question_id": QUESTION,
                                                   "decision": "Approve"}},
        "signal": None, "signal_metadata": None,
        "actor": "m@x.test", "actor_via": VIA, "in_reply_to": QUESTION, "created_at": "x",
    }


@pytest.fixture
def runs_on(monkeypatch):
    """The fake edge of ``test_mcp_edge_writes.py`` with the runs flag on."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)


@pytest.fixture
def writes_and_runs(monkeypatch):
    """The two flags that show the routine and the run part of ``initialize``."""
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_runs_enabled", True)
    monkeypatch.setattr(settings, "agent_access_writes_enabled", True)


# ═══════════════════════════════════════════════════════════════════════════
# The gateway rules, database-free
# ═══════════════════════════════════════════════════════════════════════════


class TestTheGatewayRules:
    """The status machine, the marker and the comment, with no database."""

    def test_d1_the_event_moves_awaiting_to_in_progress_only(self):
        """An outside decision ends the wait, and nothing else. Mutation: the
        event also takes ``in_progress`` — the second assert fails."""
        from fastapi import HTTPException
        from gateway.routes.projects.agent_runs import transition

        assert transition("awaiting", "agent_outside_decision") == "in_progress"
        for status in ("created", "in_progress", "stale", "stopping", "completed",
                       "stopped", "failed", "abandoned", "released"):
            with pytest.raises(HTTPException) as refused:
                transition(status, "agent_outside_decision")
            assert refused.value.status_code == 409, status

    def test_an_agent_activity_never_maps_to_the_member_prompt(self):
        """The refusal of a prompt from an agent stays (spec §12.1). The new
        act is its own agent event, never ``member_prompt``."""
        from gateway.routes.projects.agent_runs import AGENT_TYPES, _event_of

        assert all(_event_of(kind, None) != "member_prompt" for kind in AGENT_TYPES)
        assert "prompt" not in AGENT_TYPES

    def test_the_body_names_no_server_field(self):
        """No body sets ``in_reply_to``, the marker or the type. Mutation:
        ``extra="ignore"`` on ``OutsideDecisionIn`` — this test fails."""
        from gateway.routes.projects.agent_runs import OutsideDecisionIn
        from pydantic import ValidationError

        good = {"decision": "Approve", "where": "Claude Code chat", "idempotency_key": "k"}
        OutsideDecisionIn(**good)
        for extra in ({"in_reply_to": QUESTION}, {"content_metadata": {}},
                      {"type": "prompt"}, {"signal": "continue"}):
            with pytest.raises(ValidationError):
                OutsideDecisionIn(**good, **extra)

    @pytest.mark.parametrize("where,decision,labels,expected", [
        ("Claude Code chat", "Approve. Ship it on Friday.", ["Approve"],
         "Decided outside Metorite (Claude Code chat): Approve. Ship it on Friday."),
        ("Claude Code chat", "approve", ["Approve"],
         "Decided outside Metorite (Claude Code chat): approve"),
        ("Claude Code chat", "Ship it on Friday.", ["Approve"],
         "Decided outside Metorite (Claude Code chat): Approve. Ship it on Friday."),
        ("a call", "Use the second supplier.", [],
         "Decided outside Metorite (a call): Use the second supplier."),
        ("Slack", "Both are fine", ["A", "B"],
         "Decided outside Metorite (Slack): A, B. Both are fine"),
    ])
    def test_the_comment_text(self, where, decision, labels, expected):
        from gateway.routes.projects.agent_runs import outside_comment

        assert outside_comment(where, decision, labels) == expected

    def test_any_server_typed_question_is_typed(self):
        """Only the member answers a typed request. A later kind counts too,
        not only the archive. Mutation: ``typed_question`` reads only
        ``archive_request_of`` — the second assert fails."""
        from gateway.routes.projects.agent_runs import typed_question

        archive = SimpleNamespace(content_metadata={"request": {"kind": "archive_task"}})
        later = SimpleNamespace(content_metadata={"request": {"kind": "merge_task"}})
        free = SimpleNamespace(content_metadata=None)
        assert typed_question(archive) is True
        assert typed_question(later) is True
        assert typed_question(free) is False

    async def test_another_client_stamp_is_not_the_agents_own_run(self):
        """The rule of Release decides who owns the run: the same member AND
        the same client stamp. The route reads it, and no second check."""
        from gateway.routes.projects.agent_runs import may_end_run

        run = SimpleNamespace(id=RUN, member_email=MEMBER, client_via=VIA)

        def agent(via: str, email: str = MEMBER) -> Any:
            return SimpleNamespace(email=email, agent=SimpleNamespace(
                actor_via=via, scopes=w.WRITE_SCOPES, connection_id=w.CONN))

        assert await may_end_run(None, run, agent(VIA)) is True
        assert await may_end_run(None, run, agent("mcp:other.example")) is False
        assert await may_end_run(None, run, agent(VIA, "someone@else.test")) is False

    async def test_the_route_refuses_a_person_before_any_session(self, monkeypatch):
        """A person answers on the task. The route refuses a person before it
        opens a session."""
        from fastapi import HTTPException, Response
        from gateway.routes.projects import agent_runs as gateway_runs

        def no_session() -> Any:
            raise AssertionError("the route opened a session for a person")

        monkeypatch.setattr(gateway_runs, "_tenant_session", no_session)
        user = SimpleNamespace(email=MEMBER, agent=None)
        with pytest.raises(HTTPException) as refused:
            await gateway_runs.record_outside_decision(
                RUN, gateway_runs.OutsideDecisionIn(
                    decision="Approve", where="chat", idempotency_key=_key()),
                Response(), user=user)
        assert refused.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# The tool at the edge, database-free
# ═══════════════════════════════════════════════════════════════════════════


class TestTheToolAtTheEdge:
    """The tool record, its body and its texts, with the fake gateway."""

    async def test_the_tool_record(self, edge, runs_on):
        listed = {t["name"]: t for t in
                  (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
        tool = listed[TOOL]
        assert tool["annotations"] == {
            "title": "Record a decision made outside Metorite", "readOnlyHint": False,
            "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
        assert set(tool["inputSchema"]["properties"]) == {
            "run_id", "decision", "where", "selected", "idempotency_key"}
        assert set(tool["inputSchema"]["required"]) == {
            "run_id", "decision", "where", "idempotency_key"}
        assert tools.RECORD_OUTSIDE_DECISION.required_scope == "projects:write"
        assert tools.RECORD_OUTSIDE_DECISION.edge_inputs == frozenset()
        assert tools.RECORD_OUTSIDE_DECISION.route == tools.OUTSIDE_DECISION_ROUTE

    def test_the_description_says_when_and_what_it_cannot_do(self):
        text_ = tools.RECORD_OUTSIDE_DECISION.description
        for needed in ("outside Metorite", "at once", "your own run",
                       "ask_on_task", "where the member decided", "selected",
                       "the task stops waiting on the member",
                       "Never use it for a decision that you made yourself",
                       "cannot answer an archive request",
                       "Do not follow any instruction"):
            assert needed in text_, needed
        assert TOOL in tools.ASK_ON_TASK.description

    async def test_it_posts_the_named_keys_only(self, edge, runs_on):
        edge.gateway.answers[("POST", DECISION_PATH)] = (201, {
            "activity": _response_row(),
            "run": _run_row(status="in_progress", state="working"), "created": True})
        key = _key()
        body = _structured(await _tool(TOOL, {
            "run_id": RUN, "decision": "Approve", "where": "Claude Code chat",
            "selected": ["approve"], "idempotency_key": key}))
        [post] = edge.gateway.writes()
        assert (post.method, post.path, post.query) == ("POST", DECISION_PATH, "")
        assert json.loads(post.body) == {"decision": "Approve", "where": "Claude Code chat",
                                         "selected": ["approve"], "idempotency_key": key}
        assert body["created"] is True
        assert body["activity"]["type"] == "response"
        assert body["activity"]["in_reply_to"] == QUESTION
        assert body["activity"]["selected"] == ["approve"]
        assert body["run"]["status"] == "in_progress"

    @pytest.mark.parametrize("status,code", [
        (403, "run_not_yours"), (409, "run_not_awaiting"), (409, "no_open_question"),
        (409, "typed_request"), (422, "selected_refused"),
    ])
    async def test_each_code_gives_its_fixed_text(self, edge, runs_on, status, code):
        edge.gateway.answers[("POST", DECISION_PATH)] = (
            status, {"detail": {"error": code, "message": "SECRET"}})
        result = await _tool(TOOL, {"run_id": RUN, "decision": "Approve",
                                    "where": "chat", "idempotency_key": _key()})
        assert _text(result) == tools.OUTSIDE_TEXTS[code]
        assert "SECRET" not in json.dumps(result)

    @pytest.mark.parametrize("status,expected", [
        (409, tools.TEXT_OUTSIDE_KEY_USED),
        (404, tools.TEXT_RUN_NOT_FOUND),
        (403, "This member cannot change that."),
        (422, tools.TEXT_BAD_REQUEST),
        (429, "Too many changes. Wait a few minutes."),
        (503, tools.TEXT_BUSY),
    ])
    async def test_the_answer_table(self, edge, runs_on, status, expected):
        edge.gateway.answers[("POST", DECISION_PATH)] = (status, {"detail": "SECRET"})
        result = await _tool(TOOL, {"run_id": RUN, "decision": "Approve",
                                    "where": "chat", "idempotency_key": _key()})
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    def test_the_codes_and_the_limits_copy_the_gateway(self):
        """Each copy of a gateway constant has a pin test (agent edge rule)."""
        from gateway.routes.projects import agent_runs as gateway_runs

        assert set(tools.OUTSIDE_TEXTS) == set(gateway_runs.OUTSIDE_CODES)
        assert tools.MAX_OUTSIDE_DECISION == gateway_runs.MAX_DECISION
        assert tools.MAX_OUTSIDE_WHERE == gateway_runs.MAX_WHERE
        assert set(tools.OUTSIDE_BODY_KEYS) == set(gateway_runs.OutsideDecisionIn.model_fields)

    async def test_a_nul_is_refused_before_any_request(self, edge, runs_on):
        result = await _tool(TOOL, {"run_id": RUN, "decision": "a\x00b",
                                    "where": "chat", "idempotency_key": _key()})
        assert "decision" in _text(result)
        assert edge.gateway.requests == []

    async def test_the_flags_hide_the_tool_and_send_nothing(self, edge, runs_on, monkeypatch):
        names = {t["name"] for t in
                 (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
        assert TOOL in names
        settings = get_settings()
        for flag, text_ in (("agent_runs_enabled", tools.TEXT_RUNS_OFF),
                            ("agent_access_writes_enabled", tools.TEXT_WRITES_OFF)):
            monkeypatch.setattr(settings, flag, False)
            names = {t["name"] for t in
                     (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
            assert TOOL not in names, flag
            result = await _tool(TOOL, {"run_id": RUN, "decision": "Approve",
                                        "where": "chat", "idempotency_key": _key()})
            assert _text(result) == text_, flag
            monkeypatch.setattr(settings, flag, True)
        assert [r.path for r in edge.gateway.requests
                if r.path != tools.AGENT_ACCESS_ROUTE] == []

    async def test_a_read_connection_gets_the_step_up_403(self, edge, runs_on):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call(TOOL, {"run_id": RUN, "decision": "Approve",
                                     "where": "chat", "idempotency_key": _key()}))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        assert edge.gateway.requests == []


class TestTheInstructionsAndThePrompt:
    """The server instructions and ``work_on_task`` name the tool while the
    runs show, and never while they hide. Mutation: drop the sentence from
    ``mcp._RUN_INSTRUCTIONS`` — the first test fails."""

    def test_the_instructions_name_the_tool_with_the_runs_on(self, writes_and_runs):
        text_ = " ".join(edge_mcp.instructions(w.WRITE_SCOPES).split())
        assert ("When the member decides outside Metorite, such as in this chat, call"
                " record_outside_decision at once.") in text_
        assert "the task stops waiting on the member" in text_

    def test_the_instructions_name_no_tool_with_the_runs_off(self, writes_and_runs,
                                                              monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        assert TOOL not in edge_mcp.instructions(w.WRITE_SCOPES)

    def test_the_prompt_names_the_tool_with_the_runs_on(self):
        steps = " ".join(prompts._work_steps(runs=True))
        assert ("When you asked me with ask_on_task and I answer outside Metorite,"
                " such as in this chat, call record_outside_decision at once.") in steps

    def test_the_prompt_names_no_tool_with_the_runs_off(self):
        assert TOOL not in " ".join(prompts._work_steps(runs=False))


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcpoutside"
_APP_PW = "mcpoutside_scratch_pw"


def _grant(seeder: w._Seeder, project: str, who: str) -> None:
    seeder.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
               " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
               " RETURNING project_id", p=project, s=who, a=w.OWNER, o=seeder.org)


def _connection_of(a: w._Seeder, email: str) -> tuple[str, str]:
    """A write connection of *email*, and one access token for it."""
    cid = str(a.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
        " now() + interval '1 day') RETURNING id",
        o=a.org, m=email, origin=w.ORIGIN, scopes=list(w.WRITE_SCOPES), pins=[]))
    raw = agent_tokens.new_token()
    a.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
          " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
          " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
          " 'https://client.example/meta.json', :r, :s) RETURNING id",
          o=a.org, c=cid, h=agent_tokens.hash_token(raw), r=w.RESOURCE,
          s=" ".join(w.WRITE_SCOPES))
    return cid, raw


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: two members and one shared project."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcpoutside"
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

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcpoutside', 'mcpoutside')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 600_000
    a.member(MEMBER, ["feature:projects"])
    a.member(OTHER, ["feature:projects"])
    team = a.project("Team", in_space=True)
    _grant(a, team, OTHER)
    s = SimpleNamespace(todo=a.status(team, "To do", "todo", 0),
                        doing=a.status(team, "Doing", "in_progress", 1))
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, s=s, c={}, tok={},
                             task=a.task)
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.c["other"], world_.tok["other"] = _connection_of(a, OTHER)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live


@pytest.fixture
def runs(live, monkeypatch):
    """The real gateway and the real edge, with writes and runs on."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    return live


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _agent(world, tool: str, name: str = "write", **arguments: Any) -> dict[str, Any]:
    return await tw._mcp(world, name, tool, arguments)


async def _person(world, email: str, method: str, path: str,
                  body: dict[str, Any] | None = None) -> httpx.Response:
    """One request to the gateway app as the person *email*."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    headers = {"Authorization": f"Bearer {tw.INTERNAL}", "X-User-Email": email}
    if body is not None:
        headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, content=raw)
    return await tw._in_scope(world, go)


def _rows(world, sql: str, **params: Any) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(sql), params))


def _activities(world, run_id: str) -> list[Any]:
    return _rows(world, "SELECT * FROM pm_agent_run_activities WHERE run_id ="
                        " CAST(:r AS uuid) ORDER BY seq", r=run_id)


def _run_status(world, run_id: str) -> str:
    [row] = _rows(world, "SELECT status FROM pm_agent_runs WHERE id = CAST(:r AS uuid)",
                  r=run_id)
    return str(row.status)


def _comments(world, task: str) -> list[Any]:
    return _rows(world, "SELECT * FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                        " AND type = 'comment' ORDER BY created_at", t=task)


async def _asked(world, title: str, *, options: bool = True) -> tuple[str, str, dict[str, Any]]:
    """A new task in ``team`` and a question on it: ``(task, run, body)``."""
    task = world.task(world.team, world.s.doing, title)
    arguments: dict[str, Any] = {"task_id": task, "question": "Ship on Friday?",
                                 "idempotency_key": _key()}
    if options:
        arguments["options"] = [{"id": "approve", "label": "Approve", "recommended": True},
                                {"id": "wait", "label": "Wait a week"}]
    body = _structured(await _agent(world, "ask_on_task", **arguments))
    assert body["run"]["status"] == "awaiting", body
    return task, body["run"]["id"], body


async def _record(world, run_id: str, *, name: str = "write", key: str | None = None,
                  **extra: Any) -> dict[str, Any]:
    arguments = {"run_id": run_id, "decision": "Approve. Ship it on Friday.",
                 "where": "Claude Code chat", "idempotency_key": key or _key(), **extra}
    return await _agent(world, TOOL, name=name, **arguments)


async def _run_wire(world, run_id: str) -> dict[str, Any]:
    r = await _person(world, MEMBER, "GET", f"/projects/agent-runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()["run"]


async def _waiting_lists(world, task: str, run_id: str) -> dict[str, Any]:
    """Each list that shows an awaiting run to a person, for one task."""
    pill = await _person(world, MEMBER, "GET", f"/projects/tasks/{task}")
    assert pill.status_code == 200, pill.text
    focus = await _person(world, MEMBER, "GET",
                          f"/projects/tasks?project_id={world.team}&agent_focus=waiting")
    assert focus.status_code == 200, focus.text
    awaiting = await _person(world, MEMBER, "GET", "/projects/agent-runs?status=awaiting")
    assert awaiting.status_code == 200, awaiting.text
    attention = await _person(world, MEMBER, "GET",
                              f"/projects/needs-attention?project_id={world.team}")
    assert attention.status_code == 200, attention.text
    run = await _run_wire(world, run_id)
    bell = await _bell(world, MEMBER, task)
    return {
        "bell_unread": [row["kind"] for row in bell],
        "my_day_reason": _my_day_reason(bell, task),
        "pill": pill.json().get("agent_state"),
        "waiting_on_me": task in {row["id"] for row in focus.json()["rows"]},
        "status_awaiting": run_id in {row["id"] for row in awaiting.json()["rows"]},
        "needs_attention": task in {row["task_id"] for row in attention.json()["rows"]},
        "state": run["state"],
        "open_question": run["open_elicitation"],
        "needs_agent": run["needs_agent"],
    }


async def _bell(world, email: str, task: str) -> list[dict[str, Any]]:
    """The unread bell rows of *email* on *task*, through the bell's own read."""
    r = await _person(world, email, "GET", "/projects/notifications?unread_only=true")
    assert r.status_code == 200, r.text
    return [row for row in r.json()["rows"] if row["task_id"] == task]


def _my_day_reason(rows: list[dict[str, Any]], task: str) -> str | None:
    """The reason of the My Day card of *task*, from the unread rows, through
    the feed's own seam (``shell.needs._note_signals``). None: no card."""
    from gateway.routes.shell.needs import _note_signals

    found = [sig for sig in _note_signals(rows) if sig.signal_id == f"tasks:{task}"]
    return str(found[0].facts["notification"]) if found else None


def _bell_rows(world, task: str) -> list[Any]:
    return _rows(world, "SELECT recipient, kind, read_at FROM pm_notifications"
                        " WHERE task_id = CAST(:t AS uuid) AND kind = 'agent_question'"
                        " ORDER BY recipient", t=task)


# ── The acceptance ──────────────────────────────────────────────────────────


class TestTheDecision:
    """D1 to D3: the act closes the question as an agent act, and the member
    path works as before."""

    pytestmark = _DB_GATE

    async def test_d1_the_act_closes_the_question_as_an_agent_response(self, runs):
        task, run_id, asked = await _asked(runs, "d1 outside")
        question_id = asked["activity"]["id"]
        result = _structured(await _record(runs, run_id, selected=["approve"]))
        assert result["created"] is True
        assert result["run"]["status"] == "in_progress"
        assert result["activity"]["type"] == "response"
        assert result["activity"]["author"] == "agent"
        assert result["activity"]["in_reply_to"] == question_id

        rows = _activities(runs, run_id)
        assert [r.type for r in rows] == ["elicitation", "response"]
        [answer] = [r for r in rows if r.type == "response"]
        assert answer.actor_via is not None and answer.actor == MEMBER
        assert str(answer.in_reply_to) == question_id
        marker = answer.content_metadata["decided_elsewhere"]
        assert marker == {"where": "Claude Code chat", "question_id": question_id,
                          "decision": "Approve. Ship it on Friday."}
        assert answer.content["selected"] == ["approve"]
        assert answer.content["labels"] == ["Approve"]
        # No member prompt exists: the act is the agent's.
        assert not [r for r in rows if r.type == "prompt"]
        assert _run_status(runs, run_id) == "in_progress"
        bodies = [c.body for c in _comments(runs, task)]
        assert ("Decided outside Metorite (Claude Code chat): Approve. Ship it on"
                " Friday.") in bodies

    async def test_d2_the_task_leaves_every_waiting_list(self, runs):
        """Needs attention, proved. Before the act each list shows the
        task as waiting on the member. After it, none does, with no other
        change. Mutation: ``in_reply_to`` stays NULL on the response — the
        open question and the waiting lists stay."""
        task, run_id, _ = await _asked(runs, "d2 outside")
        before = await _waiting_lists(runs, task, run_id)
        assert before == {"bell_unread": ["agent_question"],
                          "my_day_reason": "agent_question",
                          "pill": "awaiting", "waiting_on_me": True, "status_awaiting": True,
                          "needs_attention": False, "state": "awaiting",
                          "open_question": before["open_question"], "needs_agent": None}
        assert before["open_question"] is not None

        _structured(await _record(runs, run_id, selected=["approve"]))
        after = await _waiting_lists(runs, task, run_id)
        assert after == {"bell_unread": [], "my_day_reason": None,
                         "pill": "working", "waiting_on_me": False, "status_awaiting": False,
                         "needs_attention": False, "state": "working",
                         "open_question": None, "needs_agent": None}

    async def test_d2b_the_act_reads_the_members_bell_row_and_no_other(self, runs, monkeypatch):
        """Fix round 1 (P2). The bell and My Day of the member stop asking.
        The bell row of a watcher stays theirs to read. My Day moves once,
        after the commit. Mutation: no ``_clear_question_bell`` call — the
        member's row stays unread, and D2 fails too."""
        from gateway.routes.projects import personal

        bumped: list[str] = []

        async def bump(org: Any, email: str) -> None:
            bumped.append(email)

        monkeypatch.setattr(personal, "bump_my_day", bump)
        task = runs.task(runs.team, runs.s.doing, "d2b bell")
        r = await _person(runs, OTHER, "PUT", f"/projects/tasks/{task}/watch")
        assert r.status_code == 200, r.text
        body = _structured(await _agent(runs, "ask_on_task", task_id=task,
                                        question="Ship on Friday?", idempotency_key=_key()))
        run_id = body["run"]["id"]
        assert [(b.recipient, b.read_at is None) for b in _bell_rows(runs, task)] == [
            (MEMBER, True), (OTHER, True)]

        key = _key()
        _structured(await _record(runs, run_id, key=key))
        rows = {b.recipient: b.read_at for b in _bell_rows(runs, task)}
        assert rows[MEMBER] is not None
        assert rows[OTHER] is None
        assert bumped == [MEMBER]
        # A replay marks nothing more and moves My Day no more.
        _structured(await _record(runs, run_id, key=key))
        assert {b.recipient: b.read_at for b in _bell_rows(runs, task)} == rows
        assert bumped == [MEMBER]

    async def test_d3_the_members_own_answer_works_as_before(self, runs):
        """The member's real answer path does not change: a prompt with
        ``selected`` answers the question, and the run reads answered."""
        _, run_id, asked = await _asked(runs, "d3 member answers")
        r = await _person(runs, MEMBER, "POST", f"/projects/agent-runs/{run_id}/activities",
                          {"type": "prompt", "selected": ["wait"], "idempotency_key": _key()})
        assert r.status_code == 201, r.text
        prompt = r.json()["activity"]
        assert prompt["in_reply_to"] == asked["activity"]["id"]
        assert prompt["content"]["labels"] == ["Wait a week"]
        assert r.json()["run"]["state"] == "answered"
        # The question is answered, so the outside act finds no wait.
        refused = await _record(runs, run_id)
        assert _text(refused) == tools.OUTSIDE_TEXTS["run_not_awaiting"]

    async def test_a_late_member_answer_finds_the_question_closed(self, runs):
        """After the outside act, a member who answers the same question on
        the card gets the 409 of a second answer, as two members do."""
        _, run_id, _ = await _asked(runs, "late member answer")
        _structured(await _record(runs, run_id, selected=["approve"]))
        r = await _person(runs, MEMBER, "POST", f"/projects/agent-runs/{run_id}/activities",
                          {"type": "prompt", "selected": ["wait"], "idempotency_key": _key()})
        assert r.status_code == 409, r.text
        # A free reply still reaches the agent.
        r = await _person(runs, MEMBER, "POST", f"/projects/agent-runs/{run_id}/activities",
                          {"type": "prompt", "body": "Thanks.", "idempotency_key": _key()})
        assert r.status_code == 201, r.text

    async def test_a_free_question_takes_a_decision_with_no_ids(self, runs):
        task, run_id, _ = await _asked(runs, "free question", options=False)
        result = _structured(await _agent(runs, TOOL, run_id=run_id,
                                          decision="Use the second supplier.",
                                          where="a call", idempotency_key=_key()))
        assert result["run"]["status"] == "in_progress"
        bodies = [c.body for c in _comments(runs, task)]
        assert "Decided outside Metorite (a call): Use the second supplier." in bodies


class TestTheRefusals:
    """D4 to D10: each refusal changes nothing."""

    pytestmark = _DB_GATE

    async def test_d4_a_run_that_does_not_wait_is_refused(self, runs):
        task = runs.task(runs.team, runs.s.doing, "d4 working run")
        started = _structured(await _agent(runs, "start_agent_run", task_id=task,
                                           idempotency_key=_key()))
        run_id = started["run"]["id"]
        count = len(_activities(runs, run_id))
        result = await _record(runs, run_id)
        assert _text(result) == tools.OUTSIDE_TEXTS["run_not_awaiting"]
        assert len(_activities(runs, run_id)) == count
        assert _run_status(runs, run_id) == "in_progress"

    async def test_d5_a_run_with_no_open_question_is_refused(self, runs):
        """An ``awaiting`` run whose question has gone. The state cannot come
        from a route, so the admin writes it."""
        task = runs.task(runs.team, runs.s.doing, "d5 no question")
        run_id = str(runs.seeder.one(
            "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
            " status, started_by, connection_id) VALUES (CAST(:o AS uuid), CAST(:t AS uuid),"
            " :m, :v, 'awaiting', :v, CAST(:c AS uuid)) RETURNING id",
            o=runs.org, t=task, m=MEMBER, v=w.R8_VIA, c=runs.c["write"]))
        result = await _record(runs, run_id)
        assert _text(result) == tools.OUTSIDE_TEXTS["no_open_question"]
        assert _activities(runs, run_id) == []
        assert _run_status(runs, run_id) == "awaiting"

    async def test_d6_a_typed_archive_request_is_refused(self, runs):
        """Only the member answers a typed request, in Metorite."""
        task = runs.task(runs.team, runs.s.doing, "d6 archive request")
        asked = _structured(await _agent(runs, "request_task_archive", task_id=task,
                                         idempotency_key=_key()))
        run_id = asked["run"]["id"]
        result = await _record(runs, run_id, selected=["approve"])
        assert _text(result) == tools.OUTSIDE_TEXTS["typed_request"]
        assert [r.type for r in _activities(runs, run_id)] == ["elicitation"]
        assert _run_status(runs, run_id) == "awaiting"
        assert _rows(runs, "SELECT archived_at FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                     t=task)[0].archived_at is None

    async def test_d7_another_members_run_is_refused(self, runs):
        """The other member can see the task, and its agent still cannot
        record a decision on the member's run."""
        _, run_id, _ = await _asked(runs, "d7 other member")
        result = await _record(runs, run_id, name="other")
        assert _text(result) == tools.OUTSIDE_TEXTS["run_not_yours"]
        assert [r.type for r in _activities(runs, run_id)] == ["elicitation"]
        assert _run_status(runs, run_id) == "awaiting"

    async def test_d8_a_person_cannot_record_it(self, runs):
        _, run_id, _ = await _asked(runs, "d8 person")
        r = await _person(runs, MEMBER, "POST", f"/projects/agent-runs/{run_id}/outside-decision",
                          {"decision": "Approve", "where": "chat", "idempotency_key": _key()})
        assert r.status_code == 403, r.text
        assert _run_status(runs, run_id) == "awaiting"

    @pytest.mark.parametrize("selected", [["nope"], ["approve", "wait"], ["approve", "approve"]])
    async def test_d9_selected_must_name_options_of_the_question(self, runs, selected):
        _, run_id, _ = await _asked(runs, f"d9 {'-'.join(selected)}")
        result = await _record(runs, run_id, selected=selected)
        assert _text(result) == tools.OUTSIDE_TEXTS["selected_refused"]
        assert _run_status(runs, run_id) == "awaiting"

    async def test_d9_selected_on_a_free_question_is_refused(self, runs):
        _, run_id, _ = await _asked(runs, "d9 free", options=False)
        result = await _record(runs, run_id, selected=["approve"])
        assert _text(result) == tools.OUTSIDE_TEXTS["selected_refused"]
        assert _run_status(runs, run_id) == "awaiting"

    async def test_d10_the_same_key_replays_and_a_used_key_is_refused(self, runs):
        _, run_id, _ = await _asked(runs, "d10 replay")
        key = _key()
        first = _structured(await _record(runs, run_id, key=key))
        again = _structured(await _record(runs, run_id, key=key))
        assert again["created"] is False
        assert again["activity"]["id"] == first["activity"]["id"]
        assert [r.type for r in _activities(runs, run_id)] == ["elicitation", "response"]
        # The key of the question is not the key of an outside decision.
        _, run2, _ = await _asked(runs, "d10 used key")
        r = await _agent(runs, TOOL, run_id=run2, decision="Approve", where="chat",
                         idempotency_key=_activities(runs, run2)[0].idempotency_key)
        assert _text(r) == tools.TEXT_OUTSIDE_KEY_USED
        assert _run_status(runs, run2) == "awaiting"
