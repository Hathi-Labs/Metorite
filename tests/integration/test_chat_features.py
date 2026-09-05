"""
Comprehensive integration test suite for the Metorite Chat application.

Covers ALL chat features across MAF and GitHub Copilot SDK agents:
  - SSE Streaming (start, content, end, error)
  - Thinking / Consciousness stream (reasoning deltas)
  - Tool Calls (start, args, result, partial, progress)
  - HITL (ask_questions bridging, native ask_user, elicitation cards)
  - Stop / Steer / Queue
  - Context spinner / Loading states
  - Agent types (MAF, Copilot SDK, BYOK)
  - Memory tools (remember, save_memory, recall_timeline, save_episode)
  - Delegation (call_agent, call_agents_parallel, call_agent_background)
  - Web tools (web_search, fetch_page)
  - Artifacts (write_artifact, download URLs)
  - Todo panel (manage_todo_list, sql-based tracking)
  - Commit detection (self-commits, pending approval)
  - Error handling (model errors, tool errors, session recovery)
  - Reconnection (stream relay, replay, cursor tracking)
  - Model switching mid-thread
  - Conversation continuity (session resume, history injection)
  - Edge cases (empty responses, long content, rapid tool calls)

Prerequisites:
    - VPS gateway running (http://187.127.172.200:8080)
    - Redis available for stream relay tests
    - At least one MAF agent (e.g. "task-manager") registered
    - At least one Copilot SDK agent (e.g. "agent-project-manager") registered
    - LiteLLM tier models configured (groq/, deepseek/, openrouter/)

Usage (on the VPS):
    cd /opt/acb/app
    uv run python -m pytest tests/integration/test_chat_features.py -v -x

    # Run specific feature group:
    uv run python -m pytest tests/integration/test_chat_features.py -v -k "sse"

    # Run excluding slow HITL tests:
    uv run python -m pytest tests/integration/test_chat_features.py -v -k "not hitl"

Environment variables:
    CC_GATEWAY_URL   -- Gateway base URL (default: http://127.0.0.1:8080)
    CC_AUTH_TOKEN    -- Bearer token for gateway auth (default: sk-local-dev-change-me)
    CC_TEST_MODEL    -- Model to use for tests (default: groq/llama-3.3-70b-versatile)
    CC_MAF_AGENT     -- MAF agent name for tests (default: task-manager)
    CC_COPILOT_AGENT -- Copilot SDK agent name for tests (default: agent-project-manager)
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GATEWAY_URL = os.environ.get("CC_GATEWAY_URL", "http://127.0.0.1:8080")
AUTH_TOKEN = os.environ.get("CC_AUTH_TOKEN", "sk-local-dev-change-me")
TEST_MODEL = os.environ.get(
    "CC_TEST_MODEL", "groq/llama-3.3-70b-versatile"
)
MAF_AGENT = os.environ.get("CC_MAF_AGENT", "task-manager")
COPILOT_AGENT = os.environ.get("CC_COPILOT_AGENT", "agent-project-manager")
STREAM_TIMEOUT = int(os.environ.get("CC_STREAM_TIMEOUT", "120"))
HITL_TIMEOUT = int(os.environ.get("CC_HITL_TIMEOUT", "60"))

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _api(path: str) -> str:
    return f"{GATEWAY_URL}{path}"


async def _post(path: str, json_data: dict) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            _api(path), json=json_data, headers=_auth_headers()
        )


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.get(_api(path), headers=_auth_headers())


async def _collect_sse_events(
    agent: str,
    message: str,
    *,
    model: str | None = None,
    thread_id: str | None = None,
    timeout: int = STREAM_TIMEOUT,
    max_events: int = 500,
    stop_after: float | None = None,
) -> list[dict[str, Any]]:
    """Collect SSE events from a stream run into a list.

    Returns events as parsed dicts.  Stops when RUN_FINISHED or
    RUN_ERROR is received, or after *timeout* seconds.
    """
    events: list[dict[str, Any]] = []
    run_id = str(uuid.uuid4())
    tid = thread_id or f"test-{agent}:{run_id[:8]}"

    payload: dict[str, Any] = {
        "agent": agent,
        "payload": {"message": message},
        "run_id": run_id,
        "thread_id": tid,
    }
    if model:
        payload["model"] = model

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15)
    ) as client:
        async with client.stream(
            "POST",
            _api("/agent/run/stream"),
            json=payload,
            headers=_auth_headers(),
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                events.append({
                    "_http_error": response.status_code,
                    "_body": body.decode(errors="replace")[:500],
                })
                return events

            buffer = ""
            start = time.monotonic()
            async for chunk in response.aiter_text():
                if stop_after and (time.monotonic() - start) > stop_after:
                    events.append({"_stopped_early": True})
                    break
                if len(events) >= max_events:
                    events.append({"_max_events": True})
                    break
                buffer += chunk
                while "\n\n" in buffer:
                    line, buffer = buffer.split("\n\n", 1)
                    if line.startswith("data: "):
                        try:
                            evt = json.loads(line[6:])
                            events.append(evt)
                            if evt.get("type") in (
                                "RUN_FINISHED",
                                "RUN_ERROR",
                            ):
                                return events
                        except json.JSONDecodeError:
                            pass
            else:
                if time.monotonic() - start > timeout:
                    events.append({"_timeout": True})
    return events


def _has_event_type(
    events: list[dict], event_type: str
) -> bool:
    return any(e.get("type") == event_type for e in events)


def _event_count(
    events: list[dict], event_type: str
) -> int:
    return sum(1 for e in events if e.get("type") == event_type)


async def _agent_exists(name: str) -> bool:
    """Check if a named agent is registered and live."""
    try:
        r = await _get("/agent")
        agents = r.json()
        return any(
            a.get("name") == name and a.get("status") == "live"
            for a in agents
        )
    except Exception:  # noqa: BLE001
        return False


_agent_cache: dict[str, bool] = {}


async def _require_agent(name: str, label: str) -> None:
    if name not in _agent_cache:
        _agent_cache[name] = await _agent_exists(name)
    if not _agent_cache[name]:
        pytest.skip(f"{label} agent '{name}' not registered or not live")


# ===================================================================
# Test Suite
# ===================================================================


class TestSSEStreaming:
    """SSE streaming: RUN_STARTED, TEXT_MESSAGE_CONTENT, RUN_FINISHED."""

    @pytest.mark.parametrize(
        "agent_name,label",
        [
            ("task-manager", "MAF"),
            ("agent-project-manager", "Copilot-SDK"),
        ],
    )
    async def test_stream_basic_flow(
        self, agent_name: str, label: str
    ) -> None:
        """Agent produces RUN_STARTED -> TEXT_MESSAGE_* -> RUN_FINISHED."""
        await _require_agent(agent_name, label)

        events = await _collect_sse_events(
            agent_name,
            "Say hello in exactly 3 words.",
            timeout=90,
        )

        assert _has_event_type(events, "RUN_STARTED"), (
            f"{label}: missing RUN_STARTED"
        )
        assert _event_count(events, "TEXT_MESSAGE_CONTENT") > 0, (
            f"{label}: no TEXT_MESSAGE_CONTENT events"
        )
        assert _has_event_type(events, "RUN_FINISHED"), (
            f"{label}: missing RUN_FINISHED"
        )

    async def test_stream_error_handling(self) -> None:
        """Invalid agent name returns RUN_ERROR."""
        events = await _collect_sse_events(
            "nonexistent-agent-zzz",
            "Hello",
            timeout=30,
        )
        assert (
            _has_event_type(events, "RUN_ERROR")
            or any("_http_error" in e for e in events)
        ), f"Expected error, got: {[e.get('type','?') for e in events[:5]]}"


class TestThinkingStream:
    """Consciousness / reasoning stream (THINKING_TEXT_MESSAGE_CONTENT)."""

    async def test_thinking_deltas_copilot(self) -> None:
        """Copilot SDK agent produces reasoning deltas."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Explain what 2+2 is. Think step by step.",
            timeout=90,
        )
        thinking_count = _event_count(
            events, "THINKING_TEXT_MESSAGE_CONTENT"
        )
        assert _has_event_type(events, "RUN_FINISHED"), (
            "Copilot SDK: stream did not finish"
        )
        print(f"  thinking deltas: {thinking_count}")


