"""R8: the pin of a chat (WS-CO CO-11, task 3510).

Spec: ``project-docs/specs/chat_organization.md`` §10 and §18. Module:
``gateway/chat_session_member.py``. Routes: PUT and DELETE
``/chat/sessions/{id}/pin`` and the list ``GET /chat/sessions``
(``gateway/routes/chat.py``). Migration:
``infra/postgres/<n>_chat_session_member.sql``, found by its content.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the table: its key, its CHECK, FORCE RLS and its policy. A second run of
   the migration changes nothing;
2. a pin shows on a second device: a new client and a new backend read it;
3. a colleague in the same room sees no pin of the member, and the pin and
   the unpin of the colleague leave the member's pin as it was;
4. an archive takes the chat out of Pinned. A pin of an archived chat is
   409. A restore shows the pin again;
5. a pin of a chat that the member cannot read is refused (404), and writes
   no row: a private chat of a colleague, and a room that the member is not
   in;
6. org B never reads or writes a pin of org A. A pin of an id of org A from
   org B is 404, and the policy refuses a row stamped with another org;
7. an unpin is idempotent, and it keeps the row;
8. a session address in mixed case pins, and the list finds the pin;
9. a Delete of the chat deletes its rows (the cascade);
10. the member purge deletes each row of the member in the bound org, on a
    kept room too, and keeps the rows of a colleague and of another org;
11. with ``CHAT_PIN_ORGS`` off, the routes answer 404 and the list sends no
    pin key.

A hermetic fake agrees with any SQL that it gets, so none of this could be
proved without the database.

Mutations, each red here: drop ``SESSION_VISIBLE_SQL`` from ``_PIN_SQL``
(case 5); drop ``member_email = :member`` from ``_UNPIN_SQL`` (case 3); drop
``s.archived_at IS NULL`` from ``_PIN_SQL`` (case 4); let ``pin_fields``
ignore ``archived`` (case 4); drop the ``chat_member_rows`` row of the purge
(case 10); key the row on the address as typed (case 8).
"""

from __future__ import annotations

import re
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
_ALICE = "alice@co11.test"
_BOB = "bob@co11.test"
_CAROL = "carol@co11.test"  # org B
_DANA = "Dana.Mixed@CO11.test"  # org A, an address in mixed case
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS chat_session_member ("


def _migration() -> Path:
    """The migration of the table, found by its content and not its number."""
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Alice, Bob and Dana in org A, Carol in org B, seeded as the superuser."""
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, promoted.org_a), (_BOB, promoted.org_a),
                           (_DANA, promoted.org_a), (_CAROL, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
        # Migration 222 grants the function to `acb_app` only. The rehearsal
        # role has a suite-private name, so it gets the same grant here.
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def db(world, monkeypatch):
    """The world with the switch on, ``acb_graph`` sessions as the app role
    (one fresh backend each), and each chat that a test makes removed after
    it."""
    from acb_common import get_settings

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


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _client(user):
    """The real chat handlers behind FastAPI. The feature gate is a router
    dependency and is left out, as in ``test_chat_write_under_rls.py``."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import list_sessions, patch_session, pin_session, unpin_session

    app = FastAPI()
    app.get("/chat/sessions")(list_sessions)
    app.patch("/chat/sessions/{session_id}")(patch_session)
    app.put("/chat/sessions/{session_id}/pin")(pin_session)
    app.delete("/chat/sessions/{session_id}/pin")(unpin_session)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _chat(ns, *, org: str, owner: str, members: tuple[str, ...] = (),
          visibility: str = "private") -> str:
    """One chat with messages, seeded as the superuser. *owner* is its owner
    participant, and each of *members* a member participant."""
    sid = f"co11-{uuid.uuid4().hex[:12]}"
    ns.made.append(sid)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "visibility, organization_id) VALUES (:id, :u, 'orchestrator', 'Pin me', 4, "
            ":v, CAST(:o AS uuid))"), {"id": sid, "u": owner, "v": visibility, "o": org})
        for subject, role in ((owner, "owner"), *((m, "member") for m in members)):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": role, "o": org})
    return sid


