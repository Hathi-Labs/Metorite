"""The pin of a chat, with no database (WS-CO CO-11, task 3510).

Spec: ``project-docs/specs/chat_organization.md`` §10 and §18. Module:
``gateway/chat_session_member.py``. The R8 half is ``test_chat_pin_r8.py``.

Fences (R7):

* ``chat-pin-one-module``: only the store module and the member purge name
  the table ``chat_session_member`` in Python, and no file of the control
  plane names it. A new reader or writer fails here until its slice adds it.
* ``chat-pin-reads-the-chat``: the pin selects the chat from
  ``chat_session`` under the visibility rule, and never an archived chat. A
  foreign key check skips RLS, so this is the guard of the plain key.
* ``chat-pin-own-row``: each statement filters on the member.
* ``chat-pin-dark``: ``CHAT_PIN_ORGS`` is the switch. Off, the routes answer
  404 before any SQL, and the list sends today's one statement.
* ``chat-pin-expand-only``: the migration creates one table with FORCE RLS,
  sets ``lock_timeout``, and adds no column that CO-2 owns.

Mutations, each red here: name the table in ``routes/chat.py``; drop
``SESSION_VISIBLE_SQL`` or ``archived_at IS NULL`` from ``_PIN_SQL``; drop
the member from ``_UNPIN_SQL``; read the pins with the switch off; take the
member from a query parameter.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "apps" / "services" / "gateway" / "gateway"
STORE = GATEWAY / "chat_session_member.py"
PURGE = GATEWAY / "routes" / "admin" / "members.py"
TABLE = "chat_session_member"
_MIGRATION_MARK = "CREATE TABLE IF NOT EXISTS chat_session_member ("

#: The Python files that may name the table: the store, and the purge row.
ALLOWED_PY = {STORE, PURGE}
#: A SQL reference to the table, or the table as a quoted literal. The
#: word edge keeps out ``chat_session_member_summary`` (migration 319).
_NAMES_TABLE = re.compile(
    r"(?i)(?:\b(?:from|join|update|into|table)\s+(?:public\.)?chat_session_member\b(?!_))"
    r"|(?:[\"']chat_session_member[\"'])")
_ANY_NAME = re.compile(r"\bchat_session_member\b(?!_)")


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


# ── chat-pin-one-module ─────────────────────────────────────────────────────


def _python_namers(files: list[Path]) -> set[Path]:
    return {p for p in files if _NAMES_TABLE.search(p.read_text(encoding="utf-8",
                                                                errors="replace"))}


def test_only_the_store_and_the_purge_name_the_table_in_python() -> None:
    namers = _python_namers(_py_files())
    assert namers == ALLOWED_PY, sorted(str(p.relative_to(ROOT)) for p in namers ^ ALLOWED_PY)


def test_no_file_of_the_control_plane_names_the_table() -> None:
    src = ROOT / "workbench" / "control_plane" / "src"
    named = [p for p in src.rglob("*") if p.suffix in {".ts", ".tsx"}
             and "node_modules" not in p.parts
             and _ANY_NAME.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert named == []


def test_the_name_fence_can_fail(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text('SQL = "SELECT pinned_at FROM chat_session_member"\n', encoding="utf-8")
    assert _python_namers([planted]) == {planted}
    quiet = tmp_path / "quiet.py"
    quiet.write_text("# 319_chat_session_member_summary.sql\n", encoding="utf-8")
    assert _python_namers([quiet]) == set()


# ── chat-pin-reads-the-chat and chat-pin-own-row ────────────────────────────


def test_the_pin_selects_a_visible_chat_that_is_not_archived() -> None:
    from gateway import chat_session_member as store
    from gateway.rooms import SESSION_VISIBLE_SQL

    sql = store._PIN_SQL
    assert "FROM chat_session s" in sql
    assert SESSION_VISIBLE_SQL in sql
    assert "s.archived_at IS NULL" in sql
    assert "VALUES" not in sql, "the pin must SELECT the chat, never name it by id alone"
    assert SESSION_VISIBLE_SQL in store._VISIBLE_SQL


def test_each_statement_filters_on_the_member() -> None:
    from gateway import chat_session_member as store

    for sql in (store._UNPIN_SQL, store._PINS_SQL):
        assert "member_email = :member" in sql, sql
    assert "session_id = :id" in store._UNPIN_SQL
    assert "COALESCE(" in store._PIN_SQL, "a second pin keeps the first time"


def test_the_member_key_is_the_address_in_lower_case() -> None:
    from gateway.chat_session_member import member_key

    assert member_key("  Dana.Mixed@CO11.test ") == "dana.mixed@co11.test"
    assert member_key(None) == ""


def test_an_archived_chat_reads_as_not_pinned() -> None:
    from gateway.chat_session_member import pin_fields

    at = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
    assert pin_fields(at, archived=False) == {"pinned": True, "pinnedAt": at.isoformat()}
    assert pin_fields(at, archived=True) == {"pinned": False, "pinnedAt": None}
    assert pin_fields(None, archived=False) == {"pinned": False, "pinnedAt": None}


# ── chat-pin-dark ───────────────────────────────────────────────────────────


def _pin_orgs(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "chat_pin_orgs", raw, raising=False)


@pytest.mark.parametrize(("raw", "org", "on"), [
    ("*", "org-a", True),
    ("org-b, ORG-A", "org-a", True),
    ("org-b", "org-a", False),
    ("", "org-a", False),
    ("*", None, False),
    ("*", "  ", False),
])
def test_the_one_reader_of_the_switch(monkeypatch, raw: str, org: str | None, on: bool) -> None:
    from gateway.chat_session_member import pin_allowed

    _pin_orgs(monkeypatch, raw)
    assert pin_allowed(org) is on


def test_a_fault_of_the_settings_reads_as_off(monkeypatch) -> None:
    import acb_common
    from gateway.chat_session_member import pin_allowed

    def _boom():
        raise RuntimeError("no settings")

    monkeypatch.setattr(acb_common, "get_settings", _boom)
    assert pin_allowed("org-a") is False


def _user(email: str, org: str | None):
    from acb_auth import UserContext
    from acb_auth.roles import UserRole

    return UserContext(email=email, role=UserRole.EMPLOYEE, organization_id=org)


def _client(user):
    from acb_auth import get_current_user
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import pin_session, unpin_session

    app = FastAPI()
    app.put("/chat/sessions/{session_id}/pin")(pin_session)
    app.delete("/chat/sessions/{session_id}/pin")(unpin_session)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class _NoDatabase:
    """A ``tenant_session`` that fails the test on use."""

    def __call__(self, *a: Any, **k: Any):
        raise AssertionError("the route opened a session")


def test_the_routes_answer_404_with_the_switch_off_before_any_sql(monkeypatch) -> None:
    import acb_graph

    _pin_orgs(monkeypatch, "org-b")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("ana@co11.test", "org-a"))
    assert client.put("/chat/sessions/s-1/pin").status_code == 404
    assert client.delete("/chat/sessions/s-1/pin").status_code == 404


def test_a_caller_with_no_address_gets_403_before_any_sql(monkeypatch) -> None:
    import acb_graph

    _pin_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    client = _client(_user("", "org-a"))
    assert client.put("/chat/sessions/s-1/pin").status_code == 403
    assert client.delete("/chat/sessions/s-1/pin").status_code == 403


def test_the_member_never_comes_from_the_request(monkeypatch) -> None:
    """R11: the routes take the address of the session. A query parameter
    or a header that names another member changes nothing."""
    import acb_graph
    from gateway.routes import chat

    seen: list[str] = []

    def _pin(session_id: str, user_id: str, *, organization_id: str | None):
        seen.append(user_id)
        from gateway.chat_session_member import PinResult

        return PinResult("pinned", datetime(2026, 11, 16, tzinfo=UTC))

    _pin_orgs(monkeypatch, "*")
    monkeypatch.setattr(acb_graph, "tenant_session", _NoDatabase())
    monkeypatch.setattr(chat, "_pin_session", _pin)
    res = _client(_user("ana@co11.test", "org-a")).put(
        "/chat/sessions/s-1/pin?member_email=bob@co11.test",
        headers={"X-User-Email": "bob@co11.test"})
    assert res.status_code == 200, res.text
    assert seen == ["ana@co11.test"]


class _Recorder:
    """A sync ``tenant_session`` that records each statement and finds no row."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def __call__(self, org: str | None):
        recorder = self

        class _S:
            def __enter__(self):
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def execute(self, stmt: Any, params: Any = None):
                recorder.sql.append(str(stmt))

                class _R:
                    def fetchall(self) -> list:
                        return []

                return _R()

        return _S()


