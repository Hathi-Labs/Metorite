"""Integration management endpoints.

Endpoints
---------
GET  /integrations/status?agent={name}
    Returns per-integration status (configured / missing) for a given agent.
    Also returns setup guides (links, required env vars) for unconfigured ones.

POST /integrations/configure
    Writes one or more credentials to the .env file and hot-reloads Settings.
    Restricted to admin/executive role.

GET  /integrations/test?service={name}
    Performs a lightweight connectivity check for a configured integration.
    Returns {service, ok, detail}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
from acb_auth import UserContext, UserRole, get_current_user, require_feature_router, require_role
from acb_common import get_logger, get_settings
from fastapi import APIRouter, Depends, HTTPException, status
from gateway.db import current_tenant
from pydantic import BaseModel
from sqlalchemy import text

_log = get_logger("gateway.integrations")

router = APIRouter(
    prefix="/integrations", tags=["integrations"],
    dependencies=[require_feature_router("integrations")],
)

# ── MT-1j slice 5 · the tenant on this surface ─────────────────────────
#
# Every `provider_keys` call in this module passes
# ``organization_id=current_tenant()`` — the SAME idiom `routes/settings.py`
# uses, not a second one (`saas_multitenancy.md` §11 MT-1j slice 5 ratchet
# round 2 · R5 · `user_management_contract.md` R11). The value comes from one
# place: the tenant `acb_auth.deps._with_resolved_access` bound from the
# authenticated session. Never from `req.service`, a header, a query parameter
# or any other request input — an org taken from the request is R11's forbidden
# move and would let a caller name somebody else's tenant.
#
# Only `/integrations/oauth/callback/{service}` is in `main.PUBLIC_ROUTES`, and
# it touches none of these calls; the five threaded sites are all reached
# through the app-wide `require_authenticated`, so the binding is in context.
#
# ``None`` outside a bound session is deliberate and is NOT a fallback: it hands
# `key_store._resolve_org` exactly the argument it receives today, so MT-0d's
# sole-org resolution and its fail-closed arm at org #2 are unchanged. What
# changes is that an authenticated caller now resolves to THEIR organization.
#
# 🚫 **The `os.environ[...] = value` / `_upsert_env_var(env_path, ...)` halves of
# `configure_integrations`, `put_integration_key` and `delete_integration_key`
# are NOT tenantable and are a known cross-tenant write** — the same class as
# `settings.py::_inject_env_into_litellm`. A process-global env var and a single
# `.env` file have one value for the whole deployment, so org #2 saving
# `ZOHO_CLIENT_ID` overwrites org #1's for every caller and every agent reading
# it through `get_settings()`. Recorded here, deliberately NOT repaired: the fix
# is per-request provider credentials, which is the same owner-gated
# credential-scope change the H4 sites wait on (`work_plan.md` §6 gate (f)).
# `_is_configured` and `missing_keys` below read those env vars, so
# `/integrations/status`'s `configured` / `env-file` columns stay deployment-wide
# too — only the `db_keys` / `encrypted-db` half is per organization.

# ---------------------------------------------------------------------------
# Static setup guides — one entry per registered integration.
# Surfaced to the Control Plane wizard so users get direct links & instructions.
# ---------------------------------------------------------------------------

_SETUP_GUIDES: dict[str, dict[str, Any]] = {
    "zoho-crm": {
        "label": "Zoho CRM",
        "description": "Deal management, contacts, accounts, and pipeline.",
        "setup_url": "https://accounts.zoho.in/developerconsole",
        "docs_url": "https://www.zoho.com/crm/developer/docs/api/v5/oauth-overview.html",
        "instructions": (
            "1. Go to Zoho Developer Console → Add Client → Self Client.\n"
            "2. Generate a code with scope: ZohoCRM.modules.ALL,ZohoCRM.settings.ALL\n"
            "3. Exchange the code for tokens (one-time). Copy the refresh_token.\n"
            "4. Copy Client ID and Client Secret from the app settings."
        ),
        "env_vars": [
            {"key": "ZOHO_CLIENT_ID", "label": "Client ID", "sensitive": False},
            {"key": "ZOHO_CLIENT_SECRET", "label": "Client Secret", "sensitive": True},
            {"key": "ZOHO_REFRESH_TOKEN", "label": "Refresh Token", "sensitive": True},
            {"key": "ZOHO_API_DOMAIN", "label": "API Domain (default: zohoapis.com)", "sensitive": False},
            {"key": "ZOHO_ACCOUNTS_URL", "label": "Accounts URL (default: accounts.zoho.com)", "sensitive": False},
            {"key": "ZOHO_REGION", "label": "Region (in/eu/us, default: in)", "sensitive": False},
        ],
    },
    "apollo": {
        "label": "Apollo.io",
        "description": "Contact enrichment and prospecting database.",
        "setup_url": "https://developer.apollo.io/keys/",
        "docs_url": "https://apolloio.github.io/apollo-api-docs/",
        "instructions": (
            "1. Log in to Apollo.io.\n"
            "2. Navigate to Settings → Integrations → API Keys.\n"
            "3. Copy the Master API Key."
        ),
        "env_vars": [
            {"key": "APOLLO_API_KEY", "label": "Master API Key", "sensitive": True},
        ],
    },
    "google-maps": {
        "label": "Google Maps / Places API",
        "description": "Business discovery and location data for prospecting.",
        "setup_url": "https://console.cloud.google.com/apis/credentials",
        "docs_url": "https://developers.google.com/maps/documentation/places/web-service/overview",
        "instructions": (
            "1. Open Google Cloud Console → APIs & Services → Enable 'Places API (New)'.\n"
            "2. Go to Credentials → Create Credentials → API Key.\n"
            "3. Restrict the key to Places API for security."
        ),
        "env_vars": [
            {"key": "GOOGLE_MAPS_API_KEY", "label": "API Key", "sensitive": True},
        ],
    },
    "instantly": {
        "label": "Instantly.ai",
        "description": "Cold email sequencing and outbound campaign management.",
        "setup_url": "https://app.instantly.ai/app/settings/integrations",
        "docs_url": "https://developer.instantly.ai/",
        "instructions": (
            "1. Log in to Instantly.ai.\n"
            "2. Go to Settings → API Keys.\n"
            "3. Copy your API key (or create a new one)."
        ),
        "env_vars": [
            {"key": "INSTANTLY_API_KEY", "label": "API Key", "sensitive": True},
        ],
    },
    "gmail": {
        "label": "Gmail (Google Workspace)",
        "description": "Read and send email via service-account impersonation.",
        "setup_url": "https://console.cloud.google.com/iam-admin/serviceaccounts",
        "docs_url": "https://developers.google.com/gmail/api/guides/",
        "instructions": (
            "1. Create a service account in Google Cloud Console.\n"
            "2. Enable domain-wide delegation for the service account.\n"
            "3. In Google Workspace Admin, grant the service account access to Gmail scopes.\n"
            "4. Download the JSON key file and set its path below."
        ),
        "env_vars": [
            {"key": "GMAIL_SA_JSON_PATH", "label": "Service Account Key Path", "sensitive": False},
            {"key": "GMAIL_DEFAULT_USER", "label": "Default Mailbox (e.g. you@domain.com)", "sensitive": False},
        ],
    },
    "gmail-send": {
        "label": "Gmail (send only)",
        "description": "Outbound email send via Google Workspace.",
        "setup_url": "https://console.cloud.google.com/iam-admin/serviceaccounts",
        "docs_url": "https://developers.google.com/gmail/api/guides/",
        "instructions": "Same setup as Gmail — service account + domain-wide delegation.",
        "env_vars": [
            {"key": "GMAIL_SA_JSON_PATH", "label": "Service Account Key Path", "sensitive": False},
            {"key": "GMAIL_DEFAULT_USER", "label": "Default Mailbox", "sensitive": False},
        ],
    },
    "whatsapp": {
        "label": "WhatsApp Business",
        "description": "Manage WhatsApp Business numbers via Meta's official "
        "Cloud API. Connect one or more numbers — each stored as its own "
        "per-account encrypted token, so several numbers coexist.",
        "setup_url": "https://business.facebook.com/wa/manage/",
        "docs_url": "https://developers.facebook.com/docs/whatsapp/cloud-api",
        "instructions": (
            "WhatsApp connects per-number (multi-account) via a guided wizard:\n"
            "1. Open the WhatsApp app → Connect a number (one-click Embedded "
            "Signup, or paste your phone-number id + token).\n"
            "2. The wizard live-tests the credentials against Meta before "
            "saving. Repeat to add more numbers."
        ),
        # NO env_vars: WhatsApp is multi-account (wa_accounts,
        # per-account encrypted tokens) via /whatsapp/accounts — not a
        # process-wide credential. The APIs tab renders a dedicated connector
        # (list numbers + disconnect + a link to the connect wizard).
        "env_vars": [],
    },
    "smtp": {
        "label": "SMTP (email relay)",
        "description": "Generic outbound email via SMTP.",
        "setup_url": "",
        "docs_url": "",
        "instructions": "Set your SMTP server host, port, username, and password.",
        "env_vars": [
            {"key": "SMTP_HOST", "label": "Host", "sensitive": False},
            {"key": "SMTP_PORT", "label": "Port (default 587)", "sensitive": False},
            {"key": "SMTP_USERNAME", "label": "Username", "sensitive": False},
            {"key": "SMTP_PASSWORD", "label": "Password", "sensitive": True},
        ],
    },
    "github": {
        "label": "GitHub",
        "description": "Private agent repo cloning and Copilot LLM models (GPT-4o, Claude Sonnet, o3-mini).",
        # Two ways to authenticate — device flow (OAuth) or a PAT.
        # A PAT with copilot+repo scopes covers both repo access AND the Copilot Models API.
        "uses": ["Repo access", "Models"],
        "setup_url": "https://github.com/settings/tokens/new?scopes=copilot,repo",
        "docs_url": "https://docs.github.com/en/copilot/using-github-copilot/ai-models",
        "instructions": (
            "One token covers both repo access and Copilot LLM models.\n\n"
            "Option A — Personal Access Token (recommended):\n"
            "1. Open the link above → GitHub → Settings → Developer Settings\n"
            "   → Personal Access Tokens → Tokens (classic).\n"
            "2. Select scopes: `copilot` (LLM model API) and `repo` (private repos).\n"
            "3. Click 'Generate token', paste the value (ghp_...) below.\n\n"
            "Option B — OAuth Device Flow (no token entry needed):\n"
            "1. Create a GitHub OAuth App (Settings → Developer Settings → OAuth Apps).\n"
            "   Name: 'Metorite', Callback URL: http://localhost.\n"
            "2. Paste the Client ID and click 'Connect GitHub Account'.\n"
            "   Note: device-flow tokens may not include the `copilot` scope —\n"
            "   use Option A if you want Copilot model access."
        ),
        "env_vars": [
            # GITHUB_TOKEN (Option A — recommended): classic or fine-grained PAT
            # with copilot + repo scopes.  Shown first because it's the simpler,
            # self-contained path.
            {"key": "GITHUB_TOKEN", "label": "Personal Access Token — copilot + repo scopes (recommended)", "sensitive": True},
            # GITHUB_CLIENT_ID is used for the OAuth device flow (Option B).
            # Only needed if you prefer the device-flow UX over a PAT.
            {"key": "GITHUB_CLIENT_ID", "label": "OAuth App Client ID (Option B — device flow)", "sensitive": False},
        ],
    },
    "serpapi": {
        "label": "SerpAPI (Google Search)",
        "description": "Real-time Google search results for research and discovery.",
        "setup_url": "https://serpapi.com/dashboard",
        "docs_url": "https://serpapi.com/search-api",
        "instructions": (
            "1. Log in to SerpAPI.\n"
            "2. Go to Dashboard — your API key is shown at the top.\n"
            "3. Copy the key."
        ),
        "env_vars": [
            {"key": "SERPAPI_API_KEY", "label": "API Key", "sensitive": True},
        ],
    },
    "apify": {
        "label": "Apify (web scraping)",
        "description": "Web scraping and automation actors for data extraction.",
        "setup_url": "https://console.apify.com/account/integrations",
        "docs_url": "https://docs.apify.com/api/v2",
        "instructions": (
            "1. Log in to Apify Console.\n"
            "2. Navigate to Account → Integrations.\n"
            "3. Copy your Personal API Token."
        ),
        "env_vars": [
            {"key": "APIFY_API_TOKEN", "label": "Personal API Token", "sensitive": True},
        ],
    },
    "anymailfinder": {
        "label": "AnyMailFinder",
        "description": "Find and verify professional email addresses.",
        "setup_url": "https://anymailfinder.com/account",
        "docs_url": "https://anymailfinder.com/docs",
        "instructions": (
            "1. Log in to AnyMailFinder.\n"
            "2. Go to Account → API Key.\n"
            "3. Copy the API key."
        ),
        "env_vars": [
            {"key": "ANYMAILFINDER_API_KEY", "label": "API Key", "sensitive": True},
        ],
    },
    "gmail-oauth": {
        "label": "Gmail OAuth (user sign-in)",
        "description": "Let users connect their personal Gmail accounts via Google sign-in. Required for the Email app.",
        "setup_url": "https://console.cloud.google.com/apis/credentials",
        "docs_url": "https://developers.google.com/gmail/api/guides/oauth-installed-app",
        "instructions": (
            "1. Go to Google Cloud Console → APIs & Services → Credentials.\n"
            "2. Click 'Create Credentials' → 'OAuth client ID'.\n"
            "3. Choose 'Web application'.\n"
            "4. Add Authorized redirect URI:\n"
            "   https://api.metorite.com/email/oauth/gmail/callback\n"
            "   (or your gateway's public URL).\n"
            "5. Copy the Client ID and Client Secret below.\n"
            "6. Also enable the Gmail API under 'Enabled APIs & Services'."
        ),
        "env_vars": [
            {"key": "GMAIL_OAUTH_CLIENT_ID", "label": "Client ID", "sensitive": False},
            {"key": "GMAIL_OAUTH_CLIENT_SECRET", "label": "Client Secret", "sensitive": True},
        ],
    },
    "microsoft-oauth": {
        "label": "Microsoft OAuth (Email)",
        "description": (
            "Let users connect their Outlook / Microsoft 365 accounts. "
            "Reuses your existing sign-in Azure app registration (AUTH_MICROSOFT_ENTRA_ID_*) "
            "— just add the Email redirect URI and Mail.ReadWrite permission to it."
        ),
        "setup_url": "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        "docs_url": "https://learn.microsoft.com/en-us/graph/auth-v2-user",
        "instructions": (
            "If you already have a sign-in app registration (AUTH_MICROSOFT_ENTRA_ID_*), "
            "you can reuse it — no separate registration needed. Just add these to it:\n\n"
            "1. Go to Azure Portal → App registrations → your existing Metorite app.\n"
            "2. Under 'Authentication', add Redirect URI (Web):\n"
            "   https://api.metorite.com/email/oauth/microsoft/callback\n"
            "3. Under 'API Permissions', add Microsoft Graph → Delegated:\n"
            "   Mail.ReadWrite, User.Read\n"
            "4. Click 'Grant admin consent' if tenant-restricted.\n\n"
            "If you are NOT using the same app registration, create a new one:\n"
            "1. App registrations → New registration.\n"
            "2. Name: 'Metorite Email'. Supported account types: 'Any organizational directory and personal Microsoft accounts'.\n"
            "3. Add Redirect URI (Web) as above.\n"
            "4. Copy the Application (client) ID below.\n"
            "5. Go to 'Certificates & secrets' → New client secret, copy it below.\n"
            "6. Under 'API Permissions', add Microsoft Graph → Delegated:\n"
            "   Mail.ReadWrite, User.Read"
        ),
        "env_vars": [
            {"key": "MSFT_OAUTH_CLIENT_ID", "label": "Application (client) ID (optional — falls back to AUTH_MICROSOFT_ENTRA_ID_ID)", "sensitive": False},
            {"key": "MSFT_OAUTH_CLIENT_SECRET", "label": "Client Secret (optional — falls back to AUTH_MICROSOFT_ENTRA_ID_SECRET)", "sensitive": True},
            {"key": "MICROSOFT_TENANT_ID", "label": "Directory (tenant) ID — required for single-tenant apps. Copy from Azure Overview tab. Leave blank for multi-tenant.", "sensitive": False},
        ],
    },
    "google-sheets": {
        "label": "Google Sheets",
        "description": "Read and write Google Sheets for data export and reporting.",
        "setup_url": "https://console.cloud.google.com/iam-admin/serviceaccounts",
        "docs_url": "https://developers.google.com/sheets/api/guides/authorizing",
        "instructions": (
            "1. Create a service account in Google Cloud Console.\n"
            "2. Enable the Google Sheets API for your project.\n"
            "3. Share your target Sheets with the service account email.\n"
            "4. Download the JSON key file and set its path below.\n"
            "   (You can reuse the same service account key as Gmail if already set up.)"
        ),
        "env_vars": [
            {"key": "GOOGLE_SHEETS_SA_JSON_PATH", "label": "Service Account Key Path (JSON file)", "sensitive": False},
        ],
    },
}


# ---------------------------------------------------------------------------
# Service categories — drives tile grid + filter tabs on the frontend
# ---------------------------------------------------------------------------

_GUIDE_CATEGORIES: dict[str, str] = {
    "github":           "core",
    "gmail-oauth":      "communication",
    "microsoft-oauth":  "communication",
    "zoho-crm":         "crm",
    "apollo":         "prospecting",
    "google-maps":    "prospecting",
    "instantly":      "email",
    "gmail":          "email",
    "gmail-send":     "email",
    "whatsapp":       "communication",
    "smtp":           "email",
    "serpapi":        "search",
    "apify":          "search",
    "anymailfinder":  "prospecting",
    "google-sheets":  "productivity",
}


async def _db_query(sql: str, **params: Any) -> list[dict[str, Any]]:
    """Execute SQL via the key store's Postgres connection."""
    from acb_llm.key_store import get_key_store  # noqa: PLC0415
    return await get_key_store()._execute(sql, **params)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_configured(service_name: str, settings: Any) -> bool:
    """Return True if all required env vars for *service_name* are non-empty."""
    checkers: dict[str, Any] = {
        "zoho-crm":      lambda s: bool(s.zoho_client_id and s.zoho_client_secret and s.zoho_refresh_token),
        "apollo":        lambda s: bool(s.apollo_api_key),
        "google-maps":   lambda s: bool(s.google_maps_api_key),
        "instantly":     lambda s: bool(s.instantly_api_key),
        "gmail":           lambda s: bool(s.gmail_sa_json_path),
        "gmail-send":      lambda s: bool(s.gmail_sa_json_path),
        "gmail-oauth":     lambda s: bool(s.gmail_oauth_client_id and s.gmail_oauth_client_secret),
        "microsoft-oauth": lambda s: bool(
            (s.msft_oauth_client_id and s.msft_oauth_client_secret)
            or (os.getenv("AUTH_MICROSOFT_ENTRA_ID_ID") and os.getenv("AUTH_MICROSOFT_ENTRA_ID_SECRET"))
        ),
        # WhatsApp is multi-account (wa_accounts) — its "connected" state is
        # NOT a process-wide env token, so the server-side env check is
        # deliberately False and the APIs-tab tile overrides `configured` from
        # the connected-number count on the client.
        "whatsapp":      lambda s: False,

        "smtp":          lambda s: bool(s.smtp_host and s.smtp_username),
        "github":        lambda s: bool(s.github_token),
        "serpapi":       lambda s: bool(getattr(s, "serpapi_api_key", "") or os.getenv("SERPAPI_API_KEY", "")),
        "apify":         lambda s: bool(getattr(s, "apify_api_token", "") or os.getenv("APIFY_API_TOKEN", "")),
        "anymailfinder": lambda s: bool(getattr(s, "anymailfinder_api_key", "") or os.getenv("ANYMAILFINDER_API_KEY", "")),
        "google-sheets": lambda s: bool(
            getattr(s, "google_sheets_sa_json_path", "") or os.getenv("GOOGLE_SHEETS_SA_JSON_PATH", "")
            or getattr(s, "gmail_sa_json_path", "") or os.getenv("GMAIL_SA_JSON_PATH", "")
        ),
    }
    checker = checkers.get(service_name)
    if checker is None:
        return True  # Unknown service — assume configured (don't block)
    try:
        return checker(settings)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# .env file writer (dev / bare-metal deployments)
