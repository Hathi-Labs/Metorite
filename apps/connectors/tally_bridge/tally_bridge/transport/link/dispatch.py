"""Run one request of Metorite against the MCP layer of this process.

The request never leaves the process: an in-process ASGI call to the MCP app,
on ``/mcp`` only. The read token of address mode does not apply here, because
Metorite checked the link credential at its door (§3.8.5).

- Only ``POST``, ``GET`` and ``DELETE``, the methods of Streamable HTTP.
- Only the headers of the MCP protocol go in and out. Nothing else, such as a
  cookie or an authorization header, passes.
- A body larger than the cap, a redirect or an error of the layer becomes an
  error reply. The request id stays, so Metorite fails the call at once.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from starlette.types import ASGIApp

from tally_bridge.transport.link.protocol import LinkReply, LinkRequest

log = logging.getLogger("tally_bridge.link")

MCP_PATH = "/mcp"
METHODS = frozenset({"POST", "GET", "DELETE"})
REQUEST_HEADERS = frozenset(
    {"content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id"}
)
REPLY_HEADERS = frozenset({"content-type", "mcp-session-id", "mcp-protocol-version"})
#: The cap of one body in each direction.
MAX_BODY_BYTES = 1_000_000


class McpDispatcher:
    def __init__(self, mcp_app: ASGIApp, port: int) -> None:
        self._app = mcp_app
        # The MCP layer checks the Host header (DNS rebinding guard), and it
        # accepts the loopback name on the bridge's port.
        self._base = f"http://127.0.0.1:{port}"

    async def __call__(self, request: LinkRequest) -> LinkReply:
        if request.method not in METHODS:
            return _error(request.id, 405, "This method is not served.")
        if len(request.body.encode("utf-8")) > MAX_BODY_BYTES:
            return _error(request.id, 413, "The request is too large.")
        headers = {k: v for k, v in request.headers.items() if k in REQUEST_HEADERS}
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self._app),
                base_url=self._base,
                follow_redirects=False,
            ) as client:
                answer = await asyncio.wait_for(
                    client.request(
                        request.method,
                        MCP_PATH,
                        headers=headers,
                        content=request.body.encode("utf-8"),
                    ),
                    timeout=max(0.1, request.deadline_s),
                )
        except TimeoutError:
            return _error(request.id, 504, "The bridge did not answer in time.")
        except Exception:
            log.exception("a link request failed in the MCP layer")
            return _error(request.id, 502, "The bridge could not run the request.")
        if 300 <= answer.status_code < 400:
            return _error(request.id, 502, "The bridge refuses a redirect.")
        if len(answer.content) > MAX_BODY_BYTES:
            return _error(request.id, 502, "The answer is too large.")
        return LinkReply(
            id=request.id,
            status=answer.status_code,
            headers={k: v for k, v in answer.headers.items() if k.lower() in REPLY_HEADERS},
            body=answer.text,
        )


def _error(request_id: str, status: int, text: str) -> LinkReply:
    return LinkReply(
        id=request_id,
        status=status,
        headers={"content-type": "application/json"},
        body='{"error": "' + text + '"}',
    )
