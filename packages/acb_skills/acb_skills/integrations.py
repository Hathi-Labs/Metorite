"""Integration Registry — maps service names to credential dicts.

This is the *server-side* credential store for the agentic workflow.
When the executor calls ``build_integrations()``, it reads the agent's
declared ``config.json["integrations"]`` list, resolves each service name
to a credential dict, and injects the result into ``state["integrations"]``.

Agents and skills must only read credentials from ``state["integrations"]``.
They must never call ``os.getenv()`` for secrets or import ``Settings``
directly — see §5 of ``project-docs/agent_repo_compatibility.md``.

----

**Adding a new integration:**

1. Add the env-var field(s) to ``acb_common.settings.Settings``.
2. Add an entry to ``_REGISTRY`` below.
3. Add the service name string to the agent's ``config.json["integrations"]``.
4. The executor picks it up automatically on the next run.

**Credential security model:**

- Credentials are resolved *at run time* from the server process environment
  (set via ``infra/docker-compose.yml`` / Hostinger secrets manager).
- They are injected into the agent run's environment/state. Where a run's
  state is persisted, secrets can reach Postgres in the clear. **Do not rely on
  application-level encryption for these** — encrypt the Postgres volume and
  restrict DB access to the gateway/orchestrator service account. (See
  FOUNDATION_BUILDOUT_CHECKLIST.md BO-7 for the sandbox/secret-scoping plan.)
- OAuth refresh tokens: if a skill raises ``IntegrationAuthError``, the
  executor should refresh and retry once (not yet implemented — Phase 2).
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from typing import Any

from acb_common import get_logger

_log = get_logger("acb_skills.integrations")


class IntegrationNotFoundError(Exception):
    """Raised when a required integration is declared but not registered."""


class IntegrationMisconfiguredError(Exception):
    """Raised when a registered integration is missing required env vars."""


# ---------------------------------------------------------------------------
# Registry — maps service-name → resolver callable
#
# Each resolver receives the acb_common Settings object and returns a dict
# of credentials to be stored at state["integrations"]["<service-name>"].
# Return an empty dict {} if the integration is optional and not configured.
# Raise IntegrationMisconfiguredError if the integration is declared but
# required env vars are missing.
# ---------------------------------------------------------------------------

def _zoho_crm(s: Any) -> dict[str, Any]:
    client_id = getattr(s, "zoho_client_id", "") or os.getenv("ZOHO_CLIENT_ID", "")
    client_secret = getattr(s, "zoho_client_secret", "") or os.getenv("ZOHO_CLIENT_SECRET", "")
    refresh_token = getattr(s, "zoho_refresh_token", "") or os.getenv("ZOHO_REFRESH_TOKEN", "")
    if not all([client_id, client_secret, refresh_token]):
        raise IntegrationMisconfiguredError(
            "zoho-crm: ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN are all required."
        )
    return {
        "type": "oauth2",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "api_domain": getattr(s, "zoho_api_domain", "https://www.zohoapis.com"),
        "accounts_url": getattr(s, "zoho_accounts_url", "https://accounts.zoho.com"),
        "region": getattr(s, "zoho_region", "in"),
    }


def _apollo(s: Any) -> dict[str, Any]:
    api_key = getattr(s, "apollo_api_key", "") or os.getenv("APOLLO_API_KEY", "")
    if not api_key:
        raise IntegrationMisconfiguredError("apollo: APOLLO_API_KEY is required.")
    return {
        "type": "api_key",
        "api_key": api_key,
        "base_url": getattr(s, "apollo_base_url", "https://api.apollo.io/v1"),
    }


def _google_maps(s: Any) -> dict[str, Any]:
    api_key = getattr(s, "google_maps_api_key", "") or os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        raise IntegrationMisconfiguredError("google-maps: GOOGLE_MAPS_API_KEY is required.")
    return {
        "type": "api_key",
        "api_key": api_key,
    }


def _instantly(s: Any) -> dict[str, Any]:
    api_key = getattr(s, "instantly_api_key", "") or os.getenv("INSTANTLY_API_KEY", "")
    if not api_key:
        raise IntegrationMisconfiguredError("instantly: INSTANTLY_API_KEY is required.")
    return {
        "type": "api_key",
        "api_key": api_key,
        "base_url": getattr(s, "instantly_base_url", "https://api.instantly.ai/api/v1"),
    }


def _gmail(s: Any) -> dict[str, Any]:
    sa_path = getattr(s, "gmail_sa_json_path", "") or os.getenv("GMAIL_SA_JSON_PATH", "")
    default_user = getattr(s, "gmail_default_user", "") or os.getenv("GMAIL_DEFAULT_USER", "")
    if not sa_path:
        raise IntegrationMisconfiguredError(
            "gmail: GMAIL_SA_JSON_PATH (service-account key) is required."
        )
    return {
        "type": "service_account",
        "sa_json_path": sa_path,
        "workspace_domain": getattr(s, "gmail_workspace_domain", ""),
        "default_user": default_user,
    }


def _gmail_send(s: Any) -> dict[str, Any]:
    """Alias for gmail — used by agents that only need outbound send."""
    return _gmail(s)


def _smtp(s: Any) -> dict[str, Any]:
    host = getattr(s, "smtp_host", "") or os.getenv("SMTP_HOST", "")
    username = getattr(s, "smtp_username", "") or os.getenv("SMTP_USERNAME", "")
    password = getattr(s, "smtp_password", "") or os.getenv("SMTP_PASSWORD", "")
    if not host:
        raise IntegrationMisconfiguredError("smtp: SMTP_HOST is required.")
    return {
        "type": "smtp",
        "host": host,
        "port": int(getattr(s, "smtp_port", None) or os.getenv("SMTP_PORT", "587")),
        "username": username,
        "password": password,
        "use_tls": str(getattr(s, "smtp_use_tls", None) or os.getenv("SMTP_USE_TLS", "true")).lower() == "true",
    }


def _litellm(s: Any) -> dict[str, Any]:
    """LLM gateway — gives agents access to shared LLM routing via the gateway's /v1."""
    return {
        "type": "litellm",
        "base_url": getattr(s, "litellm_base_url", f"http://localhost:{getattr(s, 'gateway_port', 8000)}"),
        "api_key": getattr(s, "litellm_master_key", ""),
    }


