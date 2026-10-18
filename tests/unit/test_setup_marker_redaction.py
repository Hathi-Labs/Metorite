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
* R3-3 (fix round 3) is replaced by fix round 5: redact the PARSED object, then
  serialize -> ``test_a_marker_inside_a_json_object_leaves_valid_json``.
* R5-1-M1 (fix round 5) ``MARKER_RE`` stops at a double quote again (the
  round-3 bound) -> ``test_a_quoted_value_leaves_no_token``.
* R5-1-M2 ``OPEN_MARKER_RE`` stops at a quote again -> the same test.
* R5-2-M1 ``Mem0Client.add`` skips ``redact_tree`` ->
  ``test_the_run_end_extraction_reaches_mem0_and_graphiti_with_no_token``.
* R5-2-M2 ``GraphitiClient.add_episode`` skips ``redact`` -> the same test.
* R4-2-M1 (fix round 4) the session upsert stores the preview with
  ``storable`` only -> ``test_the_session_preview_holds_no_setup_value``.
* R4-2-M2 the session PATCH stores the preview with ``storable`` only -> the
  same test.
* R4-2-M3 ``/memory/{scope}/add`` passes the raw messages to Mem0 ->
  ``test_the_memory_add_holds_no_setup_value``.

⚠️ This file proves the MODEL's marker is never stored. A member's own pasted
secret is not detected (spec §7.6, HANDOFF H-357).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from acb_common.setup_markers import redact, redact_tree

#: A fake token, built at run time so no token-shaped literal sits in the
#: source for the secret scan to flag (WS-54 fix round 6).
TOKEN = "_".join(["ghp", "SETUP", "SENTINEL", "x" * 16])
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
            rowcount = 1

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


def test_a_marker_inside_a_json_object_leaves_valid_json() -> None:
    """R3-3 kept, R5-1: redact the PARSED object, then serialize. The JSON
    stays valid, every other field keeps its value, and no token is left."""
    state = {"a": "done <<<SETUP:github:GITHUB_TOKEN=" + TOKEN, "b": 1,
             "c": "x>>>", "events": [{"result": f"ok {MARKER}"}]}
    text = json.dumps(redact_tree(state))
    back = json.loads(text)
    assert TOKEN not in text
    assert back["b"] == 1 and back["c"] == "x>>>"
    assert back["a"] == "done [setup value removed: github GITHUB_TOKEN]"


def test_a_quoted_value_leaves_no_token() -> None:
    """R5-1: the callers' pattern ``=([^>]+)`` takes a quoted value, so the
    redaction takes it whole, closed and unclosed."""
    closed = f'Saved. <<<SETUP:github:GITHUB_TOKEN="{TOKEN}">>> Done.'
    assert redact(closed) == "Saved. [setup value removed: github GITHUB_TOKEN] Done."
    single = f"<<<SETUP:github:GITHUB_TOKEN='{TOKEN}'>>>"
    assert TOKEN not in redact(single)
    unclosed = f'first\n<<<SETUP:github:GITHUB_TOKEN="{TOKEN}" and more'
    out = redact(unclosed)
    assert TOKEN not in out and out.startswith("first\n")
    # A value with a '>' that is not '>>>' is taken whole too.
    assert TOKEN not in redact(f"<<<SETUP:x:KEY=a>{TOKEN}>b>>> tail")
    assert redact(f"<<<SETUP:x:KEY=a>{TOKEN}>b>>> tail").endswith(" tail")


def test_no_caller_passes_serialized_json_to_redact() -> None:
    """R5-1: ``redact`` gets decoded text only. A caller that redacts JSON
    parses it and calls ``redact_tree``."""
    import re as _re
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    bad = []
    for base in ("apps", "packages"):
        for path in (root / base).rglob("*.py"):
            source = path.read_text(encoding="utf-8", errors="replace")
            if "setup_markers" not in source:
                continue
            if _re.search(r"\bredact(?:_tree)?\(\s*json\.dumps", source):
                bad.append(str(path.relative_to(root)))
    assert bad == [], bad

