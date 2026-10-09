"""An edit supersedes the last message: the database half, under FORCE RLS (R8).

``gateway/chat_supersede.py`` reads, decides and deletes in one transaction,
bound to the tenant (R5 ``tenant_session``). This suite runs the REAL helper
and the REAL ``POST /chat/sessions/{sid}/supersede`` handler against the H3
rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS role. It reuses
the fixtures of ``test_chat_write_under_rls.py``.

Mutations this suite catches (R7):

* the helper opens ``get_session()`` instead of ``tenant_session``: the
  bound read finds nothing and every case answers ``not_found``, and the
  unbound delete is refused;
* the author check goes: Bob deletes Alice's message;
* the last-only check goes: an earlier message and its replies vanish;
* the room check on the route goes: a member outside the room edits it;
* the delete loses its ``session_id`` predicate or the keep list: the new
  turn, or another session's row, goes with the old one.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5581/acb_tenant \\
        uv run pytest tests/unit/test_chat_supersede_rls.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

# Fixtures resolved by name (F401/F811 are expected; the imports are load-bearing).
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _BOB,
    _CAROL,
    _DB_GATE,
    _admin_one,
    _new_session,
    _user,
    app_engine,
    graph_as_app,
    members,
    promoted,
)

pytestmark = _DB_GATE

_T0 = 1_790_000_000_000


def _sid() -> str:
    return f"sup-{uuid.uuid4().hex[:12]}"


def _client(user):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import save_messages, supersede_message, upsert_session

    app = FastAPI()
    app.post("/chat/sessions")(upsert_session)
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.post("/chat/sessions/{session_id}/supersede")(supersede_message)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_run_to_stop(monkeypatch):
    """No Redis here, and no run on these threads. The run half has its own
    tests; this suite is about the rows."""
    import gateway.chat_supersede as sup

    async def _none(thread_id, actor):
        return False

    monkeypatch.setattr(sup, "settle_active_run", _none)


def _user_row(mid: str, ts: int, content: str) -> dict:
    return {"id": mid, "role": "user", "content": content, "timestamp": ts,
            "tool_events": [], "progress_lines": [], "custom_events": []}


def _seed(org: str) -> str:
    """Alice's thread: u1, a1, u2, a2 (a2 created three tasks)."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    sid = _sid()
    alice = _client(_user(_ALICE, org))
    _new_session(alice, sid)
    saved = alice.post(f"/chat/sessions/{sid}/messages", json=[
        _user_row("u1", _T0, "List the firmware tasks"),
        _user_row("u2", _T0 + 10, "Create three bootloader tasks"),
    ])
    assert saved.status_code == 200 and saved.json()["saved"] == 2, saved.text
    # Agent rows are the server's to create (S14), so they go in as the mint.
    _upsert_messages(sid, [
        MessageRecord(id="a1", role="assistant", content="Four open tasks.",
                      timestamp=_T0 + 1, author_kind="agent"),
        MessageRecord(id="a2", role="assistant", content="Created three tasks.",
                      timestamp=_T0 + 11, author_kind="agent", tool_events=[
                          {"id": "t1", "name": "list_projects", "status": "done", "result": "3"},
                          {"id": "t2", "name": "create_task", "status": "done",
                           "result": 'Created "Review bootloader" (#41)'},
                      ]),
    ], actor_email=_ALICE, agent_name="orchestrator", mint=True, organization_id=org)
    return sid


def _ids(promoted, sid: str) -> list[str]:  # noqa: F811
    with promoted.admin_engine.connect() as c:
        return [r.id for r in c.execute(text(
            "SELECT id FROM chat_message WHERE session_id = :s "
            "ORDER BY timestamp_ms, id"), {"s": sid})]


def test_the_author_supersedes_the_last_turn_and_its_reply(graph_as_app):
    from gateway.chat_supersede import compose_supersede_note, supersede_rows

    a = graph_as_app.org_a
    sid = _seed(a)
    # The browser saved the edited turn before the run started. It stays.
    alice = _client(_user(_ALICE, a))
    alice.post(f"/chat/sessions/{sid}/messages",
               json=[_user_row("u2-edit", _T0 + 20, "Create two bootloader tasks")])

    plan = supersede_rows(sid, "u2", actor=_ALICE, keep_ids=["u2-edit"],
                          organization_id=a)
    assert plan.removed_ids == ["u2", "a2"]
    assert plan.old_text == "Create three bootloader tasks"
    # The completed write comes from the STORED row, and the read does not.
    note = compose_supersede_note(plan)
    assert 'step: "create_task" (done) result: "Created \\"Review bootloader\\" (#41)"' in note
    assert "list_projects" not in note
    assert _ids(graph_as_app, sid) == ["u1", "a1", "u2-edit"]


def test_an_earlier_message_cannot_be_superseded(graph_as_app):
    from gateway.chat_supersede import SupersedeRefused, supersede_rows

    a = graph_as_app.org_a
    sid = _seed(a)
    with pytest.raises(SupersedeRefused) as err:
        supersede_rows(sid, "u1", actor=_ALICE, organization_id=a)
    assert err.value.code == "not_last"
    assert _ids(graph_as_app, sid) == ["u1", "a1", "u2", "a2"]


def test_another_member_of_the_org_cannot_supersede_it(graph_as_app):
    from gateway.chat_supersede import SupersedeRefused, supersede_rows

    a = graph_as_app.org_a
    sid = _seed(a)
    with pytest.raises(SupersedeRefused) as err:
        supersede_rows(sid, "u2", actor=_BOB, organization_id=a)
    assert err.value.code == "not_yours"
    assert _ids(graph_as_app, sid) == ["u1", "a1", "u2", "a2"]


