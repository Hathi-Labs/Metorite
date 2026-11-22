"""WS-AGA AGA-9 — the drill-down of one agent (``agents_view.md`` §4, §6).

``GET /projects/agents/{agent_key}/runs`` lists at most 40 lean runs of one
agent, newest first. An outside agent reads its task runs in
``pm_agent_runs``, through the SAME ``seen`` text as the overview. A built-in
or org agent reads its chat runs in ``agent_run``. ``AGENTS_VIEW_ORGS`` keeps
it dark.

**R8**, a real Postgres, through the real ``/projects`` router as the
non-privileged role (FORCE RLS). Two orgs, each with agents and runs of its
own, and three viewer levels in org A (admin, lead, member). :func:`_sees` is
§4 as one table (Q3, proposed). A change of Q3 changes that table only.

R7, the mutations (each turns a test here red):

* **M1** the route takes the org or the member from the query string ->
  ``test_a_hostile_query_changes_nothing``.
* **M2** ``_reach_sql`` drops the member arm -> the member rows of the table.
* **M3** ``_reach_sql`` drops the lead arm -> the lead rows of the table.
* **M4** the chat read drops ``{reach}`` -> the member and lead chat rows.
* **M5** the drill-down drops ``task_visibility_clause`` ->
  ``test_a_run_on_a_task_the_viewer_cannot_see_is_left_out``.
* **M6** a 404 names the key, or a key out of reach answers 403 or 200 ->
  ``test_every_miss_is_the_same_404``.
* **M7** the chat SELECT reads ``trace`` or ``error_message`` ->
  ``test_the_answer_holds_the_lean_keys_only``.
* **M8** the LIMIT goes, or the order turns -> ``test_at_most_40_runs_newest_first``.
* **M9** the drill-down reads a copy of the visible runs, not ``seen`` ->
  ``test_the_drilldown_agrees_with_the_overview``.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.projects", reason="gateway not installed")

from sqlalchemy import text

from tests.unit import _agent_board_r8 as b
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

ADMIN_A = "aga9-admin@a-drill.test"
LEAD_A = "aga9-lead@a-drill.test"
MEMBER_A = "aga9-member@a-drill.test"
OTHER_A = "aga9-other@a-drill.test"
ADMIN_B = "aga9-admin@b-drill.test"
#: The member's chat runs store the address in another case.
MEMBER_STORED = "Aga9-Member@A-Drill.TEST"

VIA_X = "mcp:acme:x-agent"  # four runs in org A, one in org B
VIA_H = "mcp:acme:h-agent"  # one run, on a task that only an admin can see
VIA_B = "mcp:acme:b-only"  # org B only
VIA_MANY = "mcp:acme:many-agent"  # 45 runs on the member's own task
MANY = 45

BUILT_IN = "projects-assistant"
ORG_AGENT_A = "aga9-a-helper"
ORG_AGENT_B = "aga9-b-helper"

HIDDEN_TITLE = "HIDDEN-TITLE-aga9"
ERROR_BODY = "The build step timed out"
#: Text that the store holds and the drill-down must never return.
SECRETS = ("SECRET-MESSAGE-aga9", "SECRET-TB-aga9", "SECRET-TRACE-aga9",
           "secret_tool_aga9", "secret-model-aga9")

NOT_FOUND_BODY = {"detail": "Not Found"}


def _settings() -> Any:
    from acb_common import get_settings

    return get_settings()


def _path(key: str) -> str:
    return "/projects/agents/" + urllib.parse.quote(key, safe=":") + "/runs"


# ── Hermetic: the key, the row shapes and the SQL texts ─────────────────────


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("built_in:projects-assistant", ("built_in", "projects-assistant")),
        ("org:Sales_Helper.v2", ("org", "Sales_Helper.v2")),
        ("external:mcp:claude.ai:claude-code", ("external", "mcp:claude.ai:claude-code")),
        ("external:mcp:box:lab-0a1b2c3d", ("external", "mcp:box:lab-0a1b2c3d")),
        ("external:studio:app-1", ("external", "studio:app-1")),
        ("external:mcp:fe80::1", ("external", "mcp:fe80::1")),
    ],
)
def test_a_key_of_the_overview_parses(raw: str, want: tuple[str, str]) -> None:
    from gateway.routes.projects.agent_summary import parse_agent_key

    assert parse_agent_key(raw) == want


@pytest.mark.parametrize(
    "raw",
    [
        "", "built_in:", "org:", "external:", "external:mcp:", "agent:x", "x",
        "built_in:-lead", "built_in:a b", "built_in:a/b", "built_in:../etc",
        "built_in:x'; DROP TABLE agent_run; --", "org:x%", "org:ü-agent",
        "built_in:projects-assistant\n", " built_in:projects-assistant",
        "external:MCP:acme:x", "external:http://evil.example", "external:mcp:a b",
        "external:mcp:" + "a" * 61, "built_in:" + "a" * 101, "org:" + "a" * 200,
        "BUILT_IN:projects-assistant", "external:mcp:acme:x-agent:extra'",
    ],
)
def test_a_key_of_any_other_shape_is_refused(raw: str) -> None:
    from gateway.routes.projects.agent_summary import parse_agent_key

    assert parse_agent_key(raw) is None


def test_a_value_that_is_not_text_is_refused() -> None:
    from gateway.routes.projects.agent_summary import parse_agent_key

    assert parse_agent_key(None) is None
    assert parse_agent_key(42) is None


def _at(minute: int) -> datetime:
    from datetime import UTC

    return datetime(2026, 11, 11, 9, minute, tzinfo=UTC)


def test_a_task_run_row_is_lean_and_says_its_error_in_one_line() -> None:
    from gateway.routes.projects.agent_summary import RUN_KEYS, _task_run_row

    failed = SimpleNamespace(
        id="r1", status="failed", state="failed", run_at=_at(0), ended_at=_at(2),
        task_id="t1", task_title="Fix\nthe extruder", err_status="failed",
        error_body="Timed out\n\nstack", err_gone=False, err_at=_at(2))
    row = _task_run_row(failed)
    assert tuple(row) == RUN_KEYS
    assert row["run_seconds"] == 120.0
    assert row["ended_at"] == _at(2).isoformat()
    assert row["task"] == {"id": "t1", "title": "Fix the extruder"}
    assert row["error"]["what"] == "This run failed. The agent said: Timed out stack"
    assert row["error"]["next_step"].startswith("Open the task")

    working = SimpleNamespace(
        id="r2", status="in_progress", state="working", run_at=_at(0), ended_at=_at(5),
        task_id="t1", task_title="", err_status=None, error_body=None, err_gone=False,
        err_at=_at(5))
    row = _task_run_row(working)
    assert row["ended_at"] is None and row["run_seconds"] is None
    assert row["error"] is None and row["task"]["title"] == "Untitled task"

    gone = SimpleNamespace(**{**vars(working), "err_status": "in_progress", "err_gone": True})
    assert "revoked or has expired" in _task_run_row(gone)["error"]["what"]


def test_a_chat_run_row_maps_the_state_and_never_quotes_the_store() -> None:
    from gateway.routes.projects.agent_summary import (
        CHAT_ERROR_WHAT,
        RUN_KEYS,
        _chat_run_row,
    )

    done = SimpleNamespace(id="c1", status="completed", started_at=_at(0), ended_at=_at(1),
                           duration_ms=1500)
    row = _chat_run_row(done)
    assert tuple(row) == RUN_KEYS
    assert row["state"] == "completed" and row["run_seconds"] == 1.5 and row["task"] is None
    assert row["error"] is None

    failed = SimpleNamespace(id="c2", status="error", started_at=_at(0), ended_at=_at(1),
                             duration_ms=None, error_message="SECRET")
    row = _chat_run_row(failed)
    assert row["state"] == "failed" and row["run_seconds"] == 60.0
    assert row["error"]["what"] == CHAT_ERROR_WHAT
    assert "SECRET" not in json.dumps(row)

    running = SimpleNamespace(id="c3", status="running", started_at=_at(0), ended_at=_at(1),
                              duration_ms=9000)
    row = _chat_run_row(running)
    assert row["state"] == "working" and row["ended_at"] is None and row["run_seconds"] is None

    assert _chat_run_row(SimpleNamespace(id="c4", status="cancelled", started_at=_at(0),
                                         ended_at=None, duration_ms=None))["state"] == "stopped"
    assert _chat_run_row(SimpleNamespace(id="c5", status="odd", started_at=_at(0),
                                         ended_at=None, duration_ms=None))["state"] == "unknown"


def test_the_sql_texts_share_one_visible_run_rule_and_read_no_trace() -> None:
    from gateway.routes.projects import agent_summary as m

    assert m._SEEN_RUNS_SQL in m._EXTERNAL_SQL and m._SEEN_RUNS_SQL in m._AGENT_RUNS_SQL
    assert m._ERR_SQL in m._EXTERNAL_SQL and m._ERR_SQL in m._AGENT_RUNS_SQL
    assert "task_id IS NOT NULL" in m._SEEN_RUNS_SQL
    for column in ("trace", "error_message", "error_traceback", "tool_summary", "model",
                   "thread_id", "user_id,"):
        assert column not in m._CHAT_RUNS_SQL.split("FROM")[0], column
    assert "LIMIT :cap" in m._CHAT_RUNS_SQL and "LIMIT :cap" in m._AGENT_RUNS_SQL
    assert m._reach_sql(True, runner="ar.user_id", task=None) == "true"
    assert m._reach_sql(False, runner="ar.user_id", task=None) == "(lower(ar.user_id) = :me)"
    assert "lower(r.member_email) = :me" in m._reach_sql(False)
    assert ":led" in m._reach_sql(False)


def test_the_route_is_a_get_of_the_projects_router() -> None:
    from gateway.routes.projects import router

    found = [r for r in router.routes if getattr(r, "path", "") == "/projects/agents/{agent_key}/runs"]
    assert [sorted(r.methods) for r in found] == [["GET"]]


# ── R8: the world ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    p = promoted
    ns = SimpleNamespace(p=p)
    ns.admin_a = b._principal(ADMIN_A, p.org_a)
    ns.lead_a = b._principal(LEAD_A, p.org_a, unrestricted=False)
    ns.member_a = b._principal(MEMBER_A, p.org_a, unrestricted=False)
    ns.admin_b = b._principal(ADMIN_B, p.org_b)
    for email in (ADMIN_A, LEAD_A, MEMBER_A, OTHER_A):
        b.member(p, p.org_a, email)
    b.member(p, p.org_b, ADMIN_B)
    return ns


def _chat_run(
    p: Any, org: str, run_id: str, agent: str, user: str, status: str, *,
    started: str, ended: str | None = None, duration_ms: int | None = None,
    secret: bool = False,
) -> str:
    """One row of ``agent_run``, as the RLS-bypassing admin. *started* and
    *ended* are SQL time expressions. A *secret* row holds each heavy field."""
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO agent_run (run_id, thread_id, agent_name, user_id, model, status, "
            "started_at, ended_at, duration_ms, tool_summary, error_message, error_type, "
            "error_traceback, trace, organization_id) VALUES (:id, :thread, :agent, :user, "
            ":model, :status, " + started + ", " + (ended or "NULL") + ", :dur, "
            "CAST(:tools AS jsonb), :msg, :etype, :tb, CAST(:trace AS jsonb), CAST(:o AS uuid))"),
            {"id": run_id, "thread": f"thread-{run_id}", "agent": agent, "user": user,
             "model": SECRETS[4] if secret else None, "status": status, "dur": duration_ms,
             "tools": json.dumps([{"name": SECRETS[3], "status": "error"}] if secret else []),
             "msg": SECRETS[0] if secret else None, "etype": "RuntimeError" if secret else None,
             "tb": SECRETS[1] if secret else None,
             "trace": json.dumps({"content": SECRETS[2]}) if secret else None, "o": org})
    return run_id


def _seed(w: Any) -> None:
    p = w.p
    b.clear(p)
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM agent_run WHERE run_id LIKE 'aga9-%'"))
        c.execute(text("DELETE FROM dynamic_agents WHERE name LIKE 'aga9-%'"))
        for org, name in ((p.org_a, ORG_AGENT_A), (p.org_b, ORG_AGENT_B)):
            c.execute(text(
                "INSERT INTO dynamic_agents (name, description, organization_id) "
                "VALUES (:n, :d, CAST(:o AS uuid))"),
                {"n": name, "d": "Helper — drafts the weekly note.", "o": org})
    plain = b.project(p, p.org_a, OTHER_A, grant="org")
    led = b.project(p, p.org_a, OTHER_A, grant="org")
    hidden = b.project(p, p.org_a, OTHER_A)  # no grant: only an admin sees it
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_projects SET lead = :l WHERE id = CAST(:p AS uuid)"),
                  {"l": LEAD_A, "p": led["id"]})
    own = b.task(p, p.org_a, MEMBER_A, plain, title="Own task of the member")
    theirs = b.task(p, p.org_a, OTHER_A, plain)
    led_task = b.task(p, p.org_a, OTHER_A, led, title="Task of the led project")
    w.hidden_task = b.task(p, p.org_a, OTHER_A, hidden, title=HIDDEN_TITLE)
    w.x_own = b.run(p, p.org_a, own, OTHER_A, "completed", via=VIA_X,
                    created="now() - interval '4 minutes'")
    # The member's own run, on a task that the member cannot see: left out.
    w.x_hidden = b.run(p, p.org_a, w.hidden_task, MEMBER_A, "completed", via=VIA_X,
                       created="now() - interval '3 minutes'")
    w.x_led = b.run(p, p.org_a, led_task, OTHER_A, "failed", via=VIA_X,
                    created="now() - interval '2 minutes'")
    w.x_theirs = b.run(p, p.org_a, theirs, OTHER_A, "in_progress", via=VIA_X,
                       created="now() - interval '1 minute'")
    w.h_only = b.run(p, p.org_a, w.hidden_task, OTHER_A, "awaiting", via=VIA_H)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE pm_agent_runs SET error_metadata = CAST(:e AS jsonb) "
                       "WHERE id = CAST(:r AS uuid)"),
                  {"e": json.dumps({"body": ERROR_BODY + "\n\nstack line 2"}), "r": w.x_led})
    # 45 runs, an hour apart, on the member's own task. The newest is 1 hour old.
    w.many = [b.run(p, p.org_a, own, OTHER_A, "completed", via=VIA_MANY,
                    created=f"now() - interval '{i} hours'") for i in range(1, MANY + 1)]
    proj_b = b.project(p, p.org_b, ADMIN_B, grant="org")
    task_b = b.task(p, p.org_b, ADMIN_B, proj_b)
    w.x_b = b.run(p, p.org_b, task_b, ADMIN_B, "awaiting", via=VIA_X)
    w.only_b = b.run(p, p.org_b, task_b, ADMIN_B, "completed", via=VIA_B)
    # The chat runs of `agent_run`.
    w.c_member = _chat_run(p, p.org_a, "aga9-c-member", BUILT_IN, MEMBER_STORED, "completed",
                           started="now() - interval '5 minutes'",
                           ended="now() - interval '4 minutes'", duration_ms=61500)
    w.c_other = _chat_run(p, p.org_a, "aga9-c-other", BUILT_IN, OTHER_A, "error",
                          started="now() - interval '3 minutes'",
                          ended="now() - interval '2 minutes'", secret=True)
    w.c_lead = _chat_run(p, p.org_a, "aga9-c-lead", BUILT_IN, LEAD_A, "running",
                         started="now() - interval '1 minute'")
    w.c_org = _chat_run(p, p.org_a, "aga9-c-org", ORG_AGENT_A, MEMBER_A, "cancelled",
                        started="now() - interval '6 minutes'",
                        ended="now() - interval '6 minutes'")
    w.c_b = _chat_run(p, p.org_b, "aga9-c-b", BUILT_IN, ADMIN_B, "completed",
                      started="now()", ended="now()", duration_ms=10)
    w.c_org_b = _chat_run(p, p.org_b, "aga9-c-org-b", ORG_AGENT_B, ADMIN_B, "completed",
                          started="now()", ended="now()", duration_ms=10)


def _sees(w: Any) -> dict[str, dict[str, set[str] | None]]:
    """§4 as one table (Q3, proposed). For each viewer and key: the run ids
    of a 200, or ``None`` for the one 404. Change Q3 here, and only here."""
    x, h, bi, org = f"external:{VIA_X}", f"external:{VIA_H}", f"built_in:{BUILT_IN}", f"org:{ORG_AGENT_A}"
    only_b, org_b = f"external:{VIA_B}", f"org:{ORG_AGENT_B}"
    return {
        # Admin: every run of the organization, and nothing of org B.
        "admin_a": {x: {w.x_own, w.x_hidden, w.x_led, w.x_theirs}, h: {w.h_only},
                    bi: {w.c_member, w.c_other, w.c_lead}, org: {w.c_org},
                    only_b: None, org_b: None},
        # Lead of an app: the runs on the tasks of the projects they lead,
        # and their own chat runs.
        "lead_a": {x: {w.x_led}, h: None, bi: {w.c_lead}, org: set(),
                   only_b: None, org_b: None},
        # Member: the runs on their own tasks and their own runs, on tasks
        # they can see, and their own chat runs.
        "member_a": {x: {w.x_own}, h: None, bi: {w.c_member}, org: {w.c_org},
                     only_b: None, org_b: None},
        # The other org sees its own runs only, by the same keys.
        "admin_b": {x: {w.x_b}, only_b: {w.only_b}, bi: {w.c_b}, org_b: {w.c_org_b},
                    h: None, org: None},
    }


@pytest.fixture
def w(world, monkeypatch):
    from gateway.routes.projects import agent_summary as m

    monkeypatch.setattr(_settings(), "agent_runs_enabled", True)
    monkeypatch.setattr(_settings(), "agents_view_orgs", "*")

    async def _no_presence(*, organization_id: str | None) -> list[dict[str, Any]]:
        return []

    # The overview reads the live presence in Redis. The drill-down does not.
    monkeypatch.setattr(m, "active_runs", _no_presence)
    if not getattr(world, "seeded", False):
        _seed(world)
        world.seeded = True
    return world


async def _get(w: Any, user: Any, path: str) -> tuple[int, Any]:
    async with b._session(w.p) as call:
        got = await call(user, "GET", path)
    try:
        body = got.json()
    except ValueError:
        body = got.text
    return got.status_code, body


def _ids(body: dict[str, Any]) -> set[str]:
    return {row["id"] for row in body["runs"]}


@_DB_GATE
class TestTheDrillDown:
    @pytest.mark.parametrize("viewer", ["admin_a", "lead_a", "member_a", "admin_b"])
    async def test_each_viewer_sees_the_runs_of_the_table(self, w, viewer):
        user = getattr(w, viewer)
        for key, want in _sees(w)[viewer].items():
            status, body = await _get(w, user, _path(key))
            if want is None:
                assert (status, body) == (404, NOT_FOUND_BODY), (viewer, key, body)
                continue
            assert status == 200, (viewer, key, body)
            assert _ids(body) == want, (viewer, key)
            assert body["count"] == len(want) and body["truncated"] is False
            assert body["key"] == key
            assert body["viewer"] == {"admin_a": "admin", "admin_b": "admin",
                                      "lead_a": "lead", "member_a": "member"}[viewer]

    async def test_the_source_says_what_a_run_is(self, w):
        _, ext = await _get(w, w.admin_a, _path(f"external:{VIA_X}"))
        assert (ext["source"], ext["runs_of"]) == ("external", "task")
        assert all(row["task"]["id"] for row in ext["runs"])
        _, chat = await _get(w, w.admin_a, _path(f"built_in:{BUILT_IN}"))
        assert (chat["source"], chat["runs_of"]) == ("built_in", "chat")
        assert all(row["task"] is None for row in chat["runs"])
        _, org = await _get(w, w.member_a, _path(f"org:{ORG_AGENT_A}"))
        assert (org["source"], org["runs_of"]) == ("org", "chat")
        assert [row["state"] for row in org["runs"]] == ["stopped"]

    async def test_a_run_on_a_task_the_viewer_cannot_see_is_left_out(self, w):
        status, body = await _get(w, w.member_a, _path(f"external:{VIA_X}"))
        assert status == 200
        assert w.x_hidden not in _ids(body)
        text_ = json.dumps(body)
        assert HIDDEN_TITLE not in text_ and w.hidden_task not in text_
        # The admin sees the same run, with its task.
        _, admin = await _get(w, w.admin_a, _path(f"external:{VIA_X}"))
        row = next(r for r in admin["runs"] if r["id"] == w.x_hidden)
        assert row["task"] == {"id": w.hidden_task, "title": HIDDEN_TITLE}

    async def test_the_rows_carry_the_state_the_times_and_the_error(self, w):
        _, body = await _get(w, w.admin_a, _path(f"external:{VIA_X}"))
        rows = {r["id"]: r for r in body["runs"]}
        assert rows[w.x_theirs]["state"] == "working"
        assert rows[w.x_theirs]["ended_at"] is None and rows[w.x_theirs]["run_seconds"] is None
        assert rows[w.x_own]["state"] == "completed"
        assert rows[w.x_own]["ended_at"] and rows[w.x_own]["run_seconds"] > 0
        assert rows[w.x_own]["task"]["title"] == "Own task of the member"
        err = rows[w.x_led]["error"]
        assert err["what"] == f"This run failed. The agent said: {ERROR_BODY} stack line 2"
        assert err["next_step"].startswith("Open the task")
        assert rows[w.x_own]["error"] is None
        _, chat = await _get(w, w.admin_a, _path(f"built_in:{BUILT_IN}"))
        crow = {r["id"]: r for r in chat["runs"]}
        assert crow[w.c_member]["run_seconds"] == 61.5
        assert crow[w.c_other]["state"] == "failed" and 55 <= crow[w.c_other]["run_seconds"] <= 65
        assert crow[w.c_other]["error"]["next_step"].startswith("Ask the agent again")
        assert crow[w.c_lead]["state"] == "working" and crow[w.c_lead]["run_seconds"] is None
        assert crow[w.c_lead]["ended_at"] is None

    async def test_the_answer_holds_the_lean_keys_only(self, w):
        from gateway.routes.projects.agent_summary import ANSWER_KEYS, RUN_KEYS

        for key in (f"external:{VIA_X}", f"built_in:{BUILT_IN}", f"org:{ORG_AGENT_A}"):
            status, body = await _get(w, w.admin_a, _path(key))
            assert status == 200, body
            assert tuple(body) == ANSWER_KEYS
            for row in body["runs"]:
                assert tuple(row) == RUN_KEYS
                assert row["task"] is None or set(row["task"]) == {"id", "title"}
                assert row["error"] is None or set(row["error"]) == {"at", "what", "next_step"}
            text_ = json.dumps(body)
            for secret in SECRETS:
                assert secret not in text_, (key, secret)
            assert "trace" not in text_.lower()

    async def test_at_most_40_runs_newest_first(self, w):
        status, body = await _get(w, w.admin_a, _path(f"external:{VIA_MANY}"))
        assert status == 200
        assert body["count"] == len(body["runs"]) == 40 and body["truncated"] is True
        assert [r["id"] for r in body["runs"]] == w.many[:40]
        starts = [r["started_at"] for r in body["runs"]]
        assert starts == sorted(starts, reverse=True) and len(set(starts)) == 40
        _, chat = await _get(w, w.admin_a, _path(f"built_in:{BUILT_IN}"))
        assert [r["id"] for r in chat["runs"]] == [w.c_lead, w.c_other, w.c_member]

    async def test_the_drilldown_agrees_with_the_overview(self, w):
        candidates = (VIA_X, VIA_H, VIA_B, VIA_MANY)
        for user in (w.admin_a, w.lead_a, w.member_a, w.admin_b):
            status, overview = await _get(w, user, "/projects/agents/overview")
            assert status == 200, overview
            listed = {r["agent"]: r["runs"] for r in overview["agents"] if r["source"] == "external"}
            for via in candidates:
                status, body = await _get(w, user, _path(f"external:{via}"))
                if via not in listed:
                    assert status == 404, (user.email, via)
                    continue
                assert status == 200, (user.email, via)
                assert body["count"] == min(listed[via], 40), (user.email, via)
                assert body["truncated"] is (listed[via] > 40)

    async def test_every_miss_is_the_same_404(self, w):
        misses = [
            (w.admin_a, "external:mcp:acme:nobody"),
            (w.admin_a, f"external:{VIA_B}"),  # a guess of org B's agent
            (w.admin_a, f"org:{ORG_AGENT_B}"),
            (w.admin_a, "org:aga9-nobody"),
            (w.admin_a, "org:" + BUILT_IN),  # a built-in name is never an org key
            (w.admin_a, "built_in:aga9-not-an-agent"),
            (w.admin_a, "built_in:a b"),
            (w.admin_a, "external:http://evil.example"),
            (w.admin_a, "built_in:x'; DROP TABLE agent_run; --"),
            (w.admin_a, "org:" + "a" * 300),
            (w.member_a, f"external:{VIA_H}"),  # out of the member's reach
            (w.lead_a, f"external:{VIA_H}"),
            (w.admin_b, f"external:{VIA_H}"),
        ]
        for user, key in misses:
            status, body = await _get(w, user, _path(key))
            assert (status, body) == (404, NOT_FOUND_BODY), (user.email, key, body)
        # A path that the router does not hold reads the same.
        status, body = await _get(w, w.admin_a, "/projects/agents/x/y/runs")
        assert (status, body) == (404, NOT_FOUND_BODY)

    async def test_a_hostile_query_changes_nothing(self, w):
        path = _path(f"external:{VIA_X}")
        _, plain = await _get(w, w.member_a, path)
        status, hostile = await _get(
            w, w.member_a,
            f"{path}?organization_id={w.p.org_b}&org={w.p.org_b}&me={OTHER_A}&vis_email={OTHER_A}")
        assert status == 200 and hostile == plain
        _, chat = await _get(w, w.member_a, _path(f"built_in:{BUILT_IN}") + f"?me={OTHER_A}")
        assert _ids(chat) == {w.c_member}


@_DB_GATE
class TestTheFlags:
    async def test_with_the_flag_empty_every_key_is_404(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", "")
        for user in (w.admin_a, w.lead_a, w.member_a, w.admin_b):
            for key in (f"external:{VIA_X}", f"built_in:{BUILT_IN}", f"org:{ORG_AGENT_A}"):
                status, body = await _get(w, user, _path(key))
                assert (status, body) == (404, NOT_FOUND_BODY), (user.email, key)

    async def test_with_org_a_on_org_b_is_still_404(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agents_view_orgs", w.p.org_a)
        assert (await _get(w, w.member_a, _path(f"external:{VIA_X}")))[0] == 200
        status, body = await _get(w, w.admin_b, _path(f"external:{VIA_X}"))
        assert (status, body) == (404, NOT_FOUND_BODY)

    async def test_the_runs_flag_off_hides_the_task_runs_only(self, w, monkeypatch):
        monkeypatch.setattr(_settings(), "agent_runs_enabled", False)
        status, body = await _get(w, w.admin_a, _path(f"external:{VIA_X}"))
        assert (status, body) == (404, NOT_FOUND_BODY)
        status, body = await _get(w, w.admin_a, _path(f"built_in:{BUILT_IN}"))
        assert status == 200 and _ids(body) == {w.c_member, w.c_other, w.c_lead}
