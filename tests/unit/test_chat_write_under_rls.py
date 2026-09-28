"""WS-27bm S15 — chat is saved under FORCE RLS (``projects_ai_chat.md`` §21).

Production runs the chat tables with FORCE ROW LEVEL SECURITY and a
non-privileged role. Every chat helper opened the unbound
``acb_graph.get_session()``, so every write was refused and ``chat_message``
never held a row. The ladder-only test database has no RLS, so no test saw it.

This suite runs the REAL helpers and the REAL ``save_messages`` handler
against the H3 rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS
role. It reuses the ``promoted`` and ``app_engine`` fixtures, as
``test_chat_creator_owner_backfill.py`` does. It does not patch
``get_session``. It points ``acb_graph``'s session factory at the app role.

The factory uses ``NullPool``, so each session opens a fresh backend. A fresh
backend has no ``app.tenant_id`` at all. Then an unbound read sees no row, and
an unbound write breaks NOT NULL. That makes each mutation below fail the same
way every run, whatever a pooled connection happened to carry.

Mutations this suite catches (R7):

* a chat helper that opens ``get_session()`` again: its write is refused;
* ``rooms._load_room`` unbound: a member of org A who is not in room X reads
  no row, gets ``_unsaved_thread()``, and so gets owner access. The security
  case goes red;
* ``run_trace`` unbound: no ``agent_run`` row.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5550/acb_tenant \\
        uv run pytest tests/unit/test_chat_write_under_rls.py -v -rs
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ALICE = "alice@s15-a.test"
_BOB = "bob@s15-a.test"
_CAROL = "carol@s15-b.test"


@pytest.fixture(scope="module")
def members(promoted):  # noqa: F811
    """Alice and Bob in org A, Carol in org B. Seeded as the superuser."""
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, promoted.org_a), (_BOB, promoted.org_a),
                           (_CAROL, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, "
                "organization_id) VALUES (:e, :e, 'employee', 'active', :o) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
    return promoted


@pytest.fixture
def graph_as_app(members, app_engine, monkeypatch):  # noqa: F811
    """``acb_graph`` sessions open as the app role, one fresh backend each."""
    eng = create_engine(members.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield members
    finally:
        eng.dispose()


def _sid() -> str:
    return f"s15-{uuid.uuid4().hex[:12]}"


def _admin_one(promoted, sql: str, **params):  # noqa: F811
    with promoted.admin_engine.connect() as c:
        return c.execute(text(sql), params).first()


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _client(user):
    """The real chat handlers behind FastAPI, so the body is parsed as a
    request is. The feature gate is a router dependency and is left out."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import get_messages, save_messages, upsert_session

    app = FastAPI()
    app.post("/chat/sessions")(upsert_session)
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.get("/chat/sessions/{session_id}/messages")(get_messages)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _new_session(client, sid: str) -> None:
    """``sessions.ts`` ``_syncSessionToDb``: the row the browser makes first."""
    made = client.post("/chat/sessions", json={
        "id": sid, "agent_name": "orchestrator", "title": "S15",
        "last_preview": None, "message_count": 0,
    })
    assert made.status_code == 200, made.text


def _browser_rows(mid: str) -> list[dict]:
    """The body ``lib/sessions.ts`` ``saveMessages`` sends for one user turn."""
    return [{
        "id": mid, "role": "user", "content": "Is chat saved now?",
        "timestamp": 1_790_000_000_000, "tool_events": [],
        "progress_lines": [], "agent_state": None, "custom_events": [],
    }]


# ── Why: an unbound session is refused. The shape of the production bug. ───

def test_an_unbound_chat_write_is_refused(graph_as_app):
    """A fresh backend has no ``app.tenant_id``. The column default is then
    NULL and NOT NULL refuses the row. This is the write every chat helper
    made before S15."""
    with pytest.raises(DBAPIError), graph_db.get_session() as s:
        s.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name) "
            "VALUES (:id, :u, 'orchestrator')"), {"id": _sid(), "u": _ALICE})


def test_a_pooled_backend_fails_with_the_production_error(graph_as_app):
    """A backend that once ran ``set_config(..., true)`` keeps ``''``. The
    default then casts ``''`` to uuid. This is the error the box logged."""
    eng = create_engine(graph_as_app.app_url, future=True, pool_size=1,
                        max_overflow=0)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"),
                      {"t": graph_as_app.org_a})
        with pytest.raises(DBAPIError) as err, eng.connect() as c, c.begin():
            c.execute(text(
                "INSERT INTO chat_session (id, user_id, agent_name) "
                "VALUES (:id, :u, 'orchestrator')"), {"id": _sid(), "u": _ALICE})
        assert 'invalid input syntax for type uuid: ""' in str(err.value.orig)
    finally:
        eng.dispose()


# ── The helpers, bound ──────────────────────────────────────────────────────

