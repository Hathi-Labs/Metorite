"""The tier follows the size of the work (owner, 2026-10-22).

"Use tier fast or balanced for regular things, and switch to powerful when
there's a lot of context or a long thread." Three parts:

1. **The chat turn.** Under the tier policy, a turn that starts with
   :data:`CONTEXT_POWERFUL_TOKENS` or more of history and context takes
   ``tier-powerful`` (reason ``context``). The size is known before the first
   request, so the turn keeps one model (2026-10-09). Mutation: drop the
   ``context_tokens`` branch of ``choose`` -> ``test_a_long_turn_goes_powerful``.
2. **A delegated run** never goes below its own agent's default. The email
   assistant's default is ``tier-powerful``. Mutation: return the parent's
   tier in ``_sub_agent_tier`` -> ``test_a_delegated_run_keeps_its_own_floor``.
3. **The email drafter** raises its tier by the size of the mail, and the
   window check still wins. Mutation: drop ``tier_for_size`` in
   ``_llm_draft_reply`` -> ``test_a_long_thread_drafts_on_powerful``.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from acb_skills import tier_policy as tp
from gateway.routes.email.automation import draft_budget as db
from gateway.routes.email.automation import drafting as dr

# ── 1. The chat turn ─────────────────────────────────────────────────────────


def test_a_long_turn_goes_powerful() -> None:
    c = tp.choose(default=tp.TIER_BALANCED, kind="chat", effort="auto",
                  context_tokens=tp.CONTEXT_POWERFUL_TOKENS)
    assert (c.tier, c.reason) == (tp.TIER_POWERFUL, "context")


def test_a_short_turn_keeps_the_default() -> None:
    c = tp.choose(default=tp.TIER_BALANCED, kind="chat", effort="auto",
                  context_tokens=tp.CONTEXT_POWERFUL_TOKENS - 1)
    assert (c.tier, c.reason) == (tp.TIER_BALANCED, "default")


def test_a_long_turn_never_moves_an_admin_model() -> None:
    c = tp.choose(default="openai/gpt-4o", kind="chat", effort="auto",
                  context_tokens=10 * tp.CONTEXT_POWERFUL_TOKENS)
    assert c.tier == "openai/gpt-4o"


def test_the_turn_size_counts_the_history() -> None:
    history = [{"role": "user", "content": "x" * 30_000},
               {"role": "assistant", "content": "y" * 45_000}]
    assert tp.turn_tokens("hi", history, "") == (30_000 + 45_000 + 2) // 3
    assert tp.turn_tokens(None, None, None) == 0


def test_the_policy_carries_the_size_to_every_request() -> None:
    run = tp.RunTierPolicy(agent="orchestrator", run_id="r", default=tp.TIER_BALANCED,
                           kind="chat", effort="auto",
                           context_tokens=tp.CONTEXT_POWERFUL_TOKENS + 5)
    assert run.run_tier() == tp.TIER_POWERFUL


def test_a_judgement_reply_is_named_analysis() -> None:
    """The decision step reads "compare offers" and "a reply that needs
    judgement" as analysis, which takes the powerful tier."""
    assert "comparison of options or offers" in tp.TURN_QUESTION
    assert "reply or a document that needs judgement" in tp.TURN_QUESTION
    assert tp.KIND_TIERS["analysis"] == tp.TIER_POWERFUL


async def test_the_executor_measures_the_turn(monkeypatch) -> None:
    from orchestrator import executor as ex

    async def kind(message, tools, effort, **kw):
        return tp.TurnKind("chat", "short")

    monkeypatch.setattr(ex, "_tier_policy_covers", lambda name: True)
    monkeypatch.setattr(tp, "turn_kind", kind)
    agent = SimpleNamespace(client=SimpleNamespace(model=tp.TIER_BALANCED),
                            default_options={"tools": []})
    run = await ex._tier_policy_for_run(
        "orchestrator", agent, run_id="r", thread_id=None, model=None,
        think_mode="auto",
        event_payload={"message": "reply to Ravi",
                       "messages": [{"role": "user", "content": "z" * 90_000}]},
        config={}, agent_md_model="", is_copilot=False, emit=False)
    assert run.context_tokens >= tp.CONTEXT_POWERFUL_TOKENS
    assert run.run_tier() == tp.TIER_POWERFUL


