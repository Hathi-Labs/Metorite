"""R8 — the ``isBrief`` mark of the chat list, against a REAL Postgres
(task 3564, chat sidebar fault 2).

``GET /chat/sessions`` (``routes/chat.py::_get_sessions``) says for each chat
whether it holds the day's brief (``isBrief``). A later client slice folds
each chat with ``isBrief`` and no member message into one closed group. The
mark is computed in the list query (``_IS_BRIEF_SQL``). No column stores it,
so it needs no migration (R6).

The mark reads only what the SERVER writes and a member cannot write: a row
with the id ``brief-<YYYY-MM-DD>``, the kind ``agent``, and ``run_final_at``
set. Only ``chat_brief._post_row`` seals such a row. A client save inserts
only a human row (S14), and its update of a sealed row is refused (S13). The
fold of a run seals a row under the id that the client sent, so
``_upsert_messages`` refuses a sealing write of a brief id.

The real handlers, the real room check, the real minter and the real message
seam run as the NON-privileged role of the phase-4 rehearsal (no superuser,
no BYPASSRLS), on a database with the whole ladder and two organizations.
Only the brief's feed is faked, as ``test_chat_brief_turn_r8.py`` fakes it.

What this proves:

* a chat that ``POST /chat/sessions/{id}/brief`` posted into is ``isBrief``,
  and a member turn after the brief keeps it, with ``hasUserMessage`` true;
* a reminder chat, a buddy hello, a WhatsApp reach thread and a plain chat
  in the same org are not, so the mark is scoped by the session row;
* a member's own chat titled "Your day, Mon 16 Nov" is not;
* a client save cannot forge it: an agent row with a brief id is refused, a
  human row with that id does not count, and a session save that names
  ``isBrief`` stores nothing;
* a run cannot forge it: the fold of a brief id writes nothing;
* org B never reads org A's brief, even a row planted in org B's chat;
* a chat that the member cannot see stays hidden;
* the list of 300 chats is ONE statement. Its plan probes an index of
  ``chat_message`` for each chat, and never scans the whole table. An
  ``EXISTS`` let the planner hash a scan of every row of the tenant.

**The mutations (R7).** Commit first, restore with git after.

* In ``_IS_BRIEF_SQL`` drop ``b.session_id = s.id``:
  ``test_other_agent_only_chats_are_not_briefs`` goes red.
* In ``_IS_BRIEF_SQL`` drop ``b.run_final_at IS NOT NULL``:
  ``test_a_run_cannot_forge_the_mark`` goes red.
* In ``_upsert_messages`` drop the brief-id refusal of a sealing write:
  ``test_a_run_cannot_forge_the_mark`` goes red.
* In ``_get_sessions`` leave ``isBrief`` out of the row:
  ``test_a_brief_chat_is_brief_and_a_member_turn_keeps_it`` goes red.
* Write ``_IS_BRIEF_SQL`` as an ``EXISTS``:
  ``test_three_hundred_chats_list_in_one_statement`` goes red.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_chat_sessions_is_brief_r8.py -v -rs
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
from sqlalchemy import create_engine, event, text
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

NOW = datetime(2026, 11, 16, 4, 30, tzinfo=UTC)  # 10:00 on Mon 16 Nov in Kolkata
BRIEF_ID = "brief-2026-11-16"
BRIEF_LEAD = "Your day, Mon 16 Nov. 1 thing needs you."


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@isbrief.test"


def _sid(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:12]}"


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
    """``acb_graph`` sessions open as the app role, one fresh backend each.
    The namespace carries the engine, so a test can count its statements."""
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    world.graph_engine = eng
    try:
        yield world
    finally:
        eng.dispose()


@pytest.fixture
def feed(monkeypatch):
    """The faked feed of the brief: one card, a Kolkata day of 9 to 18, and
    the route's clock at NOW. So the brief's id is ``brief-2026-11-16``."""
    from gateway import my_day_loop
    from gateway.reach import loop
    from gateway.routes import chat_brief
    from gateway.routes.shell import state

    day = state.Day(ZoneInfo("Asia/Kolkata"), 9, 18)
    cards = [{
        "signal_id": "email:a:t1", "app": "email", "kind": "needs_reply",
        "group": "needs_you", "score": 50, "title": "Q3 pricing", "state": "new",
        "at": None, "why": "Why.", "fingerprint": "fp000001",
        "facts": {"who": "Priya Shah",
                  "waiting_since": (NOW - timedelta(minutes=40)).isoformat()},
    }]

    async def snapshot(org, user, now=None):
        return loop.Snapshot(list(cards), now or NOW, day, True, True)

    async def read_day(org, user):
        return day

    monkeypatch.setattr(loop, "snapshot", snapshot)
    monkeypatch.setattr(my_day_loop, "_read_day", read_day)
    monkeypatch.setattr(chat_brief, "_now", lambda: NOW)


def _member(email: str, org: str):
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=build_access(["feature:chat", "feature:tasks"], roles=["member"]),
    )


def _seed_member(ns, email: str, org: str, name: str | None = None) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, organization_id) "
            "VALUES (:e, :n, 'employee', 'active', :o) ON CONFLICT DO NOTHING"),
            {"e": email, "n": name or email, "o": org})


def _unseed_member(ns, email: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM user_settings WHERE user_id = :u"), {"u": email})
        c.execute(text("DELETE FROM app_user WHERE email = :u"), {"u": email})


def _new_session(sid: str, email: str, org: str, title: str | None = None) -> None:
    """The row the browser makes first (``sessions.ts`` ``_syncSessionToDb``)."""
    from gateway.routes.chat import SessionUpsertRequest, _upsert_session

    _upsert_session(email, SessionUpsertRequest(id=sid, title=title),
                    organization_id=org)


def _client(user):
    """The real chat handlers behind FastAPI: the session list, the session
    save and the message save, as the browser reaches them."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import list_sessions, save_messages, upsert_session

    app = FastAPI()
    app.get("/chat/sessions")(list_sessions)
    app.post("/chat/sessions")(upsert_session)
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _listed(user) -> dict[str, dict]:
    """The real ``GET /chat/sessions`` of *user*, by chat id."""
    got = _client(user).get("/chat/sessions")
    assert got.status_code == 200, got.text
    return {row["id"]: row for row in got.json()}


