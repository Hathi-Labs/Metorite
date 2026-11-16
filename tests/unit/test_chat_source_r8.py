"""R8 — HRD-21 (task 3225): the past chats of company search are the member's own.

``find_engine.md`` §6 and D-FIND-5: "Only the solo sessions of the member in
this org. Never the chat of another member, a room, a ``no_egress`` session or
the current chat."

Against a REAL Postgres, as the non-privileged role of the phase-4 rehearsal
(no superuser, no BYPASSRLS), through the real ``GET /search/company``, the
real ``chat`` source and the real SQL of ``gateway/chat_search.py``. Only the
tenant Redis of the egress marks is a fake, through its one seam
(``egress.get_tenant_redis``):

* **Member A** finds her own solo chats with Mira that hold the words: by the
  title, by her typed row, by a reply of Mira and by a row that she tapped.
  Each hit names the title, the date and the link that opens the chat.
* **Never** her chat that read mail (the egress mark), her room, her chat
  shared with the org, her current chat, her chat with another agent (a seat
  or a row), her chat with the email assistant, or her WhatsApp thread.
* **Member B** of the same org never sees a chat of A, and A never sees B's.
  RLS lets both through, so the member rule alone stops it.
* **Org B** never sees a chat of org A, and org A never sees org B's.
* **A tool row or a system row** never makes a hit. **A reply of Mira**
  makes a hit with no snippet. The snippet is A's own typed words only.
* **The flag off**: no chat anywhere, and the answer is byte-identical.
* **The rule of "no other agent"** gives the answer of PZ-33's ``ELIGIBLE_SQL``.

⚠️ It SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a pass.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.unit.test_email_keep_separate import _assert_non_priv
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
A = f"chat-a-{TAG}@chat-r8.test"
B = f"chat-b-{TAG}@chat-r8.test"
C = f"chat-c-{TAG}@chat-r8.test"
#: The words of the search. Each chat that holds them names them so.
WORDS = "Snowflake printer"
#: A word that only a system row and a tool event hold.
TOOL_WORD = f"quillfeather{TAG}"
#: A word that only a reply of Mira holds.
MIRA_WORD = f"neuropeak{TAG}"


def _member(email: str, org: str, *perms: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org,
                       access=build_access(list(perms)))


def _chat(admin: Any, *, org: str, user_id: str, title: str, agent: str = "orchestrator",
          visibility: str = "private", channel: str | None = None, minutes_ago: int = 10,
          subjects: tuple[str, ...] | None = None) -> str:
    """One chat, seeded as the superuser. *subjects* are its participants: the
    member alone by default, as a solo chat of today holds."""
    sid = f"hrd21-{uuid.uuid4().hex[:12]}"
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session (id, user_id, agent_name, title, message_count, "
            "created_at, updated_at, visibility, channel, organization_id) "
            "VALUES (:id, :u, :a, :t, 2, :at, :at, :v, :ch, CAST(:o AS uuid))"),
            {"id": sid, "u": user_id, "a": agent, "t": title, "at": at, "v": visibility,
             "ch": channel, "o": org})
        for i, subject in enumerate((user_id,) if subjects is None else subjects):
            c.execute(text(
                "INSERT INTO chat_session_participant (session_id, subject, role, "
                "organization_id) VALUES (:s, :subject, :r, CAST(:o AS uuid))"),
                {"s": sid, "subject": subject, "r": "owner" if i == 0 else "member",
                 "o": org})
    return sid


def _row(admin: Any, *, org: str, sid: str, role: str, content: str, ts: int,
         typed: bool | None = None, author: str | None = None, kind: str | None = None,
         tool_events: list[Any] | None = None) -> None:
    """One row of a chat, seeded as the superuser."""
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO chat_message (id, session_id, role, content, timestamp_ms, "
            "tool_events, member_typed, author_email, author_kind, organization_id) "
            "VALUES (:id, :s, :r, :c, :ts, CAST(:te AS jsonb), :typed, :au, :k, "
            "CAST(:o AS uuid))"),
            {"id": f"m-{uuid.uuid4().hex[:10]}", "s": sid, "r": role, "c": content,
             "ts": ts, "te": json.dumps(tool_events or []), "typed": typed, "au": author,
             "k": kind, "o": org})


def _typed(admin: Any, org: str, sid: str, member: str, content: str, ts: int = 1) -> None:
    _row(admin, org=org, sid=sid, role="user", content=content, ts=ts, typed=True,
         author=member, kind="human")


def _mira(admin: Any, org: str, sid: str, content: str, ts: int = 2,
          author: str | None = "orchestrator") -> None:
    _row(admin, org=org, sid=sid, role="assistant", content=content, ts=ts, author=author,
         kind="agent")


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Member A's chats of every kind, B's chat in org A, and C's in org B."""
    admin, oa, ob = promoted.admin_engine, promoted.org_a, promoted.org_b
    chats: dict[str, str] = {}

    def make(name: str, **kw: Any) -> str:
        chats[name] = _chat(admin, **kw)
        return chats[name]

    # A's own solo chats with Mira that hold the words.
    sid = make("own", org=oa, user_id=A, title="Snowflake printer order", minutes_ago=5)
    _typed(admin, oa, sid, A, "Sharath wants the Snowflake printer by Friday")
    _mira(admin, oa, sid, f"I made the deal {MIRA_WORD} - Snowflake 3D printer.")
    sid = make("by_mira", org=oa, user_id=A, title="Weekly plan", minutes_ago=6)
    _typed(admin, oa, sid, A, "What ships this week?")
    _mira(admin, oa, sid, "The Snowflake printer ships on Friday.")
    sid = make("tapped", org=oa, user_id=A, title="Ops", minutes_ago=7)
    _row(admin, org=oa, sid=sid, role="user", content="Snowflake printer status", ts=1,
         typed=None, author=A, kind="human")
    # An older chat with no participant row: it predates migration 138.
    sid = make("legacy", org=oa, user_id=A, title="Old Snowflake printer talk",
               minutes_ago=600, subjects=())
    # A's chats that the search never shows.
    sid = make("marked", org=oa, user_id=A, title="Snowflake printer mail", minutes_ago=4)
    _typed(admin, oa, sid, A, "Read the Snowflake printer mail")
    make("room", org=oa, user_id=A, title="Snowflake printer room", subjects=(A, B))
    make("org_wide", org=oa, user_id=A, title="Snowflake printer for all",
         visibility="org")
    make("current", org=oa, user_id=A, title="Snowflake printer right now", minutes_ago=1)
    sid = make("seat", org=oa, user_id=A, title="Snowflake printer with the CRM")
    with admin.begin() as c:
        c.execute(text(
            "INSERT INTO chat_session_agent (session_id, agent_name, role, organization_id) "
            "VALUES (:s, 'crm-assistant', 'mentioned', CAST(:o AS uuid))"),
            {"s": sid, "o": oa})
    sid = make("agent_row", org=oa, user_id=A, title="Snowflake printer deal")
    _mira(admin, oa, sid, "The deal is ready.", author="crm-assistant")
    sid = make("null_kind", org=oa, user_id=A, title="Snowflake printer leads")
    _row(admin, org=oa, sid=sid, role="assistant", content="Leads.", ts=2,
         author="crm-assistant", kind=None)
    make("email_agent", org=oa, user_id=A, title="Snowflake printer inbox",
         agent="email-assistant")
    make("whatsapp", org=oa, user_id=A, title="Snowflake printer on WhatsApp",
         channel="whatsapp")
    sid = make("tool", org=oa, user_id=A, title="Plan", minutes_ago=8)
    _row(admin, org=oa, sid=sid, role="system", content=f"The {TOOL_WORD} is ready.", ts=1)
    _row(admin, org=oa, sid=sid, role="user", content="ok", ts=2, typed=True, author=A,
         kind="human", tool_events=[{"name": "search", "result": f"{TOOL_WORD} details"}])
    # B's chat in org A, and C's chat in org B, with the same words.
    sid = make("b_own", org=oa, user_id=B, title="Snowflake printer for B", minutes_ago=3)
    _typed(admin, oa, sid, B, "B asks about the Snowflake printer")
    sid = make("c_own", org=ob, user_id=C, title="Snowflake printer for C", minutes_ago=3)
    _typed(admin, ob, sid, C, "C asks about the Snowflake printer")
    yield promoted, chats
    with admin.begin() as c:
        c.execute(text("DELETE FROM chat_session WHERE id = ANY(:ids)"),
                  {"ids": list(chats.values())})


