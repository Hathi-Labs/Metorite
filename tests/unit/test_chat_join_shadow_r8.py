"""R8: the shadow join log (WS-CO CO-4, task 3503).

Spec: ``project-docs/specs/chat_organization.md`` §7, §10 and §11 row CO-4.
Module: ``gateway/chat_join_shadow.py``. Migration:
``infra/postgres/<n>_chat_join_log.sql``, found by its content.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), with two organizations:

1. the table: its key, its columns, FORCE RLS and its policy. A second run
   of the migration changes nothing. A CHECK refuses a reason that is not a
   code;
2. the first message of a new chat writes ONE row, and a second call writes
   none. A turn that is not the first message writes none;
3. the match reads the stored data: a chat with a record that the message
   names joins, an archived chat, a chat with a live run and a chat with an
   open ask carry with their bar, and nothing alike is clean;
4. a colleague's chat and the member's chat in another org are never a
   candidate;
5. org B never reads a row of org A, a call from org B on a chat of org A
   writes nothing, and the policy refuses a row stamped with another org;
6. a Delete of the new chat deletes its row (the cascade);
7. the member purge deletes each row of the member in the bound org, and
   keeps the rows of a colleague and of another org.

A hermetic fake agrees with any SQL that it gets, so none of this could be
proved without the database.

Mutations, each red here: drop ``{_FIRST_TURN}`` from ``INSERT_SQL`` (case
2); drop ``{_OWNER} = :member`` from ``CANDIDATES_SQL`` (case 4); drop
``ON CONFLICT`` (case 2); drop the ``chat_join_rows`` row of the purge
(case 7).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
_ALICE = "alice@co4.test"
_BOB = "bob@co4.test"
_CAROL = "carol@co4.test"  # org B
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS chat_join_log ("
_FIRST = "Send the Acme invoice draft to finance and check the totals"
_ALIKE = "Acme invoice draft finance totals check"


def _migration() -> Path:
    """The migration of the table, found by its content and not its number."""
    hits = [p for p in sorted((_ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


@pytest.fixture
def db(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The world with the switch on, and each chat that a test makes removed
    after it."""
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_join_shadow_orgs", "*", raising=False)
    made: list[str] = []
    promoted.made = made
    try:
        yield promoted
    finally:
        with promoted.admin_engine.begin() as c:
            for sid in made:
                c.execute(text("DELETE FROM reminders WHERE source_session_id = :s"), {"s": sid})
                c.execute(text("DELETE FROM chat_pending_ask WHERE thread_id = :s"), {"s": sid})
                c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": sid})


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _chat(ns, *, org: str, owner: str, title: str = "New chat", hours_ago: int = 1,
          users: int = 0, archived: bool = False, live_run: bool = False,
          members: tuple[str, ...] = ()) -> str:
    """One chat of Mira on the web, seeded as the superuser, with *users*
    member rows."""
    sid = f"co4-{uuid.uuid4().hex[:12]}"
    ns.made.append(sid)
    at = datetime.now(UTC) - timedelta(hours=hours_ago)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "visibility, organization_id, updated_at, archived_at) VALUES (:id, :u, "
            "'orchestrator', :t, :n, 'private', CAST(:o AS uuid), :at, :arch)"),
            {"id": sid, "u": owner, "t": title, "n": users, "o": org, "at": at,
             "arch": at if archived else None})
        for subject, role in ((owner, "owner"), *((m, "member") for m in members)):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": role, "o": org})
        for i in range(users):
            c.execute(text(
                "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                "organization_id, author_kind, author_email) VALUES (:id, :s, 'user', "
                "'Earlier words.', :ts, CAST(:o AS uuid), 'human', :e)"),
                {"id": f"{sid}-u{i}", "s": sid, "ts": int(at.timestamp() * 1000) + i,
                 "o": org, "e": owner})
        if live_run:
            c.execute(text(
                "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                "organization_id, author_kind, author_email, run_final_at) VALUES (:id, :s, "
                "'assistant', '', :ts, CAST(:o AS uuid), 'agent', 'orchestrator', NULL)"),
                {"id": f"{sid}-a", "s": sid, "ts": int(at.timestamp() * 1000) + 99, "o": org})
    return sid


def _open_ask(ns, sid: str, org: str, actor: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_pending_ask (organization_id, request_id, thread_id, "
            "actor_email, agent_name, kind) VALUES (CAST(:o AS uuid), :r, :t, :a, "
            "'orchestrator', 'questions')"),
            {"o": org, "r": uuid.uuid4().hex, "t": sid, "a": actor})


def _reminder(ns, sid: str, org: str, member: str, title: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO reminders (organization_id, member_email, title, fire_at, "
            "source_session_id) VALUES (CAST(:o AS uuid), :m, :t, now() + interval '1 day', "
            ":s)"), {"o": org, "m": member, "t": title, "s": sid})


