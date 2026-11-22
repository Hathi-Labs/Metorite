"""PJP-14 - a ready agent task is not "New" (R8, D170 amendment).

Spec: ``project-docs/specs/one_projects_app.md`` section 9b (INB-1, D170 amendment).

An agent-made task is READY when it is on a company board, has
``source = 'mcp'``, an assignee, a description, a type, and a status that
is not in the ``triage`` stage. A ready task is never New. It keeps its
derived disposition. The rule reads the row and writes nothing, so an edit
that breaks a condition makes the task New again.

Each test runs the REAL routes as a NON-privileged role on a promoted
catalog of its own. The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is
not a pass::

    uv run pytest tests/unit/test_projects_ready_agent_r8.py -v -rs
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database  # noqa: E402
from tests.unit._tenant_ladder import apply_ladder
from tests.unit.test_projects_mine_r8 import (
    _DB_GATE,
    _GENERATED,
    _URL,
    INTERNAL,
    _board,
    _drop_database,
    _exec_sql_file,
    _list_mine,
    _ok,
    _Seeder,
)

pytestmark = _DB_GATE

_APP_ROLE = "acb_app_pjp14"
_APP_PW = "pjp14_scratch_pw"
DOMAIN = "pjp14.test"
A = f"alice@{DOMAIN}"
B = f"bob@{DOMAIN}"


def _set(s: _Seeder, task: str, **cols) -> None:
    """Admin write of columns on one task (the edit that breaks a condition)."""
    sets = ", ".join(f"{k} = :{k}" for k in cols)
    s.one(f"UPDATE pm_tasks SET {sets} WHERE id = CAST(:id AS uuid) RETURNING id",
          id=task, **cols)


@pytest.fixture(scope="module")
def world():
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_pjp14"
    _clone_promoted_database(admin_url, dedicated)
    ded = admin_url.set(database=dedicated)
    eng = create_engine(ded, future=True)
    with create_engine(ded, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))

    org = str(uuid.uuid4())
    with eng.begin() as c:
        c.execute(text("INSERT INTO organization (id, slug, display_name) "
                       "VALUES (:id, 'pjp14', 'pjp14')"), {"id": org})
    s = _Seeder(eng, org, "pjp14")
    s.member(A, ["feature:projects"])
    s.member(B, ["feature:projects"])

    team = _board(s, "Team", by=B, grant_to="org")
    triage = s.status(team.id, "Intake", "triage", 5)
    a_root = _board(s, "Alice root", by=A, personal_owner=A, grant_to=A)
    kind = str(s.one(
        "INSERT INTO pm_task_types (project_id, name, organization_id)"
        " VALUES (CAST(:p AS uuid), 'Task', CAST(:o AS uuid)) RETURNING id",
        p=team.id, o=org))

    t: dict[str, str] = {}

    def make(key, *, source="mcp", desc="Do the thing.", typed=True,
             assignee=A, status=team.todo, project=team.id):
        tid = s.task(key, project, status, by=B, assignee=assignee)
        _set(s, tid, source=source, description=desc,
             type_id=kind if typed else None)
        t[key] = tid

    make("ready")
    make("ready_later", status=team.backlog)
    make("no_desc", desc=None)
    make("blank_desc", desc="   ")
    make("no_type", typed=False)
    make("manual", source="manual")
    make("in_triage", status=triage)
    make("personal_board", project=a_root.id)
    # A person hands an agent-made ready task to A. The creator is B, the
    # hand-off is by C (D170.2): it shows New.
    make("by_person")
    s.one("UPDATE pm_task_assignees SET assigned_by = :a WHERE task_id ="
          " CAST(:t AS uuid) RETURNING task_id", a="carol@pjp14.test",
          t=t["by_person"])
    # A legacy row with an empty assigned_by shows New. The column is NOT NULL
    # (migration 146), so a NULL cannot exist. SQL lower(NULL) = ... is not
    # true, and the empty string is the nearest legacy shape.
    make("null_by")
    s.one("UPDATE pm_task_assignees SET assigned_by = '' WHERE task_id ="
          " CAST(:t AS uuid) RETURNING task_id", t=t["null_by"])
    # The agent's own hand-off to a colleague stays ready: the creator is B,
    # and B wrote the row.
    make("breaks")  # ready now, edited in the break test
    # Ready, assigned to B only: it is not A's, so it is in no list of A.
    make("others", assignee=B)
    # An mcp task nobody holds: not ready (no assignee), and not A's.
    make("nobody", assignee=None)

    w = SimpleNamespace(
        admin=eng, org=org, t=t, s=s, kind=kind, team=team,
        app_dsn=ded.set(username=_APP_ROLE, password=_APP_PW)
        .render_as_string(hide_password=False))
    yield w
    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def armed(monkeypatch, world):
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    try:
        yield world
    finally:
        invalidate_access()


async def _state(world):
    """Rows with the overlay, the summary, and the untriaged read of A."""
    rows = {r["id"]: r for r in await _list_mine(world, A, with_overlay="1")}
    summary = await _ok(world, A, "/projects/my/summary")
    untriaged = await _ok(world, A, "/projects/my/inbox",
                          {"untriaged": "true", "include_deferred": "true",
                           "page_size": 100})
    return rows, summary["by_disposition"], {r["id"] for r in untriaged["rows"]}


def _named(world, ids):
    back = {v: k for k, v in world.t.items()}
    return {back[i] for i in ids}


class TestReadyAgentTask:
    async def test_a_ready_task_is_not_new_and_counts_under_next_or_someday(self, armed):
        rows, counts, untriaged = await _state(armed)
        ready, later = armed.t["ready"], armed.t["ready_later"]
        assert ready in rows and later in rows
        assert rows[ready]["is_new"] is False
        assert rows[later]["is_new"] is False
        assert rows[ready]["disposition"] == "NEXT"
        # The card draws its "AI agent" mark (taskCard.ts TASK_SOURCES) from this.
        assert rows[ready]["source"] == "mcp"
        assert rows[later]["disposition"] == "SOMEDAY"
        assert ready not in untriaged and later not in untriaged
        assert counts["someday"] == 1
        assert counts["next"] >= 2  # ready and breaks
        assert "ready" not in _named(armed, {i for i, r in rows.items() if r["is_new"]})

    async def test_a_task_missing_a_condition_stays_new(self, armed):
        rows, counts, untriaged = await _state(armed)
        stays = {"no_desc", "blank_desc", "no_type", "manual", "by_person", "null_by"}
        assert _named(armed, {i for i, r in rows.items() if r["is_new"]}) == stays
        # The untriaged read keeps the triage-stage task: it is not ready.
        assert _named(armed, untriaged) == stays | {"in_triage"}
        assert counts["new"] == len(stays)
        # A triage-stage task is parked: the default reads hide it (WS-27u),
        # and the agreement test below holds that it is not ready.
        assert armed.t["in_triage"] not in rows
        # The personal board is not New (D170), whatever the source.
        assert rows[armed.t["personal_board"]]["is_new"] is False
        # Tasks that are not A's are in no list of A.
        assert armed.t["others"] not in rows and armed.t["nobody"] not in rows

    async def test_a_manual_task_is_unchanged(self, armed):
        rows, _, untriaged = await _state(armed)
        manual = armed.t["manual"]
        assert rows[manual]["is_new"] is True and manual in untriaged

    async def test_breaking_a_condition_makes_it_new_again(self, armed):
        s, task = armed.s, armed.t["breaks"]
        _, before, _ = await _state(armed)
        try:
            _set(s, task, description=None)
            rows, after, untriaged = await _state(armed)
            assert rows[task]["is_new"] is True and task in untriaged
            assert after["new"] == before["new"] + 1
            _set(s, task, description="Back again.")
            _set(s, task, type_id=None)
            rows, _, _ = await _state(armed)
            assert rows[task]["is_new"] is True
            _set(s, task, type_id=armed.kind)
            rows, back, untriaged = await _state(armed)
            assert rows[task]["is_new"] is False and task not in untriaged
            assert back["new"] == before["new"]
        finally:
            _set(s, task, description="Back again.", type_id=armed.kind)

    async def test_the_sql_and_the_python_rule_agree_on_every_task(self, armed):
        """R7. ``READY_AGENT_SQL`` and ``is_ready_agent_task`` give one answer."""
        from gateway.routes.projects import mine

        sql = (
            f"SELECT t.id, {mine.READY_AGENT_SQL} AS sql_ready,"
            f" {mine.AGENT_FACTS_SQL}, s.category AS category,"
            " EXISTS (SELECT 1 FROM pm_task_assignees a WHERE a.task_id = t.id)"
            "   AS has_assignee,"
            " NOT EXISTS (SELECT 1 FROM pm_projects pj WHERE pj.id = t.project_id"
            "   AND pj.personal_owner IS NOT NULL) AS on_board"
            " FROM pm_tasks t JOIN pm_task_statuses s ON s.id = t.status_id"
        )
        with armed.admin.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": armed.org})
            rows = c.execute(text(sql), {"who": A}).fetchall()
        assert len(rows) == len(armed.t)
        answers = {}
        for r in rows:
            py = mine._ready_from_row(r)
            assert py == r.sql_ready, (r.id, py, r.sql_ready)
            answers[str(r.id)] = py
        back = {v: k for k, v in armed.t.items()}
        ready = {back[i] for i, v in answers.items() if v}
        # "others" is B's: A holds no assignee row, so it is not ready for A.
        assert ready == {"ready", "ready_later", "breaks"}
