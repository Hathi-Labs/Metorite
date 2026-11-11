"""WS-MCP SW-23 — a task carries a Check (Metorite task 857).

Spec: ``project-docs/specs/agent_coordination.md`` SW-23 and §3.5.

A Check is a read-only command that proves the work of a task is still open.
``create_task`` and ``update_task`` take ``check``. ``report_check`` records
one run. A ``passed`` run moves the task to done, with the evidence as a
comment, only when the caller may move it. ``open`` and ``cannot_run`` never
close a task. ``my_work`` gains the reason ``check_due``, and
``get_task_context`` returns the Check first. Dark behind
``agent_task_checks_enabled``.

Two halves.

**Database-free (always runs).** The records, the pinned copies, the flag,
the order of ``my_work`` and the place of the Check in ``get_task_context``,
through the fake board of ``test_mcp_edge_context.py``.

**R8 (a real database, as a NON-privileged NOBYPASSRLS role).** The edge
calls the REAL gateway app. It proves each rule of the report: a pass closes
the task with one comment, ``open`` and ``cannot_run`` never close it, a stale
version and a hidden task write nothing, a live run of another session keeps
the task open, a new Check clears the last result, the flag off answers 404
and 422 and adds no key, and the database refuses a result outside the three.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_task_check_r8.py -v -rs

**The mutations (R7).** ``E`` is ``apps/services/agent_edge/agent_edge``,
``G`` is ``apps/services/gateway/gateway/routes/projects``.

* C1. ``G/checks.py``: ``report_check`` moves the task for every result. ``test_open_and_cannot_run_never_close_the_task``.
* C2. ``G/checks.py``: ``_closing_refusal`` reads no run. ``test_a_live_run_of_another_session_keeps_the_task_open``.
* C3. ``G/checks.py``: ``_own_run`` is always ``False``. ``test_the_callers_own_run_does_not_hold_the_task``.
* C4. ``G/checks.py``: ``report_check`` skips ``require_precondition``. ``test_a_stale_version_records_nothing``.
* C5. ``G/checks.py``: ``check_patch_values`` keeps the old result. ``test_a_new_check_clears_the_last_result``.
* C6. ``G/checks.py``: ``attach_check`` ignores the flag. ``test_with_the_flag_off_the_route_and_the_field_are_dark``.
* C7. ``E/tools.py``: ``_my_work`` puts ``check_due`` rows after ``assigned``. ``test_a_due_check_comes_below_held_work_and_above_assigned``.
* C8. ``E/tools.py``: ``_get_task_context`` puts ``check`` last. ``test_the_check_is_the_first_key_of_the_context``.
* C9. ``E/tools.py``: ``shown_inputs`` ignores the flag. ``test_the_flag_off_hides_the_tool_and_the_input``.
* C10. migration: no ``pm_tasks_check_result_known``. ``test_the_database_refuses_a_result_outside_the_three``.
"""
from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("skill_projects", reason="skill-projects not installed")

from acb_auth import agent_oauth
from acb_common import get_settings
from agent_edge import tools
from skill_projects import manifest as m
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.unit import test_mcp_edge_task_writes as t
from tests.unit import test_mcp_edge_writes as w
from tests.unit.test_mcp_edge_context import (  # noqa: F401 — the fixtures
    TASK,
    _assign,
    _run,
    _structured,
    _task_row,
    _uuid,
    board,
)
from tests.unit.test_mcp_edge_writes import edge  # noqa: F401 — a fixture

_DB_GATE = w._DB_GATE
live_app = t.live_app
live = t.live
redis_fake = t.redis_fake
_mcp = t._mcp
_send = t._send
_text = w._text

_ROOT = Path(__file__).resolve().parents[2]
ROUTE = "/projects/tasks/{task_id}/check-result"
MEMBER = w.MEMBER
R8_VIA = w.R8_VIA
_APP_ROLE = "acb_app_taskcheck"
_APP_PW = "taskcheck_scratch_pw"


@pytest.fixture
def checks_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_task_checks_enabled", True)


def _migration() -> str:
    """The migration of SW-23, found by CONTENT, never by number (R1)."""
    for path in sorted((_ROOT / "infra" / "postgres").glob("*.sql")):
        body = path.read_text(encoding="utf-8")
        if "ADD COLUMN IF NOT EXISTS check_last_result" in body:
            return body
    raise AssertionError("no migration adds pm_tasks.check_last_result")


