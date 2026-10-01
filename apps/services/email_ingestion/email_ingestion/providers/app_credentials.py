"""The OAuth *app* credentials of the mail providers, and their one reader.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.3, EM-T3a items 1
and 2.

The app credentials (``client_id`` and ``client_secret``) belong to the
deployment, not to a mailbox. They come from settings at the time of use and
from nowhere else. Three callers read them through :func:`oauth_app`: the
authorize leg and the token exchange in ``gateway.routes.email.transport.oauth``,
and :func:`email_ingestion.providers.factory.build_provider`.

⚠️ **The account blob is never a source.** An earlier callback copied the app
credentials into each ``email_accounts.credentials_encrypted`` blob. A blob
that still holds them is ignored, and :func:`token_fields` drops them on the
next token write. A fallback to the blob would keep a revoked secret alive.

⚠️ **Mail is multi-tenant.** The Microsoft authority is always ``common``. The
sign-in tenant variables (``AUTH_MICROSOFT_ENTRA_ID_TENANT`` and its kin) are
not read here, because a sign-in tenant must not make mail single-tenant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from acb_common import get_settings

#: The Microsoft identity platform endpoint for mail, for any organization
#: and for personal accounts.
MICROSOFT_AUTHORITY = "common"
MICROSOFT_OAUTH_BASE = (
    f"https://login.microsoftonline.com/{MICROSOFT_AUTHORITY}/oauth2/v2.0"
)

#: Keys that an old callback wrote into the account blob. They are app
#: credentials, so no provider reads them and no export writes them.
APP_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {"client_id", "client_secret", "tenant_id"}
)


@dataclass(frozen=True)
class OAuthApp:
    """The app credentials of one provider. Empty strings when unset."""

    client_id: str = ""
    client_secret: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)


def _first(*values: str | None) -> str:
    return next((v for v in values if v), "")


def oauth_app(provider: str) -> OAuthApp:
    """The app credentials of ``provider`` from settings, at the time of use.

    ``get_settings()`` is cached for the process. The key store writes a
    credential that an operator saves in Integrations to ``os.environ`` at
    run time, so the environment is read after settings. The names are the
    ones the gateway read before EM-T3a. An unknown provider (IMAP) has none.
    """
    settings = get_settings()
    env = os.environ.get
    if provider == "gmail":
        return OAuthApp(
            client_id=_first(
                settings.gmail_oauth_client_id, env("GMAIL_OAUTH_CLIENT_ID"),
            ),
            client_secret=_first(
                settings.gmail_oauth_client_secret, env("GMAIL_OAUTH_CLIENT_SECRET"),
            ),
        )
    if provider == "microsoft":
        return OAuthApp(
            client_id=_first(
                settings.msft_oauth_client_id,
                env("MSFT_OAUTH_CLIENT_ID"),
                env("AUTH_MICROSOFT_ENTRA_ID_ID"),
            ),
            client_secret=_first(
                settings.msft_oauth_client_secret,
                env("MSFT_OAUTH_CLIENT_SECRET"),
                env("AUTH_MICROSOFT_ENTRA_ID_SECRET"),
            ),
        )
    return OAuthApp()


def token_fields(credentials: dict[str, Any]) -> dict[str, Any]:
    """``credentials`` with every app credential key removed."""
    return {k: v for k, v in credentials.items() if k not in APP_CREDENTIAL_KEYS}