def test_a_new_session_lands_stamped_with_the_members_org(graph_as_app):
    from gateway.routes.chat import SessionUpsertRequest, _ensure_session, _upsert_session

    org = graph_as_app.org_a
    browser, run = _sid(), _sid()
    _upsert_session(_ALICE, SessionUpsertRequest(id=browser, title="t"),
                    organization_id=org)
    _ensure_session(run, _ALICE, "orchestrator", organization_id=org)
    for sid in (browser, run):
        row = _admin_one(graph_as_app,
                         "SELECT organization_id::text AS o, user_id FROM chat_session "
                         "WHERE id = :s", s=sid)
        assert row is not None, sid
        assert (row.o, row.user_id) == (org, _ALICE)
    owner = _admin_one(graph_as_app,
                       "SELECT role, organization_id::text AS o FROM "
                       "chat_session_participant WHERE session_id = :s", s=run)
    assert (owner.role, owner.o) == ("owner", org)


def test_no_tenant_fails_closed_and_writes_nothing(graph_as_app):
    from acb_graph.db import TenantUnbound
    from gateway.routes.chat import _ensure_session

    sid = _sid()
    with pytest.raises(TenantUnbound):
        _ensure_session(sid, _ALICE, "orchestrator", organization_id=None)
    assert _admin_one(graph_as_app,
                      "SELECT 1 FROM chat_session WHERE id = :s", s=sid) is None


def test_the_browser_save_lands_and_reads_back(graph_as_app):
    """The real handlers, with the exact body ``sessions.ts`` sends."""
    org = graph_as_app.org_a
    client = _client(_user(_ALICE, org))
    sid, mid = _sid(), f"u-{uuid.uuid4().hex[:8]}"

    made = client.post("/chat/sessions", json={
        "id": sid, "agent_name": "orchestrator", "title": "S15",
        "message_count": 0,
    })
    assert made.status_code == 200, made.text

    saved = client.post(f"/chat/sessions/{sid}/messages",
                        json=_browser_rows(mid))
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] == 1

    got = client.get(f"/chat/sessions/{sid}/messages")
    assert got.status_code == 200
    rows = got.json()
    assert [(r["id"], r["content"]) for r in rows] == [(mid, "Is chat saved now?")]
    stamped = _admin_one(graph_as_app,
                         "SELECT organization_id::text AS o FROM chat_message "
                         "WHERE id = :m", m=mid)
    assert stamped.o == org


def test_a_text_plain_body_gets_422_and_json_gets_200(graph_as_app):
    """Defect 1, on the gateway side. The BFF sent the ``sessions.ts`` body
    with no content type, and Node's fetch labelled it text/plain. FastAPI
    does not parse that as JSON, so a list body answers 422."""
    client = _client(_user(_ALICE, graph_as_app.org_a))
    sid, mid = _sid(), f"u-{uuid.uuid4().hex[:8]}"
    _new_session(client, sid)
    body = json.dumps(_browser_rows(mid))

    plain = client.post(f"/chat/sessions/{sid}/messages", content=body,
                        headers={"Content-Type": "text/plain;charset=UTF-8"})
    assert plain.status_code == 422, plain.text

    as_json = client.post(f"/chat/sessions/{sid}/messages", content=body,
                          headers={"Content-Type": "application/json"})
    assert as_json.status_code == 200, as_json.text
    assert as_json.json()["saved"] == 1


def test_org_b_cannot_see_org_a_rows(graph_as_app):
    from acb_graph import tenant_session
    from gateway.routes.chat import _get_messages, _get_sessions

    a, b = graph_as_app.org_a, graph_as_app.org_b
    sid, mid = _sid(), f"u-{uuid.uuid4().hex[:8]}"
    client = _client(_user(_ALICE, a))
    _new_session(client, sid)
    assert client.post(f"/chat/sessions/{sid}/messages",
                       json=_browser_rows(mid)).json()["saved"] == 1

    with tenant_session(b) as s:
        for table in ("chat_session", "chat_message"):
            col = "id" if table == "chat_session" else "session_id"
            n = s.execute(text(f"SELECT count(*) FROM {table} "
                               f"WHERE {col} = :s"), {"s": sid}).scalar()
            assert n == 0, table
    assert sid not in [r["id"] for r in _get_sessions(_CAROL, organization_id=b)]
    assert _get_messages(sid, _CAROL, organization_id=b) == []
    # Alice, in her own org, still sees it.
    assert [r["id"] for r in _get_messages(sid, _ALICE, organization_id=a)] == [mid]


