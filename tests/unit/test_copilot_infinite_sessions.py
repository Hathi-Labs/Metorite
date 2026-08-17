"""Regression tests for the Copilot infinite-session context-window fix.

Bug (2026-07-03, technical-project-planner run 5b8c5836): a Copilot-SDK agent on
DeepSeek-V4-Pro (real 1M context) hit a false "context length exceeded" on a
short, ~15-tool run. Root cause: the Copilot backend's infinite-session
auto-compaction keys its 0.80/0.95 thresholds to the model's context window, but
for our BYOK model it can't learn the real window (its list_models() talks to
api.githubcopilot.com, which has no entry for our gateway-routed model), so it
uses a small default and its 0.95 buffer-exhaustion guard blocks prematurely.

Fix: inject an ``infinite_sessions`` SessionConfig block that relaxes (or
disables) those guards. The agent-framework wrapper's ``_create_session`` drops
``infinite_sessions``, so we wrap it to merge our block into the config the
underlying ``client.create_session`` receives. These tests lock both the config
policy and the wrap plumbing.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import orchestrator.executor as ex


# ---------------------------------------------------------------------------
# _copilot_infinite_session_config — env-driven policy
# ---------------------------------------------------------------------------

def test_relaxed_thresholds_by_default(monkeypatch) -> None:
    """With no env override, the block relaxes both thresholds (no early trip)."""
    for k in ("COPILOT_INFINITE_SESSIONS", "COPILOT_COMPACTION_THRESHOLD",
              "COPILOT_BUFFER_THRESHOLD"):
        monkeypatch.delenv(k, raising=False)
    cfg = ex._copilot_infinite_session_config()
    assert cfg is not None
    assert cfg["enabled"] is True
    # Relaxed well above the SDK's premature 0.80/0.95 defaults.
    assert cfg["background_compaction_threshold"] >= 0.90
    assert cfg["buffer_exhaustion_threshold"] >= 0.98


def test_off_disables_backend_compaction(monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_INFINITE_SESSIONS", "off")
    cfg = ex._copilot_infinite_session_config()
    assert cfg == {"enabled": False}


def test_default_opts_out_leaving_sdk_defaults(monkeypatch) -> None:
    """`default` means 'don't touch the SDK's own defaults' → returns None."""
    monkeypatch.setenv("COPILOT_INFINITE_SESSIONS", "default")
    assert ex._copilot_infinite_session_config() is None


def test_custom_thresholds_from_env(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    monkeypatch.setenv("COPILOT_COMPACTION_THRESHOLD", "0.5")
    monkeypatch.setenv("COPILOT_BUFFER_THRESHOLD", "0.7")
    cfg = ex._copilot_infinite_session_config()
    assert cfg["background_compaction_threshold"] == 0.5
    assert cfg["buffer_exhaustion_threshold"] == 0.7


def test_bad_threshold_falls_back_and_clamps(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    monkeypatch.setenv("COPILOT_COMPACTION_THRESHOLD", "not-a-number")
    monkeypatch.setenv("COPILOT_BUFFER_THRESHOLD", "5.0")  # out of band → clamp to 1.0
    cfg = ex._copilot_infinite_session_config()
    assert 0.0 < cfg["background_compaction_threshold"] <= 1.0
    assert cfg["buffer_exhaustion_threshold"] == 1.0


# ---------------------------------------------------------------------------
# _apply_copilot_infinite_sessions — the wrap plumbing
# ---------------------------------------------------------------------------

class _FakeClient:
    """Stands in for the Copilot SDK client; records what create_session got."""
    def __init__(self) -> None:
        self.received_config: dict | None = None

    async def create_session(self, config: dict):
        self.received_config = config
        return MagicMock(session_id="sess-1")


class _FakeCopilotAgent:
    """Minimal shape mirroring the agent-framework Copilot agent: a _client and a
    _create_session that builds a fixed SessionConfig (dropping infinite_sessions,
    exactly like the real wrapper) and calls client.create_session."""
    def __init__(self, *, byok: bool = False) -> None:
        self._client = _FakeClient()
        # Mimic the BYOK provider dict set by _apply_byok_provider_for_copilot_sdk.
        self._default_options: dict = {
            "provider": {"type": "openai", "base_url": "http://gw/v1", "api_key": "k"}
        } if byok else {}

    async def _create_session(self, streaming: bool, runtime_options=None):
        # Mirrors the real _create_session: a FIXED key set, no infinite_sessions.
        config = {"streaming": streaming, "model": "tier-balanced"}
        return await self._client.create_session(config)


def test_wrap_injects_infinite_sessions_into_client_config(monkeypatch) -> None:
    """After the wrap, the config the CLIENT receives carries infinite_sessions —
    even though the agent's own _create_session never sets it."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    monkeypatch.delenv("COPILOT_COMPACTION_THRESHOLD", raising=False)
    monkeypatch.delenv("COPILOT_BUFFER_THRESHOLD", raising=False)

    agent = _FakeCopilotAgent()  # no provider → Copilot-native path
    applied = ex._apply_copilot_infinite_sessions(agent)
    assert applied is True
    assert getattr(agent, "__cc_inf_sessions__", False) is True

    asyncio.run(agent._create_session(True, None))
    cfg = agent._client.received_config
    assert cfg is not None
    assert "infinite_sessions" in cfg, "wrap must inject infinite_sessions"
    assert cfg["infinite_sessions"]["enabled"] is True
    assert cfg["infinite_sessions"]["buffer_exhaustion_threshold"] >= 0.98
    # Fixed keys preserved.
    assert cfg["model"] == "tier-balanced"

    # client.create_session restored to the original after the call (no leak).
    assert agent._client.create_session.__name__ == "create_session"