@pytest.mark.parametrize(("raw", "statements"), [("", 1), ("*", 2)])
def test_the_list_reads_the_pins_only_with_the_switch_on(monkeypatch, raw: str,
                                                         statements: int) -> None:
    import acb_graph
    from gateway.routes.chat import _get_sessions

    _pin_orgs(monkeypatch, raw)
    rec = _Recorder()
    monkeypatch.setattr(acb_graph, "tenant_session", rec)
    assert _get_sessions("ana@co11.test", organization_id="org-a") == []
    assert len(rec.sql) == statements
    assert not _ANY_NAME.search(rec.sql[0])
    if statements == 2:
        assert "FROM chat_session_member" in rec.sql[1]


# ── chat-pin-expand-only ────────────────────────────────────────────────────


def test_the_migration_creates_one_table_and_changes_nothing_else() -> None:
    sql = _migration().read_text(encoding="utf-8")
    code = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", code) == [TABLE]
    assert "SET LOCAL lock_timeout" in code
    assert "REFERENCES chat_session (id) ON DELETE CASCADE" in code
    assert "PRIMARY KEY (session_id, member_email)" in code
    assert re.search(r"organization_id\s+UUID NOT NULL\s+REFERENCES organization \(id\)", code)
    assert "FORCE  ROW LEVEL SECURITY" in code
    assert "WITH CHECK (organization_id = current_setting(''app.tenant_id'', true)::uuid)" in code
    for word in ("DROP TABLE", "RENAME", "ALTER TABLE chat_session ", "DELETE FROM",
                 "UPDATE "):
        assert word not in code, word
    # CO-2 (task 3501) adds the topic columns in its own file.
    for column in ("topic_id", "set_by"):
        assert column not in code, column
