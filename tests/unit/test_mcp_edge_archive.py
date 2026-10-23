"""WS-55 EA-6g — the archive with an approval (EA3-33 and EA3-40).

Spec: ``project-docs/specs/external_agent_access.md`` §6.1 ("EA3-40 in
full"), §9.2 (the archive), §9.5 (the second manifest exception), §12.1
("Who may do what", Notifications, EA12-8) and §15, the row EA-6g with the
acceptance G1 to G16.

An agent may archive a task only after the member of its connection
approves it on the task in Metorite. The agent calls ``request_task_archive``
(class B). The route writes an elicitation that the SERVER types, and only
the run's member answers it, through a route that the edge cannot call.

Two halves, as in ``test_mcp_edge_claim.py``.

**Database-free (always runs).** The edge app runs in process with a fake
token store and a fake gateway. It proves the tool record, the body that the
tool sends, the answer table, the two flags and the scope.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20), and the members answer through the person
path of the same app. It proves G1 to G14. G15 is the UI
(``agentRuns.test.ts`` and ``e2e/agent-runs.spec.ts``). G16 is the four
manifest fences: ``test_mcp_edge_coverage.py``,
``test_projects_chat_coverage.py``, ``test_projects_field_parity.py`` and
``test_projects_ui_actions.py``.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_mcp_edge_archive.py -v -rs

**The mutations (R7).** Each one turned the named test red. ``E`` is
``apps/services/agent_edge/agent_edge`` and ``G`` is
``apps/services/gateway/gateway/routes/projects``.

* M1 (G1). ``G/agent_runs.py`` ``_write_activity``: no ``content_metadata.request`` on the question. ``test_g1_the_request_writes_one_typed_question_and_archives_nothing``.
* M2 (G1). ``agent_oauth.MCP_WRITE_ROUTES`` gains the approval pair. ``test_g1_neither_archive_route_is_on_the_edge`` and ``test_mcp_edge_coverage.py::test_the_archive_and_its_approval_stay_off_the_edge``.
* M3 (G2). ``G/agent_runs.py`` ``archive_request_of``: any question with an option ``approve`` is a request. ``test_g2_a_free_question_with_an_approve_option_never_archives``.
* M4 (G2). ``G/agent_runs.py`` ``ActivityIn``: ``extra="ignore"``. ``test_g2_a_signed_body_that_names_request_gets_422``.
* M5 (G3). ``G/agent_runs.py`` ``approve_task_archive``: no check of ``run.member_email``. ``test_g3_another_member_gets_403_and_the_member_archives``.
* M6 (G3, rule 4). ``G/agent_runs.py`` ``_answer``: no refusal of a generic prompt on a typed request. ``test_g3_the_generic_route_refuses_a_typed_request``.
* M7 (G3). ``G/agent_runs.py`` ``approve_task_archive``: no ``pm.task.archived`` event. ``test_g3_another_member_gets_403_and_the_member_archives``.
* M8 (G4). ``G/agent_runs.py`` ``approve_task_archive``: the archive runs in its own ``_tenant_session``. ``test_g4_the_answer_and_the_archive_commit_together[answer]``. ⚠️ ``[archive]`` stays green under M8 (measured in fix round 1): the fault is inside the archive's own session, so that session rolls back too.
* M8b (G4). ``G/agent_runs.py`` ``approve_task_archive``: swallow a ``RuntimeError`` of the archive core and go on. ``test_g4_the_answer_and_the_archive_commit_together[archive]``.
* M9 (G5). ``G/agent_runs.py`` ``approve_task_archive``: ``include_subtasks=True``. ``test_g5_open_subtasks_stay_on_the_board``.
* M10 (G6). ``G/agent_runs.py`` ``approve_task_archive``: a decline archives too. ``test_g6_a_decline_archives_nothing``.
* M11 (G7). ``G/agent_runs.py`` ``_request_expired`` returns False. ``test_g7_an_approve_after_the_expiry_gets_409``.
* N1 (fix round 1). ``G/agent_runs.py`` ``approve_task_archive``: the expiry refuses a decline too. ``test_g7_a_decline_closes_an_expired_request``.
* N2 (fix round 1). ``G/agent_runs.py`` ``_answer``: an expired request still refuses a free reply. ``test_g7_a_free_reply_moves_a_run_on_after_the_expiry``.
* N3 (fix round 1). ``G/agent_runs.py`` ``approve_task_archive``: no expiry check at all. ``test_g7_an_approve_after_the_expiry_gets_409``.
* N4 (fix round 1, 4a). ``G/agent_runs.py`` ``approve_task_archive``: no refusal of an agent caller. ``test_the_approval_route_refuses_an_agent_past_branch_0``.
* N5 (fix round 1, 4b). ``G/agent_runs.py`` ``_request_expired``: no check of a naive stamp. ``test_a_naive_expiry_stamp_reads_as_expired``.
* N6 (fix round 1, 4c). ``G/agent_runs.py`` ``request_task_archive``: no ``load_visible_task``. ``test_a_task_out_of_reach_in_the_same_org_is_not_found``.
* N7 (fix round 1, item 6). ``G/agent_runs.py`` ``approve_task_archive``: a replay returns no task. ``test_g8_a_second_approve_gives_one_archive``.
* N8 (fix round 1, item 7). ``E/tools.py`` ``GET_AGENT_RUN``: the old text, "post one response". ``test_a_stopping_run_is_finished_with_no_activity_first``.
* M12 (G7). ``G/agent_runs.py`` ``approve_task_archive``: no ``awaiting`` check. ``test_g7_an_approve_on_a_run_that_does_not_wait_gets_409``.
* M13 (G8). ``G/agent_runs.py`` ``approve_task_archive``: no replay of the key. ``test_g8_a_second_approve_gives_one_archive``.
* M14 (G9). ``G/tasks.py`` ``archive_task_in``: write the ``system`` row when the task is already archived. ``test_g9_an_archived_task_gets_no_second_activity_row``.
* M15 (G10). ``G/agent_runs.py`` ``approve_task_archive``: no ``_load_visible_run``. ``test_g10_a_member_who_lost_access_gets_404``.
* M16 (G11). ``G/agent_runs.py`` ``_comment``: a typed request notifies ``task_audience()`` too. ``test_g11_the_question_reaches_the_runs_member_only``.
* M17 (G12). ``G/agent_runs.py``: the approval route loses ``dependencies=_ENABLED``. ``test_g12_with_the_runs_flag_off_the_routes_give_404``.
* M18 (G12). ``E/tools.py`` ``REQUEST_TASK_ARCHIVE``: no ``runs=True``. ``test_g12_the_flags_hide_the_tool_and_send_nothing``.
* M19 (G12). ``E/tools.py`` ``REQUEST_TASK_ARCHIVE.required_scope`` is ``projects:read``. ``test_g12_a_read_connection_gets_the_step_up_403``.
* M20 (G13). ⚠️ Two layers at once. The app role of this file gets ``BYPASSRLS``, AND ``approve_task_archive`` drops ``_load_visible_run``. ``test_g13_organization_b_gets_404``. Each layer alone stays green, by design: FORCE RLS hides B's run, and the ``vis_org`` predicate of ``task_visibility_clause`` and of the grant closure hides B's task. ``BYPASSRLS`` alone, and ``BYPASSRLS`` with the outer ``vis_org`` set to TRUE, were both measured green.
* M21 (G14). ``E/tools.py`` ``run_activity``: ``archived`` is always None. ``test_g14_get_agent_run_shows_the_answer_and_archived`` and ``test_the_activity_keys_carry_archived``.
* M22 (G14). ``G/agent_runs.py`` ``approve_task_archive``: no ``archived`` in the answer's content. ``test_g14_get_agent_run_shows_the_answer_and_archived`` and ``test_g6_a_decline_archives_nothing``.
* M23. ``E/tools.py`` ``_request_task_archive``: the body names ``task_id`` too. ``test_it_posts_the_key_only``.
* M24. ``E/tools.py`` ``_request_task_archive``: no 409 text. ``test_the_answer_table[409]`` and ``test_a_request_while_a_question_waits_is_refused``.
* M25. ``G/agent_runs.py`` ``request_task_archive``: a member may send the request. ``test_a_member_does_not_send_the_agents_request``.
* M26 (G16). The approval row leaves ``manifest.py``. ``test_projects_chat_coverage.py::test_every_projects_route_is_mapped_or_excluded``.
* M27 (G16). The approval row is class B. ``test_mcp_edge_coverage.py::test_the_archive_and_its_approval_stay_off_the_edge``.
* M28. ``G/agent_runs.py`` ``request_task_archive``: no refusal of a stopping run. ``test_a_request_on_a_stopping_run_is_refused``.

G15 is the UI. ``agentRuns.test.ts`` and ``e2e/agent-runs.spec.ts`` hold it. Their mutations are U1 to U6 and P1 to P5, listed in the pull request.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_oauth, agent_tokens
from acb_common import get_settings
from agent_edge import app as edge_app_module
from agent_edge import tools
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder
from tests.unit.test_mcp_edge_agent_runs import _run_row

_DB_GATE = w._DB_GATE
GOOD, TASK = w.GOOD, w.TASK
MEMBER = w.MEMBER
OTHER = "other-member@mcpwrites.test"
VIA = w.VIA

#: The texts the agent reads (§9.6). A gateway text never reaches it.
TASK_NOT_FOUND = "Task not found."
REQUEST_PATH = f"/projects/tasks/{TASK}/archive-request"

_post, _rpc, _call, _tool, _text = w._post, w._rpc, w._call, w._tool, w._text
edge = w.edge


def _key() -> str:
    return f"k-{uuid.uuid4()}"


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    assert result["isError"] is False, result
    return result["structuredContent"]


# ═══════════════════════════════════════════════════════════════════════════
# The edge, database-free
# ═══════════════════════════════════════════════════════════════════════════

RUN = w._uuid(6001)
QUESTION = w._uuid(6002)


def _typed_question() -> dict[str, Any]:
    return {
        "id": QUESTION, "run_id": RUN, "task_id": TASK, "seq": 4, "type": "elicitation",
        "content": {"body": "The agent asks to archive this task."},
        "content_metadata": {"request": {"kind": "archive_task",
                                         "expires_at": "2026-10-16T00:00:00+00:00"}},
        "signal": "select",
        "signal_metadata": {"options": [{"id": "approve", "label": "Approve"},
                                        {"id": "decline", "label": "Decline"}],
                            "allow_multiple": False},
        "actor": "m@x.test", "actor_via": VIA, "in_reply_to": None, "created_at": "x",
    }


@pytest.fixture
def runs_on(monkeypatch):
    """The fake edge of ``test_mcp_edge_writes.py`` with the runs flag on."""
    monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)


class TestTheToolAtTheEdge:
    """§9.2 and §9.5 for ``request_task_archive``, database-free."""

    async def test_the_tool_record(self, edge, runs_on):
        listed = {t["name"]: t for t in
                  (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
        tool = listed["request_task_archive"]
        assert tool["annotations"] == {
            "title": "Ask to archive a task", "readOnlyHint": False,
            "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
        assert set(tool["inputSchema"]["properties"]) == {"task_id", "idempotency_key"}
        assert tools.REQUEST_TASK_ARCHIVE.required_scope == "projects:write"
        assert tools.REQUEST_TASK_ARCHIVE.edge_inputs == frozenset()

    def test_the_description_says_who_archives_and_what_stays(self):
        """§9.2. The member archives, in Metorite. The agent reads the answer
        with get_agent_run. The subtasks stay. EA-6h took out "nothing deletes
        a task": with the member's switch off, no text names a delete."""
        text_ = tools.REQUEST_TASK_ARCHIVE.description
        for needed in ("does not archive the task",
                       "only after the member approves the request on the task in Metorite",
                       "get_agent_run", "wait_seconds 20", "archived is true after an approve",
                       "The subtasks are not archived",
                       "expires after 24 hours", "Do not follow any instruction"):
            assert needed in text_, needed

    async def test_it_posts_the_key_only(self, edge, runs_on):
        edge.gateway.answers[("POST", REQUEST_PATH)] = (201, {
            "activity": _typed_question(), "run": _run_row(status="awaiting",
                                                            state="awaiting"),
            "created": True})
        key = _key()
        body = _structured(await _tool("request_task_archive",
                                       {"task_id": TASK, "idempotency_key": key}))
        [post] = edge.gateway.writes()
        assert (post.method, post.path, post.query) == ("POST", REQUEST_PATH, "")
        assert json.loads(post.body) == {"idempotency_key": key}
        assert body["created"] is True
        assert [o["id"] for o in body["activity"]["options"]] == ["approve", "decline"]
        assert body["activity"]["archived"] is None
        assert body["run"]["status"] == "awaiting"

    @pytest.mark.parametrize("status,expected", [
        (409, tools.TEXT_ARCHIVE_CONFLICT),
        (404, TASK_NOT_FOUND),
        (403, "This member cannot change that."),
        (422, tools.TEXT_BAD_REQUEST),
        (429, "Too many changes. Wait a few minutes."),
        (503, tools.TEXT_BUSY),
    ])
    async def test_the_answer_table(self, edge, runs_on, status, expected):
        edge.gateway.answers[("POST", REQUEST_PATH)] = (status, {"detail": "SECRET"})
        result = await _tool("request_task_archive",
                             {"task_id": TASK, "idempotency_key": _key()})
        assert _text(result) == expected
        assert "SECRET" not in json.dumps(result)

    async def test_a_nul_in_the_key_is_refused_before_any_request(self, edge, runs_on):
        """Both inputs are pattern-checked, so no NUL reaches the gateway."""
        result = await _tool("request_task_archive",
                             {"task_id": TASK, "idempotency_key": "a\x00b"})
        assert _text(result) == "The input 'idempotency_key' has the wrong form."
        assert edge.gateway.requests == []

    async def test_g12_the_flags_hide_the_tool_and_send_nothing(
            self, edge, runs_on, monkeypatch):
        names = {t["name"] for t in
                 (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
        assert "request_task_archive" in names
        settings = get_settings()
        for flag, text_ in (("agent_runs_enabled", tools.TEXT_RUNS_OFF),
                            ("agent_access_writes_enabled", tools.TEXT_WRITES_OFF)):
            monkeypatch.setattr(settings, flag, False)
            names = {t["name"] for t in
                     (await _post(_rpc("tools/list"))).json()["result"]["tools"]}
            assert "request_task_archive" not in names, flag
            result = await _tool("request_task_archive",
                                 {"task_id": TASK, "idempotency_key": _key()})
            assert _text(result) == text_, flag
            monkeypatch.setattr(settings, flag, True)
        # EA-6h: `tools/list` reads the member's delete switch. No call of the
        # tool reached the gateway.
        assert [r.path for r in edge.gateway.requests
                if r.path != tools.AGENT_ACCESS_ROUTE] == []

    async def test_g12_a_read_connection_gets_the_step_up_403(self, edge, runs_on):
        edge.tokens[GOOD] = w._lookup(scope="projects:read", scopes=("projects:read",))
        r = await _post(_call("request_task_archive",
                              {"task_id": TASK, "idempotency_key": _key()}))
        assert r.status_code == 403
        assert r.headers["www-authenticate"] == w.CHALLENGE_403_WRITE
        assert edge.gateway.requests == []

    async def test_the_approval_route_refuses_an_agent_past_branch_0(self, monkeypatch):
        """Fix round 1 (4a). Branch 0 keeps a signed request out (G1). The
        route refuses an agent caller too, before it opens a session."""
        from fastapi import HTTPException
        from gateway.routes.projects import agent_runs as gateway_runs

        opened: list[bool] = []

        def no_session() -> Any:
            opened.append(True)
            raise AssertionError("the route opened a session for an agent")

        monkeypatch.setattr(gateway_runs, "_tenant_session", no_session)
        agent = SimpleNamespace(actor_via=VIA, scopes=("projects:read", "projects:write"),
                                connection_id=w.CONN)
        user = SimpleNamespace(email=MEMBER, agent=agent)
        with pytest.raises(HTTPException) as refused:
            await gateway_runs.approve_task_archive(
                RUN, gateway_runs.ArchiveApprovalIn(decision="approve",
                                                    idempotency_key=_key()), user=user)
        assert refused.value.status_code == 403
        assert opened == []

    def test_a_naive_expiry_stamp_reads_as_expired(self):
        """Fix round 1 (4b). A stamp with no zone never approves."""
        from gateway.routes.projects.agent_runs import _request_expired

        now = datetime.now(UTC)
        assert _request_expired({"expires_at": "2999-01-01T00:00:00"}, now) is True
        assert _request_expired({"expires_at": "soon"}, now) is True
        assert _request_expired({"expires_at": "2999-01-01T00:00:00+00:00"}, now) is False

    def test_a_stopping_run_is_finished_with_no_activity_first(self):
        """Fix round 1 (item 7). An activity on a stopping run ends the run,
        and a finish after it gets 409. So the two descriptions say: call
        finish_agent_run with a summary, and post no activity first."""
        from gateway.routes.projects.agent_runs import transition

        assert transition("stopping", "agent_finish") == "stopped"
        assert transition("stopping", "agent_activity") == "stopped"
        for tool in (tools.GET_AGENT_RUN, tools.LIST_AGENT_RUNS):
            text_ = tool.description
            assert "finish_agent_run with a summary" in text_, tool.name
            assert "no activity before" in text_, tool.name
            assert "post one response" not in text_, tool.name

    def test_the_activity_keys_carry_archived(self):
        assert tools.RUN_ACTIVITY_KEYS[-1] == "archived"
        row = {"id": "a", "type": "prompt", "content": {"selected": ["approve"],
                                                        "archived": True}}
        assert tools.run_activity(row)["archived"] is True
        assert tools.run_activity({"id": "a", "type": "prompt", "content": {
            "archived": "yes"}})["archived"] is None
        assert tools.run_activity({"id": "a", "type": "thought",
                                   "content": {}})["archived"] is None


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app on a promoted catalog, as a NON-privileged role
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_mcparchive"
_APP_PW = "mcparchive_scratch_pw"


def _grant(seeder: w._Seeder, project: str, who: str) -> None:
    seeder.one("INSERT INTO pm_project_grants (project_id, subject, created_by,"
               " organization_id) VALUES (CAST(:p AS uuid), :s, :a, CAST(:o AS uuid))"
               " RETURNING project_id", p=project, s=who, a=w.OWNER, o=seeder.org)


def _no_personal_connection(a: w._Seeder) -> tuple[str, str]:
    """A write connection whose member opted out of the personal projects."""
    cid = str(a.one(
        "INSERT INTO agent_connection (organization_id, member_email, client_id,"
        " client_name, client_origin, scopes, pinned_projects, include_personal,"
        " expires_at) VALUES (CAST(:o AS uuid), :m, 'https://client.example/meta.json',"
        " 'Client', :origin, CAST(:scopes AS text[]), CAST(:pins AS uuid[]), false,"
        " now() + interval '1 day') RETURNING id",
        o=a.org, m=MEMBER, origin=w.ORIGIN, scopes=list(w.WRITE_SCOPES), pins=[]))
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
    """A private promoted catalog: two members, two projects, a second org.

    ``team`` is granted to both members. ``lost`` is granted to the member
    only, and a test takes that grant away (G10). Organization B has one task
    and one typed request of its own (G13).
    """
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_mcparchive"
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
                       "VALUES (:id, 'mcparchive', 'mcparchive')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 400_000
    a.member(MEMBER, ["feature:projects"])
    a.member(OTHER, ["feature:projects"])
    team, lost = a.project("Team"), a.project("Lost")
    _grant(a, team, OTHER)
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
        done=a.status(team, "Done", "done", 2),
        lost_todo=a.status(lost, "To do", "todo", 0),
    )
    # Fix round 1 (4c): a project with no grant to the member, and the
    # member's own personal project.
    private = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses) VALUES ('Private', 'active', 'manual', :a,"
        " CAST(:o AS uuid), 'Asia/Kolkata', true) RETURNING id", a=w.OWNER, o=org))
    mine = str(a.one(
        "INSERT INTO pm_projects (name, status, source, created_by, organization_id,"
        " timezone, owns_statuses, personal_owner) VALUES ('Personal mine', 'active',"
        " 'manual', :m, CAST(:o AS uuid), 'Asia/Kolkata', true, :m) RETURNING id",
        m=MEMBER, o=org))
    _grant(a, mine, MEMBER)
    s.private_todo = a.status(private, "To do", "todo", 0)
    s.mine_todo = a.status(mine, "To do", "todo", 0)
    # A second organization, with one task and one typed request in it.
    org_b = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'mcparchiveb', 'mcparchiveb')"), {"id": org_b})
    b = w._Seeder(eng, org_b)
    b.number = 500_000
    b_project = b.project("B team")
    b_task = b.task(b_project, b.status(b_project, "To do", "todo", 0), "b task")
    b_run = str(b.one(
        "INSERT INTO pm_agent_runs (organization_id, task_id, member_email, client_via,"
        " status, started_by) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m, :v,"
        " 'awaiting', :v) RETURNING id", o=org_b, t=b_task, m=MEMBER, v=VIA))
    expires = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    b.one("INSERT INTO pm_agent_run_activities (organization_id, run_id, task_id, type,"
          " content, content_metadata, signal, signal_metadata, actor, actor_via)"
          " VALUES (CAST(:o AS uuid), CAST(:r AS uuid), CAST(:t AS uuid), 'elicitation',"
          " CAST(:c AS jsonb), CAST(:cm AS jsonb), 'select', CAST(:sm AS jsonb), :m, :v)"
          " RETURNING id", o=org_b, r=b_run, t=b_task, m=MEMBER, v=VIA,
          c=json.dumps({"body": "Archive?"}),
          cm=json.dumps({"request": {"kind": "archive_task", "expires_at": expires}}),
          sm=json.dumps({"options": [{"id": "approve", "label": "Approve"},
                                     {"id": "decline", "label": "Decline"}],
                         "allow_multiple": False}))
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, lost=lost, s=s,
                             private=private, mine=mine, c={}, tok={}, task=a.task,
                             org_b=SimpleNamespace(task=b_task, run=b_run))
    for name, scopes in (("write", w.WRITE_SCOPES), ("read", ("projects:read",))):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.c["pinned"], world_.tok["pinned"] = a.connection(scopes=w.WRITE_SCOPES,
                                                            pins=(team,))
    world_.c["no_personal"], world_.tok["no_personal"] = _no_personal_connection(a)
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


