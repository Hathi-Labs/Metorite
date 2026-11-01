"""R8: the store of a chat summary and the solo SQL (WS-63 PZ-33a, D142).

Spec: ``project-docs/specs/personalization.md`` §15.3 and §15.9, items 6, 8,
9 and 10 of the PZ-33 list. Module: ``gateway/assistant_summaries.py``.
Migration: ``infra/postgres/<n>_chat_session_member_summary.sql``.

What this proves against a REAL Postgres (R8), as the NON-privileged role of
the phase-4 rehearsal (no superuser, no BYPASSRLS), on a database with the
whole ladder and two organizations:

- the column is a nullable JSONB with no default (R6);
- item 6: a mark, a write, a marked write and a Delete each leave
  ``updated_at``, ``last_preview``, ``message_count`` and the title as they
  were;
- item 9: a Delete (``off``) after the mark wins. The marked write has the
  mark in its WHERE, so it changes nothing. A newer mark wins the same way,
  and so does a Forget, which clears the summary and its mark;
- item 8: ``SOLO_ROOM_SQL`` and ``room_is_solo(resolve_room_access(...))``
  agree on each row: a NULL visibility, a mixed-case address, a ``group:``
  row, an ``org`` row and a chat with no participant row among them. The
  async seam runs the same SQL;
- item 10: the member purge deletes the summary with the private chat, and
  the chat of the same address in another org keeps its summary;
- the store reads and writes no chat of another org (R5);
- review round 1 (P2-a): Forget (``clear_for_member``) keeps a Delete
  ``off``, and leaves each other chat a waterline at its newest row, in the
  bound org only;
- review round 1 (P2-b): a chat that stops being solo through the room
  routes loses its summary (``clear_on_share``), and the purge clears the
  summary of a shared room of the member.

A hermetic fake agrees with any SQL that it gets, so none of this could be
proved without the database.

Mutations, each red here: the write also sets ``updated_at = now()`` (item
6); drop the mark from the WHERE of the marked write (item 9); make
``SOLO_ROOM_SQL`` ignore a ``group:`` row (item 8); drop the
``private_chat_sessions`` row of the purge (item 10); let Forget write NULL
(P2-a); skip ``clear_on_share`` in the room routes (P2-b).
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from acb_common.db import tenant_session
from gateway import assistant_summaries as summaries
from gateway.rooms import SOLO_ROOM_SQL, resolve_room_access, room_is_solo
from sqlalchemy import create_engine, text
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

ALICE = "alice@pz33a.test"
BOB = "bob@pz33a.test"
#: A chat row that is old, so a `now()` write would show.
OLD = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
NOW = datetime(2026, 10, 31, 10, 0, 0, tzinfo=UTC)
ANSWER = summaries.Answer(text="Planned the Q3 pricing deck.",
                          follow_ups=("Send the deck to Priya by Friday",))


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Alice and Bob in org A, seeded as the superuser."""
    with promoted.admin_engine.begin() as c:
        for email in (ALICE, BOB):
            c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) "
                "ON CONFLICT DO NOTHING"), {"e": email, "o": promoted.org_a})
        # Migration 222 grants the function to `acb_app` only. The rehearsal
        # role has a suite-private name, so it gets the same grant here.
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def db(world):
    """The world, with each chat that a test makes removed after it."""
    made: list[str] = []
    world.made = made
    yield world
    with world.admin_engine.begin() as c:
        for sid in made:
            c.execute(text("DELETE FROM chat_session WHERE id = :s"), {"s": sid})


