"""The startup check of the secrets that a service needs (task 574, WS-2d, BO-8).

Spec: ``docs/secrets_drop.md`` §6. Fence: ``tests/unit/test_secret_startup_check.py``.

A box that never set a secret runs on the ``Settings`` default. That default
is empty or public, so a value that should prove something proves nothing.
``check_startup_secrets`` is the ONE check that finds such a secret when a
service starts. Each service calls it once, from its own start.

* ``ACB_ENV=dev`` checks nothing. A laptop runs on the public defaults.
* ``SECRET_STARTUP_CHECK=shadow`` (the default) logs one warning for each
  unusable secret, then the service starts as before.
* ``SECRET_STARTUP_CHECK=enforce`` refuses to start, and the error names
  each setting.

A secret is unusable when it is empty or on ``member_proof.PUBLIC_DEFAULT_SECRETS``
(``member_proof._usable``). This module keeps no second deny list.

🔴 A log line and an error name the SETTING only, never its value.

🔴 The flip to ``enforce`` is the grant ``enforcement-flip``. Flip it on a box
only after its shadow lines show ``unusable=0`` for each unit. A refusal at
start takes the unit down at the next release, and we cannot roll back.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from acb_common import get_logger
from acb_common.settings import get_settings, secret_startup_check_mode

from acb_auth.member_proof import _usable

__all__ = [
    "NOT_A_SECRET",
    "OPTIONAL",
    "REQUIRED",
    "SERVICES",
    "Need",
    "SecretsUnusable",
    "check_startup_secrets",
    "unusable_secrets",
]

_log = get_logger(__name__)

#: The services that call the check. Each name is the argument of its call.
#: The orchestrator runs inside the gateway process, so the gateway covers it.
SERVICES = ("gateway", "agent_edge")


@dataclass(frozen=True)
class Need:
    """One secret that a service cannot work without."""

    setting: str
    services: frozenset[str]
    #: A bool setting that turns the need on. None: always needed.
    when: str | None = None
    #: Why the service needs it, for the table of the spec.
    why: str = ""


_GW = frozenset({"gateway"})
_BOTH = frozenset({"gateway", "agent_edge"})

#: The secrets that each service needs. Keep this list small: each entry can
#: stop a unit under ``enforce``.
REQUIRED: tuple[Need, ...] = (
    Need("gateway_session_secret", _GW,
         why="signs the member proof, the OAuth state and the email links"),
    Need("gateway_internal_token", _GW,
         why="service identity: the control plane and the jobs call with it"),
    Need("acb_master_key", _GW,
         why="decrypts the stored provider keys and connection credentials"),
    Need("litellm_master_key", _GW,
         why="the only lock of /v1, which spends the stored provider keys"),
    Need("database_url", _BOTH,
         why="the database login (the check reads the password only)"),
    Need("agent_edge_signing_key", _BOTH, when="agent_access_enabled",
         why="the HMAC of each request from the agent edge to the gateway"),
)

#: Each secret-like setting that no service needs at start, and why.
OPTIONAL: MappingProxyType[str, str] = MappingProxyType({
    # Provider keys: deprecated, the key store holds them (acb_llm.key_store).
    **{name: "deprecated bootstrap key; the key store holds provider keys"
       for name in ("gemini_api_key", "openai_api_key", "anthropic_api_key",
                    "openrouter_api_key", "deepseek_api_key", "groq_api_key",
                    "mistral_api_key", "together_api_key")},
    # Integrations: a feature with no credential is not configured.
    **{name: "integration credential; empty means the integration is not set up"
       for name in ("zoho_client_secret", "zoho_refresh_token", "zoho_access_token",
                    "apollo_api_key", "google_maps_api_key", "instantly_api_key",
                    "smtp_password", "serpapi_api_key", "apify_api_token",
                    "anymailfinder_api_key", "github_token", "github_read_token",
                    "whatsapp_assistant_access_token")},
    "zoho_webhook_secret": "its reader refuses: /webhooks/zoho answers 401 when empty",
    "gmail_pubsub_token": "its reader refuses: /webhooks/gmail answers 401 when empty",
    "gmail_oauth_client_secret": "mail connect; empty reads as not configured",
    "msft_oauth_client_secret": "mail connect; empty reads as not configured",
    "crm_zoho_client_secret": "CRM connect; the start route answers 503 when empty",
    "github_app_client_secret": "GitHub App; empty means the App is not set up",
    "github_app_webhook_secret": "its reader refuses: the GitHub webhook answers 503",
    "mutation_pr_token": "self-mutation PRs; falls back to github_token, gated",
    "object_store_secret_access_key": "object store; a call with no key fails at use",
    "object_store_auth_password": "object store; a call with no password fails at use",
    "vapid_private_key": "web push; empty sends no push",
    "customer_console_deployment_key": "only a box on the deployment key sets it",
    "customer_console_org_key": "only a box on the org key sets it",
    "web_worker_token": "its reader refuses: the web worker does not start when empty",
    "neo4j_password": "its reader refuses: Graphiti does not connect when empty",
    "agent_edge_signing_key_previous": "only set during a key rotation",
    "studio_direct_key": "its reader refuses: acb_auth.direct_proof writes nothing",
    # Not secrets: the name matches, the value is public.
    "vapid_public_key": "not a secret: the public half of the VAPID pair",
    "object_store_access_key_id": "not a secret: the project ref of the store",
    "zoho_token_expiry": "not a secret: an expiry time",
    "github_app_private_key_path": "not a secret: a file path",
    "secret_startup_check": "not a secret: the mode of this check",
})

#: The reason prefix of a name that holds no secret. Its value may be public.
NOT_A_SECRET = "not a secret:"


class SecretsUnusable(RuntimeError):
    """A service refuses to start. ``names`` holds the settings, never a value."""

    def __init__(self, service: str, names: tuple[str, ...]) -> None:
        self.service = service
        self.names = names
        listed = ", ".join(names) if names else "the check itself failed"
        super().__init__(
            f"{service} refuses to start: these secret settings are empty or "
            f"public: {listed}. Set each one, or set SECRET_STARTUP_CHECK=shadow "
            "(docs/secrets_drop.md §6)."
        )


def _value_usable(setting: str, raw: Any) -> bool:
    value = str(raw or "")
    if setting == "database_url":
        # A URL is not a secret. Its password is. A login with no password
        # (a socket or peer login) has no default to leak.
        try:
            password = urlsplit(value).password
        except ValueError:
            return False
        return password is None or _usable(password)
    return _usable(value)


def unusable_secrets(service: str, settings: Any) -> list[str]:
    """The sorted names of the required secrets of *service* that are unusable."""
    if service not in SERVICES:
        raise ValueError(f"unknown service {service!r}; expected one of {SERVICES}")
    bad = []
    for need in REQUIRED:
        if service not in need.services:
            continue
        if need.when is not None and not bool(getattr(settings, need.when, False)):
            continue
        if not _value_usable(need.setting, getattr(settings, need.setting, "")):
            bad.append(need.setting)
    return sorted(bad)


def check_startup_secrets(service: str, settings: Any = None) -> list[str]:
    """Check the secrets of *service* at its start. Call it once, from the start.

    Returns the unusable names (empty in dev). Under ``enforce`` it raises
    :class:`SecretsUnusable` instead. Under ``shadow`` it never raises, not
    even on a bug of its own, so the shadow cannot take a unit down.
    """
    if service not in SERVICES:
        raise ValueError(f"unknown service {service!r}; expected one of {SERVICES}")
    if settings is None:
        settings = get_settings()
    if str(getattr(settings, "acb_env", "prod")) == "dev":
        return []
    mode = secret_startup_check_mode(settings)
    try:
        bad = unusable_secrets(service, settings)
    except Exception as exc:
        _log.error("secrets.startup_check_failed", service=service, mode=mode,
                   error=type(exc).__name__)
        if mode == "enforce":
            raise SecretsUnusable(service, ()) from None
        return []
    for name in bad:
        _log.warning("secrets.unusable", service=service, setting=name, mode=mode)
    _log.info("secrets.startup_check", service=service, mode=mode, unusable=len(bad))
    if bad and mode == "enforce":
        raise SecretsUnusable(service, tuple(bad))
    return bad