@pytest.fixture
def events(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    """Every event that the two route modules emit after their commit."""
    from gateway.routes.projects import agent_runs as gateway_runs
    from gateway.routes.projects import tasks as gateway_tasks

    seen: list[tuple[str, dict[str, Any]]] = []
    for module in (gateway_runs, gateway_tasks):
        real = module.emit

        async def record(event: str, payload: dict[str, Any], real=real) -> None:
            seen.append((event, payload))
            await real(event, payload)

        monkeypatch.setattr(module, "emit", record)
    return seen


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _agent(world, tool: str, name: str = "write", **arguments: Any) -> dict[str, Any]:
    return await tw._mcp(world, name, tool, arguments)


async def _ask_archive(world, task: str, key: str | None = None) -> dict[str, Any]:
    return _structured(await _agent(world, "request_task_archive", task_id=task,
                                    idempotency_key=key or _key()))


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


async def _answer(world, run_id: str, decision: str = "approve", *, email: str = MEMBER,
                  key: str | None = None) -> httpx.Response:
    return await _person(world, email, "POST",
                         f"/projects/agent-runs/{run_id}/archive-approval",
                         {"decision": decision, "idempotency_key": key or _key()})


def _rows(world, sql: str, **params: Any) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(sql), params))


def _archived_at(world, task: str) -> Any:
    return tw._row(world, task).archived_at