class _Redis:
    """The tenant Redis of the egress marks: the chats in *marked* read marked."""

    def __init__(self, marked: set[str], fail: bool = False) -> None:
        self.marked, self.fail = marked, fail

    async def mget(self, keys: list[Any]) -> list[Any]:
        if self.fail:
            raise ConnectionError("redis down")
        return ["1" if k.value.rsplit(":", 1)[-1] in self.marked else None for k in keys]


@pytest.fixture
def marks(world, monkeypatch):
    """The ``marked`` chat holds the egress mark (it read the email agent's
    answer). Each test starts with an empty process cache."""
    from acb_skills import egress

    _p, chats = world
    fake = _Redis({chats["marked"]})
    monkeypatch.setattr(egress, "get_tenant_redis", lambda: fake)
    egress._MARKED_SESSIONS.clear()
    yield fake
    egress._MARKED_SESSIONS.clear()


@pytest.fixture
def flags(monkeypatch):
    """``CHAT_SEARCH_ORGS``, ``FIND_ORGS`` and ``CONTEXT_RECENCY`` on the
    cached settings, and room in the budgets for a cold scratch database."""
    from gateway import chat_search
    from gateway.retrieval import company

    settings = get_settings()
    for name, value in (("SOURCE_TIMEOUT_S", 30.0), ("TOTAL_BUDGET_S", 60.0),
                        ("BROADEN_BUDGET_S", 60.0), ("NEAR_LOOKUP_S", 30.0),
                        ("RELATED_BUDGET_S", 30.0), ("ANSWER_CAP_S", 300.0)):
        monkeypatch.setattr(company, name, value)
    monkeypatch.setattr(chat_search, "STATEMENT_MS", 30_000)

    def _set(chat: str, *, find: str = "*", recency: bool = True) -> None:
        monkeypatch.setattr(settings, "chat_search_orgs", chat, raising=False)
        monkeypatch.setattr(settings, "find_orgs", find, raising=False)
        monkeypatch.setattr(settings, "context_recency", recency, raising=False)

    _set("")
    return _set


