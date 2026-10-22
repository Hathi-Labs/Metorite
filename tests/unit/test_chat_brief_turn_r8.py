"""R8 — ``POST /chat/sessions/{id}/brief`` against a REAL Postgres (WS-62 CF-5,
``chat_first_surface.md`` §4.6, D-CF-6, D-PM-39).

The real handler, the real room check, the real minter and the real message
seam, as the NON-privileged role of the phase-4 rehearsal (no superuser, no
BYPASSRLS), on a database with the whole ladder and two organizations. Only
the feed is faked: ``loop.snapshot`` answers a fixed set of cards, and the
member's day is a fixed zone, so the test owns the brief's lines and the date.

What this proves:

* a fresh chat (zero rows) gets ONE assistant row: the lead line as content,
  one ``generative_ui`` event with the ``reportCard`` and its pills, the
  agent as author, the member as run member, and the session's preview;
* a second call on the same session answers 204 and adds no row;
* a second FRESH chat of the same member on the same day answers 204 and
  gets no row, because the stamp in ``user_settings.my_day_prefs`` says so;
* a day with no brief answers 204, writes no row and no stamp;
* another member's session answers 404 and gets no row;
* every row lands in the member's organization.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_chat_brief_turn_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
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

pytestmark = _DB_GATE

NOW = datetime(2026, 10, 22, 4, 30, tzinfo=UTC)  # 10:00 in Kolkata


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@cf5.test"


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """The promoted catalog, with the chat function granted to the app role,
    as ``test_chat_write_under_rls.py`` does."""
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def graph_as_app(world, app_engine, monkeypatch):  # noqa: F811
    """``acb_graph`` sessions open as the app role, one fresh backend each."""
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield world
    finally:
        eng.dispose()


@pytest.fixture
def feed(monkeypatch):
    """The faked feed: ``cards`` is what ``loop.snapshot`` answers, and the
    member's day is Kolkata, 9 to 18."""
    from gateway import my_day_loop
    from gateway.reach import loop
    from gateway.routes.shell import state

    box = {"cards": [], "complete": True}
    day = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)

    async def snapshot(org, user, now=None):
        return loop.Snapshot(list(box["cards"]), now or NOW, day, True, box["complete"])

    async def read_day(org, user):
        return day

    monkeypatch.setattr(loop, "snapshot", snapshot)
    monkeypatch.setattr(my_day_loop, "_read_day", read_day)
    # The route's clock is NOW, so the lines say "waiting 40 min" and the
    # stamp says 2026-10-22 on every run.
    from gateway.routes import chat_brief

    monkeypatch.setattr(chat_brief, "_now", lambda: NOW)
    return box


def _card(sid: str, title: str) -> dict:
    return {"signal_id": sid, "app": "email", "kind": "needs_reply", "group": "needs_you",
            "score": 50, "title": title, "state": "new", "at": None, "why": "Why.",
            "fingerprint": "fp000001",
            "facts": {"who": "Priya Shah",
                      "waiting_since": (NOW - timedelta(minutes=40)).isoformat()}}


def _member(email: str, org: str):
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access(["feature:chat", "feature:tasks"], roles=["member"]),
    )


def _seed_member(ns, email: str, org: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :e, 'employee', 'active', :o) ON CONFLICT DO NOTHING"),
            {"e": email, "o": org})


def _unseed_member(ns, email: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
        c.execute(text("DELETE FROM app_user WHERE email = :u"), {"u": email})


def _new_session(sid: str, email: str, org: str) -> None:
    """The row the browser makes first (``sessions.ts`` ``_syncSessionToDb``)."""
    from gateway.routes.chat import SessionUpsertRequest, _upsert_session

    _upsert_session(email, SessionUpsertRequest(id=sid, title="CF-5"), organization_id=org)


def _rows(ns, sid: str) -> list:
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, role, content, custom_events, author_email, author_kind, "
            "run_member_email, organization_id::text AS org "
            "FROM chat_message WHERE session_id = :s ORDER BY timestamp_ms"), {"s": sid}).fetchall()


def _session(ns, sid: str):
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT last_preview, message_count FROM chat_session WHERE id = :s"), {"s": sid}).first()


def _stamp(ns, email: str):
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT my_day_prefs->>'chat_brief_posted_on' FROM user_settings "
            "WHERE user_id = :u"), {"u": email}).scalar()


async def _post(ns, sid: str, user):
    """The real handler, under the bound tenant for the async seam."""
    from acb_common.db import bind_tenant, release_tenant
    from fastapi import Response
    from gateway.routes.chat_brief import post_brief

    token = bind_tenant(user.organization_id)
    try:
        response = Response()
        got = await post_brief(sid, response, user=user)
        return got
    finally:
        release_tenant(token)


def _status(got) -> int:
    from fastapi import Response

    return got.status_code if isinstance(got, Response) else 200