def test_byok_agent_disables_compaction(monkeypatch) -> None:
    """BYOK agent (provider set in _default_options) always gets enabled:False
    regardless of threshold env vars — the backend's wrong ~90K window estimate
    makes any threshold×90K useless against a 1M-window model."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    monkeypatch.delenv("COPILOT_COMPACTION_THRESHOLD", raising=False)
    monkeypatch.delenv("COPILOT_BUFFER_THRESHOLD", raising=False)

    agent = _FakeCopilotAgent(byok=True)
    applied = ex._apply_copilot_infinite_sessions(agent)
    assert applied is True

    asyncio.run(agent._create_session(True, None))
    cfg = agent._client.received_config
    assert cfg["infinite_sessions"] == {"enabled": False}, (
        "BYOK agent must disable backend compaction, not just relax thresholds"
    )


def test_byok_detection_at_call_time(monkeypatch) -> None:
    """Provider set AFTER the wrap (mirroring real execution order where
    _inject_agent_tools wraps _create_session before BYOK provider is applied)
    must still produce enabled:False at call time."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)

    agent = _FakeCopilotAgent()  # no provider yet
    ex._apply_copilot_infinite_sessions(agent)  # wrap applied before provider

    # Now simulate BYOK provider being set (as _apply_byok_provider_for_copilot_sdk does).
    agent._default_options["provider"] = {
        "type": "openai", "base_url": "http://gw/v1", "api_key": "k",
    }

    asyncio.run(agent._create_session(True, None))
    assert agent._client.received_config["infinite_sessions"] == {"enabled": False}, (
        "BYOK detected at call time even though wrap was applied before provider was set"
    )


