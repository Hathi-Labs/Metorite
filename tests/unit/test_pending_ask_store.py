"""WS-51 S2 — ``chat_pending_ask`` on a real Postgres, under FORCE RLS (R8).

Spec: ``project-docs/specs/chat_run_continuity.md`` §4 S2. The SQL of
``orchestrator.pending_ask`` and the route's lookup run against the H3
phase-4 catalog (the full ladder, then ``generated/01..04``), connected as
the NOBYPASSRLS role ``acb_app_h3rls``. A superuser or a table owner would
bypass the policy and prove nothing.

What it holds (R7):

1. The migration is found by CONTENT, not number (R1), it is expand-only
   (R6), and a second run changes nothing.
2. The table is FORCE RLS with a USING and WITH CHECK policy.
3. Org B never reads, lists or moves org A's row. An unbound session reads
   nothing. A write stamped with another org is refused.
4. ``move_ask`` moves a row once: two late answers resend once.
5. An expired row is never read and never moves.
6. The route: Alice's late answer to her parked card comes back as
   ``run_restarted`` and the row keeps her answer. Carol, of org B, gets
   nothing back for the same request id, and the row does not move.
7. ``GET /chat/active-sessions`` lists Alice's parked thread as
   ``needs_input`` with no live run, and never lists it for Carol.

They skip without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private_db> \\
        uv run pytest tests/unit/test_pending_ask_store.py -v -rs
"""
# The fixtures imported by name below are redefined as test arguments (F811).
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

pending_ask = pytest.importorskip("orchestrator.pending_ask")
stream_relay = pytest.importorskip("orchestrator.stream_relay")
pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, ProgrammingError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

# Fixtures, resolved by name. The imports are load-bearing.
from tests.unit.test_chat_deploy_recovery import (  # noqa: E402, F401
    fake_redis,
    liveness,
    no_persist,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@pa-a.test"
_CAROL = "carol@pa-b.test"
_QUESTION = "Archive the Apollo project?"

pytestmark = _DB_GATE


def _migration() -> Path:
    """The migration that creates the table, found by content (R1)."""
    hits = [
        p for p in (_ROOT / "infra" / "postgres").glob("[0-9]*_*.sql")
        if "CREATE TABLE IF NOT EXISTS chat_pending_ask" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, hits
    return hits[0]


@pytest.fixture(scope="module")
def world(promoted):
    """Alice in org A with a chat, Carol in org B with a chat. As the superuser."""
    ns = promoted
    ns.t_alice = f"pa-a-{uuid.uuid4().hex[:8]}"
    ns.t_carol = f"pa-b-{uuid.uuid4().hex[:8]}"
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, ns.org_a), (_CAROL, ns.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', :o) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        for tid, email, org in ((ns.t_alice, _ALICE, ns.org_a),
                                (ns.t_carol, _CAROL, ns.org_b)):
            c.execute(text(
                "INSERT INTO chat_session (id, user_id, agent_name, title, "
                "organization_id) VALUES (:id, :u, 'orchestrator', 'a chat', :o)"),
                {"id": tid, "u": email, "o": org})
        # Migration 222 grants this to `acb_app` only. The rehearsal role has
        # a suite-private name, so it gets the same grant here.
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return ns


@pytest.fixture
def as_app(world, monkeypatch):
    """``acb_graph`` sessions open as the app role, one fresh backend each."""
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield world
    finally:
        eng.dispose()


@pytest.fixture
def flag_on(monkeypatch):
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_durable_asks", True)


def _ask(org: str, tid: str, actor: str = _ALICE) -> str:
    rid = uuid.uuid4().hex
    assert pending_ask.insert_ask(org, {
        "request_id": rid, "thread_id": tid, "actor_email": actor,
        "kind": "confirmation", "question": _QUESTION,
        "payload": {"request_id": rid, "title": _QUESTION},
    })
    return rid


def _admin_row(world, org: str, rid: str):
    with world.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT state, answer FROM chat_pending_ask "
            "WHERE organization_id = CAST(:o AS uuid) AND request_id = :r"),
            {"o": org, "r": rid}).first()


# ---------------------------------------------------------------------------
# 1 and 2. The migration and the policy
# ---------------------------------------------------------------------------

def test_the_migration_is_expand_only_and_runs_twice(world):
    body = _migration().read_text(encoding="utf-8").upper()
    for verb in ("DROP TABLE", "DROP COLUMN", "RENAME", "ALTER COLUMN", "TRUNCATE"):
        assert verb not in body, f"R6: {verb} in an expand-only migration"
    sql = _migration().read_text(encoding="utf-8")
    with world.admin_engine.begin() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)  # a second run changes nothing, and raises nothing


def test_the_table_is_force_rls_with_a_two_sided_policy(world):
    with world.admin_engine.connect() as c:
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'chat_pending_ask'::regclass")).first()
        pol = c.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'chat_pending_ask'")).fetchall()
    assert rls == (True, True)
    assert len(pol) == 1 and "app.tenant_id" in pol[0][0] and "app.tenant_id" in pol[0][1]


