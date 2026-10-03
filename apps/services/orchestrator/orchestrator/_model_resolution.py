"""BYOK-by-default model resolution for loaded agents.

Extracted from ``executor.py`` (foundation maintainability refactor) — no
behaviour change (log event strings preserved). Decides the concrete model a
loaded agent runs with and, for Copilot-SDK agents, pins them to the LiteLLM
gateway (/v1 BYOK) instead of opening a native Copilot session.

Re-exported by ``executor`` (external importers: gateway.main, test_byok_default)
as ``orchestrator.executor.<name>``.
"""
from __future__ import annotations

from typing import Any

from acb_common import get_logger
# Single source of truth for the gateway tier vocabulary. Derived from the
# canonical alias map in acb_llm (same object v1_compat and the Settings UI
# resolve against) so a tier is added/renamed in ONE place and can never drift
# out of sync here. NOT re-listed as literals.
from acb_llm.client import _TIER_ALIAS_MAP as _CANON_TIER_ALIAS_MAP

_log = get_logger("orchestrator.model_resolution")


_GATEWAY_TIER_ALIASES = frozenset(_CANON_TIER_ALIAS_MAP)


def _is_gateway_model(model: str) -> bool:
    """True when *model* is a LiteLLM-gateway id: a known tier alias
    (tier-fast/balanced/powerful) or an explicit ``provider/model``."""
    m = (model or "").strip().lower()
    return bool(m) and ("/" in m or m in _GATEWAY_TIER_ALIASES)


def _byok_default_model(model: str, settings: Any) -> tuple[str, bool]:
    """Apply the BYOK-by-default policy to a resolved model string.

    Returns ``(model, is_byok)``.  When ``copilot_byok_default`` is on (the
    default), every Copilot SDK agent is BYOK-routed through the LiteLLM
    gateway: a gateway-recognised id (``tier-*`` or ``provider/model``) is kept
    as-is, while a bare name the gateway does not expose (e.g. an ``.agent.md``
    ``claude-sonnet-4-5``) — or an empty model — is normalised to
    ``copilot_chat_model`` (default ``tier-balanced``) so it always resolves.

    With the flag off, the legacy rule applies: only ``tier-*`` / ``provider/``
    models are BYOK; bare names hit api.githubcopilot.com direct.
    """
    model = (model or "").strip()
    byok_default = bool(getattr(settings, "copilot_byok_default", True))
    # The coercion target must be a model the gateway actually exposes.  Honour
    # ``copilot_chat_model`` only when it is itself a gateway id (tier-* /
    # provider/model); a bare value there (e.g. ``gpt-4o``) is not gateway-
    # routable, so fall back to the guaranteed ``tier-balanced`` alias.
    configured = (getattr(settings, "copilot_chat_model", "") or "").strip()
    default_tier = configured if _is_gateway_model(configured) else "tier-balanced"
    if byok_default:
        if not _is_gateway_model(model):
            if model and model != default_tier:
                _log.info(
                    "executor.byok_model_coerced",
                    requested=model,
                    coerced_to=default_tier,
                )
            model = default_tier
        return model, True
    return model, _is_gateway_model(model)


def _apply_byok_provider_for_copilot_sdk(
    agent: Any, requested_model: str, settings: Any,
    *, agent_md_model: str = "", agent_model_tier: str = "",
) -> str:
    """Pin a Copilot-SDK agent to the gateway /v1 (BYOK) and set its resolved
    model so ``agent.run()`` routes through litellm instead of opening a NATIVE
    Copilot session (which 402s). No-op for genuine MAF agents (no
    ``_default_options``). Used by the non-streaming run path; the streaming path
    has its own inline early-detection block. Returns the resolved model.
    """
    if not (hasattr(agent, "_default_options")
            and agent._default_options is not None):
        return (requested_model or "").strip()
    configured = (getattr(settings, "copilot_chat_model", "") or "").strip()
    final = (
        (requested_model or "").strip()
        or configured or agent_md_model or agent_model_tier
    )
    final, is_byok = _byok_default_model(final, settings)
    if is_byok:
        gw_base = (
            getattr(settings, "litellm_base_url", "") or "http://127.0.0.1:8080"
        ).rstrip("/")
        # Internal-token precedence must match acb_auth.require_internal_auth
        # (gateway_internal_token → litellm_master_key); presenting only the
        # master key 401s when the two values diverge, which surfaces on the
        # Copilot session as "Authorization error, run /login".
        gw_key = (
            getattr(settings, "llm_api_key", "")
            or getattr(settings, "litellm_master_key", "")
            or "sk-local"
        ).strip()
        agent._default_options["provider"] = {
            "type": "openai", "base_url": f"{gw_base}/v1", "api_key": gw_key,
        }
    if final:
        agent._default_options["model"] = final
    return final


