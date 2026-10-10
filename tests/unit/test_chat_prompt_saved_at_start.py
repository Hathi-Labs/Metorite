"""WS-51 S4: the server saves the member's turn when the run starts (R8).

``chat_run_continuity.md`` §4 S4. Before S4 only the browser saved the
member's turn (``lib/sessions.ts``). A tab that closed before that save
dropped the turn, and the run went on without it. Now ``/agent/run/stream``
saves the turn under the browser's own id, before the run starts, through
the one upsert seam (``_upsert_messages``). The browser's save then updates
that row and adds no second one.

This suite drives the REAL ``run_agent_stream_endpoint`` and the REAL chat
handlers against the H3 rehearsal's phase-4 catalog, as its NOSUPERUSER
NOBYPASSRLS role. It reuses the fixtures of ``test_chat_write_under_rls.py``.
Only the world outside the database is faked: the executor, the Redis relay,
the steer router and memory. Each test reads the turn back from Postgres.

Mutations this suite catches (R7):

* the save at run start removed: the tab-closed case finds no row;
* a server-minted id in place of the browser's id: the browser save adds a
  second row;
* the author taken from the payload, or no author at all: the author case
  goes red, and a member can overwrite another member's turn;
* the ``can_send`` check removed from ``prompt_to_save``: the unit case goes
  red;
* the history filter removed: the model reads the current turn twice;
* the save moved in front of the supersede: no failure, because the keep
  list protects the new turn. That order is a choice, not a fence.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_chat_prompt_saved_at_start.py -v -rs
"""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

# Fixtures resolved by name (F401/F811 are expected; the imports are load-bearing).
from tests.unit.test_chat_write_under_rls import (  # noqa: F401
    _ALICE,
    _BOB,
    _CAROL,
    _DB_GATE,
    _new_session,
    _user,
    app_engine,
    graph_as_app,
    members,
    promoted,
)

_T0 = 1_790_000_000_000


def _sid() -> str:
    return f"s4-{uuid.uuid4().hex[:12]}"


# ── The world outside the database ──────────────────────────────────────────

class _World:
    """What the faked executor saw, and how the steer router answers."""

    def __init__(self) -> None:
        self.route = "ENGAGE"
        self.payloads: list[dict[str, Any]] = []


@pytest.fixture
def world(monkeypatch):
    """Fake everything the route reaches that is not Postgres.

    The relay yields RUN_STARTED and then ends, and it never calls the fold.
    So a row that exists after the first event was written at run start.
    """
    import acb_memory
    import orchestrator.executor as executor
    import orchestrator.stream_relay as relay
    from fastapi import status
    from fastapi.responses import JSONResponse
    from gateway import chat_supersede
    from gateway.routes import agent
    from orchestrator.steer import Route, TurnDecision

    w = _World()

    async def _noop(*_a, **_k):
        return None

    async def _route(thread_id, actor, text_):
        return TurnDecision(getattr(Route, w.route), "test")

    async def _apply(decision, req, agent_name, actor, room):
        if decision.route is Route.ENGAGE:
            return None
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"steered": True, "threadId": req.thread_id},
        )

    async def _no_redis():
        raise RuntimeError("no Redis in this suite")

    async def _no_memory(**_k):
        return ""

    async def _gen():
        yield "unused"

    def _run_agent_stream(agent_name, payload, **_k):
        w.payloads.append(payload)
        return _gen()

    async def _run_detached(thread_id, gen, **_k):
        yield {"type": "RUN_STARTED", "threadId": thread_id}
        yield {"type": "RUN_FINISHED", "threadId": thread_id}

    async def _no_run(thread_id, actor):
        return False

    monkeypatch.setattr(agent, "assert_can_run_agent_in_session", _noop)
    monkeypatch.setattr(agent, "_prepare_if_new_thread", _noop)
    monkeypatch.setattr(agent, "_refuse_if_another_run_is_active", _noop)
    monkeypatch.setattr(agent, "_route_incoming_turn", _route)
    monkeypatch.setattr(agent, "_apply_turn_decision", _apply)
    monkeypatch.setattr(agent, "publish_room_event", _noop)
    monkeypatch.setattr(
        agent, "_address_agent", lambda req, room, org=None: "orchestrator",
    )
    monkeypatch.setattr(relay, "_get_client", _no_redis)
    monkeypatch.setattr(relay, "run_detached", _run_detached)
    monkeypatch.setattr(executor, "run_agent_stream", _run_agent_stream)
    monkeypatch.setattr(acb_memory, "get_session_memory", _no_memory)
    monkeypatch.setattr(acb_memory, "add_episode", _noop)
    monkeypatch.setattr(chat_supersede, "settle_active_run", _no_run)
    return w