@pytest.fixture
def graph_as_app(db, monkeypatch):
    """``acb_graph`` sessions open as the app role, one fresh backend each, so
    ``resolve_room_access`` reads under RLS as production does."""
    eng = create_engine(db.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield db
    finally:
        eng.dispose()


def _app_dsn(ns) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _chat(ns, *, org: str, user_id: str, visibility: str | None = "private",
          subjects: tuple[str, ...] = ()) -> str:
    """One chat with Mira, seeded as the superuser with an old `updated_at`."""
    sid = f"pz33a-{uuid.uuid4().hex[:12]}"
    ns.made.append(sid)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, last_preview, "
            "message_count, created_at, updated_at, visibility, organization_id) "
            "VALUES (:id, :u, 'orchestrator', 'Q3 deck', 'Sure.', 7, :t, :t, :v, "
            "CAST(:o AS uuid))"),
            {"id": sid, "u": user_id, "t": OLD, "v": visibility, "o": org})
        for i, subject in enumerate(subjects):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": "owner" if i == 0 else "member",
                 "o": org})
    return sid


def _message(ns, *, org: str, sid: str, mid: str, ts: int) -> None:
    """One row of the chat, seeded as the superuser."""
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "organization_id) VALUES (:id, :s, 'user', 'Plan the deck.', :ts, "
            "CAST(:o AS uuid))"), {"id": mid, "s": sid, "ts": ts, "o": org})


async def _summarise(org: str, sid: str, through: str = "m3") -> None:
    """The flow of the job: a mark, then the marked write."""
    assert await _in(org, summaries.mark, sid, through=through, at=NOW)
    assert await _in(org, summaries.write_marked, sid, ANSWER, through=through,
                     through_ms=1, at=NOW)


def _row(ns, sid: str) -> dict[str, Any] | None:
    """The chat row as the superuser sees it."""
    with ns.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT updated_at, last_preview, message_count, title, archived_at, "
            "member_summary FROM chat_session WHERE id = :s"), {"s": sid}).first()
    return None if row is None else dict(row._mapping)


def _kept(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["updated_at"], row["last_preview"], row["message_count"],
            row["title"], row["archived_at"])


async def _in(org: str, fn, *args, **kwargs):
    async with tenant_session(org) as session:
        return await fn(session, *args, **kwargs)


# ── R6: the column ──────────────────────────────────────────────────────────


def test_the_column_is_a_nullable_jsonb_with_no_default(db) -> None:
    with db.admin_engine.connect() as c:
        col = c.execute(text(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_name = 'chat_session' AND column_name = 'member_summary'")).first()
    assert col is not None, "the migration must add chat_session.member_summary"
    assert (col.data_type, col.is_nullable, col.column_default) == ("jsonb", "YES", None)


# ── Item 6: no write moves the chat ─────────────────────────────────────────


async def test_6_each_write_keeps_the_place_of_the_chat(db) -> None:
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    before = _row(db, sid)
    assert before is not None and before["member_summary"] is None
    states: list[Any] = []
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        states.append(_row(db, sid))
        assert await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                         through="m3", through_ms=1_761_900_000_000, at=NOW)
        states.append(_row(db, sid))
        assert await _in(db.org_a, summaries.mark, sid, through="m4", at=NOW)
        assert await _in(db.org_a, summaries.write_marked, sid,
                         summaries.Answer(text="Kept the old price."),
                         through="m4", through_ms=1_761_900_000_100, at=NOW)
        states.append(_row(db, sid))
        assert await _in(db.org_a, summaries.clear_for_member, ALICE) >= 1
        states.append(_row(db, sid))
        assert await _in(db.org_a, summaries.turn_off, sid)
        states.append(_row(db, sid))
    seen = [json.dumps(s["member_summary"], sort_keys=True) for s in states]
    assert len(set(seen)) == 5, "each step must change the summary"
    for state in states:
        assert _kept(state) == _kept(before), "a summary write moved the chat"


# ── The flow of the job: mark, then the marked write ───────────────────────


async def test_a_mark_and_then_the_marked_write_store_the_summary(db) -> None:
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.read, sid) is None
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        marked = await _in(db.org_a, summaries.read, sid)
        assert marked is not None and marked.mark_through == "m3" and marked.text == ""
        assert await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                         through="m3", through_ms=1_761_900_000_000, at=NOW)
        got = await _in(db.org_a, summaries.read, sid)
        # A later mark keeps the summary that is there.
        assert await _in(db.org_a, summaries.mark, sid, through="m5", at=NOW)
        again = await _in(db.org_a, summaries.read, sid)
    assert got == summaries.Stored(
        text=ANSWER.text, follow_ups=ANSWER.follow_ups, through="m3",
        through_ms=1_761_900_000_000, at="2026-10-31T10:00:00Z",
        mark_through="m3", mark_at="2026-10-31T10:00:00Z")
    assert (again.text, again.through, again.mark_through) == (ANSWER.text, "m3", "m5")
    assert _row(db, sid)["member_summary"]["v"] == 1