def _rows(ns, sid: str) -> list[tuple]:
    with ns.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT organization_id::text, member_email, outcome, candidate_session_id, "
            "reason, would_join_blocked_by FROM chat_join_log WHERE session_id = :s"),
            {"s": sid})]


async def _first(ns, org: str, member: str, sid: str, words: str = _FIRST,
                 turn: str | None = None):
    from gateway.chat_join_shadow import fingerprint, record_first_message

    async with tenant_engine_scope(_app_dsn(ns)):
        return await record_first_message(
            org, member, sid, turn if turn is not None else f"{sid}-u0", fingerprint(words))


# ── 1. The table ────────────────────────────────────────────────────────────


def test_1_the_table_has_its_key_its_columns_and_force_rls(db) -> None:
    with db.admin_engine.connect() as c:
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'chat_join_log'"))}
        key = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'chat_join_log'::regclass AND contype = 'p'")).scalar()
        rls = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'chat_join_log'::regclass")).first()
        policy = c.execute(text(
            "SELECT qual, with_check FROM pg_policies "
            "WHERE tablename = 'chat_join_log'")).first()
    assert set(cols) == {
        "organization_id", "session_id", "member_email", "outcome",
        "candidate_session_id", "candidate_count", "score", "band", "record_hit",
        "candidate_idle_hours", "reason", "would_join_blocked_by", "override_kind",
        "overridden_at", "created_at"}
    assert cols["override_kind"] == ("text", "YES")
    assert cols["overridden_at"] == ("timestamp with time zone", "YES")
    assert key == "PRIMARY KEY (session_id)"
    assert tuple(rls) == (True, True)
    assert "app.tenant_id" in policy.qual and "app.tenant_id" in policy.with_check


def test_1_the_migration_runs_twice(db) -> None:
    sql = _migration().read_text(encoding="utf-8")
    auto = db.admin_engine.execution_options(isolation_level="AUTOCOMMIT")
    with auto.connect() as c, c.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)
    test_1_the_table_has_its_key_its_columns_and_force_rls(db)


def test_1_a_check_refuses_free_text_as_a_reason(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    with pytest.raises(DBAPIError), db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_join_log (organization_id, session_id, member_email, outcome, "
            "band, reason) VALUES (CAST(:o AS uuid), :s, 'alice@co4.test', 'clean', 'none', "
            "'Send the Acme invoice')"), {"o": db.org_a, "s": sid})


# ── 2. One row for each new chat ────────────────────────────────────────────


async def test_2_the_first_message_writes_one_row(db) -> None:
    old = _chat(db, org=db.org_a, owner=_ALICE, title=_ALIKE, users=2, hours_ago=5)
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    d = await _first(db, db.org_a, _ALICE, new)
    assert d is not None and (d.outcome, d.candidate_id) == ("join", old)
    assert _rows(db, new) == [(db.org_a, _ALICE, "join", old, "same_words", None)]
    # A second call for the same chat writes nothing.
    assert await _first(db, db.org_a, _ALICE, new) is None
    assert len(_rows(db, new)) == 1


async def test_2_a_turn_that_is_not_the_first_writes_nothing(db) -> None:
    sid = _chat(db, org=db.org_a, owner=_ALICE, users=2)
    assert await _first(db, db.org_a, _ALICE, sid, turn=f"{sid}-u1") is None
    assert _rows(db, sid) == []


async def test_2_an_address_in_mixed_case_is_the_same_member(db) -> None:
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    assert await _first(db, db.org_a, " Alice@CO4.test ", new) is not None
    assert _rows(db, new)[0][1] == _ALICE


# ── 3. The match reads the stored data ──────────────────────────────────────


async def test_3_a_record_joins_and_the_bars_carry(db) -> None:
    rec = _chat(db, org=db.org_a, owner=_ALICE, title="Weekly notes", users=1, hours_ago=30)
    _reminder(db, rec, db.org_a, _ALICE, "Acme invoice draft")
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    d = await _first(db, db.org_a, _ALICE, new)
    assert d is not None and (d.outcome, d.reason, d.candidate_id, d.record_hit) == (
        "join", "same_record", rec, True)


@pytest.mark.parametrize("bar", ["archived", "live_run", "open_ask", "not_solo"])
async def test_3_a_chat_that_may_not_join_carries(db, bar: str) -> None:
    old = _chat(db, org=db.org_a, owner=_ALICE, title=_ALIKE, users=1, hours_ago=3,
                archived=bar == "archived", live_run=bar == "live_run",
                members=(_BOB,) if bar == "not_solo" else ())
    if bar == "open_ask":
        _open_ask(db, old, db.org_a, _ALICE)
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    d = await _first(db, db.org_a, _ALICE, new)
    assert d is not None
    assert _rows(db, new) == [(db.org_a, _ALICE, "carry", old, "blocked", bar)]


async def test_3_nothing_alike_is_clean(db) -> None:
    _chat(db, org=db.org_a, owner=_ALICE, title="Holiday rota", users=1, hours_ago=3)
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    assert await _first(db, db.org_a, _ALICE, new) is not None
    assert _rows(db, new) == [(db.org_a, _ALICE, "clean", None, "no_match", None)]


