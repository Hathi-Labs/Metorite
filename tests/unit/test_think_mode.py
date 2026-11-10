"""Reasoning-depth (``think_mode``) on the named-agent path.

Spec: ``project-docs/specs/agent_architecture.md`` §11.1.1.

``think_mode`` was honoured only on ``/copilot/chat``, so the chat UI's thinking
toggle silently did nothing for every named agent — even though ``route.ts`` has
been sending it inside ``payload`` the whole time.  Porting it is the
prerequisite for retiring that endpoint.

Two things are asserted here:

1. The mapping lives in ONE place.  ``gateway.main._apply_thinking_mode`` now
   delegates to ``orchestrator._model_resolution``; a second copy is exactly how
   the two run paths' memory injection diverged (§11.1.2).
2. It reaches whichever options dict the agent actually uses — Copilot-SDK
   agents carry ``_default_options``, native MAF agents carry
   ``default_options``.
3. A native MAF agent gets ``reasoning_effort`` and NOTHING else. Its client
   passes every key to ``AsyncCompletions.create()``, which refuses
   ``model_params`` and ``thinking``. These fakes cannot see that refusal, so
   ``test_native_maf_wire.py`` drives a real client through the real executor.
"""
from __future__ import annotations

import pytest

_mr = pytest.importorskip(
    "orchestrator._model_resolution",
    reason="orchestrator not installed in this environment",
)
_apply_thinking_mode = _mr._apply_thinking_mode
_apply_thinking_mode_for_agent = _mr._apply_thinking_mode_for_agent


class _MafAgent:
    """Native MAF agents expose a public ``default_options``."""

    def __init__(self) -> None:
        self.default_options: dict = {}


class _CopilotAgent:
    """Copilot-SDK agents expose a private ``_default_options``."""

    def __init__(self) -> None:
        self._default_options: dict = {}


# ---------------------------------------------------------------------------
# The mapping
# ---------------------------------------------------------------------------

def test_auto_is_a_no_op() -> None:
    opts: dict = {}
    _apply_thinking_mode(opts, "auto")
    assert opts == {}


def test_unknown_mode_is_a_no_op() -> None:
    opts: dict = {}
    _apply_thinking_mode(opts, "turbo")
    assert opts == {}


def test_thinking_sets_medium_effort_and_a_budget() -> None:
    opts: dict = {}
    _apply_thinking_mode(opts, "thinking")
    assert opts["model_params"]["reasoning_effort"] == "medium"
    assert opts["thinking"] == {"type": "enabled", "budget_tokens": 4000}


def test_max_sets_high_effort_and_a_bigger_budget() -> None:
    opts: dict = {}
    _apply_thinking_mode(opts, "max")
    assert opts["model_params"]["reasoning_effort"] == "high"
    assert opts["thinking"]["budget_tokens"] == 16000
    assert opts["thinking"]["budget_tokens"] > 4000


def test_existing_model_params_are_preserved() -> None:
    """The agent may already carry model params — don't clobber them."""
    opts: dict = {"model_params": {"temperature": 0.2}, "model": "tier-fast"}
    _apply_thinking_mode(opts, "thinking")
    assert opts["model_params"]["temperature"] == 0.2
    assert opts["model_params"]["reasoning_effort"] == "medium"
    assert opts["model"] == "tier-fast"


# ---------------------------------------------------------------------------
# Reaching the right options dict
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("mode", "effort"), [("thinking", "medium"), ("max", "high")])
def test_a_native_maf_agent_gets_reasoning_effort_and_nothing_else(
    mode: str, effort: str,
) -> None:
    """PR #585 review: ``model_params`` and ``thinking`` in a native agent's
    ``default_options`` end every turn in RUN_ERROR. Same values as Tier 1.5."""
    agent = _MafAgent()
    assert _apply_thinking_mode_for_agent(agent, mode) is True
    assert agent.default_options == {"reasoning_effort": effort}


def test_an_unknown_mode_leaves_a_native_agent_untouched() -> None:
    agent = _MafAgent()
    assert _apply_thinking_mode_for_agent(agent, "turbo") is False
    assert agent.default_options == {}


def test_the_native_map_matches_the_copilot_tier_1_5_values() -> None:
    assert _mr.NATIVE_REASONING_EFFORT == {"thinking": "medium", "max": "high"}