def _client(user):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.agent import run_agent_stream_endpoint
    from gateway.routes.chat import get_messages, save_messages, upsert_session

    app = FastAPI()
    app.post("/agent/run/stream")(run_agent_stream_endpoint)
    app.post("/chat/sessions")(upsert_session)
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.get("/chat/sessions/{session_id}/messages")(get_messages)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _run_body(sid: str, uid: str, words: str, **payload: Any) -> dict[str, Any]:
    """The body ``app/api/agent/chat/route.ts`` sends for one turn."""
    return {
        "agent": "orchestrator",
        "thread_id": sid,
        "assistant_message_id": f"a-{uid}",
        "payload": {
            "mode": "chat", "message": words, "messages": [],
            "user_message_id": uid, "user_message_ts": _T0, **payload,
        },
    }


def _send_and_close(client, body: dict[str, Any]) -> tuple[int, str]:
    """Send a turn, read the first event, then close: the tab goes away."""
    with client.stream("POST", "/agent/run/stream", json=body) as r:
        if r.status_code != 200:
            r.read()
            return r.status_code, r.text
        first = next(r.iter_lines())
    return 200, first


def _browser_row(uid: str, words: str, ts: int = _T0, **extra: Any) -> dict:
    """The body ``lib/sessions.ts`` ``saveMessages`` sends for one turn."""
    return {"id": uid, "role": "user", "content": words, "timestamp": ts,
            "tool_events": [], "progress_lines": [], "agent_state": None,
            "custom_events": [], **extra}


def _rows(promoted, sid: str):  # noqa: F811
    """Every row of a thread, as the superuser sees it (RLS bypassed)."""
    with promoted.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT id, role, content, timestamp_ms, author_email, author_kind, "
            "custom_events, organization_id::text AS org FROM chat_message "
            "WHERE session_id = :s ORDER BY timestamp_ms, id"), {"s": sid}))


def _user_rows(promoted, sid: str):  # noqa: F811
    return [r for r in _rows(promoted, sid) if r.role == "user"]


# ── The fence of the spec: a tab closed after the first event ───────────────

@_DB_GATE
def test_a_tab_closed_after_the_first_event_keeps_the_prompt(graph_as_app, world):
    """No browser save at all, and no session row from the browser. The run
    route makes the chat row and saves the turn before the first event."""
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    code, first = _send_and_close(alice, _run_body(sid, "u1", "Plan the release"))
    assert code == 200, first
    assert json.loads(first.removeprefix("data: "))["type"] == "RUN_STARTED"

    rows = _user_rows(graph_as_app, sid)
    assert [(r.id, r.content, r.timestamp_ms) for r in rows] == [
        ("u1", "Plan the release", _T0)]
    assert (rows[0].author_email, rows[0].author_kind) == (_ALICE, "human")
    assert rows[0].org == a
    with graph_as_app.admin_engine.connect() as c:
        sess = c.execute(text(
            "SELECT user_id, organization_id::text AS org FROM chat_session "
            "WHERE id = :s"), {"s": sid}).one()
    assert (sess.user_id, sess.org) == (_ALICE, a)

    # The next open: the member's own read shows the turn.
    shown = alice.get(f"/chat/sessions/{sid}/messages").json()
    assert [(m["id"], m["content"]) for m in shown] == [("u1", "Plan the release")]
    # The executor never saw the routing keys.
    assert "user_message_id" not in world.payloads[0]
    assert "user_message_ts" not in world.payloads[0]


# ── No double rows ──────────────────────────────────────────────────────────

@_DB_GATE
def test_the_browser_save_after_the_server_adds_no_row(graph_as_app, world):
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    assert _send_and_close(alice, _run_body(sid, "u1", "Plan the release"))[0] == 200

    saved = alice.post(f"/chat/sessions/{sid}/messages",
                       json=[_browser_row("u1", "Plan the release")])
    assert saved.status_code == 200 and saved.json()["unchanged"] == [], saved.text
    assert [r.id for r in _user_rows(graph_as_app, sid)] == ["u1"]