def test_the_app_role_cannot_bypass_rls(app_engine):
    with app_engine.connect() as c:
        row = c.execute(text(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
    assert row == (False, False)


# ---------------------------------------------------------------------------
# 3. Two tenants
# ---------------------------------------------------------------------------

def test_another_org_never_reads_lists_or_moves_the_row(as_app):
    w = as_app
    rid = _ask(w.org_a, w.t_alice)

    assert pending_ask.read_ask(w.org_a, rid)["question"] == _QUESTION
    assert pending_ask.read_ask(w.org_b, rid) is None
    assert pending_ask.waiting_asks(w.org_b, thread_id=w.t_alice) == []
    assert pending_ask.waiting_asks(w.org_b, actor_email=_ALICE) == []
    assert pending_ask.move_ask(
        w.org_b, rid, to="answered", from_states=pending_ask.WAITING, answer="x",
    ) is None
    assert tuple(_admin_row(w, w.org_a, rid)) == ("open", None)
    mine = pending_ask.waiting_asks(w.org_a, actor_email=_ALICE.upper())
    assert rid in {r["request_id"] for r in mine}


def test_an_unbound_session_reads_nothing(as_app, app_engine):
    _ask(as_app.org_a, as_app.t_alice)
    with app_engine.connect() as c, c.begin():
        assert c.execute(text("SELECT count(*) FROM chat_pending_ask")).scalar_one() == 0


def test_a_write_stamped_with_another_org_is_refused(as_app, app_engine):
    with app_engine.connect() as c, c.begin():
        c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": as_app.org_b})
        with pytest.raises((DBAPIError, ProgrammingError)) as exc:
            c.execute(text(
                "INSERT INTO chat_pending_ask (organization_id, request_id, "
                "thread_id, kind) VALUES (CAST(:a AS uuid), :r, 't', 'ask_user')"),
                {"a": as_app.org_a, "r": uuid.uuid4().hex})
    assert "row-level security" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 4 and 5. Once, and never after expiry
# ---------------------------------------------------------------------------

def test_a_row_moves_once(as_app):
    w = as_app
    rid = _ask(w.org_a, w.t_alice)
    assert pending_ask.move_ask(w.org_a, rid, to="parked", from_states=("open",))["state"] == "parked"
    first = pending_ask.move_ask(
        w.org_a, rid, to="answered", from_states=pending_ask.WAITING, answer="APPROVE",
    )
    second = pending_ask.move_ask(
        w.org_a, rid, to="answered", from_states=pending_ask.WAITING, answer="REJECT",
    )
    assert first["state"] == "answered" and first["answered_at"]
    assert second is None
    assert tuple(_admin_row(w, w.org_a, rid)) == ("answered", "APPROVE")
    assert pending_ask.insert_ask(w.org_a, {
        "request_id": rid, "thread_id": w.t_alice, "kind": "confirmation",
    }) is False, "a second write of the same request is a no-op"


def test_an_expired_row_is_never_read_or_moved(as_app):
    w = as_app
    rid = _ask(w.org_a, w.t_alice)
    with w.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE chat_pending_ask SET expires_at = now() - interval '1 minute' "
            "WHERE request_id = :r"), {"r": rid})
    assert pending_ask.read_ask(w.org_a, rid) is None
    assert pending_ask.move_ask(w.org_a, rid, to="parked", from_states=("open",)) is None


def test_the_checks_refuse_a_bad_row(as_app):
    with pytest.raises((DBAPIError, ProgrammingError)):
        pending_ask.insert_ask(as_app.org_a, {
            "request_id": "not-a-hex-id", "thread_id": "t", "kind": "ask_user",
        })
    with pytest.raises((DBAPIError, ProgrammingError)):
        pending_ask.insert_ask(as_app.org_a, {
            "request_id": uuid.uuid4().hex, "thread_id": "t", "kind": "a_new_kind",
        })


# ---------------------------------------------------------------------------
# 6 and 7. The route and the list, under RLS
# ---------------------------------------------------------------------------

def _client(email: str, org: str):
    from acb_auth import UserContext, UserRole, get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import agent as agent_routes

    app = FastAPI()
    app.post("/agent/respond-input")(agent_routes.respond_user_input)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
    )
    return TestClient(app)


def test_the_late_answer_resolves_under_the_callers_tenant_only(
    as_app, flag_on, liveness, no_persist,
):
    w = as_app
    rid = _ask(w.org_a, w.t_alice)
    pending_ask.move_ask(w.org_a, rid, to="parked", from_states=("open",))
    body = {"request_id": rid, "answer": "APPROVE", "thread_id": w.t_alice}

    carol = _client(_CAROL, w.org_b).post("/agent/respond-input", json=body)
    assert carol.status_code == 403, carol.text
    assert tuple(_admin_row(w, w.org_a, rid)) == ("parked", None)

    alice = _client(_ALICE, w.org_a).post("/agent/respond-input", json=body)
    assert alice.status_code == 409, alice.text
    detail = alice.json()["detail"]
    assert detail["error"] == "run_restarted"
    assert detail["resumeMessage"] == f'You asked me: "{_QUESTION}"\n\nMy answer: APPROVE'
    assert tuple(_admin_row(w, w.org_a, rid)) == ("answered", "APPROVE")


def test_active_sessions_lists_a_parked_thread_for_its_member_only(
    as_app, flag_on, liveness,
):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole
    from gateway.routes.chat import list_active_sessions

    w = as_app
    rid = _ask(w.org_a, w.t_alice)
    pending_ask.move_ask(w.org_a, rid, to="parked", from_states=("open",))

    def _rows(email: str, org: str) -> list[dict]:
        user = UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)
        return asyncio.run(list_active_sessions(user=user))

    alice = {r["threadId"]: r for r in _rows(_ALICE, w.org_a)}
    assert alice[w.t_alice]["state"] == "needs_input"
    assert alice[w.t_alice]["askKind"] == "confirmation"
    assert alice[w.t_alice]["agentName"] == "orchestrator"
    assert w.t_alice not in {r["threadId"] for r in _rows(_CAROL, w.org_b)}