# ---------------------------------------------------------------------------

# Allowed env var keys — only these may be written via the API.
# Derived from all env_vars in _SETUP_GUIDES (business integrations) PLUS
# the LLM provider keys used by Settings → Models.  This allows the
# /integrations/configure endpoint to serve as the fallback write path for
# LLM provider keys when the gateway is running code that predates the
# /settings/llm/key endpoint's knowledge of those providers.
_ALLOWED_ENV_KEYS: frozenset[str] = frozenset(
    var["key"]
    for guide in _SETUP_GUIDES.values()
    for var in guide["env_vars"]
) | frozenset({
    # LLM provider keys (Settings → Models page)
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "TOGETHER_API_KEY",
    "VLLM_BASE_URL",
    "LITELLM_MASTER_KEY",
    "COPILOT_CHAT_MODEL",
})


def _find_env_file() -> Path:
    """Locate the repo-root .env file (next to the workspace pyproject.toml)."""
    # Walk up from this file's location.  The WORKSPACE root pyproject.toml
    # contains [tool.uv.workspace], distinguishing it from package-level ones.
    candidate = Path(__file__).resolve()
    for _ in range(10):
        candidate = candidate.parent
        pyproject = candidate / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text(encoding="utf-8")
                if "[tool.uv.workspace]" in content:
                    return candidate / ".env"
            except OSError:
                pass
    # Fallback: cwd
    return Path.cwd() / ".env"


