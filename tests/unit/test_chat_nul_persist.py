"""A NUL in a tool result must never fail a save (incident 2026-10-09).

A member ran the task-manager agent in a Tasks chat. The Copilot CLI's built-in
``view`` tool read an Excel file as text, and its result held the raw bytes of
the ZIP container: ``PK\\x03\\x04\\x14\\x00``. Postgres refuses U+0000 in a
``text`` value and the escape ``\\u0000`` in a ``jsonb`` value. So
``POST /chat/sessions/{id}/messages`` answered 500 for 40 seconds, and the
reply of the run was never saved::

    psycopg.errors.UntranslatableCharacter: unsupported Unicode escape sequence
    DETAIL:  \\u0000 cannot be converted to text.

Two fixes, and this file is the fence of both (R7).

1. **The persistence seam.** ``acb_common.pg_text.storable`` replaces each NUL
   and each lone surrogate with U+FFFD. ``_upsert_messages`` (the route and
   the fold), the session upsert and patch, ``run_trace._persist_row`` and the
   native session body call it before the bind.
2. **The source.** ``permission_policy._decide_read`` refuses a read of a
   binary file in the workspace, and the refusal names ``read_attachment``.

The hermetic half runs everywhere. Its fake session refuses what Postgres
refuses, so it is red on a writer that skips the seam. The R8 half runs the
REAL route, the REAL fold and the REAL run trace on the H3 phase-4 catalog as
its NOBYPASSRLS role, because a fake agrees with whatever SQL it gets (R8)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<db> \\
        uv run pytest tests/unit/test_chat_nul_persist.py -v -rs
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from typing import Any

import pytest

#: The head of an XLSX file, as the incident's tool result held it.
_ZIP_HEAD = "PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00"
_FFFD = "�"


def _no_nul(value: Any) -> bool:
    """True when no string anywhere in *value* holds a NUL."""
    return "\x00" not in json.dumps(value, ensure_ascii=False).replace("\\u0000", "\x00")


# ═══════════════════════════════════════════════════════════════════════════
# The helper
# ═══════════════════════════════════════════════════════════════════════════


def test_storable_replaces_a_nul_at_any_depth_and_in_a_key() -> None:
    from acb_common.pg_text import storable

    got = storable({
        "a\x00": [{"result": _ZIP_HEAD}, ("x\x00y", 3)],
        "n": None, "i": 7, "f": 1.5, "b": True,
    })
    assert got == {
        "a" + _FFFD: [{"result": _ZIP_HEAD.replace("\x00", _FFFD)},
                      ("x" + _FFFD + "y", 3)],
        "n": None, "i": 7, "f": 1.5, "b": True,
    }
    assert _no_nul(got)


def test_storable_replaces_a_lone_surrogate_and_keeps_a_real_astral_char() -> None:
    from acb_common.pg_text import storable

    assert storable("a\ud800b\udfffc") == "a" + _FFFD + "b" + _FFFD + "c"
    assert storable("smile \U0001f600") == "smile \U0001f600"


def test_storable_keeps_the_length_and_returns_a_clean_string_as_is() -> None:
    from acb_common.pg_text import storable

    clean = "no nul here"
    assert storable(clean) is clean
    assert len(storable(_ZIP_HEAD)) == len(_ZIP_HEAD)


# ═══════════════════════════════════════════════════════════════════════════
# The writers, hermetic. The fake session refuses what Postgres refuses.
# ═══════════════════════════════════════════════════════════════════════════


class PostgresRefusal(Exception):
    """What psycopg raises for a value Postgres cannot store."""


_JSONB_PARAM = re.compile(r"(?:CAST\(\s*)?:(\w+)\s*(?:AS\s+jsonb\s*\)|::\s*jsonb)", re.I)


class _Result:
    def __init__(self, row: tuple | None, rowcount: int = 1) -> None:
        self._row = row
        self.rowcount = rowcount

    def first(self) -> tuple | None:
        return self._row

    def scalar(self) -> Any:
        return self._row[0] if self._row else None


class _StrictSession:
    """Records each write. Refuses a NUL in any text bind, a ``\\u0000``
    escape in a jsonb bind, and a string that UTF-8 cannot encode."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(clause)
        params = dict(params or {})
        if sql.strip().startswith("SELECT 1 FROM chat_session"):
            return _Result((1,))
        jsonb = set(_JSONB_PARAM.findall(sql))
        for name, value in params.items():
            if not isinstance(value, str):
                continue
            if "\x00" in value:
                raise PostgresRefusal(f"{name}: invalid byte sequence 0x00")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PostgresRefusal(f"{name}: surrogates not allowed") from exc
            if name in jsonb and "\\u0000" in value:
                raise PostgresRefusal(
                    f"{name}: unsupported Unicode escape sequence \\u0000")
        self.writes.append((sql, params))
        return _Result((params.get("id", "row"),))

    def commit(self) -> None:
        return None