def _serpapi(s: Any) -> dict[str, Any]:
    api_key = getattr(s, "serpapi_api_key", "") or os.getenv("SERPAPI_API_KEY", "")
    if not api_key:
        raise IntegrationMisconfiguredError("serpapi: SERPAPI_API_KEY is required.")
    return {"type": "api_key", "api_key": api_key}


def _apify(s: Any) -> dict[str, Any]:
    api_token = getattr(s, "apify_api_token", "") or os.getenv("APIFY_API_TOKEN", "")
    if not api_token:
        raise IntegrationMisconfiguredError("apify: APIFY_API_TOKEN is required.")
    return {
        "type": "api_key",
        "api_token": api_token,
        "base_url": "https://api.apify.com/v2",
    }


def _anymailfinder(s: Any) -> dict[str, Any]:
    api_key = getattr(s, "anymailfinder_api_key", "") or os.getenv("ANYMAILFINDER_API_KEY", "")
    if not api_key:
        raise IntegrationMisconfiguredError("anymailfinder: ANYMAILFINDER_API_KEY is required.")
    return {
        "type": "api_key",
        "api_key": api_key,
        "base_url": "https://api.anymailfinder.com/v5.0",
    }


def _google_sheets(s: Any) -> dict[str, Any]:
    # Reuse the Gmail service-account key if a dedicated one isn't set
    sa_path = (
        getattr(s, "google_sheets_sa_json_path", "")
        or os.getenv("GOOGLE_SHEETS_SA_JSON_PATH", "")
        or getattr(s, "gmail_sa_json_path", "")
        or os.getenv("GMAIL_SA_JSON_PATH", "")
    )
    if not sa_path:
        raise IntegrationMisconfiguredError(
            "google-sheets: GOOGLE_SHEETS_SA_JSON_PATH (service-account key) is required."
        )
    return {"type": "service_account", "sa_json_path": sa_path}


# Canonical credential-dict-field → env-var mapping per service. Single source
# of truth shared by the executor's run-scoped credential binding
# (``bind_run_credentials``) and the coding skill's script-subprocess env
# (``code_tools._script_env``): whatever a run binds is exactly what a declared
# integration's scripts may read.
FIELD_TO_ENV: dict[str, list[tuple[str, str]]] = {
    "zoho-crm": [
        ("client_id",     "ZOHO_CLIENT_ID"),
        ("client_secret", "ZOHO_CLIENT_SECRET"),
        ("refresh_token", "ZOHO_REFRESH_TOKEN"),
        ("api_domain",    "ZOHO_API_DOMAIN"),
        ("accounts_url",  "ZOHO_ACCOUNTS_URL"),
        ("region",        "ZOHO_REGION"),
    ],
    "apollo":        [("api_key", "APOLLO_API_KEY")],
    "serpapi":       [("api_key", "SERPAPI_API_KEY")],
    "apify":         [("api_token", "APIFY_API_TOKEN")],
    "anymailfinder": [("api_key", "ANYMAILFINDER_API_KEY")],
    "instantly":     [("api_key", "INSTANTLY_API_KEY")],
    "gmail":         [("sa_json_path", "GMAIL_SA_JSON_PATH"), ("default_user", "GMAIL_DEFAULT_USER")],
    "gmail-send":    [("sa_json_path", "GMAIL_SA_JSON_PATH"), ("default_user", "GMAIL_DEFAULT_USER")],
    "smtp":          [("host", "SMTP_HOST"), ("username", "SMTP_USERNAME"), ("password", "SMTP_PASSWORD")],
    "google-maps":   [("api_key", "GOOGLE_MAPS_API_KEY")],
    "google-sheets": [("sa_json_path", "GOOGLE_SHEETS_SA_JSON_PATH")],
    "litellm":       [("base_url", "LITELLM_BASE_URL"), ("api_key", "LITELLM_API_KEY")],
}