# ═══════════════════════════════════════════════════════════════════════════
# Database-free
# ═══════════════════════════════════════════════════════════════════════════


class TestTheRecords:
    def test_the_report_is_a_class_b_write_pair_of_a_planned_chat_tool(self):
        assert ("POST", ROUTE) in agent_oauth.MCP_WRITE_ROUTES
        rows = {(r.method, r.path): r for r in m.MANIFEST}
        assert rows[("POST", ROUTE)].cls == "B"
        assert rows[("POST", ROUTE)].tool == "report_check"
        assert "report_check" in m.PLANNED
        tool = tools.TOOLS["report_check"]
        assert (tool.method, tool.route) == ("POST", ROUTE)
        assert tool.required_scope == agent_oauth.PROJECTS_WRITE
        assert tool.checks is True
        assert tool.params["expected_version"] == "If-Match"

    def test_the_edge_copies_equal_the_gateway(self):
        from gateway.routes.projects import checks

        assert tools.MAX_CHECK_CHARS == checks.MAX_CHECK_CHARS
        assert tools.MAX_CHECK_EVIDENCE_CHARS == checks.MAX_CHECK_EVIDENCE_CHARS
        assert tools.CHECK_RESULTS == checks.CHECK_RESULTS
        assert tools.CHECKS_OFF_DETAIL == checks.CHECKS_OFF
        assert tools.NO_CHECK_DETAIL == checks.NO_CHECK
        assert tools.CHECK_CHANGED_DETAIL == checks.CHECK_CHANGED
        assert tools.CHECK_NOT_CLOSED == (
            checks.ALREADY_CLOSED, checks.NO_DONE_STATUS, checks.HELD_BY_OTHER)
        assert "check_text" in tools.CREATE_BODY_KEYS
        assert tools.TOOLS["create_task"].params["check"] == "check_text"
        assert tools.TOOLS["update_task"].params["check"] == "check_text"

    def test_the_migration_is_expand_only(self):
        body = _migration()
        for column in ("check_text", "check_last_run_at", "check_last_result",
                       "check_last_evidence"):
            assert f"ADD COLUMN IF NOT EXISTS {column}" in body, column
        assert "SET lock_timeout" in body
        assert "pm_tasks_check_result_known" in body and "pm_tasks_check_run_whole" in body
        assert "'open', 'passed', 'cannot_run'" in body
        assert not re.search(r"\bNOT NULL\b(?! VALID)", body.split("-- ===")[-1])
        assert "DROP " not in body.upper().split("-- ====")[-1]

    def test_this_suite_is_named_in_the_ci_skip_guard(self):
        """An R8 suite that CI does not list skips in silence when the database
        gate is disarmed. Match the argument line, not a mention."""
        for name in ("pr-check.yml", "deploy.yml"):
            workflow = (_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            assert re.search(r"^\s+tests/unit/test_task_check_r8\.py \\?$", workflow, re.M), name

    def test_check_of_wraps_each_written_value(self):
        value = {"text": "gh pr view 1", "last_run_at": "2026-11-01T00:00:00+00:00",
                 "last_result": "open", "last_evidence": "OPEN", "due": True}
        shown = tools.check_of(value, evidence=True)
        assert shown == {"text": {"untrusted_text": "gh pr view 1"},
                         "last_run_at": "2026-11-01T00:00:00+00:00", "last_result": "open",
                         "last_evidence": {"untrusted_text": "OPEN"}, "due": True}
        assert "last_evidence" not in tools.check_of(value, evidence=False)
        assert tools.check_of({**value, "last_result": "maybe"}, evidence=False)[
            "last_result"] is None
        assert tools.check_of(None, evidence=True) is None


class TestTheFlag:
    def _names(self, *, writes: bool = True) -> list[str]:
        scopes = frozenset({"projects:read", "projects:write"} if writes else {"projects:read"})
        return [x["name"] for x in tools.listed(scopes)]

    def test_the_flag_off_hides_the_tool_and_the_input(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "agent_access_writes_enabled", True)
        monkeypatch.setattr(settings, "agent_task_checks_enabled", False)
        assert "report_check" not in self._names()
        for name in ("create_task", "update_task"):
            entry = tools.TOOLS[name].describe()
            assert "check" not in entry["inputSchema"]["properties"], name
        bulk = tools.TOOLS["update_tasks"].describe()["inputSchema"]
        assert "check" not in bulk["properties"]["updates"]["items"]["properties"]
        for name in ("my_work", "get_task_context"):
            described = tools.TOOLS[name].describe()["description"]
            assert "check_due" not in described and "report_check" not in described, name
            assert "[[" not in described, name
        # The static schema keeps the input, so a call that sends it is refused.
        assert "check" in tools.TOOLS["update_task"].input_schema["properties"]

        monkeypatch.setattr(settings, "agent_task_checks_enabled", True)
        assert "report_check" in self._names()
        assert "report_check" not in self._names(writes=False)
        for name in ("create_task", "update_task"):
            assert "check" in tools.TOOLS[name].describe()["inputSchema"]["properties"]
        assert "check_due" in tools.TOOLS["my_work"].describe()["description"]
        assert "report_check" in tools.TOOLS["get_task_context"].describe()["description"]

    async def test_the_flag_off_refuses_the_input_before_any_request(self, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_task_checks_enabled", False)

        async def nothing(*_a, **_kw):
            raise AssertionError("no request goes out while the flag is off")

        monkeypatch.setattr(tools, "_get", nothing)
        monkeypatch.setattr(tools, "_write", nothing)
        for run, args in (
                (tools._create_task, {"project_id": _uuid(1), "title": "x", "check": "ls"}),
                (tools._update_task, {"task_id": _uuid(2), "expected_version": "x",
                                      "check": "ls"})):
            with pytest.raises(tools.ToolError) as caught:
                await run(None, args)
            assert caught.value.text == tools.TEXT_CHECKS_OFF

    async def test_a_report_while_off_answers_the_off_text(self, edge, monkeypatch):  # noqa: F811
        monkeypatch.setattr(get_settings(), "agent_task_checks_enabled", False)
        result = await w._tool("report_check", {
            "task_id": TASK, "result": "passed", "evidence": "x",
            "expected_version": w.V1})
        assert _text(result) == tools.TEXT_CHECKS_OFF
        assert edge.gateway.requests == []


def _check(text_: str = "gh pr view 7", *, due: bool, result: str | None = None) -> dict:
    return {"text": text_, "last_run_at": None if result is None else "2026-11-01T00:00:00+00:00",
            "last_result": result, "last_evidence": "E" if result else None, "due": due}


class TestTheReadsOnTheFakeBoard:
    async def test_a_due_check_comes_below_held_work_and_above_assigned(
            self, board, checks_on):  # noqa: F811
        queued, held, due, plain, fresh = (_uuid(8100 + n) for n in range(5))
        _assign(board, plain, fresh, due, held)
        board.tasks[due][tools.CHECK_KEY] = _check(due=True)
        board.tasks[fresh][tools.CHECK_KEY] = _check(due=False, result="open")
        board.tasks[plain][tools.CHECK_KEY] = None
        board.tasks[queued] = _task_row(queued)
        board.runs = [_run(_uuid(5301), queued, state="queued", status="created"),
                      _run(_uuid(5302), held, state="stale")]
        rows = _structured(await w._tool("my_work", {}))["rows"]
        assert [(r["task_id"], r["reason"]) for r in rows] == [
            (queued, "run_queued"), (held, "held_by_you"), (due, "check_due"),
            (plain, "assigned"), (fresh, "assigned")]
        by_id = {r["task_id"]: r for r in rows}
        assert by_id[due]["check"] == {"text": {"untrusted_text": "gh pr view 7"},
                                       "last_run_at": None, "last_result": None, "due": True}
        assert by_id[plain]["check"] is None
        assert "last_evidence" not in by_id[fresh]["check"]
        # A row whose task row has no key (the flag off at the gateway) has none.
        assert "check" not in by_id[queued]

    async def test_a_due_check_of_a_held_or_blocked_task_is_not_offered(
            self, board, checks_on):  # noqa: F811
        blocked = _uuid(8201)
        _assign(board, blocked)
        board.tasks[blocked][tools.CHECK_KEY] = _check(due=True)
        board.tasks[blocked]["blocked_by_count"] = 1
        assert _structured(await w._tool("my_work", {}))["rows"] == []

    async def test_the_check_is_the_first_key_of_the_context(
            self, board, checks_on):  # noqa: F811
        board.tasks[TASK][tools.CHECK_KEY] = _check(due=False, result="cannot_run")
        body = _structured(await w._tool("get_task_context", {"task_id": TASK}))
        assert next(iter(body)) == "check"
        assert body["check"]["last_result"] == "cannot_run"
        assert body["check"]["last_evidence"] == {"untrusted_text": "E"}
        board.tasks[TASK][tools.CHECK_KEY] = None
        body = _structured(await w._tool("get_task_context", {"task_id": TASK}))
        assert next(iter(body)) == "check" and body["check"] is None
        del board.tasks[TASK][tools.CHECK_KEY]
        body = _structured(await w._tool("get_task_context", {"task_id": TASK}))
        assert "check" not in body


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real gateway app
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog: one tenant, one member, two projects. The
    app role is NOSUPERUSER NOBYPASSRLS, so FORCE RLS binds each request."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_taskcheck"
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
                       "VALUES (:id, 'taskcheck', 'taskcheck')"), {"id": org})
    a = w._Seeder(eng, org)
    a.number = 100_000
    a.member(MEMBER, ["feature:projects"])
    team = a.project("Team", in_space=True)
    bare = a.project("No done lane", in_space=True)
    s = SimpleNamespace(
        todo=a.status(team, "To do", "todo", 0),
        doing=a.status(team, "Doing", "in_progress", 1),
        done=a.status(team, "Done", "done", 3),
        cancelled=a.status(team, "Dropped", "cancelled", 4),
        bare=a.status(bare, "To do", "todo", 0),
    )
    world_ = SimpleNamespace(admin=eng, org=org, seeder=a, team=team, bare=bare, s=s,
                             c={}, tok={}, task=a.task)
    for name, scopes in (("write", w.WRITE_SCOPES), ("read", ("projects:read",)),
                         ("other", w.WRITE_SCOPES)):
        world_.c[name], world_.tok[name] = a.connection(scopes=scopes)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