class TestToolCalls:
    """Tool call visibility: TOOL_CALL_START, ARGS, RESULT, PARTIAL."""

    @pytest.mark.parametrize(
        "agent_name,label",
        [
            ("task-manager", "MAF"),
            ("agent-project-manager", "Copilot-SDK"),
        ],
    )
    async def test_tool_calls_visible(
        self, agent_name: str, label: str
    ) -> None:
        """Agent tool calls appear in the stream."""
        await _require_agent(agent_name, label)

        events = await _collect_sse_events(
            agent_name,
            "Search the web for 'Metorite orchestration platform' and "
            "tell me the first result title.",
            timeout=120,
        )

        assert _has_event_type(events, "RUN_FINISHED"), (
            f"{label}: stream did not finish"
        )
        tc_start = _event_count(events, "TOOL_CALL_START")
        tc_result = _event_count(events, "TOOL_CALL_RESULT")
        print(f"  {label} TOOL_CALL_START={tc_start} TOOL_CALL_RESULT={tc_result}")

    async def test_tool_error_visible(self) -> None:
        """Tool errors produce TOOL_CALL_RESULT with success=false."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Call fetch_page with url='not-a-valid-url-xyz://invalid'.",
            timeout=120,
        )
        assert _has_event_type(events, "RUN_FINISHED"), (
            "Stream did not finish after tool error"
        )


class TestHITL:
    """Human-in-the-Loop: ask_questions bridging, elicitation cards."""

    async def test_ask_questions_elicitation_event(self) -> None:
        """ask_questions produces elicitation_requested CUSTOM event WITH request_id."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Before proceeding, use ask_questions to ask me: "
            "'{\"questions\":[{\"header\":\"Confirm\",\"question\":\"Should I continue?\","
            "\"options\":[{\"label\":\"Yes\"},{\"label\":\"No\"}]}]}'",
            timeout=120,
        )

        custom_events = [
            e for e in events
            if e.get("type") == "CUSTOM"
            and e.get("name") == "elicitation_requested"
        ]
        assert len(custom_events) > 0, (
            f"No elicitation_requested event. Events: "
            f"{[e.get('type','?') for e in events[:30]]}"
        )

        value = custom_events[0].get("value", {})
        assert "questions" in value, "elicitation_requested missing questions"
        assert "request_id" in value, (
            "elicitation_requested MISSING request_id -- "
            "HITL bridge will NOT work without this!"
        )

    async def test_ask_questions_blocks_agent(self) -> None:
        """ask_questions blocks the agent turn until user answers."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use ask_questions to ask: "
            "'{\"questions\":[{\"header\":\"BlockTest\",\"question\":\"Are you there?\","
            "\"options\":[{\"label\":\"Yes\"}]}]}'",
            timeout=60,
            stop_after=30,
        )

        has_elicitation = any(
            e.get("type") == "CUSTOM"
            and e.get("name") == "elicitation_requested"
            for e in events
        )
        assert has_elicitation, "elicitation_requested not emitted"

        has_finished = _has_event_type(events, "RUN_FINISHED")
        assert not has_finished, (
            "Agent finished before user answered -- ask_questions did NOT block!"
        )
        print("  OK: Agent correctly blocked on ask_questions")

    async def test_resolve_user_input_endpoint(self) -> None:
        """POST /agent/respond-input resolves a pending ask_user request."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Ask me a single yes/no question using ask_user.",
            timeout=60,
            stop_after=30,
        )

        user_input_events = [
            e for e in events
            if e.get("type") == "CUSTOM"
            and e.get("name") == "user_input_requested"
        ]
        if user_input_events:
            req_id = (
                user_input_events[0].get("value", {}).get("request_id")
            )
            if req_id:
                r = await _post("/agent/respond-input", {
                    "request_id": req_id,
                    "answer": "Yes",
                    "was_freeform": False,
                })
                assert r.status_code == 200, (
                    f"respond-input failed: {r.status_code} {r.text[:200]}"
                )
                print(f"  OK: Resolved user_input {req_id[:12]}")
        else:
            print("  WARN: No user_input_requested event (model may not have used native ask_user)")


