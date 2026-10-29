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
* every row lands in the member's organization;
* WS-BRIEF BR-2c-1: with ``BRIEF_IN_APP`` off, a stored run changes
  nothing, and the post is the composition of the feed, byte for byte. With
  it on, a ``ready`` run gives the lead and the pills, with no feed read,
  and the pills are ``plan.brief_parts(...).lines`` of the same cards. With
  it on and no ``ready`` run, the post is the composition.

**The mutation (R7) of BR-2c-1.** Commit first, restore with git after.
In ``post_brief``, read the stored run with the switch off too (drop
``if brief_flags.in_app(org) else None``).
``test_the_switch_off_posts_the_composition_with_a_stored_run_present``
goes red.

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

    box = {"cards": [], "complete": True, "reads": 0}
    day = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)

    async def snapshot(org, user, now=None):
        box["reads"] += 1
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


def _stamp(ns, email: str, key: str = "chat_brief_posted_on"):
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            f"SELECT my_day_prefs->>'{key}' FROM user_settings "
            "WHERE user_id = :u"), {"u": email}).scalar()


def _sealed(ns, sid: str, mid: str):
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT run_final_at IS NOT NULL FROM chat_message "
            "WHERE session_id = :s AND id = :m"), {"s": sid, "m": mid}).scalar()


