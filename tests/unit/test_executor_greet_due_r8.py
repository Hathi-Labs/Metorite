"""R8 - the greeting decision reads ``chat_message`` on a REAL Postgres (PZ-39).

``executor._greet_read`` and ``_greet_decision`` run as the NON-privileged role
of the phase-4 rehearsal, inside ``tenant_engine_scope``. What this proves:

* a chat with no earlier message greets (first reply). The turn's own rows
  leave by ID (the minted reply and the member's prompt), so they never make
  the gap zero;
* **F1:** when the prompt write failed, the previous prompt is NOT taken for
  this turn's own. Two minutes into a conversation there is no greeting;
* a reply 1 h after the last message does not greet, and one 4 h after does;
* **F2:** a regenerate (an old prompt row, or no prompt id) never greets;
* **F3:** the gap uses the server's ``created_at``, so a browser clock in
  ``timestamp_ms`` cannot move it;
* the rows of ANOTHER org with the same chat id are not read (RLS), and a read
  bound to the member's OWN org does read the rows, so "bound to nobody" is red.

**Mutations (R7).** Exclude "the newest user row" in place of the ids (the
code of b700b7cb2): ``test_f1_a_failed_prompt_write_does_not_hide_the_previous_prompt``
goes red. Read ``timestamp_ms`` in place of ``created_at``:
``test_f3_the_browser_clock_cannot_move_the_gap`` goes red.

It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, release_tenant
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_buddy_hello_r8 import (  # noqa: F401
    _address,
    _dsn,
    _new_session,
    _seed,
    _unseed,
    graph_as_app,
    world,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _put(
    ns, sid: str, org: str, mid: str, role: str, content: str, age: timedelta,
    *, browser_ms: int | None = None,
) -> None:
    """A row made *age* before NOW. ``created_at`` is the server's time. The
    browser's ``timestamp_ms`` is set to NOW unless a test skews it."""
    at = NOW - age
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "created_at, organization_id) "
            "VALUES (:i, :s, :r, :c, :t, :a, CAST(:o AS uuid))"),
            {"i": mid, "s": sid, "r": role, "c": content, "a": at, "o": org,
             "t": browser_ms if browser_ms is not None else int(NOW.timestamp() * 1000)})


async def _decide(ns, org: str, sid: str, ids: tuple[str, ...], monkeypatch) -> bool:
    from orchestrator import executor as ex

    monkeypatch.setattr(ex, "_briefing_now", lambda: NOW)
    async with tenant_engine_scope(_dsn(ns)):
        token = bind_tenant(org)
        try:
            return await ex._greet_decision(org, sid, ids)
        finally:
            release_tenant(token)


def _chat(ns, tag: str):
    org, email = ns.org_a, _address(tag)
    _seed(ns, email, org, tag)
    sid = f"pz39-{uuid.uuid4().hex[:12]}"
    _new_session(sid, email, org)
    return org, email, sid


