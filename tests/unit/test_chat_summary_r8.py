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

PZ-33b adds the job (``gateway/chat_summary_job.py``, items 1 to 5). The
tests drive ``worker.run_org`` and the handler as ``test_job_queue.py`` does,
on a frozen clock, as the app role. Only the Console call is a fake
(``acb_auth.console_resolve.chat_completion_on_console``): the real
``acompletion_with_fallback``, ``completion_on_router`` and
``run_attribution`` run. So a call names the member, the app and the
feature as production does, and a 402 is the real ``RoutedRefusal``.

- item 1: an idle solo chat with Mira, with 2 typed rows, gets one summary
  and one call, as the member, with ``feature="assistant.chat_summary"``,
  ``thinking`` off and no session open during the call. An empty or cut
  answer logs a warning. The prompt holds the member's own typed words and
  nothing else: a token in a reply of Mira, a tool result, a sub-agent
  result, a card answer, a pasted mail and a row of another author never
  reaches the payload or the stored text (the owner, 2026-11-02);
- item 2: two seeds give one live job (the dedupe key), and a second tick
  and a retry make no second call;
- item 3: a mark with no result gives up ``SummaryUncertain``, and so does
  the claim that loses the mark. A 402 gives up ``OutOfCredits``, another
  fault and a call over the limit give up ``SummaryFailed``, and the mark
  stays. With the Router off the seed enqueues nothing, and a job gives up
  ``RouterOff`` with no call;
- item 4: a shared room, another agent, a mentioned agent, a row of another
  agent, WhatsApp, an archived chat, a chat that reads ``off``, a member
  whose switch is off, the flag off, a member who left and a chat with one
  typed row get no call, from the seed or from a job put in by hand. A
  waterline holds until the member types again, a card answer is no new
  turn, and a paid call that stores nothing leaves a waterline;
- item 5: org A never reads or writes a chat of org B. A payload with the id
  of a chat of org B finds no row.

Mutations, each red here: the write also sets ``updated_at = now()`` (item
6); drop the mark from the WHERE of the marked write (item 9); make
``SOLO_ROOM_SQL`` ignore a ``group:`` row (item 8); drop the
``private_chat_sessions`` row of the purge (item 10); let Forget write NULL
(P2-a); skip ``clear_on_share`` in the room routes (P2-b). PZ-33b: skip the
Router check of the handler (RouterOff); drop the dedupe key of the seed
(item 2); take ``SOLO_ROOM_SQL`` out of ``ELIGIBLE_SQL`` (item 4); store the
answer with no ``check_answer`` (item 1, the sensitive line); drop
``thinking`` from the call (item 1); let ``ELIGIBLE_SQL`` take any agent
(item 4); drop ``through`` from ``MARK_SQL`` (item 3); drop ``through_ms``
from the mark. Round 3 adds one mutant for each source of stranger text in
the prompt: a reply of Mira, a tool result, a sub-agent result, a card
answer, a pasted mail and a row of another author.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

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


async def test_9_a_second_mark_of_the_same_row_refuses(db) -> None:
    """Two claims can both read no mark. Only the first mark goes in, and
    the first stamp stays. A mark of a newer row still goes in."""
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    later = datetime(2026, 10, 31, 11, 0, 0, tzinfo=UTC)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW)
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=later) is False
        got = await _in(db.org_a, summaries.read, sid)
        assert (got.mark_through, got.mark_at) == ("m3", "2026-10-31T10:00:00Z")
        assert await _in(db.org_a, summaries.mark, sid, through="m4", at=later)


async def test_9_a_mark_of_a_row_that_through_covers_refuses(db) -> None:
    """A clear takes the mark away and leaves ``through``. So the mark must
    read ``through``, or a job would run on rows that the member forgot."""
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    _message(db, org=db.org_a, sid=sid, mid="m3", ts=1_761_900_000_000)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.clear_for_member, ALICE) >= 1
        assert await _in(db.org_a, summaries.mark, sid, through="m3", at=NOW) is False
        assert await _in(db.org_a, summaries.mark, sid, through="m4", at=NOW)
    assert _row(db, sid)["member_summary"]["mark"]["through"] == "m4"


async def test_9_a_mark_of_a_row_that_through_ms_covers_refuses(db) -> None:
    """A clear can name a newer row than the mark. Its time still covers the
    row of the mark, so the mark refuses. A newer row still takes a mark."""
    sid = _chat(db, org=db.org_a, user_id=ALICE, subjects=(ALICE,))
    _message(db, org=db.org_a, sid=sid, mid="m4", ts=2_000)
    async with tenant_engine_scope(_app_dsn(db)):
        assert await _in(db.org_a, summaries.clear_for_member, ALICE) >= 1
        for through, ms in (("m3", 1_000), ("m4", 2_000)):
            assert await _in(db.org_a, summaries.mark, sid, through=through, at=NOW,
                             through_ms=ms) is False, through
        assert await _in(db.org_a, summaries.mark, sid, through="m5", at=NOW, through_ms=3_000)


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


# ══ PZ-33b: the job ═════════════════════════════════════════════════════════

