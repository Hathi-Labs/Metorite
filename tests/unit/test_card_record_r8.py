"""R8: the card record (WS-CO CO-17, task 3516).

Spec: ``project-docs/specs/chat_organization.md`` §16.3. Module:
``orchestrator/card_record.py``. Migration:
``infra/postgres/<n>_chat_card_record.sql``, found by its content.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the table: its CHECKs, FORCE RLS and its policy. A second run of the
   migration changes nothing. The CHECKs refuse a decline, a free-text act
   kind and a link that is not an app path;
2. an approved card writes ONE row, and a second write of the card writes none;
3. the row holds no text: its columns are the ids, the outcome key, the act
   kind and the link;
4. the link is set once, by the member's own row, and a path only;
5. a colleague in the same room reads no row, and the reader of the member
   returns the member's own;
6. org B never reads or writes a row of org A, and a chat of org A written
   from org B gives no row;
7. a Delete of the chat deletes its rows (the cascade);
8. the member purge deletes the member's rows in the bound org, and keeps the
   rows of a colleague and of another org.

Mutations, each red here: drop ``member_email = :member`` from ``read_cards``
(case 5); drop the ``record_link IS NULL`` term of ``_LINK_SQL`` (case 4); drop
the ``chat_card_rows`` row of the purge (case 8); drop the ``outcome_key``
CHECK (case 1).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@co17.test"
_BOB = "bob@co17.test"
_CAROL = "carol@co17.test"  # org B
_MARK = "CREATE TABLE IF NOT EXISTS chat_card_record ("
_LINK = "/projects?task=abc"


def _migration() -> Path:
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    with promoted.admin_engine.begin() as c:
        for email, org in ((_ALICE, promoted.org_a), (_BOB, promoted.org_a),
                           (_CAROL, promoted.org_b)):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": org})
    return promoted


@pytest.fixture
def db(world, monkeypatch):
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


def _chat(ns, *, org: str, owner: str, members: tuple[str, ...] = (),
          visibility: str = "private") -> str:
    sid = f"co17-{uuid.uuid4().hex[:12]}"
    ns.made.append(sid)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "visibility, organization_id) VALUES (:id, :u, 'orchestrator', 'Cards', 2, "
            ":v, CAST(:o AS uuid))"), {"id": sid, "u": owner, "v": visibility, "o": org})
        for subject, role in ((owner, "owner"), *((m, "member") for m in members)):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": role, "o": org})
    return sid


def _rid() -> str:
    return uuid.uuid4().hex


def _rows(ns, sid: str):
    with ns.admin_engine.connect() as c:
        return list(c.execute(text(
            "SELECT organization_id::text AS o, member_email, request_id, outcome_key, "
            "act_kind, record_link FROM chat_card_record WHERE session_id = :s "
            "ORDER BY created_at, request_id"), {"s": sid}))


def _read(ns, org: str, sid: str, viewer: str):
    from acb_graph import tenant_session
    from gateway.rooms import SESSION_VISIBLE_SQL
    from orchestrator.card_record import read_cards

    with tenant_session(org) as s:
        return read_cards(s, sid, viewer=viewer, visible_sql=SESSION_VISIBLE_SQL)


# ── 1. The table ────────────────────────────────────────────────────────────


def test_1_the_table_has_its_checks_and_force_rls(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: r.is_nullable for r in c.execute(text(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'chat_card_record'"))}
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'chat_card_record'::regclass")).first()
        policy = c.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'chat_card_record'")).first()
    # No column can hold a title, a detail, a body or a tool output. Task 3594
    # (migration 460) adds the approved addresses and the time of a send.
    assert set(cols) == {"organization_id", "id", "session_id", "member_email",
                         "request_id", "outcome_key", "act_kind", "record_link", "message_id",
                         "recipients", "sent_at", "created_at"}
    assert cols["record_link"] == "YES"
    assert tuple(rls) == (True, True)
    assert "app.tenant_id" in policy.qual and "app.tenant_id" in policy.with_check


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_table_has_its_checks_and_force_rls(db)


@pytest.mark.parametrize("col,value", [
    ("outcome_key", "declined"),
    ("act_kind", "Send the mail to Veena now"),
    ("record_link", "Summary of the chat"),
    ("record_link", "https://evil.test/x"),
    ("member_email", "Alice@co17.test"),
    ("request_id", "not-a-card-id"),
])
def test_1_the_checks_refuse_text_and_a_decline(db, col, value) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    row = {"o": db.org_a, "s": sid, "m": _ALICE, "r": _rid(),
           "k": "approved", "a": "skill_projects.create_task", "l": None}
    row[{"outcome_key": "k", "act_kind": "a", "record_link": "l",
         "member_email": "m", "request_id": "r"}[col]] = value
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_card_record (organization_id, session_id, member_email, "
            "request_id, outcome_key, act_kind, record_link) VALUES "
            "(CAST(:o AS uuid), :s, :m, :r, :k, :a, :l)"), row)


# ── 2 and 3. One row for an approved card ───────────────────────────────────


def test_2_an_approved_card_writes_one_row_and_a_repeat_none(db) -> None:
    from orchestrator.card_record import insert_row

    sid, rid = _chat(db, org=db.org_a, owner=_ALICE), _rid()
    assert insert_row(db.org_a, session_id=sid, member=_ALICE.upper(), request_id=rid,
                      act_kind="skill_projects.create_task") is True
    assert insert_row(db.org_a, session_id=sid, member=_ALICE, request_id=rid,
                      act_kind="skill_projects.create_task") is False
    [row] = _rows(db, sid)
    assert (row.o, row.member_email, row.request_id, row.outcome_key, row.act_kind,
            row.record_link) == (db.org_a, _ALICE, rid, "approved",
                                 "skill_projects.create_task", None)


def test_2_a_bad_argument_writes_nothing(db) -> None:
    from orchestrator.card_record import insert_row

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    for kw in ({"request_id": "x"}, {"act_kind": "Not a kind"}, {"member": " "}):
        args = {"session_id": sid, "member": _ALICE, "request_id": _rid(),
                "act_kind": "a.b", **kw}
        assert insert_row(db.org_a, **args) is False
    assert _rows(db, sid) == []


# ── 4. The link ─────────────────────────────────────────────────────────────


def test_4_the_link_is_set_once_by_the_members_own_row_and_a_path_only(db) -> None:
    from orchestrator.card_record import insert_row, update_link

    sid, rid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,),
                     visibility="people"), _rid()
    insert_row(db.org_a, session_id=sid, member=_ALICE, request_id=rid, act_kind="a.b")
    kw = {"session_id": sid, "request_id": rid}
    assert update_link(db.org_a, member=_ALICE, link="Any free text", **kw) is False
    assert update_link(db.org_a, member=_ALICE, link="https://x.test/a", **kw) is False
    # Bob did not approve the card: his set changes nothing.
    assert update_link(db.org_a, member=_BOB, link=_LINK, **kw) is False
    assert update_link(db.org_a, member=_ALICE, link=_LINK, **kw) is True
    assert update_link(db.org_a, member=_ALICE, link="/other", **kw) is False
    assert [r.record_link for r in _rows(db, sid)] == [_LINK]


def test_4_the_message_id_is_a_token_set_once_and_answers_was_it_sent(db) -> None:
    from orchestrator.card_record import insert_row, update_message_id, was_sent

    sent, declined = _rid(), _rid()
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    insert_row(db.org_a, session_id=sid, member=_ALICE, request_id=sent, act_kind="a.send")
    kw = {"session_id": sid, "member": _ALICE, "request_id": sent}
    assert update_message_id(db.org_a, message_id="Dear Veena, the offer is", **kw) is False
    assert update_message_id(db.org_a, message_id="<m1@mail.test>", **kw) is True
    assert update_message_id(db.org_a, message_id="<m2@mail.test>", **kw) is False
    rows = _read(db, db.org_a, sid, _ALICE)
    assert was_sent(rows, sent) is True
    # A declined card wrote no row: the answer is no.
    assert was_sent(rows, declined) is False


# ── 5. Who reads ────────────────────────────────────────────────────────────


def test_5_a_colleague_in_the_same_room_reads_none(db) -> None:
    from orchestrator.card_record import insert_row

    sid, rid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,),
                     visibility="people"), _rid()
    insert_row(db.org_a, session_id=sid, member=_ALICE, request_id=rid, act_kind="a.b")
    [mine] = _read(db, db.org_a, sid, _ALICE)
    assert mine["request_id"] == rid and mine["act_kind"] == "a.b"
    assert _read(db, db.org_a, sid, _BOB) == []
    # A member who cannot read the chat reads none, whoever the row names.
    assert _read(db, db.org_a, sid, "stranger@co17.test") == []


# ── 6. Another org ──────────────────────────────────────────────────────────


def test_6_org_b_neither_reads_nor_writes_a_row_of_org_a(db) -> None:
    from orchestrator.card_record import insert_row, update_link

    sid, rid = _chat(db, org=db.org_a, owner=_ALICE), _rid()
    insert_row(db.org_a, session_id=sid, member=_ALICE, request_id=rid, act_kind="a.b")
    assert insert_row(db.org_b, session_id=sid, member=_CAROL, request_id=_rid(),
                      act_kind="a.b") is False
    assert update_link(db.org_b, session_id=sid, member=_ALICE, request_id=rid,
                       link=_LINK) is False
    assert _read(db, db.org_b, sid, _ALICE) == []
    assert [r.record_link for r in _rows(db, sid)] == [None]
    assert len(_rows(db, sid)) == 1


# ── 7. A Delete of the chat ─────────────────────────────────────────────────


def test_7_a_delete_of_the_chat_deletes_its_rows(db) -> None:
    from orchestrator.card_record import insert_row

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    insert_row(db.org_a, session_id=sid, member=_ALICE, request_id=_rid(), act_kind="a.b")
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": sid})
    assert _rows(db, sid) == []


# ── 8. The member purge ─────────────────────────────────────────────────────


def test_8_the_purge_clause_takes_the_members_rows_in_the_bound_org_only(db) -> None:
    from acb_graph import tenant_session
    from gateway.routes.admin.members import _PURGE_DELETES
    from orchestrator.card_record import insert_row

    [rows] = [r for r in _PURGE_DELETES if r.table == "chat_card_record"]
    room = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    other = _chat(db, org=db.org_b, owner=_CAROL)
    insert_row(db.org_a, session_id=room, member=_ALICE, request_id=_rid(), act_kind="a.b")
    insert_row(db.org_a, session_id=room, member=_BOB, request_id=_rid(), act_kind="a.b")
    insert_row(db.org_b, session_id=other, member=_CAROL, request_id=_rid(), act_kind="a.b")
    with tenant_session(db.org_a) as s:
        s.execute(text(f"DELETE FROM {rows.table} WHERE {rows.where}"), {"email": _ALICE})
    assert [r.member_email for r in _rows(db, room)] == [_BOB]
    assert len(_rows(db, other)) == 1
