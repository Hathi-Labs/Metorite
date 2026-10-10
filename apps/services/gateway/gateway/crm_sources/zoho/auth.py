"""Zoho OAuth for one connection: the consent URL, the code exchange, the refresh.

One OAuth client serves every Zoho data centre (multi-DC). The consent request
goes to ``accounts.zoho.com``. The redirect returns ``location`` and
``accounts-server``, and the token request goes to that server. The token
response returns ``api_domain``, and every later API call goes there.

Three rules bind this module:

* **One allowlist.** :data:`ZOHO_DATA_CENTRES` is the only list of Zoho hosts.
  An ``accounts_server`` or an ``api_domain`` that is not on it, or that is not
  https, raises :class:`UntrustedHost` before a request leaves. A redirect
  parameter is request input, so it is never trusted as it is.
* **No storage.** Each function returns a new :class:`SourceCredential`. The
  caller stores it (CRM-Z2). This module reads no settings and no file.
* **No secret in a message.** The token call sends the secret in the form
  body, never in the URL, because httpx logs the URL of each request. An error
  names the Zoho error code or the HTTP status, and nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from gateway.crm_sources.base import (
    NeedsReconnect,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceError,
    UntrustedHost,
)


@dataclass(frozen=True)
class DataCentre:
    """The two hosts of one Zoho data centre."""

    accounts_host: str
    api_host: str


#: The ONE allowlist of Zoho hosts. Keyed by the ``location`` that the
#: consent redirect returns. Source: zoho.com/developer/oauth/
#: multi-dc-support.html, read 2026-10-11, which lists nine data centres.
ZOHO_DATA_CENTRES: Mapping[str, DataCentre] = MappingProxyType(
    {
        "us": DataCentre("accounts.zoho.com", "www.zohoapis.com"),
        "eu": DataCentre("accounts.zoho.eu", "www.zohoapis.eu"),
        "in": DataCentre("accounts.zoho.in", "www.zohoapis.in"),
        "au": DataCentre("accounts.zoho.com.au", "www.zohoapis.com.au"),
        "jp": DataCentre("accounts.zoho.jp", "www.zohoapis.jp"),
        "ca": DataCentre("accounts.zohocloud.ca", "www.zohoapis.ca"),
        "cn": DataCentre("accounts.zoho.com.cn", "www.zohoapis.com.cn"),
        "sa": DataCentre("accounts.zoho.sa", "www.zohoapis.sa"),
        "uk": DataCentre("accounts.zoho.uk", "www.zohoapis.uk"),
    }
)

ACCOUNTS_HOSTS: frozenset[str] = frozenset(dc.accounts_host for dc in ZOHO_DATA_CENTRES.values())
API_HOSTS: frozenset[str] = frozenset(dc.api_host for dc in ZOHO_DATA_CENTRES.values())

#: The keys of ``SourceCredential.provider_meta`` for Zoho.
META_ACCOUNTS_SERVER = "accounts_server"
META_API_DOMAIN = "api_domain"
META_LOCATION = "location"

#: A multi-DC client always starts the consent at the US accounts server.
CONSENT_SERVER = "https://accounts.zoho.com"

#: Zoho answers a dead refresh token with ``invalid_code`` (often with HTTP
#: 200). ``invalid_grant`` is the OAuth word for the same thing.
_RECONNECT_ERRORS = frozenset({"invalid_grant", "invalid_code"})

#: Refresh a token this long before it expires.
_EXPIRY_SKEW = timedelta(minutes=5)

_TOKEN_TIMEOUT = 30.0

#: An error code is echoed into a message only when it looks like a code.
_SAFE_CODE = re.compile(r"^[A-Za-z_ ]{1,40}$")

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    """The current time in UTC. Tests pass their own clock."""
    return datetime.now(UTC)


# ── The allowlist check ─────────────────────────────────────────────────────


def _checked_origin(url: str, allowed: frozenset[str], what: str) -> str:
    """Return ``https://<host>`` when ``url`` is a bare https origin on ``allowed``."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UntrustedHost(f"The Zoho {what} is not a valid URL") from exc
    host = parts.hostname or ""
    if parts.scheme != "https":
        raise UntrustedHost(f"The Zoho {what} must use https")
    if (
        host not in allowed
        or port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise UntrustedHost(f"The Zoho {what} {host[:80]!r} is not on the allowlist")
    return f"https://{host}"


def check_accounts_server(url: str) -> str:
    """The origin of an accounts server, or :class:`UntrustedHost`."""
    return _checked_origin(url, ACCOUNTS_HOSTS, "accounts server")


def check_api_domain(url: str) -> str:
    """The origin of an API domain, or :class:`UntrustedHost`."""
    return _checked_origin(url, API_HOSTS, "API domain")


def meta(credential: SourceCredential, key: str) -> str:
    """One key of ``provider_meta``, or ``""`` when it is not there."""
    return str(credential.provider_meta.get(key) or "")


def safe_code(value: object) -> str:
    text = str(value or "")
    return text if _SAFE_CODE.match(text) else ""


# ── Consent ─────────────────────────────────────────────────────────────────


def consent_url(config: OAuthClientConfig, *, scopes: Sequence[str], state: str) -> str:
    """The URL that sends an admin to Zoho to consent.

    The caller passes the scopes and a signed ``state`` (CRM-Z2 owns both).
    ``access_type=offline`` asks for a refresh token, and ``prompt=consent``
    makes Zoho issue one again on a reconnect.
    """
    if not scopes:
        raise ValueError("A Zoho consent needs at least one scope")
    if not state:
        raise ValueError("A Zoho consent needs a state")
    query = urlencode(
        {
            "scope": ",".join(scopes),
            "client_id": config.client_id,
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "redirect_uri": config.redirect_uri,
            "state": state,
        }
    )
    return f"{CONSENT_SERVER}/oauth/v2/auth?{query}"


# ── The token endpoint ──────────────────────────────────────────────────────


async def _token_call(
    accounts_server: str,
    form: dict[str, str],
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[int, dict[str, Any]]:
    """One POST to the token endpoint. It adds no credit and never retries."""
    server = check_accounts_server(accounts_server)
    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT, transport=transport) as http:
        r = await http.post(f"{server}/oauth/v2/token", data=form)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


def _is_token_throttle(error: str, body: Mapping[str, Any]) -> bool:
    """Zoho's accounts server throttles with ``Access Denied`` and a text that
    says "too many requests", often with HTTP 400."""
    description = str(body.get("error_description") or "").lower()
    return error.lower() == "access denied" and "too many requests" in description


def _expiry(body: Mapping[str, Any], now: datetime) -> datetime:
    try:
        seconds = int(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        seconds = 3600
    return now + timedelta(seconds=seconds)


async def exchange_code(
    config: OAuthClientConfig,
    *,
    code: str,
    accounts_server: str,
    scopes: Sequence[str],
    location: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = utcnow,
) -> SourceCredential:
    """Trade the consent code for tokens at the data centre that issued it."""
    server = check_accounts_server(accounts_server)
    status, body = await _token_call(
        server,
        {
            "grant_type": "authorization_code",
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "redirect_uri": config.redirect_uri,
            "code": code,
        },
        transport,
    )
    error = safe_code(body.get("error"))
    if status >= 300 or body.get("error") or not body.get("access_token"):
        raise SourceError(f"Zoho refused the consent code ({error or status})")
    if not body.get("refresh_token"):
        raise SourceError("Zoho sent no refresh token. Consent again with access_type=offline")
    api_domain = check_api_domain(str(body.get("api_domain") or ""))
    provider_meta = {META_ACCOUNTS_SERVER: server, META_API_DOMAIN: api_domain}
    if location:
        provider_meta[META_LOCATION] = location
    return SourceCredential(
        access_token=str(body["access_token"]),
        refresh_token=str(body["refresh_token"]),
        expires_at=_expiry(body, clock()),
        scopes=tuple(scopes),
        provider_meta=provider_meta,
    )


async def refresh(
    config: OAuthClientConfig,
    credential: SourceCredential,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = utcnow,
) -> SourceCredential:
    """A new access token for ``credential``. It returns a new credential.

    Only ``invalid_grant`` and ``invalid_code`` raise :class:`NeedsReconnect`,
    and a dead token is never retried. The status code alone decides nothing.
    A throttle (``Access Denied``, too many requests) raises
    :class:`RateLimited`. Any other refusal, ``invalid_client`` for example,
    raises :class:`SourceError`, because a new consent does not repair it.
    """
    status, body = await _token_call(
        meta(credential, META_ACCOUNTS_SERVER),
        {
            "grant_type": "refresh_token",
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "refresh_token": credential.refresh_token,
        },
        transport,
    )
    raw_error = str(body.get("error") or "")
    error = safe_code(raw_error)
    if raw_error in _RECONNECT_ERRORS:
        raise NeedsReconnect(f"Zoho refused the refresh ({error}). An admin must connect again")
    if _is_token_throttle(raw_error, body):
        raise RateLimited("The Zoho accounts server throttled the refresh", tries=1)
    if status >= 300 or raw_error or not body.get("access_token"):
        raise SourceError(f"The Zoho token refresh failed ({error or status})")
    provider_meta = dict(credential.provider_meta)
    if body.get("api_domain"):
        provider_meta[META_API_DOMAIN] = check_api_domain(str(body["api_domain"]))
    return replace(
        credential,
        access_token=str(body["access_token"]),
        expires_at=_expiry(body, clock()),
        provider_meta=provider_meta,
    )


def needs_refresh(credential: SourceCredential, now: datetime) -> bool:
    """True when the access token is missing or expires within five minutes."""
    return not credential.access_token or now >= credential.expires_at - _EXPIRY_SKEW


__all__ = [
    "ACCOUNTS_HOSTS",
    "API_HOSTS",
    "CONSENT_SERVER",
    "ZOHO_DATA_CENTRES",
    "DataCentre",
    "check_accounts_server",
    "check_api_domain",
    "consent_url",
    "META_ACCOUNTS_SERVER",
    "META_API_DOMAIN",
    "META_LOCATION",
    "meta",
    "exchange_code",
    "needs_refresh",
    "refresh",
    "safe_code",
    "utcnow",
]
