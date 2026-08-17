"""One source of truth for model limits (context window / max output).

Before ``acb_llm.model_limits`` these numbers lived in five places that
disagreed — ``deepseek/deepseek-v4-pro`` alone had five context-window values
and four max-output values, and the answer depended on which file you asked.
The runtime asked litellm (stale), the Settings UI asked a curated table it
kept to itself, and ``ensure_model_registered`` invented numbers that the
runtime then read back as though they were litellm's.

These tests pin the resolution RULES, not the vendor numbers, so they don't
turn into the sixth stale table: where a real number is asserted it's one this
repo curates or one litellm ships, not a constant copied into the test.
"""
from __future__ import annotations

import pytest
from acb_llm.model_limits import (
    DEFAULT_CONTEXT_WINDOW,
    MODEL_CAPABILITIES,
    STUB_MARKER,
    get_limits,
)

# ---------------------------------------------------------------------------
# context_window: resolved CONSERVATIVELY (over-claiming is unrecoverable)
# ---------------------------------------------------------------------------

def test_context_window_takes_the_lowest_trusted_value() -> None:
    """Curated says deepseek-chat is 1M (V4-backed); litellm says 131K.

    Over-claiming a window is a hard provider rejection that
    acompletion_with_fallback answers by switching MODEL, and agent runs don't
    use that path at all — so where trusted sources disagree, the smallest wins.
    """
    limits = get_limits("deepseek/deepseek-chat")
    assert MODEL_CAPABILITIES["deepseek/deepseek-chat"]["context_window"] == 1_000_000
    assert limits.context_window == 131_072
    assert limits.context_source == "litellm"


def test_agreeing_sources_keep_the_full_window() -> None:
    """Conservative must not mean timid: when sources agree, use the number."""
    limits = get_limits("deepseek/deepseek-v4-pro")
    assert limits.context_window == 1_000_000


def test_curated_fills_gaps_litellm_does_not_know() -> None:
    limits = get_limits("openrouter/deepseek/deepseek-v4-pro")
    assert limits.context_window == 262_144
    assert limits.context_source == "curated"


# ---------------------------------------------------------------------------
# max_output: resolved by TRUST ORDER (under-claiming truncates tool calls)
# ---------------------------------------------------------------------------

def test_curated_max_output_beats_stale_litellm() -> None:
    """The bug this whole workstream came from.

    At authoring, litellm claimed deepseek-v4-pro maxes at 8192 output tokens
    while the live model emitted 10940 in one completion — provably wrong, and
    believing it truncated a tool call's JSON arguments mid-string so the
    agent produced no text at all. A number we maintain outranks litellm's.

    The pinned value below is a TRIPWIRE, not a constant: it holds litellm's
    current claim so the next upstream drift fails here and forces a human
    re-check. History: 8192 at authoring; 393216 since the 2026-08-17 upstream
    data refresh. Re-checked 2026-08-17: curated (384_000) still wins by trust
    order, and as the SMALLER claim it errs in this module's intended
    direction — under-claiming trims a little sooner, over-claiming makes the
    provider hard-reject the request.
    """
    from litellm import model_cost

    stale = model_cost["deepseek/deepseek-v4-pro"]["max_output_tokens"]
    assert stale == 393_216, "litellm changed; re-check whether curated still wins"

    limits = get_limits("deepseek/deepseek-v4-pro")
    assert limits.max_output > 10_940, "must exceed what the model provably emits"
    assert limits.max_output_source == "curated"


def test_litellm_max_output_used_when_uncurated() -> None:
    limits = get_limits("openai/gpt-4o")
    assert limits.max_output > 0


# ---------------------------------------------------------------------------
# The stub must not launder a guess into a fact
# ---------------------------------------------------------------------------