def _turn(mid: str, content: str, ts: int, role: str = "user", **extra) -> dict:
    return {"id": mid, "role": role, "content": content, "timestamp": ts,
            "tool_events": [], "progress_lines": [], "agent_state": None,
            "custom_events": [], **extra}


def _rows(ns, sid: str) -> list:
    with ns.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT id, role, author_kind, run_final_at IS NOT NULL AS sealed "
            "FROM chat_message WHERE session_id = :s ORDER BY timestamp_ms, id"),
            {"s": sid}).fetchall()


async def _post_brief(ns, sid: str, user):
    """The real ``POST /chat/sessions/{id}/brief``, under the bound tenant."""
    from acb_common.db import bind_tenant, release_tenant
    from fastapi import Response
    from gateway.routes.chat_brief import post_brief

    token = bind_tenant(user.organization_id)
    try:
        async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
            return await post_brief(sid, Response(), user=user)
    finally:
        release_tenant(token)


async def _brief_chat(ns, email: str, org: str) -> str:
    """A fresh chat of *email* that the real brief route posted into."""
    sid = _sid("brief")
    _new_session(sid, email, org)
    got = await _post_brief(ns, sid, _member(email, org))
    assert isinstance(got, dict) and got["message_id"] == BRIEF_ID, got
    assert [(r.id, r.author_kind, r.sealed) for r in _rows(ns, sid)] == [
        (BRIEF_ID, "agent", True)]
    return sid


# ── The brief chat ───────────────────────────────────────────────────────────


async def test_a_brief_chat_is_brief_and_a_member_turn_keeps_it(graph_as_app, feed):
    ns = graph_as_app
    org, email = ns.org_a, _address("alice")
    _seed_member(ns, email, org)
    user = _member(email, org)
    try:
        sid = await _brief_chat(ns, email, org)
        row = _listed(user)[sid]
        assert (row["isBrief"], row["hasUserMessage"]) == (True, False)
        assert row["lastPreview"] == BRIEF_LEAD

        # The member writes after the brief. The chat still holds the brief,
        # and the client rule folds only a chat with no member message.
        saved = _client(user).post(f"/chat/sessions/{sid}/messages", json=[
            _turn("u1", "Reply to Priya", 1_800_000_000_000)])
        assert saved.status_code == 200 and saved.json()["unchanged"] == []
        row = _listed(user)[sid]
        assert (row["isBrief"], row["hasUserMessage"]) == (True, True)
    finally:
        _unseed_member(ns, email)