@pytest.fixture
def strict_db(monkeypatch) -> _StrictSession:
    import acb_graph

    session = _StrictSession()

    @contextlib.contextmanager
    def _tenant_session(_org: str | None):
        yield session

    monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
    return session


def _poisoned_record(mid: str = "m-zip") -> Any:
    from gateway.routes.chat import MessageRecord

    return MessageRecord(
        id=mid, role="assistant",
        content="I read the file.\x00",
        timestamp=1_790_000_000_000,
        tool_events=[{
            "id": "tc1", "name": "view",
            "args": {"path": "inputs/t/book.xlsx", "view_range": [1, 50]},
            "result": "1. " + _ZIP_HEAD,
            "status": "done",
        }],
        progress_lines=["view\x00"],
        reasoning="thinking\x00",
        agent_state={"todos": [{"title": "t\x00"}]},
        custom_events=[{"name": "card", "value": {"text": "\x00"}}],
        author_kind="agent",
    )


@pytest.mark.parametrize("from_run", [False, True], ids=["route", "fold"])
def test_a_tool_result_with_a_nul_saves_and_reads_back_without_it(
    strict_db: _StrictSession, from_run: bool,
) -> None:
    """The incident row, written by the route's helper and by the fold."""
    from gateway.routes.chat import _upsert_messages

    declined = _upsert_messages(
        "s-1", [_poisoned_record()], actor_email="sahil@dewin.test",
        agent_name="task-manager", author_from_run=from_run,
        organization_id="org-a",
    )
    assert declined == []
    (_sql, params), = strict_db.writes
    tools = json.loads(params["tool_events"])
    assert tools[0]["result"] == "1. " + _ZIP_HEAD.replace("\x00", _FFFD)
    assert params["content"] == "I read the file." + _FFFD
    assert params["reasoning"] == "thinking" + _FFFD
    for name in ("tool_events", "progress_lines", "agent_state", "custom_events"):
        assert "\\u0000" not in params[name], name
        assert _no_nul(json.loads(params[name])), name


def test_a_session_title_or_preview_with_a_nul_saves(strict_db: _StrictSession) -> None:
    """The browser sends the last reply as the preview, so it holds the NUL too."""
    from gateway.routes.chat import (
        SessionPatchRequest,
        SessionUpsertRequest,
        _patch_session,
        _upsert_session,
    )

    _upsert_session(
        "a@x.test", SessionUpsertRequest(id="s-1", title="t\x00", last_preview="p\x00"),
        organization_id="org-a",
    )
    _patch_session(
        "s-1", "a@x.test", SessionPatchRequest(title="t\x00", last_preview="p\x00"),
        organization_id="org-a",
    )
    upsert, patch = (params for _sql, params in strict_db.writes)
    for params in (upsert, patch):
        assert (params["title"], params["last_preview"]) == ("t" + _FFFD, "p" + _FFFD)