# ── Item 9: a Delete during the call wins ───────────────────────────────────


async def test_9_a_delete_after_the_mark_wins(db) -> None:
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        # The member deletes the summary while the model call runs.
        assert await _in(db.org_a, summaries.turn_off, sid)
        wrote = await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                          through="m3", through_ms=1_761_900_000_000, at=NOW)
        assert wrote is False
        # No later mark and no later write changes it either.
        assert await _in(db.org_a, summaries.mark, sid, through="m4", at=NOW) is False
        assert await _in(db.org_a, summaries.write_marked, sid, ANSWER, through="m4",
                         through_ms=1, at=NOW) is False
        got = await _in(db.org_a, summaries.read, sid)
    assert got == summaries.Stored(off=True)
    assert _row(db, sid)["member_summary"] == {"v": 1, "off": True}


async def test_9_a_forget_after_the_mark_wins_too(db) -> None:
    """Forget (PZ-35, §15.5) clears the summary and its mark, but it does not
    write ``off``. So only the mark in the WHERE stops the write of a model
    call that was running."""
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    _message(db, org=db.org_a, sid=sid, mid="m3", ts=1_761_900_000_000)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        assert await _in(db.org_a, summaries.clear_for_member, ALICE) >= 1
        wrote = await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                          through="m3", through_ms=1, at=NOW)
    assert wrote is False
    assert _row(db, sid)["member_summary"] == {
        "v": 1, "through": "m3", "through_ms": 1_761_900_000_000}


async def test_p2a_forget_keeps_a_delete_and_a_waterline_in_this_org_only(db) -> None:
    """A Delete stays ``off``, so it does not come back. Each other chat keeps
    only a waterline at its newest row, so no job summarises it again. The
    chats of another member and of another org keep their summaries."""
    deleted = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    kept = _chat(db, org=db.org_a, user_id=ALICE_CAPS, subjects=(ALICE,))
    never = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    empty = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    bobs = _chat(db, org=db.org_a, user_id=BOB, subjects=(BOB,))
    elsewhere = _chat(db, org=db.org_b, user_id=ALICE, subjects=(ALICE,))
    for sid, org in ((deleted, db.org_a), (kept, db.org_a), (never, db.org_a),
                     (bobs, db.org_a), (elsewhere, db.org_b)):
        _message(db, org=org, sid=sid, mid="m1", ts=100)
        _message(db, org=org, sid=sid, mid="m2", ts=200)
    async with tenant_engine_scope(_app_dsn(db)):
        for sid, org in ((deleted, db.org_a), (kept, db.org_a), (bobs, db.org_a),
                         (elsewhere, db.org_b)):
            await _summarise(org, sid)
        assert await _in(db.org_a, summaries.turn_off, deleted)
        count = await _in(db.org_a, summaries.clear_for_member, f"  {ALICE.upper()} ")
        after = {sid: await _in(db.org_a, summaries.read, sid)
                 for sid in (deleted, kept, never, bobs)}
        other = await _in(db.org_b, summaries.read, elsewhere)
    assert count == 3, "the three chats of the member with a row or a summary"
    assert after[deleted] == summaries.Stored(off=True)
    for sid in (kept, never):
        assert after[sid] == summaries.Stored(through="m2", through_ms=200), sid
    assert _row(db, empty)["member_summary"] is None, "a chat with nothing stays NULL"
    assert after[bobs].text == ANSWER.text and other.text == ANSWER.text
    with pytest.raises(summaries.SummaryShapeError):
        async with tenant_engine_scope(_app_dsn(db)):
            await _in(db.org_a, summaries.clear_for_member, " ")


