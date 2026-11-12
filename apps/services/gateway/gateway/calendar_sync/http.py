"""The ONE network door of the calendar sync (WS-CAL CE-10, CE-11).

Every call of a provider goes through :class:`CalendarHttp`:

1. **A host allowlist.** The URL must be ``https`` on port 443, with no user
   name, on a host the provider lists (``kb_sources.http.check_host``).
2. **``outbound_guard.request_https``.** The gateway's one guard resolves the
   host once, refuses a private address, pins the socket, refuses a redirect
   and caps the time and the size of the answer.

Verbs: GET, a POST with a form body (the token call), and a JSON request with
POST, PATCH, PUT or DELETE (the write-out of CE-13). A test passes its own
object with the same methods (:class:`Http`), so no test needs DNS or a socket.

**No sync cursor in a log.** httpx logs each request URL at INFO. A filter on
the ``httpx`` logger replaces the value of a sync token, a delta token, a page
token and a skip token with ``[redacted]`` before a line leaves.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from gateway import outbound_guard
from gateway.calendar_sync.provider import ProviderError
from gateway.kb_sources.http import HttpAnswer, check_host

_CURSOR = re.compile(
    r"((?:sync|delta|page|skip)token=|\$deltatoken=|\$skiptoken=)[^&\s\"']+",
    re.IGNORECASE)


def redact_cursors(value: Any) -> Any:
    """*value* with each cursor value replaced, or *value* unchanged."""
    if isinstance(value, bool | int | float) or value is None:
        return value
    text = value if isinstance(value, str) else str(value)
    if not _CURSOR.search(text):
        return value
    return _CURSOR.sub(r"\1[redacted]", text)


class _CursorFilter(logging.Filter):
    cursor_filter = True  # keeps a reload from adding a second filter

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_cursors(a) for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact_cursors(record.msg)
        return True


def _install_filter() -> None:
    target = logging.getLogger("httpx")
    if not any(getattr(f, "cursor_filter", False) for f in target.filters):
        target.addFilter(_CursorFilter())


_install_filter()


class Http(Protocol):
    async def get(
        self, url: str, *, params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer: ...

    async def post_form(
        self, url: str, *, form: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer: ...

    async def send_json(
        self, method: str, url: str, *, body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer: ...


_JSON_VERBS = frozenset({"POST", "PATCH", "PUT", "DELETE"})


class CalendarHttp:
    """:class:`Http` over ``outbound_guard.request_https``, on one allowlist."""

    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        self._allowed = frozenset(h.lower() for h in allowed_hosts)

    async def _call(
        self, method: str, url: str, *, headers: Mapping[str, str] | None,
        content: bytes | None = None, json: Any = None,
    ) -> HttpAnswer:
        host = check_host(url, self._allowed)
        # The guard keeps the raw bytes of an answer and decodes nothing, so a
        # gzip body reads as an empty feed. Ask for plain bytes (Opus review P0).
        sent = {**dict(headers or {}), "Accept-Encoding": "identity"}
        try:
            answer = await outbound_guard.request_https(
                method, url, headers=sent, content=content, json=json)
        except outbound_guard.OutboundRefused as exc:
            raise ProviderError("guard_refused", f"the guard refused {host}: {exc.reason}") from None
        except (TimeoutError, httpx.HTTPError) as exc:
            raise ProviderError(
                "transport", f"the call to {host} failed ({type(exc).__name__})") from None
        return HttpAnswer(
            status_code=answer.status_code, body=answer.body,
            truncated=len(answer.body) >= outbound_guard.MAX_ANSWER_BYTES)

    async def get(
        self, url: str, *, params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer:
        full = f"{url}?{urlencode(dict(params))}" if params else url
        return await self._call("GET", full, headers=headers)

    async def post_form(
        self, url: str, *, form: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer:
        sent = {"Content-Type": "application/x-www-form-urlencoded", **dict(headers or {})}
        return await self._call(
            "POST", url, headers=sent, content=urlencode(dict(form)).encode("ascii"))

    async def send_json(
        self, method: str, url: str, *, body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpAnswer:
        verb = method.upper()
        if verb not in _JSON_VERBS:
            raise ValueError("a JSON request is POST, PATCH, PUT or DELETE")
        return await self._call(verb, url, headers=headers, json=body)


__all__ = ["CalendarHttp", "Http", "redact_cursors"]
