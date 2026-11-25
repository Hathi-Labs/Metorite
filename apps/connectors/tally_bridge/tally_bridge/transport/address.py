"""Address mode: Streamable HTTP at ``/mcp`` (brief sections 6.1 and 6.2).

| Path        | Token      | Answer                                        |
|-------------|------------|-----------------------------------------------|
| ``/mcp``    | read token | the read tools, stateless, JSON answers       |
| ``/health`` | none       | ``{"ok": true, "tally": "up"}`` or ``"down"`` |
| ``/setup``  | setup key  | the setup page, from 127.0.0.1 only (``setup/``) |
| any other   | -          | 404. ``/mcp-write`` is rung 3, so it is 404   |

Metorite refuses every 3xx answer (brief section 3). So the router never
adds or removes a trailing slash, and ``/mcp/`` is a 404, not a redirect.

The MCP layer also checks the Host header (the SDK's DNS rebinding guard).
It accepts the loopback names on ``bind_port`` and ``public_host``, the name
of the tunnel. A browser page sends an ``Origin``, and the guard refuses
every ``Origin``: Metorite calls from a server and sends none.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from tally_bridge.localcopy.store import CopyStore
from tally_bridge.reader import TallyReader
from tally_bridge.secret_store import SecretStore, token_matches
from tally_bridge.settings import LiveSettings, Settings, SettingsError, SettingsStore
from tally_bridge.setup import build_setup_routes
from tally_bridge.tools import build_read_server

log = logging.getLogger("tally_bridge.transport")

MCP_PATH = "/mcp"


def build_address_app(
    settings: Settings | LiveSettings,
    secrets: SecretStore,
    reader: TallyReader,
    store: SettingsStore | None = None,
    copy: CopyStore | None = None,
    link: object | None = None,
    on_machine_saved: Callable[[Settings], str | None] | None = None,
) -> ASGIApp:
    """The app of address mode: ``/mcp``, ``/health`` and the setup page.

    The tools and the setup page share one LiveSettings, so an allow-list that
    the setup page saves holds from the next tool call.
    """
    live = settings if isinstance(settings, LiveSettings) else LiveSettings(settings)
    settings = live.current
    mcp = build_read_server(reader, live, copy)  # type: ignore[arg-type]
    mcp.settings.streamable_http_path = MCP_PATH
    mcp.settings.stateless_http = True
    mcp.settings.json_response = True
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts(settings),
        allowed_origins=[],
    )
    app = mcp.streamable_http_app()
    app.router.redirect_slashes = False

    async def health(_: Request) -> JSONResponse:
        try:
            up = await asyncio.wait_for(reader.is_up(), live.current.tally_timeout_s)
        except TimeoutError:
            up = False
        return JSONResponse({"ok": True, "tally": "up" if up else "down"})

    app.router.routes.append(Route("/health", health, methods=["GET"]))
    app.router.routes.extend(
        build_setup_routes(
            live,
            store or _NoSaveStore(settings),
            secrets,
            reader,
            # TLY-4: a company that leaves the allow-list loses its copy at once.
            on_saved=(lambda new: copy.keep_only(new.allowed_companies)) if copy else None,
            # TLY-2b: the link controller, for the pair step of the page.
            link=link,
            # TLY-2d: the entry point applies the machine switches.
            on_machine_saved=on_machine_saved,
        )
    )
    guard = BearerGuard(app, secrets)
    # TLY-2: link mode runs a request of Metorite against this app in the same
    # process. Only ``/mcp`` (transport.link.dispatch.MCP_PATH).
    guard.inner = app
    return guard


class _NoSaveStore:
    """For an app built with no settings store: it loads and never saves."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def load(self) -> Settings:
        return self._settings

    def save(self, settings: Settings) -> None:
        raise SettingsError("This bridge has no settings store.")


def _allowed_hosts(settings: Settings) -> list[str]:
    port = settings.bind_port
    hosts = [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]
    if settings.public_host:
        # Funnel and Cloudflare serve on 443, so the Host header has no port.
        hosts += [settings.public_host, f"{settings.public_host}:443"]
    return hosts


class BearerGuard:
    """Refuses a call to ``/mcp`` without the read token (security rule 2).

    A pure ASGI wrapper, so the JSON answers of the MCP layer pass through
    untouched. It never logs the presented token.
    """

    def __init__(self, app: ASGIApp, secrets: SecretStore) -> None:
        self._app = app
        self._secrets = secrets
        self.inner: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and _guarded(scope["path"])
            and not token_matches(_bearer(scope), self._secrets.read_token_sha256())
        ):
            log.warning("refused a call to %s: no valid read token", scope["path"])
            await _unauthorized(send)
            return
        await self._app(scope, receive, send)


def _guarded(path: str) -> bool:
    return path == MCP_PATH or path.startswith(MCP_PATH + "/")


def _bearer(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                return token.strip()
            return ""
    return ""


async def _unauthorized(send: Send) -> None:
    body = json.dumps({"error": "unauthorized"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