async def test_other_agent_only_chats_are_not_briefs(graph_as_app, feed, monkeypatch):
    """Each chat that opens on another sealed agent row is not a brief. The
    member also holds a brief chat, so a mark that is not scoped by the
    session row would light every chat here."""
    import acb_common.guide_flags as guide_flags
    from acb_common.db import bind_tenant, release_tenant
    from fastapi import Response
    from gateway.reach import whatsapp
    from gateway.routes.agent import _mint_run_row
    from gateway.routes.chat import MessageRecord, _upsert_messages
    from gateway.routes.chat_brief import (
        DEFAULT_AGENT,
        HELLO_MESSAGE_ID,
        _post_row,
        post_buddy_hello,
    )
    from gateway.routes.chat_reminder import reminder_event, reminder_message_id
    from gateway.routes.whatsapp_channel import bot_run

    monkeypatch.setattr(guide_flags, "guide_orgs", lambda: frozenset({"*"}))
    ns = graph_as_app
    org, email = ns.org_a, _address("bob")
    _seed_member(ns, email, org, name="Bob Rao")
    user = _member(email, org)
    try:
        brief = await _brief_chat(ns, email, org)

        # A reminder that opens in a chat: the writer of the real route.
        reminder = _sid("reminder")
        _new_session(reminder, email, org)
        rid = reminder_message_id(uuid.uuid4())
        assert _post_row(
            reminder, rid, member=email, agent_name=DEFAULT_AGENT, organization_id=org,
            lead="Call the bank, due at 11:00.",
            event=reminder_event({"title": "Call the bank"}), now_ms=1_800_000_000_000)

        # The first-visit hello: the real route.
        hello = _sid("hello")
        _new_session(hello, email, org)
        token = bind_tenant(org)
        try:
            async with tenant_engine_scope(ns.app_url.render_as_string(hide_password=False)):
                said = await post_buddy_hello(hello, Response(), user=user)
                # A WhatsApp reach thread: the real record of a sent brief.
                outbox = str(uuid.uuid4())
                await whatsapp._record(org, email, "919800000000", None,
                                       "Your day, Mon 16 Nov. 1 thing needs you.", outbox)
        finally:
            release_tenant(token)
        assert isinstance(said, dict) and said["message_id"] == HELLO_MESSAGE_ID, said
        reach = bot_run.new_thread_id(org, email, f"reach-{outbox}")

        # A plain chat: the member's turn and the run's sealed reply.
        plain = _sid("plain")
        _new_session(plain, email, org)
        _upsert_messages(plain, [MessageRecord(id="u1", role="user", content="Hi",
                                               timestamp=1_800_000_000_000)],
                         actor_email=email, agent_name=DEFAULT_AGENT, organization_id=org)
        reply = f"assistant-{uuid.uuid4().hex}"
        _mint_run_row(plain, reply, member=email, agent_name=DEFAULT_AGENT,
                      organization_id=org)
        assert _upsert_messages(
            plain, [MessageRecord(id=reply, role="assistant", content="Hello.",
                                  timestamp=1_800_000_000_001)],
            actor_email=email, agent_name=DEFAULT_AGENT, author_from_run=True,
            organization_id=org) == []

        for sid, first in ((reminder, rid), (hello, HELLO_MESSAGE_ID),
                           (reach, f"reach-{outbox}"), (plain, reply)):
            rows = _rows(ns, sid)
            assert any(r.id == first and r.author_kind == "agent" and r.sealed
                       for r in rows), (sid, rows)

        listed = _listed(user)
        assert listed[brief]["isBrief"] is True
        got = {name: listed[sid]["isBrief"] for name, sid in (
            ("reminder", reminder), ("hello", hello), ("reach", reach), ("plain", plain))}
        assert got == {"reminder": False, "hello": False, "reach": False, "plain": False}
    finally:
        _unseed_member(ns, email)