def _upsert_env_var(env_path: Path, key: str, value: str) -> None:
    """Add or update KEY=VALUE in the .env file.  Creates the file if absent."""
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated = False
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Match KEY= or KEY =  (with optional surrounding quotes / spaces)
        if re.match(rf"^{re.escape(key)}\s*=", stripped):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class IntegrationVar(BaseModel):
    key: str
    value: str


class ConfigureRequest(BaseModel):
    vars: list[IntegrationVar]


class DevicePollRequest(BaseModel):
    device_code: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
async def integration_status(
    agent: str | None = None,
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return integration status for a named agent (or all integrations).

    Query params:
        agent: bare agent name (e.g. "sales-prospector").
               If omitted, returns status for all registered integrations.

    Response items:
        {service, label, configured, description, setup_url, docs_url,
         instructions, env_vars, missing_keys}
    """
    settings = get_settings()

    # Determine which services to check
    if agent:
        from gateway.routes.agent import _AGENT_REGISTRY  # noqa: PLC0415
        from gateway.routes.agent import _load_dynamic_agents

        # Search static registry first, then dynamic agents from agents.json
        entry = next((e for e in _AGENT_REGISTRY if e["name"] == agent), None)
        if entry is None:
            entry = next((e for e in _load_dynamic_agents() if e["name"] == agent), None)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent {agent!r} not in registry.",
            )
        agent_services = entry.get("integrations", [])
        optional_services: set[str] = set(entry.get("optional_integrations", []))
        # github is always a system-level prerequisite — every agent needs repo cloning.
        # Show it first so users set it up before other integrations.
        services = ["github"] + [s for s in agent_services if s != "github"]
        # Include optional integrations at the end
        for svc in entry.get("optional_integrations", []):
            if svc not in services:
                services.append(svc)
    else:
        services = list(_SETUP_GUIDES.keys())
        optional_services = set()

    # Fetch all integration keys from the encrypted DB store for source tracking
    try:
        from acb_llm.key_store import get_key_store
        store = get_key_store()
        db_keys_by_service: dict[str, list[str]] = {}
        all_int_keys = await store.get_by_type(
            "integration", organization_id=current_tenant()
        )
        for provider in all_int_keys:
            if ":" in provider:
                svc, suffix = provider.split(":", 1)
                db_keys_by_service.setdefault(svc, []).append(suffix)
    except Exception:
        db_keys_by_service = {}

    result = []
    for svc in services:
        guide = _SETUP_GUIDES.get(svc, {})
        configured = _is_configured(svc, settings)
        # For GitHub, always compute missing_keys from actual env vars
        # because the service can be "configured" via PAT (GITHUB_TOKEN)
        # alone, but the OAuth device flow (Option B) needs
        # GITHUB_CLIENT_ID separately.  Without this, a configured PAT
        # hides the Client ID input and the device-flow start returns 422.
        if svc == "github":
            missing_keys = [
                v["key"]
                for v in guide.get("env_vars", [])
                if not os.getenv(v["key"], "").strip()
            ]
        else:
            missing_keys = (
                []
                if configured
                else [
                    v["key"]
                    for v in guide.get("env_vars", [])
                    if not os.getenv(v["key"], "").strip()
                ]
            )

        # Determine which keys are stored in the encrypted DB
        db_stored_keys = db_keys_by_service.get(svc, [])

        # Determine storage source for display
        if db_stored_keys:
            storage = "encrypted-db"
        elif configured:
            storage = "env-file"
        else:
            storage = "none"

        result.append(
            {
                "service": svc,
                "label": guide.get("label", svc),
                "configured": configured,
                "mandatory": svc not in optional_services,
                "description": guide.get("description", ""),
                "uses": guide.get("uses", []),
                "setup_url": guide.get("setup_url", ""),
                "docs_url": guide.get("docs_url", ""),
                "instructions": guide.get("instructions", ""),
                "env_vars": guide.get("env_vars", []),
                "missing_keys": missing_keys,
                "db_keys": db_stored_keys,
                "storage": storage,
                "category": _GUIDE_CATEGORIES.get(svc, "custom"),
                "is_custom": False,
            }
        )

    # Merge user-added custom API definitions from DB
    try:
        custom_rows = await _db_query(
            "SELECT * FROM custom_api_definitions ORDER BY created_at"
        )
        for row in custom_rows:
            svc = row["service_id"]
            if any(r["service"] == svc for r in result):
                continue  # skip if service_id conflicts with built-in
            env_vars = row.get("env_vars") or []
            configured = bool(
                env_vars
                and all(os.getenv(v.get("key", ""), "") for v in env_vars if v.get("key"))
            )
            missing = [
                v["key"] for v in env_vars
                if not os.getenv(v.get("key", ""), "")
            ]
            result.append({
                "service": svc,
                "label": row.get("label", svc),
                "configured": configured,
                "mandatory": False,
                "description": row.get("description", ""),
                "domain": row.get("domain", ""),
                "uses": [],
                "setup_url": row.get("setup_url", ""),
                "docs_url": row.get("docs_url", ""),
                "instructions": row.get("instructions", ""),
                "env_vars": env_vars,
                "missing_keys": missing,
                "db_keys": [],
                "storage": "none" if not configured else "env-file",
                "category": row.get("category", "custom"),
                "is_custom": True,
            })
    except Exception:
        pass  # graceful degradation if table doesn't exist yet

    return result


@router.post(
    "/configure",
    dependencies=[require_role(UserRole.EXECUTIVE, UserRole.AGENT)],
)
async def configure_integrations(
    req: ConfigureRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Write integration credentials to the encrypted DB credential store.

    Credentials are encrypted at rest with the same ACB_MASTER_KEY used
    for LLM provider keys.  They are also set in os.environ for immediate
    effect and written to .env as a bootstrap fallback.

    Only keys present in the allowed list (derived from _SETUP_GUIDES) may
    be written — all others are rejected with 422.
    """
    # Validate all keys — allow known keys OR any valid SCREAMING_SNAKE_CASE name
    # (custom APIs define their own env var names)
    _env_var_re = re.compile(r"^[A-Z][A-Z0-9_]{1,100}$")
    illegal = [
        v.key for v in req.vars
        if v.key not in _ALLOWED_ENV_KEYS and not _env_var_re.match(v.key)
    ]
    if illegal:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid env var key(s): {illegal}. "
                "Keys must be SCREAMING_SNAKE_CASE (A-Z, 0-9, underscore)."
            ),
        )

    # ── BYOK off: this is the SECOND door to a provider key ────────────────
    #
    # `api/settings/llm/key/route.ts` falls back to THIS endpoint whenever
    # `POST /settings/llm/key` answers "No env var for provider". Guarding
    # only the front door would leave that fallback wide open, and the
    # fallback writes an arbitrary env var by design.
    #
    # Only the LLM provider variables are refused. Every other integration
    # credential (Slack, GitHub OAuth, WhatsApp and the rest) is untouched,
    # because BYOK is about who supplies the MODEL, not about integrations.
    from gateway.routes.settings import _PROVIDER_ENV_MAP, refuse_if_byok_disabled

    _provider_vars = {v for v in _PROVIDER_ENV_MAP.values() if v}
    if any(v.key in _provider_vars for v in req.vars):
        refuse_if_byok_disabled()


    # Build reverse mapping: env_var → (service, suffix)
    _env_to_service_suffix: dict[str, tuple[str, str]] = {}
    for svc, guide in _SETUP_GUIDES.items():
        for var in guide["env_vars"]:
            suffix = var["key"].lower().removeprefix(f"{svc}_".upper().lower()) \
                .replace("-", "_")
            _env_to_service_suffix[var["key"]] = (svc, suffix)

    env_path = _find_env_file()
    written: list[str] = []
    db_written: list[str] = []

    for var in req.vars:
        if not var.value.strip():
            continue  # skip empties

        # 1. Write to encrypted DB store (primary persistence)
        svc_suffix = _env_to_service_suffix.get(var.key)
        if svc_suffix:
            svc, suffix = svc_suffix
            provider = f"{svc}:{suffix}"
            try:
                from acb_llm.key_store import get_key_store
                store = get_key_store()
                await store.put(
                    provider,
                    var.value,
                    credential_type="integration",
                    service=svc,
                    organization_id=current_tenant(),
                )
                db_written.append(provider)
            except Exception as exc:
                _log.warning(
                    "integrations.db_write_failed",
                    key=var.key,
                    provider=provider,
                    error=str(exc),
                )

        # 2. Set in current process env (immediate effect)
        # ⚠️ CROSS-TENANT WRITE, recorded and deliberately not repaired —
        # see the MT-1j slice 5 block at the top of this module. Steps 2
        # and 3 have one value for the whole deployment; only step 1 above
        # is per organization.
        os.environ[var.key] = var.value

        # 3. Write to .env as bootstrap fallback (still useful for
        #    bare-metal dev and first-boot before DB is available)
        _upsert_env_var(env_path, var.key, var.value)

        written.append(var.key)
        _log.info(
            "integrations.configure",
            key=var.key,
            actor=user.email,
        )

    # Bust the settings LRU cache so the live process reads new values
    from acb_common.settings import get_settings as _gs  # noqa: PLC0415
    _gs.cache_clear()

    return {
        "written": written,
        "db_stored": db_written,
        "env_file": str(env_path),
        "reload": "Settings cache cleared — new values active immediately.",
        "storage": (
            "Credentials stored in encrypted Postgres (primary) + "
            ".env (bootstrap fallback)."
        ),
    }