def test_an_errored_run_trace_with_a_nul_saves(strict_db: _StrictSession) -> None:
    """An errored run keeps the folded message in ``agent_run.trace``."""
    from gateway.run_trace import _persist_row, build_run_trace_row

    row = build_run_trace_row(
        run_id="r-1", thread_id="s-1", agent_name="task-manager",
        user_id="a@x.test", model=None,
        events=[{"type": "RUN_STARTED"},
                {"type": "RUN_ERROR", "message": "bad bytes \x00"}],
        folded={"content": "x\x00", "tool_events": [{"name": "view",
                                                     "result": _ZIP_HEAD}],
                "reasoning": None, "custom_events": []},
    )
    _persist_row(row, "org-a")
    (_sql, params), = strict_db.writes
    assert params["error_message"] == "bad bytes " + _FFFD
    trace = json.loads(params["trace"])
    assert trace["tool_events"][0]["result"] == _ZIP_HEAD.replace("\x00", _FFFD)


def test_the_native_session_body_holds_no_nul_escape() -> None:
    """``maf_agent_session.session_json`` is jsonb and holds every tool result
    of the turn (WS-43t2, behind ``MAF_NATIVE_SESSIONS``)."""
    agent_framework = pytest.importorskip("agent_framework")
    store = pytest.importorskip("orchestrator.native_session_store")
    from agent_framework import Content

    session = agent_framework.AgentSession()
    session.state[store.HISTORY_SOURCE_ID] = {"messages": [
        agent_framework.Message(role="user", contents=["read book.xlsx"]),
        agent_framework.Message(role="assistant", contents=[
            Content.from_function_call(call_id="c1", name="view", arguments="{}"),
        ]),
        agent_framework.Message(role="tool", contents=[
            Content.from_function_result(call_id="c1", result=_ZIP_HEAD),
        ]),
        agent_framework.Message(role="assistant", contents=["done\x00"]),
    ]}
    turn = store.SessionTurn(
        organization_id="org-a", thread_id="s-1", agent_name="probe",
        outcome=store.NO_ROW, session=session,
        history=store.SessionHistoryProvider(), fingerprint="f", save_digest="d",
    )
    body, count = asyncio.run(store._session_body(turn, 100_000))
    assert count == 4
    assert "\\u0000" not in body and "\x00" not in body
    assert _FFFD in body