async def _ask(p: Any, user: UserContext, q: str = WORDS, **params: str) -> dict[str, Any]:
    from tests.unit.test_company_search_r8 import _ask as ask

    return await ask(p, user, {"q": q, **params})


def _chats(answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["id"]: h for h in answer["hits"] if h["app"] == "chat"}


# ── Member A finds her own chats, and no other ──────────────────────────────

async def test_a_finds_her_own_solo_chats_and_no_other(world, marks, flags, app_engine) -> None:  # noqa: F811
    _assert_non_priv(app_engine)
    p, chats = world
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"),
                        current_chat=chats["current"])
    found = _chats(answer)
    assert set(found) == {chats["own"], chats["by_mira"], chats["tapped"], chats["legacy"]}
    assert "chat" in answer["searched"]
    own = found[chats["own"]]
    assert own["title"] == "Snowflake printer order"
    assert own["hint"] == "Your chat" and own["kind"] == "chat"
    assert own["href"] == f"/chat?do=open-chat&fill.session={chats['own']}"
    assert own["when"] and own["when"] == own["stamp"] and own["matched_by"] == "chat"
    # The snippet is A's own typed words, and never the reply of Mira.
    assert own["snippet"] == "Sharath wants the Snowflake printer by Friday"
    assert MIRA_WORD not in json.dumps(answer)
    assert found[chats["by_mira"]]["snippet"] == ""
    assert found[chats["tapped"]]["snippet"] == ""
    # Review P1: the SQL proved every word, so each chat holds 2 of 2, also
    # the chat whose printed text («Weekly plan», "Your chat") shows none.
    by_mira = found[chats["by_mira"]]
    assert (by_mira["matched_by"], by_mira["covers"], by_mira["of"]) == ("chat", 2, 2)
    assert {(h["covers"], h["of"]) for h in found.values()} == {(2, 2)}
    for name in ("marked", "room", "org_wide", "current", "seat", "agent_row", "null_kind",
                 "email_agent", "whatsapp", "tool", "b_own", "c_own"):
        assert chats[name] not in json.dumps(answer), name


async def test_the_current_chat_shows_when_it_is_not_current(world, marks, flags) -> None:
    """Non-vacuity of the current chat: named as another chat, it shows."""
    p, chats = world
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"), current_chat=chats["own"])
    assert chats["current"] in _chats(answer) and chats["own"] not in _chats(answer)


async def test_no_current_chat_shows_no_past_chat(world, marks, flags) -> None:
    p, _ = world
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"))
    assert _chats(answer) == {} and "chat" not in answer["searched"]


async def test_a_marked_chat_shows_when_the_mark_is_gone(world, marks, flags) -> None:
    """Non-vacuity of the mark: with Redis clear, the chat that read mail
    is a solo chat of A like the others. The mark alone keeps it out."""
    p, chats = world
    flags("*")
    marks.marked = set()
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"),
                        current_chat=chats["current"])
    assert chats["marked"] in _chats(answer)


async def test_a_fault_of_the_marks_shows_no_chat(world, marks, flags) -> None:
    p, chats = world
    flags("*")
    marks.fail = True
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"),
                        current_chat=chats["current"])
    assert _chats(answer) == {}
    assert answer["skipped_why"] == {"chat": "failed"}
    assert "chat" not in answer["searched"]


# ── Another member, another org ─────────────────────────────────────────────

