"""The "used your profile" mark, server half (WS-63 PZ-20b).

Spec: ``project-docs/specs/personalization.md`` §13.3 item 1 and §13.4. The
owner decided on 2026-10-30 that the mark shows only when the member's own
stored profile holds something. A block of the directory name draws none.

R7 fences named here:

* ``pz20b-event``: a solo orchestrator run with a block from a stored profile
  that holds something gives one ``assistant_profile_used`` event, and the
  value is ``{"v": 1}``. A name-only block, a profile of empty values, a
  shared room, a run with no member, the flag off and another agent give
  none.
* ``pz20b-peek``: the helper does not use up the due line of the interview.
* ``pz20b-held``: the stream sends the mark once, right after the run's
  first output. A run that gives no output sends no mark, so the fold saves
  no row for it. Each event that releases the mark alone makes the fold
  save a row.
* ``pz20b-reload``: ``fold_run_events`` keeps the event, so a reload draws it.
* ``pz20b-reader``: the history read gives the mark only to the member who
  ran the turn.
* ``pz20b-const``: the Python name and the web name agree.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from acb_common import assistant_profile as ap
from orchestrator import executor as ex

from tests.unit import test_executor_profile_block as epb
from tests.unit.test_assistant_profile_interview import NOW, _iv, clock, writes  # noqa: F401
from tests.unit.test_executor_profile_block import run  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
MARK = {"type": "CUSTOM", "name": ap.PROFILE_USED_EVENT, "value": {"v": 1}}


def _events(agent: str = "orchestrator") -> list[dict[str, Any]]:
    event = ex._profile_used_event(agent)
    return [] if event is None else [event]


# ── pz20b-event ────────────────────────────────────────────────────────────


def test_a_stored_profile_with_content_gives_one_event_with_no_text(run) -> None:  # noqa: F811
    (event,) = _events()
    assert event == MARK
    assert event["name"] == "assistant_profile_used"


@pytest.mark.parametrize("raw", [
    {"v": 1, "address": {"greeting": "brief"}},
    {"v": 1, "style": {"length": "short"}},
    {"v": 1, "style": {"notes": ["No emoji"]}},
    {"v": 1, "aliases": [{"id": "a1", "alias": "Raj", "email": "raj@fracktal.in"}]},
    {"v": 1, "facts": [{"id": "f1", "text": "Travels", "kind": "routine"}]},
])
def test_any_one_kind_of_stored_content_counts(run, raw) -> None:  # noqa: F811
    run["raw"] = raw
    assert len(_events()) == 1
    assert ap.has_stored_content(raw) is True


@pytest.mark.parametrize("raw", [None, {}, {"v": 1, "learning": True}])
def test_a_name_only_block_gives_none(run, raw) -> None:  # noqa: F811
    run["raw"] = raw
    run["preferred"] = "Ana"
    run["display"] = "Ana Rao"
    # The block holds the directory name, so the model gets it.
    assert ex._member_profile_entry("orchestrator")[0]
    ex._PROFILE_CACHE.clear()
    assert _events() == []


#: Stored values that hold keys and no content (the PZ-20b review, P2).
EMPTY_VALUES = [
    {"v": 1, "style": {"notes": []}},
    {"v": 1, "address": {"name": ""}},
    {"v": 1, "aliases": []},
    {"v": 1, "facts": []},
    {"v": 1, "style": {}, "address": {}, "aliases": [], "facts": []},
]


@pytest.mark.parametrize("raw", EMPTY_VALUES)
def test_a_profile_of_empty_values_holds_nothing(raw) -> None:
    assert ap.has_stored_content(raw) is False
    # The proof: its block equals the block of the directory name only.
    assert ap.member_block(raw, "Ana", "Ana Rao") == ap.member_block(None, "Ana", "Ana Rao")


@pytest.mark.parametrize("raw", EMPTY_VALUES)
def test_a_profile_of_empty_values_gives_no_mark(run, raw) -> None:  # noqa: F811
    run.update(raw=raw, preferred="Ana", display="Ana Rao")
    assert ex._member_profile_entry("orchestrator")[0]
    ex._PROFILE_CACHE.clear()
    assert _events() == []


def test_a_shared_room_gives_none(run) -> None:  # noqa: F811
    token = ex._RUN_SOLO_ROOM.set(False)
    try:
        assert _events() == []
    finally:
        ex._RUN_SOLO_ROOM.reset(token)


def test_a_run_with_no_member_gives_none(run) -> None:  # noqa: F811
    run["member"] = ""
    assert _events() == []


def test_the_flag_off_gives_none(run, monkeypatch) -> None:  # noqa: F811
    epb._flag(monkeypatch, False)
    assert _events() == []


def test_another_agent_gives_none(run) -> None:  # noqa: F811
    assert _events("email-assistant") == []


def test_a_cached_turn_gives_the_event_again(run) -> None:  # noqa: F811
    assert len(_events()) == 1
    assert len(_events()) == 1
    assert len(run["reads"]) == 1


def test_the_helper_builds_the_event_only(run, monkeypatch) -> None:  # noqa: F811
    """The relay gets a line from ``_sse``. The helper calls none, so a mark
    that the stream never sends never reaches the relay."""
    sent: list[Any] = []
    monkeypatch.setattr(ex, "_sse", lambda payload: sent.append(payload) or "")
    assert len(_events()) == 1
    assert sent == []


# ── pz20b-peek ─────────────────────────────────────────────────────────────


def test_the_helper_leaves_the_due_line_for_the_model(run, clock, writes) -> None:  # noqa: F811
    run["raw"] = {**copy.deepcopy(epb.EXAMPLE), "interview": _iv()}
    assert len(_events()) == 1
    # The model's build comes after the helper, and it still gets the line.
    block = ex._member_profile_entry("orchestrator")[0]
    assert "Interview (one question is due)" in block


def test_the_helper_runs_inside_the_peek() -> None:
    src = inspect.getsource(ex._profile_used_event)
    assert "with _interview_peek():" in src
    assert src.index("with _interview_peek():") < src.index("_member_profile_entry(")


def test_the_stream_reads_the_mark_after_the_access_check_and_the_compose() -> None:
    """After the compose step, so the cache serves the read. After the
    access check, so a refused run has no mark."""
    src = inspect.getsource(ex.run_agent_stream)
    at = src.index("_ProfileMarkHold(_profile_used_event(agent_name))")
    assert src.index("await _assert_may_run_agent(agent_name)") < at
    assert src.index('_laps.lap("compose")') < at
    # No other place builds the event, and none sends it at once.
    assert src.count("_profile_used_event(") == 1
    assert "PROFILE_USED_EVENT" not in src


# ── pz20b-held: the real stream path, with a stub model ────────────────────


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[Any] = []


class _StubAgent:
    """A native-MAF-shaped agent. ``run(stream=True)`` yields *updates*, then
    raises *fault* when one is given (Tier 1). ``run()`` answers *batch*, or
    raises it when it is an exception (the Tier 2 fallback)."""

    def __init__(self, updates: list[Any], *, fault: Exception | None = None,
                 batch: Any = "") -> None:
        self.name = "test-agent"
        self.tools: list[Any] = []
        self.default_options: dict[str, Any] = {}
        self._updates = updates
        self._fault = fault
        self._batch = batch

    def run(self, *_a: Any, stream: bool = False, **_k: Any) -> Any:
        return self._stream() if stream else self._answer()

    async def _stream(self) -> Any:
        for u in self._updates:
            yield u
        if self._fault is not None:
            raise self._fault

    async def _answer(self) -> _Resp:
        if isinstance(self._batch, Exception):
            raise self._batch
        return _Resp(self._batch)

    async def __aenter__(self) -> _StubAgent:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


def _text(t: str) -> Any:
    return SimpleNamespace(role="assistant", message_id="m1",
                           contents=[SimpleNamespace(type="text", text=t)])


def _tool_start(name: str, call_id: str) -> Any:
    return SimpleNamespace(role="assistant", message_id="m1", contents=[SimpleNamespace(
        type="function_call", name=name, arguments="{}", call_id=call_id)])


@pytest.fixture()
def stream(tmp_path, monkeypatch):
    """Drive the REAL ``run_agent_stream`` with a stub agent and a stub
    mark. The profile rules are the tests above. These tests hold the
    place of the mark in the stream."""
    import acb_common
    from acb_common import get_settings
    from acb_common.db import bind_tenant, release_tenant
    from acb_skills.write_artifact import bind_artifact_context
    from orchestrator import stream_relay

    async def _nothing(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(get_settings(), "agents_clone_dir", str(tmp_path / "agents"))
    monkeypatch.setattr(ex, "_profile_used_event", lambda agent_name: dict(MARK))
    monkeypatch.setattr(ex, "build_integrations", lambda *a, **k: ({}, {}))
    # No Redis and no audit row: the relay, the live-run index, the activity
    # feed and the audit write do nothing here.
    for name in ("mark_active", "mark_inactive", "register_live_run", "unregister_live_run"):
        monkeypatch.setattr(stream_relay, name, _nothing)
    monkeypatch.setattr(ex, "_push_sse_to_stream", _nothing)
    monkeypatch.setattr(ex, "record", lambda *a, **k: None)
    monkeypatch.setattr(acb_common, "publish_activity", lambda *a, **k: None)
    token = bind_tenant("org-pz20b-probe")

    def drive(agent: _StubAgent) -> list[dict[str, Any]]:
        class _Loaded:
            def __init__(self) -> None:
                self.agent_dir = tmp_path
                self.agent_name = "test-agent"
                self.config: dict[str, Any] = {}

            def build_agents(self) -> list[Any]:
                return [agent]

        class _Ctx:
            def __enter__(self) -> _Loaded:
                return _Loaded()

            def __exit__(self, *_a: Any) -> bool:
                return False

        monkeypatch.setattr(ex, "load_agent", lambda *a, **k: _Ctx())

        async def collect() -> list[str]:
            return [line async for line in ex.run_agent_stream(
                "test-agent-unregistered", {"message": "hi"})]

        frames = asyncio.run(collect())
        return [json.loads(part[5:]) for line in frames for part in line.split("\n")
                if part.startswith("data:")]

    try:
        yield drive
    finally:
        release_tenant(token)
        bind_artifact_context()
        ex._RUN_QUEUES.clear()
        ex._pending_user_input.clear()


def _marks(events: list[dict[str, Any]]) -> list[int]:
    return [i for i, e in enumerate(events) if e.get("name") == ap.PROFILE_USED_EVENT]


def _fold(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    from gateway.chat_fold import fold_run_events

    return fold_run_events(events)


def test_a_turn_that_never_answers_sends_no_mark_and_saves_no_row(stream) -> None:
    """Tier 1 fails before its first output, and the Tier 2 fallback fails
    too, as a 402 or a 429 does. The fold of the run gives no row."""
    events = stream(_StubAgent([], fault=RuntimeError("model timed out"),
                               batch=RuntimeError("402: no credits")))
    assert [e["type"] for e in events if e["type"] == "RUN_ERROR"] == ["RUN_ERROR"]
    assert _marks(events) == []
    assert _fold(events) is None


def test_a_tool_start_and_then_a_fault_sends_no_mark(stream) -> None:
    """A tool start is output for the Tier 1 loop, so the run does not fall
    back. It is not an answer: the fold keeps no row for it alone."""
    events = stream(_StubAgent([_tool_start("query_inbox", "c1")],
                               fault=RuntimeError("connection reset")))
    assert "TOOL_CALL_START" in [e["type"] for e in events]
    assert events[-1]["type"] == "RUN_ERROR"
    assert _marks(events) == []
    assert _fold(events) is None


def test_an_answer_sends_one_mark_right_after_its_first_output(stream) -> None:
    events = stream(_StubAgent([_text("Hello "), _text("Ana.")]))
    (at,) = _marks(events)
    first = next(i for i, e in enumerate(events) if ex._is_answer_event(e))
    assert events[first]["type"] == "TEXT_MESSAGE_CONTENT"
    assert at == first + 1
    assert events[at]["value"] == {"v": 1}
    assert events[-1]["type"] == "RUN_FINISHED"
    folded = _fold(events) or {}
    assert folded["content"] == "Hello Ana."
    assert [e["name"] for e in folded["custom_events"]] == [ap.PROFILE_USED_EVENT]


def test_a_fallback_to_tier_2_sends_the_mark_with_its_answer(stream) -> None:
    events = stream(_StubAgent([], fault=RuntimeError("stream refused"),
                               batch="The answer."))
    (at,) = _marks(events)
    assert events[at - 1]["type"] == "TEXT_MESSAGE_CONTENT"
    assert events[-1]["type"] == "RUN_FINISHED"
    assert (_fold(events) or {})["content"] == "The answer."


#: One event of each kind that releases the mark.
ANSWER_EVENTS = [
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "Hi"},
    {"type": "REASONING_MESSAGE_CONTENT", "delta": "thinking"},
    {"type": "THINKING_TEXT_MESSAGE_CONTENT", "delta": "thinking"},
    {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": "ok"},
    {"type": "TOOL_CALL_END", "toolCallId": "c1"},
    {"type": "TODO_LIST", "todos": [{"id": "1", "title": "x", "status": "open"}]},
    {"type": "CUSTOM", "name": "generative_ui", "value": {"component": "x"}},
]

#: Events that do not release it.
NOT_ANSWER_EVENTS = [
    {"type": "TEXT_MESSAGE_START", "messageId": "m1"},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "  "},
    {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
    {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "x"},
    {"type": "TOOL_CALL_ARGS", "toolCallId": "c1", "delta": "{}"},
    {"type": "PROGRESS_UPDATE", "message": "Working"},
    {"type": "TODO_LIST", "todos": []},
    {"type": "RUN_ERROR", "message": "boom"},
    {"type": "CUSTOM", "name": "ai.route", "value": {}},
    {"type": "CUSTOM", "name": "answer_check.held", "value": {}},
    {"type": "CUSTOM", "name": "frontend_tool", "value": {}},
    MARK,
]


@pytest.mark.parametrize("event", ANSWER_EVENTS)
def test_each_event_that_releases_the_mark_alone_saves_a_row(event) -> None:
    """So the mark never makes a row: the event that sends it already does."""
    assert ex._is_answer_event(event) is True
    assert _fold([{"type": "RUN_STARTED"}, dict(event), {"type": "RUN_ERROR"}]) is not None


@pytest.mark.parametrize("event", NOT_ANSWER_EVENTS)
def test_a_label_a_start_or_a_beat_does_not_release_the_mark(event) -> None:
    assert ex._is_answer_event(event) is False


def test_the_hold_sends_the_mark_once_and_through_sse(monkeypatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(ex, "_sse", lambda payload: sent.append(payload) or "line")
    hold = ex._ProfileMarkHold(dict(MARK))
    assert hold.after({"type": "TOOL_CALL_START"}) == ()
    assert sent == []
    assert hold.after(ANSWER_EVENTS[0]) == ("line",)
    assert hold.after(ANSWER_EVENTS[0]) == ()
    assert sent == [MARK]
    assert ex._ProfileMarkHold().after(ANSWER_EVENTS[0]) == ()


def test_a_mark_that_went_out_alone_would_save_a_row() -> None:
    """Why the mark waits: the fold keeps a run that sent only a CUSTOM
    event (the PZ-20b review, P1)."""
    assert _fold([{"type": "RUN_STARTED"}, dict(MARK), {"type": "RUN_ERROR"}]) is not None


# ── pz20b-reload ───────────────────────────────────────────────────────────


def test_a_reload_keeps_the_event() -> None:
    folded = _fold([
        {"type": "RUN_STARTED", "runId": "r1", "threadId": "t1"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Hello."},
        dict(MARK),
        {"type": "RUN_FINISHED"},
    ]) or {}
    kept = [e for e in folded["custom_events"] if e["name"] == ap.PROFILE_USED_EVENT]
    assert len(kept) == 1 and kept[0]["value"] == {"v": 1}


# ── pz20b-reader ───────────────────────────────────────────────────────────

ANA = "ana@alpha.example"
BO = "bo@alpha.example"


def _row(run_member: str | None) -> Any:
    return SimpleNamespace(
        id="m1", role="assistant", content="Hello Ana.", timestamp_ms=1,
        tool_events=[], progress_lines=[], reasoning=None, agent_state=None,
        custom_events=[{"name": "ai.route", "value": {}}, dict(MARK)],
        author_email="orchestrator", author_kind="agent", authority=None,
        run_member_email=run_member,
    )


def _names(body: dict[str, Any]) -> list[str]:
    return [e["name"] for e in body["customEvents"]]


def test_the_member_who_ran_the_turn_reads_the_mark() -> None:
    from gateway.routes.chat import _render_message

    body = _render_message(_row(ANA), ANA.upper(), None)
    assert _names(body) == ["ai.route", ap.PROFILE_USED_EVENT]
    assert body["runMemberEmail"] == ANA


def test_another_reader_of_a_shared_chat_gets_no_mark() -> None:
    from gateway.routes.chat import _render_message

    body = _render_message(_row(ANA), BO, None)
    assert _names(body) == ["ai.route"]
    assert body["content"] == "Hello Ana."


@pytest.mark.parametrize("run_member", [None, "", "  "])
def test_a_row_that_names_no_run_member_gives_no_mark(run_member) -> None:
    from gateway.routes.chat import _render_message

    assert _names(_render_message(_row(run_member), ANA, None)) == ["ai.route"]


def test_the_history_read_selects_the_run_member() -> None:
    from gateway.routes import chat

    src = inspect.getsource(chat._get_messages)
    assert "run_member_email FROM chat_message" in src
    assert ap.PROFILE_USED_EVENT in chat.RUN_MEMBER_ONLY_EVENTS


# ── pz20b-const ────────────────────────────────────────────────────────────


def test_the_web_constant_matches() -> None:
    ts = (REPO / "workbench/control_plane/src/lib/profileMark.ts").read_text(encoding="utf-8")
    assert f'PROFILE_USED_EVENT = "{ap.PROFILE_USED_EVENT}"' in ts
