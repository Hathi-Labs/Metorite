"""D99 — a member archives a chat (migration 253, ``navigation_shell.md`` §7.1).

The real ``PATCH /chat/sessions/{id}`` and ``GET /chat/sessions`` handlers run
against the H3 rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS
role, as ``test_chat_write_under_rls.py`` does. A hermetic fake agrees with
any SQL it is handed, so this runs the SQL (R8).

What it holds:

* an archive sets ``archived_at`` and the list returns it as ``archivedAt``;
* a second archive keeps the first time, and a restore clears it;
* a rename and a preview in the same PATCH leave the archive alone;
* in a room, a member who is not the owner cannot archive it: the answer is
  the 404 of a chat they cannot see, and nothing is written;
* a member of another org cannot archive the chat.

Mutations this suite catches (R7):

* ``COALESCE(s.archived_at, now())`` turned into ``now()``: the time moves;
* the owner predicate dropped: the room member archives the room;
* ``archivedAt`` left out of the list: the assistant never shows "Archived".

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5550/acb_tenant \\
        uv run pytest tests/unit/test_chat_session_archive_r8.py -v -rs
"""
from __future__ import annotations

import time
import uuid

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Resolved by name as fixtures. The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ALICE = "alice@d99-a.test"
_BOB = "bob@d99-a.test"
_CAROL = "carol@d99-b.test"


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
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
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


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _client(user):
    """The real handlers behind FastAPI, so each body is parsed as a request is."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import list_sessions, patch_session, upsert_session

    app = FastAPI()
    app.post("/chat/sessions")(upsert_session)
    app.get("/chat/sessions")(list_sessions)
    app.patch("/chat/sessions/{session_id}")(patch_session)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _new_chat(client) -> str:
    sid = f"d99-{uuid.uuid4().hex[:12]}"
    made = client.post("/chat/sessions", json={
        "id": sid, "agent_name": "orchestrator", "title": "D99",
        "last_preview": None, "message_count": 2,
    })
    assert made.status_code == 200, made.text
    return sid


def _archived_at(promoted, sid: str):  # noqa: F811
    with promoted.admin_engine.connect() as c:
        return c.execute(
            text("SELECT archived_at FROM chat_session WHERE id = :id"), {"id": sid},
        ).scalar()


def _row(client, sid: str) -> dict:
    rows = client.get("/chat/sessions").json()
    return next(r for r in rows if r["id"] == sid)


def test_archive_sets_the_time_and_the_list_returns_it(graph_as_app):
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    sid = _new_chat(alice)
    assert _row(alice, sid)["archivedAt"] is None

    res = alice.patch(f"/chat/sessions/{sid}", json={"archived": True})
    assert res.status_code == 200, res.text
    first = _archived_at(graph_as_app, sid)
    assert first is not None
    assert _row(alice, sid)["archivedAt"] == first.isoformat()


def test_a_second_archive_keeps_the_first_time_and_a_restore_clears_it(graph_as_app):
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    sid = _new_chat(alice)
    alice.patch(f"/chat/sessions/{sid}", json={"archived": True})
    first = _archived_at(graph_as_app, sid)
    time.sleep(0.05)
    alice.patch(f"/chat/sessions/{sid}", json={"archived": True})
    assert _archived_at(graph_as_app, sid) == first

    res = alice.patch(f"/chat/sessions/{sid}", json={"archived": False})
    assert res.status_code == 200, res.text
    assert _archived_at(graph_as_app, sid) is None


def test_a_rename_leaves_the_archive_alone(graph_as_app):
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    sid = _new_chat(alice)
    alice.patch(f"/chat/sessions/{sid}", json={"archived": True})
    first = _archived_at(graph_as_app, sid)
    res = alice.patch(f"/chat/sessions/{sid}", json={
        "title": "Renamed", "last_preview": "p", "message_count": 3,
    })
    assert res.status_code == 200, res.text
    assert _archived_at(graph_as_app, sid) == first
    assert _row(alice, sid)["title"] == "Renamed"


def test_a_room_member_who_is_not_the_owner_cannot_archive_it(graph_as_app):
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    sid = _new_chat(alice)
    # Bob joins Alice's room as a member. Alice is its owner.
    with graph_as_app.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, organization_id) "
            "VALUES (:s, :a, 'owner', :o), (:s, :b, 'member', :o) ON CONFLICT DO NOTHING"),
            {"s": sid, "a": _ALICE, "b": _BOB, "o": graph_as_app.org_a})
    bob = _client(_user(_BOB, graph_as_app.org_a))
    # Bob can see the room, so a rename is his to make.
    assert bob.patch(f"/chat/sessions/{sid}", json={"title": "Ours"}).status_code == 200
    # An archive would hide it from Alice, so it is the owner's alone.
    res = bob.patch(f"/chat/sessions/{sid}", json={"archived": True})
    assert res.status_code == 404, res.text
    assert _archived_at(graph_as_app, sid) is None
    # The owner can.
    assert alice.patch(f"/chat/sessions/{sid}", json={"archived": True}).status_code == 200
    assert _archived_at(graph_as_app, sid) is not None


def test_a_member_of_another_org_cannot_archive_the_chat(graph_as_app):
    alice = _client(_user(_ALICE, graph_as_app.org_a))
    sid = _new_chat(alice)
    carol = _client(_user(_CAROL, graph_as_app.org_b))
    res = carol.patch(f"/chat/sessions/{sid}", json={"archived": True})
    assert res.status_code == 404, res.text
    assert _archived_at(graph_as_app, sid) is None
