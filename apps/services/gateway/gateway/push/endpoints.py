"""The rule for a push service URL (WS-66 PN-1).

A browser gives the endpoint, and the member's request carries it to us. So
it is member input, and the sender POSTs to it. Without a rule, a member
could save ``https://10.0.0.5/admin`` and make the gateway POST there with
``POST /push/test`` (SSRF).

``gateway/outbound_guard.py`` is the one guard for a URL that a member names
for ANY host. A push endpoint is not any host. It is one of the few push
services that a browser uses, so this rule is narrower: ``https`` on port
443, no user name, and a host of :data:`PUSH_SERVICE_HOSTS` or a subdomain of
:data:`PUSH_SERVICE_SUFFIXES`. The big providers own those names, so a DNS
answer of a private address is not a real risk here, and the sender refuses
each redirect (``sender._session``).

An endpoint of another push service gets a 422 at subscribe, and the sender
skips a stored one. To add a browser, add its push service host here, with
the browser that uses it.

Fence: ``tests/unit/test_push_subscriptions.py``.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: The longest endpoint we keep. The column CHECK holds the same number.
MAX_ENDPOINT_CHARS = 2048

#: Exact hosts. Chrome, Edge on Android, Samsung Internet and every other
#: Chromium browser use Firebase Cloud Messaging.
PUSH_SERVICE_HOSTS: frozenset[str] = frozenset({
    "fcm.googleapis.com",
})

#: Subdomains of these. Firefox uses Mozilla autopush
#: (``updates.push.services.mozilla.com``). Safari on macOS, and a web app on
#: the Home Screen of iOS 16.4 or later, use ``web.push.apple.com``. Edge on
#: Windows uses WNS (``wns2-<region>.notify.windows.com``).
PUSH_SERVICE_SUFFIXES: tuple[str, ...] = (
    ".push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)


def endpoint_host(url: str) -> str:
    """The host of ``url`` in lower case, or an empty string. A log line names
    this host only, never the path: the path is the device's address."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def endpoint_refusal(url: object) -> str | None:
    """Why ``url`` is not a push service endpoint we send to, or None."""
    if not isinstance(url, str) or not url:
        return "no endpoint"
    if len(url) > MAX_ENDPOINT_CHARS:
        return "endpoint too long"
    if any(c in url for c in "\\ \t\r\n") or any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return "bad endpoint"
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "bad endpoint"
    if parts.scheme != "https":
        return "endpoint is not https"
    if parts.username is not None or parts.password is not None:
        return "bad endpoint"
    if port not in (None, 443):
        return "endpoint port"
    host = (parts.hostname or "").lower()
    if not host:
        return "bad endpoint"
    if host in PUSH_SERVICE_HOSTS or host.endswith(PUSH_SERVICE_SUFFIXES):
        return None
    return "not a known push service"