#: The frozen clock of the job tests, and the members of PZ-33b. Each address
#: has its own domain, so the teardown finds its rows.
T = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
JOB_DOMAIN = "pz33b.test"
ASHA = f"asha@{JOB_DOMAIN}"  # org A, the member of the chats
RAVI = f"ravi@{JOB_DOMAIN}"  # org A, the other party of a shared room
KIRAN = f"kiran@{JOB_DOMAIN}"  # org A, the switch "Keep chat summaries" off
LENA = f"lena@{JOB_DOMAIN}"  # org A, a member who left
OMAR = f"omar@{JOB_DOMAIN}"  # org B
#: The answer of the fake model: the member's own plan and a follow-up.
SUMMARY = {"text": "Planned the Q3 pricing deck for Priya.",
           "follow_ups": ["Send the deck to Priya by Friday"]}
#: What the member typed, what Mira answered, and a tool event. Only the
#: first two reach the prompt.
TYPED_1 = "Help me plan the Q3 pricing deck for Priya."
TYPED_2 = "Keep the old price for current customers. Send the deck to Priya by Friday."
REPLY = "Sure. Three tiers, and the old price stays."
TOOL_LEAK = "Account 556677889900 of the client"


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _checked_out() -> int:
    """The connections of the ``acb_common.db`` pool that are in use now."""
    from acb_common import db as seam

    return int(seam.get_engine().sync_engine.pool.checkedout())


class _Console:
    """``chat_completion_on_console``, the one fake: it records each call and
    answers as the Console does. ``status`` 402 is the refusal for credits.
    ``fault`` raises in place of an answer. ``content`` replaces the JSON of
    :attr:`answer`, and ``finish`` is the ``finish_reason`` of the answer.
    ``reply`` builds the answer from the messages, as a model that repeats
    what it reads would."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = 200
        self.fault: Exception | None = None
        self.answer: dict[str, Any] = dict(SUMMARY)
        self.content: str | None = None
        self.finish = "stop"
        self.hang = False
        self.reply: Any = None

    async def __call__(self, payload: dict[str, Any], **attribution: Any) -> tuple[int, Any]:
        self.calls.append({"payload": payload, "attribution": attribution,
                           "checked_out": _checked_out()})
        if self.hang:
            await asyncio.sleep(60)
        if self.fault is not None:
            raise self.fault
        if self.status != 200:
            return self.status, {"detail": "out of credits"}
        answer = self.answer if self.reply is None else self.reply(payload["messages"])
        return 200, {
            "id": f"cmpl-{len(self.calls)}", "model": "fake/model",
            "choices": [{"index": 0, "finish_reason": self.finish, "message": {
                "role": "assistant",
                "content": json.dumps(answer) if self.content is None else self.content,
            }}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }


def _identity(ns, email: str, org: str, *, status: str = "active") -> None:
    """A member in *org*: the ``app_user`` row and the identity rows that
    ``my_day_loop.departed`` reads with the cutover on."""
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', :s, CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
            {"e": email, "s": status, "o": org})
        c.execute(text("UPDATE app_user SET status = :s WHERE email = :e"),
                  {"e": email, "s": status})
        uid = c.execute(text(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e) "
            "ON CONFLICT (lower(email)) DO UPDATE SET updated_at = now() "
            "RETURNING id::text"), {"e": email}).scalar()
        c.execute(text(
            "INSERT INTO org_membership (organization_id, user_id, status, joined_at) "
            "VALUES (CAST(:o AS uuid), CAST(:u AS uuid), :s, now()) "
            "ON CONFLICT (organization_id, user_id) DO UPDATE SET status = EXCLUDED.status"),
            {"o": org, "u": uid, "s": status})


def _switch(ns, email: str, org: str, on: bool) -> None:
    """The member's stored switch, ``apps.chat.summaries`` (PZ-35)."""
    profile = {"apps": {"chat": {"summaries": on}}}
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": org, "u": email, "p": json.dumps(profile)})


@pytest.fixture
def job(graph_as_app, monkeypatch):
    """The job world: the flag on for both orgs, the Router on, the worker
    with the summary kind and its seed hook only, the identity cutover on,
    the frozen clock, and the fake Console. Teardown removes the jobs and
    the members of PZ-33b."""
    from acb_auth import console_resolve
    from acb_common import _log, get_settings
    from acb_common.settings import install_settings
    from acb_llm import routed
    from gateway import chat_summary_job
    from gateway.jobs import worker

    ns = graph_as_app
    original = get_settings()
    ns.flag = f"{ns.org_a},{ns.org_b}"

    def flags(orgs: str) -> None:
        install_settings(original.model_copy(update={
            "assistant_chat_summary_orgs": orgs, "assistant_profile_enabled": True,
            "assistant_profile_orgs": ""}))

    flags(ns.flag)
    ns.flags = flags
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    for name, value in (("_HANDLERS", {}), ("_PRUNE_SEEDED", {}), ("prune_hooks", []),
                        ("_SEEDED", {}), ("seed_hooks", [])):
        monkeypatch.setattr(worker, name, value)
    worker._stopping.clear()
    chat_summary_job.register_jobs()
    ns.router = {"on": True}
    monkeypatch.setattr(routed, "routing_is_on", lambda: ns.router["on"])
    ns.console = _Console()
    monkeypatch.setattr(console_resolve, "chat_completion_on_console", ns.console)
    vouch = getattr(_log, "_AUTOMATION_AGENT", None)
    if vouch is not None:
        vouch.set(None)
    ns.clock = {"now": T}
    monkeypatch.setattr(chat_summary_job, "_now", lambda: ns.clock["now"])
    for email in (ASHA, RAVI, KIRAN):
        _identity(ns, email, ns.org_a)
    _identity(ns, LENA, ns.org_a, status="removed")
    _identity(ns, OMAR, ns.org_b)
    _switch(ns, KIRAN, ns.org_a, False)
    try:
        yield ns
    finally:
        install_settings(original)
        with ns.admin_engine.begin() as c:
            c.execute(text("DELETE FROM jobs WHERE kind = :k"), {"k": summaries.KIND})
            c.execute(text("DELETE FROM user_settings WHERE user_id LIKE :d"),
                      {"d": f"%@{JOB_DOMAIN}"})
            c.execute(text(
                "DELETE FROM org_membership m USING user_identity ui "
                "WHERE m.user_id = ui.id AND ui.email LIKE :d"), {"d": f"%@{JOB_DOMAIN}"})
            c.execute(text("DELETE FROM user_identity WHERE email LIKE :d"),
                      {"d": f"%@{JOB_DOMAIN}"})
            c.execute(text("DELETE FROM app_user WHERE email LIKE :d"),
                      {"d": f"%@{JOB_DOMAIN}"})


