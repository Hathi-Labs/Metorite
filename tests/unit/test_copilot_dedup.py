"""Test ASSISTANT_MESSAGE deduplication in MetoriteCopilotAgent.

Verifies that when the Copilot SDK fires both ASSISTANT_MESSAGE_DELTA
(token-by-token) and ASSISTANT_MESSAGE (full content) during streaming,
the agent only emits the deltas — not a duplicate full message.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from copilot.session import SessionEvent, SessionEventType
from copilot.generated.session_events import Data


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_data(**kwargs: Any) -> Data:
    """Build a Copilot SDK Data object with only the fields we specify."""
    return Data(**kwargs)


def _make_event(
    event_type: SessionEventType,
    **data_fields: Any,
) -> SessionEvent:
    """Build a SessionEvent with the given type and data fields."""
    return SessionEvent(
        data=_make_data(**data_fields),
        id=uuid.uuid4(),
        timestamp=datetime.now(timezone.utc),
        type=event_type,
    )


# ── Mock CopilotSession that captures the event callback ───────────────────

class _MockCopilotSession:
    """A CopilotSession stand-in that captures the on() callback."""

    def __init__(self) -> None:
        self._callback: Any = None
        self.session_id = str(uuid.uuid4())

    def on(self, callback: Any) -> None:
        self._callback = callback

    def off(self, callback: Any) -> None:
        if self._callback is callback:
            self._callback = None

    async def send(self, prompt: Any) -> None:
        pass  # We feed events manually via _callback


# ── Tests ──────────────────────────────────────────────────────────────────

class TestCopilotMessageDedup:
    """Verify ASSISTANT_MESSAGE is not duplicated when deltas are streamed."""

    @pytest.fixture
    def agent(self) -> Any:
        """Create a MetoriteCopilotAgent with mocked Copilot client."""
        from orchestrator.copilot_agent import MetoriteCopilotAgent

        agent = MetoriteCopilotAgent(
            name="test-agent",
            instructions="You are a test agent.",
        )
        # Pretend we've already started (skip real Copilot CLI init).
        agent._started = True
        agent._client = MagicMock()
        return agent

    @pytest.fixture
    def mock_session(self) -> _MockCopilotSession:
        return _MockCopilotSession()

    async def _collect_updates(
        self,
        agent: Any,
        mock_session: _MockCopilotSession,
        events: list[SessionEvent],
    ) -> list[Any]:
        """Run _stream_updates; feed *events* via captured callback.

        Events are fed in a background task so the stream can consume them.
        Returns the list of AgentResponseUpdate objects yielded.
        """
        from orchestrator.copilot_agent import MetoriteCopilotAgent

        # Intercept on() to feed events in background after callback is set.
        _orig_on = _MockCopilotSession.on

        def _on_wrapper(self_mock: Any, callback: Any) -> None:
            _orig_on(self_mock, callback)

            async def _feed() -> None:
                # Yield control so send() and the queue loop can start.
                await asyncio.sleep(0)
                for ev in events:
                    callback(ev)
                callback(_make_event(SessionEventType.SESSION_IDLE))

            asyncio.ensure_future(_feed())

        # Patch _get_or_create_session → mock session.
        async def _fake_get_or_create_session(
            self_agent: Any, session: Any, streaming: bool = False,
            runtime_options: Any = None,
        ) -> Any:
            return mock_session

        # Patch _run_before_providers → minimal context.
        class _FakeContext:
            def get_messages(
                self, include_input: bool = False,  # noqa: ARG002
            ) -> list[Any]:
                return []
            instructions: list[str] = []

        async def _fake_run_before_providers(
            self_agent: Any, session: Any,
            input_messages: Any, options: Any,
        ) -> _FakeContext:
            return _FakeContext()

        with (
            patch.object(
                _MockCopilotSession, "on", _on_wrapper,
            ),
            patch.object(
                MetoriteCopilotAgent,
                "_get_or_create_session",
                _fake_get_or_create_session,
            ),
            patch.object(
                MetoriteCopilotAgent,
                "_run_before_providers",
                _fake_run_before_providers,
            ),
        ):
            updates: list[Any] = []
            stream = agent._stream_updates(
                messages=[{"role": "user", "content": "Hello"}],
            )
            async for update in stream:
                updates.append(update)
            return updates

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _text_updates(updates: list[Any]) -> list[Any]:
        """Extract updates whose first content is type 'text'."""
        return [
            u for u in updates
            if u.contents
            and getattr(u.contents[0], "type", None) == "text"
        ]

    # ── Test 1: deltas only (no ASSISTANT_MESSAGE) ─────────────────────

    async def test_deltas_only_emit_once(
        self, agent: Any, mock_session: _MockCopilotSession,
    ) -> None:
        """Deltas stream token-by-token; no duplicate."""
        events = [
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content="Hello",
                message_id="msg-1",
            ),
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content=" world",
                message_id="msg-1",
            ),
        ]
        updates = await self._collect_updates(agent, mock_session, events)

        # Should have exactly 2 text updates (one per delta).
        text_updates = [
            u for u in updates
            if u.contents and getattr(u.contents[0], "type", None) == "text"
        ]
        assert len(text_updates) == 2
        assert text_updates[0].contents[0].text == "Hello"
        assert text_updates[1].contents[0].text == " world"

    # ── Test 2: deltas + ASSISTANT_MESSAGE → ASSISTANT_MESSAGE skipped ─

    async def test_deltas_then_full_message_skips_duplicate(
        self, agent, mock_session
    ):
        """ASSISTANT_MESSAGE after deltas must be skipped."""
        events = [
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content="Hello",
                message_id="msg-1",
            ),
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content=" world",
                message_id="msg-1",
            ),
            # This full message duplicates the deltas — should be skipped.
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE,
                content="Hello world",
                message_id="msg-1",
            ),
        ]
        updates = await self._collect_updates(agent, mock_session, events)

        text_updates = [
            u for u in updates
            if u.contents and getattr(u.contents[0], "type", None) == "text"
        ]
        # Should have 2 deltas, NOT 3 (the ASSISTANT_MESSAGE is skipped).
        assert len(text_updates) == 2, (
            f"Expected 2 text updates (deltas only), got {len(text_updates)}"
        )

    # ── Test 3: ASSISTANT_MESSAGE only (no deltas) → emitted ──────────

    async def test_full_message_without_deltas_is_emitted(
        self, agent, mock_session
    ):
        """When no deltas fire, ASSISTANT_MESSAGE must be emitted."""
        events = [
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE,
                content="Hello world",
                message_id="msg-1",
            ),
        ]
        updates = await self._collect_updates(agent, mock_session, events)

        text_updates = [
            u for u in updates
            if u.contents and getattr(u.contents[0], "type", None) == "text"
        ]
        assert len(text_updates) == 1
        assert text_updates[0].contents[0].text == "Hello world"

    # ── Test 4: ASSISTANT_STREAMING_DELTA must NOT duplicate deltas ────

    async def test_streaming_delta_does_not_duplicate_message_delta(
        self, agent, mock_session
    ):
        """The SDK fires BOTH ASSISTANT_MESSAGE_DELTA and
        ASSISTANT_STREAMING_DELTA for the same content. Only
        ASSISTANT_MESSAGE_DELTA must be emitted — STREAMING_DELTA is
        ignored (matches upstream GitHubCopilotAgent). Handling both was
        the root cause of the full-response duplication bug."""
        events = [
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content="Hello",
                message_id="msg-1",
            ),
            # Parallel stream of the SAME chunk — must be ignored.
            _make_event(
                SessionEventType.ASSISTANT_STREAMING_DELTA,
                delta_content="Hello",
            ),
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content=" world",
                message_id="msg-1",
            ),
            _make_event(
                SessionEventType.ASSISTANT_STREAMING_DELTA,
                delta_content=" world",
            ),
        ]
        updates = await self._collect_updates(agent, mock_session, events)

        text_updates = [
            u for u in updates
            if u.contents and getattr(u.contents[0], "type", None) == "text"
        ]
        # Exactly 2 emissions (from MESSAGE_DELTA only), NOT 4.
        assert len(text_updates) == 2, (
            f"STREAMING_DELTA must not duplicate MESSAGE_DELTA; "
            f"got {len(text_updates)} emissions"
        )
        full = "".join(u.contents[0].text for u in text_updates)
        assert full == "Hello world"

    # ── Test 5: Double ASSISTANT_MESSAGE without deltas ───────────────

    async def test_double_full_message_without_deltas_only_emits_once(
        self, agent, mock_session
    ):
        """Two ASSISTANT_MESSAGE events without deltas → only first emitted."""
        events = [
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE,
                content="Hello world",
                message_id="msg-1",
            ),
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE,
                content="Hello world",
                message_id="msg-1",
            ),
        ]
        updates = await self._collect_updates(agent, mock_session, events)

        text_updates = [
            u for u in updates
            if u.contents and getattr(u.contents[0], "type", None) == "text"
        ]
        # Only the first ASSISTANT_MESSAGE should be emitted.
        assert len(text_updates) == 1

    # ── Test 6: Deltas + STREAMING_DELTA noise + ASSISTANT_MESSAGE ─────

    async def test_real_world_event_mix_no_duplication(
        self, agent, mock_session
    ):
        """Realistic event stream: MESSAGE_DELTAs interleaved with
        STREAMING_DELTA noise, ended by a full ASSISTANT_MESSAGE.
        Only the MESSAGE_DELTAs should be emitted — once each."""
        events = [
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content="Part",
                message_id="msg-1",
            ),
            _make_event(
                SessionEventType.ASSISTANT_STREAMING_DELTA,
                delta_content="Part",
            ),
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE_DELTA,
                delta_content=" one",
                message_id="msg-1",
            ),
            _make_event(
                SessionEventType.ASSISTANT_STREAMING_DELTA,
                delta_content=" one",
            ),
            # Final full message — duplicates the deltas, must be skipped.
            _make_event(
                SessionEventType.ASSISTANT_MESSAGE,
                content="Part one",
                message_id="msg-1",
            ),
        ]
        updates = await self._collect_updates(agent, mock_session, events)

        text_updates = [
            u for u in updates
            if u.contents and getattr(u.contents[0], "type", None) == "text"
        ]
        # 2 MESSAGE_DELTAs only (STREAMING_DELTA + full message skipped).
        assert len(text_updates) == 2
        full = "".join(u.contents[0].text for u in text_updates)
        assert full == "Part one"

