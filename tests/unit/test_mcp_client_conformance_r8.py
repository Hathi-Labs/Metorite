"""MCP-G23b, R8 — the scripted client sessions against the REAL gateway and a
REAL database (Metorite task 2963).

Spec: ``project-docs/specs/external_agent_access.md`` row MCP-G23, and
``orchestration_playbook.md`` KIT-6.

**What this adds to ``test_mcp_client_conformance.py`` (MCP-G23).** That file
speaks the wire protocol to the real edge over a FAKE board. This file speaks
it to the same real edge, and the edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20) on a private, phase-4-promoted catalog, as a
NON-privileged role under FORCE ROW LEVEL SECURITY. The client profiles, the
``Client`` wire class, the pinned tool logs and the KIT-6 scorer are IMPORTED
from that file. This file copies none of them.

**The world.** Two tenants. Org A holds one project and the tasks of the
sessions. Org B holds one project and one task. Both orgs hold a member with
the SAME address, so only the connection (its org) tells them apart.

What it proves:

1. Each tool result of a real session matches its ``outputSchema`` (the
   ``Client.call`` check), for both client profiles.
2. A session ends with exactly one disposition, and the board shows it: a task
   status for the finish path, a ``released`` run for the release path.
3. The org B connection sees none of the tasks or runs of org A through the
   same tools. It cannot claim, finish or read them, and the rows stay as they
   were.
4. The answer of the REAL gateway to ``finish_agent_run`` on a run that
   ``release_task`` already ended. See ``TestFinishAfterRelease``.
5. The instructions that ``initialize`` returns, with the writes and runs
   flags on, keep each safety sentence before char 4096.

**The mutations (R7).** Each one turns the named test red.

* M1. ``agent_runs.py`` ``release_agent_run``: skip ``_move_run`` (do not end
  the run). ``test_the_release_path_ends_with_one_released_run``.
* M2. In the ``world`` fixture, give the org B connection the token of org A
  (``w.b.token = w.a.token``). ``TestTheSecondTenant``.
* M3. ``mcp.py`` ``_RUN_INSTRUCTIONS``: pad it by 1,200 characters.
  ``test_the_instructions_keep_every_safety_sentence_before_char_4096``.
* M4. ``agent_runs.py`` ``transition``: let ``agent_finish`` move ``released``
  to ``completed``. ``TestFinishAfterRelease`` (the pinned 409).
* M5. ``tools.py`` ``_finish_agent_run``: drop the ``409`` text map. The pinned
  text of ``TestFinishAfterRelease`` changes.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    PYTEST_ADDOPTS='-o timeout_method=thread' uv run pytest -q \\
        tests/unit/test_mcp_client_conformance_r8.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import httpx
import jsonschema
from acb_auth import agent_signing, agent_tokens
from acb_common import get_settings
from agent_edge import mcp, tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database
from tests.unit.test_mcp_client_conformance import (
    ALL_PINNED,
    CLIENT_PROFILES,
    CUT,
    MAIN_TOOL_LOG,
    SAFETY_SENTENCES,
    Client,
    _score,
    _transcript,
)
from tests.unit.test_mcp_edge_writes import (  # noqa: F401 — fixtures
    _APP_PW,
    _APP_ROLE,
    _DB_GATE,
    _URL,
    KEY,
    ORIGIN,
    OWNER,
    R8_VIA,
    RESOURCE,
    WRITE_SCOPES,
    _drop_database,
    _in_scope,
    _Seeder,
    live,
    redis_fake,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import kit_conformance as kc

pytestmark = _DB_GATE

#: The two members. ``app_user.email`` is unique over ALL orgs, so each org
#: holds its own address.
MEMBER = "agent-member@mcpwrites.test"
MEMBER_B = "agent-member-b@mcpwrites.test"
PROFILES = list(CLIENT_PROFILES)


# ═══════════════════════════════════════════════════════════════════════════
# The world: two tenants
# ═══════════════════════════════════════════════════════════════════════════


def _connection(seeder: _Seeder, member: str) -> tuple[str, str]:
    """A live write connection of *member* and one access token for it."""
    cid = str(seeder.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), true,"
        " now() + interval '1 day') RETURNING id",
        o=seeder.org, m=member, origin=ORIGIN, scopes=list(WRITE_SCOPES), pins=[]))
    raw = agent_tokens.new_token()
    seeder.one("INSERT INTO agent_token (organization_id, connection_id, kind, token_hash,"
               " expires_at, client_id, resource, scope) VALUES (CAST(:o AS uuid),"
               " CAST(:c AS uuid), 'access', :h, now() + interval '1 hour',"
               " 'https://client.example/meta.json', :r, :s) RETURNING id",
               o=seeder.org, c=cid, h=agent_tokens.hash_token(raw), r=RESOURCE,
               s=" ".join(WRITE_SCOPES))
    return cid, raw


def _tenant(eng, org: str, slug: str, member: str) -> SimpleNamespace:
    """One org: a member, a project in a space, its lanes, one connection."""
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, :s, :s)"), {"id": org, "s": slug})
    seeder = _Seeder(eng, org)
    # The gateway numbers the tasks that it makes from 1 (``pm_task_counters``),
    # so a seeded task starts high, as ``test_mcp_edge_task_writes.py`` does.
    seeder.number = 100_000
    seeder.member(member, ["feature:projects"])
    team = seeder.project(f"{slug} team", in_space=True)
    if member != MEMBER:  # ``project`` grants the first member only
        seeder.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
                   " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
                   " RETURNING project_id", p=team, s=member, a=OWNER, o=org)
    s = SimpleNamespace(
        todo=seeder.status(team, "To do", "todo", 0),
        doing=seeder.status(team, "Doing", "in_progress", 1),
        review=seeder.status(team, "In review", "in_progress", 2),
        done=seeder.status(team, "Done", "done", 3),
        cancelled=seeder.status(team, "Dropped", "cancelled", 4))
    cid, token = _connection(seeder, member)
    return SimpleNamespace(org=org, seeder=seeder, team=team, s=s, conn=cid, token=token,
                           member=member)


def _assigned_task(t: SimpleNamespace, title: str) -> str:
    """A fresh task in the TO DO lane that the member is assigned to."""
    task = t.seeder.task(t.team, t.s.todo, title)
    t.seeder.one("INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                 " VALUES (CAST(:t AS uuid), :w, :a) RETURNING task_id",
                 t=task, w=t.member, a=OWNER)
    return task


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog with two tenants."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_mcpg23b"
    _clone_promoted_database(admin_url, dedicated)
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
    w = SimpleNamespace(
        admin=eng, a=_tenant(eng, str(uuid.uuid4()), "mcpg23a", MEMBER),
        b=_tenant(eng, str(uuid.uuid4()), "mcpg23b", MEMBER_B),
        app_dsn=admin_url.set(database=dedicated, username=_APP_ROLE, password=_APP_PW
                              ).render_as_string(hide_password=False))
    yield w
    eng.dispose()
    _drop_database(admin_url, dedicated)


_WARM: list[bool] = []


@pytest.fixture
async def real(live, world, monkeypatch):  # noqa: F811 — a fixture
    """The real gateway and the real edge, writes and runs on.

    The first edge call of a cold Windows process can time out ("Metorite is
    busy", about 20 s). One call warms it, once, so no bound is loosened."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
    if not _WARM:
        _WARM.append(True)
        warm = RealClient(CLIENT_PROFILES["codex"], token=world.a.token, world=world)
        await warm.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "warm", "version": "0"}})
        await warm.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "my_work", "arguments": {}}})
    return live


