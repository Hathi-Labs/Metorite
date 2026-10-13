"""The edge app: the public half of the authorization server (WS-55 EA-2a).

Spec ``project-docs/specs/external_agent_access.md`` §5.1, §5.5 and §15 EA-2a.

Routes, all under the issuer ``https://mcp.metorite.com``:

* ``GET /.well-known/oauth-authorization-server`` — RFC 8414.
* ``GET /.well-known/oauth-protected-resource`` — RFC 9728. The same
  document also answers at the path-suffixed address of the resource
  (``/.well-known/oauth-protected-resource/mcp``), which RFC 9728 §3.1 names.
* ``POST /oauth/token`` and ``POST /oauth/revoke`` — :mod:`agent_edge.token_endpoint`.
* ``POST /mcp`` — the MCP endpoint (EA-3b-1), :mod:`agent_edge.mcp`.

⚠️ **The flag.** While ``agent_access_enabled`` is false, EVERY request
answers 404 and does nothing else: every path, every method, ``OPTIONS`` and
``HEAD`` included. :class:`EdgeGate` is an ASGI middleware that runs BEFORE
routing (fix round 1). A route dependency ran after routing, so a wrong method
answered 405 with an ``Allow`` header, and that told a stranger the feature
exists.

⚠️ **No token in a URL.** A request that carries ``access_token`` in its query
is refused on every path (MCP authorization, "token in a query string").
:class:`EdgeGate` checks that too.

This unit is not deployed in EA-2a. EA-10 writes its unit file and its Caddy
block. Run it locally with ``uvicorn agent_edge.app:app``.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlsplit

from acb_auth import agent_oauth
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent_edge import mcp, metadata, token_endpoint

#: The discovery documents may be cached for a short time.
_METADATA_HEADERS = {"Cache-Control": "public, max-age=300"}


class EdgeGate:
    """An ASGI middleware that answers before routing.

    Flag off: 404 ``{"detail": "Not Found"}`` for any HTTP request, and a
    close for any websocket. Flag on: 400 ``invalid_request`` for a query that
    names ``access_token``. Every other request goes on to the app.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not agent_oauth.enabled():
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await JSONResponse({"detail": "Not Found"}, status_code=404)(
                scope, receive, send)
            return
        query = scope.get("query_string", b"").decode("latin-1")
        if any(k == "access_token" for k, _ in parse_qsl(query, keep_blank_values=True)):
            refusal = token_endpoint.OAuthError(
                "invalid_request", "a token in the URL is refused")
            await token_endpoint.error_response(refusal)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _resource_metadata_paths() -> list[str]:
    base = "/.well-known/oauth-protected-resource"
    path = urlsplit(agent_oauth.resource()).path.rstrip("/")
    return [base, f"{base}{path}"] if path else [base]


def create_app() -> FastAPI:
    """Build the edge app. The routes read Settings on each request."""
    app = FastAPI(
        title="Metorite agent edge",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(EdgeGate)

    @app.exception_handler(token_endpoint.OAuthError)
    async def _oauth_error(_request: Request, exc: token_endpoint.OAuthError) -> JSONResponse:
        return token_endpoint.error_response(exc)

    @app.get("/.well-known/oauth-authorization-server")
    async def authorization_server() -> JSONResponse:
        return JSONResponse(
            metadata.authorization_server_metadata(), headers=_METADATA_HEADERS)

    async def protected_resource() -> JSONResponse:
        return JSONResponse(
            metadata.protected_resource_metadata(), headers=_METADATA_HEADERS)

    for path in _resource_metadata_paths():
        app.add_api_route(path, protected_resource, methods=["GET"])

    app.add_api_route("/oauth/token", token_endpoint.token, methods=["POST"])
    app.add_api_route("/oauth/revoke", token_endpoint.revoke, methods=["POST"])
    # EA-3b-1. The route takes every method, so the endpoint runs the checks
    # of spec §9.6 in their order (the key before the method) and the router
    # never answers a 405 first.
    app.add_api_route(mcp.path(), mcp.endpoint, methods=mcp.ROUTE_METHODS)
    return app


app = create_app()
