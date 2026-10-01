"""Provider factory — the one place that maps a provider name to its concrete
:class:`~email_ingestion.providers.base.BaseEmailProvider` implementation.

This lives in the ``email_ingestion.providers`` package (the layer that owns
the provider classes) so every caller — the gateway routes *and* the ingestion
scheduler — imports **down** into it rather than re-deriving the name→class
``if/elif`` inline. Adding a provider now means editing this function only.

The provider imports are done lazily inside :func:`build_provider` so importing
this module stays cheap and free of import cycles, matching the previous
call-site behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from email_ingestion.providers.base import BaseEmailProvider

# Canonical provider identifiers as stored on ``email_accounts.provider``.
# "microsoft" is the stored value for Outlook / Microsoft Graph accounts.
KNOWN_PROVIDERS: frozenset[str] = frozenset({"gmail", "microsoft", "imap"})


def build_provider(provider_name: str, creds: dict[str, Any]) -> BaseEmailProvider:
    """Construct the email provider for ``provider_name`` from decrypted creds.

    Raises :class:`ValueError` for an unknown provider. Gateway callers
    translate this into an HTTP 400 (see
    ``gateway.routes.email.core._instantiate_provider``); the ingestion
    scheduler surfaces it as a sync failure.

    The OAuth providers get their app credentials here, from settings through
    :func:`~email_ingestion.providers.app_credentials.oauth_app`. A
    ``client_id`` or ``client_secret`` in ``creds`` is ignored (EM-T3a item 1).
    """
    if provider_name == "gmail":
        from email_ingestion.providers.app_credentials import oauth_app
        from email_ingestion.providers.gmail import GmailProvider
        return GmailProvider(creds, app=oauth_app("gmail"))
    if provider_name == "microsoft":
        from email_ingestion.providers.app_credentials import oauth_app
        from email_ingestion.providers.outlook import OutlookProvider
        return OutlookProvider(creds, app=oauth_app("microsoft"))
    if provider_name == "imap":
        from email_ingestion.providers.imap import IMAPProvider
        return IMAPProvider(creds)
    raise ValueError(f"Unknown provider: {provider_name}")
