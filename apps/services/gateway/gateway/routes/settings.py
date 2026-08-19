"""
GET  /settings/llm          — current tier→model mapping + provider status
POST /settings/llm/tier     — update one tier's model assignment
GET  /settings/llm/health   — proxy LiteLLM /health/readiness
POST /settings/llm/test     — send a test completion on a given tier
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
import yaml
from acb_auth import UserContext, get_current_user, require_permission
from acb_common import get_logger, get_settings
from acb_llm.model_limits import FALLBACK_CONTEXT_WINDOWS, MODEL_CAPABILITIES
from fastapi import APIRouter, Depends, HTTPException
from gateway.db import current_tenant
from pydantic import BaseModel

_log = get_logger("settings")
router = APIRouter(prefix="/settings", tags=["settings"])

# ── MT-1j slice 5 · the tenant on this surface ─────────────────────────
#
# Every `model_config` / `provider_keys` call below passes
# ``organization_id=current_tenant()``. The value comes from ONE place — the
# tenant `acb_auth.deps._with_resolved_access` bound from the authenticated
# session (`saas_multitenancy.md` §11 MT-1j slice 5 · R5 ·
# `user_management_contract.md` R11) — never from a header, query parameter or
# body field. Every route in this module is reached through the app-wide
# `require_authenticated`, whose own dependency is `get_current_user`, so the
# binding is already in context by the time these helpers run; they are sync
# functions called from async handlers, and a ContextVar read works in both.
#
# ``None`` when nothing is bound is deliberate and is NOT a fallback: it hands
# `key_store._resolve_org` / `model_config._resolve_org` exactly the argument
# they receive today, so the sole-org resolution — and its fail-closed arm the
# moment a second organization exists — is untouched. What changes is that once
# org #2 exists, an authenticated caller now resolves to THEIR organization
# instead of to nothing.

# ---------------------------------------------------------------------------
# Locate infra/litellm/config.yaml relative to the repo root.
# Tier overrides (changed via the Settings UI) are persisted in the
# model_config Postgres table so they survive `git reset --hard` on deploy.
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    """Walk up from this file to find the workspace root (pyproject.toml with [tool.uv.workspace]).

    Must skip sub-package pyproject.toml files (e.g. apps/services/gateway/pyproject.toml)
    and only stop at the monorepo root that contains infra/, .env, etc.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.exists():
            try:
                if "[tool.uv.workspace]" in pyproject.read_text(encoding="utf-8"):
                    return parent
            except OSError:
                pass
    raise FileNotFoundError("Workspace root not found from %s" % here)


def _config_path() -> Path:
    candidate = _repo_root() / "infra" / "litellm" / "config.yaml"
    if candidate.exists():
        return candidate
    raise FileNotFoundError("infra/litellm/config.yaml not found")


def _tier_overrides_path() -> Path:
    return _repo_root() / "infra" / "litellm" / "tier_overrides.yaml"


def _load_tier_overrides() -> dict[str, Any]:
    """Return tier overrides {model_list: [...]} from Postgres.

    The DB (model_config table, key 'tier_overrides') is the source of truth so
    Settings UI tier changes survive `git reset --hard` on deploy.  On the first
    read after migration the DB is empty, so we seed it once from the legacy
    tier_overrides.yaml file.
    """
    from acb_llm.model_config import load_blob, save_blob

    org = current_tenant()
    blob = load_blob("tier_overrides", organization_id=org)
    if isinstance(blob, dict) and "model_list" in blob:
        return blob
    # DB empty (or unreachable) — seed from the legacy YAML file once.
    overrides_path = _tier_overrides_path()
    if overrides_path.exists():
        try:
            with overrides_path.open() as f:
                existing = yaml.safe_load(f) or {"model_list": []}
            if existing.get("model_list"):
                try:
                    save_blob("tier_overrides", existing, organization_id=org)
                    _log.info("settings.llm.tier_overrides_seeded_from_file")
                except Exception:
                    pass  # DB unreachable — use file contents this request
                return existing
        except Exception:
            pass
    return {"model_list": []}


def _load_config() -> dict[str, Any]:
    """Load base config.yaml, then merge DB tier overrides on top."""
    path = _config_path()
    with path.open() as f:
        cfg: dict[str, Any] = yaml.safe_load(f) or {}

    # Merge tier overrides from the DB (survives git deploys)
    overrides = _load_tier_overrides()
    if overrides and "model_list" in overrides:
        override_models = {
            e["model_name"]: e
            for e in overrides["model_list"]
        }
        base_list = cfg.get("model_list", [])
        for i, entry in enumerate(base_list):
            name = entry.get("model_name", "")
            if name in override_models:
                base_list[i] = override_models[name]
        cfg["model_list"] = base_list

    return cfg


def _save_tier_override(tier_name: str, entry: dict[str, Any]) -> None:
    """Persist a single tier override to Postgres (model_config table).

    Stored in the DB rather than tier_overrides.yaml so Settings UI changes
    survive `git reset --hard` on deploy.
    """
    from acb_llm.model_config import save_blob

    existing = _load_tier_overrides()
    model_list: list[dict] = existing.get("model_list", [])
    # Replace or append the tier entry
    replaced = False
    for i, e in enumerate(model_list):
        if e.get("model_name") == tier_name:
            model_list[i] = entry
            replaced = True
            break
    if not replaced:
        model_list.append(entry)

    existing["model_list"] = model_list
    save_blob("tier_overrides", existing, organization_id=current_tenant())


