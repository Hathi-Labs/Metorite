"""LiteLLM SDK client with tiered routing (ADR-005, ADR-008).

Connects to providers directly via the litellm Python SDK — no proxy needed.
Provider API keys are loaded from the encrypted Postgres key store at startup.

Tiers (per system_architecture.md §10):
    TIER_1  Cheap/fast models                — classify / triage / cheap extraction
    TIER_2  Sonnet-class / GPT-4o-class      — structured extraction, action drafting
    TIER_3  Opus-class / GPT-5-class         — multi-hop reasoning, strategy
"""
from __future__ import annotations

import asyncio
import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from acb_common import get_logger
from litellm import acompletion  # type: ignore[import-untyped]

from acb_llm.model_limits import STUB_MARKER
from acb_llm.prompt_cache import apply_prompt_caching

_log = get_logger("acb_llm")

# Error substrings that indicate a transient failure worth retrying.
_TRANSIENT_ERRORS = (
    "rate limit", "ratelimit", "429", "503", "overload",
    "timeout", "connection", "retry", "service unavailable",
)

#: Hard ceiling on a single provider round-trip, in seconds.
#:
#: Every completion below runs under ``asyncio.wait_for`` at this bound. That is
#: not belt-and-braces over litellm's own ``timeout`` — it is the guarantee. A
#: provider-side ``timeout`` is only honoured to the extent the provider's
#: transport honours it, and on 2026-08-06 one that was not took production down:
#: a completion hung, and because callers across the email automation package hold
#: an open SQLAlchemy session across the call (``AsyncSession`` opens a
#: transaction on first ``execute()`` and holds it until commit/rollback/close),
#: the hung call parked a Postgres transaction `idle in transaction` for 14h44m.
#: A migration's ``ALTER TABLE`` then queued behind that transaction's ACCESS
#: SHARE lock, and because Postgres's lock queue is FIFO every later reader of the
#: table queued behind the *waiting* ALTER. Sending mail — which reads
#: ``email_assistant_settings`` for the signature — stopped working, and the
#: connection pool drained behind the blocked readers.
#:
#: So the bound that matters is on the WALL CLOCK of the await, not on the
#: provider's good intentions. Worst case per call is 3 attempts x this ceiling
#: plus 6s of backoff.
#:
#: Override with ``LLM_REQUEST_TIMEOUT_SECS``; a per-call ``timeout=`` kwarg still
#: wins for the provider-side bound, but never lifts the wall-clock ceiling.
_DEFAULT_REQUEST_TIMEOUT_SECS = 90.0


def _request_timeout_secs() -> float:
    """The per-attempt wall-clock ceiling, from env or the default."""
    raw = os.getenv("LLM_REQUEST_TIMEOUT_SECS", "").strip()
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            _log.warning("acb_llm.bad_request_timeout", value=raw[:32])
    return _DEFAULT_REQUEST_TIMEOUT_SECS

# ── Tier → model mapping ──────────────────────────────────────────────────
# Populated from config.yaml + tier_overrides.yaml at import time so the
# runtime always matches the configured tiers.  Falls back to these hardcoded
# defaults if the config files can't be read.
_TIER_DEFAULTS: dict[str, str] = {
    "tier1": "groq/llama-3.3-70b-versatile",     # fast & cheap (Groq)
    "tier2": "deepseek/deepseek-chat",            # balanced (DeepSeek)
    "tier3": "deepseek/deepseek-reasoner",        # powerful reasoning
    # Speech-to-text tier — resolved like any other tier and routed through
    # litellm.atranscription. Configured in config.yaml (model_name: tier-stt)
    # and editable in Settings → Models. Not a chat tier.
    "stt": "groq/whisper-large-v3-turbo",
}
_TIER_MODEL: dict[str, str] = dict(_TIER_DEFAULTS)

# Tier alias → tier ID (must stay in sync with v1_compat.py._TIER_NAME_TO_ID).
_TIER_ALIAS_MAP: dict[str, str] = {
    "tier-fast": "tier1",
    "tier-balanced": "tier2",
    "tier-powerful": "tier3",
    "tier-stt": "stt",
}

# Tier IDs whose model is a speech-to-text model (routed via atranscription,
# not chat completions). Kept separate so chat-only call sites can ignore them.
_STT_TIER_IDS: frozenset[str] = frozenset({"stt"})

