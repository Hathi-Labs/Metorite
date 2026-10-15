"""The edge's only client of the gateway (WS-55 EA-3b-1, spec §10.1).

Spec ``project-docs/specs/external_agent_access.md`` §5.1, §9.5, §9.6 and
§10.1.

**What a request carries.** The four headers of
``acb_auth.agent_signing.signed_headers`` and nothing else that this module
sets. A write (EA-6a) also carries the fifth signed header,
``X-Agent-Nonce``, a ``Content-Type`` of JSON, and ``If-Match`` when the
tool sends a version. No ``Authorization`` header, no member header, and no
header copied from the client request. The gateway reads the member from the
connection row (branch 0 of ``get_current_user``). ``trust_env`` is off, so no
proxy setting and no ``.netrc`` entry can add a credential either.

**What a request may reach.** A ``GET`` whose route template is in
``agent_oauth.MCP_ROUTES``, or a write whose pair is in
``agent_oauth.MCP_WRITE_ROUTES``. The client checks the pair before it sends,
and branch 0 checks it again.

**Where it goes.** ``agent_edge_gateway_url``, the loopback address of the
gateway (default ``http://127.0.0.1:8080``). A test points that URL at the
gateway app through ``httpx.ASGITransport`` with :func:`use_transport`
(EA3-20).

**Time (EA3-17).** Each request has :data:`REQUEST_TIMEOUT_S`. The tool call
limit lives in ``tools``. One read is longer (EA-12b, spec §12.1): the long
poll of ``get_agent_run`` passes ``timeout_s``, its ``wait_s`` plus 10
seconds.

This module maps no answer. It returns the status and the parsed body, and
``agent_edge.tools`` maps each one to a tool result (§9.6, EA3-16).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
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

#: The method of every read.
_GET = "GET"

#: A path parameter is an id. Anything else is refused before the send.
_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

#: An ``If-Match`` value: the characters of an ISO-8601 timestamp, and no
#: other. The gateway parses it strictly. This only keeps a header value plain.
_VERSION = re.compile(r"[0-9][0-9:.+TZ-]{9,39}")

#: The transport of the next requests. ``None`` is the network. A test sets
#: the gateway app here (:func:`use_transport`).
_TRANSPORT: list[httpx.AsyncBaseTransport | None] = [None]


class GatewayUnreachable(Exception):
    """No answer came in time, or the connection failed."""


class GatewayNotSent(GatewayUnreachable):
    """The connection failed before any byte of the request went out
    (``httpx.ConnectError`` or ``httpx.ConnectTimeout``). So the gateway
    cannot have acted on it (EA-6d, the idempotency slot of ``create_task``)."""


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


def _path(template: str, path_params: Mapping[str, str], method: str = _GET) -> str:
    if (method, template) not in agent_oauth.MCP_ROUTES:
        raise RouteRefused(f"{method} {template} is not in MCP_ROUTES")
    if method != _GET and (method, template) not in agent_oauth.MCP_WRITE_ROUTES:
        raise RouteRefused(f"{method} {template} is not in MCP_WRITE_ROUTES")
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
    *, method: str = _GET, body: bytes = b"",
) -> dict[str, str]:
    """The signed headers of one gateway request: four for a read, and five
    for a write, whose fifth is a fresh ``X-Agent-Nonce`` (EA3-28)."""
    return agent_signing.signed_headers(
        key,
        organization_id=caller.organization_id,
        connection_id=caller.connection_id,
        actor_via=agent_oauth.actor_via_for(caller.client_origin),
        method=method,
        path=path,
        query=list(query),
        body=body,
    )


async def get(
    key: str,
    caller: TokenLookup,
    template: str,
    *,
    path_params: Mapping[str, str] | None = None,
    query: Sequence[tuple[str, str]] = (),
    timeout_s: float | None = None,
) -> Answer:
    """One signed ``GET`` to the gateway, or raise :class:`GatewayUnreachable`.

    *key* is ``agent_signing.signing_key()``, which the transport checked.
    *query* holds each key once. The signed string sorts the pairs, and the
    gateway refuses a repeated key. *timeout_s* is the limit of this request,
    and ``None`` is :data:`REQUEST_TIMEOUT_S`. Only the long poll of
    ``get_agent_run`` sets it (EA-12b).
    """
    path = _path(template, path_params or {})
    pairs = [(str(k), str(v)) for k, v in query]
    try:
        headers = signed_request_headers(key, caller, path, pairs)
        url = _url(path, pairs)
    except Exception as exc:  # nothing here may become a 500
        # A value the signed string or the URL cannot carry, such as a lone
        # surrogate. The tool input check refuses the known cases first. The
        # log names the error type only, never an argument value (fix round 1).
        _log.warning("agent_edge.gateway_request_unbuildable", route=template,
                     error=type(exc).__name__)
        raise GatewayUnreachable(template) from None
    return await _send(_GET, url, headers, b"", template, timeout_s)


async def write(
    key: str,
    caller: TokenLookup,
    method: str,
    template: str,
    *,
    json_body: Mapping[str, Any],
    path_params: Mapping[str, str] | None = None,
    query: Sequence[tuple[str, str]] = (),
    if_match: str | None = None,
) -> Answer:
    """One signed write to the gateway (EA-6a), or raise
    :class:`GatewayUnreachable`.

    The pair must be in ``MCP_WRITE_ROUTES``. The body is the compact JSON
    of *json_body*, and the signature covers its bytes and a fresh nonce
    (EA3-28). *if_match* goes out as ``If-Match`` when it is set, and only
    in the shape of a timestamp. Nothing else of the client request goes out.
    """
    path = _path(template, path_params or {}, method)
    pairs = [(str(k), str(v)) for k, v in query]
    if if_match is not None and not _VERSION.fullmatch(if_match):
        raise RouteRefused("the version is not a timestamp")
    try:
        body = json.dumps(dict(json_body), ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        headers = signed_request_headers(key, caller, path, pairs,
                                         method=method, body=body)
        headers["Content-Type"] = "application/json"
        if if_match is not None:
            headers["If-Match"] = if_match
        url = _url(path, pairs)
    except Exception as exc:  # nothing here may become a 500
        _log.warning("agent_edge.gateway_request_unbuildable", route=template,
                     error=type(exc).__name__)
        raise GatewayUnreachable(template) from None
    return await _send(method, url, headers, body, template)


def _url(path: str, pairs: Sequence[tuple[str, str]]) -> str:
    base = str(get_settings().agent_edge_gateway_url).rstrip("/")
    return base + path + (f"?{urlencode(pairs)}" if pairs else "")


async def _send(method: str, url: str, headers: Mapping[str, str], body: bytes,
                template: str, timeout_s: float | None = None) -> Answer:
    # Read at call time, so a test that patches the module constant holds.
    limit = REQUEST_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        # Two limits, on purpose. httpx enforces its timeout on a socket only,
        # and an in-process transport has none, so the asyncio limit is the
        # one that always holds.
        async with asyncio.timeout(limit), httpx.AsyncClient(
            transport=_TRANSPORT[0],
            timeout=limit,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            if method == _GET:
                response = await client.get(url, headers=dict(headers))
            else:
                response = await client.request(method, url, headers=dict(headers),
                                                content=body)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # No connection, so no byte of the request went out.
        _log.warning("agent_edge.gateway_unreachable", route=template,
                     error=type(exc).__name__)
        raise GatewayNotSent(template) from None
    except Exception as exc:  # a timeout, a transport error, or any other
        _log.warning("agent_edge.gateway_unreachable", route=template,
                     error=type(exc).__name__)
        raise GatewayUnreachable(template) from None
    try:
        parsed = response.json() if response.content else None
    except ValueError:
        parsed = None
    return Answer(status=response.status_code, body=parsed)


__all__ = [
    "REQUEST_TIMEOUT_S",
    "Answer",
    "GatewayNotSent",
    "GatewayUnreachable",
    "RouteRefused",
    "get",
    "signed_request_headers",
    "use_transport",
    "write",
]
