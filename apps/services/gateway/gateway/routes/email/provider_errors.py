"""The HTTP answer of an email route for a failure of the mail provider.

Before this module, ``GET /email/messages/{id}/full-body`` answered 500 for
every provider failure. The chat agent fetched several full bodies at once,
Microsoft Graph answered 429, and the agent read a server fault (production
log, October 2026). :func:`provider_http_error` maps the failure to an honest
status:

* a rate limit (a provider 429, or a ``ProviderRateLimited``): 429, with
  ``Retry-After`` and :data:`THROTTLED_DETAIL`.
* a 404 of the mail API: 404, with :data:`MESSAGE_GONE_DETAIL`. Outlook
  re-keys a moved message, and a deleted one is gone.
* a refused sign-in (a 401 or a 403 of the mail API after the one refresh,
  or a failure at the token endpoint): 409, with :data:`RECONNECT_DETAIL`.
  Not 401, because a 401 of the gateway reads as an expired session of the
  member, and the member's session is fine.
* anything else: 500. The log line names the route.

The provider clients make the one bounded retry (``OutlookProvider``
``_graph_read``, and ``GmailBearer`` for Gmail). This module only reads the
error that is left.

Fence: ``tests/unit/test_email_provider_errors.py``.
"""

from __future__ import annotations

from typing import Any

from email_ingestion.providers.base import ProviderRateLimited
from fastapi import HTTPException
from gateway.routes.email.core import _log

#: The wait that the route sends when the provider sends no usable value.
DEFAULT_RETRY_AFTER_SECS = 30
#: The longest ``Retry-After`` of a provider that the route passes on.
RETRY_AFTER_MAX_SECS = 300

THROTTLED_DETAIL = (
    "The mail provider is throttling requests. Try again in {seconds} seconds."
)
MESSAGE_GONE_DETAIL = (
    "This message is no longer in the mailbox (moved or deleted)."
)
RECONNECT_DETAIL = (
    "The mail provider refused the sign-in of this mailbox. Reconnect the "
    "mailbox in Email settings, then try again."
)

#: The hosts of the OAuth token endpoints. A failed refresh there is a
#: refused sign-in, whatever its status.
_TOKEN_HOSTS = ("login.microsoftonline.com", "oauth2.googleapis.com",
                "accounts.google.com")


def _chain(exc: BaseException) -> list[BaseException]:
    """*exc* and its causes, at most 8, with no loop."""
    out: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and cur not in out and len(out) < 8:
        out.append(cur)
        cur = cur.__cause__
    return out


def provider_429_response(exc: BaseException) -> Any:
    """The HTTP answer of a provider 429 in the chain of *exc*, else None.

    A ``ProviderRateLimited`` counts with or without an answer, because a
    provider raises it when its own tries are spent (``GmailRateLimited``).
    It then gives its answer, or True.
    """
    for cur in _chain(exc):
        response = getattr(cur, "response", None)
        if getattr(response, "status_code", None) == 429:
            return response
        if isinstance(cur, ProviderRateLimited):
            return response if response is not None else True
    return None


def is_provider_429(exc: BaseException) -> bool:
    """True when the provider refused the request for a rate limit."""
    return provider_429_response(exc) is not None


def provider_retry_after(exc: BaseException) -> int:
    """The ``Retry-After`` that the route sends for a provider 429.

    The seconds of the provider's own header, from 1 to
    :data:`RETRY_AFTER_MAX_SECS`. Else :data:`DEFAULT_RETRY_AFTER_SECS`.
    """
    response = provider_429_response(exc)
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    try:
        wait = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER_SECS
    if 1 <= wait <= RETRY_AFTER_MAX_SECS:
        return wait
    return DEFAULT_RETRY_AFTER_SECS


def _http_failure(exc: BaseException) -> tuple[int, str] | None:
    """``(status, host)`` of the first HTTP answer in the chain of *exc*."""
    for cur in _chain(exc):
        response = getattr(cur, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            request = getattr(cur, "request", None) or getattr(response, "request", None)
            try:
                host = str(request.url.host) if request is not None else ""
            except Exception:  # an httpx error with no request
                host = ""
            return status, host
    return None


def is_message_gone(exc: BaseException) -> bool:
    """True for a 404 of the mail API: the message moved or was deleted."""
    got = _http_failure(exc)
    return got is not None and got[0] == 404 and got[1] not in _TOKEN_HOSTS


def is_sign_in_refused(exc: BaseException) -> bool:
    """True when the provider refused the sign-in of the mailbox.

    A 401 or a 403 of the mail API (a rate limit excluded), or any failed
    answer of a token endpoint.
    """
    if is_provider_429(exc):
        return False
    got = _http_failure(exc)
    if got is None:
        return False
    status, host = got
    if host in _TOKEN_HOSTS:
        return status >= 400
    return status in (401, 403)


def throttled(seconds: int) -> HTTPException:
    """The 429 that a route answers for a provider rate limit."""
    return HTTPException(
        status_code=429,
        detail=THROTTLED_DETAIL.format(seconds=seconds),
        headers={"Retry-After": str(seconds)},
    )


def reconnect() -> HTTPException:
    """The 409 that a route answers when the provider refused the sign-in."""
    return HTTPException(status_code=409, detail=RECONNECT_DETAIL)


def provider_http_error(
    exc: BaseException, *, route: str, **log_fields: Any,
) -> HTTPException:
    """The HTTPException that *route* raises for the provider failure *exc*.

    Each branch logs one line with the route name and the class of the
    error. A line holds no mail text and no URL, because the URL of a Graph
    upload session holds a token.
    """
    if is_provider_429(exc):
        wait = provider_retry_after(exc)
        _log.warning("email.provider_throttled", route=route,
                     retry_after=wait, **log_fields)
        return throttled(wait)
    if is_message_gone(exc):
        _log.info("email.provider_message_gone", route=route, **log_fields)
        return HTTPException(status_code=404, detail=MESSAGE_GONE_DETAIL)
    if is_sign_in_refused(exc):
        _log.warning("email.provider_sign_in_refused", route=route, **log_fields)
        return reconnect()
    got = _http_failure(exc)
    _log.error("email.provider_failed", route=route,
               error=type(exc).__name__,
               status=got[0] if got else None, **log_fields)
    return HTTPException(
        status_code=500,
        detail="The mail provider failed. Try again later.",
    )