@_DB_GATE
def test_the_browser_save_before_the_server_adds_no_row(graph_as_app, world):
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    saved = alice.post(f"/chat/sessions/{sid}/messages",
                       json=[_browser_row("u1", "Plan the release")])
    assert saved.status_code == 200, saved.text

    assert _send_and_close(alice, _run_body(sid, "u1", "Plan the release"))[0] == 200
    rows = _user_rows(graph_as_app, sid)
    assert [(r.id, r.content, r.timestamp_ms) for r in rows] == [
        ("u1", "Plan the release", _T0)]


@_DB_GATE
def test_a_held_send_that_goes_out_twice_is_one_row(graph_as_app, world):
    """#797: a send that reached the server and lost its answer is held, and
    goes out again with the same bubble and id. One row, not two."""
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    body = _run_body(sid, "u-held", "Continue the draft")
    assert _send_and_close(alice, body)[0] == 200
    world.route = "STEER"   # the first run still runs, so the second steers
    assert _send_and_close(alice, _run_body(sid, "u-held", "Continue the draft"))[0] == 202
    assert [r.id for r in _user_rows(graph_as_app, sid)] == ["u-held"]


# ── The tenant and the author come from the session ─────────────────────────

@_DB_GATE
def test_the_tenant_and_the_author_come_from_the_session(graph_as_app, world):
    """The payload names another org and another author. Neither reaches
    the row. A turn of another member is not Alice's to overwrite by id."""
    a, b = graph_as_app.org_a, graph_as_app.org_b
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    bob = _client(_user(_BOB, a))
    _new_session(alice, sid)
    # Bob joins as a member, and his own turn is in the thread.
    with graph_as_app.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :e, 'member', :o)"),
            {"s": sid, "e": _BOB, "o": a})
    assert bob.post(f"/chat/sessions/{sid}/messages",
                    json=[_browser_row("u-bob", "Bob asks")]).status_code == 200

    body = _run_body(sid, "u1", "Alice asks", organization_id=b,
                     author_email="mallory@else.test", author_kind="agent")
    assert _send_and_close(alice, body)[0] == 200
    # Alice names Bob's row: the seam declines it, and Bob's words stay.
    assert _send_and_close(alice, _run_body(sid, "u-bob", "Forged"))[0] == 200

    rows = {r.id: r for r in _user_rows(graph_as_app, sid)}
    assert set(rows) == {"u1", "u-bob"}
    assert (rows["u1"].author_email, rows["u1"].author_kind, rows["u1"].org) == (
        _ALICE, "human", a)
    assert (rows["u-bob"].content, rows["u-bob"].author_email) == ("Bob asks", _BOB)


# ── A member who may not send writes nothing ────────────────────────────────

@_DB_GATE
def test_a_viewer_and_a_member_of_another_org_write_nothing(graph_as_app, world):
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    with graph_as_app.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_participant (session_id, subject, role, "
            "organization_id) VALUES (:s, :e, 'viewer', :o)"),
            {"s": sid, "e": _BOB, "o": a})

    bob = _client(_user(_BOB, a))
    code, _ = _send_and_close(bob, _run_body(sid, "u-viewer", "Viewer words"))
    assert code == 403
    carol = _client(_user(_CAROL, graph_as_app.org_b))
    code, _ = _send_and_close(carol, _run_body(sid, "u-carol", "Other org"))
    assert code in (403, 404)

    assert _rows(graph_as_app, sid) == []
    assert world.payloads == []