async def test_member_b_never_sees_a_chat_of_a_and_a_never_sees_b(world, marks, flags) -> None:
    """A and B are in one org, so RLS lets each row through. The member rule
    alone keeps each chat to its member."""
    p, chats = world
    flags("*")
    for_b = await _ask(p, _member(B, p.org_a, "feature:chat"), current_chat="none-current")
    assert set(_chats(for_b)) == {chats["b_own"]}
    assert _chats(for_b)[chats["b_own"]]["snippet"] == "B asks about the Snowflake printer"
    for_a = await _ask(p, _member(A, p.org_a, "feature:chat"),
                       current_chat=chats["current"])
    assert chats["b_own"] not in json.dumps(for_a)
    for name in ("own", "by_mira", "tapped", "legacy", "marked", "room"):
        assert chats[name] not in json.dumps(for_b), name


async def test_org_b_never_sees_a_chat_of_org_a(world, marks, flags) -> None:
    p, chats = world
    flags("*")
    for_c = await _ask(p, _member(C, p.org_b, "feature:chat"), current_chat="none-current")
    assert set(_chats(for_c)) == {chats["c_own"]}
    # A member of org B who names A's address finds nothing of A.
    as_a = await _ask(p, _member(A, p.org_b, "feature:chat"), current_chat="none-current")
    assert _chats(as_a) == {}
    for_a = await _ask(p, _member(A, p.org_a, "feature:chat"),
                       current_chat=chats["current"])
    assert chats["c_own"] not in json.dumps(for_a)


# ── What a hit may read ─────────────────────────────────────────────────────

async def test_a_tool_row_or_a_system_row_makes_no_hit(world, marks, flags) -> None:
    p, chats = world
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"), q=TOOL_WORD,
                        current_chat=chats["current"])
    assert _chats(answer) == {}
    assert TOOL_WORD not in json.dumps(answer["hits"])


async def test_a_reply_of_mira_makes_a_hit_with_no_snippet(world, marks, flags) -> None:
    p, chats = world
    flags("*")
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"), q=MIRA_WORD,
                        current_chat=chats["current"])
    assert set(_chats(answer)) == {chats["own"]}
    hit = _chats(answer)[chats["own"]]
    assert hit["snippet"] == "" and MIRA_WORD not in json.dumps(answer["hits"])
    assert (hit["covers"], hit["of"]) == (1, 1)


# ── The flag off ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("find", "recency"), [("", False), ("", True), ("*", True)])
async def test_flag_off_no_chat_and_the_answer_is_byte_identical(
        world, marks, flags, find, recency) -> None:
    p, chats = world
    flags("", find=find, recency=recency)
    with_chat = await _ask(p, _member(A, p.org_a, "feature:chat"),
                           current_chat=chats["current"])
    without = await _ask(p, _member(A, p.org_a))
    assert json.dumps(with_chat, sort_keys=True) == json.dumps(without, sort_keys=True)
    assert _chats(with_chat) == {}
    # Another org on the list is off for this one too.
    flags(p.org_b, find=find, recency=recency)
    other = await _ask(p, _member(A, p.org_a, "feature:chat"),
                       current_chat=chats["current"])
    assert json.dumps(other, sort_keys=True) == json.dumps(without, sort_keys=True)


async def test_flag_on_for_the_org_finds_the_chats(world, marks, flags) -> None:
    """Non-vacuity of the flag test: the org's own id on the list opens it."""
    p, chats = world
    flags(p.org_a)
    answer = await _ask(p, _member(A, p.org_a, "feature:chat"),
                        current_chat=chats["current"])
    assert chats["own"] in _chats(answer)


# ── One rule of "no other agent" ────────────────────────────────────────────

def test_the_agent_rule_gives_the_answer_of_pz33(world) -> None:
    """``chat_search.ONLY_AGENT_SQL`` is a copy of the agent part of
    ``assistant_summaries.ELIGIBLE_SQL``. On chats where every other part of
    that rule holds, the two give one answer, as the app role reads them."""
    from gateway.assistant_summaries import ELIGIBLE_SQL
    from gateway.chat_search import AGENT, ONLY_AGENT_SQL

    p, chats = world
    names = ("own", "by_mira", "tapped", "legacy", "seat", "agent_row", "null_kind", "tool")
    ids = [chats[n] for n in names]
    eng = create_engine(p.app_url, poolclass=NullPool, future=True)
    try:
        with eng.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": p.org_a})
            rows = c.execute(text(
                f"SELECT s.id, {ONLY_AGENT_SQL} AS mine, {ELIGIBLE_SQL} AS theirs "
                "FROM chat_session s WHERE s.id = ANY(:ids)"),
                {"ids": ids, "agent": AGENT}).fetchall()
    finally:
        eng.dispose()
    by_id = {r.id: (r.mine, r.theirs) for r in rows}
    assert set(by_id) == set(ids)
    assert {n: by_id[chats[n]] for n in names} == {
        "own": (True, True), "by_mira": (True, True), "tapped": (True, True),
        "legacy": (True, True), "seat": (False, False), "agent_row": (False, False),
        "null_kind": (False, False), "tool": (True, True)}
