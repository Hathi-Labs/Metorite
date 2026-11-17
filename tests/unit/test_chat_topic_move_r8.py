"""R8: the topic of a chat, for each member (WS-CO CO-2b, task 3588).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163). Module:
``gateway/chat_session_member.py``, with the topic rule of
``gateway/chat_topic.py``. Routes: ``PUT /chat/sessions/{id}/topic`` and the
list ``GET /chat/sessions`` (``gateway/routes/chat.py``), and
``/chat/topics`` (``gateway/routes/chat_topics.py``). Migration:
``infra/postgres/<n>_chat_session_member_topic.sql``, found by its content.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the columns, the two CHECKs and the composite foreign key. A row never
   names a topic of another org, and a second run of the migration changes
   nothing;
2. a member's move survives the automatic pass, "No topic" too (D163);
3. the pass fills a missing row, a pin row and a row of the pass;
4. a colleague's move never touches mine: the row is per member;
5. a private topic of A never reaches B: B cannot use it (the 404 of an
   unknown topic), and B's list never names it for a shared chat;
6. org B never reads or moves a topic or a chat of org A;
7. a chat that the member cannot read cannot be moved;
8. a hidden company topic: no move into it, and the list sends no topic for
   a chat in it until an admin shows it again;
9. a merge moves the chats of the source to the target, and keeps who set
   each one;
10. a move and a merge at the same time, both orders: the move lands on the
    target or is refused, never on a gone topic, and never a deadlock;
11. the member purge leaves no row with the topic of a deleted topic, and a
    row of a colleague keeps the org (ON DELETE SET NULL (topic_id));
12. an unpin keeps the topic, and a pin keeps it too;
13. with ``CHAT_TOPICS_ORGS`` off, the route answers 404 and the list sends
    no topic key;
14. an address in mixed case moves a chat, the row keys on the lower case,
    and the pass passes the address as the chat stores it;
15. an archived chat takes no topic, as it takes no pin: the move is 409,
    the pass files nothing, the read sends no topic, and a restore shows the
    topic again.

Mutations, each red here (R7):

* drop the WHERE of the DO UPDATE in ``_SET_AUTO_SQL`` -> (2);
* drop ``SESSION_VISIBLE_SQL`` from ``_SET_TOPIC_SQL`` -> (7);
* drop ``_VISIBLE`` from ``chat_topic._FOR_CHAT_SQL`` -> (5);
* drop ``t.hidden_at IS NULL`` from ``_FOR_CHAT_SQL`` -> (8);
* drop the ``repoint_topic`` call of the merge -> (9), the chats lose the
  topic through the SET NULL of the delete;
* run the upsert before ``_usable`` in ``set_topic`` -> (10c), the move and
  the merge deadlock (40P01);
* write ``ON DELETE SET NULL`` with no column list -> (11), the delete of the
  topic fails on ``organization_id`` NOT NULL.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@co2b.test"
_BOB = "bob@co2b.test"
_ADMIN = "admin@co2b.test"  # org A, an admin of company topics
_CAROL = "carol@co2b.test"  # org B
_MIGRATION_MARK = "ADD COLUMN IF NOT EXISTS topic_id UUID"
_NO_TOPIC = {"detail": "Topic not found"}
_NO_CHAT = {"detail": "Session not found"}


def _migration() -> Path:
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Alice, Bob and the admin in org A, Carol in org B, seeded as the superuser."""
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, promoted.org_a), (_BOB, promoted.org_a),
                           (_ADMIN, promoted.org_a), (_CAROL, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def db(world, monkeypatch):
    """The world with both switches on, ``acb_graph`` sessions as the app role
    (one fresh backend each), and each chat and topic removed after a test."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_topics_orgs", "*", raising=False)
    monkeypatch.setattr(get_settings(), "chat_pin_orgs", "*", raising=False)
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    made: list[str] = []
    world.made = made
    try:
        yield world
    finally:
        eng.dispose()
        with world.admin_engine.begin() as c:
            for sid in made:
                c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": sid})
            c.execute(text("DELETE FROM chat_topic"))


def _user(email: str, org: str | None, *, admin: bool = False):
    from acb_auth import UserContext, UserRole, build_access

    perms = ["feature:chat", *(["admin:settings:manage"] if admin else [])]
    return UserContext(email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
                       organization_id=org)


def _client(user):
    """The real handlers: the topic router with its gates, and the chat
    handlers that the pin suite mounts."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes import chat_topics
    from gateway.routes.chat import (
        list_sessions,
        move_session_topic,
        patch_session,
        pin_session,
        unpin_session,
    )

    app = FastAPI()
    app.include_router(chat_topics.router)
    app.get("/chat/sessions")(list_sessions)
    app.patch("/chat/sessions/{session_id}")(patch_session)
    app.put("/chat/sessions/{session_id}/topic")(move_session_topic)
    app.put("/chat/sessions/{session_id}/pin")(pin_session)
    app.delete("/chat/sessions/{session_id}/pin")(unpin_session)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _chat(ns, *, org: str, owner: str, members: tuple[str, ...] = (),
          visibility: str = "private") -> str:
    sid = f"co2b-{uuid.uuid4().hex[:12]}"
    ns.made.append(sid)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "visibility, organization_id) VALUES (:id, :u, 'orchestrator', 'Move me', 4, "
            ":v, CAST(:o AS uuid))"), {"id": sid, "u": owner, "v": visibility, "o": org})
        for subject, role in ((owner, "owner"), *((m, "member") for m in members)):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": role, "o": org})
    return sid