def test_another_tenant_sees_no_row_and_deletes_nothing(graph_as_app):
    from gateway.chat_supersede import SupersedeRefused, supersede_rows

    sid = _seed(graph_as_app.org_a)
    with pytest.raises(SupersedeRefused) as err:
        supersede_rows(sid, "u2", actor=_ALICE, organization_id=graph_as_app.org_b)
    assert err.value.code == "not_found"
    assert _ids(graph_as_app, sid) == ["u1", "a1", "u2", "a2"]


def test_no_tenant_fails_closed(graph_as_app):
    from acb_graph.db import TenantUnbound
    from gateway.chat_supersede import supersede_rows

    sid = _seed(graph_as_app.org_a)
    with pytest.raises(TenantUnbound):
        supersede_rows(sid, "u2", actor=_ALICE, organization_id=None)
    assert _ids(graph_as_app, sid) == ["u1", "a1", "u2", "a2"]


def test_the_route_refuses_a_member_outside_the_room(graph_as_app):
    a = graph_as_app.org_a
    sid = _seed(a)
    bob = _client(_user(_BOB, a))
    refused = bob.post(f"/chat/sessions/{sid}/supersede", json={"superseded_id": "u2"})
    assert refused.status_code == 403, refused.text
    carol = _client(_user(_CAROL, graph_as_app.org_b))
    assert carol.post(f"/chat/sessions/{sid}/supersede",
                      json={"superseded_id": "u2"}).status_code in (403, 404)
    assert _ids(graph_as_app, sid) == ["u1", "a1", "u2", "a2"]


def test_the_route_supersedes_for_the_author(graph_as_app):
    a = graph_as_app.org_a
    sid = _seed(a)
    alice = _client(_user(_ALICE, a))
    ok = alice.post(f"/chat/sessions/{sid}/supersede",
                    json={"superseded_id": "u2", "keep_ids": []})
    assert ok.status_code == 200, ok.text
    assert ok.json()["removed"] == ["u2", "a2"]
    again = alice.post(f"/chat/sessions/{sid}/supersede",
                       json={"superseded_id": "u1"})
    # u1 is the last user turn now, and the same rule edits it.
    assert again.status_code == 200, again.text
    assert _ids(graph_as_app, sid) == []
    assert _admin_one(graph_as_app, "SELECT 1 FROM chat_session WHERE id = :s", s=sid)


def test_keep_ids_cannot_delete_another_members_turn_in_a_shared_room(graph_as_app):
    """🔴 Review of #795, the attack, end to end. Alice A1 → R1, then Bob
    B1 → RB in the same room. Alice edits A1 and names Bob's rows as her
    own new turn in ``keep_ids``. The stored rows prove they are Bob's, so
    the edit answers 409 and nothing is deleted."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    with graph_as_app.admin_engine.begin() as c:
        for who, role in ((_ALICE, "owner"), (_BOB, "member")):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :u, :r, :o) ON CONFLICT DO NOTHING"),
                {"s": sid, "u": who, "r": role, "o": a})
    assert alice.post(f"/chat/sessions/{sid}/messages",
                      json=[_user_row("A1", _T0, "Alice asks")]).json()["saved"] == 1
    _upsert_messages(sid, [MessageRecord(id="R1", role="assistant", content="to Alice",
                                         timestamp=_T0 + 1, author_kind="agent")],
                     actor_email=_ALICE, agent_name="orchestrator", mint=True, organization_id=a)
    bob = _client(_user(_BOB, a))
    assert bob.post(f"/chat/sessions/{sid}/messages",
                    json=[_user_row("B1", _T0 + 10, "Bob asks")]).json()["saved"] == 1
    _upsert_messages(sid, [MessageRecord(id="RB", role="assistant", content="to Bob",
                                         timestamp=_T0 + 11, author_kind="agent",
                                         tool_events=[{"id": "t", "name": "create_deal",
                                                       "status": "done", "result": "deal #9"}])],
                     actor_email=_BOB, agent_name="orchestrator", mint=True, organization_id=a)

    for keep in (["B1"], ["B1", "RB"], ["RB"]):
        refused = alice.post(f"/chat/sessions/{sid}/supersede",
                             json={"superseded_id": "A1", "keep_ids": keep})
        assert refused.status_code == 409, (keep, refused.text)
        assert refused.json()["detail"]["error"] == "not_last"
    assert _ids(graph_as_app, sid) == ["A1", "R1", "B1", "RB"]

    # Bob's own message IS the last one, and Bob may edit it.
    ok = bob.post(f"/chat/sessions/{sid}/supersede", json={"superseded_id": "B1"})
    assert ok.status_code == 200, ok.text
    assert _ids(graph_as_app, sid) == ["A1", "R1"]


def test_the_redelete_removes_a_late_fold(graph_as_app):
    """A cancelled run on another worker can fold its row after the edit.
    The new run deletes the superseded ids again at its own end."""
    from gateway.chat_supersede import delete_rows, supersede_rows
    from gateway.routes.chat import MessageRecord, _upsert_messages

    a = graph_as_app.org_a
    sid = _seed(a)
    plan = supersede_rows(sid, "u2", actor=_ALICE, organization_id=a)
    _upsert_messages(sid, [MessageRecord(
        id="a2", role="assistant", content="late fold", timestamp=_T0 + 11,
        author_kind="agent")], actor_email=_ALICE, agent_name="orchestrator",
        author_from_run=True, organization_id=a)
    assert "a2" in _ids(graph_as_app, sid)
    assert delete_rows(sid, plan.removed_ids, organization_id=a) == 1
    assert _ids(graph_as_app, sid) == ["u1", "a1"]