def test_prompt_to_save_needs_an_id_a_room_and_the_right_to_send():
    """The pure gate, so a later caller cannot skip the room check."""
    from gateway.routes.agent import prompt_to_save

    room = SimpleNamespace(can_send=True, resolve_failed=False)
    ok = dict(thread_id="t1", message_id="u1", text="Hi", timestamp=_T0,
              actor=_ALICE, room=room)
    got = prompt_to_save(**ok)
    assert got is not None
    assert (got.message_id, got.content, got.timestamp_ms, got.custom_events) == (
        "u1", "Hi", _T0, [])

    assert prompt_to_save(**{**ok, "room": SimpleNamespace(
        can_send=False, resolve_failed=False)}) is None
    assert prompt_to_save(**{**ok, "room": SimpleNamespace(
        can_send=False, resolve_failed=True)}) is None
    assert prompt_to_save(**{**ok, "room": None}) is None
    assert prompt_to_save(**{**ok, "message_id": ""}) is None   # an old bundle
    assert prompt_to_save(**{**ok, "message_id": "x" * 129}) is None
    assert prompt_to_save(**{**ok, "thread_id": None}) is None
    assert prompt_to_save(**{**ok, "actor": ""}) is None
    assert prompt_to_save(**{**ok, "text": "  "}) is None
    # A time stamp that is not a sane integer falls back to the server clock.
    for bad in (None, "1790000000000", True, -5, 2**60):
        assert prompt_to_save(**{**ok, "timestamp": bad}).timestamp_ms > _T0 - 10**12
    edit = prompt_to_save(**{**ok, "supersedes": "u0"})
    assert edit.custom_events == [{"name": "edited", "value": {"supersedes": "u0"}}]


# ── An edit replaces the server-saved prompt (#795) ─────────────────────────

@_DB_GATE
def test_an_edit_replaces_the_server_saved_prompt_with_no_orphan(graph_as_app, world):
    """The first turn was saved only by the server (the tab closed). The
    edit names it. The old turn and its reply go, and the edited turn is
    saved with its marker, so it draws "Edited" after a reload."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    assert _send_and_close(alice, _run_body(sid, "u1", "Make three tasks"))[0] == 200
    # The reply that the run's fold would write.
    _upsert_messages(sid, [MessageRecord(
        id="a-u1", role="assistant", content="Made three tasks.",
        timestamp=_T0 + 5, author_kind="agent",
    )], actor_email=_ALICE, agent_name="orchestrator", author_from_run=True,
        organization_id=a)

    edit = _run_body(sid, "u2", "Make two tasks", supersedes="u1")
    edit["payload"]["user_message_ts"] = _T0 + 10
    assert _send_and_close(alice, edit)[0] == 200

    rows = _rows(graph_as_app, sid)
    ids = [r.id for r in rows]
    assert "u1" not in ids and "a-u1" not in ids
    assert [r.id for r in rows if r.role == "user"] == ["u2"]
    (u2,) = [r for r in rows if r.id == "u2"]
    assert u2.content == "Make two tasks" and u2.author_email == _ALICE
    assert u2.custom_events == [{"name": "edited", "value": {"supersedes": "u1"}}]
    # The model reads the edit with the platform's note, and the row holds
    # only the member's words.
    assert world.payloads[-1]["message"].endswith("Make two tasks")
    assert world.payloads[-1]["message"] != "Make two tasks"


# ── A steer is saved with its actor ─────────────────────────────────────────

@_DB_GATE
def test_a_steer_saves_the_turn_with_its_actor(graph_as_app, world):
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    world.route = "STEER"
    code, _ = _send_and_close(alice, _run_body(sid, "u-steer", "Also add a due date"))
    assert code == 202
    rows = _user_rows(graph_as_app, sid)
    assert [(r.id, r.content, r.author_email) for r in rows] == [
        ("u-steer", "Also add a due date", _ALICE)]
    assert world.payloads == []   # a steer starts no run


@_DB_GATE
def test_a_stop_saves_nothing(graph_as_app, world):
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    world.route = "ABORT"
    assert _send_and_close(alice, _run_body(sid, "u-stop", "stop"))[0] == 202
    assert _rows(graph_as_app, sid) == []


# ── The model reads the current turn once ───────────────────────────────────

@_DB_GATE
def test_the_history_from_the_store_leaves_out_the_current_turn(graph_as_app, world):
    """A first turn sends no history, so the executor rebuilds it from the
    store. The turn saved at run start must not come back as history."""
    a = graph_as_app.org_a
    sid = _sid()
    alice = _client(_user(_ALICE, a))
    _new_session(alice, sid)
    alice.post(f"/chat/sessions/{sid}/messages",
               json=[_browser_row("u0", "An earlier turn", ts=_T0 - 100)])
    assert _send_and_close(alice, _run_body(sid, "u1", "Plan the release"))[0] == 200

    history = world.payloads[0]["_history_loader"]()
    assert history == [{"role": "user", "content": "An earlier turn"}]
    assert [r.id for r in _user_rows(graph_as_app, sid)] == ["u0", "u1"]