# ── 2. A delegated run ───────────────────────────────────────────────────────


def _agent(model: str) -> Any:
    return SimpleNamespace(client=SimpleNamespace(model=model))


@pytest.mark.parametrize(("parent", "own", "want"), [
    ("tier-balanced", "tier-powerful", "tier-powerful"),
    ("tier-powerful", "tier-balanced", "tier-powerful"),
    ("tier-fast", "tier-balanced", "tier-balanced"),
    (None, "tier-powerful", None),
    ("openai/gpt-4o", "tier-powerful", "openai/gpt-4o"),
])
def test_a_delegated_run_keeps_its_own_floor(parent, own, want) -> None:
    from orchestrator import executor as ex

    assert ex._sub_agent_tier(parent, _agent(own), {}, "", is_copilot=False) == want


def test_the_email_assistant_defaults_to_powerful() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "apps" / "agents"
           / "agent-email-assistant" / "agents.py").read_text(encoding="utf-8")
    assert 'os.environ.get("EMAIL_AGENT_MODEL", "tier-powerful")' in src


# ── 3. The email drafter ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("model", "chars", "files", "want"), [
    ("tier-fast", 2_000, False, "tier-fast"),
    ("tier-fast", 2_000, True, "tier-balanced"),
    ("tier-fast", db.BALANCED_CHARS, False, "tier-balanced"),
    ("tier-fast", db.POWERFUL_CHARS, False, "tier-powerful"),
    ("tier-powerful", 10, False, "tier-powerful"),
    ("openai/gpt-4o", db.POWERFUL_CHARS, True, "openai/gpt-4o"),
])
def test_the_draft_tier_follows_the_size(model, chars, files, want) -> None:
    assert db.tier_for_size(model, chars, has_files=files) == want


def _resp(content: str) -> Any:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def test_a_waited_for_draft_stops_at_balanced() -> None:
    """tier-powerful can be a reasoning model of 20 to 60 s, and the reply card
    waits inside a 60 s call (review of #1219, P2)."""
    assert db.tier_for_size("tier-fast", db.POWERFUL_CHARS,
                            ceiling=db.INTERACTIVE_CEILING) == "tier-balanced"
    assert db.tier_for_size("tier-powerful", 10,
                            ceiling=db.INTERACTIVE_CEILING) == "tier-powerful"


@pytest.mark.parametrize(("thread", "interactive", "want"), [
    ("", False, "tier-fast"),
    ("Earlier mail. " * 6000, False, "tier-powerful"),
    ("Earlier mail. " * 6000, True, "tier-balanced"),
])
async def test_a_long_thread_drafts_on_powerful(monkeypatch, thread, interactive, want) -> None:
    seen: list[str] = []

    async def fake(*, model, messages, **kw):
        seen.append(model)
        return _resp("Dear Ravi,\n\nThanks."), model

    monkeypatch.setattr("acb_llm.context.acompletion_with_fallback", fake)
    monkeypatch.setattr(db, "_window_of", lambda m: 1_000_000)
    email = {"from": "ravi@navy.test", "from_name": "Ravi", "subject": "BQ",
             "body": "Please quote.", "thread": thread}
    await dr._llm_draft_reply(email, about="", signature="", model="tier-fast",
                              interactive_fallback=interactive)
    assert seen == [want]


async def test_the_turn_counts_the_system_and_memory_context(monkeypatch) -> None:
    """The BFF sends ``system_context`` and the gateway ``memory_context``
    (review of #1219, P2). Both count toward the size of the turn."""
    from orchestrator import executor as ex

    async def kind(message, tools, effort, **kw):
        return tp.TurnKind("chat", "short")

    monkeypatch.setattr(ex, "_tier_policy_covers", lambda name: True)
    monkeypatch.setattr(tp, "turn_kind", kind)
    agent = SimpleNamespace(client=SimpleNamespace(model=tp.TIER_BALANCED),
                            default_options={"tools": []})
    run = await ex._tier_policy_for_run(
        "orchestrator", agent, run_id="r", thread_id=None, model=None,
        think_mode="auto",
        event_payload={"message": "hi", "messages": [],
                       "system_context": "s" * 40_000, "memory_context": "m" * 40_000},
        config={}, agent_md_model="", is_copilot=False, emit=False)
    assert run.context_tokens >= tp.CONTEXT_POWERFUL_TOKENS