def _job_chat(ns, *, org: str, member: str = ASHA, agent: str = "orchestrator",
              channel: str | None = None, archived: bool = False,
              subjects: tuple[str, ...] | None = None, typed: int = 2,
              newest: datetime = T - timedelta(hours=1),
              events: Any = None) -> tuple[str, str]:
    """One chat with its rows, seeded as the superuser: *typed* typed rows of
    the member, a pasted mail, a reply of Mira with a tool event, and Mira's
    newest reply, sealed. *events* replaces the tool events of that reply.
    Answers the chat id and the id of its newest row."""
    sid = f"pz33b-{uuid.uuid4().hex[:12]}"
    if events is None:
        events = [{"name": "find_tasks", "args": {}, "result": TOOL_LEAK}]
    ns.made.append(sid)
    rows: list[tuple[str, str, str, str | None, str | None, datetime]] = []
    for i, words in enumerate([TYPED_1, TYPED_2][:typed]):
        rows.append((f"m{i + 1}", "user", words, "human", member,
                     newest - timedelta(minutes=50 - 20 * i)))
    rows.append(("m9", "user", "From: priya@acme.example\nSubject: rates\n\nCall 9845012345.",
                 "human", member, newest - timedelta(minutes=5)))
    rows.append(("a1", "assistant", REPLY, "agent", agent, newest - timedelta(minutes=40)))
    rows.append(("a2", "assistant", "Done. I saved the plan.", "agent", agent, newest))
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, last_preview, "
            "message_count, created_at, updated_at, visibility, organization_id, channel, "
            "archived_at) VALUES (:id, :u, :a, 'Q3 deck', 'Done.', 5, :t, :t, 'private', "
            "CAST(:o AS uuid), :ch, :arc)"),
            {"id": sid, "u": member, "a": agent, "t": OLD, "o": org, "ch": channel,
             "arc": T - timedelta(days=1) if archived else None})
        for i, subject in enumerate((member,) if subjects is None else subjects):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": "owner" if i == 0 else "member",
                 "o": org})
        # Each chat has its own agent as the primary row, as migration 139 and
        # the run doors leave it. So this row alone must exclude no chat.
        c.execute(text(
            "INSERT INTO chat_session_agent (session_id, agent_name, role, organization_id) "
            "VALUES (:s, :a, 'primary', CAST(:o AS uuid))"), {"s": sid, "a": agent, "o": org})
        for mid, role, words, kind, author, at in rows:
            c.execute(text(
                "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                "organization_id, author_kind, author_email, tool_events, run_final_at) "
                "VALUES (:id, :s, :r, :c, :ts, CAST(:o AS uuid), :k, :e, CAST(:te AS jsonb), "
                ":fin)"),
                {"id": mid, "s": sid, "r": role, "c": words, "ts": _ms(at), "o": org,
                 "k": kind, "e": author,
                 "te": json.dumps(events if mid == "a1" else []),
                 "fin": at if role == "assistant" else None})
    return sid, "a2"


def _mention(ns, org: str, sid: str, agent: str = "crm-assistant") -> None:
    """Add a mentioned agent to the chat, as ``routes/rooms.py`` does. The
    chat stays solo: an agent is no participant."""
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_agent (session_id, agent_name, role, organization_id) "
            "VALUES (:s, :a, 'mentioned', CAST(:o AS uuid))"), {"s": sid, "a": agent, "o": org})


def _reply_of(ns, org: str, sid: str, agent: str = "crm-assistant") -> None:
    """A sealed row of another agent, with its text. The agent may have left
    the room: its row stays."""
    at = T - timedelta(hours=2)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "organization_id, author_kind, author_email, run_final_at) VALUES ('g1', :s, "
            "'assistant', 'CRM agent: Acme paid 4.2 lakh', :ts, CAST(:o AS uuid), 'agent', "
            ":a, :fin)"), {"s": sid, "ts": _ms(at), "o": org, "a": agent, "fin": at})


