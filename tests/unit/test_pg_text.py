"""No NUL and no lone surrogate reaches a chat or run-trace column (2026-10-09).

On 2026-10-09 a chat agent read a ``.docx`` as raw zip bytes. The bytes held
``\\x00``, and they reached a tool result. Postgres refuses ``\\u0000`` in
``jsonb`` (``UntranslatableCharacter``), so the ``chat_message`` upsert
answered 500 fourteen times, and ``run_trace.record_failed`` and
``chat_fold.persist_failed`` logged the same error. The turn was lost.

``acb_common.pg_text`` is the one cleaner. ``gateway.routes.chat.
_upsert_messages`` (the browser save and the chat fold) and
``gateway.run_trace._persist_row`` call it.

R8: the round trips run against a REAL database, as the NOBYPASSRLS app role
of the H3 rehearsal (``graph_as_app``). A fake would accept the NUL.

Mutations this file catches (R7):

* ``_upsert_messages`` dumps ``tool_events`` with ``json.dumps`` again: the
  fold write fails with ``UntranslatableCharacter``;
* the browser save stops cleaning ``content``: the route answers 500;
* ``_persist_row`` stops cleaning its row: no ``agent_run`` row;
* ``pg_safe`` forgets the surrogate range.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db> \\
        uv run pytest tests/unit/test_pg_text.py -v -rs
"""
from __future__ import annotations

import json
import uuid

import pytest
from acb_common.pg_text import pg_json, pg_safe

pytest.importorskip("sqlalchemy")

# Resolved by name as fixtures. The imports are load-bearing.
from tests.unit.test_chat_write_under_rls import (  # noqa: E402, F401
    _ALICE,
    _admin_one,
    _client,
    _new_session,
    _user,
    graph_as_app,
    members,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: E402, F401
    _DB_GATE,
    app_engine,
    promoted,
)

#: What the incident's tool result held: zip bytes read as text.
_ZIP_TEXT = "PK\x03\x04\x14\x00\x06\x00word/document.xml\x00\x00"
_LONE = "half a pair \ud800 here"


# ── The cleaner (no database) ──────────────────────────────────────────────

def test_pg_safe_removes_nul_and_lone_surrogates_and_keeps_the_rest() -> None:
    got = pg_safe({
        "a\x00": [_ZIP_TEXT, ("t\udfff", 3)], "emoji": "📎 ok", "n": None, "i": 7,
    })
    assert got == {
        "a": ["PK\x03\x04\x14\x06word/document.xml", ["t", 3]],
        "emoji": "📎 ok", "n": None, "i": 7,
    }
    assert pg_safe(_LONE) == "half a pair  here"
    json.dumps(got).encode("utf-8")


def test_pg_json_never_writes_the_escape_postgres_refuses() -> None:
    out = pg_json([{"result": _ZIP_TEXT + _LONE}])
    assert "\\u0000" not in out and "\\ud800" not in out


# ── R8: the writes, on a real database ─────────────────────────────────────

def _sid() -> str:
    return f"nul-{uuid.uuid4().hex[:12]}"


def _tool_events() -> list[dict]:
    return [{"name": "view", "status": "done", "result": _ZIP_TEXT, "args": {"p": _LONE}}]


@_DB_GATE
def test_the_fold_saves_a_tool_result_that_held_nul(graph_as_app) -> None:  # noqa: F811
    """The chat_fold write: an assistant row whose tool result held zip bytes."""
    from gateway.routes.chat import MessageRecord, _ensure_session, _upsert_messages

    org, sid, mid = graph_as_app.org_a, _sid(), f"a-{uuid.uuid4().hex[:8]}"
    _ensure_session(sid, _ALICE, "task-manager", organization_id=org)
    record = MessageRecord(
        id=mid, role="assistant", content="Read it.\x00", timestamp=1_790_000_000_000,
        tool_events=_tool_events(), progress_lines=["step\x00"],
        reasoning="r\x00" + _LONE, agent_state={"k": "v\x00"},
        custom_events=[{"x": "y\x00"}],
    )
    declined = _upsert_messages(
        sid, [record], actor_email=_ALICE, agent_name="task-manager",
        author_from_run=True, organization_id=org,
    )
    assert declined == []
    row = _admin_one(
        graph_as_app,
        "SELECT content, reasoning, tool_events::text AS te, progress_lines::text AS pl, "
        "agent_state::text AS st, custom_events::text AS ce FROM chat_message WHERE id = :m",
        m=mid,
    )
    assert row is not None
    assert row.content == "Read it."
    assert row.reasoning == "rhalf a pair  here"
    assert json.loads(row.te)[0]["result"] == "PK\x03\x04\x14\x06word/document.xml"
    for text_ in (row.te, row.pl, row.st, row.ce):
        assert "\\u0000" not in text_


@_DB_GATE
def test_the_browser_save_of_such_a_row_answers_200(graph_as_app) -> None:  # noqa: F811
    """The route that answered 500 fourteen times in a minute."""
    client = _client(_user(_ALICE, graph_as_app.org_a))
    sid, mid = _sid(), f"u-{uuid.uuid4().hex[:8]}"
    _new_session(client, sid)
    # json.dumps escapes the lone surrogate, as the browser's JSON.stringify does.
    body = json.dumps([{
        "id": mid, "role": "user", "content": "see \x00 this", "timestamp": 1_790_000_000_000,
        "tool_events": _tool_events(), "progress_lines": [], "agent_state": None,
        "custom_events": [],
    }])
    saved = client.post(f"/chat/sessions/{sid}/messages", content=body,
                        headers={"Content-Type": "application/json"})
    assert saved.status_code == 200, saved.text
    row = _admin_one(graph_as_app, "SELECT content FROM chat_message WHERE id = :m", m=mid)
    assert row is not None and row.content == "see  this"


@_DB_GATE
def test_the_run_trace_saves_a_trace_that_held_nul(graph_as_app) -> None:  # noqa: F811
    from gateway.run_trace import _persist_row

    run_id = f"run-nul-{uuid.uuid4().hex[:8]}"
    _persist_row({
        "run_id": run_id, "thread_id": _sid(), "agent_name": "task-manager",
        "user_id": _ALICE, "model": "m", "status": "error", "tool_count": 1,
        "tool_summary": [{"name": "view", "preview": _ZIP_TEXT}],
        "error_message": "AgentException\x00", "error_type": "AgentException",
        "trace": {"events": [{"result": _ZIP_TEXT + _LONE}]}, "flagged": False,
    }, graph_as_app.org_a)
    row = _admin_one(
        graph_as_app,
        "SELECT error_message, trace::text AS tr, tool_summary::text AS ts "
        "FROM agent_run WHERE run_id = :r", r=run_id,
    )
    assert row is not None
    assert row.error_message == "AgentException"
    assert "\\u0000" not in row.tr and "\\u0000" not in row.ts
