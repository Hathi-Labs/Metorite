"""AO-3: the AI cannot answer, and the chat says so (incident 2026-11-17).

The Anthropic account ran out of credits, and every Router call to it got a
400. The Console answered each chat stream with a 200 and a lone ``[DONE]``.
The gateway relayed it, the agent read an empty stream as a finished turn,
and the fold saved a reply with no text. The member saw an empty bubble.

The rules, each with the mutation it was run against:

1. **The gateway reads no answer as an outage.** A 200 with no bytes, or with
   the old lone ``[DONE]``, becomes the 502 of a Router outage
   (``test_v1_router_streaming.py`` holds the hop itself).
2. **One typed failure.** A model call that the Router answers with 502 or
   503 is the run error ``ai_unavailable``. A refused connection stays
   ``connection``.
3. **The turn ends at Tier 1.** Tier 2 would ask the same Router again.
4. **No empty reply is saved.** A label alone (``ai.route``) is no answer, in
   the fold and in the browser's checkpoint. The member's own turn is the
   run route's write, which this does not touch.
5. **WhatsApp still holds the message** for the new code (WAC-13).

The Console half is in ``test_customer_console_router.py``, and the card is
``components/aiRestingCard.test.ts``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from acb_auth import console_resolve
from acb_common.settings import get_settings
from acb_llm.run_errors import RUN_ERROR_CODES, classify_run_error, classify_status
from agent_framework.exceptions import ChatClientException
from gateway.chat_fold import LABEL_EVENTS, fold_run_events
from gateway.routes import v1_compat

from tests.unit.test_profile_used_event import (
    _StubAgent,
    _text,
    stream,  # noqa: F401 — a fixture, used by name
)

REPO = Path(__file__).resolve().parents[2]
_REQ = httpx.Request("POST", "http://127.0.0.1:8000/v1/chat/completions")


def _router_says(status: int) -> ChatClientException:
    """What the agent framework raises when our Router door answers *status*:
    the SDK's status error, wrapped as the member once saw it."""
    inner = openai.APIStatusError(
        f"Error code: {status}",
        response=httpx.Response(status, request=_REQ),
        body={"error": {"message": "the AI Router is unavailable",
                        "type": "ConsoleRouterUnavailable", "code": status}},
    )
    try:
        try:
            raise inner
        except openai.APIStatusError as exc:
            raise ChatClientException(
                f"service failed to complete the prompt: {exc}",
                inner_exception=exc,  # type: ignore[arg-type]
            ) from exc
    except ChatClientException as outer:
        return outer