async def test_a_title_that_looks_like_a_brief_is_not_a_brief(graph_as_app, feed):
    """The member names a chat like the brief and types its lead. The title
    and the words are the member's, so the chat is not a brief."""
    from gateway.routes.chat import SessionPatchRequest, _patch_session

    ns = graph_as_app
    org, email = ns.org_a, _address("cara")
    _seed_member(ns, email, org)
    user = _member(email, org)
    try:
        sid = _sid("title")
        _new_session(sid, email, org, title="Your day, Mon 16 Nov")
        _client(user).post(f"/chat/sessions/{sid}/messages", json=[
            _turn("u1", BRIEF_LEAD, 1_800_000_000_000)])
        assert _patch_session(sid, email, SessionPatchRequest(
            title="Your day, Mon 16 Nov", last_preview=BRIEF_LEAD), organization_id=org)
        row = _listed(user)[sid]
        assert row["title"] == "Your day, Mon 16 Nov"
        assert (row["isBrief"], row["hasUserMessage"]) == (False, True)
    finally:
        _unseed_member(ns, email)


# ── A member cannot forge the mark ──────────────────────────────────────────


async def test_a_client_save_cannot_forge_the_mark(graph_as_app, feed):
    """What ``POST /chat/sessions/{id}/messages`` does with a brief id."""
    ns = graph_as_app
    org, email = ns.org_a, _address("dev")
    _seed_member(ns, email, org)
    user = _member(email, org)
    client = _client(user)
    try:
        # (a) An agent row with the brief id: refused, no row (S14).
        agent_chat = _sid("forge-agent")
        _new_session(agent_chat, email, org)
        forged = client.post(f"/chat/sessions/{agent_chat}/messages", json=[
            _turn(BRIEF_ID, BRIEF_LEAD, 1_800_000_000_000, role="assistant",
                  author_kind="agent", author_email="orchestrator")])
        assert forged.status_code == 200, forged.text
        assert forged.json()["unchanged"] == [BRIEF_ID]
        assert _rows(ns, agent_chat) == []

        # (b) A user row with the brief id: stored as the member's own turn,
        # with no seal. It does not count.
        human_chat = _sid("forge-human")
        _new_session(human_chat, email, org)
        saved = client.post(f"/chat/sessions/{human_chat}/messages", json=[
            _turn(BRIEF_ID, BRIEF_LEAD, 1_800_000_000_000, author_kind="agent")])
        assert saved.status_code == 200 and saved.json()["unchanged"] == [BRIEF_ID]
        saved = client.post(f"/chat/sessions/{human_chat}/messages", json=[
            _turn(BRIEF_ID, BRIEF_LEAD, 1_800_000_000_000)])
        assert saved.status_code == 200 and saved.json()["unchanged"] == []
        assert [(r.id, r.role, r.author_kind, r.sealed) for r in _rows(ns, human_chat)] == [
            (BRIEF_ID, "user", "human", False)]

        # (c) A session save that names the mark: nothing stores it.
        named_chat = _sid("forge-named")
        upsert = client.post("/chat/sessions", json={
            "id": named_chat, "title": "Mine", "isBrief": True, "is_brief": True})
        assert upsert.status_code == 200, upsert.text

        listed = _listed(user)
        assert {sid: listed[sid]["isBrief"] for sid in (agent_chat, human_chat, named_chat)} \
            == {agent_chat: False, human_chat: False, named_chat: False}
        with ns.admin_engine.connect() as c:
            stored = c.execute(text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name IN ('chat_session', 'chat_message') "
                "AND column_name ILIKE '%brief%'")).scalar_one()
        assert stored == 0, "the mark is computed, never a column (no migration)"
    finally:
        _unseed_member(ns, email)