def _rows(ns, sid: str) -> list[tuple[str, str, bool]]:
    """The rows of the chat as the superuser sees them: the org, the member,
    and whether the row holds a pin."""
    with ns.admin_engine.connect() as c:
        return [(r.o, r.member_email, r.pinned_at is not None) for r in c.execute(text(
            "SELECT organization_id::text AS o, member_email, pinned_at "
            "FROM chat_session_member WHERE session_id = :s ORDER BY member_email"),
            {"s": sid})]


def _listed(client, sid: str) -> dict:
    res = client.get("/chat/sessions")
    assert res.status_code == 200, res.text
    [row] = [r for r in res.json() if r["id"] == sid]
    return row


# ── 1. The table ────────────────────────────────────────────────────────────


def test_1_the_table_has_its_key_its_check_and_force_rls(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'chat_session_member'"))}
        key = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'chat_session_member'::regclass AND contype = 'p'")).scalar()
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'chat_session_member'::regclass")).first()
        policy = c.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'chat_session_member'")).first()
    assert cols == {
        "organization_id": ("uuid", "NO"),
        "session_id": ("text", "NO"),
        "member_email": ("text", "NO"),
        "pinned_at": ("timestamp with time zone", "YES"),
        # CO-2b adds the topic of the chat in its own migration, nullable.
        # `test_chat_topic_move_r8.py` holds its checks and its key.
        "topic_id": ("uuid", "YES"),
        "set_by": ("text", "YES"),
    }
    assert key == "PRIMARY KEY (session_id, member_email)"
    assert tuple(rls) == (True, True)
    assert "app.tenant_id" in policy.qual and "app.tenant_id" in policy.with_check


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    # The file holds its own BEGIN and COMMIT, so it runs on an autocommit
    # connection, through the cursor, as the ladder runs it.
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_table_has_its_key_its_check_and_force_rls(db)


def test_1_the_check_refuses_an_address_in_mixed_case(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_member (organization_id, session_id, member_email) "
            "VALUES (CAST(:o AS uuid), :s, 'Alice@co11.test')"), {"o": db.org_a, "s": sid})


# ── 2. A second device ──────────────────────────────────────────────────────