def _row(live, task: str) -> Any:
    return t._row(live, task)


def _comments(live, task: str) -> list[Any]:
    with live.admin.begin() as c:
        return list(c.execute(text(
            "SELECT body, created_by, meta FROM pm_activities WHERE task_id = CAST(:t AS uuid)"
            " AND type = 'comment' ORDER BY seq"), {"t": task}))


def _set_check(live, task: str, check: str | None, *, ran: timedelta | None = None,
               result: str = "open") -> None:
    with live.admin.begin() as c:
        c.execute(text(
            "UPDATE pm_tasks SET check_text = :c,"
            " check_last_run_at = CASE WHEN CAST(:ran AS interval) IS NULL THEN NULL"
            "   ELSE now() - CAST(:ran AS interval) END,"
            " check_last_result = CASE WHEN CAST(:ran AS interval) IS NULL THEN NULL"
            "   ELSE :r END WHERE id = CAST(:t AS uuid)"),
            {"c": check, "t": task, "r": result,
             "ran": None if ran is None else f"{int(ran.total_seconds())} seconds"})


def _assignee(live, task: str) -> None:
    with live.admin.begin() as c:
        c.execute(text(
            "INSERT INTO pm_task_assignees (organization_id, task_id, assignee, assigned_by)"
            " VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :a, :a)"),
            {"o": live.org, "t": task, "a": MEMBER})


