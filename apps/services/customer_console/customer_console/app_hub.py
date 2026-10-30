"""The Console's client of an app's hub (Aroh): the ONE outbound call to a box.

Spec: ``project-docs/specs/customer_console.md`` §6 "CP-APP", the block
"Connect Aroh" (owner request, 2026-10-30).

⚠️ **A recorded exception, and a narrow one.** The Console makes no request to
a Metorite tenant box: a box asks the Console, over the arrow that exists. An
APP hub is different in one way only. The operator connects it once, and the
Console must hand it the box's deployment key. So the Console calls the hub,
and ONLY from the operator doors under ``/apps/connection``, never on a
request path of a customer and never on a timer.

The hub's operator API, all under ``Authorization: Bearer <hub operator
token>``:

* ``PUT    /hub/api/router-key``  body ``{"key": "cc_depl_…"}`` → ``{ok, kind, last4}``
* ``GET    /hub/api/router-key``  → the status only, never the key
* ``DELETE /hub/api/router-key``
* ``GET    /hub/api/orgs``
* ``GET    /hub/api/seats?org=<org id>``
* ``POST   /hub/api/seats/{id}/suspend|resume|release``

🔴 **Neither secret reaches a log line or an answer.** The hub token and the
deployment key go in the outgoing request and nowhere else. What comes back to
a caller is the hub's status code and the named fields of its body, never the
body as a whole: :func:`public_fields` picks them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

__all__ = [
    "SEAT_ACTIONS",
    "HubAnswer",
    "HubUnreachable",
    "clean_base_url",
    "delete_router_key",
    "list_orgs",
    "list_seats",
    "public_fields",
    "put_router_key",
    "router_key_status",
    "seat_action",
    "set_transport",
]

_log = logging.getLogger("platform.app_hub")

ROUTER_KEY_PATH = "/hub/api/router-key"
ORGS_PATH = "/hub/api/orgs"
SEATS_PATH = "/hub/api/seats"

#: The seat acts the hub takes. Any other word is refused before a request.
SEAT_ACTIONS: frozenset[str] = frozenset({"suspend", "resume", "release"})

#: One ceiling for each call. The operator waits for the answer on the page.
_TIMEOUT_SECONDS = 10.0

#: The hosts that may use plain http, for a hub on the same machine.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: The fields of a hub answer the Console may show. A field outside this set
#: is dropped, so a hub that echoed a secret back could not leak it here.
_PUBLIC_FIELDS = ("ok", "kind", "last4", "configured", "error", "status")

#: Tests set a mock transport here (``httpx.MockTransport``). Production leaves
#: it ``None``, and httpx uses the network.
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Replace the network for tests. ``None`` puts the network back."""
    global _transport
    _transport = transport


class HubUnreachable(Exception):
    """The hub gave no answer: DNS, connect, TLS or timeout."""


@dataclass(frozen=True)
class HubAnswer:
    """The hub's status code and its JSON body (``None`` when not JSON)."""

    status: int
    body: Any

    @property
    def ok(self) -> bool:
        """2xx, and the body does not say ``ok: false``."""
        if not 200 <= self.status < 300:
            return False
        return not (isinstance(self.body, dict) and self.body.get("ok") is False)


def clean_base_url(raw: str) -> str:
    """A hub base URL with no trailing slash, or ``ValueError``.

    ``https`` only, except a hub on this machine. A path, a query or a user
    name in the URL is refused: the Console adds the path itself.
    """
    url = (raw or "").strip().rstrip("/")
    parts = urlsplit(url)
    if not parts.hostname:
        raise ValueError("the base URL needs a host, for example https://aroh.dev.metorite.com")
    if parts.scheme != "https" and not (
        parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS
    ):
        raise ValueError("the base URL must use https")
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("the base URL is the host only, with no path")
    return url


def public_fields(answer: HubAnswer) -> dict[str, Any]:
    """The hub's status and the named fields of its body, nothing else."""
    out: dict[str, Any] = {"hub_status": answer.status}
    if isinstance(answer.body, dict):
        for name in _PUBLIC_FIELDS:
            if name in answer.body:
                out[name] = answer.body[name]
    return out


def _call(
    method: str,
    base_url: str,
    path: str,
    token: str,
    *,
    json: Any = None,
    params: dict[str, str] | None = None,
) -> HubAnswer:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        with httpx.Client(
            transport=_transport, timeout=_TIMEOUT_SECONDS, follow_redirects=False
        ) as client:
            res = client.request(
                method, f"{base_url}{path}", headers=headers, json=json, params=params
            )
    except httpx.HTTPError as exc:
        # The class name only. A message may carry the URL, and an exception
        # of a request object may carry its headers.
        _log.warning(
            "app_hub.unreachable",
            extra={"hub_path": path, "hub_error": type(exc).__name__},
        )
        raise HubUnreachable(type(exc).__name__) from None
    try:
        body = res.json()
    except ValueError:
        body = None
    _log.info("app_hub.answer", extra={"hub_path": path, "hub_status": res.status_code})
    return HubAnswer(status=res.status_code, body=body)


def put_router_key(base_url: str, token: str, key: str) -> HubAnswer:
    """Hand the hub its router key (the box's ``cc_depl_`` key)."""
    return _call("PUT", base_url, ROUTER_KEY_PATH, token, json={"key": key})


def router_key_status(base_url: str, token: str) -> HubAnswer:
    """Whether the hub holds a router key. The hub never sends the key back."""
    return _call("GET", base_url, ROUTER_KEY_PATH, token)


def delete_router_key(base_url: str, token: str) -> HubAnswer:
    """Tell the hub to drop its router key."""
    return _call("DELETE", base_url, ROUTER_KEY_PATH, token)


def list_orgs(base_url: str, token: str) -> HubAnswer:
    """The organizations the hub serves."""
    return _call("GET", base_url, ORGS_PATH, token)


def list_seats(base_url: str, token: str, org: str) -> HubAnswer:
    """The hub's seats for one organization."""
    return _call("GET", base_url, SEATS_PATH, token, params={"org": org})


def seat_action(base_url: str, token: str, seat_id: str, action: str) -> HubAnswer:
    """Suspend, resume or release one seat on the hub."""
    if action not in SEAT_ACTIONS:
        raise ValueError(f"unknown seat action {action!r}")
    if not seat_id or "/" in seat_id:
        raise ValueError("a seat id is one path segment")
    return _call("POST", base_url, f"{SEATS_PATH}/{seat_id}/{action}", token)
