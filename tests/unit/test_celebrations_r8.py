"""R8 - the celebration read runs on a REAL Postgres (WS-63 PZ-43).

``acb_common.celebrations.READ_SQL`` and ``executor._celebration_read`` run as
the NON-privileged role of the phase-4 rehearsal, inside ``tenant_engine_scope``.
What this proves:

* the read gives the ``celebrations`` key of the member's profile and nothing
  else of the profile;
* the newest assistant reply with content, in ANY chat of the member, is read,
  and an empty reply (the minted row of this turn) is not;
* a reply older than the look-back, and a reply of ANOTHER member, are not read;
* the People ``start_date`` of the member is read, and the one of another org's
  person with the same address is not (RLS and the org in the join);
* the once-a-day decision, with the real rows: the first reply of the local day
  gets the line, the second gets none, and the next day gets none.

It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.

Mutations (R7), each red here: drop ``lower(btrim(s.user_id)) = :m`` (another
member's reply counts); drop the ``role = 'assistant'`` filter; drop the
``content <> ''`` filter (this turn's minted row counts); drop the org from the
People join.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import celebrations as c
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

#: 15:30 in Asia/Kolkata on Thu 12 March 2026.
NOW = datetime(2026, 3, 12, 10, 0, tzinfo=UTC)
ZONE = "Asia/Kolkata"
PROFILE = {"v": 1, "address": {"name": "Ana"}, "facts": [],
           "celebrations": {"birthday": {"month": 3, "day": 12},
                            "festivals": [], "work_anniversary": True}}


def _put(ns, sid: str, org: str, mid: str, role: str, content: str, at: datetime) -> None:
    with ns.admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "created_at, organization_id) "
            "VALUES (:i, :s, :r, :c, :t, :a, CAST(:o AS uuid))"),
            {"i": mid, "s": sid, "r": role, "c": content, "a": at, "o": org,
             "t": int(at.timestamp() * 1000)})
        conn.execute(text("UPDATE chat_session SET updated_at = GREATEST(updated_at, :a) "
                          "WHERE id = :s"), {"a": at, "s": sid})


def _profile(ns, org: str, email: str, profile: dict | None) -> None:
    with ns.admin_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile) "
            "VALUES (CAST(:o AS uuid), :u, CAST(:p AS jsonb))"),
            {"o": org, "u": email, "p": None if profile is None else json.dumps(profile)})


def _person(ns, org: str, email: str, start: date | None) -> None:
    with ns.admin_engine.begin() as conn:
        # Creating a member can make the People row too, so update first.
        done = conn.execute(text(
            "UPDATE people SET start_date = :d WHERE organization_id = CAST(:o AS uuid) "
            "AND lower(email) = lower(:e)"), {"d": start, "o": org, "e": email}).rowcount
        if done:
            return
        conn.execute(text(
            "INSERT INTO people (id, name, email, status, skills, source, source_key, "
            "organization_id, updated_by, updated_at, start_date) "
            "VALUES (gen_random_uuid(), 'Celebration Test', :e, 'active', ARRAY[]::text[], "
            "'manual', :k, CAST(:o AS uuid), 'test', now(), :d)"),
            {"e": email, "k": f"manual:{email}:{org}", "o": org, "d": start})


def _unseed_all(ns, email: str) -> None:
    with ns.admin_engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_session WHERE lower(user_id) = lower(:u)"),
                     {"u": email})
        conn.execute(text("DELETE FROM people WHERE lower(email) = lower(:u)"), {"u": email})
    _unseed(ns, email)


async def _read(ns, org: str, member: str, monkeypatch):
    from orchestrator import executor as ex

    monkeypatch.setattr(ex, "_briefing_now", lambda: NOW)
    async with tenant_engine_scope(_dsn(ns)):
        token = bind_tenant(org)
        try:
            return await ex._celebration_read(org, member)
        finally:
            release_tenant(token)


def _world(ns, tag: str):
    email = _address(tag)
    _seed(ns, email, ns.org_a, tag)
    return ns.org_a, email


@pytest.mark.asyncio
async def test_the_read_gives_the_key_and_the_join_date_and_no_reply(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = _world(ns, "key")
    try:
        _profile(ns, org, email, PROFILE)
        _person(ns, org, email, date(2023, 3, 12))
        raw, last, join = await _read(ns, org, email.lower(), monkeypatch)
        assert raw == PROFILE["celebrations"]
        assert last is None
        assert join == date(2023, 3, 12)
        line = c.celebration_line(raw, now=NOW, zone=ZONE, join_date=join, last_reply_at=last)
        assert "birthday" in line and "3 years" in line
    finally:
        _unseed_all(ns, email)


@pytest.mark.asyncio
async def test_a_member_with_no_row_reads_nothing(graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = _world(ns, "none")
    try:
        assert await _read(ns, org, email.lower(), monkeypatch) == (None, None, None)
    finally:
        _unseed_all(ns, email)


@pytest.mark.asyncio
async def test_the_first_reply_of_the_day_greets_and_the_second_does_not(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = _world(ns, "once")
    sid, sid2 = f"pz43-{uuid.uuid4().hex[:10]}", f"pz43-{uuid.uuid4().hex[:10]}"
    try:
        _profile(ns, org, email, PROFILE)
        _new_session(sid, email, org)
        _new_session(sid2, email, org)
        # Turn 1: only the empty row of this turn exists. Greet.
        _put(ns, sid, org, "a-turn1", "assistant", "", NOW - timedelta(minutes=1))
        raw, last, join = await _read(ns, org, email, monkeypatch)
        assert last is None
        assert c.celebration_line(raw, now=NOW, zone=ZONE, join_date=join,
                                  last_reply_at=last) != ""
        # The reply is stored. A later turn, in ANOTHER chat the same day: no greeting.
        with ns.admin_engine.begin() as conn:
            conn.execute(text("UPDATE chat_message SET content = 'Happy birthday, Ana' "
                              "WHERE id = 'a-turn1' AND session_id = :s"), {"s": sid})
        later = NOW + timedelta(hours=2)
        raw, last, join = await _read(ns, org, email, monkeypatch)
        assert last is not None
        assert c.celebration_line(raw, now=later, zone=ZONE, join_date=join,
                                  last_reply_at=last) == ""
        # The next local day: the birthday is over, so no greeting either.
        tomorrow = NOW + timedelta(days=1)
        assert c.celebration_line(raw, now=tomorrow, zone=ZONE, join_date=join,
                                  last_reply_at=last) == ""
        # A reply of yesterday does not stop a greeting on the day.
        with ns.admin_engine.begin() as conn:
            conn.execute(text("UPDATE chat_message SET created_at = :t WHERE id = 'a-turn1' "
                              "AND session_id = :s"), {"t": NOW - timedelta(hours=20), "s": sid})
        raw, last, join = await _read(ns, org, email, monkeypatch)
        assert c.celebration_line(raw, now=NOW, zone=ZONE, join_date=join,
                                  last_reply_at=last) != ""
    finally:
        _unseed_all(ns, email)


@pytest.mark.asyncio
async def test_a_user_row_and_another_members_reply_do_not_count(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = _world(ns, "mine")
    org2, other = _world(ns, "other")
    sid, sid2 = f"pz43-{uuid.uuid4().hex[:10]}", f"pz43-{uuid.uuid4().hex[:10]}"
    try:
        _profile(ns, org, email, PROFILE)
        _new_session(sid, email, org)
        _new_session(sid2, other, org2)
        _put(ns, sid, org, "u1", "user", "hello", NOW - timedelta(hours=1))
        _put(ns, sid2, org2, "a-other", "assistant", "hi", NOW - timedelta(hours=1))
        raw, last, _ = await _read(ns, org, email, monkeypatch)
        assert raw is not None and last is None
    finally:
        _unseed_all(ns, email)
        _unseed_all(ns, other)


@pytest.mark.asyncio
async def test_a_reply_older_than_the_lookback_is_not_read(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = _world(ns, "old")
    sid = f"pz43-{uuid.uuid4().hex[:10]}"
    try:
        _new_session(sid, email, org)
        _put(ns, sid, org, "a-old", "assistant", "hi", NOW - timedelta(hours=c.LOOKBACK_HOURS + 2))
        assert (await _read(ns, org, email, monkeypatch))[1] is None
    finally:
        _unseed_all(ns, email)


@pytest.mark.asyncio
async def test_the_join_date_of_another_orgs_person_is_not_read(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    ns = graph_as_app
    org, email = _world(ns, "join")
    try:
        # Only a person of ANOTHER org holds this address.
        _person(ns, ns.org_b, email, date(2020, 1, 1))
        assert (await _read(ns, org, email, monkeypatch))[2] is None
    finally:
        _unseed_all(ns, email)


@pytest.mark.asyncio
async def test_a_session_bound_to_another_org_reads_nothing(
        graph_as_app, monkeypatch) -> None:  # noqa: F811
    """RLS, not the WHERE: a read bound to org B that names org A sees no row."""
    from orchestrator import executor as ex

    ns = graph_as_app
    org, email = _world(ns, "rls")
    try:
        _profile(ns, org, email, PROFILE)
        _person(ns, org, email, date(2023, 3, 12))
        monkeypatch.setattr(ex, "_briefing_now", lambda: NOW)
        async with tenant_engine_scope(_dsn(ns)):
            token = bind_tenant(ns.org_b)
            try:
                from acb_common.db import tenant_session

                async with tenant_session(ns.org_b) as db:
                    row = (await db.execute(text(c.READ_SQL), {
                        "org": org, "m": email.lower(),
                        "since": c.lookback_since(NOW)})).first()
            finally:
                release_tenant(token)
        assert tuple(row) == (None, None, None)
    finally:
        _unseed_all(ns, email)