async def test_a_run_cannot_forge_the_mark(graph_as_app, feed):
    """A run sends its own ``assistant_message_id``. The run route mints the
    row under that id (``_mint_run_row``), and the fold seals it
    (``persist_final_assistant_message`` calls ``_upsert_messages`` with
    ``author_from_run=True``). A brief id there writes nothing sealed."""
    from gateway.chat_fold import persist_channel_reply
    from gateway.routes.agent import _mint_run_row
    from gateway.routes.chat import MessageRecord, _upsert_messages
    from gateway.routes.chat_brief import DEFAULT_AGENT

    ns = graph_as_app
    org, email = ns.org_a, _address("eli")
    _seed_member(ns, email, org)
    user = _member(email, org)
    try:
        run_chat = _sid("forge-run")
        _new_session(run_chat, email, org)
        _mint_run_row(run_chat, BRIEF_ID, member=email, agent_name=DEFAULT_AGENT,
                      organization_id=org)
        declined = _upsert_messages(
            run_chat, [MessageRecord(id=BRIEF_ID, role="assistant", content=BRIEF_LEAD,
                                     timestamp=1_800_000_000_000)],
            actor_email=email, agent_name=DEFAULT_AGENT, author_from_run=True,
            organization_id=org)
        assert declined == [BRIEF_ID]
        assert [(r.id, r.author_kind, r.sealed) for r in _rows(ns, run_chat)] == [
            (BRIEF_ID, "agent", False)]

        # The channel reply seals the same way, and refuses the same way.
        channel_chat = _sid("forge-channel")
        _new_session(channel_chat, email, org)
        assert await persist_channel_reply(
            channel_chat, BRIEF_ID, BRIEF_LEAD, user_id=email, agent_name=DEFAULT_AGENT,
            timestamp_ms=1_800_000_000_000, organization_id=org) is False
        assert _rows(ns, channel_chat) == []

        listed = _listed(user)
        assert (listed[run_chat]["isBrief"], listed[channel_chat]["isBrief"]) == (False, False)
    finally:
        _unseed_member(ns, email)


# ── Tenancy and visibility ───────────────────────────────────────────────────


async def test_org_b_never_reads_the_brief_of_org_a(graph_as_app, feed):
    """The list runs under RLS as the app role. Org B's list never holds org
    A's brief chat. A brief row of org A planted in org B's chat (by the
    superuser, as no route can) does not mark it, because RLS hides it."""
    ns = graph_as_app
    alice, zed = _address("alice"), _address("zed")
    _seed_member(ns, alice, ns.org_a)
    _seed_member(ns, zed, ns.org_b)
    try:
        brief = await _brief_chat(ns, alice, ns.org_a)
        theirs = _sid("org-b")
        _new_session(theirs, zed, ns.org_b)
        with ns.admin_engine.begin() as c:
            c.execute(text(
                "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
                "author_kind, author_email, run_member_email, run_final_at, organization_id) "
                "VALUES (:id, :sid, 'assistant', :lead, 1800000000000, 'agent', "
                "'orchestrator', :m, now(), :org)"),
                {"id": BRIEF_ID, "sid": theirs, "lead": BRIEF_LEAD, "m": zed,
                 "org": ns.org_a})

        b_list = _listed(_member(zed, ns.org_b))
        assert brief not in b_list
        assert b_list[theirs]["isBrief"] is False
        a_list = _listed(_member(alice, ns.org_a))
        assert a_list[brief]["isBrief"] is True
        assert theirs not in a_list
    finally:
        _unseed_member(ns, alice)
        _unseed_member(ns, zed)


async def test_a_chat_the_member_cannot_see_stays_hidden(graph_as_app, feed):
    ns = graph_as_app
    org = ns.org_a
    alice, carol = _address("alice"), _address("carol")
    _seed_member(ns, alice, org)
    _seed_member(ns, carol, org)
    try:
        brief = await _brief_chat(ns, alice, org)
        assert brief not in _listed(_member(carol, org))
        assert _listed(_member(alice, org))[brief]["isBrief"] is True
    finally:
        _unseed_member(ns, alice)
        _unseed_member(ns, carol)


# ── 300 chats: one statement, and an index probe for each chat ───────────────