def _jobs(ns, sid: str | None = None) -> list[Any]:
    """The rows of the summary kind, the oldest first, as the superuser sees
    them. With *sid*, only the jobs of that chat."""
    with ns.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, state, attempts, last_error, payload, dedupe_key, "
            "organization_id::text AS org FROM jobs WHERE kind = :k ORDER BY created_at"),
            {"k": summaries.KIND}).fetchall()
    return [r for r in rows if sid is None or r.payload.get("session_id") == sid]


async def _tick(ns, org: str, now: datetime = T) -> Counter[str]:
    """One run of the worker for *org* at *now*: the seed hook, then the jobs."""
    from gateway.jobs import worker

    ns.clock["now"] = now
    async with tenant_engine_scope(_app_dsn(ns)):
        return await worker.run_org(org, now)


async def _put(ns, org: str, sid: str, through: str, now: datetime = T) -> str:
    """A job of the kind put in by hand, as a retry or an old job would be."""
    from gateway.jobs import queue

    async with tenant_engine_scope(_app_dsn(ns)), tenant_session(org) as db:
        out = await queue.enqueue(db, summaries.KIND, {"session_id": sid, "through": through},
                                  dedupe_key=f"test:{uuid.uuid4().hex[:8]}", now=now)
    return out.id


def _summary(ns, sid: str) -> Any:
    row = _row(ns, sid)
    return None if row is None else row["member_summary"]


# ── Item 1: one summary, one call, as the member ────────────────────────────


async def test_1_an_idle_solo_chat_gets_one_summary_and_one_call(job) -> None:
    sid, newest = _job_chat(job, org=job.org_a)
    before = _row(job, sid)
    counts = await _tick(job, job.org_a)
    assert counts == Counter(done=1)
    assert len(job.console.calls) == 1
    call = job.console.calls[0]
    who = call["attribution"]
    assert (who["member"], who["member_proven"], who["agent"], who["module_slug"]) == (
        ASHA, True, "assistant.chat_summary", "chat")
    assert call["payload"]["model"] == "tier-fast"
    assert call["payload"]["max_tokens"] == 300
    assert call["payload"]["response_format"] == {"type": "json_object"}
    # tier-fast thinks by default, and the reasoning spends the cap (P0).
    assert call["payload"]["thinking"] == {"type": "disabled"}
    assert call["checked_out"] == 0, "no session may be open during the model call"
    prompt = json.dumps(call["payload"]["messages"])
    assert TYPED_1 in prompt and TYPED_2 in prompt
    for leak in (REPLY, TOOL_LEAK, "9845012345", "priya@acme.example"):
        assert leak not in prompt, leak
    stored = _summary(job, sid)
    assert (stored["text"], stored["follow_ups"], stored["through"]) == (
        SUMMARY["text"], SUMMARY["follow_ups"], newest)
    assert stored["through_ms"] == _ms(T - timedelta(hours=1))
    assert stored["mark"]["through"] == newest
    assert _kept(_row(job, sid)) == _kept(before), "the job moved the chat"
    (row,) = _jobs(job, sid)
    assert row.state == "done" and row.payload == {"session_id": sid, "through": newest}
    assert row.dedupe_key == f"summary:{sid}:{newest}"


async def test_1_the_answer_goes_through_check_answer(job) -> None:
    """A sensitive line that the member did not type, and a long number of
    the pasted mail, never reach the column. The member's own line stays."""
    sid, _newest = _job_chat(job, org=job.org_a)
    job.console.answer = {
        "text": "Planned the Q3 pricing deck for Priya. Priya seems depressed. "
                "Call her on 98450 12345.",
        "follow_ups": ["Send the deck to Priya by Friday", "Ask about her salary"]}
    assert await _tick(job, job.org_a) == Counter(done=1)
    stored = _summary(job, sid)
    assert stored["text"] == "Planned the Q3 pricing deck for Priya."
    assert stored["follow_ups"] == ["Send the deck to Priya by Friday"]


async def test_1_an_empty_answer_at_the_cap_logs_a_warning_and_stores_nothing(job) -> None:
    """The model stopped at the cap, and ``content`` came back empty. The
    call was billed, so the job says so at warning. The mark stays."""
    sid, newest = _job_chat(job, org=job.org_a)
    job.console.content, job.console.finish = "", "length"
    with structlog.testing.capture_logs() as caps:
        assert await _tick(job, job.org_a) == Counter(done=1)
    bad = [c for c in caps if c.get("event") == "assistant.chat_summary_bad_answer"]
    assert len(bad) == 1 and bad[0]["log_level"] == "warning"
    assert (bad[0]["finish_reason"], bad[0]["chars"]) == ("length", 0)
    stored = _summary(job, sid)
    assert "text" not in stored and stored["mark"]["through"] == newest


async def test_1_a_cut_answer_logs_a_warning_with_no_word_of_it(job) -> None:
    sid, _newest = _job_chat(job, org=job.org_a)
    job.console.content, job.console.finish = '{"text": "Planned the Q3 pri', "length"
    with structlog.testing.capture_logs() as caps:
        assert await _tick(job, job.org_a) == Counter(done=1)
    bad = [c for c in caps if c.get("event") == "assistant.chat_summary_bad_answer"]
    assert len(bad) == 1 and "Planned" not in json.dumps(bad[0], default=str)
    assert "text" not in _summary(job, sid)