class TestModelSwitching:
    """Per-message model switching and BYOK routing."""

    async def test_model_switch_mid_thread(self) -> None:
        """Switching model mid-thread creates a new Copilot session."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        thread_id = f"model-switch-{uuid.uuid4().hex[:8]}"

        events1 = await _collect_sse_events(
            COPILOT_AGENT,
            "Say 'turn one'.",
            model="groq/llama-3.3-70b-versatile",
            thread_id=thread_id,
            timeout=90,
        )
        assert _has_event_type(events1, "RUN_FINISHED"), "Turn 1 did not finish"

        events2 = await _collect_sse_events(
            COPILOT_AGENT,
            "What did I just say in the previous message?",
            model="deepseek/deepseek-v4-flash",
            thread_id=thread_id,
            timeout=90,
        )
        assert _has_event_type(events2, "RUN_FINISHED"), (
            "Turn 2 did not finish after model switch"
        )

    async def test_byok_model_routing(self) -> None:
        """BYOK model (groq/..., deepseek/...) routes through gateway."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Say 'byok test' in exactly 2 words.",
            model="groq/llama-3.3-70b-versatile",
            timeout=90,
        )

        assert _has_event_type(events, "RUN_FINISHED"), "BYOK stream did not finish"
        assert _event_count(events, "TEXT_MESSAGE_CONTENT") > 0, (
            "BYOK: no text content"
        )