async def _context(live, task: str) -> dict[str, Any]:
    result = await _mcp(live, "write", "get_task_context", {"task_id": task})
    assert result["isError"] is False, result
    return result["structuredContent"]


async def _report(live, task: str, result: str, evidence: str = "the evidence",
                  version: str | None = None, *, name: str = "write") -> dict[str, Any]:
    if version is None:
        version = (await _context(live, task))["task"]["version"]
    return await _mcp(live, name, "report_check", {
        "task_id": task, "result": result, "evidence": evidence,
        "expected_version": version})


async def _made(live, title: str, check: str | None = "gh pr view 11 --json state") -> str:
    args: dict[str, Any] = {"project_id": live.team, "title": title}
    if check is not None:
        args["check"] = check
    made = await _mcp(live, "write", "create_task", args)
    assert made["isError"] is False, made
    return made["structuredContent"]["task"]["id"]


@pytest.mark.usefixtures("checks_on")
class TestTheReportOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_a_pass_closes_the_task_with_the_evidence_as_a_comment(self, live):
        task = await _made(live, "sw23 pass")
        context = await _context(live, task)
        assert next(iter(context)) == "check"
        assert context["check"]["text"] == {"untrusted_text": "gh pr view 11 --json state"}
        assert (context["check"]["last_result"], context["check"]["due"]) == (None, True)
        result = await _report(live, task, "passed", "state MERGED")
        assert result["isError"] is False, result
        got = result["structuredContent"]
        assert (got["closed"], got["not_closed"], got["result"]) == (True, None, "passed")
        row = _row(live, task)
        assert str(row.status_id) == live.s.done and row.completed_at is not None
        assert (row.check_last_result, row.check_last_evidence) == ("passed", "state MERGED")
        assert got["version"] == row.updated_at.isoformat()
        [comment] = _comments(live, task)
        assert comment.body == "Check passed.\n\nstate MERGED"
        assert comment.created_by == MEMBER and comment.meta["via"] == R8_VIA
        # A second pass finds the task closed, moves nothing and adds no comment.
        again = (await _report(live, task, "passed", "still merged"))["structuredContent"]
        assert (again["closed"], again["not_closed"]) == (False, "already_closed")
        assert len(_comments(live, task)) == 1

    async def test_open_and_cannot_run_never_close_the_task(self, live):
        task = await _made(live, "sw23 open")
        for result in ("open", "cannot_run"):
            got = (await _report(live, task, result, f"saw {result}"))["structuredContent"]
            assert (got["closed"], got["not_closed"], got["result"]) == (False, None, result)
            row = _row(live, task)
            assert str(row.status_id) == live.s.todo and row.completed_at is None
            assert (row.check_last_result, row.check_last_evidence) == (result, f"saw {result}")
        assert _comments(live, task) == []
        assert (await _context(live, task))["check"]["due"] is False

    async def test_a_stale_version_records_nothing(self, live):
        task = await _made(live, "sw23 stale")
        old = (await _context(live, task))["task"]["version"]
        moved = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": old, "title": "sw23 stale renamed"})
        assert moved["isError"] is False, moved
        result = await _report(live, task, "passed", "x", version=old)
        assert _text(result) == tools.TEXT_STALE
        row = _row(live, task)
        assert row.check_last_result is None and str(row.status_id) == live.s.todo

    async def test_a_task_with_no_check_gets_the_no_check_text(self, live):
        task = await _made(live, "sw23 none", check=None)
        assert "check" in await _context(live, task)
        assert (await _context(live, task))["check"] is None
        assert _text(await _report(live, task, "passed")) == tools.TEXT_NO_CHECK
        assert _row(live, task).check_last_result is None

    async def test_a_new_check_clears_the_last_result(self, live):
        task = await _made(live, "sw23 change")
        await _report(live, task, "open", "still open")
        version = (await _context(live, task))["task"]["version"]
        same = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": version, "check": "gh pr view 11 --json state"})
        assert same["isError"] is False, same
        assert _row(live, task).check_last_result == "open"
        version = (await _context(live, task))["task"]["version"]
        new = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": version, "check": "gh pr view 12"})
        assert new["isError"] is False, new
        row = _row(live, task)
        assert (row.check_text, row.check_last_result, row.check_last_run_at,
                row.check_last_evidence) == ("gh pr view 12", None, None, None)
        version = (await _context(live, task))["task"]["version"]
        gone = await _mcp(live, "write", "update_task", {
            "task_id": task, "expected_version": version, "check": None})
        assert gone["isError"] is False, gone
        assert _row(live, task).check_text is None

    async def test_a_live_run_of_another_session_keeps_the_task_open(self, live):
        task = await _made(live, "sw23 held")
        with live.admin.begin() as c:
            c.execute(text(
                "INSERT INTO pm_agent_runs (organization_id, task_id, member_email,"
                " connection_id, client_via, status, started_by, started_at,"
                " last_activity_at) VALUES (CAST(:o AS uuid), CAST(:t AS uuid), :m,"
                " CAST(:c AS uuid), :v, 'in_progress', :m, now(), now())"),
                {"o": live.org, "t": task, "m": MEMBER, "c": live.c["other"], "v": R8_VIA})
        got = (await _report(live, task, "passed", "merged"))["structuredContent"]
        assert (got["closed"], got["not_closed"]) == (False, "held_by_other")
        row = _row(live, task)
        assert row.check_last_result == "passed" and str(row.status_id) == live.s.todo
        assert _comments(live, task) == []

    async def test_the_callers_own_run_does_not_hold_the_task(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_runs_enabled", True)
        task = await _made(live, "sw23 own run")
        version = (await _context(live, task))["task"]["version"]
        claimed = await _mcp(live, "write", "claim_task", {
            "task_id": task, "expected_version": version})
        assert claimed["isError"] is False, claimed
        got = (await _report(live, task, "passed", "merged"))["structuredContent"]
        assert (got["closed"], got["not_closed"]) == (True, None)

    async def test_a_project_with_no_done_lane_records_and_keeps_the_task_open(self, live):
        task = live.task(live.bare, live.s.bare, "sw23 no done")
        _set_check(live, task, "gh pr view 13")
        got = (await _report(live, task, "passed", "merged"))["structuredContent"]
        assert (got["closed"], got["not_closed"]) == (False, "no_done_status")
        row = _row(live, task)
        assert row.check_last_result == "passed" and str(row.status_id) == live.s.bare

    async def test_a_hidden_task_reads_as_a_task_that_does_not_exist(self, live):
        seeder = live.seeder
        hidden_project = seeder.project("sw23 hidden", in_space=True)
        for node in (hidden_project, seeder.root(hidden_project)):
            seeder.one("DELETE FROM pm_project_grants WHERE project_id = CAST(:p AS uuid)"
                       " AND subject = :s RETURNING project_id", p=node, s=MEMBER)
        status = seeder.status(hidden_project, "To do", "todo", 0)
        hidden = seeder.task(hidden_project, status, "sw23 hidden task")
        _set_check(live, hidden, "ls")
        result = await _report(live, hidden, "passed", "x", version=w.V1)
        assert _text(result) == tools.TEXT_TASK_NOT_FOUND
        assert _row(live, hidden).check_last_result is None

    async def test_a_read_only_connection_cannot_report(self, live):
        task = await _made(live, "sw23 read only")
        version = (await _context(live, task))["task"]["version"]
        with pytest.raises(AssertionError, match="Insufficient scope"):
            await _report(live, task, "passed", "x", version=version, name="read")
        assert _row(live, task).check_last_result is None

    async def test_a_signed_report_with_no_version_gets_428(self, live):
        task = await _made(live, "sw23 no version")
        sent = await _send(live, "POST", f"/projects/tasks/{task}/check-result",
                           body={"result": "passed", "evidence": "x"})
        assert sent.status_code == 428, sent.text
        assert _row(live, task).check_last_result is None

    async def test_a_due_check_is_offered_by_my_work_before_the_assigned_tasks(self, live):
        never = live.task(live.team, live.s.todo, "sw23 due never")
        old = live.task(live.team, live.s.todo, "sw23 due old")
        recent = live.task(live.team, live.s.todo, "sw23 recent")
        plain = live.task(live.team, live.s.todo, "sw23 plain")
        _set_check(live, never, "ls never")
        _set_check(live, old, "ls old", ran=timedelta(days=8))
        _set_check(live, recent, "ls recent", ran=timedelta(days=1))
        for task in (never, old, recent, plain):
            _assignee(live, task)
        result = await _mcp(live, "write", "my_work", {"limit": 25})
        assert result["isError"] is False, result
        rows = [r for r in result["structuredContent"]["rows"]
                if r["task_id"] in {never, old, recent, plain}]
        reasons = {r["task_id"]: r["reason"] for r in rows}
        assert reasons == {never: "check_due", old: "check_due",
                           recent: "assigned", plain: "assigned"}
        order = [r["task_id"] for r in rows]
        assert max(order.index(never), order.index(old)) < min(
            order.index(recent), order.index(plain))
        by_id = {r["task_id"]: r for r in rows}
        assert by_id[never]["check"]["text"] == {"untrusted_text": "ls never"}
        assert by_id[recent]["check"]["due"] is False
        assert by_id[plain]["check"] is None


class TestTheFlagAndTheSchemaOnARealDatabase:
    pytestmark = _DB_GATE

    async def test_with_the_flag_off_the_route_and_the_field_are_dark(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "agent_task_checks_enabled", False)
        task = live.task(live.team, live.s.todo, "sw23 dark")
        _set_check(live, task, "ls")
        sent = await _send(live, "POST", f"/projects/tasks/{task}/check-result",
                           body={"result": "passed", "evidence": "x"},
                           headers={"If-Match": w.V1})
        assert sent.status_code == 404, sent.text
        patched = await _send(live, "PATCH", f"/projects/tasks/{task}",
                              body={"check_text": "rm -rf /"})
        assert patched.status_code == 422 and patched.json()["detail"] == "task_checks_off"
        made = await _send(live, "POST", "/projects/tasks",
                           body={"project_id": live.team, "title": "sw23 dark made",
                                 "check_text": "ls"})
        assert made.status_code == 422 and made.json()["detail"] == "task_checks_off"
        read = await _send(live, "GET", f"/projects/tasks/{task}")
        assert read.status_code == 200 and "check" not in read.json()
        listed = await _send(live, "GET", "/projects/tasks", query=f"project_id={live.team}")
        assert all("check" not in row for row in listed.json()["rows"])
        assert _row(live, task).check_text == "ls"

    def test_the_database_refuses_a_result_outside_the_three(self, world):
        task = world.task(world.team, world.s.todo, "sw23 schema")
        app = create_engine(world.app_dsn, future=True)
        try:
            for sql in (
                    "UPDATE pm_tasks SET check_last_result = 'maybe',"
                    " check_last_run_at = now() WHERE id = CAST(:t AS uuid)",
                    "UPDATE pm_tasks SET check_last_result = 'open' WHERE id = CAST(:t AS uuid)"):
                with pytest.raises(IntegrityError), app.begin() as c:
                    c.execute(text("SELECT set_config('app.tenant_id', :o, true)"),
                              {"o": world.org})
                    c.execute(text(sql), {"t": task})
            with app.begin() as c:
                c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": world.org})
                changed = c.execute(text(
                    "UPDATE pm_tasks SET check_text = 'ls', check_last_result = 'passed',"
                    " check_last_run_at = now() WHERE id = CAST(:t AS uuid) RETURNING id"),
                    {"t": task}).fetchall()
            assert len(changed) == 1
        finally:
            app.dispose()
        # A fresh connection that never bound a tenant: FORCE RLS shows the app
        # role no row. A connection that held an empty tenant gets an error from
        # the policy instead. Both fail closed.
        fresh = create_engine(world.app_dsn, future=True)
        try:
            with fresh.connect() as c:
                try:
                    seen = c.execute(text(
                        "SELECT count(*) FROM pm_tasks WHERE id = CAST(:t AS uuid)"),
                        {"t": task}).scalar()
                except DBAPIError:
                    seen = 0
            assert seen == 0
        finally:
            fresh.dispose()

    def test_due_is_the_seven_day_rule_of_the_gateway_sql(self, world):
        """``checks._CHECK_SQL`` decides ``due`` with the database clock: never
        run is due, six days is not, eight days is. A task with no Check has
        no row."""
        from gateway.routes.projects import checks

        rows = {}
        for label, ran in (("never", None), ("six", timedelta(days=6)),
                           ("eight", timedelta(days=8))):
            task = world.task(world.team, world.s.todo, f"sw23 {label}")
            _set_check(world, task, "ls", ran=ran)
            rows[label] = task
        rows["none"] = world.task(world.team, world.s.todo, "sw23 no check")
        with world.admin.begin() as c:
            found = {str(r.id): r.due for r in c.execute(
                text(checks._CHECK_SQL), {"ids": list(rows.values())})}
        assert found == {rows["never"]: True, rows["six"]: False, rows["eight"]: True}
