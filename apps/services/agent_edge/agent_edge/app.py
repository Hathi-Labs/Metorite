"""The edge app: the public half of the authorization server (WS-55 EA-2a).

Spec ``project-docs/specs/external_agent_access.md`` §5.1, §5.5 and §15 EA-2a.

Routes, all under the issuer ``https://mcp.metorite.com``:

* ``GET /.well-known/oauth-authorization-server`` — RFC 8414.
* ``GET /.well-known/oauth-protected-resource`` — RFC 9728. The same
  document also answers at the path-suffixed address of the resource
  (``/.well-known/oauth-protected-resource/mcp``), which RFC 9728 §3.1 names.
* ``POST /oauth/token`` and ``POST /oauth/revoke`` — :mod:`agent_edge.token_endpoint`.

⚠️ **The flag.** While ``agent_access_enabled`` is false, every route answers
404, the same answer as a route that does not exist, and does nothing else.
An app-level dependency enforces it, so a route that EA-3 adds is covered too.

⚠️ **No token in a URL.** A request that carries ``access_token`` in its query
is refused on every route (MCP authorization, "token in a query string").

This unit is not deployed in EA-2a. EA-10 writes its unit file and its Caddy
block. Run it locally with ``uvicorn agent_edge.app:app``.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from acb_auth import agent_oauth
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_edge import metadata, token_endpoint

#: The discovery documents may be cached for a short time.
_METADATA_HEADERS = {"Cache-Control": "public, max-age=300"}


async def _require_enabled(request: Request) -> None:
    """404 while the flag is off, and refuse a token in the URL."""
    if not agent_oauth.enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if "access_token" in request.query_params:
        raise token_endpoint.OAuthError(
            "invalid_request", "a token in the URL is refused")


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
        dependencies=[Depends(_require_enabled)],
    )

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
    return app


app = create_app()
