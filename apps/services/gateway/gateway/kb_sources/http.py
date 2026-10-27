"""The ONE network door of a file source (WS-61 KC-1a).

Spec: ``project-docs/specs/knowledge_base.md`` §7.5: "It never fetches a URL a
member typed. The provider's URLs come from the provider's own listing, and
``outbound_guard.py`` still checks the host."

Every call of an adapter goes through :class:`GuardedHttp`:

1. **The adapter's host allowlist.** The URL must be ``https`` on port 443,
   with no user name, and its host must be on the list the adapter gives.
   Another host raises :class:`UntrustedHost` before a request leaves.
2. **``outbound_guard.request_https``.** The gateway's one guard resolves the
   host once, refuses a private address, pins the socket, refuses a redirect
   and caps the time and the size of the answer.

Only two verbs exist here: :meth:`GuardedHttp.get` and
:meth:`GuardedHttp.post`. ``post`` sends a form body, for the token call and
the revoke call, so a token never sits in a URL. No other verb has a method,
and ``tests/unit/test_kc_sources_read_only_scopes.py`` refuses a write verb
anywhere in ``kb_sources``.

A test passes its own object with the same two methods (:class:`Http`), so
no test needs DNS or a socket.

**No page token in a log (KC-3).** The Drive change feed takes its cursor as
the ``pageToken`` query value, and httpx logs each request URL at INFO. A
filter on the ``httpx`` logger replaces each ``pageToken`` value with
``[redacted]`` before a line leaves. Fence:
``tests/unit/test_kb_sync_engine.py``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

import httpx

from gateway import outbound_guard
from gateway.kb_sources.base import SourceError, UntrustedHost

#: The value of a ``pageToken`` in a URL or a line of text.
_PAGE_TOKEN = re.compile(r"(pageToken=)[^&\s\"']+")


def redact_page_tokens(value: Any) -> Any:
    """*value* with each ``pageToken`` value replaced, or *value* unchanged.

    httpx passes the URL as an ``httpx.URL``, not as text, so any value whose
    text holds a page token comes back as redacted text.
    """
    if isinstance(value, bool | int | float) or value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    if "pageToken=" not in text:
        return value
    return _PAGE_TOKEN.sub(r"\1[redacted]", text)


class _PageTokenFilter(logging.Filter):
    """Redact the page token of each ``httpx`` request line."""

    #: The mark that keeps a reload of this module from adding a second filter.
    page_token_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_page_tokens(a) for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact_page_tokens(record.msg)
        return True


def _install_page_token_filter() -> None:
    """Add :class:`_PageTokenFilter` to the ``httpx`` logger, once."""
    target = logging.getLogger("httpx")
    if not any(getattr(f, "page_token_filter", False) for f in target.filters):
        target.addFilter(_PageTokenFilter())


_install_page_token_filter()


@dataclass(frozen=True)
class HttpAnswer:
    """The status of an answer and its body. ``truncated`` is True when the
    guard's cap cut the body, so the JSON cannot be trusted whole."""

    status_code: int
    body: bytes
    truncated: bool = False

    def json(self) -> Any:
        """The body as JSON, or ``{}`` when it is empty or not JSON."""
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}


class Http(Protocol):
    """The two calls an adapter may make."""

    async def get(
        self, url: str, *, params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer: ...

    async def post(
        self, url: str, *, form: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer: ...


def check_host(url: str, allowed: frozenset[str]) -> str:
    """The host of *url* when it is an https URL on *allowed*, else raise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UntrustedHost("the source URL is not valid") from exc
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port not in (None, 443):
        raise UntrustedHost("a source URL must use https on port 443")
    if parts.username is not None or parts.password is not None:
        raise UntrustedHost("a source URL holds no user name")
    if host not in allowed:
        raise UntrustedHost(f"the host {host[:100]} is not on the source allowlist")
    return host


class GuardedHttp:
    """:class:`Http` over ``outbound_guard.request_https``, on one allowlist."""

    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self._allowed = frozenset(h.lower() for h in allowed_hosts)

    async def _call(
        self, method: str, url: str, *, headers: Mapping[str, str] | None,
        content: bytes | None = None,
    ) -> HttpAnswer:
        host = check_host(url, self._allowed)
        try:
            answer = await outbound_guard.request_https(
                method, url, headers=dict(headers or {}), content=content)
        except outbound_guard.OutboundRefused as exc:
            # The guard names the host and a reason, never the path.
            raise SourceError(f"the guard refused {host}: {exc.reason}") from None
        except (TimeoutError, httpx.HTTPError) as exc:
            raise SourceError(f"the call to {host} failed ({type(exc).__name__})") from None
        return HttpAnswer(
            status_code=answer.status_code,
            body=answer.body,
            truncated=len(answer.body) >= outbound_guard.MAX_ANSWER_BYTES,
        )

    async def get(
        self, url: str, *, params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer:
        full = f"{url}?{urlencode(dict(params))}" if params else url
        return await self._call("GET", full, headers=headers)

    async def post(
        self, url: str, *, form: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer:
        sent = {"Content-Type": "application/x-www-form-urlencoded", **dict(headers or {})}
        return await self._call(
            "POST", url, headers=sent, content=urlencode(dict(form)).encode("ascii"))


__all__ = ["GuardedHttp", "Http", "HttpAnswer", "check_host", "redact_page_tokens"]