async def test_9_a_newer_mark_wins_over_an_older_write(db) -> None:
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        assert await _in(db.org_a, summaries.mark, sid, through="m4", at=NOW)
        wrote = await _in(db.org_a, summaries.write_marked, sid, ANSWER,
                          through="m3", through_ms=1, at=NOW)
        got = await _in(db.org_a, summaries.read, sid)
    assert wrote is False
    assert (got.text, got.through, got.mark_through) == ("", None, "m4")


async def test_p3_forget_clears_a_chat_whose_user_id_has_outer_space(db) -> None:
    """The solo rule trims ``user_id`` (``SOLO_ROOM_SQL``), so a padded id is
    the member's chat. Forget must clear it too (review round 2, P3)."""
    sid = _chat(db, org=db.org_a, user_id=f"  {ALICE} ", subjects=(ALICE,))
    _message(db, org=db.org_a, sid=sid, mid="m1", ts=100)
    async with tenant_engine_scope(_app_dsn(db)):
        await _summarise(db.org_a, sid)
        assert await _in(db.org_a, summaries.clear_for_member, ALICE) >= 1
        got = await _in(db.org_a, summaries.read, sid)
    assert got == summaries.Stored(through="m1", through_ms=100)


async def test_a_bad_value_is_refused_before_any_sql(db) -> None:
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    async with tenant_engine_scope(_app_dsn(db)):
        with pytest.raises(summaries.SummaryShapeError):
            await _in(db.org_a, summaries.mark, sid, through="", at=NOW)
        with pytest.raises(summaries.SummaryShapeError):
            await _in(db.org_a, summaries.mark, sid, through="m3",
                      at=datetime(2026, 10, 31, 10, 0))
        with pytest.raises(summaries.SummaryShapeError):
            await _in(db.org_a, summaries.write_marked, sid,
                      {"text": "raw"},  # type: ignore[arg-type]
                      through="m3", through_ms=1, at=NOW)
    assert _row(db, sid)["member_summary"] is None


# ── R5: no chat of another org ──────────────────────────────────────────────


async def test_the_store_reaches_no_chat_of_another_org(db) -> None:
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        assert await _in(db.org_b, summaries.read, sid) is None
        assert await _in(db.org_b, summaries.mark, sid, through="m9", at=NOW) is False
        assert await _in(db.org_b, summaries.write_marked, sid, ANSWER, through="m3",
                         through_ms=1, at=NOW) is False
        assert await _in(db.org_b, summaries.turn_off, sid) is False
    stored = _row(db, sid)["member_summary"]
    assert stored["mark"]["through"] == "m3" and "text" not in stored and "off" not in stored


# ── Item 8: one solo rule, in two forms ─────────────────────────────────────

ALICE_CAPS = "Alice@PZ33A.Test"

#: (name, visibility, user_id, participant subjects, solo?). `None` is a NULL
#: visibility, which the column refuses since 138, so the case drops the NOT
#: NULL for its rows.
CASES: tuple[tuple[str, str | None, str, tuple[str, ...], bool], ...] = (
    ("no participant row", "private", ALICE, (), True),
    ("the owner row", "private", ALICE, (ALICE,), True),
    ("a mixed-case subject", "private", ALICE, (ALICE_CAPS,), True),
    ("a mixed-case user", "private", ALICE_CAPS, (ALICE,), True),
    ("a group row", "private", ALICE, (ALICE, "group:eng"), False),
    ("a group row only", "private", ALICE, ("group:eng",), False),
    ("an org row", "private", ALICE, (ALICE, "org"), False),
    ("another member", "private", ALICE, (ALICE, BOB), False),
    ("the owner removed", "private", ALICE, (BOB,), False),
    ("org visibility", "org", ALICE, (), False),
    ("people visibility", "people", ALICE, (ALICE,), False),
    ("a user id that is no address", "private", "default", (), False),
    ("one address in two cases", "private", ALICE, (ALICE, ALICE_CAPS), False),
    ("a NULL visibility, the owner row", None, ALICE, (ALICE,), True),
    ("a NULL visibility, no participant row", None, ALICE, (), True),
    ("a NULL visibility, a group row", None, ALICE, ("group:eng",), False),
)