def test_non_byok_uses_relaxed_thresholds(monkeypatch) -> None:
    """Copilot-native agent (no provider) uses relaxed thresholds, not disabled."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    monkeypatch.delenv("COPILOT_COMPACTION_THRESHOLD", raising=False)
    monkeypatch.delenv("COPILOT_BUFFER_THRESHOLD", raising=False)

    agent = _FakeCopilotAgent()  # no provider → native Copilot
    ex._apply_copilot_infinite_sessions(agent)

    asyncio.run(agent._create_session(True, None))
    inf = agent._client.received_config["infinite_sessions"]
    assert inf["enabled"] is True, "Copilot-native should keep compaction enabled"
    assert inf["background_compaction_threshold"] >= 0.90
    assert inf["buffer_exhaustion_threshold"] >= 0.98


def test_wrap_is_idempotent(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    agent = _FakeCopilotAgent()
    assert ex._apply_copilot_infinite_sessions(agent) is True
    # Second application is a no-op (guard flag set).
    assert ex._apply_copilot_infinite_sessions(agent) is False


def test_wrap_noop_when_opted_out(monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_INFINITE_SESSIONS", "default")
    agent = _FakeCopilotAgent()
    # Opted out → no wrap, no flag, original method intact.
    assert ex._apply_copilot_infinite_sessions(agent) is False
    assert getattr(agent, "__cc_inf_sessions__", False) is False


def test_off_flows_through_to_client(monkeypatch) -> None:
    monkeypatch.setenv("COPILOT_INFINITE_SESSIONS", "off")
    agent = _FakeCopilotAgent()
    assert ex._apply_copilot_infinite_sessions(agent) is True
    asyncio.run(agent._create_session(True, None))
    assert agent._client.received_config["infinite_sessions"] == {"enabled": False}


# ---------------------------------------------------------------------------
# Rebind regression (2026-07-17) — the wrap alone was NOT enough
# ---------------------------------------------------------------------------
# Every test above exercises the wrap in ISOLATION, which is why they all stayed
# green while production silently bypassed the fix: `run_agent_stream` installs
# the wrap via _inject_agent_tools, then REBINDS `agent._create_session` to
# MetoriteCopilotAgent's method (executor.py:2361) — discarding the wrap
# before agent.run() ever calls it. So the Copilot backend kept running its
# default 0.80/0.95 compaction against its wrong ~90K BYOK window guess.
# The policy now lives in `effective_infinite_sessions` and is applied by the
# Metorite session methods directly, so it survives any binding order.


class _RebindFakeClient:
    def __init__(self) -> None:
        self.received_config: dict | None = None

    async def create_session(self, config: dict):
        self.received_config = config
        return MagicMock(session_id="sess-2")

    async def resume_session(self, session_id: str, config: dict):
        self.received_config = config
        return MagicMock(session_id=session_id)


class _RebindAgent:
    """Shaped enough to run the REAL MetoriteCopilotAgent session methods."""

    def __init__(self, *, byok: bool = False) -> None:
        self._client = _RebindFakeClient()
        self._default_options: dict = {
            "provider": {"type": "openai", "base_url": "http://gw/v1", "api_key": "k"}
        } if byok else {}
        self._settings: dict = {}
        self._tools: list = []
        self._permission_handler = None
        self._mcp_servers = None

    async def _create_session(self, streaming, runtime_options=None):
        # The base-wrapper behaviour (drops infinite_sessions) the wrap patches.
        return await self._client.create_session({"streaming": streaming})


def _rebind(agent):
    """Reproduce executor.py:2361 — the clobber that discarded the wrap."""
    from orchestrator.copilot_agent import MetoriteCopilotAgent
    agent._create_session = MetoriteCopilotAgent._create_session.__get__(
        agent, type(agent)
    )
    agent._resume_session = MetoriteCopilotAgent._resume_session.__get__(
        agent, type(agent)
    )


def test_byok_policy_survives_executor_create_session_rebind(monkeypatch) -> None:
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    agent = _RebindAgent(byok=True)
    ex._apply_copilot_infinite_sessions(agent)  # 1. tool injection wraps ...
    _rebind(agent)                              # 2. ... executor clobbers it

    asyncio.run(agent._create_session(True, None))
    cfg = agent._client.received_config
    assert cfg is not None
    assert cfg.get("infinite_sessions") == {"enabled": False}, (
        "BYOK compaction policy must survive the executor's _create_session "
        "rebind — otherwise the backend compacts against a wrong ~90K window"
    )


def test_byok_policy_applied_on_resume(monkeypatch) -> None:
    """A resumed session rebuilds config from scratch — it must re-apply too."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    agent = _RebindAgent(byok=True)
    _rebind(agent)

    asyncio.run(agent._resume_session("sess-9", True))
    cfg = agent._client.received_config
    assert cfg.get("infinite_sessions") == {"enabled": False}


def test_native_policy_survives_rebind(monkeypatch) -> None:
    """Copilot-native keeps compaction ON (relaxed) after the rebind."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    monkeypatch.delenv("COPILOT_COMPACTION_THRESHOLD", raising=False)
    monkeypatch.delenv("COPILOT_BUFFER_THRESHOLD", raising=False)
    agent = _RebindAgent(byok=False)
    _rebind(agent)

    asyncio.run(agent._create_session(True, None))
    inf = agent._client.received_config.get("infinite_sessions")
    assert inf is not None and inf["enabled"] is True
    assert inf["buffer_exhaustion_threshold"] >= 0.98


def test_opt_out_omits_block_after_rebind(monkeypatch) -> None:
    """`default` must leave the SDK's own defaults intact (no block injected)."""
    monkeypatch.setenv("COPILOT_INFINITE_SESSIONS", "default")
    agent = _RebindAgent(byok=True)
    _rebind(agent)

    asyncio.run(agent._create_session(True, None))
    assert "infinite_sessions" not in agent._client.received_config


def test_effective_policy_is_single_source_of_truth(monkeypatch) -> None:
    """The extracted policy both paths now share."""
    monkeypatch.delenv("COPILOT_INFINITE_SESSIONS", raising=False)
    from orchestrator._copilot_session import effective_infinite_sessions as eff

    assert eff({"provider": {"type": "openai"}}) == {"enabled": False}
    assert eff({})["enabled"] is True
    monkeypatch.setenv("COPILOT_INFINITE_SESSIONS", "default")
    assert eff({}) is None