def test_dynamic_registration_stub_is_not_believed() -> None:
    """``ensure_model_registered`` writes a minimal entry into litellm's registry
    so an unknown model can still be ROUTED. Its token counts are invented
    (262144/32768). ``context_window_for`` used to read them straight back and
    report our own guess as litellm's answer — and it outranked the real
    fallback table. Keep the routing, ignore the numbers.
    """
    from acb_llm.client import ensure_model_registered
    from litellm import model_cost

    model = "deepseek/some-unreleased-model-xyz"
    assert ensure_model_registered(model) == "deepseek", "routing must still work"
    assert model_cost[model][STUB_MARKER] is True
    assert model_cost[model]["max_input_tokens"] == 262144, "stub still well-formed"

    limits = get_limits(model)
    assert limits.context_window == DEFAULT_CONTEXT_WINDOW
    assert limits.context_source == "default", "the stub's 262144 must not win"


def test_unknown_model_gets_the_default_not_a_starved_window() -> None:
    """The old default was 32768 — a quarter of a current model's real window,
    applied silently to anything litellm didn't recognise."""
    limits = get_limits("mystery/model-9000")
    assert limits.context_window == DEFAULT_CONTEXT_WINDOW == 128_000
    assert limits.context_source == "default"


def test_empty_model_is_safe() -> None:
    limits = get_limits("")
    assert limits.context_window > 0 and limits.max_output > 0


# ---------------------------------------------------------------------------
# Tier aliases track the LIVE mapping (no pinned copies)
# ---------------------------------------------------------------------------

def test_tier_alias_follows_the_live_tier_mapping(monkeypatch) -> None:
    """A pinned tier→window table is how settings.py drifted: it claimed
    tier-balanced was 1M while overrides had re-pointed it at a 131K model."""
    from acb_llm import client

    monkeypatch.setitem(client._TIER_MODEL, "tier2", "deepseek/deepseek-v4-pro")
    assert get_limits("tier-balanced").context_window == 1_000_000

    monkeypatch.setitem(client._TIER_MODEL, "tier2", "openai/gpt-4o")
    assert get_limits("tier-balanced").context_window == 128_000


# ---------------------------------------------------------------------------
# Env override — a provider raising a limit shouldn't need a deploy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,attr,source_attr", [
    ("MAX_OUTPUT", "max_output", "max_output_source"),
    ("CONTEXT_WINDOW", "context_window", "context_source"),
])
def test_env_override_wins(monkeypatch, field: str, attr: str,
                           source_attr: str) -> None:
    monkeypatch.setenv(f"ACB_LIMITS__DEEPSEEK_DEEPSEEK_V4_PRO__{field}", "77000")
    limits = get_limits("deepseek/deepseek-v4-pro")
    assert getattr(limits, attr) == 77_000
    assert getattr(limits, source_attr) == "env"


def test_malformed_env_override_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("ACB_LIMITS__DEEPSEEK_DEEPSEEK_V4_PRO__MAX_OUTPUT", "banana")
    assert get_limits("deepseek/deepseek-v4-pro").max_output_source == "curated"


# ---------------------------------------------------------------------------
# The de-duplication itself
# ---------------------------------------------------------------------------

def test_settings_ui_and_runtime_read_the_same_table() -> None:
    """The root cause: the curated table was unreachable from the runtime, so
    the UI and the prompt budgeter answered differently for the same model."""
    from acb_llm.model_limits import FALLBACK_CONTEXT_WINDOWS
    from gateway.routes.settings import _MODEL_CAPABILITIES, _TIER_CONTEXT_WINDOWS

    assert _MODEL_CAPABILITIES is MODEL_CAPABILITIES
    assert _TIER_CONTEXT_WINDOWS is FALLBACK_CONTEXT_WINDOWS


def test_tier_aliases_are_not_pinned_in_the_fallback_table() -> None:
    """They must resolve through the live tier mapping instead — a pinned copy
    silently overwrote the correct dynamic value in the Settings endpoint."""
    from acb_llm.model_limits import FALLBACK_CONTEXT_WINDOWS

    for alias in ("tier-fast", "tier-balanced", "tier-powerful"):
        assert alias not in FALLBACK_CONTEXT_WINDOWS