def env_var_names(services: list[str] | tuple[str, ...]) -> set[str]:
    """The canonical env-var names belonging to *services* (unknown → ignored).

    Used to grant a script subprocess exactly its agent's DECLARED
    integrations' credentials — never the whole environment.
    """
    names: set[str] = set()
    for service in services or []:
        for _field, env_var in FIELD_TO_ENV.get(service, []):
            names.add(env_var)
    return names


# ---------------------------------------------------------------------------
# Per-run credential binding (MT-0a) — contextvar, NOT os.environ
#
# `saas_multitenancy.md` §6.1 / MT-0a. The executor used to export a run's
# resolved credentials into the gateway's process-global ``os.environ`` and
# restore them at teardown. That removed *permanent accumulation* but could not
# remove *concurrent* exposure, and the code said so itself: "os.environ is
# process-global, so under concurrent in-process runs the scoping is
# best-effort — two overlapping runs still share the env for the overlap
# window."
#
# Under one tenant that is a within-org concern. Under two it is a credential
# leak: tenant A's Zoho token is readable by tenant B's concurrently-running
# agent — and agents run model-generated tool calls over content ingested from
# email and WhatsApp, which is precisely the code that must be assumed hostile.
#
# A ContextVar is per-asyncio-task and is copied into tasks created from the
# binding context, so two overlapping runs each see only their own credentials
# with no window at all. That is the whole change.
# ---------------------------------------------------------------------------

#: ``None`` default rather than ``{}`` — a mutable ContextVar default is shared
#: by every context that never sets one (ruff B039), which is precisely the
#: process-global sharing this whole change exists to remove. Readers normalise
#: it through :func:`run_credentials`.
_RUN_CREDENTIALS: ContextVar[Mapping[str, str] | None] = ContextVar(
    "acb_run_credentials", default=None,
)


def bind_run_credentials(integrations: dict[str, Any]) -> Token[Mapping[str, str] | None]:
    """Bind *integrations*' credentials to the current run's async context.

    Returns a token the caller **must** pass to :func:`release_run_credentials`
    at the run's teardown. Empty values are skipped, so an unconfigured optional
    integration binds nothing rather than an empty string a caller might treat
    as present.

    Unlike the ``os.environ`` export this replaces, nothing here is visible to
    any other task: a concurrent run in the same process binds its own value and
    the two never observe each other.
    """
    env: dict[str, str] = {}
    for service, creds in (integrations or {}).items():
        if not isinstance(creds, dict):
            continue
        for field, env_var in FIELD_TO_ENV.get(service, []):
            val = creds.get(field, "")
            if val:
                env[env_var] = str(val)
    return _RUN_CREDENTIALS.set(env)


def release_run_credentials(token: Token[Mapping[str, str] | None] | None) -> None:
    """Undo :func:`bind_run_credentials`. Never raises.

    ``ContextVar.reset`` rejects a token created in a *different* Context, which
    a teardown running on another task would hit. Falling back to an explicit
    empty bind keeps the failure closed — the run's credentials are gone either
    way — rather than leaving them readable because the reset raised.
    """
    if token is None:
        return
    try:
        _RUN_CREDENTIALS.reset(token)
    except (ValueError, RuntimeError):
        _RUN_CREDENTIALS.set(None)


def run_credentials() -> Mapping[str, str]:
    """This run's bound credentials, keyed by canonical env-var name.

    Empty mapping when nothing is bound — callers never see the ``None`` the
    ContextVar stores as its (deliberately immutable) default.
    """
    return _RUN_CREDENTIALS.get() or {}