class _CountingAgent(_StubAgent):
    """The stub agent, counting its model calls: a stream, or a batch run."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.calls: list[str] = []

    def run(self, *a: Any, **k: Any) -> Any:
        self.calls.append("stream" if k.get("stream") else "batch")
        return super().run(*a, **k)


# ── rule 2: one typed failure ──────────────────────────────────────────────


def test_the_code_is_in_the_vocabulary_and_the_browser_holds_its_words() -> None:
    assert "ai_unavailable" in RUN_ERROR_CODES
    ts = (REPO / "workbench/control_plane/src/lib/runErrors.ts").read_text(encoding="utf-8")
    assert '| "ai_unavailable"' in ts
    assert "\n  ai_unavailable: {" in ts.replace("\r\n", "\n")


@pytest.mark.parametrize("status", [502, 503])
def test_a_router_502_or_503_is_ai_unavailable(status: int) -> None:
    """Mutation: map 502 and 503 back to ``connection``, and this fails."""
    assert classify_status(status) == "ai_unavailable"
    assert classify_run_error(_router_says(status)) == "ai_unavailable"


def test_a_refused_connection_is_still_connection() -> None:
    """Nothing listened (a restart). That is the app, not the AI."""
    assert classify_run_error(openai.APIConnectionError(request=_REQ)) == "connection"


async def _console_answers(monkeypatch, *, status: int, frames: list[bytes]) -> Any:
    """The gateway's stream hop over a fake Console, as the agent's call meets it."""
    monkeypatch.setenv("ROUTER_SERVING_ENABLED", "1")
    monkeypatch.setenv("CUSTOMER_CONSOLE_URL", "https://console.invalid")
    monkeypatch.setenv("CUSTOMER_CONSOLE_ORG_KEY", "cc_live_ao3fixture_notarealsecret")
    get_settings.cache_clear()

    def handle(_request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"detail": {"reason": "unavailable"}})
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames)),
                              headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(
        console_resolve, "_new_http_client",
        lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    class _Req:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

    try:
        return await v1_compat._serve_via_router_stream(
            _Req(), {"model": "tier-balanced", "stream": True,
                     "messages": [{"role": "user", "content": "hi"}]})
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(("status", "frames"), [
    (503, []),                      # the Console since AO-3
    (200, [b"data: [DONE]\n\n"]),   # a Console from before AO-3
    (200, []),                      # a 200 with nothing at all
], ids=["console-503", "old-lone-done", "empty-200"])
def test_a_stream_that_no_step_opened_reaches_the_agent_as_ai_unavailable(
        monkeypatch, status: int, frames: list[bytes]) -> None:
    """From the fake Console to the code the chat draws. Mutation: relay the
    lone ``[DONE]`` as a 200, and the old-Console case gets no status."""
    resp = asyncio.run(_console_answers(monkeypatch, status=status, frames=frames))
    assert resp.status_code == 502
    assert resp.media_type != "text/event-stream"
    assert classify_status(resp.status_code) == "ai_unavailable"


# ── rule 3: the turn ends at Tier 1, and no row is saved ───────────────────


@pytest.mark.parametrize("status", [502, 503])
def test_the_turn_ends_with_ai_unavailable_and_saves_no_reply(stream, status: int) -> None:  # noqa: F811
    """Mutation: drop the ``ai_unavailable`` arm of the executor, and Tier 2
    runs: the batch call answers, and the error never reaches the chat."""
    agent = _CountingAgent([], fault=_router_says(status), batch="An answer Tier 2 must not give.")
    events = stream(agent)

    errors = [e for e in events if e.get("type") == "RUN_ERROR"]
    assert [e["code"] for e in errors] == ["ai_unavailable"]
    assert events[-1]["type"] == "RUN_ERROR"
    assert agent.calls == ["stream"], "Tier 2 asked the same Router again"
    assert not [e for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT"]
    # No reply row: the fold of the run gives nothing to save.
    assert fold_run_events(events) is None


def test_another_fault_before_output_still_falls_back_to_tier_2(stream) -> None:  # noqa: F811
    """The new arm is narrow: a fault that is not the Router's keeps today's
    fallback."""
    agent = _CountingAgent([], fault=RuntimeError("stream refused"), batch="The answer.")
    events = stream(agent)
    assert agent.calls == ["stream", "batch"]
    assert events[-1]["type"] == "RUN_FINISHED"
    assert (fold_run_events(events) or {})["content"] == "The answer."


def test_a_fault_after_output_keeps_the_answer_so_far(stream) -> None:  # noqa: F811
    agent = _CountingAgent([_text("Half an ")], fault=_router_says(502))
    events = stream(agent)
    assert events[-1]["type"] == "RUN_ERROR"
    assert events[-1]["code"] == "ai_unavailable"
    assert (fold_run_events(events) or {})["content"] == "Half an "


# ── rule 4: a label alone is no answer ─────────────────────────────────────

ROUTE = {"type": "CUSTOM", "name": "ai.route", "value": {"tier": "tier-balanced"}}


@pytest.mark.parametrize("end", ["RUN_FINISHED", "RUN_ERROR"])
def test_a_run_of_labels_alone_saves_no_row(end: str) -> None:
    """The incident's own fold: ``chat_fold.persisted chars=0 tools=0``.
    Mutation: count ``custom_events`` whole again, and this fails."""
    events = [{"type": "RUN_STARTED", "runId": "r1"}, dict(ROUTE), dict(ROUTE), {"type": end}]
    assert fold_run_events(events) is None


def test_a_label_stays_on_a_row_that_holds_an_answer() -> None:
    folded = fold_run_events([
        {"type": "RUN_STARTED", "runId": "r1"}, dict(ROUTE),
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "Done."}, {"type": "RUN_FINISHED"},
    ]) or {}
    assert folded["content"] == "Done."
    assert [e["name"] for e in folded["custom_events"]] == ["ai.route"]


def test_a_card_alone_still_saves_its_row() -> None:
    """A card that is not a label is an answer, as before."""
    folded = fold_run_events([
        {"type": "RUN_STARTED"},
        {"type": "CUSTOM", "name": "generative_ui", "value": {"component": "x"}},
        {"type": "RUN_ERROR"},
    ])
    assert folded is not None


def test_the_label_list_names_the_route_event_and_the_browser_agrees() -> None:
    from acb_skills.tier_policy import ROUTE_EVENT

    assert frozenset({ROUTE_EVENT}) == LABEL_EVENTS
    ts = (REPO / "workbench/control_plane/src/lib/assistantCheckpoint.ts").read_text(encoding="utf-8")
    assert "LABEL_EVENTS: ReadonlySet<string> = new Set([ROUTE_EVENT])" in ts
    assert "(c.customEvents ?? []).every((e) => LABEL_EVENTS.has(e.name))" in ts


# ── rule 5: WhatsApp holds the message for the new code too ────────────────


def test_whatsapp_reads_ai_unavailable_as_the_ai_path_down() -> None:
    """Mutation: drop ``ai_unavailable`` from ``UNAVAILABLE_CODES``, and a
    Router 502 sends the plain failure, not the hold (WAC-13)."""
    from gateway.routes.whatsapp_channel import bot_run

    assert "ai_unavailable" in bot_run.UNAVAILABLE_CODES
    assert bot_run._ai_path_down("ai_unavailable", _router_says(502), run_started=0.0)