def _apply_thinking_mode(opts: dict, think_mode: str) -> None:
    """Apply thinking/reasoning mode to a COPILOT-SDK agent's options dict.

    Maps our three thinking modes to model-specific parameters:
    - ``"thinking"``: chain-of-thought with a moderate budget
    - ``"max"``:      chain-of-thought with the maximum budget
    - ``"auto"``:     no override (the model decides)

    ⚠️ **Never hand this a native MAF agent's ``default_options``.**
    ``OpenAIChatCompletionClient`` passes every key of that dict to
    ``AsyncCompletions.create()``, and ``model_params`` / ``thinking`` are not
    keywords it takes. Every Thinking or Max turn then raised
    ``unexpected keyword argument 'model_params'`` before a single model
    request (PR #585 review). Native agents go through
    :func:`_apply_thinking_mode_native`, which sets ``reasoning_effort`` only.
    :func:`_apply_thinking_mode_for_agent` picks the right one.

    Moved here from ``gateway.main`` so the two run paths share ONE
    implementation — ``/copilot/chat`` and ``/agent/run/stream`` having their own
    copies of memory injection is precisely how the orchestrator ended up
    silently missing agent- and org-scope memory (agent_architecture.md §11.1.2).
    """
    if think_mode == "thinking":
        opts["model_params"] = opts.get("model_params", {})
        opts["model_params"]["reasoning_effort"] = "medium"
        opts["thinking"] = {"type": "enabled", "budget_tokens": 4000}
    elif think_mode == "max":
        opts["model_params"] = opts.get("model_params", {})
        opts["model_params"]["reasoning_effort"] = "high"
        opts["thinking"] = {"type": "enabled", "budget_tokens": 16000}


#: Think mode → the OpenAI ``reasoning_effort`` a native MAF agent sends.
#: The same two values the Copilot path (Tier 1.5) sends for Thinking and Max.
#: "auto" is absent on purpose: a native agent sends no ``reasoning_effort``
#: then, which is how it behaved before this map existed.
NATIVE_REASONING_EFFORT: dict[str, str] = {"thinking": "medium", "max": "high"}


def _apply_thinking_mode_native(opts: dict, think_mode: str) -> bool:
    """Set ONLY ``reasoning_effort`` on a native MAF agent's ``default_options``.

    ``OpenAIChatCompletionClient`` forwards each key to
    ``AsyncCompletions.create()``, and ``reasoning_effort`` is one of its
    keywords. Returns True when a value was set.
    """
    effort = NATIVE_REASONING_EFFORT.get(think_mode)
    if effort is None:
        return False
    opts["reasoning_effort"] = effort
    return True


def _apply_thinking_mode_for_agent(agent: Any, think_mode: str) -> bool:
    """Apply *think_mode* to whichever options dict *agent* actually uses.

    Copilot-SDK agents carry ``_default_options``; native MAF agents carry
    ``default_options``.  ``_apply_model_for_maf_agent`` already relies on that
    distinction — this mirrors it so a named agent honours the chat UI's thinking
    toggle on either runtime. A native agent gets ``reasoning_effort`` and
    nothing else (:func:`_apply_thinking_mode_native`).

    Returns True when the mode was applied.  ``"auto"`` (or empty) is a no-op, so
    the default path is untouched.
    """
    if not think_mode or think_mode == "auto":
        return False
    opts = getattr(agent, "_default_options", None)
    if isinstance(opts, dict):
        _apply_thinking_mode(opts, think_mode)
        return True
    opts = getattr(agent, "default_options", None)
    if not isinstance(opts, dict):
        return False
    return _apply_thinking_mode_native(opts, think_mode)


def _apply_model_for_maf_agent(
    agent: Any, requested_model: str, settings: Any,
    *, agent_model_tier: str = "",
) -> str:
    """Pin the resolved LiteLLM tier on a NATIVE MAF agent's default_options.

    Native MAF agents read their model from ``default_options["model"]`` (merged
    over the build-time client model at run time). Without setting it the agent
    SILENTLY IGNORES the requested/inherited tier and keeps the build-time
    default (tier-balanced) — so the per-account model and the chat-app tier
    picker have no effect. This is the MAF counterpart of
    ``_apply_byok_provider_for_copilot_sdk``: a no-op for Copilot-SDK agents
    (they own ``_default_options`` and are handled there). Returns the resolved
    model (also used to seed the sub-agent model ContextVar).
    """
    final = (requested_model or "").strip() or (agent_model_tier or "").strip()
    final, _ = _byok_default_model(final, settings)
    # Skip Copilot-SDK agents — the BYOK provider helper owns those.
    if hasattr(agent, "_default_options") and agent._default_options is not None:
        return final
    opts = getattr(agent, "default_options", None)
    if final and isinstance(opts, dict):
        opts["model"] = final
    return final