def _infra_dir() -> Path:
    """Locate the infra/ directory by walking up from this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infra"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("infra/ directory not found from %s" % here)


def _write_env_key(var: str, value: str) -> None:
    """Upsert VAR=value in the repo-root .env (same file Settings and integrations use)."""
    # Walk up to the workspace root (has [tool.uv.workspace] in pyproject.toml)
    here = Path(__file__).resolve()
    env_file: Path | None = None
    for parent in here.parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.exists():
            try:
                if "[tool.uv.workspace]" in pyproject.read_text(encoding="utf-8"):
                    env_file = parent / ".env"
                    break
            except OSError:
                pass
    if env_file is None:
        env_file = Path.cwd() / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True) if env_file.exists() else []
    pattern = re.compile(rf"^{re.escape(var)}\s*=")
    found = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{var}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{var}={value}\n")
    env_file.write_text("".join(new_lines), encoding="utf-8")


def _recreate_litellm_bg() -> None:
    """Update runtime tier config after settings change (no-op — SDK uses in-memory config).

    Previously restarted a LiteLLM Docker container.  Now that all LLM routing
    goes through the gateway's own /v1 endpoint backed by acb_llm SDK, tier
    changes take effect on the next request (acb_llm reads tiers dynamically).
    """
    # Tier changes are live immediately — acb_llm.client reads _TIER_MODEL at call time.
    pass


# Keep backward compat name used elsewhere in this file
_restart_litellm_bg = _recreate_litellm_bg


def _inject_env_into_litellm(env_var: str, value: str) -> None:
    """Update the live os.environ and encrypted key store with a new provider key.

    Since there's no separate LiteLLM proxy, keys are set in the current
    process environment AND the encrypted Postgres key store.  The gateway's
    /v1 endpoint reads from the key store on every request.

    ⚠️ **The ``os.environ`` half is PROCESS-WIDE and therefore not tenantable
    here.** ``key_store.configure_litellm`` sets ``litellm.<provider>_api_key``
    and the matching env var once per process, so a second organization saving
    its own key would overwrite the first's for every caller. MT-1j slice 5
    threads the *persisted* half (``_sync_key_to_store`` below, per-org and
    correct); the in-process half is the same class as
    ``acb_llm.client._ensure_keys_loaded`` — see its ``H4`` note — and belongs
    to whichever ticket makes provider credentials per-request rather than
    per-process. Recorded, not fixed: `saas_multitenancy.md` §11 MT-1j slice 5.
    """
    # Update os.environ immediately so _is_provider_configured() returns True.
    os.environ[env_var] = value
    # Also update the encrypted key store (best-effort).
    _sync_key_to_store(env_var, value)


def _sync_key_to_store(env_var: str, value: str) -> None:
    """Write a provider key to the encrypted Postgres key store.

    Maps env var name (e.g. GEMINI_API_KEY) → provider slug (e.g. gemini)
    using the reverse of _PROVIDER_ENV_MAP.

    MT-1j slice 5: the write is scoped to the caller's organization. Read the
    binding HERE, on the request's own context, and pass it explicitly — never
    from inside the scheduled coroutine. ``ensure_future`` does copy the
    context, but the value this write owns must not depend on that: the tenant
    is resolved at the call site or it is ``None``, and ``put`` then raises the
    moment a second organization exists rather than writing an ownerless
    credential (``key_store.put``'s docstring, MT-0d). Before this, org #2's
    admin saving a provider key hit exactly that raise and lost it silently to
    the ``except`` below.
    """
    import asyncio as _asyncio

    # Reverse _PROVIDER_ENV_MAP: "GEMINI_API_KEY" → "gemini", etc.
    _env_to_provider: dict[str, str] = {
        v: k for k, v in _PROVIDER_ENV_MAP.items() if v
    }
    provider = _env_to_provider.get(env_var)
    if not provider:
        return
    org = current_tenant()
    try:
        from acb_llm.key_store import get_key_store
        store = get_key_store()
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context — schedule and forget
            _asyncio.ensure_future(
                store.put(provider, value.strip(), organization_id=org)
            )
        else:
            loop.run_until_complete(
                store.put(provider, value.strip(), organization_id=org)
            )
    except Exception:
        pass  # best-effort — key will be re-seeded from .env on restart


# ---------------------------------------------------------------------------
# Provider detection helpers
# ---------------------------------------------------------------------------

_PROVIDER_ENV_MAP: dict[str, str] = {
    "gemini":      "GEMINI_API_KEY",
    "openai":      "OPENAI_API_KEY",
    "anthropic":   "ANTHROPIC_API_KEY",
    "deepseek":    "DEEPSEEK_API_KEY",
    "openrouter":  "OPENROUTER_API_KEY",
    "github":      "GITHUB_TOKEN",    # GitHub Copilot subscription (also routes Claude)
    "groq":        "GROQ_API_KEY",
    "mistral":     "MISTRAL_API_KEY",
    "together":    "TOGETHER_API_KEY",
    "deepgram":    "DEEPGRAM_API_KEY",  # speech-to-text w/ named speakers (Note Taker STT tier)
    "assemblyai":  "ASSEMBLYAI_API_KEY",  # STT + live streaming, both with named speakers
    "ollama":      "",        # local — always "configured" if URL reachable
    "vllm":        "VLLM_BASE_URL",
}

_PROVIDER_LABELS: dict[str, str] = {
    "gemini":      "Google Gemini",
    "openai":      "OpenAI",
    "anthropic":   "Anthropic",
    "deepseek":    "DeepSeek",
    "openrouter":  "OpenRouter",
    "github":      "GitHub Copilot",
    "groq":        "Groq",
    "mistral":     "Mistral AI",
    "together":    "Together AI",
    "deepgram":    "Deepgram (speech-to-text)",
    "assemblyai":  "AssemblyAI (speech-to-text)",
    "ollama":      "Ollama (local)",
    "vllm":        "vLLM (local)",
}

# GitHub Copilot proxy constants
_COPILOT_API_BASE = "https://api.githubcopilot.com"
_COPILOT_EXTRA_HEADERS = {"Copilot-Integration-Id": "vscode-chat"}
# Maps the display model name (github/…) → actual upstream model string.
_GITHUB_MODEL_MAP: dict[str, str] = {
    "github/gpt-4o-mini":    "openai/gpt-4o-mini",
    "github/gpt-4o":         "openai/gpt-4o",
    "github/claude-sonnet":  "anthropic/claude-sonnet-4-5",
    "github/o3-mini":        "openai/o3-mini",
    "github/o1":             "openai/o1",
}
# Reverse: upstream model string → display name (for get_llm_config response)
_GITHUB_MODEL_MAP_REV: dict[str, str] = {v: k for k, v in _GITHUB_MODEL_MAP.items()}

_PROVIDER_MODELS: dict[str, list[str]] = {
    "gemini": [
        "gemini/gemini-2.5-flash-lite",
        "gemini/gemini-2.5-flash",
        "gemini/gemini-2.5-pro",
        "gemini/gemini-2.0-flash",
        "gemini/gemini-1.5-pro",
    ],
    "openai": [
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
        "openai/gpt-4.1",
        "openai/gpt-4.1-mini",
        "openai/o3-mini",
        "openai/o3",
        # Speech-to-text (Note Taker STT tier)
        "openai/gpt-4o-transcribe",
        "openai/gpt-4o-mini-transcribe",
        "openai/whisper-1",
    ],
    "anthropic": [
        "anthropic/claude-opus-4-5",
        "anthropic/claude-sonnet-4-5",
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-opus-4",
        "anthropic/claude-sonnet-4",
        "anthropic/claude-3-7-sonnet-latest",
    ],
    "deepseek": [
        "deepseek/deepseek-chat",
        "deepseek/deepseek-reasoner",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
    ],
    "openrouter": [
        "openrouter/anthropic/claude-opus-4-5",
        "openrouter/anthropic/claude-sonnet-4-5",
        "openrouter/openai/gpt-4o",
        "openrouter/openai/o3-mini",
        "openrouter/google/gemini-2.5-pro",
        "openrouter/google/gemini-2.5-flash",
        "openrouter/meta-llama/llama-4-maverick",
        "openrouter/deepseek/deepseek-r1",
        "openrouter/deepseek/deepseek-r1-0528",
        "openrouter/deepseek/deepseek-chat-v3-0324",
        "openrouter/deepseek/deepseek-chat",
        "openrouter/deepseek/deepseek-v4-pro",
        "openrouter/deepseek/deepseek-v4-flash",
        # Qwen
        "openrouter/qwen/qwen3.7-max",
        "openrouter/qwen/qwen3.7-plus",
        "openrouter/qwen/qwen3.6-plus",
        "openrouter/qwen/qwen3.5-flash-02-23",
        # Kimi
        "openrouter/moonshotai/kimi-k2.6",
        "openrouter/moonshotai/kimi-k2-thinking",
        "openrouter/moonshotai/kimi-k2.5",
        "openrouter/qwen/qwen3-235b-a22b",
    ],
    # GitHub Copilot models — use the `github/` prefix so update_tier can detect
    # them and set the correct api_base + auth headers automatically.
    "github": [
        "github/gpt-4o-mini",
        "github/gpt-4o",
        "github/claude-sonnet",
        "github/o3-mini",
        "github/o1",
    ],
    "ollama": [
        "ollama/llama3.2",
        "ollama/llama3.1:8b",
        "ollama/qwen2.5:7b",
        "ollama/mistral:7b",
        "ollama/phi4",
        "ollama/gemma3:4b",
    ],
    "groq": [
        "groq/llama-3.3-70b-versatile",
        "groq/llama-3.1-8b-instant",
        "groq/llama-3.1-70b-versatile",
        "groq/mixtral-8x7b-32768",
        "groq/gemma2-9b-it",
        "groq/moonshotai/kimi-k2-instruct",
        # Speech-to-text (Note Taker STT tier)
        "groq/whisper-large-v3-turbo",
        "groq/whisper-large-v3",
    ],
    "mistral": [
        "mistral/mistral-small-latest",
        "mistral/mistral-medium-latest",
        "mistral/mistral-large-latest",
        "mistral/codestral-latest",
    ],
    "together": [
        "together_ai/meta-llama/Llama-3-70b-chat-hf",
        "together_ai/meta-llama/Llama-3-8b-chat-hf",
        "together_ai/Qwen/Qwen2.5-72B-Instruct-Turbo",
        "together_ai/mistralai/Mistral-7B-Instruct-v0.3",
        "together_ai/deepseek-ai/DeepSeek-R1",
    ],
    # Deepgram is speech-to-text only (no chat models). These power the Note
    # Taker's STT tier and are the only models here that can name speakers.
    "deepgram": [
        "deepgram/nova-3",
        "deepgram/nova-2",
    ],
    # AssemblyAI is speech-to-text only too, and — unlike Deepgram — reaches us
    # through a native provider rather than litellm (its API is submit-then-poll).
    "assemblyai": [
        "assemblyai/universal-3-pro",
        "assemblyai/universal-2",
    ],
    "vllm": [
        "openai/Qwen/Qwen3-8B-Instruct",
        "openai/meta-llama/Llama-3.1-8B-Instruct",
        "openai/mistralai/Mistral-7B-Instruct-v0.3",
    ],
}

# ── Model capability metadata ─────────────────────────────────────────────
# Human-readable labels + capability flags + the real context/output limits
# for known models.  Defined in acb_llm.model_limits and imported here: this
# table used to live in this file, where the runtime that budgets prompts
# could not reach it, so the runtime trusted litellm's (stale) numbers while
# the Settings UI showed these — the same model reporting two different
# sizes depending on who asked.  One table, one answer; acb_llm owns it.
_MODEL_CAPABILITIES = MODEL_CAPABILITIES

_TIER_LABELS: dict[str, dict[str, str]] = {
    "tier-fast":      {"id": "tier1", "label": "Tier 1 — Fast / Cheap", "description": "Triage, classification, quick routing"},
    "tier-balanced":  {"id": "tier2", "label": "Tier 2 — Balanced",     "description": "Structured extraction, drafting, summaries"},
    "tier-powerful":  {"id": "tier3", "label": "Tier 3 — Powerful",     "description": "Multi-hop reasoning, strategy, planning"},
    "tier-stt":       {"id": "stt",   "label": "STT — Speech-to-text",  "description": "Transcribe meeting & voice audio (Note Taker)"},
}


def _provider_from_model(model: str) -> str:
    """Infer provider slug from litellm model string (model field only)."""
    if model.startswith("gemini/"):
        return "gemini"
    if model.startswith("anthropic/"):
        return "anthropic"
    if model.startswith("deepseek/"):
        return "deepseek"
    if model.startswith("openrouter/"):
        return "openrouter"
    if model.startswith("github/"):
        return "github"
    if model.startswith("groq/"):
        return "groq"
    if model.startswith("mistral/"):
        return "mistral"
    if model.startswith("together_ai/"):
        return "together"
    if model.startswith("deepgram/"):
        return "deepgram"
    if model.startswith("assemblyai/"):
        return "assemblyai"
    if model.startswith("ollama/"):
        return "ollama"
    if model.startswith("openai/"):
        # check if it looks like a vLLM path (has an extra / after openai/)
        if "/" in model.removeprefix("openai/"):
            return "vllm"
        return "openai"
    return "unknown"


def _provider_from_entry(entry: dict[str, Any]) -> str:
    """Infer provider slug from a full LiteLLM model_list entry.

    Checks both model string and api_base so GitHub Copilot entries
    (which use openai/* model strings routed through api.githubcopilot.com)
    are correctly identified as 'github' rather than 'openai'.
    """
    params = entry.get("litellm_params", {})
    model = params.get("model", "")
    api_base = params.get("api_base", "")
    if _COPILOT_API_BASE in str(api_base):
        return "github"
    return _provider_from_model(model)


def _is_provider_configured(provider: str) -> bool:
    env_var = _PROVIDER_ENV_MAP.get(provider, "")
    if not env_var:
        return provider == "ollama"  # local — assume available
    # Check os.environ first (Docker / CI), then Settings (pydantic-settings loads .env)
    val = os.environ.get(env_var, "").strip()
    if not val:
        try:
            val = (getattr(get_settings(), env_var.lower(), "") or "").strip()
        except Exception:
            pass
    return bool(val)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class TierInfo(BaseModel):
    tier_name: str          # e.g. "tier-fast"
    tier_id: str            # "tier1" | "tier2" | "tier3"
    label: str
    description: str
    model: str              # current litellm model string
    provider: str           # "gemini" | "openai" | ...
    provider_configured: bool


class ProviderInfo(BaseModel):
    id: str
    label: str
    configured: bool
    env_var: str
    models: list[str]


class ModelInfo(BaseModel):
    """A single model entry returned by the provider-models endpoint."""
    id: str
    label: str
    provider: str
    vision: bool = False
    audio: bool = False
    transcription: bool = False   # speech-to-text model (routed via atranscription)
    reasoning: bool = False
    context_window: int = 0
    max_output: int = 0
    desc: str = ""


class LLMConfig(BaseModel):
    tiers: list[TierInfo]
    providers: list[ProviderInfo]
    litellm_ui_url: str = ""  # no separate proxy — empty string


class TierUpdateRequest(BaseModel):
    tier_name: str   # e.g. "tier-fast"
    model: str       # new litellm model string
    api_base: str | None = None   # for Ollama / vLLM


class TestRequest(BaseModel):
    tier_name: str   # e.g. "tier-fast"


# ---------------------------------------------------------------------------
# GET /settings/llm
# ---------------------------------------------------------------------------

@router.get("/llm", response_model=LLMConfig)
async def get_llm_config(_user: UserContext = Depends(get_current_user)) -> LLMConfig:
    try:
        cfg = _load_config()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    model_list: list[dict] = cfg.get("model_list", [])

    tiers: list[TierInfo] = []
    for entry in model_list:
        tier_name = entry.get("model_name", "")
        if tier_name not in _TIER_LABELS:
            continue  # skip embeddings etc.
        meta = _TIER_LABELS[tier_name]
        model = entry.get("litellm_params", {}).get("model", "")
        provider = _provider_from_entry(entry)  # use entry-aware detection (catches Copilot)
        # For GitHub Copilot entries, normalise the display model name to `github/…`
        # so the Settings UI shows the human-readable name that matches _PROVIDER_MODELS.
        if provider == "github":
            model = _GITHUB_MODEL_MAP_REV.get(model, f"github/{model.split('/')[-1]}")
        tiers.append(TierInfo(
            tier_name=tier_name,
            tier_id=meta["id"],
            label=meta["label"],
            description=meta["description"],
            model=model,
            provider=provider,
            provider_configured=_is_provider_configured(provider),
        ))

    providers: list[ProviderInfo] = []
    for pid, label in _PROVIDER_LABELS.items():
        providers.append(ProviderInfo(
            id=pid,
            label=label,
            configured=_is_provider_configured(pid),
            env_var=_PROVIDER_ENV_MAP.get(pid, ""),
            models=_PROVIDER_MODELS.get(pid, []),
        ))

    return LLMConfig(
        tiers=tiers,
        providers=providers,
        litellm_ui_url="",  # no separate proxy
    )


# ---------------------------------------------------------------------------
# POST /settings/llm/tier  — patch one tier in config.yaml
# ---------------------------------------------------------------------------

@router.post("/llm/tier", response_model=TierInfo, dependencies=[require_permission("feature:models")])
async def update_tier(
    req: TierUpdateRequest,
    _user: UserContext = Depends(get_current_user),
) -> TierInfo:
    if req.tier_name not in _TIER_LABELS:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {req.tier_name}")

    try:
        cfg = _load_config()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    model_list: list[dict] = cfg.get("model_list", [])
    updated = False
    for entry in model_list:
        if entry.get("model_name") == req.tier_name:
            params = entry.setdefault("litellm_params", {})

            # Detect GitHub Copilot model (github/… prefix used in Settings UI)
            if req.model.startswith("github/"):
                # Translate display name → upstream model + inject Copilot routing
                upstream = _GITHUB_MODEL_MAP.get(req.model, req.model.replace("github/", "openai/"))
                params["model"] = upstream
                params["api_base"] = _COPILOT_API_BASE
                params["api_key"] = "os.environ/GITHUB_TOKEN"
                params["extra_headers"] = dict(_COPILOT_EXTRA_HEADERS)
            elif req.api_base:
                # Local model (Ollama / vLLM)
                params["model"] = req.model
                params["api_base"] = req.api_base
                params["api_key"] = "EMPTY"
                params.pop("extra_headers", None)  # clear any stale provider headers
            else:
                # Hosted provider (Gemini, OpenAI, Groq, Mistral, Together)
                params["model"] = req.model
                params.pop("api_base", None)       # Gemini/OpenAI don't need custom base
                params.pop("extra_headers", None)  # remove stale Copilot headers
                provider = _provider_from_model(req.model)
                env_var = _PROVIDER_ENV_MAP.get(provider, "")
                params["api_key"] = f"os.environ/{env_var}" if env_var else "EMPTY"
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail=f"Tier {req.tier_name} not found in config")

    # Persist to the model_config DB table (NOT config.yaml) so the change
    # survives `git reset --hard` on deploy.
    _save_tier_override(req.tier_name, entry)
    _log.info("settings.llm.tier_updated", tier=req.tier_name, model=req.model, actor=_user.email)

    # Update in-memory _TIER_MODEL dict so the Test button and all subsequent
    # completions use the new model immediately — no gateway restart needed.
    try:
        from acb_llm.client import set_tier_model

        tier_id = _TIER_LABELS[req.tier_name]["id"]
        actual_model = params.get("model", req.model)
        set_tier_model(tier_id, actual_model)
    except ImportError:
        pass  # acb_llm not installed — tier change still persisted to disk

    # Restart LiteLLM so the new config takes effect immediately
    _restart_litellm_bg()

    meta = _TIER_LABELS[req.tier_name]
    # For GitHub Copilot models, return the `github/…` display name the UI sent;
    # for all others, return the actual litellm model string.
    display_model = req.model
    provider = "github" if req.model.startswith("github/") else _provider_from_model(req.model)
    return TierInfo(
        tier_name=req.tier_name,
        tier_id=meta["id"],
        label=meta["label"],
        description=meta["description"],
        model=display_model,
        provider=provider,
        provider_configured=_is_provider_configured(provider),
    )


# ---------------------------------------------------------------------------
# GET /settings/llm/health  — gateway LLM routing health
# ---------------------------------------------------------------------------

class LiteLLMHealth(BaseModel):
    healthy: bool
    detail: str
    ui_url: str = ""


@router.get("/llm/health", response_model=LiteLLMHealth)
async def llm_health(_user: UserContext = Depends(get_current_user)) -> LiteLLMHealth:
    """Check that the gateway's /v1/chat/completions endpoint is reachable.

    Since LLM routing is now handled by the gateway itself (acb_llm SDK),
    we test the gateway's own endpoint rather than a separate proxy.
    """
    settings = get_settings()
    base = str(settings.litellm_base_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.post(
                f"{base}/v1/chat/completions",
                json={"model": "tier-balanced",
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 5},
            )
        healthy = r.status_code == 200
        detail = "Online" if healthy else f"HTTP {r.status_code}"
    except Exception as exc:
        healthy = False
        detail = f"Unreachable: {exc}"
    return LiteLLMHealth(healthy=healthy, detail=detail, ui_url="")


# ---------------------------------------------------------------------------
# POST /settings/llm/test  — quick completion test on a tier
# ---------------------------------------------------------------------------

class TestResult(BaseModel):
    success: bool
    response: str
    latency_ms: int


@router.post("/llm/test", response_model=TestResult, dependencies=[require_permission("feature:models")])
async def test_tier(
    req: TestRequest,
    _user: UserContext = Depends(get_current_user),
) -> TestResult:
    import time

    # Resolve alias → tier id from the ONE canonical map (no local literal copy).
    from acb_llm.client import _TIER_ALIAS_MAP, LLMTier, complete

    tier_id = _TIER_ALIAS_MAP.get(req.tier_name)
    if not tier_id:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {req.tier_name}")

    t0 = time.monotonic()
    try:
        text = await complete(
            tier=LLMTier(tier_id),
            messages=[{"role": "user", "content": "Reply with only: OK"}],
            max_tokens=10,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        return TestResult(success=True, response=(text or "").strip() or "(empty response)", latency_ms=elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return TestResult(success=False, response=str(exc), latency_ms=elapsed)


# ---------------------------------------------------------------------------
# POST /settings/llm/key  — write a provider API key to infra/.env + restart
# ---------------------------------------------------------------------------

class ProviderKeyRequest(BaseModel):
    provider: str   # "gemini" | "openai" | "github" | "groq" | ...
    api_key: str


@router.post("/llm/key", dependencies=[require_permission("feature:models")])
async def set_provider_key(
    req: ProviderKeyRequest,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    env_var = _PROVIDER_ENV_MAP.get(req.provider)
    if not env_var:
        raise HTTPException(status_code=400, detail=f"No env var for provider: {req.provider}")
    if not req.api_key.strip():
        raise HTTPException(status_code=400, detail="api_key cannot be empty")
    try:
        _write_env_key(env_var, req.api_key.strip())
        # Update the live process environment so _is_provider_configured()
        # returns True immediately without a gateway restart.
        os.environ[env_var] = req.api_key.strip()
        # Bust the settings LRU cache so get_settings() also picks up the new value.
        try:
            from acb_common.settings import get_settings as _gs
            _gs.cache_clear()
        except Exception:
            pass
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write env: {exc}") from exc
    _log.info("settings.llm.key_updated", provider=req.provider, actor=_user.email)
    # Inject key into running LiteLLM container immediately (no restart needed).
    # Fallback: restart the container so it picks up the new .env value.
    _inject_env_into_litellm(env_var, req.api_key.strip())
    return {"ok": "true", "env_var": env_var, "provider": req.provider}


# ---------------------------------------------------------------------------
# POST /settings/llm/copilot-model  — set copilot_chat_model in .env
# ---------------------------------------------------------------------------

class CopilotModelRequest(BaseModel):
    model: str   # e.g. "claude-sonnet-4-5", "gpt-4o", "o3-mini"


@router.post("/llm/copilot-model", dependencies=[require_permission("feature:models")])
async def set_copilot_model(
    req: CopilotModelRequest,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Update the model used by GitHub Copilot SDK agents (Tier 1.5 path)."""
    if not req.model.strip():
        raise HTTPException(status_code=400, detail="model cannot be empty")
    try:
        _write_env_key("COPILOT_CHAT_MODEL", req.model.strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write env: {exc}") from exc
    _log.info("settings.llm.copilot_model_updated", model=req.model, actor=_user.email)
    return {"ok": "true", "model": req.model.strip()}


# ---------------------------------------------------------------------------
# Enabled model catalogue — models the user has turned on via Settings → Models.
# Persisted in the model_config Postgres table (key 'enabled_models') so it
# survives `git reset --hard` on deploy.  The legacy infra/enabled_models.json
# (formerly custom_models.json) is read once to seed the DB, then ignored.
# JSON structure: {"enabled": [...], "hidden": [...]}
# Legacy keys: "custom" is treated as an alias for "enabled".
# ---------------------------------------------------------------------------

def _enabled_models_path() -> Path:
    """Locate infra/enabled_models.json (or legacy custom_models.json)."""
    try:
        infra = _infra_dir()
        new_path = infra / "enabled_models.json"
        old_path = infra / "custom_models.json"
        # Migrate legacy file on first access
        if not new_path.exists() and old_path.exists():
            old_path.rename(new_path)
        return new_path
    except FileNotFoundError:
        return Path.cwd() / "enabled_models.json"


def _normalise_catalogue(data: object) -> dict[str, object]:
    """Coerce any stored shape into {enabled: [...], hidden: [...]}.

    Supports all legacy shapes:
      - plain list  (very old format)
      - {"custom": [...], "hidden": [...]}  (pre-2026-06 format)
      - {"enabled": [...], "hidden": [...]}  (current format)
    """
    if isinstance(data, list):
        return {"enabled": data, "hidden": []}
    if isinstance(data, dict):
        enabled = data.get("enabled") or data.get("custom") or []
        hidden = data.get("hidden", [])
        return {
            "enabled": enabled if isinstance(enabled, list) else [],
            "hidden": hidden if isinstance(hidden, list) else [],
        }
    return {"enabled": [], "hidden": []}


def _load_catalogue_from_file() -> dict[str, object]:
    """Read the legacy enabled_models.json file (used only to seed the DB)."""
    import json
    p = _enabled_models_path()
    if not p.exists():
        return {"enabled": [], "hidden": []}
    try:
        return _normalise_catalogue(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return {"enabled": [], "hidden": []}


def _load_catalogue() -> dict[str, object]:
    """Load model catalogue {enabled: [...], hidden: [...]} from Postgres.

    The DB (model_config table, key 'enabled_models') is the source of truth so
    the config survives `git reset --hard` on deploy.  On the first read after
    migration the DB is empty, so we seed it from the legacy JSON file once.
    """
    from acb_llm.model_config import load_blob, save_blob

    org = current_tenant()
    blob = load_blob("enabled_models", organization_id=org)
    if blob is not None:
        return _normalise_catalogue(blob)
    # DB empty (or unreachable) — seed from the legacy file once.
    cat = _load_catalogue_from_file()
    if cat.get("enabled") or cat.get("hidden"):
        try:
            save_blob("enabled_models", cat, organization_id=org)
            _log.info("settings.llm.enabled_models_seeded_from_file")
        except Exception:
            pass  # DB unreachable — fall back to file contents this request
    return cat


def _save_catalogue(catalogue: dict[str, object]) -> None:
    from acb_llm.model_config import save_blob
    save_blob("enabled_models", catalogue, organization_id=current_tenant())


def _load_enabled_models() -> list[dict[str, str]]:
    cat = _load_catalogue()
    raw = cat.get("enabled", [])
    return raw if isinstance(raw, list) else []  # type: ignore[return-value]


def _save_enabled_models(models: list[dict[str, str]]) -> None:
    cat = _load_catalogue()
    cat["enabled"] = models  # type: ignore[assignment]
    # Remove legacy "custom" key if present
    cat.pop("custom", None)  # type: ignore[attr-defined]
    _save_catalogue(cat)


# Keep alias so any other code that calls _load_custom_models() still works
_load_custom_models = _load_enabled_models
_save_custom_models = _save_enabled_models


class EnabledModelEntry(BaseModel):
    id: str        # LiteLLM model string, e.g. "openrouter/qwen/qwen3.8-preview"
    label: str     # display name in the picker
    provider: str  # "gemini" | "openrouter" | "anthropic" | ...
    group: str = ""


# Backward-compat alias
CustomModelEntry = EnabledModelEntry


class EnabledModelAddRequest(BaseModel):
    id: str
    label: str
    provider: str
    group: str = ""


CustomModelAddRequest = EnabledModelAddRequest


@router.get("/llm/enabled-models")
async def list_enabled_models(
    _user: UserContext = Depends(get_current_user),
) -> dict[str, object]:
    """Return enabled models and the hidden model list."""
    cat = _load_catalogue()
    enabled_raw = cat.get("enabled") or cat.get("custom") or []
    return {
        "custom": [EnabledModelEntry(**m) for m in enabled_raw],  # keep "custom" key for frontend compat
        "enabled": [EnabledModelEntry(**m) for m in enabled_raw],
        "hidden": cat.get("hidden") or [],
    }


@router.post("/llm/enabled-models", status_code=201, dependencies=[require_permission("feature:models")])
@router.post("/llm/custom-models", status_code=201, dependencies=[require_permission("feature:models")])
async def add_custom_model(
    req: CustomModelAddRequest,
    _user: UserContext = Depends(get_current_user),
) -> CustomModelEntry:
    """Enable a model (add it to the catalogue so it appears in pickers).

    Registered at both ``/llm/enabled-models`` (used by the Models settings
    page eye-toggle) and the legacy ``/llm/custom-models`` path.

    The model ID must be a valid LiteLLM model string that the configured
    provider can route (e.g. ``openrouter/qwen/qwen3.8-preview``).
    No LiteLLM restart is required — the model is routed directly on the
    next request using the provider's API key already in os.environ.
    Idempotent: re-enabling an already-enabled model returns it instead of 409.
    """
    model_id = req.id.strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="id cannot be empty")
    if not req.label.strip():
        raise HTTPException(status_code=400, detail="label cannot be empty")

    models = _load_enabled_models()
    # Idempotent: if already enabled, return the existing entry (no 409).
    existing = next((m for m in models if m["id"] == model_id), None)
    if existing is not None:
        return EnabledModelEntry(**existing)

    provider = req.provider.strip() or _provider_from_model(model_id)
    # Derive clean group name from provider slug — no "Custom" prefix
    provider_labels: dict[str, str] = {
        "gemini": "Gemini", "openai": "OpenAI", "anthropic": "Anthropic",
        "openrouter": "OpenRouter", "github": "GitHub Copilot", "groq": "Groq",
        "deepseek": "DeepSeek", "mistral": "Mistral", "together": "Together AI",
        "ollama": "Ollama", "vllm": "vLLM",
    }
    group = req.group.strip() or provider_labels.get(provider, provider)
    entry: dict[str, str] = {
        "id": model_id,
        "label": req.label.strip(),
        "provider": provider,
        "group": group,
    }
    models.append(entry)
    _save_enabled_models(models)
    _log.info("settings.llm.model_enabled", id=model_id, actor=_user.email)
    return EnabledModelEntry(**entry)


@router.delete("/llm/enabled-models/{model_id:path}", status_code=200, dependencies=[require_permission("feature:models")])
async def remove_enabled_model(
    model_id: str,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Disable a model by removing it from the enabled list."""
    models = _load_enabled_models()
    new_models = [m for m in models if m["id"] != model_id]
    if len(new_models) == len(models):
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} not in enabled list")
    _save_enabled_models(new_models)
    _log.info("settings.llm.model_disabled", id=model_id, actor=_user.email)
    return {"disabled": model_id}


# ---------------------------------------------------------------------------
# Hidden model list  — models suppressed from the chat picker
# ---------------------------------------------------------------------------

@router.get("/llm/hidden-models")
async def list_hidden_models(
    _user: UserContext = Depends(get_current_user),
) -> list[str]:
    """Return the list of model IDs currently hidden from the chat picker."""
    cat = _load_catalogue()
    raw = cat.get("hidden", [])
    return raw if isinstance(raw, list) else []  # type: ignore[return-value]


@router.post("/llm/hidden-models", status_code=201, dependencies=[require_permission("feature:models")])
async def hide_model(
    body: dict[str, str],
    _user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Add a model ID to the hidden list so it no longer appears in the chat picker."""
    model_id = (body.get("id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="id cannot be empty")
    cat = _load_catalogue()
    hidden: list[str] = cat.get("hidden", [])  # type: ignore[assignment]
    if not isinstance(hidden, list):
        hidden = []
    if model_id not in hidden:
        hidden.append(model_id)
        cat["hidden"] = hidden  # type: ignore[assignment]
        _save_catalogue(cat)
        _log.info("settings.llm.model_hidden", id=model_id, actor=_user.email)
    return {"hidden": model_id}


@router.delete("/llm/hidden-models/{model_id:path}", status_code=200, dependencies=[require_permission("feature:models")])
async def unhide_model(
    model_id: str,
    _user: UserContext = Depends(get_current_user),
) -> dict[str, str]:
    """Remove a model ID from the hidden list so it reappears in the chat picker."""
    cat = _load_catalogue()
    hidden: list[str] = cat.get("hidden", [])  # type: ignore[assignment]
    if not isinstance(hidden, list):
        hidden = []
    if model_id not in hidden:
        raise HTTPException(status_code=404, detail=f"Model {model_id!r} is not hidden")
    hidden.remove(model_id)
    cat["hidden"] = hidden  # type: ignore[assignment]
    _save_catalogue(cat)
    _log.info("settings.llm.model_unhidden", id=model_id, actor=_user.email)
    return {"unhidden": model_id}


# ---------------------------------------------------------------------------
# Provider model cache
# Structure of infra/provider_models_cache.json:
#   {
#     "openrouter": {
#       "fetched_at": "2026-06-14T10:00:00Z",
#       "models": [{id, label, provider, vision, audio, reasoning,
#                   context_window, max_output, desc}, ...]
#     },
#     ...
#   }
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


def _models_cache_path() -> Path:
    try:
        return _infra_dir() / "provider_models_cache.json"
    except FileNotFoundError:
        return Path.cwd() / "provider_models_cache.json"


def _load_models_cache() -> dict[str, Any]:
    import json
    p = _models_cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_models_cache(cache: dict[str, Any]) -> None:
    import json
    p = _models_cache_path()
    p.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _cache_entry_fresh(entry: dict[str, Any]) -> bool:
    """True if the cache entry was fetched within _CACHE_TTL_SECONDS."""
    import datetime
    fetched_at = entry.get("fetched_at", "")
    if not fetched_at:
        return False
    try:
        ts = datetime.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.UTC)
        return (now - ts).total_seconds() < _CACHE_TTL_SECONDS
    except Exception:
        return False


def _is_transcription_model(model_id: str) -> bool:
    """Heuristic: is this a speech-to-text model (routed via atranscription)?

    Used so un-catalogued STT models a live provider fetch surfaces (e.g. a new
    whisper variant from Groq's ``/models``) are still offered for the STT tier
    and kept out of the chat-tier pickers, without waiting for a catalogue entry.
    """
    mid = model_id.lower()
    return (
        "whisper" in mid
        or mid.startswith("deepgram/")
        or mid.startswith("assemblyai/")
        or "transcribe" in mid
        or "/nova-" in mid
    )


def _model_info_from_caps(model_id: str, provider: str) -> ModelInfo:
    """Build a ModelInfo from _MODEL_CAPABILITIES, falling back to bare defaults."""
    caps = _MODEL_CAPABILITIES.get(model_id, {})
    is_stt = caps.get("transcription", _is_transcription_model(model_id))
    return ModelInfo(
        id=model_id,
        label=caps.get("label", model_id.split("/")[-1]),
        provider=provider,
        vision=caps.get("vision", False),
        audio=caps.get("audio", is_stt),  # STT models consume audio input
        transcription=is_stt,
        reasoning=caps.get("reasoning", False),
        context_window=caps.get("context_window", 0),
        max_output=caps.get("max_output", 0),
        desc=caps.get("desc", ""),
    )


# ── Per-provider live fetch functions ────────────────────────────────────────

async def _fetch_openrouter(key: str | None) -> list[ModelInfo]:
    """Fetch all models from OpenRouter — no auth required, key adds rate limits."""
    headers: dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers=headers,
            )
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception as exc:
        _log.warning("openrouter.fetch_failed", error=str(exc))
        return []

    result: list[ModelInfo] = []
    for m in data:
        mid = f"openrouter/{m.get('id', '').lstrip('openrouter/')}"
        name: str = m.get("name", mid.split("/")[-1])
        # Strip common "Provider: " prefixes from display names
        for prefix in ("OpenRouter: ", "openrouter: "):
            name = name.removeprefix(prefix)
        ctx = int(m.get("context_length") or 0)
        arch = m.get("architecture") or {}
        inp_mod: list[str] = arch.get("input_modalities") or []
        vision = "image" in inp_mod
        audio = "audio" in inp_mod
        # Heuristic reasoning flag
        lid = mid.lower()
        reasoning = any(
            x in lid
            for x in ("r1", "reasoner", "thinking", "o3", "o1-", "deepseek-r")
        )
        # Check static caps first (more accurate)
        caps = _MODEL_CAPABILITIES.get(mid, {})
        result.append(ModelInfo(
            id=mid,
            label=caps.get("label", name),
            provider="openrouter",
            vision=caps.get("vision", vision),
            audio=caps.get("audio", audio),
            reasoning=caps.get("reasoning", reasoning),
            context_window=caps.get("context_window", ctx),
            max_output=caps.get("max_output", 0),
            desc=caps.get("desc", m.get("description", "")[:120]),
        ))
    return result


async def _fetch_openai_compat(
    provider: str,
    base_url: str,
    api_key: str,
    prefix: str,
    auth_header: str = "Authorization",
    auth_scheme: str = "Bearer",
    extra_headers: dict[str, str] | None = None,
) -> list[ModelInfo]:
    """Fetch models from any OpenAI-compatible endpoint."""
    hdrs: dict[str, str] = {auth_header: f"{auth_scheme} {api_key}"}
    if extra_headers:
        hdrs.update(extra_headers)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{base_url}/models", headers=hdrs)
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception as exc:
        _log.warning(f"{provider}.fetch_failed", error=str(exc))
        return []

    result: list[ModelInfo] = []
    for m in data:
        raw_id: str = m.get("id", "")
        # Skip empty or ownership-only entries
        if not raw_id or ":" in raw_id:
            continue
        mid = f"{prefix}{raw_id}"
        result.append(_model_info_from_caps(mid, provider))
    return result


async def _fetch_gemini(api_key: str) -> list[ModelInfo]:
    """Fetch models from Google Generative Language API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key, "pageSize": 100},
            )
            r.raise_for_status()
            data = r.json().get("models", [])
    except Exception as exc:
        _log.warning("gemini.fetch_failed", error=str(exc))
        return []

    result: list[ModelInfo] = []
    for m in data:
        name: str = m.get("name", "")  # "models/gemini-2.0-flash"
        raw = name.removeprefix("models/")
        if not raw:
            continue
        # Only include generative models
        methods: list[str] = m.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        mid = f"gemini/{raw}"
        ctx = int(m.get("inputTokenLimit") or 0)
        out = int(m.get("outputTokenLimit") or 0)
        caps = _MODEL_CAPABILITIES.get(mid, {})
        result.append(ModelInfo(
            id=mid,
            label=caps.get("label", m.get("displayName", raw)),
            provider="gemini",
            vision=caps.get("vision", False),
            audio=caps.get("audio", False),
            reasoning=caps.get("reasoning", False),
            context_window=caps.get("context_window", ctx),
            max_output=caps.get("max_output", out),
            desc=caps.get("desc", m.get("description", "")[:120]),
        ))
    return result


async def _fetch_anthropic(api_key: str) -> list[ModelInfo]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception as exc:
        _log.warning("anthropic.fetch_failed", error=str(exc))
        return []

    result: list[ModelInfo] = []
    for m in data:
        raw = m.get("id", "")
        if not raw:
            continue
        mid = f"anthropic/{raw}"
        result.append(_model_info_from_caps(mid, "anthropic"))
    return result


async def _fetch_ollama(base_url: str) -> list[ModelInfo]:
    url = base_url.rstrip("/")
    # Try the /api/tags endpoint (Ollama native)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{url}/api/tags")
            r.raise_for_status()
            data = r.json().get("models", [])
    except Exception:
        return []

    result: list[ModelInfo] = []
    for m in data:
        raw = m.get("name", "")
        if not raw:
            continue
        mid = f"ollama/{raw}"
        result.append(_model_info_from_caps(mid, "ollama"))
    return result


async def _fetch_live_models(
    provider: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> list[ModelInfo]:
    """Dispatch to the right live-fetch function per provider."""
    if provider == "openrouter":
        return await _fetch_openrouter(api_key)

    if provider == "gemini" and api_key:
        return await _fetch_gemini(api_key)

    if provider == "anthropic" and api_key:
        return await _fetch_anthropic(api_key)

    if provider == "openai" and api_key:
        return await _fetch_openai_compat(
            "openai", "https://api.openai.com/v1", api_key, "openai/",
        )

    if provider == "deepseek" and api_key:
        return await _fetch_openai_compat(
            "deepseek", "https://api.deepseek.com/v1", api_key, "deepseek/",
        )

    if provider == "groq" and api_key:
        return await _fetch_openai_compat(
            "groq", "https://api.groq.com/openai/v1", api_key, "groq/",
        )

    if provider == "mistral" and api_key:
        return await _fetch_openai_compat(
            "mistral", "https://api.mistral.ai/v1", api_key, "mistral/",
        )

    if provider == "together" and api_key:
        return await _fetch_openai_compat(
            "together",
            "https://api.together.xyz/v1",
            api_key,
            "together_ai/",
        )

    if provider == "assemblyai" and api_key:
        return await _fetch_assemblyai(api_key)

    if provider == "ollama":
        base = api_base or "http://localhost:11434"
        return await _fetch_ollama(base)

    # github / vllm / deepgram / unknown — fall back to static list.
    # (Deepgram publishes no model-list endpoint either; its models are curated.)
    return []


async def _fetch_assemblyai(api_key: str) -> list[ModelInfo]:
    """AssemblyAI models, verified against the account.

    Unlike OpenAI-compatible providers, AssemblyAI publishes **no model-list
    endpoint** — the valid `speech_models` values are documented, not
    discoverable. So "refresh" can't enumerate them; what it CAN do is prove the
    key works, which is the actual question a user is asking when they hit it.
    We call a cheap authenticated endpoint and, on success, return the curated
    list; on an auth failure we return nothing so the UI doesn't imply the key
    is good. Any id the user types still works — the provider passes whatever
    follows "assemblyai/" straight through as the model."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Cheapest authenticated call available: list one transcript.
            r = await client.get(
                "https://api.assemblyai.com/v2/transcript",
                params={"limit": 1},
                headers={"authorization": api_key},
            )
        if r.status_code in (401, 403):
            _log.warning("provider_models.assemblyai_key_rejected", status=r.status_code)
            return []
        if r.status_code >= 400:
            # Reachable but unhappy (rate limit, transient) — fall back to the
            # curated list rather than blanking the picker.
            _log.info("provider_models.assemblyai_probe_soft_fail", status=r.status_code)
    except Exception as exc:
        _log.warning("provider_models.assemblyai_probe_failed", error=str(exc)[:200])
        return []
    return _static_models_for_provider("assemblyai")


def _static_models_for_provider(provider: str) -> list[ModelInfo]:
    """Return static ModelInfo list from _PROVIDER_MODELS."""
    return [
        _model_info_from_caps(mid, provider)
        for mid in _PROVIDER_MODELS.get(provider, [])
    ]


# ---------------------------------------------------------------------------
# GET /settings/llm/provider-models — return cached (or static fallback) models
# ---------------------------------------------------------------------------

@router.get("/llm/provider-models", response_model=list[ModelInfo])
async def list_provider_models(
    provider: str,
    _user: UserContext = Depends(get_current_user),
) -> list[ModelInfo]:
    """Return models for a provider.

    Checks the on-disk cache first.  If the cache entry is stale or missing,
    falls back to the static list so the UI is never empty.  Use the refresh
    endpoint to populate / update the cache.

    GitHub is special-cased to the LIVE Copilot SDK list (the same source the
    chat model dropdown uses via ``/copilot/models``).  Without this the Models
    page showed a stale static ``github/...`` list whose IDs never matched the
    dropdown's live IDs (e.g. ``claude-sonnet-4.5``), so enabling a Copilot
    model here had no effect on the picker.  Serving the same live IDs makes the
    Models page the real source of truth for the picker's allow-list filter.
    """
    if provider == "github":
        try:
            # Lazy import to avoid a circular import at module load (main.py
            # imports this router).  Reuses main.py's 5-min cache + SDK fetch.
            from gateway.main import copilot_models
            data = await copilot_models()
            raw_models = data.get("models", []) if isinstance(data, dict) else []
            live = [
                ModelInfo(
                    id=str(m.get("id")),
                    label=str(m.get("label") or m.get("id") or ""),
                    provider="github",
                    reasoning=bool(m.get("reasoning", False)),
                    context_window=int(m.get("context_window") or 0),
                    desc="Via your GitHub Copilot subscription",
                )
                # Drop the "auto" picker convenience — it's not a real
                # toggleable model on the Models page.
                for m in raw_models
                if m.get("id") and str(m.get("id")) != "auto"
            ]
            if live:
                return live
        except Exception as exc:  # fall back to cache/static
            _log.warning("settings.github_live_models_failed", error=str(exc)[:160])

    cache = _load_models_cache()
    entry = cache.get(provider, {})
    if entry and _cache_entry_fresh(entry):
        raw = entry.get("models", [])
        return [ModelInfo(**m) for m in raw]

    # Cache miss / stale → serve static list (UI never empty)
    return _static_models_for_provider(provider)


# Context windows for models litellm doesn't know — the same table acb_llm
# resolves against, imported rather than repeated.
#
# It used to be a second copy that ALSO pinned tier-fast/-balanced/-powerful to
# hardcoded numbers "matching config.yaml", maintained by hand. It drifted, as
# hand-maintained copies do: tier_overrides.yaml re-pointed tier-balanced at
# deepseek-chat (131K) while this said 1M. Worse, the pin was applied AFTER the
# dynamic resolution below and overwrote it — the endpoint computed the right
# window and then replaced it with the stale one, so the UI's context ring
# under-reported usage by ~7.6x. The tier aliases are deliberately absent here
# so the dynamic resolution stands.
_TIER_CONTEXT_WINDOWS = FALLBACK_CONTEXT_WINDOWS


@router.get("/llm/context-windows")
async def get_context_windows(
    _user: UserContext = Depends(get_current_user),
) -> dict[str, int]:
    """Return a ``{model_id: context_window}`` map for every known model.

    Merges three sources so the frontend can show an accurate context-usage
    ring for whatever model is selected (including after a mid-chat switch):
      1. Static ``_MODEL_CAPABILITIES`` (covers all built-in models).
      2. The live provider-models cache (covers refreshed provider lists, e.g.
         the full OpenRouter catalogue with real context sizes).
      3. Tier routing aliases (``tier-fast`` / ``tier-balanced`` /
         ``tier-powerful``).

    Keys are stored under both their full id and their bare suffix (after the
    last ``/``) so lookups succeed regardless of provider-prefix differences
    between the picker id and the capability key.
    """
    out: dict[str, int] = {}

    def _put_full(model_id: str, cw: int) -> None:
        """Set the exact id only (no bare-suffix derivation)."""
        if model_id and cw > 0:
            out[model_id] = cw

    def _put_with_bare(model_id: str, cw: int) -> None:
        """Set the exact id AND its bare suffix — only safe for CURATED sources
        (the static map + tiers) where the value is trustworthy."""
        if not model_id or cw <= 0:
            return
        out[model_id] = cw
        bare = model_id.split("/")[-1]
        out.setdefault(bare, cw)

    # 1. Live cache FIRST (least trusted) — full id ONLY.  Deriving bare
    #    suffixes here would mis-assign inflated/beta numbers (e.g.
    #    openrouter/auto=2M, openrouter Claude=1M-beta) to the picker's generic
    #    "auto" / copilot "claude-*" entries.  Curated values below override.
    try:
        cache = _load_models_cache()
        for entry in cache.values():
            for m in entry.get("models", []):
                _put_full(str(m.get("id", "")), int(m.get("context_window", 0) or 0))
    except Exception:
        pass

    # 2. Static capabilities (curated, trusted) — overrides cache, adds bare.
    for mid, caps in _MODEL_CAPABILITIES.items():
        _put_with_bare(mid, int(caps.get("context_window", 0) or 0))

    # 3. Tier aliases — resolved dynamically so the ring tracks the currently-
    #    configured model (Settings UI changes propagate immediately).
    try:
        from acb_llm.client import _TIER_ALIAS_MAP
        from acb_llm.context import context_window_for as _cwf
        for alias in _TIER_ALIAS_MAP:
            cw = _cwf(alias)
            if cw > 0:
                _put_with_bare(alias, cw)
    except Exception:
        pass
    # Static fallback for legacy / non-gateway tier aliases.
    for mid, cw in _TIER_CONTEXT_WINDOWS.items():
        _put_with_bare(mid, cw)

    # "auto" means the SDK picks the model at runtime — never pin it to a
    # (mis)matched window; let the frontend apply a conservative default.
    out.pop("auto", None)

    return out


class RefreshRequest(BaseModel):
    providers: list[str] | None = None  # None = all configured


class RefreshResult(BaseModel):
    provider: str
    count: int
    fetched_at: str
    error: str | None = None


# ---------------------------------------------------------------------------
# POST /settings/llm/provider-models/refresh — fetch live models from APIs
# ---------------------------------------------------------------------------

@router.post(
    "/llm/provider-models/refresh",
    response_model=list[RefreshResult],
)
async def refresh_provider_models(
    req: RefreshRequest,
    _user: UserContext = Depends(get_current_user),
) -> list[RefreshResult]:
    """Fetch the latest model list from each configured provider and cache it.

    Pass ``providers`` to refresh only specific providers, or omit / pass
    ``null`` to refresh all configured ones.  Results are written to
    infra/provider_models_cache.json and returned immediately.
    """
    import datetime

    # Determine which providers to refresh
    to_refresh: list[str] = req.providers or list(_PROVIDER_ENV_MAP.keys())
    # Always include openrouter (no key required)
    if "openrouter" not in to_refresh:
        to_refresh.append("openrouter")

    cache = _load_models_cache()
    results: list[RefreshResult] = []

    for provider in to_refresh:
        # Resolve API key from env
        env_var = _PROVIDER_ENV_MAP.get(provider, "")
        api_key: str | None = None
        if env_var:
            api_key = os.environ.get(env_var, "").strip() or None
            if not api_key:
                try:
                    api_key = (
                        getattr(get_settings(), env_var.lower(), "") or ""
                    ).strip() or None
                except Exception:
                    pass

        # Skip providers that need a key but don't have one (except openrouter)
        if provider != "openrouter" and provider not in ("ollama", "vllm") and not api_key:
            results.append(RefreshResult(
                provider=provider,
                count=0,
                fetched_at=datetime.datetime.now(
                    datetime.UTC
                ).isoformat(),
                error="No API key configured",
            ))
            continue

        fetched_at = datetime.datetime.now(
            datetime.UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            models = await _fetch_live_models(provider, api_key)
        except Exception as exc:
            models = []
            _log.warning(
                "provider_models.refresh_failed",
                provider=provider,
                error=str(exc),
            )
            results.append(RefreshResult(
                provider=provider,
                count=0,
                fetched_at=fetched_at,
                error=str(exc),
            ))
            continue

        if not models:
            # Live fetch returned nothing — keep previous cache entry if any
            prev = cache.get(provider, {})
            results.append(RefreshResult(
                provider=provider,
                count=len(prev.get("models", [])),
                fetched_at=fetched_at,
                error="No models returned from API",
            ))
            continue

        cache[provider] = {
            "fetched_at": fetched_at,
            "models": [m.model_dump() for m in models],
        }
        results.append(RefreshResult(
            provider=provider,
            count=len(models),
            fetched_at=fetched_at,
        ))
        _log.info(
            "provider_models.refreshed",
            provider=provider,
            count=len(models),
        )

    _save_models_cache(cache)
    return results


# ---------------------------------------------------------------------------
# GET /settings/llm/provider-models/cache-info — metadata about the cache
# ---------------------------------------------------------------------------

class CacheInfo(BaseModel):
    provider: str
    fetched_at: str | None
    count: int
    fresh: bool


@router.get(
    "/llm/provider-models/cache-info",
    response_model=list[CacheInfo],
)
async def get_cache_info(
    _user: UserContext = Depends(get_current_user),
) -> list[CacheInfo]:
    """Return cache metadata (last-fetched timestamp, model count, freshness)."""
    cache = _load_models_cache()
    result: list[CacheInfo] = []
    for provider in list(_PROVIDER_ENV_MAP.keys()) + ["openrouter"]:
        entry = cache.get(provider, {})
        result.append(CacheInfo(
            provider=provider,
            fetched_at=entry.get("fetched_at"),
            count=len(entry.get("models", [])),
            fresh=_cache_entry_fresh(entry),
        ))
    # Deduplicate (openrouter may appear twice)
    seen: set[str] = set()
    deduped: list[CacheInfo] = []
    for c in result:
        if c.provider not in seen:
            seen.add(c.provider)
            deduped.append(c)
    return deduped


# ---------------------------------------------------------------------------
# GET  /settings/appearance — the organisation's default look
# PUT  /settings/appearance — change it (admin only)
#
# The theming engine (workbench/control_plane/src/lib/theme/) lets each member
# pick a theme, but the ORG DEFAULT is what everyone who has not chosen sees,
# and it is the only way to move the whole company onto one look at once.
# That decision has to outlive a browser, so it lives here.
#
# WHAT THIS ROUTE DELIBERATELY DOES NOT KNOW
# ------------------------------------------
# Which themes exist. That list is `THEMES` in the frontend's themes.ts, and
# the whole point of the engine is that adding a theme is one manifest entry —
# if the gateway validated `theme_id` against a copy of that list, every new
# theme would need a backend deploy too, and a mismatch between the two lists
# would reject a theme the app can actually render.
#
# So `theme_id` is stored as an opaque identifier, constrained only to the
# shape that is safe to put in a CSS attribute selector (the same rule
# `buildThemeCss` enforces). The frontend re-validates on read and falls back
# to its default for an id it does not recognise — which it must do anyway,
# since a theme can be removed from the app long after this row named it.
#
# Personal preferences are NOT stored here. They are per-device, they change
# often, and round-tripping them would add a request to every page load to
# render something the pre-paint boot script has already applied from
# localStorage.
# ---------------------------------------------------------------------------

_APPEARANCE_KEY = "appearance"

#: Same rule as SAFE_ID in the frontend's css.ts: a theme id ends up inside
#: `html[data-theme="…"]`, so restrict it to characters needing no escaping.
_THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_MODES = ("dark", "light")
_DENSITIES = ("compact", "default", "comfortable")

_APPEARANCE_DEFAULTS: dict[str, Any] = {
    "themeId": "rapidtool",
    "mode": "dark",
    "density": "default",
    "allowUserOverride": True,
}


class OrgAppearance(BaseModel):
    """The organisation-wide default look."""

    themeId: str = _APPEARANCE_DEFAULTS["themeId"]
    mode: str = _APPEARANCE_DEFAULTS["mode"]
    density: str = _APPEARANCE_DEFAULTS["density"]
    allowUserOverride: bool = True


class AppearanceResponse(BaseModel):
    org: OrgAppearance
    #: Who last changed it and when — an org-wide setting affects everybody,
    #: so "who did this" is the first question when the company's UI changes.
    updatedBy: str = ""
    updatedAt: str = ""


def _coerce_appearance(raw: Any) -> tuple[OrgAppearance, str, str]:
    """Normalise a stored blob into a usable default.

    Every field falls back independently: a row written by an older or newer
    version of the app should degrade one key at a time rather than reset the
    whole organisation's look.
    """
    blob = raw if isinstance(raw, dict) else {}
    org = blob.get("org") if isinstance(blob.get("org"), dict) else blob

    theme_id = str(org.get("themeId") or "")
    if not _THEME_ID_RE.match(theme_id) or len(theme_id) > 64:
        theme_id = _APPEARANCE_DEFAULTS["themeId"]

    mode = org.get("mode")
    if mode not in _MODES:
        mode = _APPEARANCE_DEFAULTS["mode"]

    density = org.get("density")
    if density not in _DENSITIES:
        density = _APPEARANCE_DEFAULTS["density"]

    allow = org.get("allowUserOverride")
    if not isinstance(allow, bool):
        allow = _APPEARANCE_DEFAULTS["allowUserOverride"]

    return (
        OrgAppearance(
            themeId=theme_id, mode=mode, density=density, allowUserOverride=allow
        ),
        str(blob.get("updatedBy") or ""),
        str(blob.get("updatedAt") or ""),
    )


@router.get("/appearance", response_model=AppearanceResponse)
async def get_appearance(
    _user: UserContext = Depends(get_current_user),
) -> AppearanceResponse:
    """Return the org's default theme, mode and density.

    Readable by any signed-in member, not just admins: every client needs it on
    load to know what to render for someone who has not picked a theme.
    """
    from acb_common import load_org_setting  # noqa: PLC0415

    org, updated_by, updated_at = _coerce_appearance(
        load_org_setting(_APPEARANCE_KEY, default={})
    )
    return AppearanceResponse(org=org, updatedBy=updated_by, updatedAt=updated_at)


@router.put(
    "/appearance",
    response_model=AppearanceResponse,
    dependencies=[require_permission("admin:settings:manage")],
)
async def put_appearance(
    body: OrgAppearance,
    user: UserContext = Depends(get_current_user),
) -> AppearanceResponse:
    """Set the org's default look. Admin-gated — this changes everyone's UI."""
    from datetime import datetime, timezone  # noqa: PLC0415

    from acb_common import save_org_setting  # noqa: PLC0415

    # Re-validate rather than trusting the model: pydantic types `mode` and
    # `density` as plain strings (they are open vocabularies on the frontend),
    # so the enum check has to happen here or an arbitrary value reaches every
    # member's browser.
    if body.mode not in _MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(_MODES)}")
    if body.density not in _DENSITIES:
        raise HTTPException(
            status_code=400, detail=f"density must be one of {list(_DENSITIES)}"
        )
    if not _THEME_ID_RE.match(body.themeId) or len(body.themeId) > 64:
        raise HTTPException(
            status_code=400,
            detail="themeId must be lowercase alphanumeric with hyphens",
        )

    updated_at = datetime.now(timezone.utc).isoformat()
    save_org_setting(
        _APPEARANCE_KEY,
        {
            "org": body.model_dump(),
            "updatedBy": user.email or "",
            "updatedAt": updated_at,
        },
        updated_by=user.email or "",
    )
    _log.info(
        "settings.appearance.updated",
        theme_id=body.themeId,
        mode=body.mode,
        allow_user_override=body.allowUserOverride,
        by=user.email,
    )
    return AppearanceResponse(
        org=body, updatedBy=user.email or "", updatedAt=updated_at
    )


# ---------------------------------------------------------------------------
# GET    /settings/branding — the organisation's logo, for the app shell
# PUT    /settings/branding — replace it (admin only)
# DELETE /settings/branding — remove it (admin only)
#
# A customer's own logo sits top-left with "powered by Metorite" beneath
# it. That is the one place in the product where the tenant's identity, rather
# than ours, is the primary mark, so the rules around what may be stored there
# are worth stating in one place.
#
# WHY THE BYTES LIVE IN `org_settings` AND NOT IN OBJECT STORAGE
# --------------------------------------------------------------
# A logo is a single, small, org-wide document that changes perhaps twice in a
# company's life and is read on every page load. That is the same shape as the
# appearance blob directly above, and it is emphatically not the shape that
# justifies an object store: introducing one would mean a bucket per tenant, a
# signing path, a lifecycle policy and a second place a tenant's data can be
# left behind at deletion — for one row.
#
# ⚠️ **`org_settings` IS NOT TENANT-SCOPED TODAY, and this row will collide.**
# An earlier version of this comment claimed tenancy came free here because the
# table sat behind RLS like everything else. That was false and it was
# load-bearing — it was the stated reason the bytes live here. The facts:
# `151_org_settings.sql` keys the table on `key` ALONE and says so ("there is
# no per-tenant key namespace because this deployment is one organisation");
# `test_tenancy_boundary.py` lists org_settings in BASELINE_UNSCOPED; and
# `acb_common/org_settings.py` writes through a raw `psycopg.connect()` that
# binds no `app.tenant_id`, allow-listed in `test_psycopg_seam.py` as "a
# binding site the RLS work must convert, not a permanent exemption".
#
# So on the day a second organisation shares this database, tenant B's upload
# overwrites the one global 'branding' row and tenant A's members render
# tenant B's logo. After MT-1b promotion it fails the other way: the unbound
# connection reads zero rows under FORCE RLS and every org's logo silently
# vanishes. This is inherited from the `appearance` neighbour rather than
# introduced here — but branding is the first *content* placed in this table
# rather than a preference, and it is the most visible cross-tenant artifact
# the product has. **MT-1b must widen this PK and bind the tenant before a
# second customer is onboarded.** Tracked as a board finding, not fixed here:
# converting the org_settings seam is MT-1b's work, not this slice's.
#
# The cap below is what keeps that true. At 128 KiB a logo is a row, not a
# file; without a cap it becomes a file in a column, which is the failure mode
# this reasoning depends on avoiding.
#
# WHAT IS NOT TRUSTED
# -------------------
# The declared content type, the file name, and the dimensions the client
# claims. All three come from the caller. The MIME type served back to
# browsers is derived from the magic bytes by `probe_image`, and the data URI
# is *rebuilt* here from that derived type rather than echoed from the request
# — an echoed data URI is a stored-XSS sink wearing an image's clothes.
# ---------------------------------------------------------------------------

_BRANDING_KEY = "branding"

#: Raw upload ceiling, before base64. A 3× PNG wordmark is typically 15–40 KiB,
#: so this is generous; it exists to keep the row a row (see above) and to
#: bound what every member downloads on load.
_LOGO_MAX_BYTES = 128 * 1024

#: Below this the file is a favicon or an accident, not a logo.
_LOGO_MIN_EDGE = 32
#: Above this it is a print asset. The shell renders it ~28px tall.
_LOGO_MAX_EDGE = 2048

#: The header slot is a short horizontal strip. A 1:20 sliver technically fits
#: the size rules and renders as a 2px smear or shoves the nav off screen, so
#: shape is constrained as well as size: from twice as tall as wide, through to
#: eight times as wide as tall (which covers every real wordmark).
_LOGO_MIN_ASPECT = 0.5
_LOGO_MAX_ASPECT = 8.0


class OrgLogo(BaseModel):
    """A stored logo, as the shell needs it to render."""

    dataUri: str
    mime: str
    width: int
    height: int
    byteSize: int


class BrandingResponse(BaseModel):
    #: `None` means "no logo uploaded" — the shell falls back to our own mark.
    #: It is a distinct state from "the gateway is unreachable", which the BFF
    #: reports separately, because the two want different UI.
    logo: OrgLogo | None = None
    updatedBy: str = ""
    updatedAt: str = ""


class BrandingUpload(BaseModel):
    """The upload body: base64 of the raw file, and nothing else.

    Deliberately not a multipart form and deliberately not a data URI. A data
    URI carries a caller-declared MIME type, and every historical mistake in
    this area starts with trusting one.
    """

    logoBase64: str


def _coerce_branding(raw: Any) -> BrandingResponse:
    """Normalise a stored blob, degrading one field at a time.

    A row written by an older or newer build must not blank an organisation's
    branding wholesale; a logo that fails to parse simply reads as "no logo".
    """
    blob = raw if isinstance(raw, dict) else {}
    logo_raw = blob.get("logo")
    logo: OrgLogo | None = None
    if isinstance(logo_raw, dict):
        try:
            logo = OrgLogo(**logo_raw)
        except (TypeError, ValueError):
            logo = None
    return BrandingResponse(
        logo=logo,
        updatedBy=str(blob.get("updatedBy") or ""),
        updatedAt=str(blob.get("updatedAt") or ""),
    )


# `def`, not `async def`: `load_org_setting` is a synchronous DB call, so an
# async handler would block the event loop for its duration. FastAPI runs a
# sync handler in the threadpool, which is the correct shape for this seam.
# (The appearance handlers above predate that rule; they are not a precedent.)
@router.get("/branding", response_model=BrandingResponse)
def get_branding(
    _user: UserContext = Depends(get_current_user),
) -> BrandingResponse:
    """Return the org's logo. Readable by any signed-in member.

    Not admin-gated: every member's shell renders this, so gating the read
    would mean only admins saw their own company's branding.
    """
    from acb_common import load_org_setting  # noqa: PLC0415

    return _coerce_branding(load_org_setting(_BRANDING_KEY, default={}))


@router.put(
    "/branding",
    response_model=BrandingResponse,
    dependencies=[require_permission("admin:settings:manage")],
)
def put_branding(
    body: BrandingUpload,
    user: UserContext = Depends(get_current_user),
) -> BrandingResponse:
    """Replace the org's logo. Admin-gated — this changes everyone's shell."""
    import base64  # noqa: PLC0415
    import binascii  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    from acb_common import save_org_setting  # noqa: PLC0415

    from gateway.image_probe import UnsupportedImage, probe_image  # noqa: PLC0415

    payload = body.logoBase64.strip()
    # Accept a data URI prefix so a caller that pasted one is not stuck, but
    # discard it: only the bytes after the comma are ever looked at, and the
    # MIME type it declared is not read at all.
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")

    # Reject the encoded form before decoding, so a caller cannot make us
    # allocate 10 MiB to find out we did not want it. 4/3 plus padding.
    if len(payload) > (_LOGO_MAX_BYTES * 4 // 3) + 16:
        raise HTTPException(
            status_code=413,
            detail=f"That image is larger than {_LOGO_MAX_BYTES // 1024} KB.",
        )

    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="The upload was not valid base64.") from None

    if not data:
        raise HTTPException(status_code=400, detail="The upload was empty.")
    if len(data) > _LOGO_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That image is {len(data) // 1024} KB; the limit is "
            f"{_LOGO_MAX_BYTES // 1024} KB.",
        )

    try:
        info = probe_image(data)
    except UnsupportedImage as exc:
        # `probe_image`'s messages are written for the person who picked the
        # file, so they are relayed rather than replaced by a generic 400.
        raise HTTPException(status_code=400, detail=str(exc)) from None

    if info.width <= 0 or info.height <= 0:
        raise HTTPException(status_code=400, detail="That image looks damaged.")
    if max(info.width, info.height) < _LOGO_MIN_EDGE:
        raise HTTPException(
            status_code=400,
            detail=f"That image is only {info.width}×{info.height}. Logos need to "
            f"be at least {_LOGO_MIN_EDGE}px on the longer side.",
        )
    if max(info.width, info.height) > _LOGO_MAX_EDGE:
        raise HTTPException(
            status_code=400,
            detail=f"That image is {info.width}×{info.height}. The limit is "
            f"{_LOGO_MAX_EDGE}px on the longer side.",
        )

    aspect = info.width / info.height
    if aspect < _LOGO_MIN_ASPECT or aspect > _LOGO_MAX_ASPECT:
        raise HTTPException(
            status_code=400,
            detail="That shape will not fit the header. Logos need to be between "
            "twice as tall as they are wide and eight times as wide as they are "
            "tall.",
        )

    # Rebuilt from the DERIVED type and the DECODED bytes. Nothing the caller
    # sent is echoed into what browsers will later be handed.
    data_uri = f"data:{info.mime};base64,{base64.b64encode(data).decode('ascii')}"

    updated_at = datetime.now(timezone.utc).isoformat()
    logo = OrgLogo(
        dataUri=data_uri,
        mime=info.mime,
        width=info.width,
        height=info.height,
        byteSize=len(data),
    )
    save_org_setting(
        _BRANDING_KEY,
        {
            "logo": logo.model_dump(),
            "updatedBy": user.email or "",
            "updatedAt": updated_at,
        },
        updated_by=user.email or "",
    )
    _log.info(
        "settings.branding.updated",
        mime=info.mime,
        width=info.width,
        height=info.height,
        bytes=len(data),
        by=user.email,
    )
    return BrandingResponse(
        logo=logo, updatedBy=user.email or "", updatedAt=updated_at
    )


@router.delete(
    "/branding",
    response_model=BrandingResponse,
    dependencies=[require_permission("admin:settings:manage")],
)
def delete_branding(
    user: UserContext = Depends(get_current_user),
) -> BrandingResponse:
    """Remove the logo and fall back to our own mark. Admin-gated."""
    from datetime import datetime, timezone  # noqa: PLC0415

    from acb_common import save_org_setting  # noqa: PLC0415

    updated_at = datetime.now(timezone.utc).isoformat()
    # The row is overwritten rather than deleted, so `updatedBy` still answers
    # "who removed our logo" — the same question the appearance blob keeps an
    # author for, and the one asked first when a company's shell changes.
    save_org_setting(
        _BRANDING_KEY,
        {"logo": None, "updatedBy": user.email or "", "updatedAt": updated_at},
        updated_by=user.email or "",
    )
    _log.info("settings.branding.cleared", by=user.email)
    return BrandingResponse(logo=None, updatedBy=user.email or "", updatedAt=updated_at)
