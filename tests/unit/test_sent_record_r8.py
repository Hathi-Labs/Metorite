"""R8: the sent-mail record of a chat (task 3594).

Spec: ``project-docs/specs/chat_organization.md`` §16.3. Module:
``orchestrator/card_record.py`` (``update_sent``, ``read_sent_acts``).
Migration: ``infra/postgres/<n>_chat_card_record_recipients.sql``, found by
its content. The hermetic half is ``test_sent_record.py``.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the two new columns, and the CHECK of ``recipients``. A second run of the
   migration changes nothing;
2. the send is set once, by the member's own row, with a token and clean
   addresses only;
3. another member and another org read nothing;
4. only the last 5 sent acts come back, newest last, and an approved card
   whose send failed is not one.

Mutations, each red here: drop ``message_id IS NULL`` from ``_SENT_SQL``
(case 2); drop ``member_email = :member`` from ``read_sent_acts`` (case 3);
drop ``message_id IS NOT NULL`` from ``read_sent_acts`` (case 4); drop the
``reversed`` of ``read_sent_acts`` (case 4).
"""

from __future__ import annotations

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

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
_ALICE = "alice@s3594.test"
_BOB = "bob@s3594.test"
_CAROL = "carol@s3594.test"  # org B
_MARK = "ADD COLUMN IF NOT EXISTS recipients TEXT[]"


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
    sid = f"s3594-{uuid.uuid4().hex[:12]}"
    ns.made.append(sid)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "visibility, organization_id) VALUES (:id, :u, 'orchestrator', 'Sends', 2, "
            ":v, CAST(:o AS uuid))"), {"id": sid, "u": owner, "v": visibility, "o": org})
        for subject, role in ((owner, "owner"), *((m, "member") for m in members)):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": role, "o": org})
    return sid


def _rid() -> str:
    return uuid.uuid4().hex


def _approved(ns, org: str, sid: str, member: str) -> str:
    from orchestrator.card_record import insert_row

    rid = _rid()
    assert insert_row(org, session_id=sid, member=member, request_id=rid,
                      act_kind="agents.send_email") is True
    return rid


def _read(ns, org: str, sid: str, viewer: str):
    from acb_graph import tenant_session
    from gateway.rooms import SESSION_VISIBLE_SQL
    from orchestrator.card_record import read_sent_acts

    with tenant_session(org) as s:
        return read_sent_acts(s, sid, viewer=viewer, visible_sql=SESSION_VISIBLE_SQL)


def _row(ns, rid: str):
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT message_id, recipients, sent_at FROM chat_card_record "
            "WHERE request_id = :r"), {"r": rid}).first()


# ── 1. The columns and the CHECK ────────────────────────────────────────────


def test_1_the_new_columns_are_nullable_and_hold_no_text(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: (r.is_nullable, r.data_type) for r in c.execute(text(
            "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
            "WHERE table_name = 'chat_card_record'"))}
    assert cols["recipients"] == ("YES", "ARRAY")
    assert cols["sent_at"] == ("YES", "timestamp with time zone")
    for banned in ("subject", "body", "attachment", "file_name", "filename"):
        assert not any(banned in name for name in cols), banned


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_new_columns_are_nullable_and_hold_no_text(db)


@pytest.mark.parametrize("value", [
    ["Veena@x.test"],                      # a capital letter
    ["veena x.test"],                      # no @
    ["veena@x.test bob@y.test"],           # white space in one item
    ["a@x.test\tb"],                       # a tab
    [],                                    # empty
    ["a@x.test", None],                    # a NULL item
    [f"p{i}@x.test" for i in range(51)],   # more than 50
])
def test_1_the_check_refuses_text_in_recipients(db, value) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    rid = _approved(db, db.org_a, sid, _ALICE)
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_card_record SET recipients = CAST(:v AS text[]) "
                       "WHERE request_id = :r"), {"v": value, "r": rid})