def _system_rows(world, task: str) -> int:
    return len(_rows(world, "SELECT id FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
                            " AND type = 'system'", t=task))


def _run_activities(world, run_id: str) -> list[Any]:
    return _rows(world, "SELECT * FROM pm_agent_run_activities WHERE run_id ="
                        " CAST(:r AS uuid) ORDER BY seq", r=run_id)


def _run_status(world, run_id: str) -> str:
    [row] = _rows(world, "SELECT status FROM pm_agent_runs WHERE id = CAST(:r AS uuid)",
                  r=run_id)
    return str(row.status)


def _archived_events(events: list[tuple[str, dict[str, Any]]], task: str) -> int:
    return sum(1 for e, p in events if e == "pm.task.archived" and p.get("task_id") == task)


def _snapshot(world, task: str, run_id: str) -> tuple[Any, ...]:
    """What a refused answer must leave as it was."""
    return (_archived_at(world, task), _system_rows(world, task),
            len(_run_activities(world, run_id)), _run_status(world, run_id))


def _subtask(world, parent: str, title: str) -> str:
    """An open subtask of *parent*, in ``team``."""
    world.seeder.number += 1
    return str(world.seeder.one(
        "INSERT INTO pm_tasks (title, project_id, root_project_id, status_id,"
        " created_by, organization_id, task_number, parent_task_id) VALUES (:t,"
        " CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), :a, CAST(:o AS uuid),"
        " :n, CAST(:parent AS uuid)) RETURNING id",
        t=title, p=world.team, s=world.s.todo, a=w.OWNER, o=world.org,
        n=world.seeder.number, parent=parent))