def test_the_session_preview_holds_no_setup_value(monkeypatch) -> None:
    """The browser sends the last reply as ``last_preview`` (AgentChat), on
    the upsert and on the PATCH."""
    import acb_graph
    from gateway.routes.chat import (
        SessionPatchRequest,
        SessionUpsertRequest,
        _patch_session,
        _upsert_session,
    )

    session = _Recording()

    @contextlib.contextmanager
    def _tenant_session(_org: str | None):
        yield session

    monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
    _upsert_session("a@x.test", SessionUpsertRequest(
        id="s-1", title=f"t {MARKER}", last_preview=f"Saved. {MARKER}"),
        organization_id="org-a")
    upserts = len(session.writes)
    _patch_session("s-1", "a@x.test", SessionPatchRequest(
        title=f"t {MARKER}", last_preview=f"Saved. {MARKER}"), organization_id="org-a")
    assert upserts and len(session.writes) > upserts, "both writes ran"
    for params in session.writes:
        assert TOKEN not in json.dumps(params, default=str), params
    previews = [w["last_preview"] for w in session.writes if w.get("last_preview")]
    assert len(previews) >= 2 and all("setup value removed" in p for p in previews)


async def test_the_memory_add_holds_no_setup_value(monkeypatch) -> None:
    import asyncio

    from gateway.routes import memory

    seen: list[Any] = []

    class _Mem0:
        async def add(self, scope: str, messages: Any, agent_id: str = "") -> None:
            seen.append(messages)

    monkeypatch.setattr(memory, "_get_mem0", lambda: _Mem0())
    monkeypatch.setattr(memory, "_authorize_scope", lambda *a, **k: None)
    req = memory.AddRequest(messages=[
        {"role": "assistant", "content": f"Saved. {MARKER}"},
        {"role": "user", "content": "thanks"},
    ])
    out = await memory.add_memories("a@x.test", req, user=None)  # type: ignore[arg-type]
    for _ in range(5):
        await asyncio.sleep(0)
    assert out["status"] == "queued"
    assert seen and TOKEN not in json.dumps(seen)
    assert seen[0][1] == {"role": "user", "content": "thanks"}


async def test_the_run_end_extraction_reaches_mem0_and_graphiti_with_no_token(monkeypatch) -> None:
    """R5-2: the run-end extraction of ``routes/agent.py`` and ``main.py``
    sends ``build_extraction_conversation(...)`` to ``add_memories_background``,
    and the member's message to ``add_episode``. The Tier 1 stream does not
    strip a marker, so the folded answer can hold one. Neither store gets it."""
    from acb_memory import graphiti_client, mem0_client
    from gateway.chat_fold import build_extraction_conversation

    mem0_seen: list[Any] = []
    graph_seen: list[dict[str, Any]] = []

    class _Mem0:
        def add(self, messages: Any, user_id: str = "", agent_id: str = "") -> None:
            mem0_seen.append(messages)

    class _Graph:
        async def add_episode(self, **kw: Any) -> None:
            graph_seen.append(kw)

    client = mem0_client.get_memory_client()
    monkeypatch.setattr(client, "_get_client", lambda: _Mem0())
    graph = graphiti_client.get_graphiti_client()

    async def _g() -> Any:
        return _Graph()

    monkeypatch.setattr(graph, "_get_graphiti", _g)
    folded = {"content": f"Saved the key. {MARKER}", "tool_events": [], "reasoning": None,
              "custom_events": []}
    conv = build_extraction_conversation([], f"my token {MARKER}", folded)
    assert conv and TOKEN in json.dumps(conv), "the probe must carry the marker in"
    await mem0_client.add_memories_background("a@x.test", conv, agent_id="probe")
    await graphiti_client.add_episode(name="agent:probe:a", content=f"my token {MARKER}",
                                      group_id="a@x.test")
    assert mem0_seen and TOKEN not in json.dumps(mem0_seen)
    assert graph_seen and TOKEN not in json.dumps(graph_seen, default=str)