def test_2_a_pin_shows_on_a_second_device(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    phone = _client(_user(_ALICE, db.org_a))
    res = phone.put(f"/chat/sessions/{sid}/pin")
    assert res.status_code == 200, res.text
    assert res.json()["pinned"] is True
    first = res.json()["pinnedAt"]
    # Another device: a new client, and each request opens a new backend.
    laptop = _client(_user(_ALICE, db.org_a))
    row = _listed(laptop, sid)
    assert (row["pinned"], row["pinnedAt"]) == (True, first)
    # A second pin keeps the time of the first.
    again = laptop.put(f"/chat/sessions/{sid}/pin")
    assert again.status_code == 200 and again.json()["pinnedAt"] == first
    assert _rows(db, sid) == [(db.org_a, _ALICE, True)]


# ── 3. A colleague in the same room ─────────────────────────────────────────


def test_3_a_colleague_in_the_same_room_sees_no_pin(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    alice = _client(_user(_ALICE, db.org_a))
    bob = _client(_user(_BOB, db.org_a))
    assert alice.put(f"/chat/sessions/{sid}/pin").status_code == 200
    mine = _listed(alice, sid)["pinnedAt"]
    assert _listed(bob, sid)["pinned"] is False
    assert _listed(bob, sid)["pinnedAt"] is None
    # Bob pins and unpins his own: Alice's pin stays as it was.
    assert bob.put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert _listed(bob, sid)["pinned"] is True
    assert bob.delete(f"/chat/sessions/{sid}/pin").status_code == 200
    assert _listed(bob, sid)["pinned"] is False
    assert _listed(alice, sid)["pinnedAt"] == mine
    assert _rows(db, sid) == [(db.org_a, _ALICE, True), (db.org_a, _BOB, False)]


# ── 4. An archive ───────────────────────────────────────────────────────────


def test_4_an_archive_takes_the_chat_out_of_pinned(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    alice = _client(_user(_ALICE, db.org_a))
    assert alice.put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert alice.patch(f"/chat/sessions/{sid}", json={"archived": True}).status_code == 200
    row = _listed(alice, sid)
    assert row["archivedAt"] is not None
    assert (row["pinned"], row["pinnedAt"]) == (False, None)
    # A pin of an archived chat changes nothing.
    refused = alice.put(f"/chat/sessions/{sid}/pin")
    assert refused.status_code == 409, refused.text
    # A restore shows the pin again: the archive wrote no row.
    assert alice.patch(f"/chat/sessions/{sid}", json={"archived": False}).status_code == 200
    assert _listed(alice, sid)["pinned"] is True


def test_4_an_archive_of_a_room_takes_it_out_of_pinned_for_each_member(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    alice = _client(_user(_ALICE, db.org_a))
    bob = _client(_user(_BOB, db.org_a))
    assert bob.put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert alice.patch(f"/chat/sessions/{sid}", json={"archived": True}).status_code == 200
    assert _listed(bob, sid)["pinned"] is False


# ── 5. A chat that the member cannot read ───────────────────────────────────


def test_5_a_pin_of_a_chat_the_member_cannot_read_is_refused(db) -> None:
    private = _chat(db, org=db.org_a, owner=_ALICE)
    room = _chat(db, org=db.org_a, owner=_ALICE, members=(_DANA,), visibility="people")
    bob = _client(_user(_BOB, db.org_a))
    for sid in (private, room):
        assert bob.put(f"/chat/sessions/{sid}/pin").status_code == 404
        assert bob.delete(f"/chat/sessions/{sid}/pin").status_code == 404
        assert _rows(db, sid) == []
    assert bob.put("/chat/sessions/co11-no-such-chat/pin").status_code == 404


# ── 6. Two orgs ─────────────────────────────────────────────────────────────


def test_6_org_b_never_reads_or_writes_a_pin_of_org_a(db) -> None:
    from acb_graph import tenant_session
    from gateway.chat_session_member import pinned_at_of

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    assert _client(_user(_ALICE, db.org_a)).put(f"/chat/sessions/{sid}/pin").status_code == 200
    carol = _client(_user(_CAROL, db.org_b))
    assert carol.put(f"/chat/sessions/{sid}/pin").status_code == 404
    assert _rows(db, sid) == [(db.org_a, _ALICE, True)]
    with tenant_session(db.org_b) as s:
        assert s.execute(text("SELECT count(*) FROM chat_session_member "
                              "WHERE session_id = :s"), {"s": sid}).scalar() == 0
        assert pinned_at_of(s, _ALICE) == {}
    # The policy refuses a row stamped with another org, from the app role.
    with pytest.raises(DBAPIError), tenant_session(db.org_b) as s:
        s.execute(text(
            "INSERT INTO chat_session_member (organization_id, session_id, member_email, "
            "pinned_at) VALUES (CAST(:o AS uuid), :s, 'carol@co11.test', now())"),
            {"o": db.org_a, "s": sid})


# ── 7. Unpin ────────────────────────────────────────────────────────────────


def test_7_an_unpin_is_idempotent_and_keeps_the_row(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    alice = _client(_user(_ALICE, db.org_a))
    none_yet = alice.delete(f"/chat/sessions/{sid}/pin")
    assert none_yet.status_code == 200 and none_yet.json()["pinned"] is False
    assert alice.put(f"/chat/sessions/{sid}/pin").status_code == 200
    for _ in range(2):
        res = alice.delete(f"/chat/sessions/{sid}/pin")
        assert res.status_code == 200 and res.json()["pinned"] is False
    assert _listed(alice, sid)["pinned"] is False
    assert _rows(db, sid) == [(db.org_a, _ALICE, False)]


# ── 8. An address in mixed case ─────────────────────────────────────────────


def test_8_an_address_in_mixed_case_pins_and_the_list_finds_it(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_DANA)
    dana = _client(_user(_DANA, db.org_a))
    assert dana.put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert _listed(dana, sid)["pinned"] is True
    assert _rows(db, sid) == [(db.org_a, _DANA.lower(), True)]


# ── 9. A Delete of the chat ─────────────────────────────────────────────────


def test_9_a_delete_of_the_chat_deletes_its_rows(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    for who in (_ALICE, _BOB):
        assert _client(_user(who, db.org_a)).put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert len(_rows(db, sid)) == 2
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": sid})
    assert _rows(db, sid) == []


# ── 10. The member purge ────────────────────────────────────────────────────


@pytest.fixture
def purge_world(db, monkeypatch):
    """An owner in org A who survives the purge, and the off-Postgres hops
    stubbed, as ``test_chat_summary_r8.py`` does."""
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
    leaver = f"leaver.{uuid.uuid4().hex[:8]}@co11.test"
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": leaver, "o": db.org_a})
    db.leaver = leaver
    yield db
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": leaver})


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


async def test_10_the_purge_deletes_the_rows_of_the_member_in_this_org(purge_world) -> None:
    from acb_common.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import purge_member_rows

    ns = purge_world
    mine = _chat(ns, org=ns.org_a, owner=ns.leaver)
    room = _chat(ns, org=ns.org_a, owner=_BOB, members=(ns.leaver,), visibility="people")
    other_org = _chat(ns, org=ns.org_b, owner=ns.leaver)
    leaver_a = _client(_user(ns.leaver, ns.org_a))
    for sid in (mine, room):
        assert leaver_a.put(f"/chat/sessions/{sid}/pin").status_code == 200
    assert _client(_user(_BOB, ns.org_a)).put(f"/chat/sessions/{room}/pin").status_code == 200
    assert _client(_user(ns.leaver, ns.org_b)).put(
        f"/chat/sessions/{other_org}/pin").status_code == 200
    async with tenant_engine_scope(_app_dsn(ns)), tenant_session(ns.org_a) as session:
        member = await get_member(session, ns.org_a, ns.leaver)
        deleted, _kept, _plans = await purge_member_rows(
            session, ns.org_a, member, actor_email=ns.owner_email)
    assert deleted["chat_member_rows"] == 2
    assert _rows(ns, mine) == []
    # The room stays, with Bob's pin and without hers.
    assert _rows(ns, room) == [(ns.org_a, _BOB, True)]
    # The same address in another org keeps its pin.
    assert _rows(ns, other_org) == [(ns.org_b, ns.leaver, True)]


# ── 11. The switch ──────────────────────────────────────────────────────────


def test_11_with_the_switch_off_the_routes_are_404_and_the_list_sends_no_pin(
    db, monkeypatch,
) -> None:
    from acb_common import get_settings

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    alice = _client(_user(_ALICE, db.org_a))
    assert alice.put(f"/chat/sessions/{sid}/pin").status_code == 200
    monkeypatch.setattr(get_settings(), "chat_pin_orgs", "", raising=False)
    assert alice.put(f"/chat/sessions/{sid}/pin").status_code == 404
    assert alice.delete(f"/chat/sessions/{sid}/pin").status_code == 404
    row = _listed(alice, sid)
    assert "pinned" not in row and "pinnedAt" not in row
    # The switch hides the pin and deletes nothing.
    assert _rows(db, sid) == [(db.org_a, _ALICE, True)]


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_chat_session_member\.sql", _migration().name)