def _expire(world, question_id: str) -> None:
    """Move the request's ``expires_at`` one second into the past."""
    world.seeder.one(
        "UPDATE pm_agent_run_activities SET content_metadata = jsonb_set("
        " content_metadata, '{request,expires_at}', to_jsonb(CAST(:at AS text)))"
        " WHERE id = CAST(:a AS uuid) RETURNING id",
        at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(), a=question_id)


async def _requested(world, title: str) -> tuple[str, str, dict[str, Any]]:
    """A new task in ``team`` and a typed request on it: ``(task, run, body)``."""
    task = world.task(world.team, world.s.doing, title)
    body = await _ask_archive(world, task)
    return task, body["run"]["id"], body


# ── The acceptance ──────────────────────────────────────────────────────────


class TestTheRequest:
    """G1, G2 and G11: the request writes a question and archives nothing."""

    pytestmark = _DB_GATE

    async def test_g1_the_request_writes_one_typed_question_and_archives_nothing(self, runs):
        task, run_id, body = await _requested(runs, "g1")
        assert body["created"] is True
        assert body["run"]["status"] == "awaiting"
        question = body["activity"]
        assert question["type"] == "elicitation" and question["author"] == "agent"
        assert [o["id"] for o in question["options"]] == ["approve", "decline"]
        [row] = _run_activities(runs, run_id)
        request = row.content_metadata["request"]
        assert request["kind"] == "archive_task"
        expires = datetime.fromisoformat(request["expires_at"])
        left = expires - datetime.now(UTC)
        # The stamp comes from the database clock, and a container clock can
        # run a little ahead of the host. One minute of skew is allowed.
        assert timedelta(hours=23, minutes=55) < left <= timedelta(hours=24, minutes=1)
        assert _archived_at(runs, task) is None
        assert _run_status(runs, run_id) == "awaiting"

    async def test_g1_neither_archive_route_is_on_the_edge(self, runs):
        """A signed request to the archive or to the approval gets 401 from
        branch 0, and the task stays on the board."""
        task, run_id, _ = await _requested(runs, "g1 signed")
        for path, body in ((f"/projects/tasks/{task}/archive", {}),
                           (f"/projects/agent-runs/{run_id}/archive-approval",
                            {"decision": "approve", "idempotency_key": _key()})):
            assert ("POST", path) not in agent_oauth.MCP_ROUTES
            r = await tw._send(runs, "POST", path, body=body)
            assert r.status_code == 401, (path, r.text)
        assert _archived_at(runs, task) is None
        assert _run_status(runs, run_id) == "awaiting"

    async def test_the_same_key_returns_the_first_request(self, runs):
        task = runs.task(runs.team, runs.s.doing, "replay")
        key = _key()
        first = await _ask_archive(runs, task, key)
        again = await _ask_archive(runs, task, key)
        assert again["created"] is False
        assert again["activity"]["id"] == first["activity"]["id"]
        assert len(_run_activities(runs, first["run"]["id"])) == 1
        assert len(_rows(runs, "SELECT id FROM pm_agent_runs WHERE task_id ="
                               " CAST(:t AS uuid)", t=task)) == 1

    async def test_a_member_does_not_send_the_agents_request(self, runs):
        """A member archives with the archive route. The request is the
        agent's, so a person gets 403 and no run starts."""
        task = runs.task(runs.team, runs.s.doing, "member request")
        r = await _person(runs, MEMBER, "POST", f"/projects/tasks/{task}/archive-request",
                          {"idempotency_key": _key()})
        assert r.status_code == 403, r.text
        assert _rows(runs, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_request_on_a_stopping_run_is_refused(self, runs):
        """A Stop waits, so the agent must stop. The request writes nothing,
        and no orphan request sits on a stopped run."""
        task = runs.task(runs.team, runs.s.doing, "stopping")
        started = _structured(await _agent(runs, "start_agent_run", task_id=task,
                                           idempotency_key=_key()))
        run_id = started["run"]["id"]
        stop = await _person(runs, MEMBER, "POST", f"/projects/agent-runs/{run_id}/activities",
                             {"type": "prompt", "signal": "stop", "body": "Stop.",
                              "idempotency_key": _key()})
        assert stop.status_code == 201, stop.text
        before = _snapshot(runs, task, run_id)
        result = await _agent(runs, "request_task_archive", task_id=task,
                              idempotency_key=_key())
        assert _text(result) == tools.TEXT_ARCHIVE_CONFLICT
        assert _snapshot(runs, task, run_id) == before

    @pytest.mark.parametrize("case", ["outside_pins", "personal_off", "private_project"])
    async def test_a_task_out_of_reach_in_the_same_org_is_not_found(self, runs, case):
        """Fix round 1 (4c). The agent scope of §7.3 and the grants hold the
        request, not only the tenant: no run starts and nothing is asked."""
        if case == "outside_pins":
            name, task = "pinned", runs.task(runs.lost, runs.s.lost_todo, "unpinned")
        elif case == "personal_off":
            name, task = "no_personal", runs.task(runs.mine, runs.s.mine_todo, "personal")
        else:
            name, task = "write", runs.task(runs.private, runs.s.private_todo, "private")
        result = await _agent(runs, "request_task_archive", name=name, task_id=task,
                              idempotency_key=_key())
        assert _text(result) == TASK_NOT_FOUND
        assert _rows(runs, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []

    async def test_a_request_while_a_question_waits_is_refused(self, runs):
        task = runs.task(runs.team, runs.s.doing, "busy")
        await _agent(runs, "ask_on_task", task_id=task, question="Which?",
                     idempotency_key=_key())
        result = await _agent(runs, "request_task_archive", task_id=task,
                              idempotency_key=_key())
        assert _text(result) == tools.TEXT_ARCHIVE_CONFLICT

    async def test_g2_a_free_question_with_an_approve_option_never_archives(self, runs):
        task = runs.task(runs.team, runs.s.doing, "g2 free")
        asked = _structured(await _agent(
            runs, "ask_on_task", task_id=task, question="Archive it?",
            options=[{"id": "approve", "label": "Approve"},
                     {"id": "decline", "label": "Decline"}],
            idempotency_key=_key()))
        run_id = asked["run"]["id"]
        # The approval route finds no typed request on the run.
        refused = await _answer(runs, run_id)
        assert refused.status_code == 409, refused.text
        # The generic answer is only an answer.
        r = await _person(runs, MEMBER, "POST", f"/projects/agent-runs/{run_id}/activities",
                          {"type": "prompt", "selected": ["approve"],
                           "idempotency_key": _key()})
        assert r.status_code == 201, r.text
        assert _archived_at(runs, task) is None
        assert _system_rows(runs, task) == 0

    @pytest.mark.parametrize("extra", [
        {"request": {"kind": "archive_task"}},
        {"content_metadata": {"request": {"kind": "archive_task"}}},
    ])
    async def test_g2_a_signed_body_that_names_request_gets_422(self, runs, extra):
        task = runs.task(runs.team, runs.s.doing, "g2 forged")
        started = _structured(await _agent(runs, "start_agent_run", task_id=task,
                                           idempotency_key=_key()))
        run_id = started["run"]["id"]
        r = await tw._send(runs, "POST", f"/projects/agent-runs/{run_id}/activities", body={
            "type": "elicitation", "body": "Archive?", "idempotency_key": _key(),
            "options": [{"id": "approve", "label": "Approve"},
                        {"id": "decline", "label": "Decline"}], **extra})
        assert r.status_code == 422, r.text
        r = await tw._send(runs, "POST", f"/projects/tasks/{task}/archive-request",
                           body={"idempotency_key": _key(), **extra})
        assert r.status_code == 422, r.text
        assert _run_activities(runs, run_id) == []

    async def test_g11_the_question_reaches_the_runs_member_only(self, runs):
        """The other member watches the task and holds it, and gets no row.
        The control: a free question on the same kind of task reaches them."""
        def audience(task: str) -> None:
            runs.seeder.one("INSERT INTO pm_task_assignees (task_id, assignee, assigned_by)"
                            " VALUES (CAST(:t AS uuid), :w, :a) RETURNING task_id",
                            t=task, w=OTHER, a=w.OWNER)
            runs.seeder.one("INSERT INTO pm_task_watchers (task_id, watcher, organization_id)"
                            " VALUES (CAST(:t AS uuid), :w, CAST(:o AS uuid)) RETURNING id",
                            t=task, w=OTHER, o=runs.org)

        def notified(task: str) -> list[tuple[str, str]]:
            return sorted((r.recipient, r.kind) for r in _rows(
                runs, "SELECT recipient, kind FROM pm_notifications WHERE task_id ="
                      " CAST(:t AS uuid)", t=task))

        typed = runs.task(runs.team, runs.s.doing, "g11 typed")
        audience(typed)
        await _ask_archive(runs, typed)
        assert notified(typed) == [(MEMBER, "agent_question")]

        free = runs.task(runs.team, runs.s.doing, "g11 free")
        audience(free)
        await _agent(runs, "ask_on_task", task_id=free, question="Which?",
                     idempotency_key=_key())
        assert notified(free) == [(MEMBER, "agent_question"), (OTHER, "agent_question")]


class TestTheApproval:
    """G3 to G10, G13 and G14: only the run's member answers."""

    pytestmark = _DB_GATE

    async def test_g3_another_member_gets_403_and_the_member_archives(self, runs, events):
        task, run_id, _ = await _requested(runs, "g3")
        before = _snapshot(runs, task, run_id)
        refused = await _answer(runs, run_id, email=OTHER)
        assert refused.status_code == 403, refused.text
        assert _snapshot(runs, task, run_id) == before
        ok = await _answer(runs, run_id)
        assert ok.status_code == 200, ok.text
        assert ok.json()["archived"] is True
        assert _archived_at(runs, task) is not None
        assert _system_rows(runs, task) == 1
        assert _archived_events(events, task) == 1
        answer = _run_activities(runs, run_id)[-1]
        assert answer.type == "prompt" and answer.actor == MEMBER
        assert answer.content["selected"] == ["approve"] and answer.content["archived"] is True
        assert _run_status(runs, run_id) == "in_progress"

    async def test_g3_the_generic_route_refuses_a_typed_request(self, runs):
        """EA3-40 rule 4. ``selected`` gets 422, and a body alone gets 409,
        from either member. A Stop stays every member's right."""
        task, run_id, _ = await _requested(runs, "g3 generic")
        before = _snapshot(runs, task, run_id)
        path = f"/projects/agent-runs/{run_id}/activities"
        for email in (MEMBER, OTHER):
            r = await _person(runs, email, "POST", path, {
                "type": "prompt", "selected": ["approve"], "idempotency_key": _key()})
            assert r.status_code == 422, r.text
            r = await _person(runs, email, "POST", path, {
                "type": "prompt", "body": "Yes, archive it.", "idempotency_key": _key()})
            assert r.status_code == 409, r.text
        assert _snapshot(runs, task, run_id) == before
        stop = await _person(runs, OTHER, "POST", path, {
            "type": "prompt", "signal": "stop", "body": "Stop.", "idempotency_key": _key()})
        assert stop.status_code == 201, stop.text
        late = await _answer(runs, run_id)
        assert late.status_code == 409, late.text
        assert _archived_at(runs, task) is None

    @pytest.mark.parametrize("where", ["answer", "archive"])
    async def test_g4_the_answer_and_the_archive_commit_together(
            self, runs, monkeypatch, where):
        """A fault after the archive wrote, or after the answer wrote, rolls
        back both."""
        from gateway.routes.projects import agent_runs as gateway_runs

        task, run_id, _ = await _requested(runs, f"g4 {where}")
        before = _snapshot(runs, task, run_id)
        if where == "archive":
            real = gateway_runs.archive_task_in

            async def archive_then_fail(*args: Any, **kwargs: Any) -> Any:
                await real(*args, **kwargs)
                raise RuntimeError("injected after the archive")

            monkeypatch.setattr(gateway_runs, "archive_task_in", archive_then_fail)
        else:
            async def fail(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError("injected after the answer")

            # The last step of the answer, after its row and its comment.
            monkeypatch.setattr(gateway_runs, "_move_run", fail)
        with pytest.raises(RuntimeError, match="injected"):
            await _answer(runs, run_id)
        assert _snapshot(runs, task, run_id) == before
        assert _archived_at(runs, task) is None

    async def test_g5_open_subtasks_stay_on_the_board(self, runs):
        parent = runs.task(runs.team, runs.s.doing, "g5 parent")
        kids = [_subtask(runs, parent, f"g5 kid {n}") for n in (1, 2)]
        body = await _ask_archive(runs, parent)
        ok = await _answer(runs, body["run"]["id"])
        assert ok.status_code == 200, ok.text
        assert _archived_at(runs, parent) is not None
        for kid in kids:
            assert _archived_at(runs, kid) is None

    async def test_g6_a_decline_archives_nothing(self, runs, events):
        task, run_id, _ = await _requested(runs, "g6")
        r = await _answer(runs, run_id, "decline")
        assert r.status_code == 200, r.text
        assert r.json()["archived"] is False
        assert _archived_at(runs, task) is None
        assert _system_rows(runs, task) == 0
        assert _archived_events(events, task) == 0
        answer = _run_activities(runs, run_id)[-1]
        assert answer.content["selected"] == ["decline"] and answer.content["archived"] is False

    async def test_g7_an_approve_after_the_expiry_gets_409(self, runs):
        """EA3-40 rule 5: only an APPROVE is refused after the expiry."""
        task, run_id, body = await _requested(runs, "g7")
        _expire(runs, body["activity"]["id"])
        before = _snapshot(runs, task, run_id)
        r = await _answer(runs, run_id, "approve")
        assert r.status_code == 409, r.text
        assert _snapshot(runs, task, run_id) == before

    async def test_g7_a_decline_closes_an_expired_request(self, runs, events):
        """Fix round 1. An expired request never jams the run: the member's
        decline closes it, archives nothing, and the run leaves awaiting."""
        task, run_id, body = await _requested(runs, "g7 decline")
        _expire(runs, body["activity"]["id"])
        r = await _answer(runs, run_id, "decline")
        assert r.status_code == 200, r.text
        assert r.json()["archived"] is False
        assert _archived_at(runs, task) is None
        assert _archived_events(events, task) == 0
        assert _run_status(runs, run_id) == "in_progress"
        answer = _run_activities(runs, run_id)[-1]
        assert str(answer.in_reply_to) == body["activity"]["id"]
        # The agent can ask again.
        again = await _ask_archive(runs, task)
        assert again["run"]["id"] == run_id and again["run"]["status"] == "awaiting"

    async def test_g7_a_free_reply_moves_a_run_on_after_the_expiry(self, runs):
        """Fix round 1. After the expiry the request is stale, so any member's
        reply moves the run on, answers nothing and archives nothing."""
        task, run_id, body = await _requested(runs, "g7 reply")
        path = f"/projects/agent-runs/{run_id}/activities"
        early = await _person(runs, OTHER, "POST", path, {
            "type": "prompt", "body": "Not yet.", "idempotency_key": _key()})
        assert early.status_code == 409, early.text
        _expire(runs, body["activity"]["id"])
        late = await _person(runs, OTHER, "POST", path, {
            "type": "prompt", "body": "Keep it, please.", "idempotency_key": _key()})
        assert late.status_code == 201, late.text
        assert late.json()["activity"]["in_reply_to"] is None
        assert _run_status(runs, run_id) == "in_progress"
        assert _archived_at(runs, task) is None
        # ``selected`` stays refused on a typed request, expired or not.
        r = await _person(runs, MEMBER, "POST", path, {
            "type": "prompt", "selected": ["approve"], "idempotency_key": _key()})
        assert r.status_code == 422, r.text

    async def test_g7_an_approve_on_a_run_that_does_not_wait_gets_409(self, runs):
        task, run_id, _ = await _requested(runs, "g7 finished")
        finished = _structured(await _agent(runs, "finish_agent_run", run_id=run_id,
                                            idempotency_key=_key()))
        assert finished["run"]["status"] == "completed"
        before = _snapshot(runs, task, run_id)
        r = await _answer(runs, run_id)
        assert r.status_code == 409, r.text
        assert _snapshot(runs, task, run_id) == before

    async def test_g8_a_second_approve_gives_one_archive(self, runs, events):
        task, run_id, _ = await _requested(runs, "g8")
        key = _key()
        first = await _answer(runs, run_id, key=key)
        assert first.status_code == 200, first.text
        again = await _answer(runs, run_id, key=key)
        assert again.status_code == 200, again.text
        assert again.json()["created"] is False
        assert again.json()["activity"]["id"] == first.json()["activity"]["id"]
        # Fix round 1: a retried click shows the archive.
        assert again.json()["task"]["id"] == task
        assert again.json()["task"]["archived_at"] is not None
        other_key = await _answer(runs, run_id)
        assert other_key.status_code == 409, other_key.text
        assert _system_rows(runs, task) == 1
        assert _archived_events(events, task) == 1
        assert len([a for a in _run_activities(runs, run_id) if a.type == "prompt"]) == 1

    async def test_g9_an_archived_task_gets_no_second_activity_row(self, runs, events):
        task, run_id, _ = await _requested(runs, "g9")
        by_hand = await _person(runs, MEMBER, "POST", f"/projects/tasks/{task}/archive")
        assert by_hand.status_code == 200, by_hand.text
        assert _system_rows(runs, task) == 1
        r = await _answer(runs, run_id)
        assert r.status_code == 200, r.text
        assert r.json()["archived"] is True
        assert _system_rows(runs, task) == 1
        assert _archived_events(events, task) == 1

    async def test_g10_a_member_who_lost_access_gets_404(self, runs):
        task = runs.task(runs.lost, runs.s.lost_todo, "g10")
        body = await _ask_archive(runs, task)
        run_id = body["run"]["id"]
        runs.seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                        " AND subject = :s RETURNING project_id", p=runs.lost, s=MEMBER)
        try:
            before = _snapshot(runs, task, run_id)
            for decision in ("decline", "approve"):
                r = await _answer(runs, run_id, decision)
                assert r.status_code == 404, (decision, r.text)
            assert _snapshot(runs, task, run_id) == before
        finally:
            _grant(runs.seeder, runs.lost, MEMBER)

    async def test_g13_organization_b_gets_404(self, runs):
        b = runs.org_b
        for decision in ("decline", "approve"):
            r = await _answer(runs, b.run, decision)
            assert r.status_code == 404, (decision, r.text)
        with runs.admin.begin() as c:
            answers = c.execute(text("SELECT count(*) FROM pm_agent_run_activities WHERE"
                                     " run_id = CAST(:r AS uuid) AND type = 'prompt'"),
                                {"r": b.run}).scalar()
        assert answers == 0
        result = await _agent(runs, "request_task_archive", task_id=b.task,
                              idempotency_key=_key())
        assert _text(result) == TASK_NOT_FOUND
        with runs.admin.begin() as c:
            archived = c.execute(text("SELECT archived_at FROM pm_tasks WHERE id ="
                                      " CAST(:t AS uuid)"), {"t": b.task}).scalar()
        assert archived is None

    @pytest.mark.parametrize("decision", ["approve", "decline"])
    async def test_g14_get_agent_run_shows_the_answer_and_archived(self, runs, decision):
        _, run_id, body = await _requested(runs, f"g14 {decision}")
        r = await _answer(runs, run_id, decision)
        assert r.status_code == 200, r.text
        read = _structured(await _agent(runs, "get_agent_run", run_id=run_id,
                                        after=body["activity"]["seq"]))
        (prompt,) = [a for a in read["activities"] if a["type"] == "prompt"]
        assert prompt["author"] == "person"
        assert prompt["selected"] == [decision]
        assert prompt["labels"] == [{"untrusted_text": decision.capitalize()}]
        assert prompt["archived"] is (decision == "approve")
        assert prompt["in_reply_to"] == body["activity"]["id"]
        assert read["run"]["state"] == "answered"


class TestTheSwitches:
    """G12 end to end: the runs flag, the writes flag and the scope."""

    pytestmark = _DB_GATE

    async def test_g12_with_the_runs_flag_off_the_routes_give_404(self, runs, monkeypatch):
        task, run_id, _ = await _requested(runs, "g12 off")
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", False)
        r = await _answer(runs, run_id)
        assert r.status_code == 404, r.text
        r = await tw._send(runs, "POST", f"/projects/tasks/{task}/archive-request",
                           body={"idempotency_key": _key()})
        assert r.status_code == 404, r.text
        result = await _agent(runs, "request_task_archive", task_id=task,
                              idempotency_key=_key())
        assert _text(result) == tools.TEXT_RUNS_OFF
        assert _archived_at(runs, task) is None
        assert _run_status(runs, run_id) == "awaiting"

    async def test_g12_tools_list_follows_both_flags_and_the_scope(self, runs, monkeypatch):
        async def names(token: str) -> set[str]:
            async def go() -> httpx.Response:
                async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                        base_url=w.ISSUER) as client:
                    return await client.post(
                        "/mcp", content=json.dumps(_rpc("tools/list")),
                        headers={"authorization": f"Bearer {token}",
                                 "content-type": "application/json",
                                 "mcp-protocol-version": "2025-11-25"})
            r = await tw._in_scope(runs, go)
            assert r.status_code == 200, r.text
            return {t["name"] for t in r.json()["result"]["tools"]}

        assert "request_task_archive" in await names(runs.tok["write"])
        assert "request_task_archive" not in await names(runs.tok["read"])
        for flag in ("agent_runs_enabled", "agent_access_writes_enabled"):
            monkeypatch.setattr(get_settings(), flag, False)
            assert "request_task_archive" not in await names(runs.tok["write"]), flag
            monkeypatch.setattr(get_settings(), flag, True)

    async def test_g12_a_read_connection_cannot_send_the_request(self, runs):
        task = runs.task(runs.team, runs.s.doing, "g12 read")

        async def go() -> httpx.Response:
            async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=edge_app_module.create_app()),
                    base_url=w.ISSUER) as client:
                return await client.post(
                    "/mcp", content=json.dumps(_call(
                        "request_task_archive", {"task_id": task, "idempotency_key": _key()})),
                    headers={"authorization": f"Bearer {runs.tok['read']}",
                             "content-type": "application/json",
                             "mcp-protocol-version": "2025-11-25"})
        r = await tw._in_scope(runs, go)
        assert r.status_code == 403, r.text
        assert 'error="insufficient_scope"' in r.headers["www-authenticate"]
        signed = await tw._send(runs, "POST", f"/projects/tasks/{task}/archive-request",
                                body={"idempotency_key": _key()}, signed="read")
        assert signed.status_code == 401, signed.text
        assert _rows(runs, "SELECT id FROM pm_agent_runs WHERE task_id = CAST(:t AS uuid)",
                     t=task) == []