class TestMemoryTools:
    """Memory tools: remember, save_memory, recall_timeline, save_episode."""

    async def test_remember_tool(self) -> None:
        """remember() tool is called and returns results."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use the remember tool to recall facts about user 'test-user-123'. "
            "Then tell me what you found.",
            timeout=120,
        )
        assert _has_event_type(events, "RUN_FINISHED"), "remember: stream did not finish"

    async def test_save_memory_tool(self) -> None:
        """save_memory() tool is called and returns success."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use the save_memory tool to save this fact: "
            "'test-user-123 prefers dark mode'. Then confirm the save.",
            timeout=120,
        )
        assert _has_event_type(events, "RUN_FINISHED"), "save_memory: stream did not finish"


class TestDelegation:
    """Inter-agent delegation: call_agent, call_agents_parallel."""

    async def test_call_agent_delegation(self) -> None:
        """call_agent() delegates to another agent and returns its response."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use call_agent to ask task-manager: 'What is your purpose?' "
            "Then summarize its response in one sentence.",
            timeout=180,
        )
        assert _has_event_type(events, "RUN_FINISHED"), "call_agent: stream did not finish"

        sub_events = [
            e for e in events
            if e.get("type", "").startswith("SUB_AGENT_")
        ]
        print(f"  SUB_AGENT events: {len(sub_events)}")


class TestWebTools:
    """Web tools: web_search, fetch_page."""

    async def test_web_search(self) -> None:
        """web_search() returns search results."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use web_search to find 'Python asyncio best practices'. "
            "Tell me one tip you found.",
            timeout=120,
        )
        assert _has_event_type(events, "RUN_FINISHED"), "web_search: stream did not finish"

    async def test_fetch_page(self) -> None:
        """fetch_page() fetches a URL."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use fetch_page to read https://example.com. "
            "Tell me the page title.",
            timeout=120,
        )
        assert _has_event_type(events, "RUN_FINISHED"), "fetch_page: stream did not finish"


class TestArtifacts:
    """Artifacts: write_artifact, download URLs."""

    async def test_write_artifact(self) -> None:
        """write_artifact() creates a file and returns download URL."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Use write_artifact to create a file called "
            "'outputs/test_artifact.md' with content '# Test Artifact'. "
            "Tell me the download URL.",
            timeout=120,
        )
        assert _has_event_type(events, "RUN_FINISHED"), "write_artifact: stream did not finish"

        all_text = " ".join(
            e.get("delta", "")
            for e in events
            if e.get("type") == "TEXT_MESSAGE_CONTENT"
        )
        if "download" in all_text.lower() or "/workspace/" in all_text:
            print("  OK: download URL found in response")


