"""Google OAuth for a Drive source: the consent URL, the code, the refresh, the revoke.

Spec: ``project-docs/specs/knowledge_curation.md`` §7.1 and §7.4, and
``project-docs/specs/knowledge_base.md`` §7.4 (the Google grant).

Metorite owns ONE Google OAuth client (D-EM-5). The Gmail connect uses it,
and so does Drive. The route reads its id and secret through
``email_ingestion.providers.app_credentials.oauth_app("gmail")`` and passes
them in as an :class:`OAuthClientConfig`. This module reads no settings.

Four rules bind this module:

* **One read scope.** :data:`DRIVE_READONLY` is the only Drive scope. The
  consent asks for :data:`SCOPES`, and a grant may hold only what
  :data:`ALLOWED_SCOPES` lists: ``drive.readonly`` and the identity scopes,
  in their short and their URL form.
* **No incremental grant.** The consent URL never sends
  ``include_granted_scopes``. So the Drive token holds the Drive scope only,
  and never the Gmail scopes of the same Google account.
* **Two POSTs, both form bodies.** The token call and the revoke call. The
  secret and the tokens never sit in a URL.
* **A revoke ends the WHOLE Google grant**, Gmail included. So only the
  route decides to revoke, after the shared-grant check
  (``routes/knowledge/sources.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from gateway.kb_sources.base import (
    SCOPE_MEMBER,
    NeedsReconnect,
    OAuthClientConfig,
    SourceCredential,
    SourceError,
)
from gateway.kb_sources.http import Http, HttpAnswer

#: The one Drive scope (§7.1). Read only.
DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

#: The scopes the consent asks for. ``openid`` and ``email`` name the
#: account. The route checks the grant against :data:`ALLOWED_SCOPES`.
SCOPES: tuple[str, ...] = ("openid", "email", DRIVE_READONLY)

#: Every scope a Drive grant may hold. Google answers ``email`` as its URL
#: form, so both forms are listed. Anything else refuses the grant.
ALLOWED_SCOPES: frozenset[str] = frozenset({
    DRIVE_READONLY,
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
})

#: The Google hosts a Drive source may call. ``accounts.google.com`` is only
#: in the consent URL, which the browser opens.
CONSENT_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_HOSTS: frozenset[str] = frozenset({
    "accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com",
})

#: The ``provider_meta`` keys of a Drive credential. Public facts only,
#: because the store copies them to a plain column. ``account_email`` is the
#: address that the shared-grant functions of migration 300 compare.
META_ACCOUNT_EMAIL = "account_email"
META_ACCOUNT_ID = "account_id"

#: A dead refresh token. ``invalid_client`` is the platform client being
#: wrong, and ``shared_token`` stops on it too.
_RECONNECT_ERRORS = frozenset({"invalid_grant"})

#: Refresh a token this long before it expires.
_EXPIRY_SKEW = timedelta(minutes=5)

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    """The current time in UTC. Tests pass their own clock."""
    return datetime.now(UTC)


def needs_refresh(cred: SourceCredential, now: datetime) -> bool:
    """True when the access token expires within five minutes."""
    return cred.expires_at - _EXPIRY_SKEW <= now


def safe_code(value: object) -> str:
    """A Google error code when it looks like one, else ``""``."""
    code = str(value or "").strip()
    if code and len(code) <= 40 and code.replace("_", "").isalnum():
        return code
    return ""


def consent_url(config: OAuthClientConfig, *, state: str, scope_kind: str) -> str:
    """The consent page of the one Google client, for a member source."""
    if scope_kind != SCOPE_MEMBER:
        raise ValueError("KC-1a builds the member source only")
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        # A new refresh token on each consent, so a reconnect always has one.
        "prompt": "consent",
        "state": state,
    }
    return f"{CONSENT_URL}?{urlencode(params)}"


def _error(answer: HttpAnswer) -> SourceError:
    body = answer.json()
    code = safe_code(body.get("error") if isinstance(body, Mapping) else "")
    exc_type = NeedsReconnect if code in _RECONNECT_ERRORS else SourceError
    exc = exc_type(f"Google refused the token call ({code or answer.status_code})")
    exc.code = code
    return exc


def _expiry(body: Mapping[str, Any], now: datetime) -> datetime:
    try:
        seconds = int(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        seconds = 3600
    return now + timedelta(seconds=max(60, seconds))


def _scopes(body: Mapping[str, Any]) -> tuple[str, ...]:
    raw = body.get("scope")
    return tuple(raw.split()) if isinstance(raw, str) else ()


async def exchange_code(
    config: OAuthClientConfig, http: Http, *, code: str, clock: Clock = utcnow,
) -> SourceCredential:
    """The code for tokens. A grant with no refresh token still returns, with
    an empty ``refresh_token``, and the route refuses it."""
    answer = await http.post(TOKEN_URL, form={
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "grant_type": "authorization_code",
    })
    body = answer.json()
    if answer.status_code != 200 or not isinstance(body, Mapping) or not body.get("access_token"):
        raise _error(answer)
    return SourceCredential(
        access_token=str(body["access_token"]),
        refresh_token=str(body.get("refresh_token") or ""),
        expires_at=_expiry(body, clock()),
        scopes=_scopes(body),
    )


async def refresh(
    config: OAuthClientConfig, http: Http, cred: SourceCredential, *, clock: Clock = utcnow,
) -> SourceCredential:
    """A new access token. Google keeps the refresh token, so it stays."""
    if not cred.refresh_token:
        raise NeedsReconnect("The Drive connection holds no refresh token")
    answer = await http.post(TOKEN_URL, form={
        "refresh_token": cred.refresh_token,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "grant_type": "refresh_token",
    })
    body = answer.json()
    if answer.status_code != 200 or not isinstance(body, Mapping) or not body.get("access_token"):
        raise _error(answer)
    return replace(
        cred,
        access_token=str(body["access_token"]),
        refresh_token=str(body.get("refresh_token") or cred.refresh_token),
        expires_at=_expiry(body, clock()),
        scopes=_scopes(body) or cred.scopes,
    )


async def revoke(http: Http, cred: SourceCredential) -> bool:
    """End the Google grant. True when Google answers 200.

    ⚠️ This ends the grant of the Google account on the one client, so the
    Gmail token of the same account ends too. Only the route calls it, and
    only when the shared-grant check answered false.
    """
    token = cred.refresh_token or cred.access_token
    if not token:
        return False
    answer = await http.post(REVOKE_URL, form={"token": token})
    return answer.status_code == 200


__all__ = [
    "ALLOWED_SCOPES",
    "CONSENT_URL",
    "DRIVE_READONLY",
    "GOOGLE_HOSTS",
    "META_ACCOUNT_EMAIL",
    "META_ACCOUNT_ID",
    "REVOKE_URL",
    "SCOPES",
    "TOKEN_URL",
    "consent_url",
    "exchange_code",
    "needs_refresh",
    "refresh",
    "revoke",
    "safe_code",
    "utcnow",
]
