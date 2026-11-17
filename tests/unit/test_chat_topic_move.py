"""The topic of a chat, with no database (WS-CO CO-2b, task 3588).

Spec: ``project-docs/specs/chat_organization.md`` §6 and §10 (D163). Module:
``gateway/chat_session_member.py`` (the one writer of the row), with the
topic rule of ``gateway/chat_topic.py``. Route: ``PUT
/chat/sessions/{id}/topic`` and the list ``GET /chat/sessions``
(``gateway/routes/chat.py``). The R8 half is ``test_chat_topic_move_r8.py``.

Fences (R7). Each docstring names the mutation that turns it red:

* ``chat-topic-move-writer``: only the store module writes ``topic_id`` and
  ``set_by``, and only it and ``chat_topic.py`` name the values of
  ``set_by`` in Python. The one-module fence of ``test_chat_pin.py`` keeps
  every other file away from the table.
* ``chat-topic-move-pass``: the pass writes ``set_by = 'auto'`` only where
  ``set_by`` is NULL or ``'auto'``. A move of the member always wins.
* ``chat-topic-move-lock-order``: a move and the pass lock the topic FIRST,
  under the member rule, and then write the row. The merge moves the rows of
  the source after its locks and before its delete.
* ``chat-topic-move-dark``: with ``CHAT_TOPICS_ORGS`` off, the route answers
  404 before any SQL and before it reads the body. The list sends no topic
  key.
* ``chat-topic-move-session-identity``: the member is the session's. A body
  that names any key but ``topic_id`` gets 422.
* ``chat-topic-move-expand-only``: the migration adds two nullable columns,
  two CHECKs, one foreign key and two indexes, and changes nothing else.
"""

from __future__ import annotations

import inspect
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
STORE = GATEWAY / "chat_session_member.py"
TOPICS = GATEWAY / "chat_topic.py"
_MIGRATION_MARK = "ADD COLUMN IF NOT EXISTS topic_id UUID"

#: A write of the topic columns, or a value of ``set_by`` in a statement.
_WRITES_TOPIC = re.compile(r"(?i)\bset\s+topic_id\s*=|\bset_by\s*=\s*'(?:auto|member)'")
#: The two files that may name them: the writer, and the doc of the rule.
ALLOWED_PY = {STORE, TOPICS}


def _migration() -> Path:
    hits = [p for p in sorted((ROOT / "infra" / "postgres").glob("[0-9]*.sql"))
            if _MIGRATION_MARK in p.read_text(encoding="utf-8")]
    assert len(hits) == 1, hits
    return hits[0]


def _py_files() -> list[Path]:
    out: list[Path] = []
    for root in (ROOT / "apps", ROOT / "packages", ROOT / "scripts"):
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(ROOT).parts)
            if not parts & {".venv", "node_modules", "__pycache__", "tests"}:
                out.append(path)
    return out


# ── chat-topic-move-writer ──────────────────────────────────────────────────


def _writers(files: list[Path]) -> set[Path]:
    return {p for p in files if _WRITES_TOPIC.search(p.read_text(encoding="utf-8",
                                                                 errors="replace"))}


def test_only_the_store_writes_the_topic_columns() -> None:
    """Mutation: write ``UPDATE ... SET topic_id = ...`` in ``routes/chat.py``
    or ``chat_topic.py`` -> red here (and the pin fence for the table name)."""
    writers = _writers(_py_files())
    assert writers <= ALLOWED_PY, sorted(str(p.relative_to(ROOT)) for p in writers - ALLOWED_PY)
    assert STORE in writers
    store_sql = re.compile(r"(?i)\bset\s+topic_id\s*=")
    assert not store_sql.search(TOPICS.read_text(encoding="utf-8")), \
        "chat_topic.py calls chat_session_member.repoint_topic, and writes no row"