def test_1_the_check_takes_fifty_clean_addresses(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE)
    rid = _approved(db, db.org_a, sid, _ALICE)
    value = [f"p{i}@x.test" for i in range(50)]
    with db.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_card_record SET recipients = CAST(:v AS text[]) "
                       "WHERE request_id = :r"), {"v": value, "r": rid})
    assert list(_row(db, rid).recipients) == value


# ── 2. Set once, by the member's own row ────────────────────────────────────


def test_2_the_send_is_set_once_by_the_members_own_row(db) -> None:
    from orchestrator.card_record import update_sent

    sid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    rid = _approved(db, db.org_a, sid, _ALICE)
    kw = {"session_id": sid, "request_id": rid}
    people = ["Veena@X.test", "bad address@x.test", "raj@y.test"]
    # A text id, no clean address, and another member write nothing.
    assert update_sent(db.org_a, member=_ALICE, message_id="Dear Veena", recipients=people,
                       **kw) is False
    assert update_sent(db.org_a, member=_ALICE, message_id="<m1@x>", recipients=["x y"],
                       **kw) is False
    assert update_sent(db.org_a, member=_BOB, message_id="<m1@x>", recipients=people,
                       **kw) is False
    assert update_sent(db.org_a, member=_ALICE, message_id="<m1@x>", recipients=people,
                       **kw) is True
    assert update_sent(db.org_a, member=_ALICE, message_id="<m2@x>", recipients=people,
                       **kw) is False
    row = _row(db, rid)
    assert (row.message_id, list(row.recipients)) == ("<m1@x>", ["veena@x.test", "raj@y.test"])
    assert row.sent_at is not None


# ── 3. Another member and another org ───────────────────────────────────────


def test_3_another_member_and_another_org_read_nothing(db) -> None:
    from orchestrator.card_record import update_sent

    sid = _chat(db, org=db.org_a, owner=_ALICE, members=(_BOB,), visibility="people")
    rid = _approved(db, db.org_a, sid, _ALICE)
    assert update_sent(db.org_a, session_id=sid, member=_ALICE, request_id=rid,
                       message_id="<m1@x>", recipients=["veena@x.test"]) is True
    [mine] = _read(db, db.org_a, sid, _ALICE)
    assert (mine["message_id"], list(mine["recipients"])) == ("<m1@x>", ["veena@x.test"])
    assert _read(db, db.org_a, sid, _BOB) == []
    assert _read(db, db.org_a, sid, "stranger@s3594.test") == []
    assert _read(db, db.org_b, sid, _ALICE) == []
    # Org B cannot set the send of a row of org A.
    rid2 = _approved(db, db.org_a, sid, _ALICE)
    assert update_sent(db.org_b, session_id=sid, member=_ALICE, request_id=rid2,
                       message_id="<m2@x>", recipients=["a@x.test"]) is False
    assert _row(db, rid2).message_id is None


# ── 4. The last 5 sent acts, newest last ────────────────────────────────────


def test_4_only_the_last_five_sent_acts_come_back_newest_last(db) -> None:
    from orchestrator.card_record import sent_acts_text, update_sent

    sid = _chat(db, org=db.org_a, owner=_ALICE)
    for i in range(7):
        rid = _approved(db, db.org_a, sid, _ALICE)
        assert update_sent(db.org_a, session_id=sid, member=_ALICE, request_id=rid,
                           message_id=f"<m{i}@x>", recipients=[f"p{i}@x.test"]) is True
        time.sleep(0.01)
    # An approved card whose send failed: no message id, so not a sent act.
    _approved(db, db.org_a, sid, _ALICE)
    acts = _read(db, db.org_a, sid, _ALICE)
    assert [a["message_id"] for a in acts] == [f"<m{i}@x>" for i in range(2, 7)]
    text_ = sent_acts_text(acts)
    assert text_.splitlines()[-2].endswith("message <m6@x>.")