# ═══════════════════════════════════════════════════════════════════════════
# The client: the Client of MCP-G23, on the real stack
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RealClient(Client):
    """``Client`` with one change: each request runs in the task scope of the
    non-privileged DSN, as every R8 suite of the edge does."""

    world: Any = None

    async def send(self, message, extra=None):
        async def go():
            return await Client.send(self, message, extra)
        return await _in_scope(self.world, go)

    async def call_raw(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One ``tools/call`` that may fail. Returns the whole result."""
        return await self.rpc("tools/call", {"name": name, "arguments": arguments})


def _client(w, tenant: SimpleNamespace, profile: str) -> RealClient:
    return RealClient(CLIENT_PROFILES[profile], token=tenant.token, world=w)


def _row(w, sql: str, **params) -> Any:
    with w.admin.begin() as c:
        return c.execute(text(sql), params).first()


def _task_status(w, task: str) -> str:
    return str(_row(w, "SELECT status_id FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                    t=task)[0])


def _runs(w, task: str) -> list[tuple[str, str]]:
    with w.admin.begin() as c:
        return [(str(r[0]), r[1]) for r in c.execute(text(
            "SELECT id, status FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"
            " ORDER BY created_at"), {"t": task})]


async def _open(client: RealClient, task: str) -> str:
    """Connect, list and read, as ``test_mcp_client_conformance._open``."""
    p = client.profile
    init = await client.connect()
    assert init["serverInfo"]["name"] == "metorite"
    client.listed_tools = (await client.rpc("tools/list"))["tools"]
    if "prompts/list" in p["sends"]:
        prompts = (await client.rpc("prompts/list"))["prompts"]
        assert [x["name"] for x in prompts][:1] == ["work_on_task"]
        assert (await client.rpc("prompts/get", {"name": p["reads_prompt"],
                                                 "arguments": {}}))["messages"]
    if "resources/list" in p["sends"]:
        assert (await client.rpc("resources/list"))["resources"] == []
    rows = (await client.call("my_work", {}))["rows"]
    mine = [r for r in rows if r["task_id"] == task]
    assert len(mine) == 1, rows
    return mine[0]["version"]


def _k(label: str) -> str:
    """An idempotency key that no other test of the shared database uses."""
    return f"g23b-{label}-{uuid.uuid4().hex[:12]}"


def _row_all(w, sql: str, **params) -> list[Any]:
    with w.admin.begin() as c:
        return list(c.execute(text(sql), params))


#: The tool log of the FINISH path: the disposition is a task status.
FINISH_TOOL_LOG = [
    "my_work", "get_task_context", "create_task", "update_task", "claim_task",
    "post_agent_activity", "get_task_context", "set_task_status", "finish_agent_run",
]
#: The tool log of the RELEASE path: the disposition is a release. The last
#: tool of ``MAIN_TOOL_LOG`` (``finish_agent_run``) is NOT in it. See
#: ``TestFinishAfterRelease`` for why.
RELEASE_TOOL_LOG = MAIN_TOOL_LOG[:-1]


async def _until_claimed(client: RealClient, t: SimpleNamespace, task: str,
                         ) -> tuple[str, dict[str, Any]]:
    """The common start of both paths, up to the first heartbeat. Returns the
    run id and the task that ``create_task`` made."""
    version = await _open(client, task)
    ctx = await client.call("get_task_context", {"task_id": task})
    assert ctx["task"]["id"] == task
    made = await client.call("create_task", {
        "project_id": t.team, "title": "A follow-up task", "status_id": t.s.todo,
        "idempotency_key": _k("create")})
    assert made["task"]["status"]["id"] == t.s.todo
    updated = await client.call("update_task", {
        "task_id": made["task"]["id"], "expected_version": made["task"]["version"],
        "title": "A follow-up task, renamed"})
    assert updated["task"]["version"] != made["task"]["version"]
    claimed = await client.call("claim_task", {"task_id": task, "expected_version": version})
    assert claimed["moved"] is True and claimed["agent_run_id"]
    run_id = claimed["agent_run_id"]
    thought = await client.call("post_agent_activity", {
        "run_id": run_id, "type": "thought", "body": "Reading the code.",
        "idempotency_key": _k("thought")})
    assert thought["activity"]["type"] == "thought"
    return run_id, made["task"]


async def finish_session(client: RealClient, t: SimpleNamespace, task: str) -> str:
    """The FINISH path: a task status is the disposition, then the finish."""
    run_id, _ = await _until_claimed(client, t, task)
    ctx = await client.call("get_task_context", {"task_id": task})
    moved = await client.call("set_task_status", {
        "task_id": task, "status_id": t.s.review, "expected_version": ctx["task"]["version"]})
    assert moved["status"]["id"] == t.s.review and moved["id"] == task
    finished = await client.call("finish_agent_run", {
        "run_id": run_id, "summary": "Moved the task to review.",
        "idempotency_key": _k("finish")})
    assert finished["run"]["status"] == "completed"
    return run_id


async def release_session(client: RealClient, t: SimpleNamespace, task: str) -> str:
    """The RELEASE path: a release is the disposition."""
    run_id, _ = await _until_claimed(client, t, task)
    released = await client.call("release_task", {"run_id": run_id})
    assert released["released"] is True
    return run_id


# ═══════════════════════════════════════════════════════════════════════════
# 1 and 2: the sessions, the schemas, the one disposition
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("name", PROFILES)
class TestAScriptedSessionOnTheRealGateway:

    async def test_the_finish_path_ends_with_one_status_and_one_completed_run(
            self, real, world, name):
        t = world.a
        task = _assigned_task(t, "finish path")
        client = _client(world, t, name)
        run_id = await finish_session(client, t, task)
        # (1) Each result passed ``jsonschema.validate`` in ``Client.call``.
        assert client.tool_calls == FINISH_TOOL_LOG
        # (2) The board shows the one disposition: the status, and ONE run.
        assert _task_status(world, task) == t.s.review
        assert _runs(world, task) == [(run_id, "completed")]
        # The scorer reads the transcript. Its disposition test reads
        # ``args.status``, and the real tool sends ``status_id`` (a finding).
        # So a status move is not a disposition to it. Pinned here.
        failed = {s.id for s in _score(client).steps if not s.passed}
        assert failed == {"worktree", "one_disposition", "finishes_run"}, failed

    async def test_the_release_path_ends_with_one_released_run(self, real, world, name):
        t = world.a
        task = _assigned_task(t, "release path")
        client = _client(world, t, name)
        run_id = await release_session(client, t, task)
        assert client.tool_calls == RELEASE_TOOL_LOG
        # The run is released, once. The task keeps its lane and its assignee.
        assert _runs(world, task) == [(run_id, "released")]
        assert _task_status(world, task) == t.s.doing
        assert (t.member,) in _row_all(
            world, "SELECT assignee FROM pm_task_assignees WHERE task_id = CAST(:t AS uuid)",
            t=task)
        failed = {s.id for s in _score(client).steps if not s.passed}
        # The release is a disposition for the scorer. The finish after it is
        # refused by the gateway, so the log has none (``TestFinishAfterRelease``).
        assert failed == {"worktree", "finishes_run"}, failed

    async def test_the_tasks_that_the_session_made_are_in_the_real_database(
            self, real, world, name):
        t = world.a
        client = _client(world, t, name)
        await release_session(client, t, _assigned_task(t, "made tasks"))
        row = _row(world, "SELECT project_id, organization_id FROM pm_tasks"
                          " WHERE title = 'A follow-up task, renamed'"
                          " AND organization_id = CAST(:o AS uuid) LIMIT 1", o=t.org)
        assert row is not None and str(row[0]) == t.team and str(row[1]) == t.org

    async def test_the_transcript_of_a_real_session_is_the_kit_6_jsonl(
            self, real, world, name, tmp_path):
        t = world.a
        client = _client(world, t, name)
        await release_session(client, t, _assigned_task(t, "transcript"))
        path = tmp_path / f"{name}.jsonl"
        path.write_text(_transcript(client), encoding="utf-8")
        header, events = kc.load(path)
        assert tuple(header) == kc.HEADER_KEYS
        assert sorted(kc.score(events, header).failed_ids) == ["finishes_run", "worktree"]

    async def test_the_schema_check_can_fail(self, real, world, name):
        """Mutation of (1): a schema that the result does not meet fails the
        session. The client checks each result against ``tools/list``."""
        t = world.a
        client = _client(world, t, name)
        await _open(client, _assigned_task(t, "schema mutation"))
        for listed in client.listed_tools:
            if listed["name"] == "my_work":
                listed["outputSchema"] = {**listed["outputSchema"], "required": ["nope"]}
        with pytest.raises(jsonschema.ValidationError):
            await client.call("my_work", {})


# ═══════════════════════════════════════════════════════════════════════════
# 3: a second tenant
# ═══════════════════════════════════════════════════════════════════════════


class TestTheSecondTenant:

    async def _org_a_run(self, world) -> tuple[str, str]:
        """A claim by org A that leaves its run open. Returns ``(task, run)``."""
        t = world.a
        task = _assigned_task(t, "org a secret task")
        client = _client(world, t, "claude_code")
        version = await _open(client, task)
        claimed = await client.call("claim_task", {"task_id": task, "expected_version": version})
        return task, claimed["agent_run_id"]

    async def test_org_b_sees_none_of_the_tasks_or_runs_of_org_a(self, real, world):
        a_task, a_run = await self._org_a_run(world)
        b_task = _assigned_task(world.b, "org b task")
        b = _client(world, world.b, "claude_code")
        await b.connect()
        b.listed_tools = (await b.rpc("tools/list"))["tools"]
        rows = (await b.call("my_work", {}))["rows"]
        seen = {r["task_id"] for r in rows}
        assert b_task in seen and a_task not in seen
        a_ids = {str(r[0]) for r in _row_all(
            world, "SELECT id FROM pm_tasks WHERE organization_id = CAST(:o AS uuid)",
            o=world.a.org)}
        assert not seen & a_ids
        listed = await b.call("list_agent_runs", {})
        assert a_run not in {r["id"] for r in listed["runs"]}
        # The reverse holds: org A cannot read the task of org B.
        a = _client(world, world.a, "claude_code")
        await a.connect()
        a.listed_tools = (await a.rpc("tools/list"))["tools"]
        denied = await a.call_raw("get_task_context", {"task_id": b_task})
        assert denied["isError"] is True

    async def test_org_b_cannot_claim_read_post_release_or_finish_a_run_of_org_a(
            self, real, world):
        a_task, a_run = await self._org_a_run(world)
        a_version = _row(world, "SELECT updated_at FROM pm_tasks WHERE id = CAST(:t AS uuid)",
                         t=a_task)[0].isoformat()
        b = _client(world, world.b, "claude_code")
        await b.connect()
        b.listed_tools = (await b.rpc("tools/list"))["tools"]
        attempts = {
            "get_task_context": {"task_id": a_task},
            "claim_task": {"task_id": a_task, "expected_version": a_version},
            "get_agent_run": {"run_id": a_run},
            "post_agent_activity": {"run_id": a_run, "type": "thought", "body": "mine now",
                                    "idempotency_key": _k("b-post")},
            "release_task": {"run_id": a_run},
            "finish_agent_run": {"run_id": a_run, "summary": "stolen",
                                 "idempotency_key": _k("b-finish")},
        }
        for tool, arguments in attempts.items():
            result = await b.call_raw(tool, arguments)
            assert result["isError"] is True, (tool, result)
            said = result["content"][0]["text"]
            assert a_task not in said and a_run not in said and "org a secret" not in said, tool
            # The answer is the same "not found" that an id of no org gets.
            assert said in (tools.TEXT_TASK_NOT_FOUND, tools.TEXT_RUN_NOT_FOUND), (tool, said)
        # M2: nothing of org A moved.
        assert _runs(world, a_task) == [(a_run, "in_progress")]
        assert _task_status(world, a_task) == world.a.s.doing
        assert _row_all(world, "SELECT assignee FROM pm_task_assignees"
                               " WHERE task_id = CAST(:t AS uuid)", t=a_task) == [
                                   (world.a.member,)]
        assert _row_all(world, "SELECT 1 FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                               " AND body ILIKE '%stolen%'", t=a_task) == []

    async def test_the_rows_of_org_a_are_hidden_by_row_level_security_too(
            self, real, world):
        """The same wall, one layer down: a tenant-bound session of org B reads
        no row of org A from ``pm_tasks`` or ``pm_agent_runs``."""
        a_task, _ = await self._org_a_run(world)
        with world.admin.begin() as c:
            c.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": world.b.org})
            tasks = c.execute(text("SELECT id FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                              {"t": a_task}).all()
            runs = c.execute(text("SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)"),
                             {"t": a_task}).all()
        assert tasks == [] and runs == []


# ═══════════════════════════════════════════════════════════════════════════
# 4: finish_agent_run on a RELEASED run
# ═══════════════════════════════════════════════════════════════════════════


async def _gateway(w, t: SimpleNamespace, method: str, path: str,
                   body: dict[str, Any] | None = None) -> httpx.Response:
    """One request that the test signs as the connection of *t*."""
    from gateway.main import app as gateway_app

    raw = json.dumps(body).encode() if body is not None else b""
    headers = agent_signing.signed_headers(
        KEY, organization_id=t.org, connection_id=t.conn, actor_via=R8_VIA,
        method=method, path=path, query="", body=raw)
    if method != "GET":
        headers["Content-Type"] = "application/json"

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(method, path, headers=headers, content=raw)
    return await _in_scope(w, go)


class TestFinishAfterRelease:
    """PR 2117 (MCP-G23) left this unchecked. Its ``MAIN_TOOL_LOG`` ends with
    ``release_task`` then ``finish_agent_run``, and KIT-6 scores
    ``finishes_run`` as "after the disposition". The FAKE board answered 200.

    **The REAL answer, pinned.** The gateway route is ``finish_agent_run`` in
    ``apps/services/gateway/gateway/routes/projects/agent_runs.py``
    (``@router.post("/agent-runs/{run_id}/finish")``). On a ``released`` run it
    answers **409**, with a summary and without one.

    * With a summary, the route calls ``_write_activity(..., event="agent_finish")``,
      and ``transition("released", "agent_finish")`` raises the 409 "This agent
      run is released. It cannot take this activity."
    * With no summary, the early return of the route covers ``_FINISHED``
      (``completed`` and ``stopped``) only, so ``released`` falls to
      ``transition`` too: the same 409.

    **What the edge shows the agent.** ``_finish_agent_run`` in
    ``agent_edge/tools.py`` maps the 409 to ``TEXT_RUN_CONFLICT``: "The agent
    run cannot take this now. Read it again with get_agent_run." For a finish
    with no summary it reads the run, and ``released`` is not a finished
    status, so it raises the same text. The agent gets ``isError`` true, and a
    read of the run then shows ``released``. The text is a finite step, not a
    retry loop. It does not say that the release already ended the run.

    Mutations (R7). M4: let ``agent_finish`` move ``released`` to
    ``completed`` in ``_MOVES`` -> the 409 tests go red. M5: drop
    ``texts={409: TEXT_RUN_CONFLICT}`` from the edge call -> the text test
    goes red.
    """

    PINNED_EDGE_TEXT = (
        "The agent run cannot take this now. Read it again with get_agent_run.")
    PINNED_GATEWAY_DETAIL = "This agent run is released. It cannot take this activity."

    async def _released(self, world) -> tuple[RealClient, str, str]:
        t = world.a
        task = _assigned_task(t, "finish after release")
        client = _client(world, t, "codex")
        run_id = await release_session(client, t, task)
        return client, task, run_id

    async def test_the_gateway_answers_409_for_a_finish_with_a_summary(self, real, world):
        _, task, run_id = await self._released(world)
        r = await _gateway(world, world.a, "POST", f"/projects/agent-runs/{run_id}/finish",
                           {"summary": "Done.", "idempotency_key": _k("g-fin")})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == self.PINNED_GATEWAY_DETAIL
        assert _runs(world, task) == [(run_id, "released")]

    async def test_the_gateway_answers_409_for_a_finish_with_no_summary(self, real, world):
        _, task, run_id = await self._released(world)
        r = await _gateway(world, world.a, "POST", f"/projects/agent-runs/{run_id}/finish",
                           {"idempotency_key": _k("g-fin0")})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == self.PINNED_GATEWAY_DETAIL
        assert _runs(world, task) == [(run_id, "released")]

    @pytest.mark.parametrize("with_summary", [True, False])
    async def test_the_agent_gets_the_conflict_text_and_a_read_shows_released(
            self, real, world, with_summary):
        client, task, run_id = await self._released(world)
        args: dict[str, Any] = {"run_id": run_id, "idempotency_key": _k("e-fin")}
        if with_summary:
            args["summary"] = "Done."
        result = await client.call_raw("finish_agent_run", args)
        assert result["isError"] is True
        assert result["content"][0]["text"] == self.PINNED_EDGE_TEXT
        assert "structuredContent" not in result
        # The text sends the agent to ``get_agent_run``: that shows the truth.
        seen = await client.call("get_agent_run", {"run_id": run_id, "wait_seconds": 0})
        assert seen["run"]["status"] == "released" and seen["run"]["state"] == "released"
        assert _runs(world, task) == [(run_id, "released")]
        # Not a loop: a second try gets the same finite answer, and no write.
        again = await client.call_raw("finish_agent_run", args)
        assert again["content"][0]["text"] == self.PINNED_EDGE_TEXT
        assert _runs(world, task) == [(run_id, "released")]

    async def test_a_second_release_is_a_200_no_op(self, real, world):
        """The contrast: the release route answers a ``released`` run with 200
        and no write (its docstring)."""
        client, task, run_id = await self._released(world)
        again = await client.call("release_task", {"run_id": run_id})
        assert again["released"] is True
        assert _runs(world, task) == [(run_id, "released")]


# ═══════════════════════════════════════════════════════════════════════════
# 5: the instructions
# ═══════════════════════════════════════════════════════════════════════════


class TestTheInstructionsOnTheRealStack:

    async def _instructions(self, world) -> str:
        client = _client(world, world.a, "claude_code")
        await client.connect()
        assert client.instructions
        return client.instructions

    async def test_the_instructions_keep_every_safety_sentence_before_char_4096(
            self, real, world):
        assert get_settings().agent_access_writes_enabled is True
        assert get_settings().agent_runs_enabled is True
        full = await self._instructions(world)
        assert len(full) > CUT  # Claude Code really cuts it.
        kept = full[:CUT]
        step1 = kept.find("1. At the start of a session, call my_work.")
        step2 = kept.find("2. Take the top row.")
        claim = kept.find("call claim_task with the task_id and the version of the row")
        assert -1 < step1 < step2 < claim
        for sentence in SAFETY_SENTENCES:
            at = kept.find(sentence)
            assert at != -1 and at + len(sentence) < CUT, sentence
        # The tools that the instructions name are the tools that the real edge lists.
        client = _client(world, world.a, "claude_code")
        await client.connect()
        listed = {x["name"] for x in (await client.rpc("tools/list"))["tools"]}
        assert set(ALL_PINNED) <= listed

    async def test_the_budget_check_can_fail(self, real, world, monkeypatch):
        """M3. Pad the run rules, and a safety rule ends after the cut."""
        monkeypatch.setattr(mcp, "_RUN_INSTRUCTIONS", " " + "x " * 600 + mcp._RUN_INSTRUCTIONS)
        kept = (await self._instructions(world))[:CUT]
        sentence = "call record_outside_decision at once."
        assert not (kept.find(sentence) != -1 and kept.find(sentence) + len(sentence) < CUT)