def test_the_writer_fence_can_fail(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text('SQL = "UPDATE x SET topic_id = :t, set_by = \'member\'"\n',
                       encoding="utf-8")
    assert _writers([planted]) == {planted}
    quiet = tmp_path / "quiet.py"
    quiet.write_text("tid = topics.topic_id(raw)\nset_by = SET_BY[word]\n", encoding="utf-8")
    assert _writers([quiet]) == set()


def test_no_file_of_the_control_plane_names_set_by_of_a_chat() -> None:
    """The client never sends who set a topic. The server writes it."""
    src = ROOT / "workbench" / "control_plane" / "src"
    named = [p for p in (src / "lib" / "shell").rglob("*") if p.suffix in {".ts", ".tsx"}
             and "set_by" in p.read_text(encoding="utf-8", errors="replace")]
    named += [p for p in (src / "lib").glob("sessions*.ts")
              if "set_by" in p.read_text(encoding="utf-8", errors="replace")]
    assert named == []


# ── chat-topic-move-pass ────────────────────────────────────────────────────


def test_the_pass_never_writes_over_a_move_of_the_member() -> None:
    """D163. Mutation: drop the WHERE of the DO UPDATE in ``_SET_AUTO_SQL``
    -> red here, and the pass moves the chat back in the R8 suite."""
    from gateway import chat_session_member as store

    auto = " ".join(store._SET_AUTO_SQL.split())
    assert "'auto'" in auto and "'member'" not in auto
    assert ("DO UPDATE SET topic_id = EXCLUDED.topic_id, set_by = 'auto' "
            "WHERE chat_session_member.set_by IS NULL "
            "OR chat_session_member.set_by = 'auto'") in auto
    move = " ".join(store._SET_TOPIC_SQL.split())
    assert move.endswith(
        "DO UPDATE SET topic_id = EXCLUDED.topic_id, set_by = 'member' RETURNING set_by")


def test_each_write_selects_a_chat_that_the_member_can_read() -> None:
    """A foreign key check skips RLS, so a write never names a chat by its id
    alone. Mutation: drop ``SESSION_VISIBLE_SQL`` from ``_SET_TOPIC_SQL``."""
    from gateway import chat_session_member as store
    from gateway.rooms import SESSION_VISIBLE_SQL

    for sql in (store._SET_TOPIC_SQL, store._SET_AUTO_SQL):
        assert "FROM chat_session s" in sql
        assert SESSION_VISIBLE_SQL in sql
        # The pin rule of CO-11: an archived chat takes no topic.
        assert "s.archived_at IS NULL" in sql
        assert "VALUES" not in sql
        # The DEFAULT stamps the bound org: the column list never names it.
        assert "INSERT INTO chat_session_member (session_id, member_email, topic_id, set_by)" \
            in sql


def test_each_read_filters_on_the_member() -> None:
    from gateway import chat_session_member as store

    assert "member_email = :member" in store._TOPICS_SQL
    assert "session_id = ANY(CAST(:ids AS text[]))" in store._TOPICS_SQL


def test_the_repoint_keeps_who_set_it_and_names_the_bound_org() -> None:
    """A merge keeps ``set_by``, so a member's move stays a member's move.
    Mutation: add ``set_by = 'auto'`` to ``_REPOINT_SQL`` -> red."""
    from gateway import chat_session_member as store

    assert "set_by" not in store._REPOINT_SQL
    assert "WHERE topic_id = CAST(:src AS uuid)" in store._REPOINT_SQL
    assert "current_setting('app.tenant_id', true)" in store._REPOINT_SQL


# ── chat-topic-move-lock-order ──────────────────────────────────────────────


def test_a_move_locks_its_topic_first_under_the_member_rule() -> None:
    """A merge locks topics before rows, so a move must too, or the two
    deadlock. Mutation: run the upsert before ``_usable`` -> red here, and
    the crossing move of the R8 suite fails."""
    from gateway import chat_session_member as store
    from gateway import chat_topic as topics

    sql = " ".join(topics._FOR_CHAT_SQL.split())
    assert sql.endswith("FOR KEY SHARE")
    assert topics._VISIBLE in sql
    assert "t.hidden_at IS NULL" in sql
    for fn, write in ((store.set_topic, "_SET_TOPIC_SQL"), (store.set_auto_topic, "_SET_AUTO_SQL")):
        src = inspect.getsource(fn)
        assert src.index("_usable(") < src.index(write), fn.__name__
    assert "topic_for_chat(" in inspect.getsource(store._usable)


def test_the_merge_moves_the_rows_between_its_locks_and_its_delete() -> None:
    """Mutation: call ``repoint_topic`` after ``_MERGE_SQL`` -> red here, and
    the SET NULL of the delete leaves the chats with no topic in R8."""
    from gateway import chat_topic as topics

    src = inspect.getsource(topics.merge_company_topics)
    assert src.index("_LOCK_SQL") < src.index("repoint_topic(") < src.index("_MERGE_SQL")


def test_a_bad_topic_id_runs_no_sql() -> None:
    from gateway.chat_session_member import set_auto_topic, set_topic

    class _NoSql:
        def execute(self, *a: Any, **k: Any):
            raise AssertionError("SQL ran for a bad id")

    for bad in ("", "abc", "1; DROP TABLE x"):
        assert set_topic(_NoSql(), "s-1", viewer="ana@co2b.test", topic_id=bad).state == "no_topic"
        assert set_auto_topic(_NoSql(), "s-1", member="ana@co2b.test", topic_id=bad).state \
            == "no_topic"


def test_the_list_keys_name_no_owner_and_no_scope() -> None:
    from gateway.chat_session_member import topic_fields
    from gateway.chat_topic import Topic

    t = Topic(id="t-1", name="Sales", scope="member", hidden_at=None)
    assert topic_fields(t) == {"topicId": "t-1", "topicName": "Sales"}
    assert topic_fields(None) == {"topicId": None, "topicName": None}
    # An archived chat reads as no topic, as it reads as not pinned.
    assert topic_fields(t, archived=True) == {"topicId": None, "topicName": None}


def test_the_list_reads_only_a_topic_that_shows(monkeypatch) -> None:
    """A chat whose topic the list of topics leaves out (a hidden company
    topic) reads as no topic. Mutation: return ``topics_of`` as it is."""
    from gateway import chat_session_member as store
    from gateway import chat_topic as topics

    shown = topics.Topic(id="t-shown", name="Sales", scope="company", hidden_at=None)
    monkeypatch.setattr(store, "topics_of", lambda s, ids, m: {"a": "t-shown", "b": "t-hidden"})
    monkeypatch.setattr(topics, "list_topics", lambda s, m, **k: [shown])
    assert store.topics_of_chats(object(), ["a", "b"], "ana@co2b.test") == {"a": shown}


def test_topics_of_an_empty_list_runs_no_sql() -> None:
    from gateway.chat_session_member import topics_of

    class _NoSql:
        def execute(self, *a: Any, **k: Any):
            raise AssertionError("SQL ran for no chat")

    assert topics_of(_NoSql(), [], "ana@co2b.test") == {}


# ── chat-topic-move-dark ────────────────────────────────────────────────────


def _topic_orgs(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_topics_orgs", raw, raising=False)


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _client(user):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import move_session_topic

    app = FastAPI()
    app.put("/chat/sessions/{session_id}/topic")(move_session_topic)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class _NoDatabase:
    """A ``tenant_session`` that fails the test on use."""

    def __call__(self, *a: Any, **k: Any):
        raise AssertionError("the route opened a session")


_TID = "11111111-2222-3333-4444-555555555555"
_BAD_BODIES = ("{not json", "", "[1, 2]", '"a string"', "null")


def _raw(client, body: str):
    return client.put("/chat/sessions/s-1/topic", content=body.encode("utf-8"),
                      headers={"content-type": "application/json"})


def test_the_route_answers_404_with_the_switch_off_before_any_sql(monkeypatch) -> None:
    """Mutation: drop the ``topics_allowed`` check of the route -> red."""
    import acb_graph

    _topic_orgs(monkeypatch, "org-b")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2b.test", "org-a"))
    for body in ({"topic_id": _TID}, {"topic_id": None}, {"set_by": "auto"}):
        res = client.put("/chat/sessions/s-1/topic", json=body)
        assert (res.status_code, res.json()) == (404, {"detail": "Not Found"}), res.text
    for raw in _BAD_BODIES:
        res = _raw(client, raw)
        assert (res.status_code, res.json()) == (404, {"detail": "Not Found"}), (raw, res.text)


def test_the_route_declares_no_body_parameter() -> None:
    """FastAPI parses a declared body before the handler, so a dark route
    would answer 422. Mutation: declare ``payload: TopicMoveIn`` -> red."""
    from gateway.routes import chat

    [route] = [r for r in chat.router.routes
               if getattr(r, "path", "") == "/chat/sessions/{session_id}/topic"]
    assert route.dependant.body_params == []


def test_with_the_switch_on_the_route_reaches_the_store(monkeypatch) -> None:
    from gateway import chat_session_member as store
    from gateway import chat_topic as topics
    from gateway.routes import chat

    seen: list[tuple[str, str, str | None, str | None]] = []
    sales = topics.Topic(id=_TID, name="Sales", scope="company", hidden_at=None)

    def _move(session_id, user_id, topic_id, *, organization_id):
        seen.append((session_id, user_id, topic_id, organization_id))
        return store.TopicMove("set", sales if topic_id else None)

    _topic_orgs(monkeypatch, "org-a")
    monkeypatch.setattr(chat, "_move_session", _move)
    client = _client(_user("ana@co2b.test", "org-a"))
    res = client.put("/chat/sessions/s-1/topic", json={"topic_id": _TID.upper()})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "topicId": _TID, "topicName": "Sales"}
    res = client.put("/chat/sessions/s-1/topic", json={"topic_id": None})
    assert res.json() == {"ok": True, "topicId": None, "topicName": None}
    # The id takes its canonical shape before it reaches the store.
    assert seen == [("s-1", "ana@co2b.test", _TID, "org-a"),
                    ("s-1", "ana@co2b.test", None, "org-a")]


def test_the_real_gateway_app_serves_the_route() -> None:
    from gateway.main import app

    from tests.unit._routes import served_routes

    served = {(m, r.path) for r in served_routes(app.routes) for m in (r.methods or ())}
    assert ("PUT", "/chat/sessions/{session_id}/topic") in served


@pytest.mark.parametrize(("state", "code", "detail"), [
    ("no_topic", 404, "Topic not found"), ("missing", 404, "Session not found"),
    ("kept", 404, "Session not found"),
    ("archived", 409, "This chat is archived. Restore it, then move it."),
])
def test_each_outcome_has_its_answer(monkeypatch, state: str, code: int, detail: str) -> None:
    from gateway import chat_session_member as store
    from gateway.routes import chat

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(chat, "_move_session", lambda *a, **k: store.TopicMove(state))  # type: ignore[arg-type]
    res = _client(_user("ana@co2b.test", "org-a")).put(
        "/chat/sessions/s-1/topic", json={"topic_id": _TID})
    assert (res.status_code, res.json()) == (code, {"detail": detail}), res.text


@pytest.mark.parametrize("bad", ["abc", "1; DROP TABLE x", "00000000", ""])
def test_an_id_that_is_not_a_uuid_is_404_before_any_sql(monkeypatch, bad: str) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    res = _client(_user("ana@co2b.test", "org-a")).put(
        "/chat/sessions/s-1/topic", json={"topic_id": bad})
    assert (res.status_code, res.json()) == (404, {"detail": "Topic not found"}), res.text


def test_a_caller_with_no_address_gets_403_before_any_sql(monkeypatch) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    res = _client(_user("  ", "org-a")).put("/chat/sessions/s-1/topic", json={"topic_id": None})
    assert res.status_code == 403


def test_a_bad_body_with_the_switch_on_is_422_before_any_sql(monkeypatch) -> None:
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co2b.test", "org-a"))
    for raw in _BAD_BODIES:
        assert _raw(client, raw).status_code == 422, raw
    # The key is required: an empty object is no move.
    assert client.put("/chat/sessions/s-1/topic", json={}).status_code == 422


# ── chat-topic-move-session-identity ────────────────────────────────────────


@pytest.mark.parametrize("extra", [
    {"set_by": "auto"}, {"member_email": "bob@co2b.test"},
    {"organization_id": str(uuid.UUID(int=9))}, {"owner_email": "bob@co2b.test"},
])
def test_a_body_that_names_more_than_the_topic_is_422(monkeypatch, extra) -> None:
    """Mutation: drop ``extra="forbid"`` from ``TopicMoveIn`` -> red."""
    import acb_graph

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    res = _client(_user("ana@co2b.test", "org-a")).put(
        "/chat/sessions/s-1/topic", json={"topic_id": _TID, **extra})
    assert res.status_code == 422, res.text


def test_the_member_never_comes_from_the_request(monkeypatch) -> None:
    from gateway import chat_session_member as store
    from gateway.routes import chat

    seen: list[str] = []

    def _move(session_id, user_id, topic_id, *, organization_id):
        seen.append(f"{user_id}|{organization_id}")
        return store.TopicMove("set", None)

    _topic_orgs(monkeypatch, "*")
    monkeypatch.setattr(chat, "_move_session", _move)
    res = _client(_user("ana@co2b.test", "org-a")).put(
        "/chat/sessions/s-1/topic?member_email=bob@co2b.test&organization_id=org-b",
        json={"topic_id": None},
        headers={"X-User-Email": "bob@co2b.test", "X-Organization-Id": "org-b"})
    assert res.status_code == 200, res.text
    assert seen == ["ana@co2b.test|org-a"]


# ── The list ────────────────────────────────────────────────────────────────


def _select_columns(sql: str) -> list[str]:
    """The output names of the outer SELECT of *sql*: the alias after ``AS``,
    else the last part of the column name. A comma inside brackets or quotes
    belongs to a subquery or a literal, and splits nothing."""
    head = sql[sql.index("SELECT") + len("SELECT"):sql.index("FROM chat_session s ")]
    parts: list[str] = []
    depth, quoted, cur = 0, False, ""
    for ch in head:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    names = []
    for part in parts:
        alias = re.search(r"\bAS\s+(\w+)\s*$", part.strip())
        names.append(alias.group(1) if alias else part.strip().split(".")[-1])
    return names


def _list_defaults() -> dict[str, Any]:
    from datetime import UTC, datetime

    at = datetime(2026, 11, 17, tzinfo=UTC)
    return {
        "id": "s-1", "agent_name": "orchestrator", "title": "Q4", "last_preview": "",
        "message_count": 2, "created_at": at, "updated_at": at, "user_id": "ana@co2b.test",
        "visibility": "private", "participant_count": 0, "has_user_message": True,
        "is_brief": False, "my_role": "owner", "archived_at": None,
        "continued_from": None,
    }


def _list_row(**over: Any) -> Any:
    """One row of the chat list, with a value for EACH column of the real
    ``_SESSIONS_SQL``. A new column of the list fails here, in one place,
    with its name, and never as an AttributeError deep in a test."""
    from types import SimpleNamespace

    from gateway.routes.chat import _SESSIONS_SQL

    defaults = _list_defaults()
    missing = [c for c in _select_columns(_SESSIONS_SQL) if c not in defaults]
    assert not missing, f"_SESSIONS_SQL has new columns, give each a default here: {missing}"
    return SimpleNamespace(**{**defaults, **over})


def test_the_fake_list_row_mirrors_the_real_select() -> None:
    """The fake of ``_ListDb`` holds the columns of ``_SESSIONS_SQL``, no
    more and no less. Mutation: add a column to ``_SESSIONS_SQL`` -> red here
    with its name."""
    from gateway.routes.chat import _SESSIONS_SQL

    columns = _select_columns(_SESSIONS_SQL)
    assert "is_brief" in columns and "archived_at" in columns
    assert sorted(columns) == sorted(_list_defaults())


def test_the_column_reader_can_fail() -> None:
    sql = ("SELECT s.id, (SELECT count(*) FROM x WHERE a IN (1, 2)) AS n, "
           "COALESCE(s.v, 'a,b') AS v, s.new_column FROM chat_session s WHERE true")
    assert _select_columns(sql) == ["id", "n", "v", "new_column"]


class _ListDb:
    """A sync ``tenant_session`` that answers the list with one chat and
    records each statement."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def __call__(self, org: str | None):
        db = self

        class _S:
            def __enter__(self):
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def execute(self, stmt: Any, params: Any = None):
                db.sql.append(str(stmt))

                class _R:
                    def fetchall(self) -> list:
                        return [_list_row()] if len(db.sql) == 1 else []

                return _R()

        return _S()


@pytest.mark.parametrize(("raw", "keys"), [("", False), ("*", True)])
def test_the_list_sends_the_topic_keys_only_with_the_switch_on(monkeypatch, raw: str,
                                                                 keys: bool) -> None:
    """Off, the list runs its one statement and sends no topic key.
    Mutation: read the topics with the switch off -> red."""
    import acb_graph
    from acb_common import get_settings
    from gateway import chat_session_member as store
    from gateway import chat_topic as topics
    from gateway.routes.chat import _get_sessions

    _topic_orgs(monkeypatch, raw)
    monkeypatch.setattr(get_settings(), "chat_pin_orgs", "", raising=False)
    sales = topics.Topic(id=_TID, name="Sales", scope="company", hidden_at=None)
    asked: list[tuple[list[str], str]] = []

    def _of(s, ids, member):
        asked.append((list(ids), member))
        return {"s-1": sales}

    monkeypatch.setattr(store, "topics_of_chats", _of)
    db = _ListDb()
    monkeypatch.setattr(acb_graph, "tenant_session", db)
    [row] = _get_sessions("ana@co2b.test", organization_id="org-a")
    assert len(db.sql) == 1
    if keys:
        assert (row["topicId"], row["topicName"]) == (_TID, "Sales")
        assert asked == [(["s-1"], "ana@co2b.test")]
    else:
        assert "topicId" not in row and "topicName" not in row
        assert asked == []


# ── chat-topic-move-expand-only ─────────────────────────────────────────────


def test_the_migration_adds_two_columns_and_changes_nothing_else() -> None:
    """Mutation: write a plain ``ON DELETE SET NULL`` -> red here, and the
    delete of a topic fails on ``organization_id`` in the R8 suite."""
    sql = _migration().read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert "SET LOCAL lock_timeout" in code
    assert "ADD COLUMN IF NOT EXISTS topic_id UUID," in code
    assert re.search(r"ADD COLUMN IF NOT EXISTS set_by\s+TEXT;", code)
    assert "CHECK (set_by IN ('auto', 'member'))" in code
    assert "CHECK (topic_id IS NULL OR set_by IS NOT NULL)" in code
    assert re.search(r"FOREIGN KEY \(organization_id, topic_id\)\s+"
                     r"REFERENCES chat_topic \(organization_id, id\)\s+"
                     r"ON DELETE SET NULL \(topic_id\);", code)
    assert "ON chat_session_member (organization_id, member_email, topic_id)" in code
    assert code.count("IF NOT EXISTS (") == 3, "each constraint is guarded"
    for word in ("CREATE TABLE", "DROP ", "RENAME", "DELETE FROM", "UPDATE ", "INSERT INTO",
                 "SET NOT NULL", "ALTER COLUMN", "DEFAULT"):
        assert word not in code, word
    # Both new columns are nullable (R6): no NOT NULL in an ADD COLUMN.
    assert not re.search(r"ADD COLUMN[^,;]*NOT NULL", code)


def test_the_older_files_keep_their_marks() -> None:
    """The suites of 450 and 451 find their files by content. This file must
    not carry either mark, or each finds two files."""
    sql = _migration().read_text(encoding="utf-8")
    for mark in ("CREATE TABLE IF NOT EXISTS chat_session_member (",
                 "CREATE TABLE IF NOT EXISTS chat_topic ("):
        assert mark not in sql


def test_the_suite_finds_the_migration_by_its_content() -> None:
    assert re.fullmatch(r"\d+_chat_session_member_topic\.sql", _migration().name)