# ═══════════════════════════════════════════════════════════════════════════
# The source: a binary file is not read as text
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A run whose workspace root is *tmp_path*, as the executor binds it."""
    from acb_skills import permission_policy

    monkeypatch.setattr(permission_policy, "_workspace_root", lambda: str(tmp_path))
    return tmp_path


def _xlsx_bytes() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>" * 200)
        zf.writestr("xl/workbook.xml", "<workbook/>" * 200)
    return buf.getvalue()


@pytest.mark.parametrize(("name", "data", "kind"), [
    ("book.xlsx", _xlsx_bytes(), "xlsx"),
    ("brief.docx", b"PK\x03\x04" + b"\x14\x00" * 40, "docx"),
    ("scan.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n", "pdf"),
    ("photo", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "png"),
    ("blob.dat", b"abc\x00def", "dat"),
], ids=["xlsx", "docx", "pdf", "png-no-suffix", "nul-in-head"])
def test_a_read_of_a_binary_file_is_refused_with_a_pointer(
    workspace, name: str, data: bytes, kind: str,
) -> None:
    from acb_skills.permission_policy import decide

    path = workspace / "inputs" / name
    path.parent.mkdir()
    path.write_bytes(data)
    approved, code, detail = decide({"kind": "read", "path": str(path)})
    assert (approved, code) == (False, "read_binary_file")
    assert f"({kind}," in detail
    assert "\x00" not in detail
    if kind in ("xlsx", "docx", "pdf"):
        assert "read_attachment" in detail
    else:
        assert "read_attachment" not in detail


@pytest.mark.parametrize("data", [
    b"plain text\nline two\n",
    "café résumé\n".encode(),
    b"",
], ids=["ascii", "utf8", "empty"])
def test_a_read_of_a_text_file_is_approved(workspace, data: bytes) -> None:
    from acb_skills.permission_policy import decide

    path = workspace / "notes.md"
    path.write_bytes(data)
    assert decide({"kind": "read", "path": str(path)})[:2] == (True, "read_in_workspace")


def test_a_read_of_a_missing_file_or_a_folder_is_approved(workspace) -> None:
    from acb_skills.permission_policy import decide

    assert decide({"kind": "read", "path": str(workspace / "nope.xlsx")})[0] is True
    assert decide({"kind": "read", "path": str(workspace)})[0] is True


def test_a_binary_file_outside_the_workspace_is_never_opened(workspace, tmp_path_factory) -> None:
    """Containment runs first. The sniff never opens a path outside the root."""
    from acb_skills.permission_policy import decide

    outside = tmp_path_factory.mktemp("outside") / "secret.xlsx"
    outside.write_bytes(_xlsx_bytes())
    assert decide({"kind": "read", "path": str(outside)})[:2] == (
        False, "read_out_of_workspace")


@pytest.mark.parametrize("mode", ["enforce", "audit"])
def test_the_handler_answers_a_binary_read_with_the_pointer_in_every_mode(
    workspace, monkeypatch, mode: str,
) -> None:
    """Audit mode approves a permission refusal. A binary read is not one: an
    approval would only hand the model the bytes again."""
    pytest.importorskip("copilot")
    from acb_skills.permission_policy import risk_aware_permission_handler
    from copilot.generated.rpc import PermissionDecisionReject

    monkeypatch.setenv("AGENT_PERMISSION_MODE", mode)
    path = workspace / "book.xlsx"
    path.write_bytes(_xlsx_bytes())
    result = risk_aware_permission_handler({"kind": "read", "path": str(path)}, {})
    assert isinstance(result, PermissionDecisionReject)
    assert "binary file (xlsx," in result.feedback
    assert "read_attachment" in result.feedback
    assert "ask the user" not in result.feedback


# ═══════════════════════════════════════════════════════════════════════════
# R8: the real route, the real fold, the real run trace, on real Postgres
# ═══════════════════════════════════════════════════════════════════════════

pytest.importorskip("sqlalchemy")

import acb_graph.db as graph_db  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

# Resolved by name as fixtures (F401, F811). The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _APP_ROLE,
    _DB_GATE,
    app_engine,
    promoted,
)

_ALICE = "alice@nul-a.test"


@pytest.fixture(scope="module")
def members(promoted):  # noqa: F811
    with promoted.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO app_user (email, display_name, role, status, "
            "organization_id) VALUES (:e, :e, 'employee', 'active', :o) "
            "ON CONFLICT DO NOTHING"), {"e": _ALICE, "o": promoted.org_a})
        c.execute(text(
            f"GRANT EXECUTE ON FUNCTION public.chat_session_exists(text) TO {_APP_ROLE}"))
    return promoted


@pytest.fixture
def graph_as_app(members, app_engine, monkeypatch):  # noqa: F811
    """``acb_graph`` sessions open as the NOBYPASSRLS role, one backend each."""
    eng = create_engine(members.app_url, poolclass=NullPool, future=True)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield members
    finally:
        eng.dispose()


def _sid() -> str:
    return f"nul-{uuid.uuid4().hex[:12]}"


def _admin_one(promoted, sql: str, **params):  # noqa: F811
    with promoted.admin_engine.connect() as c:
        return c.execute(text(sql), params).first()


def _client(org: str):
    from acb_auth import UserContext, get_current_user
    from acb_auth.roles import UserRole
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.chat import get_messages, save_messages, upsert_session

    app = FastAPI()
    app.post("/chat/sessions")(upsert_session)
    app.post("/chat/sessions/{session_id}/messages")(save_messages)
    app.get("/chat/sessions/{session_id}/messages")(get_messages)
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        email=_ALICE, role=UserRole.EMPLOYEE, organization_id=org)
    return TestClient(app, raise_server_exceptions=False)


@_DB_GATE
def test_r8_postgres_refuses_the_incident_value(graph_as_app):
    """Why the seam exists: the exact error of the incident, on real Postgres."""
    with pytest.raises(DBAPIError) as err, graph_as_app.admin_engine.connect() as c:
        c.execute(text("SELECT CAST(:v AS jsonb)"),
                  {"v": json.dumps([{"result": _ZIP_HEAD}])})
    assert "\\u0000 cannot be converted to text" in str(err.value.orig)


@_DB_GATE
def test_r8_the_route_saves_a_nul_tool_result_and_reads_it_back(graph_as_app):
    """The browser's save of the incident row: 200, and the GET has no NUL."""
    org = graph_as_app.org_a
    client = _client(org)
    sid = _sid()
    made = client.post("/chat/sessions", json={
        "id": sid, "agent_name": "task-manager", "title": "Book\x00",
        "last_preview": "1. PK\x03\x04\x00", "message_count": 0,
    })
    assert made.status_code == 200, made.text
    row = {
        "id": "u-1", "role": "user", "content": "Read book.xlsx\x00",
        "timestamp": 1_790_000_000_000,
        "tool_events": [{"id": "tc1", "name": "view",
                         "args": {"view_range": [1, 50]},
                         "result": "1. " + _ZIP_HEAD, "status": "done"}],
        "progress_lines": ["view\x00"], "reasoning": "r\x00",
        "agent_state": {"k\x00": "v\x00"},
        "custom_events": [{"name": "c", "value": "\x00"}],
    }
    saved = client.post(f"/chat/sessions/{sid}/messages", json=[row])
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] == 1

    got = client.get(f"/chat/sessions/{sid}/messages")
    assert got.status_code == 200, got.text
    (msg,) = got.json()
    assert _no_nul(msg)
    assert msg["toolEvents"][0]["result"] == "1. " + _ZIP_HEAD.replace("\x00", _FFFD)
    assert msg["content"] == "Read book.xlsx" + _FFFD
    title = _admin_one(graph_as_app,
                       "SELECT title, last_preview FROM chat_session WHERE id = :s",
                       s=sid)
    assert (title.title, title.last_preview) == ("Book" + _FFFD, "1. PK\x03\x04" + _FFFD)