#: A token in each place where a stranger can write. A reply of Mira can
#: restate a mail or a tool answer, so it counts as a stranger's text too.
TOKENS = {
    "a reply of Mira": "Zqmirareply",
    "a tool result": "Zqtoolresult",
    "a sub-agent result": "Zqsubresult",
    "a card answer": "Zqcardquestion",
    "a pasted mail": "Zqpastedmail",
    "a row of another author": "Zqotherauthor",
}


def _row_by(ns, org: str, sid: str, mid: str, role: str, words: str, at: datetime,
            kind: str, author: str) -> None:
    """One sealed row of the chat by *author*."""
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "organization_id, author_kind, author_email, run_final_at) VALUES (:id, :s, :r, "
            ":c, :ts, CAST(:o AS uuid), :k, :e, :fin)"),
            {"id": mid, "s": sid, "r": role, "c": words, "ts": _ms(at), "o": org,
             "k": kind, "e": author, "fin": at})


def _echo(messages: list[dict[str, str]]) -> dict[str, Any]:
    """The answer of a model that repeats each token that it reads."""
    seen = [tok for tok in TOKENS.values() if tok in json.dumps(messages)]
    return {"text": "Planned the deck." + (f" Also {' '.join(seen)}." if seen else ""),
            "follow_ups": ["Send the deck to Priya by Friday"]}


async def test_1_the_prompt_holds_the_members_own_words_only(job) -> None:
    """The owner, 2026-11-02 (D-PZ-28). The fence reads the payload at the
    Console seam. No token of a stranger's place is in it, or in the stored
    text, and the member's short typed rows are."""
    from gateway.chat_recovery import compose_card_answer

    events = [{"name": "find_tasks", "args": {}, "result": f"Found {TOKENS['a tool result']}",
               "subAgentName": "projects-assistant",
               "subAgentTools": [{"name": "read_thread", "args": {},
                                  "result": f"Thread {TOKENS['a sub-agent result']}"}]}]
    sid, _newest = _job_chat(job, org=job.org_a, events=events)
    at = T - timedelta(hours=1, minutes=20)
    _row_by(job, job.org_a, sid, "x1", "assistant",
            f"Priya's mail says {TOKENS['a reply of Mira']}.", at, "agent", "orchestrator")
    _row_by(job, job.org_a, sid, "x2", "user",
            compose_card_answer(f"Send {TOKENS['a card answer']} now?", "APPROVE"),
            at + timedelta(minutes=1), "human", ASHA)
    _row_by(job, job.org_a, sid, "x3", "user",
            f"From: priya@acme.example\nSubject: {TOKENS['a pasted mail']}\n\nThe rates rise.",
            at + timedelta(minutes=2), "human", ASHA)
    _row_by(job, job.org_a, sid, "x4", "user", f"Note from {TOKENS['a row of another author']}.",
            at + timedelta(minutes=3), "human", RAVI)
    job.console.reply = _echo
    assert await _tick(job, job.org_a) == Counter(done=1)
    (call,) = job.console.calls
    payload = json.dumps(call["payload"])
    assert TYPED_1 in payload and TYPED_2 in payload, "the member's words must go in"
    stored = json.dumps(_summary(job, sid))
    for place, token in TOKENS.items():
        assert token not in payload, place
        assert token not in stored, place
    assert _summary(job, sid)["text"] == "Planned the deck."


# ── Item 2: one live job, and no second call ────────────────────────────────


async def test_2_a_second_seed_a_second_tick_and_a_retry_make_no_second_call(job) -> None:
    sid, newest = _job_chat(job, org=job.org_a)
    async with tenant_engine_scope(_app_dsn(job)):
        await summaries.seed(job.org_a, T)
        await summaries.seed(job.org_a, T + timedelta(minutes=15))
    assert len(_jobs(job, sid)) == 1, "the dedupe key keeps one live job"
    assert await _tick(job, job.org_a, T + timedelta(minutes=15)) == Counter(done=1)
    assert len(job.console.calls) == 1
    # The next bucket: the summary covers the newest row, so the seed adds nothing.
    assert await _tick(job, job.org_a, T + timedelta(minutes=30)) == Counter()
    # A retry of the same rows, put in by hand, settles with no call.
    await _put(job, job.org_a, sid, newest, T + timedelta(minutes=31))
    assert await _tick(job, job.org_a, T + timedelta(minutes=31)) == Counter(done=1)
    assert len(job.console.calls) == 1
    assert len(_jobs(job, sid)) == 2


async def test_2_the_seed_takes_at_most_its_cap_of_chats(job, monkeypatch) -> None:
    monkeypatch.setattr(summaries, "SUMMARY_SEED_MAX", 2)
    made = [_job_chat(job, org=job.org_a, newest=T - timedelta(hours=1, minutes=i))[0]
            for i in range(3)]
    async with tenant_engine_scope(_app_dsn(job)):
        await summaries.seed(job.org_a, T)
    assert sorted(r.payload["session_id"] for r in _jobs(job)) == sorted(made[:2])


# ── Item 3: the give-ups ────────────────────────────────────────────────────


def _given_up(ns, sid: str, name: str) -> None:
    (row,) = _jobs(ns, sid)
    assert row.state == "failed", row
    assert str(row.last_error).split(":", 1)[0] == name, row.last_error


