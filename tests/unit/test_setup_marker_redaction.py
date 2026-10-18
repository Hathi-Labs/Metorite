"""WS-54 fix round 2, item 5 — no SETUP value reaches a stored chat or session.

A chat agent writes ``<<<SETUP:github:GITHUB_TOKEN=ghp_…>>>`` into its
answer. The executor saved the native session (``_finish_native_session``)
BEFORE it stripped the marker, and the chat transcript stored the raw answer
too. So the token sat in plain text in ``maf_agent_session`` and in
``chat_message``. ``acb_common.setup_markers`` now replaces each marker on the
write path of both stores. No database: the session body is pure, and the
chat upsert runs on a fake session that records each bind (the idiom of
``test_chat_nul_persist.py``).

R7, the mutations. Each one turns a test here red (run 2026-10-18):

* R2-5-M1 ``_session_body`` skips ``redact_tree`` ->
  ``test_the_native_session_body_holds_no_setup_value``.
* R2-5-M2 ``_upsert_messages`` skips ``_without_setup_values`` ->
  ``test_the_chat_transcript_holds_no_setup_value``.
* R2-5-M3 ``redact`` leaves a marker that never closes ->
  ``test_a_marker_is_removed_in_every_shape``.
* R2-5-M4 ``run_trace._persist_row`` skips ``redact_tree`` ->
  ``test_an_errored_run_trace_holds_no_setup_value``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from acb_common.setup_markers import redact, redact_tree

TOKEN = "ghp_SETUP_SENTINEL_0123456789abcdef"
MARKER = f"<<<SETUP:github:GITHUB_TOKEN={TOKEN}>>>"


def test_a_marker_is_removed_in_every_shape() -> None:
    assert redact(f"Saved. {MARKER} Done.") == (
        "Saved. [setup value removed: github GITHUB_TOKEN] Done.")
    # A value with a '>' (the callers save none, and none may be stored).
    assert TOKEN not in redact(f"<<<SETUP:x:KEY=a>{TOKEN}>>>")
    # A marker that a cut stream never closed.
    assert TOKEN not in redact(f"first line\n<<<SETUP:github:GITHUB_TOKEN={TOKEN}")
    assert redact("no marker here") == "no marker here"
    tree = redact_tree({"a": [MARKER, ("x", MARKER)], "n": 3, "b": {"c": MARKER}})
    assert TOKEN not in json.dumps(tree)
    assert tree["n"] == 3
    # The replacement is not a marker, so a model that reads it saves nothing.
    assert "<<<SETUP:" not in redact(MARKER)


def test_the_native_session_body_holds_no_setup_value() -> None:
    agent_framework = pytest.importorskip("agent_framework")
    store = pytest.importorskip("orchestrator.native_session_store")

    session = agent_framework.AgentSession()
    session.state[store.HISTORY_SOURCE_ID] = {"messages": [
        agent_framework.Message(role="user", contents=[f"my token is in {MARKER}"]),
        agent_framework.Message(role="assistant", contents=[f"Saved. {MARKER}"]),
    ]}
    turn = store.SessionTurn(
        organization_id="org-a", thread_id="s-1", agent_name="probe",
        outcome=store.NO_ROW, session=session,
        history=store.SessionHistoryProvider(), fingerprint="f", save_digest="d",
    )
    body, count = asyncio.run(store._session_body(turn, 100_000))
    assert count == 2
    assert TOKEN not in body
    assert "setup value removed" in body


class _Recording:
    def __init__(self) -> None:
        self.writes: list[dict[str, Any]] = []

    def execute(self, _sql: Any, params: dict[str, Any] | None = None) -> Any:
        self.writes.append(dict(params or {}))

        class _R:
            def first(self) -> Any:
                return ((params or {}).get("id", "row"),)

            def fetchall(self) -> list[Any]:
                return []

            def scalar(self) -> Any:
                return None

        return _R()

    def commit(self) -> None:
        return None


def test_the_chat_transcript_holds_no_setup_value(monkeypatch) -> None:
    import acb_graph
    from gateway.routes.chat import MessageRecord, _upsert_messages

    session = _Recording()

    @contextlib.contextmanager
    def _tenant_session(_org: str | None):
        yield session

    monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
    record = MessageRecord(
        id="m-setup", role="assistant", content=f"Saved the key. {MARKER}",
        timestamp=1_790_000_000_000,
        tool_events=[{"name": "x", "result": MARKER}],
        progress_lines=[MARKER], reasoning=MARKER,
        agent_state={"segments": [{"text": MARKER}]},
        custom_events=[{"name": "card", "value": {"text": MARKER}}],
        author_kind="agent",
    )
    _upsert_messages("s-1", [record], actor_email="a@x.test", agent_name="probe",
                     author_from_run=True, organization_id="org-a")
    assert session.writes, "nothing was written"
    assert TOKEN not in json.dumps(session.writes, default=str)
    assert any("setup value removed" in str(v) for w in session.writes for v in w.values())


def test_an_errored_run_trace_holds_no_setup_value(monkeypatch) -> None:
    """An errored run keeps its folded message in ``agent_run.trace``."""
    import acb_graph
    from gateway.run_trace import _persist_row, build_run_trace_row

    session = _Recording()

    @contextlib.contextmanager
    def _tenant_session(_org: str | None):
        yield session

    monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
    row = build_run_trace_row(
        run_id="r-1", thread_id="s-1", agent_name="probe", user_id="a@x.test", model=None,
        events=[{"type": "RUN_STARTED"}, {"type": "RUN_ERROR", "message": f"failed {MARKER}"}],
        folded={"content": f"Saved. {MARKER}", "tool_events": [], "reasoning": None,
                "custom_events": []},
    )
    _persist_row(row, "org-a")
    assert session.writes and TOKEN not in json.dumps(session.writes, default=str)