@_DB_GATE
def test_r8_the_fold_saves_a_nul_tool_result_and_records_the_run(graph_as_app, monkeypatch):
    """The run's own fold, from a replay whose tool result holds the ZIP."""
    from gateway import chat_fold
    from orchestrator import stream_relay

    org = graph_as_app.org_a
    sid, mid = _sid(), f"assistant-{uuid.uuid4().hex[:8]}"
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    events = [
        {"type": "RUN_STARTED", "runId": run_id},
        {"type": "TOOL_CALL_START", "toolCallId": "tc1", "toolCallName": "view"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "tc1",
         "delta": '{"path": "book.xlsx", "view_range": [1, 50]}'},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "tc1", "content": "1. " + _ZIP_HEAD},
        {"type": "TEXT_MESSAGE_START", "messageId": mid},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": "The file is binary."},
        {"type": "TEXT_MESSAGE_END", "messageId": mid},
        {"type": "RUN_ERROR", "message": "stopped at \x00"},
    ]

    async def _replay(*_a, **_k):
        return events

    async def _solo(*_a, **_k):
        return None

    monkeypatch.setattr(stream_relay, "replay_events", _replay)
    monkeypatch.setattr(chat_fold, "_run_authority", _solo)

    out = asyncio.run(chat_fold.persist_final_assistant_message(
        sid, mid, user_id=_ALICE, agent_name="task-manager",
        run_id=run_id, organization_id=org,
    ))
    assert out is not None
    row = _admin_one(graph_as_app,
                     "SELECT tool_events, run_final_at FROM chat_message WHERE id = :m",
                     m=mid)
    assert row is not None, "the fold wrote no row"
    assert row.run_final_at is not None
    assert row.tool_events[0]["result"] == "1. " + _ZIP_HEAD.replace("\x00", _FFFD)
    trace = _admin_one(graph_as_app,
                       "SELECT status, error_message, trace FROM agent_run "
                       "WHERE run_id = :r", r=run_id)
    assert trace is not None, "the errored run wrote no agent_run row"
    assert trace.status == "error"
    assert trace.error_message == "stopped at " + _FFFD
    assert _no_nul(trace.trace)