async def test_3_a_mark_with_no_result_gives_up_uncertain(job) -> None:
    """The mark holds ``through_ms``, as the job writes it in production."""
    sid, newest = _job_chat(job, org=job.org_a)
    async with tenant_engine_scope(_app_dsn(job)):
        assert await _in(job.org_a, summaries.mark, sid, through=newest, at=T,
                         through_ms=_ms(T - timedelta(hours=1)))
    await _put(job, job.org_a, sid, newest)
    assert await _tick(job, job.org_a) == Counter(failed=1)
    _given_up(job, sid, "SummaryUncertain")
    assert job.console.calls == []
    assert _summary(job, sid)["mark"]["through"] == newest


async def test_3_the_claim_that_loses_the_mark_makes_no_call(job, monkeypatch) -> None:
    """A lost lease: the old claim and the new one both read no mark. The old
    claim marks first, so the new claim gives up and makes no second call."""
    from gateway import chat_summary_job

    sid, newest = _job_chat(job, org=job.org_a)
    real = chat_summary_job.read_job_chat

    async def read_then_lose(db: Any, session_id: str) -> Any:
        chat = await real(db, session_id)
        async with tenant_session(job.org_a) as other:
            assert await summaries.mark(other, session_id, through=newest, at=T)
        return chat

    monkeypatch.setattr(chat_summary_job, "read_job_chat", read_then_lose)
    assert await _tick(job, job.org_a) == Counter(failed=1)
    _given_up(job, sid, "SummaryUncertain")
    assert job.console.calls == []


async def test_3_a_forget_before_the_mark_wins(job, monkeypatch) -> None:
    """The verifier's probe. A Forget lands after the handler read the chat
    and before its mark. The clear leaves ``through``, so the mark refuses:
    no call runs, and the forgotten rows are not stored."""
    from gateway import chat_summary_job

    sid, newest = _job_chat(job, org=job.org_a)
    real = chat_summary_job.read_job_chat

    async def read_then_forget(db: Any, session_id: str) -> Any:
        chat = await real(db, session_id)
        async with tenant_session(job.org_a) as other:
            assert await summaries.clear_for_member(other, ASHA) >= 1
        return chat

    monkeypatch.setattr(chat_summary_job, "read_job_chat", read_then_forget)
    assert await _tick(job, job.org_a) == Counter(done=1)
    assert job.console.calls == []
    stored = _summary(job, sid)
    assert stored["through"] == newest and "text" not in stored and "mark" not in stored


async def test_3_a_forget_behind_a_new_row_wins_too(job, monkeypatch) -> None:
    """The review's probe ``test_r3_5``. A new row comes, then a Forget, both
    after the handler read the chat. The clear names the new row, so the row
    ids differ, and only the time of the waterline stops the mark."""
    from gateway import chat_summary_job

    sid, _newest = _job_chat(job, org=job.org_a)
    real = chat_summary_job.read_job_chat

    async def read_then_row_then_forget(db: Any, session_id: str) -> Any:
        chat = await real(db, session_id)
        _row_at(job, job.org_a, sid, "m20", "user", "Hello again.", T)
        async with tenant_session(job.org_a) as other:
            assert await summaries.clear_for_member(other, ASHA) >= 1
        return chat

    monkeypatch.setattr(chat_summary_job, "read_job_chat", read_then_row_then_forget)
    assert await _tick(job, job.org_a) == Counter(done=1)
    assert job.console.calls == []
    stored = _summary(job, sid)
    assert stored["through"] == "m20" and "text" not in stored and "mark" not in stored


async def test_3_a_402_gives_up_out_of_credits_and_the_mark_stays(job) -> None:
    sid, newest = _job_chat(job, org=job.org_a)
    job.console.status = 402
    assert await _tick(job, job.org_a) == Counter(failed=1)
    _given_up(job, sid, "OutOfCredits")
    assert len(job.console.calls) == 1
    stored = _summary(job, sid)
    assert stored["mark"]["through"] == newest and "text" not in stored
    # The mark stays, so the next tick does not try the same rows again.
    assert await _tick(job, job.org_a, T + timedelta(minutes=15)) == Counter()
    assert len(job.console.calls) == 1


async def test_3_another_fault_gives_up_summary_failed_and_the_mark_stays(job) -> None:
    sid, newest = _job_chat(job, org=job.org_a)
    job.console.fault = RuntimeError("the Console is down")
    assert await _tick(job, job.org_a) == Counter(failed=1)
    _given_up(job, sid, "SummaryFailed")
    assert _summary(job, sid)["mark"]["through"] == newest


async def test_3_a_call_over_the_limit_gives_up_and_the_mark_stays(job, monkeypatch) -> None:
    """A slow vendor must not hold the shared worker for the 120 s of the
    Console client (the review of PZ-33b)."""
    from gateway import chat_summary_job

    monkeypatch.setattr(chat_summary_job, "CALL_TIMEOUT_S", 0.05)
    sid, newest = _job_chat(job, org=job.org_a)
    job.console.hang = True
    assert await _tick(job, job.org_a) == Counter(failed=1)
    _given_up(job, sid, "SummaryFailed")
    assert _summary(job, sid)["mark"]["through"] == newest