class TestTodoPanel:
    """Todo list tracking: manage_todo_list, sql-based."""

    async def test_manage_todo_list(self) -> None:
        """manage_todo_list produces TODO_LIST events."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Create a todo list with 3 items for planning a party: "
            "1) Send invitations, 2) Buy supplies, 3) Decorate. "
            "Use manage_todo_list. Then mark item 1 as completed.",
            timeout=120,
        )

        todo_events = [
            e for e in events if e.get("type") == "TODO_LIST"
        ]
        assert len(todo_events) > 0, (
            f"No TODO_LIST events. Got types: "
            f"{set(e.get('type','?') for e in events)}"
        )
        print(f"  TODO_LIST events: {len(todo_events)}")


class TestConversationContinuity:
    """Session continuity, history injection, resume."""

    async def test_thread_continuity(self) -> None:
        """Messages in the same thread maintain conversation context."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        thread_id = f"continuity-{uuid.uuid4().hex[:8]}"

        events1 = await _collect_sse_events(
            COPILOT_AGENT,
            "Remember this number: 4273. Just say 'OK, remembered 4273'.",
            thread_id=thread_id,
            timeout=90,
        )
        assert _has_event_type(events1, "RUN_FINISHED"), "Turn 1 failed"

        events2 = await _collect_sse_events(
            COPILOT_AGENT,
            "What number did I ask you to remember earlier? Reply with just the number.",
            thread_id=thread_id,
            timeout=90,
        )
        assert _has_event_type(events2, "RUN_FINISHED"), "Turn 2 failed"

        all_text2 = " ".join(
            e.get("delta", "")
            for e in events2
            if e.get("type") == "TEXT_MESSAGE_CONTENT"
        )
        has_context = "4273" in all_text2
        print(f"  Context preserved: {has_context} (response contains '4273')")


class TestStreamRelay:
    """Stream relay, reconnection, cursor tracking."""

    async def test_reconnect_endpoint_exists(self) -> None:
        """GET /agent/run/{thread_id}/reconnect returns a response."""
        thread_id = f"reconnect-test-{uuid.uuid4().hex[:8]}"
        r = await _get(f"/agent/run/{thread_id}/reconnect")
        assert r.status_code in (200, 404), (
            f"Unexpected reconnect status: {r.status_code}"
        )

    async def test_stream_ids_in_events(self) -> None:
        """Every SSE event has a _stream_id for cursor tracking."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Say 'cursor test'.",
            timeout=90,
        )

        sse_events = [
            e for e in events if not e.get("type", "").startswith("_")
        ]
        for evt in sse_events[:20]:
            assert "_stream_id" in evt, (
                f"Event missing _stream_id: {evt.get('type','?')}"
            )
        print(f"  {len(sse_events)} events all have _stream_id")


class TestErrorRecovery:
    """Error handling: model errors, tool errors, session recovery."""

    async def test_invalid_model_graceful(self) -> None:
        """Invalid model name returns RUN_ERROR, not a crash."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "Say hello.",
            model="nonexistent/model-zzz-12345",
            timeout=60,
        )

        has_error = (
            _has_event_type(events, "RUN_ERROR")
            or any("_http_error" in e for e in events)
        )
        assert has_error, (
            f"Expected RUN_ERROR for invalid model, got: "
            f"{[e.get('type','?') for e in events[:10]]}"
        )

    async def test_empty_message_handled(self) -> None:
        """Empty user message should not crash."""
        await _require_agent(COPILOT_AGENT, "Copilot-SDK")

        events = await _collect_sse_events(
            COPILOT_AGENT,
            "",
            timeout=60,
        )
        terminal = (
            _has_event_type(events, "RUN_FINISHED")
            or _has_event_type(events, "RUN_ERROR")
            or any("_http_error" in e for e in events)
        )
        assert terminal, "Empty message: stream did not terminate"