@contextlib.contextmanager
def _nullable_visibility(ns):
    """Let `chat_session.visibility` hold NULL for one test. The suite owns
    its database, and the NOT NULL comes back after."""
    with ns.admin_engine.begin() as c:
        c.execute(text("ALTER TABLE chat_session ALTER COLUMN visibility DROP NOT NULL"))
    try:
        yield
    finally:
        with ns.admin_engine.begin() as c:
            c.execute(text("DELETE FROM chat_session WHERE visibility IS NULL"))
            c.execute(text("ALTER TABLE chat_session ALTER COLUMN visibility SET NOT NULL"))


def _solo_sql(ns, org: str, sid: str) -> bool:
    eng = create_engine(ns.app_url, poolclass=NullPool, future=True)
    try:
        with eng.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": org})
            return c.execute(text(
                f"SELECT 1 FROM chat_session s WHERE s.id = :sid AND {SOLO_ROOM_SQL}"),
                {"sid": sid}).first() is not None
    finally:
        eng.dispose()


@pytest.mark.parametrize(("name", "visibility", "user_id", "subjects", "solo"),
                         CASES, ids=[c[0] for c in CASES])
def test_8_the_solo_sql_agrees_with_room_is_solo(graph_as_app, name, visibility,
                                                 user_id, subjects, solo) -> None:
    ns = graph_as_app
    scope = _nullable_visibility(ns) if visibility is None else contextlib.nullcontext()
    with scope:
        sid = _chat(ns, org=ns.org_a, user_id=user_id, visibility=visibility,
                    subjects=subjects)
        by_python = room_is_solo(resolve_room_access(sid, user_id, organization_id=ns.org_a))
        by_sql = _solo_sql(ns, ns.org_a, sid)
    assert by_python == by_sql, f"{name}: room_is_solo={by_python}, SOLO_ROOM_SQL={by_sql}"
    # Both forms could share one fault, so each case also states the rule.
    assert by_sql is solo, name


async def test_8_the_solo_sql_selects_through_the_async_seam(db) -> None:
    """The seed of PZ-33b selects with the SQL through ``acb_common.db``."""
    legacy = _chat(db, org=db.org_a, user_id=ALICE)
    owner = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    shared = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE, "group:eng"))
    elsewhere = _chat(db, org=db.org_b, user_id=ALICE)
    async with tenant_engine_scope(_app_dsn(db)), tenant_session(db.org_a) as session:
        rows = (await session.execute(text(
            f"SELECT s.id FROM chat_session s WHERE s.id IN (:a, :b, :c, :d) "
            f"AND {SOLO_ROOM_SQL} ORDER BY s.id"),
            {"a": legacy, "b": owner, "c": shared, "d": elsewhere})).fetchall()
    assert [r.id for r in rows] == sorted([legacy, owner])


# ── Review round 1, P2-b: a chat that stops being solo loses its summary ───


