"""R8 — HRD-17e-1 (task 3646): no shrink before the seal, on a REAL Postgres.

Spec: ``project-docs/specs/chat_failure_modes.md`` §6 row HRD-17e-1.

**The check says YES.** ``routes/chat.py`` ``_MESSAGE_UPSERT_SQL`` sets
``content = EXCLUDED.content`` with no length test. A client write of an
agent row passes its ``WHERE`` while ``run_final_at`` is NULL and the run
member matches. So a client write can make a stored reply shorter before the
fold seals it. ``test_flag_off_a_shorter_client_write_wins_as_today`` pins
that baseline.

The real message seam and the real ``POST /chat/sessions/{id}/messages``
handler run as the NON-privileged role of the phase-4 rehearsal (no superuser,
no BYPASSRLS), on a database with the whole ladder and two organizations.
Only the two flags are set in the test.

What this proves:

* flag off: the upsert runs ``_MESSAGE_UPSERT_SQL`` byte for byte, with no
  other statement, and a shorter client write wins, as today;
* shadow: the shorter write still wins, and ONE line names the lengths and
  no text;
* enforce: a shorter client write before the seal keeps the stored text. A
  longer write, an extending write, an equal write and a write of equal
  length pass. The fold still writes shorter text and seals the row. The
  mint changes nothing. A human row of the same member still takes shorter
  text. A write after the seal stays refused. An org with the flag off keeps
  the old behaviour;
* characters, not bytes: a text with fewer characters and more bytes is
  shorter. An empty new text over a stored text is shorter. An empty stored
  text takes any text. ``content`` is ``NOT NULL``, so no stored NULL exists;
* the known cost of enforce: one healthy tab clears ``content`` at each tool
  start (``app/api/agent/chat/route.ts``), and enforce keeps the narration
  until a longer write or the fold.

**The mutations (R7).** Copy the file, change it, run, and restore it with
``cp`` (``cmp`` then says the file is the same again).

* In ``_NO_SHRINK_ENFORCE_SQL`` make the content line ``EXCLUDED.content``
  again (remove the guard): ``test_enforce_keeps_the_stored_text_of_a_shorter_client_write``
  goes red.
* In ``_no_shrink_sql`` drop ``or author_from_run`` (guard the fold too):
  ``test_enforce_the_fold_still_writes_shorter_text_and_seals`` goes red.
* In ``_no_shrink_sql`` drop ``kind != "agent"`` (guard a human row):
  ``test_enforce_a_human_row_of_the_same_member_still_takes_shorter_text``
  goes red.
* In ``chat_no_shrink.log_shrink`` log the text: the no-text asserts of
  ``test_shadow_a_shorter_client_write_still_wins_and_logs_one_line`` go red.
* In ``chat_no_shrink.no_shrink_mode`` return ON for each org when the list
  is not empty (drop the per-org check):
  ``test_enforce_is_per_org`` goes red.
* In ``_no_shrink_sql`` give the variant with the flag off (drop
  ``mode == OFF or``): ``test_flag_off_a_shorter_client_write_wins_as_today``
  goes red.

Run, on a pgvector server of your own::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/acb_tenant \\
        uv run pytest tests/unit/test_chat_upsert_no_shrink_r8.py -v -rs
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db
import structlog
from acb_common import get_settings
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

AGENT = "orchestrator"
SECRET = "The Acme invoice INV-2041 is 41,200 rupees, due 30 Nov."
TS = 1_800_000_000_000


def _address(tag: str) -> str:
    return f"{tag}.{uuid.uuid4().hex[:8]}@noshrink.test"


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
    ``world.upserts`` holds the text of each ``chat_message`` upsert that
    ran, and ``world.statements`` the count of every statement."""
    eng = create_engine(world.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    world.upserts = []
    world.statements = 0

    def _seen(conn, clause, multiparams, params, execution_options):
        world.statements += 1
        sql = getattr(clause, "text", "")
        if "INSERT INTO chat_message" in sql:
            world.upserts.append(sql)

    event.listen(eng, "before_execute", _seen)
    get_settings.cache_clear()
    try:
        yield world
    finally:
        get_settings.cache_clear()
        eng.dispose()


def _flags(monkeypatch, *, enforce: str = "", shadow: str = "") -> None:
    monkeypatch.setattr(get_settings(), "chat_no_shrink_orgs", enforce)
    monkeypatch.setattr(get_settings(), "chat_no_shrink_shadow_orgs", shadow)


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
        c.execute(text("DELETE FROM app_user WHERE email = :u"), {"u": email})


def _client(user):
    """The real message save behind FastAPI, as the browser reaches it."""
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import save_messages

    app = FastAPI()
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class Chat:
    """One chat of one member, with one minted agent row ``a1``."""

    def __init__(self, ns, org: str, tag: str) -> None:
        from gateway.routes.agent import _mint_run_row
        from gateway.routes.chat import SessionUpsertRequest, _upsert_session

        self.ns, self.org = ns, org
        self.email = _address(tag)
        _seed_member(ns, self.email, org)
        self.user = _member(self.email, org)
        self.sid = _sid(tag)
        _upsert_session(self.email, SessionUpsertRequest(id=self.sid), organization_id=org)
        _mint_run_row(self.sid, "a1", member=self.email, agent_name=AGENT,
                      organization_id=org)

    def save(self, content: str, mid: str = "a1", role: str = "assistant") -> dict:
        """A client save of one row, through the real route."""
        turn = {"id": mid, "role": role, "content": content, "timestamp": TS,
                "tool_events": [], "progress_lines": [], "agent_state": None,
                "custom_events": []}
        if role == "assistant":
            turn |= {"author_kind": "agent", "author_email": AGENT}
        got = _client(self.user).post(f"/chat/sessions/{self.sid}/messages", json=[turn])
        assert got.status_code == 200, got.text
        return got.json()

    def fold(self, content: str, mid: str = "a1") -> list[str]:
        """The run's fold, as ``chat_fold.persist_final_assistant_message``
        writes it."""
        from gateway.routes.chat import MessageRecord, _upsert_messages

        return _upsert_messages(
            self.sid, [MessageRecord(id=mid, role="assistant", content=content,
                                     timestamp=TS + 1)],
            actor_email=self.email, agent_name=AGENT, author_from_run=True,
            organization_id=self.org)

    def row(self, mid: str = "a1") -> Any:
        with self.ns.admin_engine.connect() as c:
            return c.execute(text(
                "SELECT content, run_final_at IS NOT NULL AS sealed, author_kind "
                "FROM chat_message WHERE session_id = :s AND id = :m"),
                {"s": self.sid, "m": mid}).first()

    def close(self) -> None:
        _unseed_member(self.ns, self.email)


def _lines(caps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in caps if str(c.get("event", "")).startswith("chat_no_shrink")]


# ── Flag off: the baseline of today ──────────────────────────────────────────


def test_flag_off_a_shorter_client_write_wins_as_today(graph_as_app, monkeypatch):
    from gateway.routes.chat import _MESSAGE_UPSERT_SQL

    ns = graph_as_app
    # Org B is listed, and org A is not: org A runs the text of before.
    _flags(monkeypatch, enforce=ns.org_b, shadow=ns.org_b)
    chat = Chat(ns, ns.org_a, "off")
    try:
        assert chat.save(SECRET)["unchanged"] == []
        ns.upserts.clear()
        before = ns.statements
        with structlog.testing.capture_logs() as caps:
            assert chat.save("The Acme")["unchanged"] == []
        assert chat.row().content == "The Acme"
        # The text of before, byte for byte.
        assert ns.upserts == [_MESSAGE_UPSERT_SQL]
        assert _lines(caps) == []
        off = ns.statements - before
        # Neither arm adds a statement: shadow reads the old length inside
        # the upsert, and enforce tests it inside the upsert.
        for mode in ("shadow", "enforce"):
            _flags(monkeypatch, **{mode: ns.org_a})
            before = ns.statements
            chat.save(SECRET)
            assert ns.statements - before == off, mode
    finally:
        chat.close()


# ── Shadow ───────────────────────────────────────────────────────────────────


def test_shadow_a_shorter_client_write_still_wins_and_logs_one_line(graph_as_app, monkeypatch):
    from gateway.routes.chat import _NO_SHRINK_SHADOW_SQL

    ns = graph_as_app
    _flags(monkeypatch, shadow=ns.org_a)
    chat = Chat(ns, ns.org_a, "shadow")
    try:
        with structlog.testing.capture_logs() as caps:
            chat.save(SECRET[:8])                 # grows from the mint: no line
            chat.save(SECRET)                     # grows: no line
            chat.save(SECRET)                     # equal: no line
            ns.upserts.clear()
            assert chat.save(SECRET[:8])["unchanged"] == []   # shorter
        assert chat.row().content == SECRET[:8]   # the write of before wins
        assert ns.upserts == [_NO_SHRINK_SHADOW_SQL]
        (line,) = _lines(caps)
        assert line["event"] == "chat_no_shrink_shadow"
        assert (line["old_chars"], line["new_chars"], line["prefix"]) == (
            len(SECRET), 8, True)
        assert (line["thread_id"], line["message_id"], line["org"]) == (
            chat.sid[:12], "a1", ns.org_a)
        dump = repr(caps)
        assert SECRET not in dump and SECRET[:8] not in dump and "INV-2041" not in dump
    finally:
        chat.close()


# ── Enforce ──────────────────────────────────────────────────────────────────


def test_enforce_keeps_the_stored_text_of_a_shorter_client_write(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "enforce")
    try:
        chat.save(SECRET)
        with structlog.testing.capture_logs() as caps:
            got = chat.save("The Acme invoice")
        # The id still counts as saved: the other columns merge.
        assert got["unchanged"] == []
        row = chat.row()
        assert (row.content, row.sealed) == (SECRET, False)
        (line,) = _lines(caps)
        assert (line["event"], line["log_level"]) == ("chat_no_shrink_refused", "warning")
        assert (line["old_chars"], line["new_chars"], line["prefix"]) == (
            len(SECRET), 16, True)
        assert SECRET not in repr(caps)
        # A shorter text that does not start the stored one is refused too.
        chat.save("Done.")
        assert chat.row().content == SECRET
    finally:
        chat.close()


def test_enforce_lets_a_longer_an_extending_and_an_equal_write_through(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "grow")
    try:
        with structlog.testing.capture_logs() as caps:
            for text_ in ("The", "The Acme", SECRET, SECRET):
                chat.save(text_)
                assert chat.row().content == text_
            other = "x" * len(SECRET)             # equal length, other text
            chat.save(other)
            assert chat.row().content == other
            chat.save(other + " and more")        # longer, not an extension
            assert chat.row().content == other + " and more"
        assert _lines(caps) == []
    finally:
        chat.close()


def test_enforce_the_fold_still_writes_shorter_text_and_seals(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "fold")
    try:
        chat.save(SECRET)
        with structlog.testing.capture_logs() as caps:
            assert chat.fold("Paid.") == []
        row = chat.row()
        assert (row.content, row.sealed) == ("Paid.", True)
        assert _lines(caps) == []
    finally:
        chat.close()


def test_enforce_the_mint_changes_nothing(graph_as_app, monkeypatch):
    from gateway.routes.agent import _mint_run_row
    from gateway.routes.chat import _MESSAGE_UPSERT_SQL

    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "mint")
    try:
        chat.save(SECRET)
        ns.upserts.clear()
        _mint_run_row(chat.sid, "a1", member=chat.email, agent_name=AGENT,
                      organization_id=ns.org_a)
        assert chat.row().content == SECRET
        _mint_run_row(chat.sid, "a2", member=chat.email, agent_name=AGENT,
                      organization_id=ns.org_a)
        assert chat.row("a2").content == ""
        assert ns.upserts == [_MESSAGE_UPSERT_SQL, _MESSAGE_UPSERT_SQL]
    finally:
        chat.close()


