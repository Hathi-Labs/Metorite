"""The edge's only client of the gateway (WS-55 EA-3b-1, spec §10.1).

Spec ``project-docs/specs/external_agent_access.md`` §5.1, §9.5, §9.6 and
§10.1.

**What a request carries.** The four headers of
``acb_auth.agent_signing.signed_headers`` and nothing else that this module
sets. No ``Authorization`` header, no member header, and no header copied
from the client request. The gateway reads the member from the connection
row (branch 0 of ``get_current_user``). ``trust_env`` is off, so no proxy
setting and no ``.netrc`` entry can add a credential either.

**What a request may reach.** A ``GET`` whose route template is in
``agent_oauth.MCP_ROUTES``. The client checks the pair before it sends, and
branch 0 checks it again.

**Where it goes.** ``agent_edge_gateway_url``, the loopback address of the
gateway (default ``http://127.0.0.1:8080``). A test points that URL at the
gateway app through ``httpx.ASGITransport`` with :func:`use_transport`
(EA3-20).

**Time (EA3-17).** Each request has :data:`REQUEST_TIMEOUT_S`. The tool call
limit lives in ``tools``.

This module maps no answer. It returns the status and the parsed body, and
``agent_edge.tools`` maps each one to a tool result (§9.6, EA3-16).
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from acb_auth import agent_oauth, agent_signing
from acb_auth.agent_tokens import TokenLookup
from acb_common import get_logger, get_settings

_log = get_logger("agent_edge.gateway_client")

#: The limit of each gateway request, in seconds (EA3-17).
REQUEST_TIMEOUT_S = 10.0

#: The one method the edge sends in EA-3.
_GET = "GET"

#: A path parameter is an id. Anything else is refused before the send.
_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: The transport of the next requests. ``None`` is the network. A test sets
#: the gateway app here (:func:`use_transport`).
_TRANSPORT: list[httpx.AsyncBaseTransport | None] = [None]


class GatewayUnreachable(Exception):
    """No answer came in time, or the connection failed."""


class RouteRefused(ValueError):
    """The pair is not in ``MCP_ROUTES``, or a path parameter is not an id."""


@dataclass(frozen=True)
class Answer:
    """The gateway's status code and its parsed JSON body (``None`` if none)."""

    status: int
    body: Any


@contextlib.contextmanager
def use_transport(transport: httpx.AsyncBaseTransport | None) -> Iterator[None]:
    """Send the requests of this block through *transport*. For tests."""
    prior = _TRANSPORT[0]
    _TRANSPORT[0] = transport
    try:
        yield
    finally:
        _TRANSPORT[0] = prior


def _path(template: str, path_params: Mapping[str, str]) -> str:
    if (_GET, template) not in agent_oauth.MCP_ROUTES:
        raise RouteRefused(f"{template} is not in MCP_ROUTES")
    path = template
    for name, value in path_params.items():
        if not _ID.fullmatch(value):
            raise RouteRefused(f"{name} is not an id")
        path = path.replace("{" + name + "}", value)
    if "{" in path:
        raise RouteRefused("a path parameter is missing")
    return path


def signed_request_headers(
    key: str, caller: TokenLookup, path: str, query: Sequence[tuple[str, str]],
) -> dict[str, str]:
    """The headers of one gateway request: the four signed ones, and no other."""
    return agent_signing.signed_headers(
        key,
        organization_id=caller.organization_id,
        connection_id=caller.connection_id,
        actor_via=agent_oauth.actor_via_for(caller.client_origin),
        method=_GET,
        path=path,
        query=list(query),
    )


async def get(
    key: str,
    caller: TokenLookup,
    template: str,
    *,
    path_params: Mapping[str, str] | None = None,
    query: Sequence[tuple[str, str]] = (),
) -> Answer:
    """One signed ``GET`` to the gateway, or raise :class:`GatewayUnreachable`.

    *key* is ``agent_signing.signing_key()``, which the transport checked.
    *query* holds each key once. The signed string sorts the pairs, and the
    gateway refuses a repeated key.
    """
    path = _path(template, path_params or {})
    pairs = [(str(k), str(v)) for k, v in query]
    headers = signed_request_headers(key, caller, path, pairs)
    base = str(get_settings().agent_edge_gateway_url).rstrip("/")
    url = base + path + (f"?{urlencode(pairs)}" if pairs else "")
    try:
        # Two limits, on purpose. httpx enforces its timeout on a socket only,
        # and an in-process transport has none, so the asyncio limit is the
        # one that always holds.
        async with asyncio.timeout(REQUEST_TIMEOUT_S), httpx.AsyncClient(
            transport=_TRANSPORT[0],
            timeout=REQUEST_TIMEOUT_S,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(url, headers=headers)
    except (httpx.HTTPError, TimeoutError) as exc:
        _log.warning("agent_edge.gateway_unreachable", route=template,
                     error=type(exc).__name__)
        raise GatewayUnreachable(template) from None
    try:
        body = response.json() if response.content else None
    except ValueError:
        body = None
    return Answer(status=response.status_code, body=body)


__all__ = [
    "REQUEST_TIMEOUT_S",
    "Answer",
    "GatewayUnreachable",
    "RouteRefused",
    "get",
    "signed_request_headers",
    "use_transport",
]