def test_the_copilot_chat_path_never_hands_a_native_agent_model_params() -> None:
    """``/copilot/chat`` runs the native MAF orchestrator. It must go through
    ``_apply_thinking_mode_for_agent``, never the Copilot-only mapping."""
    import ast
    from pathlib import Path

    main_py = (
        Path(__file__).resolve().parents[2]
        / "apps/services/gateway/gateway/main.py"
    )
    calls = [
        node for node in ast.walk(ast.parse(main_py.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "_apply_thinking_mode"
    ]
    assert calls == [], "gateway/main.py calls the Copilot-only mapping on an agent"


def test_applies_to_a_copilot_sdk_agent() -> None:
    agent = _CopilotAgent()
    assert _apply_thinking_mode_for_agent(agent, "thinking") is True
    assert agent._default_options["thinking"]["budget_tokens"] == 4000


def test_auto_leaves_the_agent_untouched() -> None:
    """The default path must be byte-identical to before this existed."""
    for agent, attr in ((_MafAgent(), "default_options"), (_CopilotAgent(), "_default_options")):
        assert _apply_thinking_mode_for_agent(agent, "auto") is False
        assert getattr(agent, attr) == {}


def test_empty_mode_leaves_the_agent_untouched() -> None:
    agent = _MafAgent()
    assert _apply_thinking_mode_for_agent(agent, "") is False
    assert agent.default_options == {}


def test_agent_without_options_is_skipped_not_raised() -> None:
    class _Bare:
        pass

    assert _apply_thinking_mode_for_agent(_Bare(), "max") is False


def test_private_options_win_when_both_are_present() -> None:
    """Mirrors ``_apply_model_for_maf_agent``, which treats the presence of
    ``_default_options`` as "this is a Copilot-SDK agent"."""

    class _Both:
        def __init__(self) -> None:
            self._default_options: dict = {}
            self.default_options: dict = {}

    agent = _Both()
    _apply_thinking_mode_for_agent(agent, "max")
    assert agent._default_options != {}
    assert agent.default_options == {}


# ---------------------------------------------------------------------------
# One implementation, not two
# ---------------------------------------------------------------------------

def test_gateway_delegates_to_the_shared_implementation() -> None:
    gateway_main = pytest.importorskip(
        "gateway.main", reason="gateway not installed in this environment",
    )
    gateway_opts: dict = {}
    shared_opts: dict = {}
    gateway_main._apply_thinking_mode(gateway_opts, "max")
    _apply_thinking_mode(shared_opts, "max")
    assert gateway_opts == shared_opts


# ---------------------------------------------------------------------------
# The request field
# ---------------------------------------------------------------------------

def test_request_model_defaults_to_auto() -> None:
    routes_agent = pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed in this environment",
    )
    req = routes_agent.AgentRunRequest(agent="task-manager")
    assert req.think_mode == "auto"


def test_request_model_accepts_an_explicit_mode() -> None:
    routes_agent = pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed in this environment",
    )
    req = routes_agent.AgentRunRequest(agent="task-manager", think_mode="max")
    assert req.think_mode == "max"


# ---------------------------------------------------------------------------
# Resolving it from either place it can arrive
# ---------------------------------------------------------------------------

def _routes():
    return pytest.importorskip(
        "gateway.routes.agent", reason="gateway not installed in this environment",
    )


def test_resolves_from_the_payload_the_frontend_actually_sends() -> None:
    """The live regression: route.ts nests think_mode inside payload."""
    r = _routes()
    req = r.AgentRunRequest(agent="task-manager", payload={"think_mode": "max"})
    assert r._resolve_think_mode(req) == "max"


def test_resolves_from_the_top_level_field() -> None:
    r = _routes()
    req = r.AgentRunRequest(agent="task-manager", think_mode="thinking")
    assert r._resolve_think_mode(req) == "thinking"


def test_top_level_wins_over_the_payload() -> None:
    r = _routes()
    req = r.AgentRunRequest(
        agent="task-manager", think_mode="max", payload={"think_mode": "thinking"},
    )
    assert r._resolve_think_mode(req) == "max"


def test_absent_everywhere_resolves_to_auto() -> None:
    r = _routes()
    assert r._resolve_think_mode(r.AgentRunRequest(agent="task-manager")) == "auto"


def test_unknown_values_normalise_to_auto() -> None:
    """Normalised here so the run log reflects what was actually applied."""
    r = _routes()
    for bad in ("turbo", "MAXIMUM", "", "   ", "none"):
        req = r.AgentRunRequest(agent="task-manager", payload={"think_mode": bad})
        assert r._resolve_think_mode(req) == "auto", bad


def test_case_and_whitespace_are_tolerated() -> None:
    r = _routes()
    req = r.AgentRunRequest(agent="task-manager", payload={"think_mode": "  Max "})
    assert r._resolve_think_mode(req) == "max"


def test_non_string_payload_value_does_not_raise() -> None:
    r = _routes()
    for junk in (123, None, {"nested": True}, ["max"]):
        req = r.AgentRunRequest(agent="task-manager", payload={"think_mode": junk})
        assert r._resolve_think_mode(req) == "auto"
