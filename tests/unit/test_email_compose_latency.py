"""The manual "Draft with AI" path is latency-sensitive — the user is watching
a spinner. These pin the plumbing decisions that keep it fast, on the source:

  * memory recall and the consult qualifier run CONCURRENTLY (asyncio.gather),
    so qualifying the email costs no wall-clock unless it triggers a consult;
  * the post-draft Mem0 write is fire-and-forget (_spawn_background) — awaiting
    it made the user wait through an LLM extraction pass AFTER the draft was
    already written;
  * the interactive endpoints (/compose-assist, /draft-reply) run on the
    account's COMPOSE model (fast by default), never the background draft
    model (tier-powerful → a reasoning model → 20-60s per click).
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

from gateway.routes.email.automation import drafting


def test_memory_and_consult_qualifier_run_concurrently() -> None:
    src = inspect.getsource(drafting._orchestrate_draft)
    assert "asyncio.gather(" in src and "_draft_consult_plan" in src, (
        "memory recall and the consult qualifier are independent — they must "
        "run concurrently, not as serial awaits")


def test_mem0_write_is_fire_and_forget() -> None:
    src = inspect.getsource(drafting._orchestrate_draft)
    assert "_spawn_background(" in src, (
        "the post-draft Mem0 record must be fire-and-forget")
    assert "await add_memories_background" not in src, (
        "awaiting the Mem0 add blocks the response on an LLM extraction pass "
        "after the draft is already written")


def test_interactive_drafting_uses_the_compose_tier() -> None:
    # Manual "Draft with AI" (both entry points) → the COMPOSE tier, which our
    # code chooses (D-EM-61). Background paths (rule actions, follow-ups) use
    # the DRAFT tier. test_email_no_tier_choice.py proves the values.
    assert 'EMAIL_TASK_TIERS["compose"]' in inspect.getsource(
        drafting._compose_assist_run)
    assert 'EMAIL_TASK_TIERS["compose"]' in inspect.getsource(
        drafting.draft_reply_smart)


def test_stream_endpoint_shares_the_compose_pipeline() -> None:
    # The SSE endpoint must stay a thin event wrapper over the SAME
    # implementation as the JSON endpoint — never a second drafting path.
    assert "_compose_assist_run" in inspect.getsource(drafting.compose_assist)
    assert "_compose_assist_run" in inspect.getsource(
        drafting.compose_assist_stream)


async def test_activity_events_carry_what_the_panel_renders() -> None:
    """The composer's activity panel renders these events directly, so the
    orchestrator must emit the consult AGENT + the QUESTION it asked (not just
    "consulting…"), and the qualifier's verdict — that visibility is the
    feature, not a log line."""
    events: list[dict] = []

    async def capture(evt: dict) -> None:
        events.append(evt)

    async def fake_draft(*_a, **_kw) -> str:
        return "Hi,\n\nDone."

    async def fake_plan(_email: dict, on_activity=None, **_kw) -> list:
        await drafting._emit_activity(
            on_activity, kind="qualify", emailKind="project/delivery",
            consults=1, detail="needs task-manager")
        return [{"agent": "task-manager", "question": "What is the status?"}]

    async def fake_run_agent(_agent: str, _payload: dict) -> dict:
        return {"answer": "Dispatching 29 July."}

    import sys
    import types
    mod = types.ModuleType("orchestrator.executor")
    mod.run_agent = fake_run_agent  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"orchestrator.executor": mod}), \
            patch.object(drafting, "_llm_draft_reply", fake_draft), \
            patch.object(drafting, "_draft_consult_plan", fake_plan):
        await drafting._orchestrate_draft(
            {"from": "a@b.com", "subject": "s", "body": "b"},
            about="", signature="", user_email="me@x.com",
            on_activity=capture)

    kinds = [e.get("kind") for e in events]
    assert "qualify" in kinds, (
        "the panel must show WHY a specialist was (or wasn't) called")
    consults = [e for e in events if e.get("kind") == "consult"]
    assert consults, "a specialist consult must surface as an activity event"
    assert consults[0]["agent"] == "task-manager"
    assert consults[0]["question"] == "What is the status?", (
        "the panel shows the actual question put to the agent")
    assert any(c.get("status") == "done" for c in consults), (
        "the consult must resolve, so the panel doesn't spin forever")
    # Stage events bracket the run so the panel can show ordered progress.
    assert {"memory", "draft"} <= {
        e.get("id") for e in events if e.get("kind") == "stage"}