def _client(user):
    """The real chat handlers behind FastAPI, as ``test_chat_write_under_rls``
    builds them: the browser's save and the session list."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import list_sessions, save_messages

    app = FastAPI()
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.get("/chat/sessions")(list_sessions)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


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

        # Review of #1216, P1: the row is sealed. The member's own browser
        # save of the brief's id with other words is refused (S13), and the
        # words stay.
        assert _sealed(ns, sid, "brief-2026-10-22") is True
        client = _client(user)
        forged = client.post(f"/chat/sessions/{sid}/messages", json=[{
            "id": "brief-2026-10-22", "role": "assistant", "content": "Take the day off.",
            "timestamp": 1_790_000_000_000, "tool_events": [], "progress_lines": [],
            "agent_state": None, "custom_events": [], "author_kind": "agent",
        }])
        assert forged.status_code == 200, forged.text
        assert forged.json()["unchanged"] == ["brief-2026-10-22"]
        assert _rows(ns, sid)[0].content == "Your day, Thu 22 Oct. 2 things need you."

        # Review of #1216, P1: the list says no MEMBER wrote here, so the
        # nudge stays. A member turn turns it true.
        [listed] = [s for s in client.get("/chat/sessions").json() if s["id"] == sid]
        assert (listed["messageCount"], listed["hasUserMessage"]) == (1, False)
        client.post(f"/chat/sessions/{sid}/messages", json=[{
            "id": "u1", "role": "user", "content": "Reply to Priya", "timestamp": 1_790_000_000_001,
            "tool_events": [], "progress_lines": [], "agent_state": None, "custom_events": [],
        }])
        [listed] = [s for s in client.get("/chat/sessions").json() if s["id"] == sid]
        assert listed["hasUserMessage"] is True

        # The same session again: one brief row still (and the member turn above).
        assert _status(again) == 204
        assert [r.id for r in _rows(ns, sid)] == ["u1", "brief-2026-10-22"]
        # The second chat: nothing posted.
        assert _status(other) == 204
        assert _rows(ns, sid2) == []
    finally:
        _unseed_member(ns, email)


async def test_a_day_with_no_brief_posts_nothing_and_stamps_the_day(graph_as_app, feed):
    """A complete read with nothing to say is the day's answer (review of
    #1216, P2): the day is stamped, and the next fresh chat reads no feed."""
    ns = graph_as_app
    org, email = ns.org_a, _address("bob")
    _seed_member(ns, email, org)
    feed["cards"] = []
    feed["reads"] = 0
    sid, sid2 = f"cf5-{uuid.uuid4().hex[:12]}", f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        _new_session(sid2, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            got = await _post(ns, sid, _member(email, org))
            again = await _post(ns, sid2, _member(email, org))
        assert _status(got) == 204 and _status(again) == 204
        assert _rows(ns, sid) == [] and _rows(ns, sid2) == []
        assert _stamp(ns, email) == "2026-10-22"
        assert feed["reads"] == 1
    finally:
        _unseed_member(ns, email)


async def test_an_incomplete_read_posts_nothing_and_rests_ten_minutes(graph_as_app, feed):
    """An incomplete read is not the day's answer (review of #1216, P2). It
    leaves a retry mark, and until it passes the route reads no feed."""
    ns = graph_as_app
    org, email = ns.org_a, _address("dan")
    _seed_member(ns, email, org)
    feed["cards"] = [_card("email:a:t1", "Q3 pricing")]
    feed["complete"] = False
    feed["reads"] = 0
    sid, sid2 = f"cf5-{uuid.uuid4().hex[:12]}", f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        _new_session(sid2, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            got = await _post(ns, sid, _member(email, org))
            again = await _post(ns, sid2, _member(email, org))
        assert _status(got) == 204 and _status(again) == 204
        assert _rows(ns, sid) == [] and _rows(ns, sid2) == []
        assert _stamp(ns, email) is None
        retry = _stamp(ns, email, "chat_brief_retry_after")
        assert retry == (NOW + timedelta(minutes=10)).isoformat()
        assert feed["reads"] == 1
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


# ── BR-2c-1: the stored run ──────────────────────────────────────────────────


def _brief_switches(monkeypatch, org: str, *, in_app: bool) -> None:
    from types import SimpleNamespace

    from gateway.brief import flags

    monkeypatch.setattr(flags, "get_settings", lambda: SimpleNamespace(
        brief_enabled=True, brief_orgs=org, brief_in_app=in_app))


def _linked(card: dict) -> dict:
    """A card with the in-app href that the gather keeps."""
    return {**card, "href": f"/email?thread={card['signal_id'].split(':')[-1]}"}


async def _store_run(org: str, email: str, cards: list[dict], *, state: str = "ready",
                     lead: str | None = None) -> str:
    """A stored run of today, made as the run makes it: ``gather.claims``,
    the template lead, and the guarded moves."""
    from acb_common.db import tenant_session
    from gateway.brief import gather, store
    from gateway.reach import plan

    today = NOW.astimezone(ZoneInfo("Asia/Kolkata")).date()
    async with tenant_session(org) as db:
        run = (await store.create_run(db, email, kind="brief", local_date=today,
                                      slot_at=NOW - timedelta(hours=2, minutes=30))).run
        if state == "empty":
            await store.set_state(db, email, run.id, "empty", now=NOW, from_state="planned")
            return run.id
        parts = plan.brief_parts(cards, today, now=NOW)
        await store.add_claims(db, email, run.id, gather.claims(cards, NOW))
        await store.set_state(db, email, run.id, "gathered", now=NOW, from_state="planned")
        await store.set_lead(db, email, run.id, lead or plan.brief_lead(parts),
                             source="template")
        await store.set_state(db, email, run.id, "ready", now=NOW, from_state="gathered")
    return run.id


def _drop_runs(ns, email: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM brief_runs WHERE member_email = :u"), {"u": email})


#: The answer before BR-2c-1, for the two cards of ``_two_cards``.
GOLDEN_LEAD = "Your day, Thu 22 Oct. 2 things need you."
GOLDEN_PILLS = ['Reply to Priya Shah: "Q3 pricing" (waiting 40 min)',
                'Reply to Priya Shah: "Renewal" (waiting 40 min)']


def _two_cards() -> list[dict]:
    return [_card("email:a:t1", "Q3 pricing"), _card("email:a:t2", "Renewal")]


def _golden_event() -> dict:
    """The event of the composition of ``_two_cards``, as the route wrote
    it before BR-2c-1."""
    from gateway.reach import plan
    from gateway.routes import chat_brief

    parts = plan.brief_parts(_two_cards(), NOW.date(), now=NOW)
    assert parts is not None and plan.brief_lead(parts) == GOLDEN_LEAD
    assert parts.lines == GOLDEN_PILLS
    return chat_brief.brief_event(parts)


async def test_the_switch_off_posts_the_composition_with_a_stored_run_present(
        graph_as_app, feed, monkeypatch):
    """(d) ``BRIEF_IN_APP`` off: a ready run with other words is in the store,
    and the post is the composition of the feed, byte for byte."""
    ns = graph_as_app
    org, email = ns.org_a, _address("flo")
    _seed_member(ns, email, org)
    _brief_switches(monkeypatch, org, in_app=False)
    feed["cards"] = _two_cards()
    feed["reads"] = 0
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            await _store_run(org, email, [_linked(_card("email:a:t9", "Stored words"))],
                             lead="Stored lead. 1 thing needs you.")
            got = await _post(ns, sid, _member(email, org))
        assert got == {"posted": True, "message_id": "brief-2026-10-22",
                       "preview": GOLDEN_LEAD}
        [row] = _rows(ns, sid)
        assert row.content == GOLDEN_LEAD
        assert row.custom_events == [_golden_event()]
        assert feed["reads"] == 1
    finally:
        _drop_runs(ns, email)
        _unseed_member(ns, email)


async def test_the_switch_on_posts_the_stored_run_with_no_feed_read(
        graph_as_app, feed, monkeypatch):
    """(e) ``BRIEF_IN_APP`` on: the pills are the claims' text in position
    order, and they equal ``plan.brief_parts(...).lines`` of the same
    cards. The feed is not read, and the run stays ``ready``: only the read
    route delivers it."""
    from gateway.reach import plan

    ns = graph_as_app
    org, email = ns.org_a, _address("gus")
    _seed_member(ns, email, org)
    _brief_switches(monkeypatch, org, in_app=True)
    cards = [_linked(c) for c in _two_cards()]
    feed["cards"] = []  # a feed read would say "no brief"
    feed["reads"] = 0
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            run_id = await _store_run(org, email, cards)
            got = await _post(ns, sid, _member(email, org))
        assert feed["reads"] == 0
        assert got == {"posted": True, "message_id": "brief-2026-10-22",
                       "preview": GOLDEN_LEAD}
        [row] = _rows(ns, sid)
        assert row.content == GOLDEN_LEAD
        [event] = row.custom_events
        pills = event["value"]["props"]["data"]["pills"]
        with ns.admin_engine.connect() as c:
            texts = [r[0] for r in c.execute(text(
                "SELECT text FROM brief_claims WHERE run_id = CAST(:r AS uuid) "
                "ORDER BY position"), {"r": run_id})]
            run_state = c.execute(text(
                "SELECT state FROM brief_runs WHERE id = CAST(:r AS uuid)"),
                {"r": run_id}).scalar()
        parts = plan.brief_parts(cards, NOW.date(), now=NOW)
        assert parts is not None
        assert pills == texts == parts.lines == GOLDEN_PILLS
        # The same card as the composition of the same cards, byte for byte.
        assert row.custom_events == [_golden_event()]
        assert _stamp(ns, email) == "2026-10-22"
        assert _sealed(ns, sid, "brief-2026-10-22") is True
        assert run_state == "ready"
    finally:
        _drop_runs(ns, email)
        _unseed_member(ns, email)


@pytest.mark.parametrize("stored", [None, "empty"])
async def test_the_switch_on_with_no_ready_run_posts_the_composition(
        graph_as_app, feed, monkeypatch, stored):
    """With the switch on and no ``ready`` run, the post falls back to the
    composition of the feed, as before."""
    ns = graph_as_app
    org, email = ns.org_a, _address("hal")
    _seed_member(ns, email, org)
    _brief_switches(monkeypatch, org, in_app=True)
    feed["cards"] = _two_cards()
    feed["reads"] = 0
    sid = f"cf5-{uuid.uuid4().hex[:12]}"
    try:
        _new_session(sid, email, org)
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            if stored is not None:
                await _store_run(org, email, [], state=stored)
            got = await _post(ns, sid, _member(email, org))
        assert feed["reads"] == 1
        assert got["preview"] == GOLDEN_LEAD
        [row] = _rows(ns, sid)
        assert row.custom_events == [_golden_event()]
    finally:
        _drop_runs(ns, email)
        _unseed_member(ns, email)