def _seed_many(ns, email: str, org: str, *, chats: int, turns: int, tag: str) -> None:
    """*chats* solo chats of *email*, with *turns* rows each, as the
    superuser. Each odd turn is a sealed agent row, each even one a typed
    member row, and no id is a brief id."""
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, organization_id, "
            "updated_at) SELECT :p || g, :e, 'orchestrator', 'Chat ' || g, "
            "CAST(:o AS uuid), now() - g * interval '1 minute' "
            "FROM generate_series(1, :n) g"),
            {"p": f"{tag}-", "e": email, "o": org, "n": chats})
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "author_kind, author_email, run_member_email, run_final_at, organization_id) "
            "SELECT 'm-' || k, :p || g, "
            "  CASE WHEN k % 2 = 0 THEN 'user' ELSE 'assistant' END, 'words', k, "
            "  CASE WHEN k % 2 = 0 THEN 'human' ELSE 'agent' END, "
            "  CASE WHEN k % 2 = 0 THEN :e ELSE 'orchestrator' END, "
            "  CASE WHEN k % 2 = 0 THEN NULL ELSE :e END, "
            "  CASE WHEN k % 2 = 0 THEN NULL ELSE now() END, CAST(:o AS uuid) "
            "FROM generate_series(1, :n) g, generate_series(1, :t) k"),
            {"p": f"{tag}-", "e": email, "o": org, "n": chats, "t": turns})


def _probes_by_session(plan: str, alias: str) -> bool:
    """Whether the scan of ``chat_message <alias>`` in *plan* is an index
    probe on ``session_id = s.id``: an index scan, or a bitmap scan whose
    index node holds that condition."""
    lines = plan.splitlines()
    for i, line in enumerate(lines):
        if re.search(rf"(Index|Index Only|Bitmap Heap) Scan .*on chat_message {alias}$",
                     line.rstrip()):
            return any("Index Cond: (session_id = s.id)" in below
                       for below in lines[i + 1:i + 6])
    return False


def _drop_many(ns, tag: str) -> None:
    with ns.admin_engine.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id LIKE :p"), {"p": f"{tag}-%"})


async def test_three_hundred_chats_list_in_one_statement(graph_as_app, feed):
    """The mark adds no statement for each chat: the list of 300 chats runs
    ONE statement after the tenant bind. Its plan probes an index of
    ``chat_message`` for each chat and never reads the whole table, once the
    table holds more than the member's own chats, as production does."""
    from gateway.routes.chat import _SESSIONS_SQL

    ns = graph_as_app
    org = ns.org_a
    many, others = _address("many"), _address("others")
    _seed_member(ns, many, org)
    _seed_member(ns, others, org)
    tag, other_tag = f"n300{uuid.uuid4().hex[:6]}", f"bulk{uuid.uuid4().hex[:6]}"
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        brief = await _brief_chat(ns, many, org)
        _seed_many(ns, many, org, chats=300, turns=10, tag=tag)
        _seed_many(ns, others, org, chats=4000, turns=30, tag=other_tag)
        with ns.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text("ANALYZE chat_message"))
            c.execute(text("ANALYZE chat_session"))
            c.execute(text("ANALYZE chat_session_participant"))

        from gateway.routes.chat import _get_sessions

        event.listen(ns.graph_engine, "before_cursor_execute", _count)
        try:
            rows = _get_sessions(many, organization_id=org)
        finally:
            event.remove(ns.graph_engine, "before_cursor_execute", _count)
        assert len(rows) == 301
        assert sum(r["isBrief"] for r in rows) == 1
        assert next(r for r in rows if r["id"] == brief)["isBrief"] is True
        reads = [s for s in statements if "chat_session" in s or "chat_message" in s]
        assert len(reads) == 1, statements
        assert len(statements) <= 2, statements  # the tenant bind, then the list

        from acb_graph import tenant_session

        with tenant_session(org) as s:
            plan = "\n".join(r[0] for r in s.execute(
                text("EXPLAIN (COSTS OFF) " + _SESSIONS_SQL), {"uid": many}))
        print("\nEXPLAIN of the session list:\n" + plan)
        # No hashed scan of the tenant's whole table. Each of the two marks
        # (``m`` for hasUserMessage, ``b`` for isBrief) probes an index that
        # leads with session_id, once for each chat.
        assert "Seq Scan on chat_message" not in plan, plan
        for alias in ("m", "b"):
            assert _probes_by_session(plan, alias), (alias, plan)
    finally:
        _drop_many(ns, tag)
        _drop_many(ns, other_tag)
        _unseed_member(ns, many)
        _unseed_member(ns, others)