async def test_p2b_a_chat_that_gets_a_second_party_loses_its_summary(graph_as_app) -> None:
    from gateway.routes import rooms as room_routes

    ns = graph_as_app
    joined = _chat(ns, org=ns.org_a, user_id=ALICE, subjects=(ALICE,))
    shown = _chat(ns, org=ns.org_a, user_id=ALICE, subjects=(ALICE,))
    people = _chat(ns, org=ns.org_a, user_id=ALICE, subjects=(ALICE,))
    floor = _chat(ns, org=ns.org_a, user_id=ALICE, subjects=(ALICE,))
    deleted = _chat(ns, org=ns.org_a, user_id=ALICE, subjects=(ALICE,))
    for sid in (joined, shown, people, floor, deleted):
        _message(ns, org=ns.org_a, sid=sid, mid="m3", ts=300)
    async with tenant_engine_scope(_app_dsn(ns)):
        for sid in (joined, shown, people, floor, deleted):
            await _summarise(ns.org_a, sid)
        assert await _in(ns.org_a, summaries.turn_off, deleted)
        # The route helpers, as `add_participant` and `patch_room` call them.
        room_routes._add_participant(joined, BOB, "member", ALICE, waterline_ms=1,
                                     organization_id=ns.org_a)
        room_routes._update_room(shown, {"visibility": "org"}, ns.org_a)
        room_routes._set_visibility(people, "people", ns.org_a)
        room_routes._update_room(floor, {"floor_mode": "driver"}, ns.org_a)
        room_routes._add_participant(deleted, BOB, "member", ALICE, waterline_ms=1,
                                     organization_id=ns.org_a)
        got = {sid: await _in(ns.org_a, summaries.read, sid)
               for sid in (joined, shown, people, floor, deleted)}
    for sid in (joined, shown, people):
        assert got[sid] == summaries.Stored(through="m3", through_ms=300), sid
    assert got[floor].text == ANSWER.text, "a solo chat keeps its summary"
    assert got[deleted] == summaries.Stored(off=True)


# ── Item 10: the purge takes the summary with the private chat ─────────────


@pytest.fixture
def purge_world(db, monkeypatch):
    """An owner in org A who survives the purge, and the off-Postgres hops
    stubbed, as ``test_assistant_profile_offboarding_r8.py`` does."""
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
    leaver = f"leaver.{uuid.uuid4().hex[:8]}@pz33a.test"
    with db.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid))"),
            {"e": leaver, "o": db.org_a})
    db.leaver = leaver
    yield db
    with db.admin_engine.begin() as c:
        c.execute(text("DELETE FROM app_user WHERE lower(email) = :e"), {"e": leaver})


async def test_10_the_purge_deletes_the_summary_with_the_private_chat(purge_world) -> None:
    from gateway.routes.admin._common import get_member
    from gateway.routes.admin.members import purge_member_rows

    ns = purge_world
    mine = _chat(ns, org=ns.org_a, user_id=ns.leaver, subjects=(ns.leaver,))
    other_org = _chat(ns, org=ns.org_b, user_id=ns.leaver, subjects=(ns.leaver,))
    bobs = _chat(ns, org=ns.org_a, user_id=BOB, subjects=(BOB,))
    # A summary written while the room was solo, and the room then shared by
    # a path that bypasses the room routes. The purge keeps the room.
    shared = _chat(ns, org=ns.org_a, user_id=ns.leaver, subjects=(ns.leaver,))
    async with tenant_engine_scope(_app_dsn(ns)):
        for org, sid in ((ns.org_a, mine), (ns.org_b, other_org), (ns.org_a, bobs),
                         (ns.org_a, shared)):
            await _summarise(org, sid)
        with ns.admin_engine.begin() as c:
            c.execute(text("UPDATE chat_session SET visibility = 'people' WHERE id = :s"),
                      {"s": shared})
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :b, 'member', CAST(:o AS uuid))"),
                {"s": shared, "b": BOB, "o": ns.org_a})
        async with tenant_session(ns.org_a) as session:
            member = await get_member(session, ns.org_a, ns.leaver)
            deleted, _kept, _plans = await purge_member_rows(
                session, ns.org_a, member, actor_email=ns.owner_email)
    assert deleted["private_chat_sessions"] == 1
    assert _row(ns, mine) is None, "the private chat and its summary must go"
    room = _row(ns, shared)
    assert room is not None, "the purge keeps a shared room"
    assert "text" not in room["member_summary"], "the purge must clear its summary"
    for sid in (other_org, bobs):
        kept = _row(ns, sid)
        assert kept is not None and kept["member_summary"]["text"] == ANSWER.text, sid