async def test_3_with_the_router_off_no_call_runs(job) -> None:
    """The seed enqueues nothing, and a job that exists gives up with
    ``RouterOff`` and makes no call (§15.3, "CODE")."""
    sid, newest = _job_chat(job, org=job.org_a)
    job.router["on"] = False
    assert await _tick(job, job.org_a) == Counter()
    assert _jobs(job, sid) == []
    await _put(job, job.org_a, sid, newest, T + timedelta(minutes=15))
    assert await _tick(job, job.org_a, T + timedelta(minutes=15)) == Counter(failed=1)
    _given_up(job, sid, "RouterOff")
    assert job.console.calls == []
    assert _summary(job, sid) is None, "no mark with the Router off"


# ── Item 4: the chats that get no summary ───────────────────────────────────


def _no_summary_cases(ns) -> dict[str, tuple[str, str]]:
    """Each chat that must get no call: the name, and the chat and its
    newest row."""
    return {
        "a shared room": _job_chat(ns, org=ns.org_a, subjects=(ASHA, RAVI)),
        "a group row": _job_chat(ns, org=ns.org_a, subjects=(ASHA, "group:eng")),
        "another agent": _job_chat(ns, org=ns.org_a, agent="crm-assistant"),
        "a WhatsApp chat": _job_chat(ns, org=ns.org_a, channel="whatsapp"),
        "an archived chat": _job_chat(ns, org=ns.org_a, archived=True),
        "the switch off": _job_chat(ns, org=ns.org_a, member=KIRAN),
        "a member who left": _job_chat(ns, org=ns.org_a, member=LENA),
        "one typed row": _job_chat(ns, org=ns.org_a, typed=1),
        "not idle yet": _job_chat(ns, org=ns.org_a, newest=T - timedelta(minutes=10)),
    }


async def test_4_no_chat_of_the_list_gets_a_call_from_the_seed_or_a_job(job) -> None:
    cases = _no_summary_cases(job)
    deleted, _ = _job_chat(job, org=job.org_a)
    async with tenant_engine_scope(_app_dsn(job)):
        assert await _in(job.org_a, summaries.turn_off, deleted)
    cases["a chat that reads off"] = (deleted, "a2")
    cases["a mentioned agent"] = _job_chat(job, org=job.org_a)
    _mention(job, job.org_a, cases["a mentioned agent"][0])
    cases["a row of another agent"] = _job_chat(job, org=job.org_a)
    _reply_of(job, job.org_a, cases["a row of another agent"][0])
    # The 7-day window bounds the reads of the seed only.
    _job_chat(job, org=job.org_a, newest=T - timedelta(days=8))
    assert await _tick(job, job.org_a) == Counter()
    assert _jobs(job) == [], "the seed must enqueue no job"
    # The handler holds each rule too: a job put in by hand makes no call.
    for sid, newest in cases.values():
        await _put(job, job.org_a, sid, newest, T + timedelta(minutes=15))
    counts = await _tick(job, job.org_a, T + timedelta(minutes=15))
    assert counts == Counter(done=len(cases)), counts
    assert job.console.calls == []
    for name, (sid, _newest) in cases.items():
        expected = {"v": 1, "off": True} if name == "a chat that reads off" else None
        assert _summary(job, sid) == expected, name


def _row_at(ns, org: str, sid: str, mid: str, role: str, words: str, at: datetime) -> None:
    """One more row of the chat with Mira, sealed when it is hers."""
    mine = role == "assistant"
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "organization_id, author_kind, author_email, run_final_at) VALUES (:id, :s, :r, "
            ":c, :ts, CAST(:o AS uuid), :k, :e, :fin)"),
            {"id": mid, "s": sid, "r": role, "c": words, "ts": _ms(at), "o": org,
             "k": "agent" if mine else "human", "e": "orchestrator" if mine else ASHA,
             "fin": at if mine else None})


async def test_4_a_waterline_holds_until_the_member_types_again(job) -> None:
    """A Delete of PZ-35 leaves the waterline of ``clear_for_member``. A row
    that only Mira wrote after it, such as a brief, starts no call. A new
    typed row starts a new summary of the rows after the waterline."""
    sid, newest = _job_chat(job, org=job.org_a)
    async with tenant_engine_scope(_app_dsn(job)):
        assert await _in(job.org_a, summaries.clear_for_member, ASHA) >= 1
    assert _summary(job, sid)["through"] == newest
    assert await _tick(job, job.org_a) == Counter(), "the waterline covers the chat"
    _row_at(job, job.org_a, sid, "a3", "assistant", "Good morning.", T + timedelta(minutes=5))
    later = T + timedelta(hours=1)
    assert await _tick(job, job.org_a, later) == Counter()
    await _put(job, job.org_a, sid, "a3", later)
    assert await _tick(job, job.org_a, later) == Counter(done=1)
    assert job.console.calls == [], "nothing new, so no call"
    _row_at(job, job.org_a, sid, "m10", "user", "Book the review with Priya.",
            later + timedelta(minutes=5))
    last = later + timedelta(hours=1)
    assert await _tick(job, job.org_a, last) == Counter(done=1)
    assert len(job.console.calls) == 1
    prompt = json.dumps(job.console.calls[0]["payload"]["messages"])
    assert "Book the review with Priya." in prompt and "Good morning." not in prompt
    assert TYPED_1 not in prompt, "a row that a Delete covered must not come back"
    assert _summary(job, sid)["through"] == "m10"