def test_a_member_outside_the_room_is_not_its_owner(graph_as_app):
    """🔴 The security case. Alice owns private room X. Bob is in org A and is
    not a participant. Bound, ``_load_room`` sees the row and Bob gets no
    role. Unbound, it sees nothing and returns ``_unsaved_thread()``, which
    is owner access, so any member could write into any room in the org."""
    from gateway.rooms import resolve_room_access

    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    assert alice.post(f"/chat/sessions/{sid}/messages",
                      json=_browser_rows("u-alice")).json()["saved"] == 1

    access = resolve_room_access(sid, _BOB, organization_id=a)
    assert access.unknown_session is False
    assert access.role is None
    assert (access.can_read, access.can_send) == (False, False)

    bob = _client(_user(_BOB, a))
    refused = bob.post(f"/chat/sessions/{sid}/messages",
                       json=_browser_rows("u-bob"))
    assert refused.status_code == 403, refused.text
    assert bob.get(f"/chat/sessions/{sid}/messages").json() == []
    assert _admin_one(graph_as_app, "SELECT 1 FROM chat_message WHERE id = 'u-bob'") is None

    # Alice herself is the owner, through the bound read.
    mine = resolve_room_access(sid, _ALICE, organization_id=a)
    assert (mine.role, mine.can_send) == ("owner", True)


def test_no_tenant_denies_room_access(graph_as_app):
    """With no tenant, the lookup raises and the answer is a refusal, never
    the owner answer of an unsaved thread."""
    from gateway.rooms import resolve_room_access

    access = resolve_room_access(_sid(), _ALICE, organization_id=None)
    assert access.resolve_failed is True
    assert (access.role, access.can_send) == (None, False)


def test_the_mint_lands(graph_as_app):
    from gateway.routes.agent import _mint_run_row

    a = graph_as_app.org_a
    sid, mid = _sid(), f"assistant-{uuid.uuid4().hex[:8]}"
    _mint_run_row(sid, mid, member=_ALICE, agent_name="orchestrator",
                  organization_id=a)
    row = _admin_one(graph_as_app,
                     "SELECT author_kind, run_member_email, "
                     "organization_id::text AS o FROM chat_message WHERE id = :m",
                     m=mid)
    assert row is not None, "the mint wrote no row"
    assert (row.author_kind, row.run_member_email, row.o) == ("agent", _ALICE, a)


def test_the_fold_lands_seals_and_records_the_run(graph_as_app, monkeypatch):
    """The fold writes the agent row and seals it. From the same replay it
    records the ``agent_run`` trace row. Redis is the only stub."""
    from gateway import chat_fold
    from orchestrator import stream_relay

    a = graph_as_app.org_a
    sid, mid = _sid(), f"assistant-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    events = [
        {"type": "RUN_STARTED", "runId": run_id},
        {"type": "TEXT_MESSAGE_START", "messageId": mid},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": "Saved."},
        {"type": "TEXT_MESSAGE_END", "messageId": mid},
        {"type": "RUN_FINISHED", "runId": run_id},
    ]

    async def _replay(*_a, **_k):
        return events

    async def _solo(*_a, **_k):
        return None

    monkeypatch.setattr(stream_relay, "replay_events", _replay)
    monkeypatch.setattr(chat_fold, "_run_authority", _solo)

    out = asyncio.run(chat_fold.persist_final_assistant_message(
        sid, mid, user_id=_ALICE, agent_name="orchestrator",
        run_id=run_id, organization_id=a,
    ))
    assert out is not None and "Saved." in out["content"]
    row = _admin_one(graph_as_app,
                     "SELECT content, run_final_at, organization_id::text AS o "
                     "FROM chat_message WHERE id = :m", m=mid)
    assert row is not None, "the fold wrote no row"
    assert "Saved." in row.content
    assert row.run_final_at is not None, "the fold did not seal the row"
    assert row.o == a
    trace = _admin_one(graph_as_app,
                       "SELECT status, organization_id::text AS o FROM agent_run "
                       "WHERE run_id = :r", r=run_id)
    assert trace is not None, "the run trace wrote no agent_run row"
    assert trace.o == a


def test_a_run_trace_row_lands(graph_as_app):
    from gateway.run_trace import record_run_trace

    a = graph_as_app.org_a
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    asyncio.run(record_run_trace(
        run_id=run_id, thread_id=_sid(), agent_name="orchestrator",
        user_id=_ALICE, model=None,
        events=[{"type": "RUN_STARTED"}, {"type": "RUN_FINISHED"}],
        folded=None, organization_id=a,
    ))
    row = _admin_one(graph_as_app,
                     "SELECT organization_id::text AS o FROM agent_run "
                     "WHERE run_id = :r", r=run_id)
    assert row is not None and row.o == a


def test_a_blob_put_lands_and_reads_back(graph_as_app):
    """The agent workspace store under the same bind. The tenant comes from
    the caller's frame when the call names none."""
    from acb_common.db import bind_tenant
    from acb_memory import blob_store

    a = graph_as_app.org_a
    agent = f"s15-agent-{uuid.uuid4().hex[:6]}"

    async def _go():
        bind_tenant(a)
        meta = await blob_store.put_file(agent, "outputs/s15.md", b"# saved")
        data = await blob_store.get_file(agent, "outputs/s15.md")
        return meta, data

    meta, data = asyncio.run(_go())
    assert meta is not None and data == b"# saved"
    row = _admin_one(graph_as_app,
                     "SELECT organization_id::text AS o FROM agent_blob "
                     "WHERE agent_name = :n", n=agent)
    assert row is not None and row.o == a