# ── 4. Only the member's own chats ──────────────────────────────────────────


async def test_4_a_colleague_s_chat_and_another_org_are_never_candidates(db) -> None:
    _chat(db, org=db.org_a, owner=_BOB, title=_ALIKE, users=1, hours_ago=2)
    _chat(db, org=db.org_b, owner=_ALICE, title=_ALIKE, users=1, hours_ago=2)
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    d = await _first(db, db.org_a, _ALICE, new)
    assert d is not None
    assert (d.outcome, d.reason, d.candidate_count) == ("clean", "no_candidates", 0)


async def test_4_a_colleague_cannot_log_a_chat_of_the_member(db) -> None:
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    assert await _first(db, db.org_a, _BOB, new) is None
    assert _rows(db, new) == []


# ── 5. Two orgs ─────────────────────────────────────────────────────────────


async def test_5_org_b_never_reads_or_writes_a_row_of_org_a(db) -> None:
    from acb_common.db import tenant_session

    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    assert await _first(db, db.org_a, _ALICE, new) is not None
    # A call from org B on the chat of org A writes nothing.
    other = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    assert await _first(db, db.org_b, _ALICE, other) is None
    assert _rows(db, other) == []
    async with tenant_engine_scope(_app_dsn(db)):
        async with tenant_session(db.org_b) as s:
            seen = (await s.execute(text(
                "SELECT count(*) FROM chat_join_log WHERE session_id = :s"),
                {"s": new})).scalar()
        assert seen == 0
        async with tenant_session(db.org_a) as s:
            assert (await s.execute(text(
                "SELECT count(*) FROM chat_join_log WHERE session_id = :s"),
                {"s": new})).scalar() == 1
        # The policy refuses a row stamped with another org, from the app role.
        with pytest.raises(DBAPIError):
            async with tenant_session(db.org_b) as s:
                await s.execute(text(
                    "INSERT INTO chat_join_log (organization_id, session_id, member_email, "
                    "outcome, band, reason) VALUES (CAST(:o AS uuid), :s, 'carol@co4.test', "
                    "'clean', 'none', 'no_match')"), {"o": db.org_a, "s": other})


# ── 6. The cascade ──────────────────────────────────────────────────────────


async def test_6_a_delete_of_the_chat_deletes_its_row(db) -> None:
    new = _chat(db, org=db.org_a, owner=_ALICE, users=1)
    assert await _first(db, db.org_a, _ALICE, new) is not None
    assert len(_rows(db, new)) == 1
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": new})
    assert _rows(db, new) == []


# ── 7. The member purge ─────────────────────────────────────────────────────


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
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": db.owner_email, "o": db.org_a})
        c.execute(text(
            "INSERT INTO user_role (user_id, role_id, organization_id) "
            "SELECT id, :r, CAST(:o AS uuid) FROM app_user WHERE email = :e "
            "ON CONFLICT DO NOTHING"),
            {"r": role, "e": db.owner_email, "o": db.org_a})
    leaver = f"leaver.{uuid.uuid4().hex[:8]}@co4.test"
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": leaver, "o": db.org_a})
    db.leaver = leaver
    yield db
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": leaver})


async def test_7_the_purge_deletes_the_rows_of_the_member_in_this_org(purge_world) -> None:
    from acb_common.db import tenant_session
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import purge_member_rows

    ns = purge_world
    # Her chat in a room that stays: the cascade of her private chats would
    # not reach it, so only the clause deletes its row.
    room = _chat(ns, org=ns.org_a, owner=ns.leaver, users=1)
    mine = _chat(ns, org=ns.org_a, owner=ns.leaver, users=1)
    bobs = _chat(ns, org=ns.org_a, owner=_BOB, users=1)
    other_org = _chat(ns, org=ns.org_b, owner=ns.leaver, users=1)
    for org, who, sid in ((ns.org_a, ns.leaver, room), (ns.org_a, ns.leaver, mine),
                          (ns.org_a, _BOB, bobs), (ns.org_b, ns.leaver, other_org)):
        assert await _first(ns, org, who, sid) is not None
    with ns.admin_engine.begin() as c:
        c.execute(text("UPDATE chat_session SET visibility = 'org' WHERE id = :s"),
                  {"s": room})
    async with tenant_engine_scope(_app_dsn(ns)), tenant_session(ns.org_a) as session:
        member = await get_member(session, ns.org_a, ns.leaver)
        deleted, _kept, _plans = await purge_member_rows(
            session, ns.org_a, member, actor_email=ns.owner_email)
    assert deleted["chat_join_rows"] == 2
    assert _rows(ns, room) == [] and _rows(ns, mine) == []
    assert len(_rows(ns, bobs)) == 1
    assert len(_rows(ns, other_org)) == 1


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_chat_join_log\.sql", _migration().name)