def _company(ns, org: str, name: str, *, hidden: bool = False) -> str:
    with ns.admin_engine.begin() as c:
        return c.execute(text(
            "INSERT INTO chat_topic (organization_id, name, scope, hidden_at) "
            "VALUES (CAST(:o AS uuid), :n, 'company', CASE WHEN :h THEN now() END) "
            "RETURNING id::text"), {"o": org, "n": name, "h": hidden}).scalar()


def _mine(client, name: str) -> str:
    res = client.post("/chat/topics", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["topic"]["id"]


def _rows(ns, sid: str) -> list[tuple[str, str, str | None, str | None]]:
    """The rows of a chat as the superuser sees them: the org, the member,
    the topic and who set it."""
    with ns.admin_engine.connect() as c:
        return [(r.o, r.member_email, r.topic_id, r.set_by) for r in c.execute(text(
            "SELECT organization_id::text AS o, member_email, topic_id::text AS topic_id, "
            "set_by FROM chat_session_member WHERE session_id = :s ORDER BY member_email"),
            {"s": sid})]


def _move(client, sid: str, topic: str | None):
    return client.put(f"/chat/sessions/{sid}/topic", json={"topic_id": topic})


def _listed(client, sid: str) -> dict:
    res = client.get("/chat/sessions")
    assert res.status_code == 200, res.text
    [row] = [r for r in res.json() if r["id"] == sid]
    return row


def _topic_of(client, sid: str) -> tuple[str | None, str | None]:
    row = _listed(client, sid)
    return row["topicId"], row["topicName"]


def _auto(org: str, sid: str, member: str, topic: str) -> str:
    """The automatic pass of CO-1, in a tenant-bound session of the app role."""
    from acb_graph import tenant_session
    from gateway.chat_session_member import set_auto_topic

    with tenant_session(org) as s:
        return set_auto_topic(s, sid, member=member, topic_id=topic).state


# ── 1. The columns and the keys ─────────────────────────────────────────────


def test_1_the_columns_the_checks_and_the_key(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'chat_session_member'"))}
        defs = {r.conname: r.d for r in c.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conrelid = 'chat_session_member'::regclass"))}
    assert cols["topic_id"] == ("uuid", "YES")
    assert cols["set_by"] == ("text", "YES")
    assert defs["chat_session_member_topic_fk"] == (
        "FOREIGN KEY (organization_id, topic_id) REFERENCES chat_topic(organization_id, id) "
        "ON DELETE SET NULL (topic_id)")
    assert "'auto'" in defs["chat_session_member_set_by"]
    assert defs["chat_session_member_topic_set_by"] == (
        "CHECK (((topic_id IS NULL) OR (set_by IS NOT NULL)))")


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_columns_the_checks_and_the_key(db)


@pytest.mark.parametrize(("topic", "set_by"), [("sales", "robot"), ("sales", None)])
def test_1_the_checks_refuse_a_bad_row(db, topic: str, set_by: str | None) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    sales = _company(db, db.org_a, "Sales")
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_member (organization_id, session_id, member_email, "
            "topic_id, set_by) VALUES (CAST(:o AS uuid), :s, :m, CAST(:t AS uuid), :b)"),
            {"o": db.org_a, "s": sid, "m": _ALICE, "t": sales, "b": set_by})


def test_1_a_row_never_names_a_topic_of_another_org(db) -> None:
    """The composite key. A foreign key check skips RLS, so the key is the
    guard: even the superuser cannot point a row of org A at org B."""
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    theirs = _company(db, db.org_b, "Sales")
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_member (organization_id, session_id, member_email, "
            "topic_id, set_by) VALUES (CAST(:o AS uuid), :s, :m, CAST(:t AS uuid), 'member')"),
            {"o": db.org_a, "s": sid, "m": _ALICE, "t": theirs})


# ── 2. A move survives the pass ─────────────────────────────────────────────


def test_2_a_move_of_the_member_survives_the_pass(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    sales = _company(db, db.org_a, "Sales")
    general = _company(db, db.org_a, "General")
    alice = _client(_user(_ALICE, db.org_a))
    res = _move(alice, sid, sales)
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "topicId": sales, "topicName": "Sales"}
    assert _auto(db.org_a, sid, _ALICE, general) == "kept"
    assert _topic_of(alice, sid) == (sales, "Sales")
    assert _rows(db, sid) == [(db.org_a, _ALICE, sales, "member")]


def test_2_no_topic_is_a_move_too_and_the_pass_leaves_it(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    general = _company(db, db.org_a, "General")
    alice = _client(_user(_ALICE, db.org_a))
    assert _auto(db.org_a, sid, _ALICE, general) == "set"
    res = _move(alice, sid, None)
    assert res.status_code == 200 and res.json()["topicId"] is None, res.text
    assert _auto(db.org_a, sid, _ALICE, general) == "kept"
    assert _topic_of(alice, sid) == (None, None)
    assert _rows(db, sid) == [(db.org_a, _ALICE, None, "member")]


# ── 3. The pass fills a missing row, a pin row and its own row ──────────────


def test_3_the_pass_fills_a_missing_row_a_pin_row_and_its_own_row(db) -> None:
    fresh = _chat(db, org=db.org_a, owner=_ALICE)
    pinned = _chat(db, org=db.org_a, owner=_ALICE)
    sales = _company(db, db.org_a, "Sales")
    general = _company(db, db.org_a, "General")
    alice = _client(_user(_ALICE, db.org_a))
    assert _auto(db.org_a, fresh, _ALICE, sales) == "set"
    assert _rows(db, fresh) == [(db.org_a, _ALICE, sales, "auto")]
    # A second pass moves its own row.
    assert _auto(db.org_a, fresh, _ALICE, general) == "set"
    assert _rows(db, fresh) == [(db.org_a, _ALICE, general, "auto")]
    # A pin row has no topic and no set_by: the pass fills it, and the pin stays.
    assert alice.put(f"/chat/sessions/{pinned}/pin").status_code == 200
    assert _auto(db.org_a, pinned, _ALICE, sales) == "set"
    assert _rows(db, pinned) == [(db.org_a, _ALICE, sales, "auto")]
    assert _listed(alice, pinned)["pinned"] is True


# ── 4. Per member ───────────────────────────────────────────────────────────


def test_4_a_colleague_s_move_never_touches_mine(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    sales = _company(db, db.org_a, "Sales")
    finance = _company(db, db.org_a, "Finance")
    alice = _client(_user(_ALICE, db.org_a))
    bob = _client(_user(_BOB, db.org_a))
    assert _move(alice, sid, sales).status_code == 200
    assert _topic_of(bob, sid) == (None, None)
    assert _move(bob, sid, finance).status_code == 200
    assert _move(bob, sid, None).status_code == 200
    assert _topic_of(alice, sid) == (sales, "Sales")
    assert _rows(db, sid) == [(db.org_a, _ALICE, sales, "member"),
                              (db.org_a, _BOB, None, "member")]


# ── 5. A private topic never reaches a colleague ────────────────────────────


def test_5_a_private_topic_of_a_never_reaches_b(db) -> None:
    room = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    bobs = _chat(db, org=db.org_a, owner=_BOB)
    alice = _client(_user(_ALICE, db.org_a))
    bob = _client(_user(_BOB, db.org_a, admin=True))
    private = _mine(alice, "Board prep")
    assert _move(alice, room, private).status_code == 200
    # Bob, an admin too, cannot use her topic, on his chat or on the room.
    for sid in (bobs, room):
        res = _move(bob, sid, private)
        assert (res.status_code, res.json()) == (404, _NO_TOPIC), res.text
    assert _auto(db.org_a, bobs, _BOB, private) == "no_topic"
    # The 404 is the 404 of a topic that does not exist.
    res = _move(bob, bobs, str(uuid.uuid4()))
    assert (res.status_code, res.json()) == (404, _NO_TOPIC)
    # The shared chat: Bob's list names no topic, and never her name.
    assert _topic_of(bob, room) == (None, None)
    assert "Board prep" not in str(bob.get("/chat/sessions").json())
    assert _rows(db, bobs) == []
    assert _rows(db, room) == [(db.org_a, _ALICE, private, "member")]


# ── 6. Two orgs ─────────────────────────────────────────────────────────────


def test_6_org_b_never_reads_or_moves_org_a(db) -> None:
    from acb_graph import tenant_session
    from gateway.chat_session_member import topics_of

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    carols = _chat(db, org=db.org_b, owner=_CAROL)
    sales_a = _company(db, db.org_a, "Sales")
    _company(db, db.org_b, "Sales")
    assert _move(_client(_user(_ALICE, db.org_a)), sid, sales_a).status_code == 200
    carol = _client(_user(_CAROL, db.org_b))
    # Her own chat into a topic of org A: the topic is not hers to see.
    res = _move(carol, carols, sales_a)
    assert (res.status_code, res.json()) == (404, _NO_TOPIC), res.text
    # A chat of org A: a topic of org A is no topic in org B.
    assert _move(carol, sid, sales_a).status_code == 404
    assert _move(carol, sid, None).json() == _NO_CHAT
    assert _rows(db, sid) == [(db.org_a, _ALICE, sales_a, "member")]
    assert _rows(db, carols) == []
    with tenant_session(db.org_b) as s:
        assert topics_of(s, [sid], _ALICE) == {}
        assert s.execute(text("SELECT count(*) FROM chat_session_member "
                              "WHERE session_id = :s"), {"s": sid}).scalar() == 0


# ── 7. A chat that the member cannot read ───────────────────────────────────


def test_7_a_chat_that_the_member_cannot_read_cannot_be_moved(db) -> None:
    private = _chat(db, org=db.org_a, owner=_ALICE)
    room = _chat(db, org=db.org_a, owner=_ALICE, members=(_ADMIN,), visibility="people")
    sales = _company(db, db.org_a, "Sales")
    bob = _client(_user(_BOB, db.org_a))
    for sid in (private, room):
        for topic in (sales, None):
            res = _move(bob, sid, topic)
            assert (res.status_code, res.json()) == (404, _NO_CHAT), res.text
        assert _auto(db.org_a, sid, _BOB, sales) == "missing"
        assert _rows(db, sid) == []
    assert _move(bob, "co2b-no-such-chat", sales).json() == _NO_CHAT


# ── 8. A hidden company topic ───────────────────────────────────────────────


def test_8_a_hidden_company_topic_takes_no_chat_and_shows_none(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    other = _chat(db, org=db.org_a, owner=_ALICE)
    legal = _company(db, db.org_a, "Legal")
    alice = _client(_user(_ALICE, db.org_a))
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    assert _move(alice, sid, legal).status_code == 200
    assert admin.patch(f"/chat/topics/{legal}", json={"hidden": True}).status_code == 200
    # The chat keeps its row, and the list sends no topic: it sits under its day.
    assert _topic_of(alice, sid) == (None, None)
    assert _rows(db, sid) == [(db.org_a, _ALICE, legal, "member")]
    # No move and no pass into a hidden topic, for an admin too.
    for client in (alice, admin):
        res = _move(client, other, legal)
        assert (res.status_code, res.json()) == (404, _NO_TOPIC), res.text
    assert _auto(db.org_a, other, _ALICE, legal) == "no_topic"
    # Shown again, the chat is back in its topic.
    assert admin.patch(f"/chat/topics/{legal}", json={"hidden": False}).status_code == 200
    assert _topic_of(alice, sid) == (legal, "Legal")


# ── 9. A merge ──────────────────────────────────────────────────────────────


def test_9_a_merge_moves_the_chats_and_keeps_who_set_them(db) -> None:
    room = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    ops = _company(db, db.org_a, "Ops")
    operations = _company(db, db.org_a, "Operations")
    alice = _client(_user(_ALICE, db.org_a))
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    assert _move(alice, room, ops).status_code == 200
    assert _auto(db.org_a, room, _BOB, ops) == "set"
    res = admin.post(f"/chat/topics/{ops}/merge", json={"into": operations})
    assert res.status_code == 200, res.text
    assert _rows(db, room) == [(db.org_a, _ALICE, operations, "member"),
                               (db.org_a, _BOB, operations, "auto")]
    assert _topic_of(alice, room) == (operations, "Operations")
    # The member's move still survives the pass after the merge.
    assert _auto(db.org_a, room, _ALICE, _company(db, db.org_a, "General")) == "kept"


# ── 10. A move and a merge at the same time ─────────────────────────────────


def _until(check, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def _a_session_waits_on_a_lock(ns) -> bool:
    auto = ns.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c:
        return bool(c.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'")).scalar())


def _in_a_thread(fn) -> tuple[threading.Thread, dict]:
    out: dict = {}

    def run() -> None:
        try:
            out["value"] = fn()
        except BaseException as exc:  # the test reads it
            out["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _set(org: str, sid: str, member: str, topic: str | None) -> str:
    from acb_graph import tenant_session
    from gateway.chat_session_member import set_topic

    with tenant_session(org) as s:
        return set_topic(s, sid, viewer=member, topic_id=topic).state


def test_10a_a_move_into_a_source_that_a_merge_holds_is_refused(db) -> None:
    """The merge commits first. The move waits on the lock of its topic,
    then finds the topic gone: refused, and no row names it."""
    from acb_graph import tenant_session
    from gateway.chat_topic import merge_company_topics

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    ops = _company(db, db.org_a, "Ops")
    operations = _company(db, db.org_a, "Operations")
    with tenant_session(db.org_a) as s1:
        assert merge_company_topics(s1, ops, into=operations, member=_ADMIN,
                                    admin=True).outcome == "ok"
        t, out = _in_a_thread(lambda: _set(db.org_a, sid, _ALICE, ops))
        assert _until(lambda: _a_session_waits_on_a_lock(db) or not t.is_alive())
    t.join(30)
    assert not t.is_alive() and "error" not in out, out
    assert out["value"] == "no_topic"
    assert _rows(db, sid) == []


def test_10b_a_merge_that_waits_on_a_move_takes_the_moved_chat(db) -> None:
    """The move commits first. The merge waits on the lock of the move, then
    moves the new row to the target, and keeps ``set_by``."""
    from acb_graph import tenant_session
    from gateway.chat_session_member import set_topic
    from gateway.chat_topic import merge_company_topics

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    ops = _company(db, db.org_a, "Ops")
    operations = _company(db, db.org_a, "Operations")

    def merge():
        with tenant_session(db.org_a) as s2:
            return merge_company_topics(s2, ops, into=operations, member=_ADMIN,
                                        admin=True).outcome

    with tenant_session(db.org_a) as s1:
        assert set_topic(s1, sid, viewer=_ALICE, topic_id=ops).state == "set"
        t, out = _in_a_thread(merge)
        assert _until(lambda: _a_session_waits_on_a_lock(db) or not t.is_alive())
    t.join(30)
    assert not t.is_alive() and "error" not in out, out
    assert out["value"] == "ok"
    assert _rows(db, sid) == [(db.org_a, _ALICE, operations, "member")]


def test_10c_a_move_out_of_the_source_into_the_target_does_not_deadlock(db) -> None:
    """The case of the lock order. Alice's chat sits in the source. A merge
    has locked both topics and has not moved the chats yet. Alice moves her
    chat to the target at that moment. The move takes the topic first, so it
    waits before it touches her row, and the merge moves the row and commits.
    Then the move lands. Mutation: lock the row first (the upsert before
    ``_usable``) -> the move holds her row and waits on the target, the merge
    waits on her row, and Postgres ends one of them (40P01)."""
    from acb_graph import tenant_session
    from gateway import chat_topic as topics

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    ops = _company(db, db.org_a, "Ops")
    operations = _company(db, db.org_a, "Operations")
    assert _set(db.org_a, sid, _ALICE, ops) == "set"
    with tenant_session(db.org_a) as s1:
        # The first step of the merge: both topics locked, no row moved yet.
        locked = s1.execute(text(topics._LOCK_SQL), {
            "id": ops, "into": operations, "member": _ADMIN, "admin": True}).fetchall()
        assert len(locked) == 2
        t, out = _in_a_thread(lambda: _set(db.org_a, sid, _ALICE, operations))
        assert _until(lambda: _a_session_waits_on_a_lock(db) or not t.is_alive())
        # The rest of the merge, in the same transaction.
        assert topics.merge_company_topics(s1, ops, into=operations, member=_ADMIN,
                                           admin=True).outcome == "ok"
    t.join(30)
    assert not t.is_alive() and "error" not in out, out
    assert out["value"] == "set"
    assert _rows(db, sid) == [(db.org_a, _ALICE, operations, "member")]


def test_10d_crossing_moves_and_merges_through_the_routes(db) -> None:
    """Three rounds through the routes, at once: each move answers 200 or the
    404 of a topic, the merge answers 200, and no row names the source."""
    alice = _client(_user(_ALICE, db.org_a))
    admin = _client(_user(_ADMIN, db.org_a, admin=True))
    for n in range(3):
        sid = _chat(db, org=db.org_a, owner=_ALICE)
        x = _company(db, db.org_a, f"Ops {n}")
        y = _company(db, db.org_a, f"Operations {n}")
        gate = threading.Barrier(2)

        def run(fn, gate=gate):
            gate.wait(10)
            return fn()

        t1, o1 = _in_a_thread(lambda s=sid, x=x: run(lambda: _move(alice, s, x)))
        t2, o2 = _in_a_thread(lambda x=x, y=y: run(
            lambda: admin.post(f"/chat/topics/{x}/merge", json={"into": y})))
        t1.join(30)
        t2.join(30)
        assert "error" not in o1 and "error" not in o2, (o1, o2)
        assert o2["value"].status_code == 200, o2["value"].text
        moved = o1["value"]
        assert (moved.status_code, moved.json().get("detail")) in (
            (200, None), (404, "Topic not found")), moved.text
        rows = _rows(db, sid)
        assert all(topic != x for _o, _m, topic, _b in rows), rows
        if moved.status_code == 200:
            assert rows == [(db.org_a, _ALICE, y, "member")]


# ── 11. The member purge ────────────────────────────────────────────────────


@pytest.fixture
def purge_world(db, monkeypatch):
    """An owner in org A who survives the purge, and the off-Postgres hops
    stubbed, as ``test_chat_pin_r8.py`` does."""
    from gateway.routes.admin import members
    from gateway.routes.crm import questions

    async def _no_keys(org: str, email: str) -> int:
        return 0

    monkeypatch.setattr(members, "record_admin_change", lambda *a, **k: None)
    monkeypatch.setattr(questions, "forget_member_keys", _no_keys)
    with db.admin_engine.begin() as c:
        role = c.execute(text(
            "INSERT INTO org_role (organization_id, slug, display_name, is_system, rank) "
            "VALUES (CAST(:o AS uuid), 'owner', 'Owner', true, 0) "
            "ON CONFLICT DO NOTHING RETURNING id"), {"o": db.org_a}).scalar()
        if role is None:
            role = c.execute(text(
                "SELECT id FROM org_role WHERE organization_id = CAST(:o AS uuid) "
                "AND slug = 'owner'"), {"o": db.org_a}).scalar()
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e "
            "ON CONFLICT DO NOTHING"),
            {"r": role, "e": db.owner_email, "o": db.org_a})
    leaver = f"leaver.{uuid.uuid4().hex[:8]}@co2b.test"
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": leaver, "o": db.org_a})
    db.leaver = leaver
    yield db
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": leaver})


async def test_11_the_purge_leaves_no_row_on_a_deleted_topic(purge_world) -> None:
    """The leaver's private topic goes, and so do her rows. Bob's row in the
    same room keeps its company topic. A row that a superuser planted on her
    topic keeps its org and loses only the topic: ON DELETE SET NULL
    (topic_id). A plain SET NULL would fail the purge on ``organization_id``."""
    from acb_common.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import purge_member_rows

    ns = purge_world
    room = _chat(ns, org=ns.org_a, owner=_BOB, members=(ns.leaver,), visibility="people")
    planted = _chat(ns, org=ns.org_a, owner=_ALICE)
    sales = _company(ns, ns.org_a, "Sales")
    leaver = _client(_user(ns.leaver, ns.org_a))
    exit_notes = _mine(leaver, "Exit notes")
    assert _move(leaver, room, exit_notes).status_code == 200
    assert _move(_client(_user(_BOB, ns.org_a)), room, sales).status_code == 200
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_member (organization_id, session_id, member_email, "
            "topic_id, set_by) VALUES (CAST(:o AS uuid), :s, :m, CAST(:t AS uuid), 'auto')"),
            {"o": ns.org_a, "s": planted, "m": _ALICE, "t": exit_notes})
    dsn = ns.app_url.render_as_string(hide_password=False)
    async with tenant_engine_scope(dsn), tenant_session(ns.org_a) as session:
        member = await get_member(session, ns.org_a, ns.leaver)
        deleted, _kept, _plans = await purge_member_rows(
            session, ns.org_a, member, actor_email=ns.owner_email)
    assert deleted["chat_topics"] == 1
    assert _rows(ns, room) == [(ns.org_a, _BOB, sales, "member")]
    assert _rows(ns, planted) == [(ns.org_a, _ALICE, None, "auto")]
    with ns.admin_engine.connect() as c:
        dangling = c.execute(text(
            "SELECT count(*) FROM chat_session_member m WHERE m.topic_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM chat_topic t WHERE t.id = m.topic_id)")).scalar()
    assert dangling == 0


# ── 12. The pin and the topic share the row ─────────────────────────────────


def test_12_an_unpin_keeps_the_topic_and_a_pin_keeps_it_too(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    sales = _company(db, db.org_a, "Sales")
    alice = _client(_user(_ALICE, db.org_a))
    assert _move(alice, sid, sales).status_code == 200
    assert alice.put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert alice.delete(f"/chat/sessions/{sid}/pin").status_code == 200
    assert _rows(db, sid) == [(db.org_a, _ALICE, sales, "member")]
    row = _listed(alice, sid)
    assert (row["pinned"], row["topicId"]) == (False, sales)


# ── 13. The switch ──────────────────────────────────────────────────────────


def test_13_with_the_switch_off_the_route_is_404_and_the_list_sends_no_topic(
    db, monkeypatch,
) -> None:
    from acb_common import get_settings

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    sales = _company(db, db.org_a, "Sales")
    alice = _client(_user(_ALICE, db.org_a))
    assert _move(alice, sid, sales).status_code == 200
    for raw in ("", db.org_b):
        monkeypatch.setattr(get_settings(), "chat_topics_orgs", raw, raising=False)
        res = _move(alice, sid, None)
        assert (res.status_code, res.json()) == (404, {"detail": "Not Found"}), res.text
        row = _listed(alice, sid)
        assert "topicId" not in row and "topicName" not in row
    # The switch hides the topic and changes nothing.
    assert _rows(db, sid) == [(db.org_a, _ALICE, sales, "member")]


# ── 14. An address in mixed case ────────────────────────────────────────────


def test_14_an_address_in_mixed_case_moves_and_the_pass_needs_the_stored_case(db) -> None:
    """The row keys on the address in lower case. The visibility rule binds
    the address as the chat stores it, as the pin does. So the pass (CO-1)
    passes that form, and the lower case alone reads as no chat. Mutation:
    key the row on the address as typed -> the CHECK of 450 refuses it."""
    dana = "Dana.Mixed@CO2B.test"
    sid = _chat(db, org=db.org_a, owner=dana)
    sales = _company(db, db.org_a, "Sales")
    general = _company(db, db.org_a, "General")
    client = _client(_user(dana, db.org_a))
    assert _move(client, sid, sales).status_code == 200
    assert _rows(db, sid) == [(db.org_a, dana.lower(), sales, "member")]
    assert _topic_of(client, sid) == (sales, "Sales")
    assert _auto(db.org_a, sid, dana, general) == "kept"
    assert _auto(db.org_a, sid, dana.lower(), general) == "missing"


# ── 15. An archived chat, as for the pin ────────────────────────────────────


def test_15_an_archived_chat_takes_no_topic_and_a_restore_shows_it_again(db) -> None:
    """The pin rule of CO-11, matched. Mutation: drop ``s.archived_at IS
    NULL`` from ``_SET_TOPIC_SQL`` -> the move of an archived chat answers 200."""
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    sales = _company(db, db.org_a, "Sales")
    general = _company(db, db.org_a, "General")
    alice = _client(_user(_ALICE, db.org_a))
    assert _move(alice, sid, sales).status_code == 200
    assert alice.patch(f"/chat/sessions/{sid}", json={"archived": True}).status_code == 200
    # The read sends no topic, the move is 409, and the pass files nothing.
    assert _topic_of(alice, sid) == (None, None)
    res = _move(alice, sid, general)
    assert (res.status_code, res.json()) == (
        409, {"detail": "This chat is archived. Restore it, then move it."}), res.text
    assert _auto(db.org_a, sid, _ALICE, general) == "archived"
    # The archive wrote no row: a restore shows the topic again.
    assert _rows(db, sid) == [(db.org_a, _ALICE, sales, "member")]
    assert alice.patch(f"/chat/sessions/{sid}", json={"archived": False}).status_code == 200
    assert _topic_of(alice, sid) == (sales, "Sales")


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_chat_session_member_topic\.sql", _migration().name)