@pytest.mark.asyncio
async def test_a_first_reply_greets_and_the_turns_own_rows_do_not_count(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email, sid = _chat(ns, "first")
    try:
        _put(ns, sid, org, "u1", "user", "hello", timedelta(0))
        _put(ns, sid, org, "a1", "assistant", "", timedelta(0))
        assert await _decide(ns, org, sid, ("a1", "u1"), monkeypatch) is True
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_f1_a_failed_prompt_write_does_not_hide_the_previous_prompt(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    """u1 "hi" and a failed (empty) reply a1, both 2 minutes old. The minted
    reply a2 exists, and the prompt u2 was never stored. The old rule took u1
    for this turn's prompt and returned True, 2 minutes into a conversation."""
    ns = graph_as_app
    org, email, sid = _chat(ns, "f1")
    try:
        _put(ns, sid, org, "u1", "user", "hi", timedelta(minutes=2))
        _put(ns, sid, org, "a1", "assistant", "", timedelta(minutes=2))
        _put(ns, sid, org, "a2", "assistant", "", timedelta(0))
        assert await _decide(ns, org, sid, ("a2", "u2"), monkeypatch) is False
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_the_gap_edge_on_stored_rows(graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email, sid = _chat(ns, "edge")
    try:
        _put(ns, sid, org, "a0", "assistant", "answer", timedelta(hours=3, minutes=59))
        _put(ns, sid, org, "u1", "user", "now", timedelta(0))
        _put(ns, sid, org, "a1", "assistant", "", timedelta(0))
        assert await _decide(ns, org, sid, ("a1", "u1"), monkeypatch) is False
        with ns.admin_engine.begin() as c:
            c.execute(text("UPDATE chat_message SET created_at = :t WHERE id = 'a0' "
                           "AND session_id = :s"), {"t": NOW - timedelta(hours=4), "s": sid})
        assert await _decide(ns, org, sid, ("a1", "u1"), monkeypatch) is True
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_f2_a_regenerate_after_a_long_gap_does_not_greet(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    """u1 and a1 are 5 h old, and a new empty reply a1r exists. The prompt is
    the OLD u1 (a re-send), or there is no prompt id (a retry)."""
    ns = graph_as_app
    org, email, sid = _chat(ns, "f2")
    try:
        _put(ns, sid, org, "u1", "user", "question", timedelta(hours=5))
        _put(ns, sid, org, "a1", "assistant", "answer", timedelta(hours=5))
        _put(ns, sid, org, "a1r", "assistant", "", timedelta(0))
        assert await _decide(ns, org, sid, ("a1r", "u1"), monkeypatch) is False
        assert await _decide(ns, org, sid, ("a1r", ""), monkeypatch) is False
        # A NEW message after the same gap does greet.
        _put(ns, sid, org, "u2", "user", "back again", timedelta(0))
        assert await _decide(ns, org, sid, ("a1r", "u2"), monkeypatch) is True
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_f3_the_browser_clock_cannot_move_the_gap(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    """The previous prompt carries a browser clock 6 h in the past, but the
    server stored it 1 minute ago. The server's time rules."""
    ns = graph_as_app
    org, email, sid = _chat(ns, "f3")
    try:
        skew = int((NOW - timedelta(hours=6)).timestamp() * 1000)
        _put(ns, sid, org, "u0", "user", "earlier", timedelta(minutes=1), browser_ms=skew)
        _put(ns, sid, org, "u1", "user", "now", timedelta(0))
        _put(ns, sid, org, "a1", "assistant", "", timedelta(0))
        assert await _decide(ns, org, sid, ("a1", "u1"), monkeypatch) is False
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_another_orgs_rows_are_not_read_and_the_own_org_is(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, other, email, theirs = ns.org_a, ns.org_b, _address("a"), _address("b")
    _seed(ns, email, org, "A")
    _seed(ns, theirs, other, "B")
    sid = f"pz39-{uuid.uuid4().hex[:12]}"
    xsid = f"pz39x-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        _put(ns, sid, org, "m1", "assistant", "mine", timedelta(minutes=3))
        with ns.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO chat_session (id, user_id, organization_id) "
                "VALUES (:s, :u, CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
                {"s": xsid, "u": theirs, "o": other})
        _put(ns, xsid, other, "t1", "assistant", "theirs", timedelta(minutes=1))
        from orchestrator import executor as ex

        async def read(bound: str, session: str):
            async with tenant_engine_scope(_dsn(ns)):
                token = bind_tenant(bound)
                try:
                    return await ex._greet_read(bound, session, "r", "p")
                finally:
                    release_tenant(token)

        expected = int((NOW - timedelta(minutes=3)).timestamp() * 1000)
        # Bound to the member's own org: the rows ARE read (F5).
        assert (await read(org, sid))[0] == expected
        # Bound to another org, or to an org that does not exist: none.
        assert await read(other, sid) == (None, None)
        assert await read(str(uuid.uuid4()), sid) == (None, None)
        # The other org's chat id from org A: RLS gives no row.
        assert await read(org, xsid) == (None, None)
        # And from its own org it does read.
        assert (await read(other, xsid))[0] == int((NOW - timedelta(minutes=1)).timestamp() * 1000)
    finally:
        _unseed(ns, email)
        _unseed(ns, theirs)
