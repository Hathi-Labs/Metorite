"""R8 - the greeting decision reads ``chat_message`` on a REAL Postgres (PZ-39).

``executor._greet_read`` and ``_greet_decision`` run as the NON-privileged role
of the phase-4 rehearsal, inside ``tenant_engine_scope``. What this proves:

* a chat with no earlier message greets (first reply). The turn's own rows
  (the newest ``user`` row and the empty minted reply) do not count, so they
  never make the gap zero;
* a reply 1 h after the last message does not greet, and one 4 h after does;
* the rows of ANOTHER org with the same chat id are not read (RLS);
* the rows of another chat are not read.

**Mutation (R7).** Drop the ``NOT IN (...)`` part of ``_GREET_LAST_SQL``:
``test_the_turns_own_rows_do_not_count`` goes red.

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


def _put(ns, sid: str, org: str, mid: str, role: str, content: str, at: datetime) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "organization_id) VALUES (:i, :s, :r, :c, :t, CAST(:o AS uuid))"),
            {"i": mid, "s": sid, "r": role, "c": content,
             "t": int(at.timestamp() * 1000), "o": org})


async def _decide(ns, org: str, sid: str, monkeypatch) -> bool:
    from orchestrator import executor as ex

    monkeypatch.setattr(ex, "_briefing_now", lambda: NOW)
    async with tenant_engine_scope(_dsn(ns)):
        token = bind_tenant(org)
        try:
            return await ex._greet_decision(org, sid)
        finally:
            release_tenant(token)


@pytest.mark.asyncio
async def test_the_greeting_follows_the_stored_gap(graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = ns.org_a, _address("gap")
    _seed(ns, email, org, "Gap")
    sid = f"pz39-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        # The turn's own rows only: the user prompt and the empty mint.
        _put(ns, sid, org, "u1", "user", "hello", NOW)
        _put(ns, sid, org, "a1", "assistant", "", NOW)
        assert await _decide(ns, org, sid, monkeypatch) is True      # first reply
        # An earlier exchange 1 h ago, then this turn.
        _put(ns, sid, org, "u0", "user", "earlier", NOW - timedelta(hours=1, minutes=1))
        _put(ns, sid, org, "a0", "assistant", "answer", NOW - timedelta(hours=1))
        assert await _decide(ns, org, sid, monkeypatch) is False
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_the_gap_edge_on_stored_rows(graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = ns.org_a, _address("edge")
    _seed(ns, email, org, "Edge")
    sid = f"pz39-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        _put(ns, sid, org, "a0", "assistant", "answer", NOW - timedelta(hours=3, minutes=59))
        _put(ns, sid, org, "u1", "user", "now", NOW)
        assert await _decide(ns, org, sid, monkeypatch) is False
        with ns.admin_engine.begin() as c:
            c.execute(text("UPDATE chat_message SET timestamp_ms = :t WHERE id = 'a0' "
                           "AND session_id = :s"),
                      {"t": int((NOW - timedelta(hours=4)).timestamp() * 1000), "s": sid})
        assert await _decide(ns, org, sid, monkeypatch) is True
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_the_turns_own_rows_do_not_count(graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = ns.org_a, _address("own")
    _seed(ns, email, org, "Own")
    sid = f"pz39-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        _put(ns, sid, org, "u1", "user", "now", NOW)
        _put(ns, sid, org, "a1", "assistant", "", NOW)
        from orchestrator import executor as ex

        async with tenant_engine_scope(_dsn(ns)):
            token = bind_tenant(org)
            try:
                assert await ex._greet_read(org, sid) == (True, None)
            finally:
                release_tenant(token)
    finally:
        _unseed(ns, email)


@pytest.mark.asyncio
async def test_another_orgs_rows_are_not_read(graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, other, email, theirs = ns.org_a, ns.org_b, _address("a"), _address("b")
    _seed(ns, email, org, "A")
    _seed(ns, theirs, other, "B")
    sid = f"pz39-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        # The same chat id in the other org holds a message 1 minute ago.
        with ns.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO chat_session (id, user_id, organization_id) "
                "VALUES (:s, :u, CAST(:o AS uuid)) ON CONFLICT DO NOTHING"),
                {"s": f"x-{sid}", "u": theirs, "o": other})
        _put(ns, f"x-{sid}", other, "t1", "assistant", "theirs", NOW - timedelta(minutes=1))
        from orchestrator import executor as ex

        async with tenant_engine_scope(_dsn(ns)):
            token = bind_tenant(org)
            try:
                # org A reads the other org's chat id: RLS gives no row.
                assert await ex._greet_read(org, f"x-{sid}") == (True, None)
                # Another chat of org A is not read for this chat.
                assert await ex._greet_read(org, sid) == (True, None)
            finally:
                release_tenant(token)
    finally:
        _unseed(ns, email)
        _unseed(ns, theirs)