def credential(name: str, default: str = "") -> str:
    """Read one credential by canonical env-var name.

    **This is what in-process skills must call instead of ``os.getenv``.**

    Precedence is deliberate and unchanged from the behaviour this replaces:
    an operator-provided value in the process environment wins, because it is a
    deployment-wide setting the operator chose and the old code explicitly did
    not overwrite it ("Gateway .env still wins"). Only then does the run's own
    bound credential apply.

    ⚠️ **Honest limit, and it is the reason MT-0d exists.** An operator-provided
    var IS still process-global and therefore still shared across tenants. MT-0a
    scopes the *run-resolved* credentials; making the operator's own store
    per-tenant is MT-0d (``provider_keys`` keyed ``(organization_id, provider)``).
    Do not read this function as making the process environment tenant-safe.
    """
    return os.environ.get(name) or run_credentials().get(name, "") or default


# Master registry: service-name → resolver
_REGISTRY: dict[str, Any] = {
    "zoho-crm":      _zoho_crm,
    "apollo":        _apollo,
    "google-maps":   _google_maps,
    "instantly":     _instantly,
    "gmail":         _gmail,
    "gmail-send":    _gmail_send,
    "smtp":          _smtp,
    "litellm":       _litellm,
    "serpapi":       _serpapi,
    "apify":         _apify,
    "anymailfinder": _anymailfinder,
    "google-sheets": _google_sheets,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_integrations(
    mandatory: list[str],
    optional: list[str],
    settings: Any,
    *,
    is_authorized: Callable[[str], bool] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Resolve integration service names to their credential dicts.

    Never raises.  All failures are returned in the second element of the
    tuple so the executor can log them and inject them into agent state.

    Args:
        mandatory:  Service names from ``config.json["integrations"]``.
                    Resolution failures are logged as errors but do NOT abort
                    the run — the agent can still start and may handle missing
                    integrations gracefully at tool-call time.
        optional:   Service names from ``config.json["optional_integrations"]``.
                    Resolution failures are logged as warnings and silently
                    skipped.
        settings:   Loaded ``acb_common.Settings`` instance.
        is_authorized:
                    Optional predicate ``(service) -> bool`` deciding whether
                    the ACTING USER may have this service's credentials
                    resolved on their behalf (org access control,
                    ``integrations:use:<service>``). ``None`` means no
                    filtering — the pre-org-access-control behaviour, and what
                    every non-user-initiated caller (cron, reconciler) gets.

                    An agent's ``config.json`` declares which integrations it
                    *wants*; this decides which the caller is allowed to give
                    it. The agent never widens its own access by declaring
                    more.

    Returns:
        A 2-tuple of:
        - ``resolved``: ``{service: credentials_dict}`` for every service that
          resolved successfully.
        - ``unavailable``: ``{service: reason}`` for every service that failed
          (not in registry, missing env vars, or not authorised for the acting
          user).  Agents can read this from ``state["integration_warnings"]``
          to surface helpful messages.

    An unauthorised service is reported through ``unavailable`` rather than
    raising, deliberately: agents already handle that map gracefully, so a
    member who lacks one integration still gets a working agent that explains
    what it cannot do, instead of a failed run.
    """
    resolved: dict[str, dict[str, Any]] = {}
    unavailable: dict[str, str] = {}

    for service_name, is_mandatory in (
        [(s, True) for s in mandatory] + [(s, False) for s in optional]
    ):
        if is_authorized is not None and not is_authorized(service_name):
            reason = (
                f"You do not have access to the {service_name!r} integration "
                f"(missing permission 'integrations:use:{service_name}'). "
                f"An organization admin can grant it."
            )
            unavailable[service_name] = reason
            _log.info(
                "integrations.not_authorized",
                service=service_name,
                mandatory=is_mandatory,
            )
            continue

        resolver = _REGISTRY.get(service_name)
        if resolver is None:
            reason = (
                f"{service_name!r} is not in the IntegrationRegistry "
                f"(no resolver registered in acb_skills/integrations.py)."
            )
            unavailable[service_name] = reason
            _log.warning(
                "integrations.not_registered",
                service=service_name,
                mandatory=is_mandatory,
            )
            continue
        try:
            resolved[service_name] = resolver(settings)
            _log.debug("integrations.resolved", service=service_name)
        except IntegrationMisconfiguredError as exc:
            reason = str(exc)
            unavailable[service_name] = reason
            level = "error" if is_mandatory else "warning"
            getattr(_log, level)(
                "integrations.misconfigured",
                service=service_name,
                mandatory=is_mandatory,
                error=reason,
            )

    return resolved, unavailable


def list_registered() -> list[str]:
    """Return the list of service names known to the registry."""
    return sorted(_REGISTRY.keys())