def test_enforce_a_human_row_of_the_same_member_still_takes_shorter_text(
    graph_as_app, monkeypatch,
):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "human")
    try:
        chat.save("Please find the Acme invoice and tell me the total", mid="u1", role="user")
        with structlog.testing.capture_logs() as caps:
            assert chat.save("Acme total?", mid="u1", role="user")["unchanged"] == []
        row = chat.row("u1")
        assert (row.content, row.author_kind) == ("Acme total?", "human")
        assert _lines(caps) == []
    finally:
        chat.close()


def test_enforce_a_write_after_the_seal_stays_refused(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "sealed")
    try:
        chat.save(SECRET[:20])
        assert chat.fold(SECRET) == []
        for late in ("Old", SECRET + " and more"):
            assert chat.save(late)["unchanged"] == ["a1"]
            assert chat.row().content == SECRET
    finally:
        chat.close()


def test_enforce_is_per_org(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    a = Chat(ns, ns.org_a, "orga")
    b = Chat(ns, ns.org_b, "orgb")
    try:
        for chat in (a, b):
            chat.save(SECRET)
            chat.save("Short")
        assert a.row().content == SECRET
        assert b.row().content == "Short"
    finally:
        a.close()
        b.close()


def test_enforce_counts_characters_not_bytes(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "unicode")
    try:
        chat.save("abcdefgh")                      # 8 characters, 8 bytes
        wide = "é" * 7                             # 7 characters, 14 bytes
        assert len(wide.encode("utf-8")) > len("abcdefgh")
        chat.save(wide)
        assert chat.row().content == "abcdefgh"
        hindi = "नमस्ते दुनिया"
        chat.save(hindi + "!")                     # more characters: passes
        assert chat.row().content == hindi + "!"
        with structlog.testing.capture_logs() as caps:
            chat.save("नमस्ते")                     # a shorter start: refused
        assert chat.row().content == hindi + "!"
        (line,) = _lines(caps)
        assert (line["old_chars"], line["new_chars"], line["prefix"]) == (
            len(hindi) + 1, len("नमस्ते"), True)
        chat.save(hindi + "!!")                    # grows again: passes
        assert chat.row().content == hindi + "!!"
    finally:
        chat.close()


def test_enforce_empty_texts_and_no_stored_null(graph_as_app, monkeypatch):
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "empty")
    try:
        assert chat.row().content == ""            # the mint
        chat.save("")                              # equal: passes
        assert chat.row().content == ""
        chat.save("Hi")                            # over an empty text: passes
        assert chat.row().content == "Hi"
        with structlog.testing.capture_logs() as caps:
            chat.save("")                          # empty over a text: refused
        assert chat.row().content == "Hi"
        (line,) = _lines(caps)
        assert (line["old_chars"], line["new_chars"], line["prefix"]) == (2, 0, True)
        with ns.admin_engine.connect() as c:
            nullable = c.execute(text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'chat_message' AND column_name = 'content'")).scalar()
        assert nullable == "NO"
    finally:
        chat.close()


def test_enforce_the_tool_start_clear_of_one_tab(graph_as_app, monkeypatch):
    """The known cost of enforce (``route.ts`` clears ``assistantContent`` at
    each tool start, and saves each 3 s). One healthy tab writes narration,
    then an empty text, then a short answer. Enforce keeps the narration until
    a longer write or the fold. The fold writes the true answer."""
    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a)
    chat = Chat(ns, ns.org_a, "toolclear")
    narration = "Let me look in your inbox for the Acme invoice."
    try:
        chat.save(narration)
        assert chat.row().content == narration
        chat.save("")                              # the tool start clears it
        assert chat.row().content == narration     # kept: the stale narration
        chat.save("Found it.")                     # a short answer: kept too
        assert chat.row().content == narration
        answer = "Found it. " + SECRET
        chat.save(answer)                          # longer: passes
        assert chat.row().content == answer
        assert chat.fold("Found it.") == []        # the fold writes and seals
        row = chat.row()
        assert (row.content, row.sealed) == ("Found it.", True)
    finally:
        chat.close()


def test_enforce_a_reminder_post_still_writes_its_lead(graph_as_app, monkeypatch):
    """``chat_brief._post_row`` writes its lead as a client write over the
    empty row that the mint made. Enforce leaves it as it was."""
    from gateway.routes.chat_brief import DEFAULT_AGENT, _post_row
    from gateway.routes.chat_reminder import reminder_event, reminder_message_id

    ns = graph_as_app
    _flags(monkeypatch, enforce=ns.org_a, shadow=ns.org_a)
    chat = Chat(ns, ns.org_a, "reminder")
    try:
        rid = reminder_message_id(uuid.uuid4())
        with structlog.testing.capture_logs() as caps:
            assert _post_row(
                chat.sid, rid, member=chat.email, agent_name=DEFAULT_AGENT,
                organization_id=ns.org_a, lead="Call the bank, due at 11:00.",
                event=reminder_event({"title": "Call the bank"}), now_ms=TS)
        row = chat.row(rid)
        assert (row.content, row.sealed) == ("Call the bank, due at 11:00.", True)
        assert _lines(caps) == []
    finally:
        chat.close()