@pytest.mark.parametrize("bad", ["fault", "nothing_kept"])
async def test_4_a_paid_call_that_stores_nothing_needs_a_new_typed_row(job, bad) -> None:
    """The verifier measured 1, 2, 3, 4 calls in four days with nothing
    stored, because each new row of Mira moved the newest row. The mark keeps
    the waterline, so daily spend is bounded by what members type."""
    sid, newest = _job_chat(job, org=job.org_a)
    if bad == "fault":
        job.console.fault = RuntimeError("the vendor is down")
    else:
        job.console.content = "not json"
    await _tick(job, job.org_a)
    assert len(job.console.calls) == 1
    stored = _summary(job, sid)
    assert stored["mark"]["through"] == newest and "through_ms" not in stored
    assert stored["mark"]["through_ms"] == _ms(T - timedelta(hours=1))
    job.console.fault, job.console.content = None, None
    for day in range(1, 4):
        at = T + timedelta(days=day)
        _row_at(job, job.org_a, sid, f"b{day}", "assistant", "Good morning.",
                at - timedelta(hours=1))
        assert await _tick(job, job.org_a, at) == Counter()
    await _put(job, job.org_a, sid, "b3", T + timedelta(days=3, minutes=1))
    assert await _tick(job, job.org_a, T + timedelta(days=3, minutes=1)) == Counter(done=1)
    assert len(job.console.calls) == 1, "a row of Mira buys no call"
    _row_at(job, job.org_a, sid, "m10", "user", "Book the review with Priya.",
            T + timedelta(days=3, minutes=5))
    assert await _tick(job, job.org_a, T + timedelta(days=4)) == Counter(done=1)
    assert len(job.console.calls) == 2, "a new typed row buys one more"
    prompt = json.dumps(job.console.calls[1]["payload"]["messages"])
    assert TYPED_1 in prompt, "the retry reads the rows that no summary covers"
    assert _summary(job, sid)["through"] == "m10"


async def test_4_a_card_answer_after_the_waterline_is_no_new_turn(job) -> None:
    """The prompt holds the member's typed words only, so a card answer and a
    reply of Mira after a summary buy no call (the owner, 2026-11-02). Round
    2 counted a card answer, and that rule is gone."""
    from gateway.chat_recovery import compose_card_answer

    sid, _newest = _job_chat(job, org=job.org_a)
    assert await _tick(job, job.org_a) == Counter(done=1)
    _row_at(job, job.org_a, sid, "m11", "user", compose_card_answer("Send it?", "APPROVE"),
            T + timedelta(minutes=5))
    _row_at(job, job.org_a, sid, "a4", "assistant", "Sent.", T + timedelta(minutes=6))
    later = T + timedelta(hours=1)
    assert await _tick(job, job.org_a, later) == Counter(), "the seed adds no job"
    await _put(job, job.org_a, sid, "a4", later)
    assert await _tick(job, job.org_a, later) == Counter(done=1)
    assert len(job.console.calls) == 1, "the handler makes no call"


async def test_4_a_stored_switch_on_and_no_switch_read_as_on(job) -> None:
    """``apps.chat.summaries`` false turns the member's summaries off (above).
    True and a missing value read as on (PZ-35 declares the key later)."""
    _switch(job, RAVI, job.org_a, True)
    on, _ = _job_chat(job, org=job.org_a, member=RAVI)
    unset, _ = _job_chat(job, org=job.org_a, member=ASHA)
    assert await _tick(job, job.org_a) == Counter(done=2)
    assert _summary(job, on)["text"] == SUMMARY["text"]
    assert _summary(job, unset)["text"] == SUMMARY["text"]


async def test_4_the_flag_off_gives_no_job_and_no_call(job) -> None:
    sid, newest = _job_chat(job, org=job.org_a)
    job.flags(job.org_b)
    assert await _tick(job, job.org_a) == Counter()
    assert _jobs(job, sid) == []
    await _put(job, job.org_a, sid, newest, T + timedelta(minutes=15))
    assert await _tick(job, job.org_a, T + timedelta(minutes=15)) == Counter(done=1)
    assert job.console.calls == [] and _summary(job, sid) is None


# ── Item 5: org A never reaches a chat of org B ─────────────────────────────


async def test_5_org_a_never_reads_or_writes_a_chat_of_org_b(job) -> None:
    theirs, newest = _job_chat(job, org=job.org_b, member=OMAR)
    assert await _tick(job, job.org_a) == Counter()
    assert _jobs(job, theirs) == []
    # A payload of org A with the id of the chat of org B finds no row.
    await _put(job, job.org_a, theirs, newest, T + timedelta(minutes=15))
    assert await _tick(job, job.org_a, T + timedelta(minutes=15)) == Counter(done=1)
    assert job.console.calls == [] and _summary(job, theirs) is None
    # Org B summarises its own chat, as its own member.
    assert await _tick(job, job.org_b, T + timedelta(minutes=15)) == Counter(done=1)
    assert [c["attribution"]["member"] for c in job.console.calls] == [OMAR]
    assert _summary(job, theirs)["text"] == SUMMARY["text"]
    assert {r.org for r in _jobs(job, theirs)} == {job.org_a, job.org_b}