# Track whether keys have been loaded from the store.
_keys_loaded = False
_tier_models_initialised = False


# ── Tier model initialisation from config ─────────────────────────────────

def _find_workspace_root() -> Path | None:
    """Walk up from this file to locate the workspace root.

    Looks for pyproject.toml with ``[tool.uv.workspace]`` — the same
    convention used by settings.py's _repo_root().
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.exists():
            try:
                if "[tool.uv.workspace]" in pyproject.read_text(
                    encoding="utf-8"
                ):
                    return parent
            except OSError:
                pass
    return None


def _init_tier_models() -> None:
    """Populate _TIER_MODEL from config.yaml + tier_overrides.yaml.

    Called once at import time so the in-memory tier mapping always
    reflects the configured models — no gateway restart needed after
    deployments that change config.yaml (tier_overrides.yaml changes
    are handled by set_tier_model() at runtime).
    """
    global _tier_models_initialised
    if _tier_models_initialised:
        return
    _tier_models_initialised = True

    import yaml

    root = _find_workspace_root()
    if not root:
        _log.debug("acb_llm.tier_models_no_root")
        return

    config_path = root / "infra" / "litellm" / "config.yaml"
    if not config_path.exists():
        _log.debug(
            "acb_llm.tier_models_no_config", path=str(config_path)
        )
        return

    try:
        with config_path.open() as f:
            cfg: dict[str, Any] = yaml.safe_load(f) or {}
    except Exception as exc:
        _log.warning(
            "acb_llm.tier_models_config_read_failed", error=str(exc)
        )
        return

    # Merge tier overrides on top (Settings UI changes survive deploys).
    # Source of truth is the model_config Postgres table (key 'tier_overrides');
    # fall back to the legacy tier_overrides.yaml file when the DB is empty or
    # unreachable (e.g. very first boot before the gateway has seeded it).
    overrides: dict[str, Any] = {}
    try:
        from acb_llm.model_config import load_blob
        # H4: no tenant is reachable here and threading one would be wrong.
        # `_init_tier_models` runs at IMPORT time (bottom of this module) and
        # populates `_TIER_MODEL`, which is PROCESS-GLOBAL: one tier→model map
        # shared by every request in the process. There is no request, no
        # session and no bound tenant at import, and passing one would produce
        # a global map filled from whichever organization happened to be first.
        # Per-organization tier resolution is a redesign of this module's
        # caching, not a threading exercise — MT-1j slice 5 records it and does
        # not attempt it (`saas_multitenancy.md` §11). The untenanted read still
        # fails CLOSED once a second organization exists, which degrades to the
        # `config.yaml` defaults rather than to another tenant's models.
        blob = load_blob("tier_overrides")
        if isinstance(blob, dict) and "model_list" in blob:
            overrides = blob
    except Exception as exc:
        _log.warning("acb_llm.tier_models_db_read_failed", error=str(exc))

    if not overrides.get("model_list"):
        overrides_path = root / "infra" / "litellm" / "tier_overrides.yaml"
        if overrides_path.exists():
            try:
                with overrides_path.open() as f:
                    overrides = yaml.safe_load(f) or {}
            except Exception as exc:
                _log.warning(
                    "acb_llm.tier_models_overrides_read_failed",
                    error=str(exc),
                )

    if overrides and "model_list" in overrides:
        override_map = {
            e["model_name"]: e
            for e in overrides["model_list"]
        }
        base_list = cfg.get("model_list", [])
        for i, entry in enumerate(base_list):
            name = entry.get("model_name", "")
            if name in override_map:
                base_list[i] = override_map[name]
        cfg["model_list"] = base_list

    # Extract tier model assignments
    model_list: list[dict] = cfg.get("model_list", [])
    count = 0
    for entry in model_list:
        tier_name = entry.get("model_name", "")
        tier_id = _TIER_ALIAS_MAP.get(tier_name)
        if not tier_id:
            continue
        model = entry.get("litellm_params", {}).get("model", "")
        if model:
            _TIER_MODEL[tier_id] = model
            ensure_model_registered(model)
            count += 1

    if count:
        _log.info(
            "acb_llm.tier_models_initialised",
            models=_TIER_MODEL.copy(),
        )


def set_tier_model(tier_id: str, model: str) -> bool:
    """Update a single tier's model assignment at runtime.

    Called by the Settings UI after a user changes a tier model.
    Updates the in-memory ``_TIER_MODEL`` dict so the Test button
    and all subsequent completions use the new model immediately.

    Args:
        tier_id: One of ``"tier1"``, ``"tier2"``, ``"tier3"``.
        model: LiteLLM model string (e.g. ``"deepseek/deepseek-v4-pro"``).

    Returns:
        ``True`` if the model prefix was recognised and registered.
        ``False`` if the prefix is unknown, but the tier mapping is
        still updated so the caller can try the model anyway.
    """
    if tier_id not in ("tier1", "tier2", "tier3", *_STT_TIER_IDS):
        raise ValueError(f"Unknown tier_id: {tier_id!r}")

    _TIER_MODEL[tier_id] = model

    # Dynamically register so litellm routes through the correct provider
    # even for brand-new models not yet in litellm's built-in registry.
    provider = ensure_model_registered(model)
    _log.info(
        "acb_llm.tier_model_updated",
        tier=tier_id,
        model=model,
        provider=provider,
    )
    return provider is not None


async def _ensure_keys_loaded() -> None:
    """Load provider keys from the encrypted Postgres store into litellm's config.

    On first run with an empty store, auto-seeds any keys found in env vars.
    Falls back to env vars only if the store is completely unreachable.

    ⚠️ **H4 — this is the LIVE completion path's key read, and it is NOT
    tenant-threadable as written.** Two structural reasons, both measured
    2026-08-19 for MT-1j slice 5 (`saas_multitenancy.md` §11):

    1. **It is a once-per-process latch writing PROCESS-GLOBAL state.**
       ``_keys_loaded`` runs the body exactly once, and
       ``store.configure_litellm()`` then assigns ``litellm.<provider>_api_key``
       and ``os.environ[...]`` — module attributes shared by every request in
       the process. Calling it per organization would not scope anything; it
       would make the LAST organization's key the one every caller sends.
    2. **Its caller has no tenant to give it.** The choke point is the
       gateway's ``/v1/chat/completions``
       (``routes/v1_compat.py::_handle_chat_completions``), authenticated by
       ``require_llm_api_auth`` — the deployment-wide ``LITELLM_MASTER_KEY``
       every agent holds. ``get_current_user`` is not in that route's
       dependency chain at all (``dependencies=_auth`` discards the pure
       token check's return; nothing resolves an identity), so
       ``current_tenant()`` is ``None`` *(mechanism corrected 2026-08-19 at
       verification — an earlier draft described a ``system:internal``
       branch this route never takes)*. Deriving the org from ``X-CC-Agent``
       or the body instead is exactly what R5 /
       ``user_management_contract.md`` R11 forbid.

    Making the live path per-tenant therefore needs a **tenant-scoped API key**
    (R11's second admitted source) and per-request provider credentials rather
    than per-process ones. Widening ``LITELLM_MASTER_KEY``'s scope is a
    credential-scope change — ``work_plan.md`` §6 gate (f), the owner's act,
    not this ticket's. Until then the untenanted read fails CLOSED once a
    second organization exists: no key, a visibly failing completion, never
    another tenant's key.
    """
    global _keys_loaded
    if _keys_loaded:
        return
    _keys_loaded = True

    try:
        from acb_llm.key_store import get_key_store
        store = get_key_store()

        # Seed from env vars on first boot (one-time migration)
        existing = await store.get_all()
        if not existing:
            _env_to_provider = {
                "GEMINI_API_KEY": "gemini",
                "OPENAI_API_KEY": "openai",
                "ANTHROPIC_API_KEY": "anthropic",
                "DEEPSEEK_API_KEY": "deepseek",
                "OPENROUTER_API_KEY": "openrouter",
                "GROQ_API_KEY": "groq",
                "MISTRAL_API_KEY": "mistral",
                "TOGETHER_API_KEY": "together",
                "DEEPGRAM_API_KEY": "deepgram",
                "ASSEMBLYAI_API_KEY": "assemblyai",
            }
            for env_var, provider in _env_to_provider.items():
                val = os.environ.get(env_var, "")
                if val and val.strip():
                    await store.put(provider, val.strip())
                    _log.info("acb_llm.key_seeded_from_env", provider=provider)

        await store.configure_litellm()
        _log.info("acb_llm.keys_loaded_from_store")
    except Exception as exc:
        _log.warning("acb_llm.key_store_unavailable", error=str(exc))
        # Fall back to env vars for bootstrap / first-run
        _load_keys_from_env()


def _load_keys_from_env() -> None:
    """Bootstrap litellm config from environment variables (fallback)."""
    import litellm as _litellm

    env_map = {
        "OPENAI_API_KEY": "api_key",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "GEMINI_API_KEY": "gemini_api_key",
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "GROQ_API_KEY": "groq_api_key",
        "MISTRAL_API_KEY": "mistral_api_key",
        "TOGETHER_API_KEY": "together_api_key",
        "OPENROUTER_API_KEY": "openrouter_api_key",
    }
    for env_var, attr in env_map.items():
        val = os.environ.get(env_var, "")
        if val:
            setattr(_litellm, attr, val)
            _log.debug("acb_llm.key_from_env", provider=attr)


class LLMTier(StrEnum):
    TIER_1 = "tier1"
    TIER_2 = "tier2"
    TIER_3 = "tier3"


# Models we dynamically registered with a PLACEHOLDER (zero) price purely so
# litellm can ROUTE them (see ensure_model_registered). Their price is unknown,
# NOT free — cost computation must report them as unknown ("—"), never a
# misleading $0.00. Tracked here so _compute_cost can tell a real $0 (a
# genuinely free known model) from "we never had a price."
_DYNAMIC_STUB_PRICED_MODELS: set[str] = set()


def ensure_model_registered(model: str) -> str | None:
    """Ensure *model* can be routed by litellm.

    If the model is already in litellm's registry, returns the provider name.
    If it follows a known provider prefix (``deepseek/``, ``openai/``, etc.)
    but isn't registered yet, adds it dynamically so litellm routes through
    the correct API rather than silently falling back to OpenRouter.

    Returns the provider name on success, or ``None`` if the model prefix
    isn't recognised (caller should warn the user).

    This keeps the model catalogue dynamic — new provider models work
    immediately without waiting for a litellm or Metorite release.
    """
    from litellm import model_cost

    # Already known — return the provider
    if model in model_cost:
        provider = model_cost[model].get("litellm_provider", "")
        if provider:
            return provider

    # Map known prefixes to litellm providers.
    # When a new model appears (e.g. deepseek/deepseek-v4-turbo), we
    # register it under the correct provider so litellm calls the right
    # API instead of guessing (and potentially falling back to OpenRouter).
    _PREFIX_PROVIDER: dict[str, str] = {
        "deepseek/": "deepseek",
        "openai/": "openai",
        "anthropic/": "anthropic",
        "groq/": "groq",
        "gemini/": "gemini",
        "mistral/": "mistral",
        "together_ai/": "together_ai",
        "openrouter/": "openrouter",
        "cohere/": "cohere",
    }

    for prefix, provider in _PREFIX_PROVIDER.items():
        if model.startswith(prefix):
            # Dynamic registration: add a minimal entry so litellm knows
            # to route through this provider's API.
            #
            # Every number here is a placeholder needed to make the entry
            # well-formed — we do not know this model, that is why we are here.
            # STUB_MARKER says so, so acb_llm.model_limits keeps the ROUTING and
            # ignores the token counts. Without it, context_window_for read the
            # 262144 straight back out of model_cost and reported our own guess
            # as litellm's answer — and it outranked the real fallback table.
            model_cost[model] = {
                "litellm_provider": provider,
                "mode": "chat",
                "max_tokens": 32768,
                "max_input_tokens": 262144,
                "max_output_tokens": 32768,
                "input_cost_per_token": 0,
                "output_cost_per_token": 0,
                "supports_function_calling": True,
                "supports_parallel_function_calling": True,
                "supports_native_streaming": True,
                "supports_system_messages": True,
                "supports_tool_choice": True,
                "supports_response_schema": True,
                STUB_MARKER: True,
            }
            # Remember this is a placeholder price, not a real $0 (H8): so the
            # cost path reports "unknown" instead of a confident $0.00.
            _DYNAMIC_STUB_PRICED_MODELS.add(model)
            _log.info(
                "acb_llm.model_registered_dynamic",
                model=model,
                provider=provider,
            )
            return provider

    return None  # unknown prefix — caller should warn


# ── Telemetry (HH-3) ───────────────────────────────────────────────────────

_telemetry_initialised = False


def _init_telemetry() -> None:
    """Register LiteLLM's OpenTelemetry callback when an OTLP endpoint exists.

    Gated on ``OTEL_EXPORTER_OTLP_ENDPOINT`` so nothing is exported (or even
    imported) until an actual trace backend is configured — the reason MAF's
    own instrumentation had to be disabled was exporting into the void.
    """
    global _telemetry_initialised
    if _telemetry_initialised:
        return
    _telemetry_initialised = True

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        import litellm

        if "otel" not in (litellm.callbacks or []):
            litellm.callbacks = [*(litellm.callbacks or []), "otel"]
        _log.info("acb_llm.otel_enabled")
    except Exception as exc:
        _log.warning("acb_llm.otel_init_failed", error=str(exc))


# ── LiteLLM Redis response cache (specs Phase 5) ─────────────────────────────

_litellm_cache_initialised = False


def _init_litellm_cache() -> None:
    """Enable the LiteLLM exact-match Redis response cache (opt-in per call).

    We use the litellm SDK directly (no proxy), so the proxy's ``cache: True``
    config doesn't apply — the cache is installed here via ``litellm.cache``.
    Gated on ``LITELLM_REDIS_CACHE=1`` so it's inert unless explicitly turned on.
    Even when installed, only requests that pass ``cache={"no-cache": False,
    "no-store": False}`` (i.e. ``enable_litellm_cache=True`` on complete*) are
    stored/served — every other call bypasses it. Good for deterministic
    classify/triage/extraction paths that repeat with identical inputs.
    """
    global _litellm_cache_initialised
    if _litellm_cache_initialised:
        return
    _litellm_cache_initialised = True

    if os.environ.get("LITELLM_REDIS_CACHE", "0") != "1":
        return
    try:
        import litellm
        from litellm.caching.caching import Cache

        ttl = int(os.environ.get("LITELLM_REDIS_CACHE_TTL", "300"))
        litellm.cache = Cache(
            type="redis",
            host=os.environ.get("REDIS_HOST", "redis"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            ttl=ttl,
            # mode default_off equivalent for the SDK: nothing is cached unless
            # the per-request cache flag opts in (see complete*).
            mode="default_off",
        )
        _log.info("acb_llm.litellm_redis_cache_enabled", ttl=ttl)
    except Exception as exc:
        _log.warning("acb_llm.litellm_cache_init_failed", error=str(exc))


def _usage_stats(response: Any) -> dict[str, int]:
    """Extract token + cache counters from a LiteLLM response, best-effort.

    Cache counters are the groundwork for the prompt-caching rollout
    (specs/llm_caching_memory.md Phase 1): once explicit breakpoints ship,
    ``cache_read_tokens`` vs ``prompt_tokens`` is the hit-rate signal.
    """
    stats: dict[str, int] = {}
    try:
        usage = response.get("usage") if hasattr(response, "get") else None
        if not usage:
            return stats
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            val = usage.get(key) if hasattr(usage, "get") else getattr(usage, key, None)
            if isinstance(val, int):
                stats[key] = val
        # Provider cache counters (Anthropic-style top-level and OpenAI-style
        # nested prompt_tokens_details.cached_tokens — LiteLLM passes both).
        for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            val = usage.get(key) if hasattr(usage, "get") else getattr(usage, key, None)
            if isinstance(val, int):
                stats[key] = val
        details = (
            usage.get("prompt_tokens_details") if hasattr(usage, "get")
            else getattr(usage, "prompt_tokens_details", None)
        )
        if details is not None:
            cached = (
                details.get("cached_tokens") if hasattr(details, "get")
                else getattr(details, "cached_tokens", None)
            )
            if isinstance(cached, int):
                stats["cached_tokens"] = cached
    except Exception:
        pass
    return stats


def _compute_cost(model: str, response: Any, stats: dict[str, int]) -> float | None:
    """Best-effort USD cost for one completion, via litellm's price map.

    Tries the response-aware ``completion_cost`` first (accounts for cache
    read/write pricing when the provider reports it), then falls back to
    ``cost_per_token`` on the raw token counts. Returns ``None`` when the model
    isn't in litellm's catalogue (unknown price) so the UI can show "—" rather
    than a misleading $0. Never raises.
    """
    # Models we registered with a placeholder zero price (for routing only) have
    # NO known price — report unknown, never a confident $0.00 (H8). Without
    # this guard litellm.completion_cost finds our 0-cost stub and returns 0.0,
    # defeating the "unknown → None" contract for exactly the tier models in use.
    if model in _DYNAMIC_STUB_PRICED_MODELS:
        return None
    try:
        from litellm import completion_cost
        c = completion_cost(completion_response=response, model=model)
        if isinstance(c, (int, float)) and c >= 0:
            return round(float(c), 8)
    except Exception:
        pass
    try:
        from litellm import cost_per_token
        p, comp = cost_per_token(
            model=model,
            prompt_tokens=int(stats.get("prompt_tokens", 0) or 0),
            completion_tokens=int(stats.get("completion_tokens", 0) or 0),
        )
        total = float(p or 0.0) + float(comp or 0.0)
        return round(total, 8) if total >= 0 else None
    except Exception:
        return None


def _emit_usage(
    model: str, tier: str, response: Any, *,
    source: str | None = None, agent: str | None = None,
    run_id: str | None = None, member: str | None = None,
    instance: str | None = None,
) -> None:
    """Log per-call token/cache usage + USD cost; optionally persist to audit.

    Always emits a structured log line (the cost/cache dashboards read these)
    and publishes a live model activation (with cost) to the activity bus.
    ``source`` attributes the call to the originating app (email / tasks / …)
    and ``agent`` to a specific agent (e.g. v1_compat forwarding the X-CC-Agent
    header) when the caller knows them; otherwise the run context supplies them.
    Set ``LLM_USAGE_AUDIT=1`` to also append an ``audit_event`` row per call.
    Never raises.

    Attribution — decision D1's four-tuple (WS-6c, specs/observability_e2.md
    §7). ``run_id``/``member``/``instance`` complete ``agent`` so a completion
    can be rolled up per run, per member, per agent and per tenant partition.
    **An in-run caller passes none of them**: the run context supplies all four
    (``bind_run_context`` binds them; ``activity._INHERIT`` copies them onto the
    event), which is why widening this signature costs the ~dozen in-run call
    sites zero changes. Only a choke point OUTSIDE a run — the gateway's
    ``/v1/chat/completions``, a bare HTTP request that inherits no contextvars —
    has anything to pass explicitly. ``member`` is the activity feed's ``user``.
    An empty explicit value means "unknown / shared" and defers to the context,
    so a shared run stays *absent* rather than gaining an empty field.
    """
    stats = _usage_stats(response)
    if not stats:
        return
    cost = _compute_cost(model, response, stats)
    # Run correlation (E2): merge_contextvars already tags this LOG line with
    # run_id/agent/user; pull them explicitly so the AUDIT row is joinable too.
    try:
        from acb_common import get_run_context
        _run_ctx = get_run_context()
    except Exception:
        _run_ctx = {}
    # Explicitly-passed attribution, for a caller with no run context of its
    # own. Empty values fall through to the run context / are simply omitted —
    # never emitted as "". Keyed with the activity feed's own vocabulary
    # (member → user) so the log line, the event and the audit row agree.
    _stamp = {
        k: v for k, v in
        (("run_id", run_id), ("user", member), ("instance", instance))
        if v
    }
    _log.info(
        "acb_llm.usage", model=model, tier=tier, cost_usd=cost,
        **stats, **_stamp,
    )
    # Live activity feed (E2): surface every model call on the global bus so the
    # /observability view shows model activations + cost in real time, across
    # every app. Best-effort + non-blocking (never raises); run/agent/user/
    # instance are inherited from the run context when this call is inside an
    # agent run, and `source` is inherited too unless the caller passes it
    # explicitly. Passing None for the stamp fields is what ARMS that
    # inheritance (`activity._INHERIT`) — an explicit value only overrides it.
    try:
        from acb_common import publish_activity
        publish_activity(
            kind="model",
            model=model,
            tier=tier or None,
            tokens=stats.get("total_tokens"),
            cost_usd=cost,
            source=source,
            agent=agent,
            run_id=_stamp.get("run_id"),
            user=_stamp.get("user"),
            instance=_stamp.get("instance"),
        )
    except Exception:
        pass
    if os.environ.get("LLM_USAGE_AUDIT", "0") != "1":
        return
    try:
        from acb_audit import AuditEvent, record

        _actor_agent = agent or _run_ctx.get("agent")

        def _persist() -> None:
            record(AuditEvent(
                actor=f"agent:{_actor_agent}" if _actor_agent
                else "system:acb_llm",
                action="llm_completion",
                target=f"model:{model}",
                # _stamp last: an explicitly-passed attribution outranks the
                # ambient run context (they agree for an in-run call, and only
                # an out-of-run caller passes anything at all).
                payload={
                    "tier": tier, "cost_usd": cost,
                    **stats, **_run_ctx, **_stamp,
                },
            ))

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _persist()
        else:
            # record() opens a sync DB session — keep it off the event loop.
            task = loop.run_in_executor(None, _persist)
            task.add_done_callback(lambda t: t.exception())
    except Exception:
        pass


# Initialise from config at import time (best-effort; hardcoded
# defaults above are the fallback if config files are absent).
_init_tier_models()
_init_telemetry()
_init_litellm_cache()


async def complete(
    *,
    tier: LLMTier,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1024,
    cache_key: str | None = None,
    enable_litellm_cache: bool = False,
    **extra: Any,
) -> str:
    """Send a chat completion directly to the provider via litellm SDK.

    Returns the assistant message content as a plain string. Caller is responsible
    for any downstream parsing / guardrail validation (see acb_llm.guardrails).

    ``cache_key`` routes same-prefix requests to the same OpenAI server pool
    (``prompt_cache_key``) and is a no-op for other providers.  Set
    ``enable_litellm_cache=True`` to opt this call into the LiteLLM Redis
    exact-match response cache (Phase 5) — good for deterministic classify /
    triage / extraction calls that repeat with identical inputs.
    """
    await _ensure_keys_loaded()

    model = _TIER_MODEL[tier.value]

    # Dynamically register model so new provider models work immediately.
    ensure_model_registered(model)

    # Provider-aware prompt caching (specs/llm_caching_memory.md Phase 2/3):
    # Anthropic cache_control blocks + OpenAI prompt_cache_key; sentinel stripped.
    messages, _tools, extra = apply_prompt_caching(  # type: ignore[assignment]
        model=model, messages=messages, tools=None, cache_key=cache_key, extra=extra
    )
    if enable_litellm_cache:
        extra.setdefault("cache", {"no-cache": False, "no-store": False})

    # Ask the provider to bound itself too, so a well-behaved transport fails
    # cleanly rather than being cancelled mid-flight. `setdefault` — an explicit
    # per-call `timeout=` still wins here.
    ceiling = _request_timeout_secs()
    extra.setdefault("timeout", ceiling)

    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(2 ** attempt)  # 2 s, then 4 s
        try:
            response = await asyncio.wait_for(
                acompletion(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra,
                ),
                timeout=ceiling,
            )
            # content can be None for thinking models (e.g. gemini-2.5-pro returns
            # reasoning tokens separately; the text content field is null until done).
            choices = response.get("choices") or []
            if not choices:
                last_exc = RuntimeError(
                    f"LLM returned no choices (model={model}). "
                    f"Response: {dict(response)}"
                )
                continue  # retry
            _emit_usage(model, tier.value, response)
            content = choices[0]["message"]["content"]
            return content or ""  # type: ignore[no-any-return,index]
        except TimeoutError as exc:
            # MUST precede the generic handler. `asyncio.wait_for`'s TimeoutError
            # stringifies to '', so the substring test below reads it as
            # NON-transient and re-raises — which would turn the one failure this
            # ceiling exists to make survivable into a hard error on attempt 1.
            _log.warning("acb_llm.request_timeout", model=model,
                         attempt=attempt + 1, ceiling_secs=ceiling)
            last_exc = exc
            continue
        except Exception as exc:
            if any(token in str(exc).lower() for token in _TRANSIENT_ERRORS):
                last_exc = exc
                continue  # retry on transient errors
            raise  # re-raise non-transient errors immediately

    raise last_exc or RuntimeError(f"LLM completion failed after 3 attempts (model={model})")


async def complete_with_tools(
    *,
    tier: LLMTier,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str = "auto",
    temperature: float = 0.2,
    max_tokens: int = 4096,
    cache_key: str | None = None,
    enable_litellm_cache: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """Like complete() but with tool-calling support.

    Returns the full assistant message dict, which may include ``tool_calls``.
    Feed the returned dict directly back into ``messages`` for the next turn.

    The returned dict is always JSON-serializable (plain Python dicts/lists, no
    Pydantic objects) so it can be stored in LangGraph state without issue.

    See ``complete`` for ``cache_key`` / ``enable_litellm_cache`` semantics.
    For Anthropic tiers the ``tools`` array's last entry is marked
    ``cache_control`` so the whole (byte-stable) schema block is cached.
    """
    await _ensure_keys_loaded()

    model = _TIER_MODEL[tier.value]
    ensure_model_registered(model)

    # Provider-aware prompt caching: Anthropic cache_control on the stable
    # system block AND the tool array; OpenAI prompt_cache_key; sentinel stripped.
    messages, tools, extra = apply_prompt_caching(  # type: ignore[assignment]
        model=model, messages=messages, tools=tools,
        cache_key=cache_key, extra=extra,
    )
    if enable_litellm_cache:
        extra.setdefault("cache", {"no-cache": False, "no-store": False})

    # Same wall-clock ceiling as `complete` — see _DEFAULT_REQUEST_TIMEOUT_SECS.
    # Tool-calling turns are the LONGEST-lived completions in the codebase and
    # the ones most often awaited with a DB session open, so bounding this one
    # matters more than bounding the plain-text path, not less.
    ceiling = _request_timeout_secs()
    extra.setdefault("timeout", ceiling)

    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(2 ** attempt)
        try:
            response = await asyncio.wait_for(
                acompletion(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra,
                ),
                timeout=ceiling,
            )
            choices = response.get("choices") or []
            if not choices:
                last_exc = RuntimeError(
                    f"LLM returned no choices (model={model}). Response: {dict(response)}"
                )
                continue

            _emit_usage(model, tier.value, response)
            msg = choices[0]["message"]

            # Normalise to a plain serialisable dict — LiteLLM may return
            # Pydantic model objects that LangGraph can't pickle.
            result: dict[str, Any] = {
                "role": (msg.get("role") if hasattr(msg, "get") else getattr(msg, "role", "assistant")) or "assistant",
            }
            content = msg.get("content") if hasattr(msg, "get") else getattr(msg, "content", None)
            tool_calls_raw = msg.get("tool_calls") if hasattr(msg, "get") else getattr(msg, "tool_calls", None)

            if content is not None:
                result["content"] = content

            if tool_calls_raw:
                normalised_calls: list[dict[str, Any]] = []
                for tc in tool_calls_raw:
                    if hasattr(tc, "function"):
                        fn = tc.function
                        fn_name = fn.name if hasattr(fn, "name") else fn.get("name", "")
                        fn_args = fn.arguments if hasattr(fn, "arguments") else fn.get("arguments", "{}")
                        tc_id = tc.id if hasattr(tc, "id") else tc.get("id", "")
                    else:
                        fn = tc.get("function") or {}
                        fn_name = fn.get("name", "")
                        fn_args = fn.get("arguments", "{}")
                        tc_id = tc.get("id", "")
                    normalised_calls.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": fn_name, "arguments": fn_args},
                    })
                result["tool_calls"] = normalised_calls

            return result

        except TimeoutError as exc:
            # MUST precede the generic handler — see the note in `complete`.
            _log.warning("acb_llm.request_timeout", model=model,
                         attempt=attempt + 1, ceiling_secs=ceiling)
            last_exc = exc
            continue
        except Exception as exc:
            if any(token in str(exc).lower() for token in _TRANSIENT_ERRORS):
                last_exc = exc
                continue
            raise

    raise last_exc or RuntimeError(f"LLM tool-call completion failed after 3 attempts (model={model})")
