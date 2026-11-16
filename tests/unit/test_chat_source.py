"""HRD-21 (task 3225, ``find_engine.md`` §3.1 rung 6, §6, D-FIND-5): the
``chat`` source of company search, hermetic.

The R8 twin, ``test_chat_source_r8.py``, runs the SQL as the app role. This
file fences what code decides, each case written so a wrong build fails it:

* **A dark source changes no answer.** With ``CHAT_SEARCH_ORGS`` empty, a
  member who holds ``feature:chat`` and names a current chat gets the answer
  of a member who does not, byte for byte: ``CONTEXT_RECENCY`` off and on,
  ``FIND_ORGS`` off and on, a zero, hits, ``explain``, a spent budget and
  ``apps=chat``. The chat reader is never called, and its session opener
  raises if it is.
* **Pass 1 only.** With the switch open, the source is asked once, with the
  whole query. It is not in the miss rule, so a past chat that holds every
  word does not stop the second pass of the records.
* **Fail closed.** With no current chat the source is not asked, and the
  reader reads nothing. A failed read of the egress marks names the source
  skipped, never "searched" with no hit.
* **The read of the marks is read-only**, and a fault raises.
* **The snippet comes from the member's own typed row only.**
* **The tool** sends the run's own thread, never a model value, and prints a
  past chat with its date and its bracket.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import company_search_tools as cst
from acb_skills import egress
from gateway import chat_search
from gateway.retrieval import company, find, sources

from tests.unit.test_company_search_recency import recency  # noqa: F401
from tests.unit.test_company_search_sources import ALL, ORG, member
from tests.unit.test_find_honest_zero import (  # noqa: F401
    MARKER,
    World,
    chat_orgs,
    find_on,
)

HERE = "t-here-1"
WHEN = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)


class Chats:
    """A fake ``search_own_chats``: one past chat that holds its words."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str | None]] = []
        self.rows = [chat_search.ChatHit(
            session_id="s-1", title="Snowflake order for Sharath", last_activity=WHEN,
            snippet="Sharath wants the Snowflake printer by Friday")]
        self.words = {"s-1": "snowflake order sharath printer zqvortex quasar"}
        self.fail: BaseException | None = None

    async def __call__(self, user: Any, q: str, k: int, *, current: str | None) -> list[Any]:
        self.calls.append((q, k, current))
        if self.fail is not None:
            raise self.fail
        if not current:
            return []
        words = q.lower().split()
        return [r for r in self.rows
                if all(w in self.words[r.session_id].split() for w in words)][:k]


@pytest.fixture()
def w(monkeypatch) -> World:
    """Fake readers of every app but Studio and the past chats. Those two keep
    their real readers and their real switches."""
    world = World()
    for key, source in list(sources.REGISTRY.items()):
        if key in ("studio", find.CHAT_KEY):
            continue
        monkeypatch.setitem(sources.REGISTRY, key, dataclasses.replace(
            source, read=world.reader(key), gate=None,
            near=world.near(key) if source.near is not None else None,
            related=None, open=None,
        ))
    return world