async def test_a_fresh_chat_gets_one_brief_row_and_the_second_chat_of_the_day_none(
        graph_as_app, feed):
    ns = graph_as_app
    org, email = ns.org_a, _address("alice")
    _seed_member(ns, email, org)
    feed["cards"] = [_card("email:a:t1", "Q3 pricing"), _card("email:a:t2", "Renewal")]
    sid, sid2 = f"cf5-{uuid.uuid4().hex[:12]}", f"cf5-{uuid.uuid4().hex[:12]}"
    user = _member(email, org)
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            first = await _post(ns, sid, user)
            again = await _post(ns, sid, user)
            # The second fresh chat of the same day: stamped, so nothing.
            _new_session(sid2, email, org)
            other = await _post(ns, sid2, user)

        assert _status(first) == 200
        assert first["posted"] is True and first["message_id"] == "brief-2026-10-22"
        assert first["preview"] == "Your day, Thu 22 Oct. 2 things need you."

        rows = _rows(ns, sid)
        assert len(rows) == 1, rows
        [row] = rows
        assert (row.id, row.role, row.content) == (
            "brief-2026-10-22", "assistant", "Your day, Thu 22 Oct. 2 things need you.")
        assert (row.author_kind, row.author_email, row.run_member_email) == (
            "agent", "orchestrator", email)
        assert row.org == org
        [event] = row.custom_events
        assert event["name"] == "generative_ui"
        assert event["value"]["props"]["name"] == "reportCard"
        assert event["value"]["props"]["data"]["pills"] == [
            'Reply to Priya Shah: "Q3 pricing" (waiting 40 min)',
            'Reply to Priya Shah: "Renewal" (waiting 40 min)',
        ]
        session = _session(ns, sid)
        assert (session.last_preview, session.message_count) == (
            "Your day, Thu 22 Oct. 2 things need you.", 1)
        assert _stamp(ns, email) == "2026-10-22"

        # The same session again: one row still.
        assert _status(again) == 204
        assert len(_rows(ns, sid)) == 1
        # The second chat: nothing posted.
        assert _status(other) == 204
        assert _rows(ns, sid2) == []
    finally:
        _unseed_member(ns, email)


async def test_a_day_with_no_brief_posts_nothing_and_stamps_nothing(graph_as_app, feed):
    ns = graph_as_app
    org, email = ns.org_a, _address("bob")
    _seed_member(ns, email, org)
    feed["cards"] = []
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            got = await _post(ns, sid, _member(email, org))
        assert _status(got) == 204
        assert _rows(ns, sid) == []
        assert _stamp(ns, email) is None
    finally:
        _unseed_member(ns, email)


async def test_an_incomplete_read_posts_nothing(graph_as_app, feed):
    ns = graph_as_app
    org, email = ns.org_a, _address("dan")
    _seed_member(ns, email, org)
    feed["cards"] = [_card("email:a:t1", "Q3 pricing")]
    feed["complete"] = False
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            got = await _post(ns, sid, _member(email, org))
        assert _status(got) == 204
        assert _rows(ns, sid) == []
    finally:
        feed["complete"] = True
        _unseed_member(ns, email)


async def test_a_chat_with_a_turn_gets_no_brief(graph_as_app, feed):
    """The member wrote first: the brief never lands under their words."""
    from gateway.routes.chat import MessageRecord, _upsert_messages

    ns = graph_as_app
    org, email = ns.org_a, _address("eve")
    _seed_member(ns, email, org)
    feed["cards"] = [_card("email:a:t1", "Q3 pricing")]
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        _upsert_messages(sid, [MessageRecord(id="u1", role="user", content="Hi",
                                             timestamp=1_790_000_000_000)],
                         actor_email=email, agent_name="orchestrator", organization_id=org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            got = await _post(ns, sid, _member(email, org))
        assert _status(got) == 204
        assert [r.id for r in _rows(ns, sid)] == ["u1"]
        assert _stamp(ns, email) is None
    finally:
        _unseed_member(ns, email)


async def test_another_members_session_is_404_and_gets_no_row(graph_as_app, feed):
    from fastapi import HTTPException

    ns = graph_as_app
    org = ns.org_a
    alice, carol = _address("alice"), _address("carol")
    _seed_member(ns, alice, org)
    _seed_member(ns, carol, org)
    feed["cards"] = [_card("email:a:t1", "Q3 pricing")]
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, alice, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            with pytest.raises(HTTPException) as err:
                await _post(ns, sid, _member(carol, org))
        assert err.value.status_code == 404
        assert _rows(ns, sid) == []
        assert _stamp(ns, carol) is None
    finally:
        _unseed_member(ns, alice)
        _unseed_member(ns, carol)


async def test_a_session_of_another_organization_is_404(graph_as_app, feed):
    from fastapi import HTTPException

    ns = graph_as_app
    alice, zed = _address("alice"), _address("zed")
    _seed_member(ns, alice, ns.org_a)
    _seed_member(ns, zed, ns.org_b)
    feed["cards"] = [_card("email:a:t1", "Q3 pricing")]
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, alice, ns.org_a)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            with pytest.raises(HTTPException) as err:
                await _post(ns, sid, _member(zed, ns.org_b))
        assert err.value.status_code == 404
        assert _rows(ns, sid) == []
    finally:
        _unseed_member(ns, alice)
        _unseed_member(ns, zed)
