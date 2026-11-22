"""RESUME-1a: ``chat_pending_ask.stored_call`` on a real Postgres, under FORCE RLS (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4.12.4 item 1 and the
RESUME-1 row of §4.12.7 (task 3260). The SQL of ``orchestrator.pending_ask``
runs against the H3 phase-4 catalog (the full ladder, then
``generated/01..04``), connected as the NOBYPASSRLS role ``acb_app_h3rls``. A
superuser or a table owner would bypass the policy and prove nothing.

What it holds (R7):

1. The migration is found by CONTENT, not number (R1). It is expand-only (R6),
   a second run changes nothing, and an INSERT that does not name the column
   still works.
2. FORCE RLS holds the column: org B reads none of org A's stored call, an
   unbound session reads nothing, and WITH CHECK refuses a row of another org.
3. The column holds a jsonb object. ``read_ask``, ``waiting_asks`` and the
   RETURNING of ``move_ask`` carry no stored call.
4. ``read_stored_call`` gives it for org A only: nothing for org B, an expired
   row, an answered row or a closed row.
5. A move to ``closed`` or ``answered`` and ``close_thread_asks`` set it to
   NULL. A park keeps it.
6. ``GET /chat/pending-asks`` on the real database gives no ``stored_call``
   key and no argument.

They skip without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db> \\
        uv run pytest tests/unit/test_resume_stored_call_r8.py -v -rs
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pending_ask = pytest.importorskip("orchestrator.pending_ask")
pytest.importorskip("sqlalchemy")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import DBAPIError, ProgrammingError  # noqa: E402

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_chat_deploy_recovery import (  # noqa: E402, F401
    fake_redis,
    liveness,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)
from tests.unit.test_pending_ask_store import (  # noqa: E402, F401
    _ALICE,
    _QUESTION,
    as_app,
    flag_on,
    world,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
#: A value that only the arguments of the stored call hold.
_SENTINEL = "zq-sentinel-r8-5c1e"


def _migration() -> Path:
    """The migration that adds the column, found by content (R1)."""
    hits = [
        p for p in (_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql")
        if "ADD COLUMN IF NOT EXISTS stored_call" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, hits
    return hits[0]


def _stored(tool: str = "set_reminder") -> dict[str, Any]:
    """A stored call of version 1, as ``acb_skills.stored_call`` builds it."""
    return {
        "v": 1, "kind": "confirmation", "tool": tool, "module": "skill_projects.writes",
        "agent": "orchestrator", "arguments": {"title": f"Call {_SENTINEL}"},
        "call_id": "call_1", "card_text": '["Run this change?","","",{}]',
        "act_keys": ['["skill_projects.writes","set_reminder",[]]'], "digest": "0" * 32,
    }


def _ask(org: str, tid: str, *, stored: dict[str, Any] | None = None) -> str:
    rid = uuid.uuid4().hex
    row: dict[str, Any] = {
        "request_id": rid, "thread_id": tid, "actor_email": _ALICE,
        "kind": "confirmation", "question": _QUESTION,
        "payload": {"request_id": rid, "title": _QUESTION},
    }
    if stored is not None:
        row["stored_call"] = stored
    assert pending_ask.insert_ask(org, row)
    return rid


def _admin_stored(world, org: str, rid: str) -> Any:
    with world.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT stored_call FROM chat_pending_ask "
            "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
            {"o": org, "r": rid}).scalar_one()


# ---------------------------------------------------------------------------
# 1. The migration
# ---------------------------------------------------------------------------

def test_the_migration_is_expand_only_and_runs_twice(world):
    body = _migration().read_text(encoding="utf-8")
    for verb in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER COLUMN", "TRUNCATE"):
        assert verb not in body.upper(), f"R6: {verb} in an expand-only migration"
    sql = "\n".join(line for line in body.splitlines()
                    if not line.lstrip().startswith("--")).upper()
    for verb in ("NOT NULL", "DEFAULT", "UPDATE ", "CHECK"):
        assert verb not in sql, f"R6: {verb} in the statements of the migration"
    assert "lock_timeout" in body
    with world.admin_engine.connect() as c:
        before = c.execute(text(
            "SELECT format_type(atttypid, atttypmod), attnotnull, atthasdef "
            "FROM pg_attribute WHERE attrelid = 'chat_pending_ask'::regclass "
            "AND attname = 'stored_call' AND NOT attisdropped")).first()
    assert tuple(before) == ("jsonb", False, False), "nullable jsonb, no default"
    with world.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(body)  # a second run changes nothing, and raises nothing
    with world.admin_engine.connect() as c:
        after = c.execute(text(
            "SELECT format_type(atttypid, atttypmod), attnotnull, atthasdef, "
            "col_description('chat_pending_ask'::regclass, attnum) "
            "FROM pg_attribute WHERE attrelid = 'chat_pending_ask'::regclass "
            "AND attname = 'stored_call' AND NOT attisdropped")).first()
    assert tuple(after[:3]) == tuple(before)
    assert "RESUME-1a" in after[3]


def test_an_insert_that_does_not_name_the_column_still_works(as_app):
    """Old code meets the new schema (R6): the statement of before writes NULL."""
    w = as_app
    rid = _ask(w.org_a, w.t_alice)
    assert _admin_stored(w, w.org_a, rid) is None
    assert pending_ask.read_stored_call(w.org_a, rid) is None


# ---------------------------------------------------------------------------
# 2. FORCE RLS holds the column
# ---------------------------------------------------------------------------

def test_another_org_never_reads_the_stored_call(as_app, app_engine):
    w = as_app
    rid = _ask(w.org_a, w.t_alice, stored=_stored())
    assert pending_ask.read_stored_call(w.org_a, rid) == _stored()
    assert pending_ask.read_stored_call(w.org_b, rid) is None
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": w.org_b})
        seen = c.execute(text(
            "SELECT count(*) FROM chat_pending_ask WHERE request_id = :r "
            "AND stored_call IS NOT NULL"), {"r": rid}).scalar_one()
    assert seen == 0, "org B read org A's stored call"


def test_an_unbound_session_reads_no_stored_call(as_app, app_engine):
    _ask(as_app.org_a, as_app.t_alice, stored=_stored())
    with app_engine.connect() as c, c.begin():
        assert c.execute(text(
            "SELECT count(*) FROM chat_pending_ask WHERE stored_call IS NOT NULL",
        )).scalar_one() == 0


def test_with_check_refuses_a_stored_call_of_another_org(as_app, app_engine):
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": as_app.org_b})
        with pytest.raises((DBAPIError, ProgrammingError)) as exc:
            c.execute(text(
                "INSERT INTO chat_pending_ask (organization_id, request_id, thread_id, "
                "kind, stored_call) VALUES (CAST(:a AS uuid), :r, 't', 'confirmation', "
                "CAST(:s AS jsonb))"),
                {"a": as_app.org_a, "r": uuid.uuid4().hex, "s": json.dumps(_stored())})
    assert "row-level security" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 3. A jsonb object, and no browser read carries it
# ---------------------------------------------------------------------------

def test_the_column_holds_an_object_and_no_read_of_the_columns_carries_it(as_app):
    w = as_app
    rid = _ask(w.org_a, w.t_alice, stored=_stored())
    with w.admin_engine.connect() as c:
        kind = c.execute(text(
            "SELECT jsonb_typeof(stored_call) FROM chat_pending_ask WHERE request_id = :r"),
            {"r": rid}).scalar_one()
    assert kind == "object"
    assert _admin_stored(w, w.org_a, rid) == _stored()

    read = pending_ask.read_ask(w.org_a, rid)
    listed = pending_ask.waiting_asks(w.org_a, thread_id=w.t_alice)
    moved = pending_ask.move_ask(w.org_a, rid, to="parked", from_states=("open",))
    assert read is not None and moved is not None and rid in {r["request_id"] for r in listed}
    for row in (read, moved, *listed):
        assert "stored_call" not in row
        assert _SENTINEL not in json.dumps(row, default=str)


# ---------------------------------------------------------------------------
# 4. The one reader
# ---------------------------------------------------------------------------

def test_the_reader_gives_a_waiting_row_of_the_org_only(as_app):
    w = as_app
    live = _ask(w.org_a, w.t_alice, stored=_stored())
    parked = _ask(w.org_a, w.t_alice, stored=_stored("update_task"))
    assert pending_ask.move_ask(w.org_a, parked, to="parked", from_states=("open",))
    expired = _ask(w.org_a, w.t_alice, stored=_stored())
    answered = _ask(w.org_a, w.t_alice, stored=_stored())
    closed = _ask(w.org_a, w.t_alice, stored=_stored())
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE chat_pending_ask SET expires_at = now() - interval '1 minute' "
            "WHERE request_id = :r"), {"r": expired})
        # A row that left the waiting states by another path keeps its value
        # here, so the reader's own state test is what hides it.
        c.execute(text(
            "UPDATE chat_pending_ask SET state = 'answered', answered_at = now() "
            "WHERE request_id = :r"), {"r": answered})
        c.execute(text(
            "UPDATE chat_pending_ask SET state = 'closed', answered_at = now() "
            "WHERE request_id = :r"), {"r": closed})

    assert pending_ask.read_stored_call(w.org_a, live) == _stored()
    assert pending_ask.read_stored_call(w.org_a, parked) == _stored("update_task")
    assert pending_ask.read_stored_call(w.org_b, live) is None
    for rid in (expired, answered, closed):
        assert _admin_stored(w, w.org_a, rid) is not None, "non-vacuity"
        assert pending_ask.read_stored_call(w.org_a, rid) is None


# ---------------------------------------------------------------------------
# 5. An answer and a close clear it, and a park keeps it
# ---------------------------------------------------------------------------

def test_an_answer_and_a_close_clear_the_call_and_a_park_keeps_it(as_app):
    w = as_app
    parked = _ask(w.org_a, w.t_alice, stored=_stored())
    answered = _ask(w.org_a, w.t_alice, stored=_stored())
    closed = _ask(w.org_a, w.t_alice, stored=_stored())

    assert pending_ask.move_ask(w.org_a, parked, to="parked", from_states=("open",))
    assert _admin_stored(w, w.org_a, parked) == _stored(), "a park keeps the call"
    assert pending_ask.move_ask(w.org_a, answered, to="answered",
                                from_states=pending_ask.WAITING, answer="APPROVE")
    assert pending_ask.move_ask(w.org_a, closed, to="closed", from_states=("open",))
    assert _admin_stored(w, w.org_a, answered) is None
    assert _admin_stored(w, w.org_a, closed) is None

    # A late answer to the parked row clears it too.
    assert pending_ask.move_ask(w.org_a, parked, to="answered",
                                from_states=pending_ask.WAITING, answer="APPROVE")
    assert _admin_stored(w, w.org_a, parked) is None


def test_closing_the_thread_clears_the_call(as_app):
    from acb_graph import tenant_session

    w = as_app
    tid = f"rs-close-{uuid.uuid4().hex[:8]}"
    open_rid = _ask(w.org_a, tid, stored=_stored())
    parked_rid = _ask(w.org_a, tid, stored=_stored())
    assert pending_ask.move_ask(w.org_a, parked_rid, to="parked", from_states=("open",))
    with tenant_session(w.org_a) as s:
        assert pending_ask.close_thread_asks(s, tid) == 2
    for rid in (open_rid, parked_rid):
        assert _admin_stored(w, w.org_a, rid) is None
        assert pending_ask.read_stored_call(w.org_a, rid) is None


# ---------------------------------------------------------------------------
# 6. The browser route, on the real database
# ---------------------------------------------------------------------------

def test_pending_asks_gives_no_stored_call(as_app, flag_on, liveness, monkeypatch):
    from acb_auth import UserContext, UserRole, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import agent as agent_routes
    from gateway.routes import chat as chat_routes

    w = as_app
    rid = _ask(w.org_a, w.t_alice, stored=_stored())
    assert pending_ask.move_ask(w.org_a, rid, to="parked", from_states=("open",))
    assert _SENTINEL in json.dumps(_admin_stored(w, w.org_a, rid)), "non-vacuity"

    class _Room:
        can_send = True

    monkeypatch.setattr(agent_routes, "_resolve_room", lambda *_a, **_k: _Room())
    app = FastAPI()
    app.get("/chat/pending-asks")(chat_routes.list_pending_asks)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=_ALICE, role=UserRole.EMPLOYEE, organization_id=w.org_a,
    )
    res = TestClient(app).get("/chat/pending-asks", params={"thread_id": w.t_alice})
    assert res.status_code == 200, res.text
    assert rid in [a["requestId"] for a in res.json()]
    assert "stored_call" not in res.text
    assert _SENTINEL not in res.text