@pytest.fixture()
def chats(monkeypatch) -> Chats:
    """The fake reader, and a session opener that fails the test if the real
    reader ever opens one."""
    fake = Chats()

    def _no_session(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the chat reader opened a session")

    monkeypatch.setattr(chat_search, "search_own_chats", fake)
    monkeypatch.setattr(chat_search, "tenant_session", _no_session)
    return fake


def _route(q: str, user: Any, **given: Any) -> dict[str, Any]:
    return asyncio.run(company.company_search(
        q=q, apps=given.pop("apps", None), k=5, user=user, **given))


def _bare(answer: dict[str, Any]) -> str:
    """The answer as JSON, with each time of the trace at 0."""
    out = dict(answer)
    trace = out.get("trace")
    if isinstance(trace, dict):
        out["trace"] = {**trace, "rungs": [{**r, "ms": 0} for r in trace["rungs"]]}
    return json.dumps(out, sort_keys=True)


# ── A dark source changes no answer ─────────────────────────────────────────

#: (query, explain, a spent budget, apps).
CASES = [
    ("Snowflake printer", False, False, None),
    (f"{MARKER} Quasar", False, False, None),
    ("Snowflake printer", True, False, None),
    (f"{MARKER} Quasar", False, True, None),
    ("Snowflake printer", False, False, "chat"),
]


@pytest.mark.parametrize("recency_on", [False, True])
@pytest.mark.parametrize("find_orgs", ["", "*"])
@pytest.mark.parametrize("case", CASES)
def test_a_dark_chat_source_changes_no_answer(
        w, chats, recency, find_on, chat_orgs, monkeypatch,  # noqa: F811
        recency_on, find_orgs, case) -> None:
    recency(recency_on)
    find_on(find_orgs)
    chat_orgs("")
    q, explain, spent, apps = case
    if spent:
        monkeypatch.setattr(company, "TOTAL_BUDGET_S", 0.0)
    plain = _route(q, member(*ALL), explain=explain)
    with_chat = _route(q, member(*ALL, "chat"), explain=explain, apps=apps,
                       current_chat=HERE)
    assert _bare(with_chat) == _bare(plain)
    assert chats.calls == [], "a dark chat source read"


def test_another_org_on_the_list_is_dark_too(w, chats, recency, find_on, chat_orgs) -> None:  # noqa: F811
    recency(True)
    find_on("*")
    chat_orgs("0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0002")
    assert _bare(_route("Snowflake printer", member(*ALL, "chat"), current_chat=HERE)) == _bare(
        _route("Snowflake printer", member(*ALL)))
    assert chats.calls == []


def test_the_reader_checks_its_switch_itself(chats, chat_orgs) -> None:  # noqa: F811
    """``Source.gate`` does not stop a read, so the reader checks it first."""
    chat_orgs("")
    hits = asyncio.run(sources.REGISTRY["chat"].find(
        member("chat"), "Snowflake", 5, current=HERE))
    assert hits == [] and chats.calls == []


# ── The switch open ─────────────────────────────────────────────────────────

@pytest.fixture()
def open_chats(w, chats, recency, find_on, chat_orgs):  # noqa: F811
    recency(True)
    find_on("*")
    chat_orgs(ORG)
    return chats


def test_a_past_chat_is_a_hit_of_rung_6(w, open_chats) -> None:
    answer = _route("Snowflake printer", member(*ALL, "chat"), current_chat=HERE)
    chat = [h for h in answer["hits"] if h["app"] == "chat"]
    assert chat == [{
        "app": "chat", "kind": "chat", "id": "s-1", "title": "Snowflake order for Sharath",
        "hint": "Your chat", "snippet": "Sharath wants the Snowflake printer by Friday",
        "when": WHEN.isoformat(), "href": "/chat?do=open-chat&fill.session=s-1",
        "stamp": WHEN.isoformat(), "matched_by": "chat", "covers": 2, "of": 2,
    }]
    assert "chat" in answer["searched"]
    # The record that holds both words comes first: the lower rung wins a tie.
    assert answer["hits"][0]["app"] != "chat"
    assert open_chats.calls == [("Snowflake printer", 5, HERE)]


def test_the_chat_source_runs_in_pass_1_only(w, open_chats) -> None:
    answer = _route(f"{MARKER} Quasar", member(*ALL, "chat"), current_chat=HERE)
    assert answer["broadened"], "the records were asked again, each word alone"
    assert open_chats.calls == [(f"{MARKER} Quasar", 5, HERE)]


def test_a_chat_with_every_word_does_not_stop_pass_2(w, open_chats) -> None:
    """The miss rule reads the records only (D-FIND-4), so a past chat that
    holds every word still lets the second pass look for the records."""
    answer = _route(f"{MARKER} Quasar", member(*ALL, "chat"), current_chat=HERE)
    assert [h["id"] for h in answer["hits"]] == ["s-1"]
    assert w.asked(MARKER.lower()) and w.asked("quasar")


def test_no_current_chat_asks_no_past_chat(w, open_chats) -> None:
    answer = _route(f"{MARKER} Quasar", member(*ALL, "chat"))
    assert open_chats.calls == []
    assert "chat" not in answer["searched"]
    assert answer["where_else"][0] == {"why": "past_chats"}


@pytest.mark.parametrize("current", ["", "   ", "x" * (company.CURRENT_CHAT_MAX + 1)])
def test_a_current_chat_that_is_empty_or_too_long_asks_nothing(w, open_chats, current) -> None:
    _route("Snowflake printer", member(*ALL, "chat"), current_chat=current)
    assert open_chats.calls == []


def test_a_member_without_the_chat_app_gets_no_past_chat(w, open_chats) -> None:
    answer = _route("Snowflake printer", member(*ALL), current_chat=HERE)
    assert open_chats.calls == [] and "chat" not in answer["searched"]


def test_a_search_that_asked_the_chats_names_no_past_chats_line(w, open_chats) -> None:
    open_chats.rows = []
    answer = _route(f"{MARKER} Quasar", member(*ALL, "chat"), current_chat=HERE)
    assert {"why": "past_chats"} not in answer["where_else"]
    assert not any(e.get("app") == "chat" for e in answer["where_else"])


def test_a_failed_read_is_skipped_never_searched(w, open_chats) -> None:
    open_chats.fail = RuntimeError("redis down")
    answer = _route(f"{MARKER} Quasar", member(*ALL, "chat"), current_chat=HERE)
    assert "chat" in answer["skipped"] and "chat" not in answer["searched"]
    assert answer["skipped_why"]["chat"] == "failed"
    assert {"why": "skipped", "app": "chat", "reason": "failed"} in answer["where_else"]
    assert "Where else: chat failed" in cst.format_answer(answer, dated=True)


def test_the_trace_gives_rung_6_its_one_row(w, open_chats) -> None:
    answer = _route("Snowflake printer", member(*ALL, "chat"), current_chat=HERE,
                    explain=True)
    rows = answer["trace"]["rungs"]
    assert [r["rung"] for r in rows if r["source"] == "chat"] == ["chat"]
    row = rows[-1]
    assert (row["rung"], row["source"], row["ran"], row["hits"], row["outcome"]) == (
        "chat", "chat", True, 1, "ok")
    assert "Past chats: 1 hit" in cst.format_trace(answer)


def test_the_answer_of_kr1_holds_the_past_chat_too(w, open_chats, recency) -> None:  # noqa: F811
    recency(False)
    answer = _route("Snowflake printer", member(*ALL, "chat"), current_chat=HERE)
    chat = [h for h in answer["hits"] if h["app"] == "chat"]
    assert [list(h) for h in chat] == [
        ["app", "kind", "id", "title", "hint", "snippet", "when", "href"]]


def test_no_tenant_or_member_comes_from_the_request(w, open_chats) -> None:
    """The source passes the session's own member. The current chat is the
    only value of the request that it reads, and it only takes a chat away."""
    user = member(*ALL, "chat")
    _route("Snowflake printer", user, current_chat=HERE)
    assert open_chats.calls == [("Snowflake printer", 5, HERE)]


# ── The reader ──────────────────────────────────────────────────────────────

class _Db:
    """A fake async session: it answers the statement with *rows*."""

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.sql: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.sql.append(str(statement))
        return SimpleNamespace(fetchall=lambda: list(self.rows))


def _opener(db: _Db, seen: list[str]):
    @asynccontextmanager
    async def _session(org: str):
        seen.append(org)
        yield db
    return _session


def _row(sid: str, said: str | None = None) -> Any:
    return SimpleNamespace(id=sid, title=f"Chat {sid}", updated_at=WHEN, said=said)


@pytest.mark.parametrize(("user", "current", "k"), [
    (member("chat", org=None), HERE, 5),
    (SimpleNamespace(email=None, organization_id=ORG), HERE, 5),
    (member("chat"), None, 5),
    (member("chat"), "  ", 5),
    (member("chat"), "x" * (chat_search.CURRENT_MAX + 1), 5),
    (member("chat"), HERE, 0),
])
def test_the_reader_fails_closed(monkeypatch, user, current, k) -> None:
    seen: list[str] = []
    monkeypatch.setattr(chat_search, "tenant_session", _opener(_Db([_row("s-1")]), seen))
    assert asyncio.run(chat_search.search_own_chats(user, "Snowflake", k, current=current)) == []
    assert seen == [], "the reader opened a session"


def test_the_reader_drops_each_marked_chat(monkeypatch) -> None:
    seen: list[str] = []
    db = _Db([_row("s-1", "Plan the Snowflake order"), _row("s-2"), _row("s-3")])
    monkeypatch.setattr(chat_search, "tenant_session", _opener(db, seen))

    async def _marked(sessions: Any) -> frozenset[str]:
        assert list(sessions) == ["s-1", "s-2", "s-3"]
        return frozenset({"s-2"})

    monkeypatch.setattr(egress, "marked_sessions", _marked)
    hits = asyncio.run(chat_search.search_own_chats(member("chat"), "Snowflake", 5,
                                                     current=HERE))
    assert [h.session_id for h in hits] == ["s-1", "s-3"]
    assert hits[0].snippet == "Plan the Snowflake order" and hits[1].snippet == ""
    assert seen == [ORG]
    # The LOCAL statement limit first, then the one search.
    assert "statement_timeout" in db.sql[0] and len(db.sql) == 2


def test_a_fault_of_the_marks_raises(monkeypatch) -> None:
    monkeypatch.setattr(chat_search, "tenant_session", _opener(_Db([_row("s-1")]), []))

    async def _down(sessions: Any) -> frozenset[str]:
        raise ConnectionError("redis down")

    monkeypatch.setattr(egress, "marked_sessions", _down)
    with pytest.raises(ConnectionError):
        asyncio.run(chat_search.search_own_chats(member("chat"), "Snowflake", 5,
                                                 current=HERE))


class TestTheStatement:
    """Each rule of D-FIND-5 is in the WHERE of the one statement."""

    sql = " ".join(chat_search.search_sql(2).split())

    @pytest.mark.parametrize("rule", [
        "s.user_id = :uid",
        "s.organization_id = CAST(NULLIF(current_setting('app.tenant_id', true), '') AS uuid)",
        "s.agent_name = :agent",
        "s.channel IS NULL",
        "s.id <> :current",
        "m.role IN ('user', 'assistant')",
        "LIMIT :sessions",
        "LIMIT :rows",
        "left(m.content, :chars)",
        "r.body ILIKE :w0 AND r.body ILIKE :w1",
        "mine.title ILIKE :w0 AND mine.title ILIKE :w1",
        "LIMIT :pool",
    ])
    def test_each_rule_is_in_the_statement(self, rule: str) -> None:
        assert rule in self.sql

    def test_the_solo_rule_and_the_agent_rule_are_the_shared_text(self) -> None:
        from gateway.rooms import SOLO_ROOM_SQL

        assert " ".join(SOLO_ROOM_SQL.split()) in self.sql
        assert " ".join(chat_search.ONLY_AGENT_SQL.split()) in self.sql

    def test_only_a_typed_row_of_the_member_gives_text(self) -> None:
        """The statement selects the text of a row only through the member
        rule, and it reads no system row and no tool event."""
        assert "CASE WHEN ( r.role = 'user' AND r.member_typed IS TRUE" in self.sql
        assert self.sql.rstrip().count("SELECT mine.id, mine.title, mine.updated_at, said.said") == 1
        assert "'system'" not in self.sql and "tool_events" not in self.sql
        assert "member_summary" not in self.sql


#: A typed row whose first word comes after 124 characters.
LONG_SAID = "x " * 60 + "the Snowflake order"


@pytest.mark.parametrize(("said", "snippet"), [
    ("Plan the Snowflake order for Friday", "Plan the Snowflake order for Friday"),
    # The passage starts SNIPPET_LEAD characters before the first word.
    (LONG_SAID, "…" + LONG_SAID[124 - chat_search.SNIPPET_LEAD:].lstrip()),
    # A pasted mail and a long paste are not typed words.
    ("From: Ravi <ravi@x.test>\nThe Snowflake order", ""),
    ("Snowflake " * 200, ""),
])
def test_the_snippet_is_the_members_typed_words(said: str, snippet: str) -> None:
    assert LONG_SAID.index("Snowflake") == 124
    assert chat_search.snippet_of(said, ["snowflake"]) == snippet


def test_a_card_answer_gives_no_snippet() -> None:
    from acb_common.assistant_profile import typed_text
    from gateway.chat_recovery import compose_card_answer

    answer = compose_card_answer("Delete the Snowflake deal?", "approve")
    # Non-vacuity: the paste rule passes it, so the card rule is what stops it.
    assert typed_text(answer).strip() and "Snowflake" in answer
    assert chat_search.snippet_of(answer, ["snowflake"]) == ""


# ── The read of the marks ───────────────────────────────────────────────────

class _Redis:
    """A fake tenant Redis client. It records each command it gets."""

    def __init__(self, marked: set[str], fail: bool = False) -> None:
        self.marked = marked
        self.fail = fail
        self.commands: list[str] = []

    async def mget(self, keys: list[Any]) -> list[Any]:
        self.commands.append("mget")
        if self.fail:
            raise ConnectionError("redis down")
        return ["1" if k.value.rsplit(":", 1)[-1] in self.marked else None for k in keys]

    def __getattr__(self, name: str) -> Any:
        async def _other(*_a: Any, **_k: Any) -> Any:
            self.commands.append(name)
        return _other


@pytest.fixture()
def bound(monkeypatch):
    from acb_common.db import bind_tenant, release_tenant

    egress._MARKED_SESSIONS.clear()
    token = bind_tenant(ORG)
    yield
    release_tenant(token)
    egress._MARKED_SESSIONS.clear()


def test_the_marks_are_read_once_and_read_only(bound, monkeypatch) -> None:
    fake = _Redis({"s-red"})
    monkeypatch.setattr(egress, "get_tenant_redis", lambda: fake)
    egress._MARKED_SESSIONS.add((ORG, "s-cached"))
    egress._MARKED_SESSIONS.add((ORG, "s-other"))
    order = list(egress._MARKED_SESSIONS._items)
    found = asyncio.run(egress.marked_sessions(["s-cached", "s-red", "s-clear"]))
    assert found == {"s-cached", "s-red"}
    assert fake.commands == ["mget"], "no TTL slides and no mark is written"
    assert list(egress._MARKED_SESSIONS._items) == order, "the cache did not change"


def test_a_fault_of_redis_raises(bound, monkeypatch) -> None:
    monkeypatch.setattr(egress, "get_tenant_redis", lambda: _Redis(set(), fail=True))
    with pytest.raises(ConnectionError):
        asyncio.run(egress.marked_sessions(["s-1"]))


def test_no_tenant_raises() -> None:
    with pytest.raises(RuntimeError):
        asyncio.run(egress.marked_sessions(["s-1"]))


# ── The tool ────────────────────────────────────────────────────────────────

CHAT_HIT = {"app": "chat", "kind": "chat", "id": "s-1", "title": "Snowflake order",
            "hint": "Your chat", "snippet": "the Snowflake printer",
            "when": WHEN.isoformat(), "stamp": WHEN.isoformat(),
            "href": "/chat?do=open-chat&fill.session=s-1"}


def test_a_past_chat_prints_its_date_and_its_bracket() -> None:
    """The date is ``chat_session.updated_at``. A rename moves it too
    (``routes/chat.py``), so the word is "updated", never "last message"."""
    plain = cst._hit_lines(CHAT_HIT)
    assert plain[0] == ("- «Snowflake order» (Your chat, updated 2026-11-12) "
                        "/chat?do=open-chat&fill.session=s-1")
    dated = cst._hit_lines({**CHAT_HIT, "matched_by": "chat", "covers": 2, "of": 2},
                           dated=True)
    assert dated[0] == ("- «Snowflake order» (Your chat, updated 2026-11-12) "
                        "/chat?do=open-chat&fill.session=s-1 [past chat · 2 of 2 words]")
    assert dated[1] == '  full_id: chat:s-1  "the Snowflake printer"'
    assert "message" not in cst.WHEN_WORDS["chat"]


def test_no_claim_prints_above_a_past_chat_with_find_off(
        w, chats, recency, find_on, chat_orgs) -> None:  # noqa: F811
    """``CONTEXT_RECENCY`` on and ``FIND_ORGS`` off (KR-ZERO). The records
    hold nothing, so the route asks each word alone, and a past chat holds
    both words. "No record holds all of the words" would be false above it."""
    recency(True)
    find_on("")
    chat_orgs(ORG)
    answer = _route(f"{MARKER} Quasar", member(*ALL, "chat"), current_chat=HERE)
    assert answer["broadened"] and [h["app"] for h in answer["hits"]] == ["chat"]
    lines = cst.format_answer(answer, dated=True).splitlines()
    assert cst.NO_RECORD_HOLDS_ALL not in "\n".join(lines)
    assert lines[1] == f"{cst.EACH_WORD_OPENING} {MARKER} (0), Quasar (0)."
    # Non-vacuity: with the chat dark, the line of KR-ZERO keeps its claim.
    chat_orgs("")
    dark = cst.format_answer(_route(f"{MARKER} Quasar", member(*ALL, "chat"),
                                    current_chat=HERE), dated=True)
    assert dark.splitlines()[1] == f"{cst.BROADENED_OPENING} {MARKER} (0), Quasar (0)."


def test_the_tool_sends_the_runs_own_thread(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []

    async def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
        sent.append(dict(params))
        return {"query": "x", "hits": [], "skipped": []}

    monkeypatch.setattr(cst, "_get", _get)
    monkeypatch.setattr(cst, "_close_profile_writes", lambda: None)
    monkeypatch.setattr(cst, "_current_chat", lambda: "t-run")
    asyncio.run(cst.search_company("Snowflake"))
    asyncio.run(cst.search_company_dated("Snowflake"))
    assert [p.get("current_chat") for p in sent] == ["t-run", "t-run"]
    monkeypatch.setattr(cst, "_current_chat", lambda: None)
    asyncio.run(cst.search_company("Snowflake"))
    assert "current_chat" not in sent[-1], "no thread sends the request of before"


@pytest.fixture()
def solo():
    """The run on this frame is the member's own solo run, as the run
    boundary binds it (``executor._bind_solo_room``)."""
    from orchestrator import executor

    token = executor._bind_solo_room(True)
    yield
    executor._release_solo_room(token)


def test_only_a_solo_run_sends_its_thread(monkeypatch) -> None:
    """A room, and a room of one that other members can read, bind
    ``solo_room=False``. The tool sends no thread there, so the route asks no
    past chat, and none prints into a chat that another person can open."""
    from orchestrator import executor

    monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: "t-run")
    assert executor.run_is_solo() is False, "a frame with no run is not solo"
    assert cst._current_chat() is None
    for value, sent in ((False, None), (True, "t-run")):
        token = executor._bind_solo_room(value)
        try:
            assert executor.run_is_solo() is value
            assert cst._current_chat() == sent
        finally:
            executor._release_solo_room(token)


def test_the_thread_comes_from_the_run(monkeypatch, solo) -> None:
    from orchestrator import executor

    monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: " t-run ")
    assert cst._current_chat() == "t-run"
    monkeypatch.setattr(executor, "resolve_relay_thread_id",
                        lambda: "x" * (cst.CURRENT_CHAT_MAX + 1))
    assert cst._current_chat() is None
    monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: None)
    assert cst._current_chat() is None

    def _boom() -> str:
        raise RuntimeError("no run")

    monkeypatch.setattr(executor, "resolve_relay_thread_id", _boom)
    assert cst._current_chat() is None
    assert cst.CURRENT_CHAT_MAX == company.CURRENT_CHAT_MAX == chat_search.CURRENT_MAX


def test_a_batch_run_sends_no_thread(monkeypatch, solo) -> None:
    """A run with no chat gets a thread that the executor makes
    (``<agent>:<run id>``), with ``batch_thread`` bound (H-227). It names no
    chat, so the tool sends nothing, and the route asks no past chat."""
    from acb_skills.write_artifact import artifact_context_scope, bind_artifact_context
    from orchestrator import executor

    monkeypatch.setattr(executor, "resolve_relay_thread_id", lambda: "orchestrator:run-1")
    with artifact_context_scope():
        bind_artifact_context(session_id="orchestrator:run-1", batch_thread=True)
        assert cst._current_chat() is None
    with artifact_context_scope():
        bind_artifact_context(session_id="t-chat", batch_thread=False)
        assert cst._current_chat() == "orchestrator:run-1"


def test_the_schema_every_org_reads_names_no_past_chats() -> None:
    """The docstrings are shared by every org, and the source is dark, so the
    model is not told of a source that its org may lack."""
    for tool in (cst.search_company, cst.search_company_dated):
        assert "chat" not in (tool.__doc__ or "").lower()