# ---------------------------------------------------------------------------
# /integrations/keys — individual credential CRUD (mirrors /settings/llm/key)
# ---------------------------------------------------------------------------

class IntegrationKeyRequest(BaseModel):
    service: str        # e.g. "zoho-crm", "apollo"
    key_name: str       # e.g. "client_id", "api_token", "api_key"
    value: str          # plain-text credential value


class IntegrationKeyDelete(BaseModel):
    service: str
    key_name: str


@router.get("/keys")
async def list_integration_keys(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """List all stored integration credentials (values NOT revealed).

    Returns {service: [key_names], ...} — which services have which keys
    stored in the encrypted DB.  Values are never returned by this endpoint.
    """
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    all_integration_keys = await store.get_by_type(
        "integration", organization_id=current_tenant()
    )

    # Group by service
    by_service: dict[str, list[str]] = {}
    for provider in all_integration_keys:
        if ":" in provider:
            svc, suffix = provider.split(":", 1)
            by_service.setdefault(svc, []).append(suffix)

    return {
        "services": by_service,
        "total_keys": len(all_integration_keys),
        "storage": "encrypted-postgres",
    }


@router.put("/keys")
async def put_integration_key(
    req: IntegrationKeyRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Store or update a single integration credential in the encrypted DB.

    Also sets the corresponding os.environ variable for immediate effect
    and writes to .env as a bootstrap fallback.
    """
    if not req.value.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="value cannot be empty",
        )

    # Validate the service is known
    if req.service not in _SETUP_GUIDES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown service: {req.service!r}. "
                f"Known: {sorted(_SETUP_GUIDES.keys())}"
            ),
        )

    # Find the env var for this key
    guide = _SETUP_GUIDES[req.service]
    env_var = None
    for var in guide["env_vars"]:
        suffix = var["key"].lower().removeprefix(
            f"{req.service}_".upper().lower()
        ).replace("-", "_")
        if suffix == req.key_name:
            env_var = var["key"]
            break

    if env_var is None:
        known_keys = [
            v["key"].lower().removeprefix(
                f"{req.service}_".upper().lower()
            ).replace("-", "_")
            for v in guide["env_vars"]
        ]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown key_name {req.key_name!r} for service "
                f"{req.service!r}. Known keys: {known_keys}"
            ),
        )

    # Store in encrypted DB
    provider = f"{req.service}:{req.key_name}"
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    await store.put(
        provider,
        req.value,
        credential_type="integration",
        service=req.service,
        organization_id=current_tenant(),
    )

    # Set in current process env (immediate effect)
    # ⚠️ CROSS-TENANT WRITE, recorded and deliberately not repaired — see the
    # MT-1j slice 5 block at the top of this module. The store write above is
    # per organization; these two are deployment-wide.
    #
    # ⚠️ And on THIS route they are newly REACHABLE, which is not true of the
    # store half. Before the tenant was threaded, the untenanted `store.put`
    # above raised (`_resolve_org` → "" → RuntimeError) the moment a second
    # organization existed, so the handler 500'd BEFORE reaching these lines —
    # an accidental fail-closed arm, not a design. Now the put succeeds for a
    # tenant-bound caller and the process-global writes below RUN: env var,
    # `.env` file, settings cache_clear. That is new in COUNT, not in kind —
    # the same deployment-wide write is already reachable today via
    # `POST /integrations/configure` (whose put failure is swallowed into
    # `integrations.db_write_failed` and continues) and via
    # `DELETE /integrations/keys` (whose `os.environ.pop` is unconditional).
    # Fixing the reachability by re-breaking the write would be repairing the
    # fail-closed contract in the wrong direction (D33 finding 3); the fix is
    # per-request provider credentials — `work_plan.md` §6 gate (f).
    os.environ[env_var] = req.value

    # Write to .env as bootstrap fallback
    env_path = _find_env_file()
    _upsert_env_var(env_path, env_var, req.value)

    # Bust settings cache
    from acb_common.settings import get_settings as _gs  # noqa: PLC0415
    _gs.cache_clear()

    _log.info(
        "integrations.key_put",
        service=req.service,
        key_name=req.key_name,
        provider=provider,
        actor=user.email,
    )

    return {
        "ok": True,
        "service": req.service,
        "key_name": req.key_name,
        "provider": provider,
        "env_var": env_var,
        "storage": "encrypted-postgres",
    }


@router.delete("/keys")
async def delete_integration_key(
    req: IntegrationKeyDelete,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a single integration credential from the encrypted DB."""
    if req.service not in _SETUP_GUIDES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown service: {req.service!r}",
        )

    provider = f"{req.service}:{req.key_name}"
    from acb_llm.key_store import get_key_store
    store = get_key_store()
    await store.delete(provider, organization_id=current_tenant())

    # Find and clear the env var
    # ⚠️ CROSS-TENANT WRITE, recorded and deliberately not repaired — see the
    # MT-1j slice 5 block at the top of this module. The delete above removes
    # only this organization's row; the pop below unsets the variable for the
    # whole process, i.e. for every other organization too.
    guide = _SETUP_GUIDES[req.service]
    for var in guide["env_vars"]:
        suffix = var["key"].lower().removeprefix(
            f"{req.service}_".upper().lower()
        ).replace("-", "_")
        if suffix == req.key_name:
            os.environ.pop(var["key"], None)
            break

    _log.info(
        "integrations.key_deleted",
        service=req.service,
        key_name=req.key_name,
        actor=user.email,
    )

    return {
        "ok": True,
        "service": req.service,
        "key_name": req.key_name,
        "deleted": True,
    }


# ---------------------------------------------------------------------------
# /integrations/discover — AI-powered API credential schema discovery
# ---------------------------------------------------------------------------

class DiscoverRequest(BaseModel):
    query: str  # e.g. "Notion", "Slack", "HubSpot"


@router.post("/discover")
async def discover_api(
    req: DiscoverRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Use LLM (+ optional web search) to generate an API integration schema.

    The returned definition can be reviewed, then saved via
    POST /integrations/custom and credentials entered via
    POST /integrations/configure.
    """
    import json as _json  # noqa: PLC0415

    import litellm as _litellm  # noqa: PLC0415

    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query cannot be empty")

    query = req.query.strip()

    # 1. Optional web search enhancement via SerpAPI
    web_context = ""
    settings_obj = get_settings()
    serpapi_key = (
        getattr(settings_obj, "serpapi_api_key", "")
        or os.getenv("SERPAPI_API_KEY", "")
    )
    if serpapi_key:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://serpapi.com/search.json",
                    params={
                        "q": f"{query} API developer documentation authentication",
                        "num": 3,
                        "api_key": serpapi_key,
                    },
                )
                data = r.json()
                results = data.get("organic_results", [])[:3]
                web_context = "\n".join(
                    f"- {res.get('title', '')}: {res.get('snippet', '')} — "
                    f"{res.get('link', '')}"
                    for res in results
                )
        except Exception as exc:
            _log.debug("discover.web_search_failed", error=str(exc))

    # 2. Build LLM prompt — request array to support multi-API companies
    web_section = f"\nRelevant search results:\n{web_context}\n" if web_context else ""
    prompt = (
        f'You are an API integration expert. The user wants to connect to: "{query}".\n'
        + web_section
        + r"""Return ONLY a valid JSON array of 1-6 relevant API integration definitions.

Rules for the query:
- If the query is a specific product ("Notion", "Stripe") → return 1-2 results
- If it is a company with multiple APIs ("Google", "Microsoft", "Atlassian", "Meta")
  → return their major distinct APIs (e.g. Google: Sheets, Maps, Gmail, Drive, Calendar)
- Include only genuinely distinct APIs that require separate credentials

Each item in the array must have EXACTLY these fields:
{
  "service_id": "lowercase-hyphens, e.g. google-sheets",
  "label": "Human Readable Name",
  "category": "crm|email|productivity|search|prospecting|analytics|communication|payments|storage|custom",
  "description": "One sentence what this API does",
  "domain": "main domain for logo, e.g. sheets.google.com or notion.so",
  "setup_url": "https://... URL to get credentials",
  "docs_url": "https://... URL to API documentation",
  "instructions": "Numbered steps to get the API key",
  "env_vars": [
    {"key": "SERVICE_API_KEY", "label": "API Key", "sensitive": true}
  ]
}

- env var keys: SCREAMING_SNAKE_CASE, e.g. NOTION_API_TOKEN, GOOGLE_SHEETS_SA_JSON_PATH
- sensitive=true for secrets/tokens; sensitive=false for plain URLs/IDs
- domain: base domain only, no https://, used for logo fetching
- Return ONLY the JSON array, no markdown fences, no extra text"""
    )

    # 3. Call LLM — try models in order of preference
    _models = [
        "deepseek/deepseek-chat",
        "openai/gpt-4o-mini",
        "gpt-4o-mini",
        "anthropic/claude-haiku-20240307",
    ]
    last_err: Exception | None = None
    for model in _models:
        try:
            resp = await _litellm.acompletion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2400,
            )
            content = (resp.choices[0].message.content or "").strip()
            # Strip markdown code fences if present
            if "```" in content:
                content = "\n".join(
                    ln for ln in content.splitlines()
                    if not ln.strip().startswith("```")
                ).strip()
            parsed = _json.loads(content)
            # Accept both array and single-object responses
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list) or not parsed:
                raise ValueError("Expected a non-empty JSON array")
            # Validate each item
            results: list[dict[str, Any]] = []
            for item in parsed:
                if item.get("service_id") and item.get("label") and item.get("env_vars"):
                    results.append(item)
            if not results:
                raise ValueError("No valid API definitions in response")
            _log.info(
                "integrations.discover",
                query=query,
                count=len(results),
                service_ids=[r.get("service_id") for r in results],
                model=model,
                actor=user.email,
            )
            return {
                "ok": True,
                "results": results,
                "query": query,
                "web_enhanced": bool(web_context),
                "model": model,
            }
        except (_json.JSONDecodeError, ValueError):
            continue
        except Exception as exc:
            last_err = exc
            continue

    err_detail = str(last_err) if last_err else "No LLM model available"
    raise HTTPException(status_code=500, detail=f"Discovery failed: {err_detail}")


# ---------------------------------------------------------------------------
# /integrations/custom — CRUD for user-defined API definitions
# ---------------------------------------------------------------------------

class CustomApiDef(BaseModel):
    service_id: str
    label: str
    category: str = "custom"
    description: str = ""
    domain: str = ""
    setup_url: str = ""
    docs_url: str = ""
    instructions: str = ""
    env_vars: list[dict[str, Any]] = []


@router.get("/custom")
async def list_custom_apis(
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all user-defined custom API definitions."""
    try:
        rows = await _db_query(
            "SELECT * FROM custom_api_definitions ORDER BY created_at"
        )
        return [
            {
                "service_id": r["service_id"],
                "label": r["label"],
                "category": r.get("category", "custom"),
                "description": r.get("description", ""),
                "domain": r.get("domain", ""),
                "setup_url": r.get("setup_url", ""),
                "docs_url": r.get("docs_url", ""),
                "instructions": r.get("instructions", ""),
                "env_vars": r.get("env_vars") or [],
                "created_at": (
                    r["created_at"].isoformat() if r.get("created_at") else None
                ),
            }
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.post("/custom")
async def create_custom_api(
    req: CustomApiDef,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a custom API definition (upsert on service_id)."""
    import json as _json  # noqa: PLC0415

    if not re.match(r"^[a-z][a-z0-9-]{0,60}$", req.service_id):
        raise HTTPException(
            400,
            "service_id must be lowercase letters, numbers, and hyphens.",
        )
    try:
        await _db_query(
            """
            INSERT INTO custom_api_definitions
                (service_id, label, category, description, domain,
                 setup_url, docs_url, instructions, env_vars)
            VALUES
                (:service_id, :label, :category, :description, :domain,
                 :setup_url, :docs_url, :instructions, :env_vars)
            ON CONFLICT (service_id) DO UPDATE SET
                label        = :label,
                category     = :category,
                description  = :description,
                domain       = :domain,
                setup_url    = :setup_url,
                docs_url     = :docs_url,
                instructions = :instructions,
                env_vars     = :env_vars,
                updated_at   = now()
            """,
            service_id=req.service_id,
            label=req.label,
            category=req.category,
            description=req.description,
            domain=req.domain or "",
            setup_url=req.setup_url,
            docs_url=req.docs_url,
            instructions=req.instructions,
            env_vars=_json.dumps(req.env_vars),
        )
        _log.info(
            "integrations.custom_created",
            service_id=req.service_id,
            actor=user.email,
        )
        return {"ok": True, "service_id": req.service_id}
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.delete("/custom/{service_id}")
async def delete_custom_api(
    service_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a custom API definition."""
    try:
        await _db_query(
            "DELETE FROM custom_api_definitions WHERE service_id = :service_id",
            service_id=service_id,
        )
        _log.info(
            "integrations.custom_deleted",
            service_id=service_id,
            actor=user.email,
        )
        return {"ok": True, "service_id": service_id, "deleted": True}
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.get("/test")
async def test_integration(
    service: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Perform a lightweight connectivity test for a configured integration.

    Returns {service, ok, detail}.
    """
    settings = get_settings()

    if not _is_configured(service, settings):
        return {
            "service": service,
            "ok": False,
            "detail": "Integration is not configured — missing required env vars.",
        }

    try:
        result = await _run_test(service, settings)
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "detail": f"Test raised: {exc}"}

    _log.info("integrations.test", service=service, ok=result.get("ok"), actor=user.email)
    return {"service": service, **result}


async def _run_test(service: str, settings: Any) -> dict[str, Any]:
    """Run a real connectivity check per integration."""
    async with httpx.AsyncClient(timeout=10) as client:
        if service == "zoho-crm":
            # Exchange refresh token for access token
            resp = await client.post(
                f"{settings.zoho_accounts_url}/oauth/v2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.zoho_client_id,
                    "client_secret": settings.zoho_client_secret,
                    "refresh_token": settings.zoho_refresh_token,
                },
            )
            if resp.status_code == 200 and "access_token" in resp.json():
                return {"ok": True, "detail": "Token exchange succeeded."}
            return {"ok": False, "detail": f"Token exchange failed: {resp.text[:200]}"}

        if service == "apollo":
            resp = await client.get(
                f"{settings.apollo_base_url}/auth/health",
                headers={"Cache-Control": "no-cache", "X-Api-Key": settings.apollo_api_key},
            )
            if resp.status_code in (200, 204):
                return {"ok": True, "detail": "Apollo API reachable."}
            return {"ok": False, "detail": f"Status {resp.status_code}: {resp.text[:200]}"}

        if service == "google-maps":
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": "1600+Amphitheatre+Parkway", "key": settings.google_maps_api_key},
            )
            data = resp.json()
            if data.get("status") in ("OK", "ZERO_RESULTS"):
                return {"ok": True, "detail": "Google Maps API reachable."}
            return {"ok": False, "detail": f"API error: {data.get('status')} — {data.get('error_message', '')}"}

        if service == "instantly":
            resp = await client.get(
                f"{settings.instantly_base_url}/campaign/list",
                params={"api_key": settings.instantly_api_key, "limit": 1, "skip": 0},
            )
            if resp.status_code == 200:
                return {"ok": True, "detail": "Instantly API reachable."}
            return {"ok": False, "detail": f"Status {resp.status_code}: {resp.text[:200]}"}

        if service in ("gmail", "gmail-send"):
            sa_path = Path(settings.gmail_sa_json_path)
            if sa_path.exists():
                return {"ok": True, "detail": "Service account key file found."}
            return {"ok": False, "detail": f"Key file not found: {sa_path}"}

        if service == "github":
            resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"token {settings.github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            if resp.status_code == 200:
                login = resp.json().get("login", "?")
                return {"ok": True, "detail": f"Authenticated as GitHub user '{login}'."}
            return {"ok": False, "detail": f"Status {resp.status_code}: {resp.text[:200]}"}

        if service == "gmail-oauth":
            client_id = (
                settings.gmail_oauth_client_id
                or os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
            )
            client_secret = (
                settings.gmail_oauth_client_secret
                or os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
            )
            if not client_id or not client_secret:
                return {"ok": False, "detail": "Gmail OAuth client ID or secret is empty."}
            # Validate by attempting token exchange with a dummy code.
            # Valid credentials → Google returns "invalid_grant" (expected).
            # Invalid credentials → Google returns "invalid_client".
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": "dummy_test_code",
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:8000/email/oauth/gmail/callback",
                },
            )
            err = (resp.json() or {}).get("error", "")
            if err == "invalid_grant":
                return {"ok": True, "detail": "Credentials valid — ready for user OAuth flow."}
            if err == "invalid_client":
                return {"ok": False, "detail": "Invalid client ID or secret. Check your Google Cloud OAuth credentials."}
            return {"ok": False, "detail": f"Unexpected: {resp.text[:200]}"}

        if service == "microsoft-oauth":
            client_id = (
                settings.msft_oauth_client_id
                or os.environ.get("MSFT_OAUTH_CLIENT_ID", "")
                or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_ID", "")
            )
            client_secret = (
                settings.msft_oauth_client_secret
                or os.environ.get("MSFT_OAUTH_CLIENT_SECRET", "")
                or os.environ.get("AUTH_MICROSOFT_ENTRA_ID_SECRET", "")
            )
            if not client_id or not client_secret:
                return {"ok": False, "detail": "Microsoft OAuth client ID or secret is empty."}
            # Validate by attempting token exchange with a dummy code.
            # Valid credentials → Microsoft returns "invalid_grant" (expected).
            # Invalid credentials → Microsoft returns "invalid_client".
            resp = await client.post(
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": "dummy_test_code",
                    "grant_type": "authorization_code",
                    "redirect_uri": "http://localhost:8000/email/oauth/microsoft/callback",
                },
            )
            err = (resp.json() or {}).get("error", "")
            if err == "invalid_grant":
                return {"ok": True, "detail": "Credentials valid — ready for user OAuth flow."}
            if err == "invalid_client":
                return {"ok": False, "detail": "Invalid client ID or secret. Check your Azure app registration credentials."}
            return {"ok": False, "detail": f"Unexpected: {resp.text[:200]}"}

    return {"ok": False, "detail": f"No test defined for service '{service}'."}


# ---------------------------------------------------------------------------
# MCP Server Registry — CRUD + connection test
# ---------------------------------------------------------------------------

class McpServerRequest(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    transport: str = "http-sse"  # "stdio" | "http-sse"
    command: str | None = None   # stdio only
    url: str | None = None       # http-sse only
    env_vars: dict[str, str] = {}
    headers: dict[str, str] = {}
    agent_scope: list[str] = ["*"]
    enabled: bool = True


@router.get("/mcp", summary="List registered MCP servers")
async def list_mcp_servers(
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return all MCP servers (enabled + disabled)."""
    try:
        from acb_graph import get_session  # noqa: PLC0415
        with get_session() as s:
            rows = s.execute(
                text("SELECT name, label, description, transport, command, url, "
                "env_vars, headers, agent_scope, enabled, created_at, updated_at "
                "FROM mcp_servers ORDER BY created_at")
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            result.append({
                "name": r[0], "label": r[1], "description": r[2],
                "transport": r[3], "command": r[4], "url": r[5],
                "env_vars": r[6] or {}, "headers": r[7] or {},
                "agent_scope": r[8] or ["*"], "enabled": r[9],
                "created_at": str(r[10]) if r[10] else None,
                "updated_at": str(r[11]) if r[11] else None,
            })
        return result
    except Exception as exc:
        _log.warning("mcp.list_failed", error=str(exc))
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.post("/mcp", status_code=status.HTTP_201_CREATED, summary="Register or update an MCP server")
async def register_mcp_server(
    req: McpServerRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Add or update an MCP server configuration."""
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$", req.name):
        raise HTTPException(
            422,
            "MCP server name must be 2-50 lowercase letters, digits, "
            "or hyphens.",
        )
    if req.transport == "stdio" and not req.command:
        raise HTTPException(422, "stdio transport requires a 'command'.")
    if req.transport == "http-sse" and not req.url:
        raise HTTPException(422, "http-sse transport requires a 'url'.")

    try:
        from acb_graph import get_session  # noqa: PLC0415
        with get_session() as s:
            s.execute(
                text("""INSERT INTO mcp_servers (name, label, description, transport, command, url,
                   env_vars, headers, agent_scope, enabled, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,now())
                   ON CONFLICT (name) DO UPDATE SET
                   label=EXCLUDED.label, description=EXCLUDED.description,
                   transport=EXCLUDED.transport, command=EXCLUDED.command,
                   url=EXCLUDED.url, env_vars=EXCLUDED.env_vars,
                   headers=EXCLUDED.headers, agent_scope=EXCLUDED.agent_scope,
                   enabled=EXCLUDED.enabled, updated_at=now()"""),
                (req.name, req.label or req.name, req.description,
                 req.transport, req.command, req.url,
                 json.dumps(req.env_vars), json.dumps(req.headers),
                 json.dumps(req.agent_scope), req.enabled),
            )
            s.commit()
        _log.info("mcp.registered", name=req.name, actor=user.email)
        return {"ok": True, "name": req.name}
    except Exception as exc:
        _log.warning("mcp.register_failed", name=req.name, error=str(exc))
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.delete("/mcp/{name}", summary="Remove an MCP server")
async def remove_mcp_server(
    name: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete an MCP server from the registry."""
    try:
        from acb_graph import get_session  # noqa: PLC0415
        with get_session() as s:
            result = s.execute(
                text("DELETE FROM mcp_servers WHERE name = :name"), {"name": name},
            )
            s.commit()
            if result.rowcount == 0:
                raise HTTPException(404, f"MCP server '{name}' not found.")
        _log.info("mcp.removed", name=name, actor=user.email)
        return {"ok": True, "deleted": name}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.post("/mcp/test", summary="Test connectivity to an MCP server")
async def test_mcp_server(
    req: McpServerRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Perform a lightweight connectivity check for an MCP server.

    For http-sse: makes a GET to the URL to verify reachability.
    For stdio: checks that the command is executable.
    Returns {ok, detail}.
    """
    if req.transport == "http-sse" and req.url:
        try:
            headers = dict(req.headers)
            headers.setdefault("Accept", "application/json")
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(req.url, headers=headers)
                if resp.status_code < 500:
                    return {"ok": True, "detail": f"Server reachable (HTTP {resp.status_code})."}
                return {"ok": False, "detail": f"Server returned HTTP {resp.status_code}."}
        except httpx.ConnectError:
            return {"ok": False, "detail": f"Could not connect to {req.url}."}
        except Exception as exc:
            return {"ok": False, "detail": str(exc)}
    if req.transport == "stdio" and req.command:
        import shutil  # noqa: PLC0415
        exe = (req.command or "").split()[0]
        if shutil.which(exe):
            return {"ok": True, "detail": f"Command '{exe}' found on PATH."}
        return {"ok": False, "detail": f"Command '{exe}' not found on PATH."}
    return {"ok": False, "detail": "No URL or command to test."}


# ---------------------------------------------------------------------------
# Plugin Registry — install from manifest URL, list, remove
# ---------------------------------------------------------------------------

class PluginInstallRequest(BaseModel):
    manifest_url: str


@router.get("/plugins", summary="List installed plugins")
async def list_plugins(
    user: UserContext = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return all installed plugins."""
    try:
        from acb_graph import get_session  # noqa: PLC0415
        with get_session() as s:
            rows = s.execute(
                text("SELECT id, name, label, description, manifest_url, openapi_url, "
                "logo_url, auth_type, auth_config, manifest, openapi_spec, "
                "tools_generated, enabled, version, created_at, updated_at "
                "FROM plugins ORDER BY created_at")
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            result.append({
                "id": str(r[0]), "name": r[1], "label": r[2],
                "description": r[3], "manifest_url": r[4],
                "openapi_url": r[5], "logo_url": r[6],
                "auth_type": r[7], "auth_config": r[8] or {},
                "manifest": r[9] or {}, "openapi_spec": r[10] or {},
                "tools_generated": r[11] or [],
                "enabled": r[12], "version": r[13],
                "created_at": str(r[14]) if r[14] else None,
                "updated_at": str(r[15]) if r[15] else None,
            })
        return result
    except Exception as exc:
        _log.warning("plugins.list_failed", error=str(exc))
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.post("/plugins/install", status_code=status.HTTP_201_CREATED,
             summary="Install a plugin from a manifest URL")
async def install_plugin(
    req: PluginInstallRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch the plugin manifest + OpenAPI spec from a URL and register it.

    The manifest URL should point to an ai-plugin.json file.
    The OpenAPI spec URL is read from the manifest's ``api.url`` field.
    """
    manifest_url = req.manifest_url.strip()
    if not manifest_url.startswith(("https://", "http://")):
        raise HTTPException(422, "Manifest URL must start with http:// or https://.")

    # 1. Fetch the manifest
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            manifest_resp = await client.get(manifest_url, headers={"Accept": "application/json"})
            if manifest_resp.status_code != 200:
                raise HTTPException(422, f"Failed to fetch manifest: HTTP {manifest_resp.status_code}")
            manifest: dict[str, Any] = manifest_resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Failed to fetch manifest: {exc}") from exc

    # 2. Validate required fields
    plugin_name = (manifest.get("name_for_model") or manifest.get("name") or "").strip()
    if not plugin_name:
        raise HTTPException(422, "Manifest missing 'name_for_model' or 'name' field.")

    label = (manifest.get("name_for_human") or plugin_name).strip()
    description = (manifest.get("description_for_model") or manifest.get("description_for_human") or "").strip()
    auth_type = (manifest.get("auth", {}) or {}).get("type", "none")
    api_url = (manifest.get("api", {}) or {}).get("url", "")
    logo_url = (manifest.get("logo_url") or "").strip()
    version = (manifest.get("schema_version") or "0.0.0").strip()

    # 3. Fetch OpenAPI spec if provided
    openapi_spec: dict[str, Any] = {}
    tools_generated: list[dict[str, Any]] = []
    if api_url:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                spec_resp = await client.get(api_url, headers={"Accept": "application/json, application/yaml"})
                if spec_resp.status_code == 200:
                    content_type = spec_resp.headers.get("content-type", "")
                    if "yaml" in content_type:
                        import yaml  # noqa: PLC0415
                        openapi_spec = yaml.safe_load(spec_resp.text) or {}
                    else:
                        openapi_spec = spec_resp.json()
                    # Generate tool definitions from OpenAPI paths
                    tools_generated = _openapi_to_tool_defs(plugin_name, openapi_spec)
        except Exception as exc:
            _log.warning("plugins.openapi_fetch_failed", name=plugin_name, error=str(exc))

    # 4. Store in DB
    try:
        from acb_graph import get_session  # noqa: PLC0415
        with get_session() as s:
            s.execute(
                text("""INSERT INTO plugins (name, label, description, manifest_url, openapi_url,
                   logo_url, auth_type, auth_config, manifest, openapi_spec,
                   tools_generated, enabled, version, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,
                           %s::jsonb,%s,%s,now())
                   ON CONFLICT (name) DO UPDATE SET
                   label=EXCLUDED.label, description=EXCLUDED.description,
                   manifest_url=EXCLUDED.manifest_url, openapi_url=EXCLUDED.openapi_url,
                   logo_url=EXCLUDED.logo_url, auth_type=EXCLUDED.auth_type,
                   auth_config=EXCLUDED.auth_config, manifest=EXCLUDED.manifest,
                   openapi_spec=EXCLUDED.openapi_spec,
                   tools_generated=EXCLUDED.tools_generated,
                   enabled=EXCLUDED.enabled, version=EXCLUDED.version,
                   updated_at=now()"""),
                (plugin_name, label, description, manifest_url, api_url,
                 logo_url, auth_type, json.dumps(manifest.get("auth", {}) or {}),
                 json.dumps(manifest), json.dumps(openapi_spec),
                 json.dumps(tools_generated), True, version),
            )
            s.commit()
        _log.info("plugin.installed", name=plugin_name, actor=user.email)
        return {
            "ok": True, "name": plugin_name, "label": label,
            "tools_count": len(tools_generated),
            "auth_type": auth_type,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}") from exc


@router.delete("/plugins/{plugin_id}", summary="Remove an installed plugin")
async def remove_plugin(
    plugin_id: str,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a plugin from the registry."""
    try:
        import uuid as _uuid  # noqa: PLC0415
        pid = _uuid.UUID(plugin_id)
    except ValueError:
        raise HTTPException(422, "Invalid plugin ID format.") from None
    try:
        from acb_graph import get_session  # noqa: PLC0415
        with get_session() as s:
            result = s.execute(text("DELETE FROM plugins WHERE id = :id"), {"id": pid})
            s.commit()
            if result.rowcount == 0:
                raise HTTPException(404, f"Plugin '{plugin_id}' not found.")
        _log.info("plugin.removed", id=plugin_id, actor=user.email)
        return {"ok": True, "deleted": plugin_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"DB error: {exc}") from exc


def _openapi_to_tool_defs(plugin_name: str, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert OpenAPI paths to Metorite tool definitions.

    Each operation becomes a tool the agent can call.  Returns a list of
    dicts with {name, description, parameters (JSON Schema), method, path}.
    """
    tools: list[dict[str, Any]] = []
    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    for path_url, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, op in methods.items():
            if not isinstance(op, dict) or method not in ("get", "post", "put", "delete", "patch"):
                continue
            op_id = op.get("operationId", f"{method}_{path_url.strip('/').replace('/', '_')}")
            tool_name = f"{plugin_name}_{op_id}".lower().replace("-", "_")[:64]
            summary = op.get("summary", "") or op.get("description", "") or op_id
            description = f"{summary} — {method.upper()} {path_url}"
            # Build JSON Schema for parameters
            params_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for param in op.get("parameters", []) or []:
                pname = param.get("name", "")
                pdesc = param.get("description", "")
                pschema = param.get("schema", {"type": "string"})
                params_schema["properties"][pname] = {
                    "description": pdesc, **pschema,
                }
                if param.get("required"):
                    params_schema["required"].append(pname)
            # Request body as a single "body" param
            if op.get("requestBody"):
                rb = op["requestBody"]
                rb_desc = (rb.get("description") or "Request body")
                content = (rb.get("content", {}) or {})
                json_content = content.get("application/json", {})
                rb_schema = json_content.get("schema", {"type": "object"})
                params_schema["properties"]["body"] = {
                    "description": rb_desc, **rb_schema,
                }
                if rb.get("required"):
                    params_schema["required"].append("body")

            tools.append({
                "name": tool_name,
                "description": description,
                "parameters": params_schema,
                "method": method.upper(),
                "path": path_url,
            })
    return tools


# ---------------------------------------------------------------------------
# GitHub OAuth Device Flow
# ---------------------------------------------------------------------------

@router.post("/github/device/start")
async def github_device_start(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Initiate the GitHub OAuth Device Flow.

    Returns the user_code and verification_uri to show in the UI.
    The frontend polls /github/device/poll until the user approves.

    Requires GITHUB_CLIENT_ID to be configured (via /configure first).
    """
    settings = get_settings()
    client_id: str = getattr(settings, "github_client_id", "")
    if not client_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="GITHUB_CLIENT_ID is not configured. Save it via /integrations/configure first.",
        )

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://github.com/login/device/code",
            data={"client_id": client_id, "scope": "repo copilot"},
            headers={"Accept": "application/json"},
        )

    data = resp.json()
    if "error" in data:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub error: {data['error']} — {data.get('error_description', '')}",
        )

    _log.info("github.device.start", actor=user.email)
    return {
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "device_code": data["device_code"],
        "expires_in": data["expires_in"],
        "interval": data["interval"],
    }


@router.post("/github/device/poll")
async def github_device_poll(
    req: DevicePollRequest,
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Poll GitHub to check if the user has approved the device flow.

    Returns:
        {status: "authorized", login: str}  — token saved to .env, Settings reloaded
        {status: "pending"}                 — user hasn't approved yet, keep polling
        {status: "slow_down", interval: int} — increase polling interval
        {status: "expired"}                 — code expired, restart flow
        {status: "denied"}                  — user denied access
    """
    settings = get_settings()
    client_id: str = getattr(settings, "github_client_id", "")
    if not client_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="GITHUB_CLIENT_ID is not configured.",
        )

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "device_code": req.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
        )

    data = resp.json()

    if "access_token" in data:
        token: str = data["access_token"]
        # Persist to .env and hot-reload
        env_path = _find_env_file()
        _upsert_env_var(env_path, "GITHUB_TOKEN", token)
        os.environ["GITHUB_TOKEN"] = token
        from acb_common.settings import get_settings as _gs  # noqa: PLC0415
        _gs.cache_clear()

        # Fetch the GitHub login name for a friendly confirmation message
        login = "unknown"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                ur = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                if ur.status_code == 200:
                    login = ur.json().get("login", "unknown")
        except Exception:  # noqa: BLE001
            pass

        _log.info("github.device.authorized", login=login, actor=user.email)
        return {"status": "authorized", "login": login}

    error = data.get("error", "unknown")
    if error == "slow_down":
        return {"status": "slow_down", "interval": data.get("interval", 10)}
    if error in ("expired_token", "access_denied"):
        return {"status": error.replace("_", "-")}
    # authorization_pending and anything else
    return {"status": "pending"}


# ---------------------------------------------------------------------------
# GitHub account info & CLI import
# ---------------------------------------------------------------------------

def _parse_gh_status(output: str) -> dict[str, Any]:
    """Parse `gh auth status` output into structured fields."""
    result: dict[str, Any] = {
        "authenticated": False,
        "login": None,
        "scopes": [],
        "has_copilot": False,
    }
    if "Logged in to github.com" not in output:
        return result
    result["authenticated"] = True
    login_m = re.search(r"account\s+(\S+)\s+\(", output)
    if login_m:
        result["login"] = login_m.group(1)
    scope_line = next((ln for ln in output.splitlines() if "Token scopes:" in ln), "")
    result["scopes"] = re.findall(r"'([^']+)'", scope_line)
    result["has_copilot"] = "copilot" in result["scopes"]
    return result


@router.get("/github/account")
async def github_account(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Return current GitHub account info: connected token details + gh CLI status.

    Response fields:
        token_configured (bool)   — GITHUB_TOKEN is set in env
        token_login      (str?)   — GitHub login resolved from the token
        token_scopes     (list)   — OAuth scopes on the token (from X-OAuth-Scopes header)
        token_has_copilot(bool)   — whether 'copilot' scope is present
        gh_cli_available (bool)   — gh binary is on PATH
        gh_cli_authenticated(bool)— gh has a logged-in account
        gh_cli_login     (str?)   — account name from gh auth status
        gh_cli_scopes    (list)   — scopes on the CLI token
        gh_cli_has_copilot(bool)  — whether gh CLI token has 'copilot' scope
    """
    settings = get_settings()
    token: str = getattr(settings, "github_token", "") or ""

    info: dict[str, Any] = {
        "token_configured": bool(token.strip()),
        "token_login": None,
        "token_scopes": [],
        "token_has_copilot": False,
        "gh_cli_available": False,
        "gh_cli_authenticated": False,
        "gh_cli_login": None,
        "gh_cli_scopes": [],
        "gh_cli_has_copilot": False,
    }

    # Resolve info from current GITHUB_TOKEN via GitHub REST API
    if token.strip():
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                if resp.status_code == 200:
                    info["token_login"] = resp.json().get("login")
                    scopes_header = resp.headers.get("X-OAuth-Scopes", "")
                    info["token_scopes"] = [s.strip() for s in scopes_header.split(",") if s.strip()]
                    info["token_has_copilot"] = "copilot" in info["token_scopes"]
        except Exception:  # noqa: BLE001
            pass

    # Check gh CLI availability + auth state (non-blocking; failure is graceful)
    try:
        proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        output = proc.stdout + proc.stderr
        parsed = _parse_gh_status(output)
        info["gh_cli_available"] = True
        info["gh_cli_authenticated"] = parsed["authenticated"]
        info["gh_cli_login"] = parsed["login"]
        info["gh_cli_scopes"] = parsed["scopes"]
        info["gh_cli_has_copilot"] = parsed["has_copilot"]
    except FileNotFoundError:
        pass  # gh not installed
    except Exception:  # noqa: BLE001
        info["gh_cli_available"] = True  # binary found but errored

    return info


@router.post(
    "/github/connect-cli",
    dependencies=[require_role(UserRole.EXECUTIVE, UserRole.AGENT)],
)
async def github_connect_cli(
    user: UserContext = Depends(get_current_user),
) -> dict[str, Any]:
    """Import the active GitHub CLI token into GITHUB_TOKEN in .env.

    Reads the token via `gh auth token`, checks its scopes, writes to .env,
    and returns the connected account details.

    If the token lacks the `copilot` scope, the import still succeeds (repo
    cloning will work) but `has_copilot` is False and `refresh_command` is
    returned so the user knows what to run to gain model access.
    """
    # 1. Read token from gh CLI
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=400,
            detail="GitHub CLI (gh) is not installed or not in PATH.",
        )

    if proc.returncode != 0 or not proc.stdout.strip():
        raise HTTPException(
            status_code=400,
            detail="gh CLI is not authenticated. Run: gh auth login",
        )

    token = proc.stdout.strip()

    # 2. Parse scopes + login from gh auth status
    login = "unknown"
    scopes: list[str] = []
    try:
        status_proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        parsed = _parse_gh_status(status_proc.stdout + status_proc.stderr)
        login = parsed["login"] or "unknown"
        scopes = parsed["scopes"]
    except Exception:  # noqa: BLE001
        pass

    # 3. Save to .env and hot-reload settings
    env_path = _find_env_file()
    _upsert_env_var(env_path, "GITHUB_TOKEN", token)
    os.environ["GITHUB_TOKEN"] = token
    from acb_common.settings import get_settings as _gs  # noqa: PLC0415
    _gs.cache_clear()

    has_copilot = "copilot" in scopes
    _log.info("github.connect_cli", login=login, has_copilot=has_copilot, actor=user.email)

    return {
        "ok": True,
        "login": login,
        "scopes": scopes,
        "has_copilot": has_copilot,
        "refresh_command": "gh auth refresh --scopes copilot,repo" if not has_copilot else None,
        "message": (
            f"Connected as @{login}."
            + (
                ""
                if has_copilot
                else " Token lacks 'copilot' scope — Copilot models unavailable until you refresh."
            )
        ),
    }
