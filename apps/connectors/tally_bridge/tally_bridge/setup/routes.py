"""The setup page and its API (brief section 6.7), in its address-mode form.

| Step | Route | What the bridge does |
|---|---|---|
| 1. Find Tally | ``GET /setup/api/state`` | Asks Tally if it is up, and which companies are open |
| 2. Choose companies | ``POST /setup/api/companies`` | Writes the allow-list (section 6.5 rule 4) |
| 3. Connect to Metorite | ``POST /setup/api/token`` | Makes a new read token, stores its hash, shows the token once |
| 4. Done | ``GET /setup/api/state`` | Tally up or down, the companies, the address, the last call |

Link mode (rung 2) changes step 3 only. It waits for the spec of task 3653.

The page never shows a stored secret again. ``state`` says only whether a
read token exists. A new token replaces the old one, so the Metorite admin
must enter the new value.

Every route passes ``SetupGuard`` first (``setup/guard.py``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import html
from collections.abc import Callable
from importlib import resources
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tally_bridge import __version__, calllog
from tally_bridge.reader import (
    TallyAnswerError,
    TallyDown,
    TallyReader,
    TallyRefused,
    TallyTimeout,
    envelope,
    records,
)
from tally_bridge.secret_store import ReadOnlySecretStore, SecretStore, new_token, token_sha256
from tally_bridge.settings import LiveSettings, Settings, SettingsError, SettingsStore
from tally_bridge.setup.guard import KEY_HEADER, SECURITY_HEADERS, SetupGuard, refused

MAX_COMPANIES = 50
MAX_NAME = 200


def _asset(name: str) -> str:
    return (
        resources.files("tally_bridge.setup").joinpath("static", name).read_text(encoding="utf-8")
    )


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=SECURITY_HEADERS)


def build_setup_routes(
    live: LiveSettings,
    store: SettingsStore,
    secrets: SecretStore,
    reader: TallyReader,
    on_saved: Callable[[Settings], object] | None = None,
) -> list[Route]:
    """``on_saved`` runs after a new allow-list is saved. Address mode passes
    the delete of the local copy of each company that left the list (TLY-4)."""
    api = _SetupApi(live, store, secrets, reader, on_saved)
    return [
        Route("/setup", api.page, methods=["GET"]),
        Route("/setup/app.js", api.asset("setup.js", "text/javascript; charset=utf-8")),
        Route("/setup/app.css", api.asset("setup.css", "text/css; charset=utf-8")),
        Route("/setup/api/state", api.state, methods=["GET"]),
        Route("/setup/api/companies", api.companies, methods=["POST"]),
        Route("/setup/api/token", api.token, methods=["POST"]),
    ]


def _chosen(payload: Any) -> list[str] | None:
    """The company names of a companies request, or None when it is malformed."""
    chosen = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(chosen, list) or len(chosen) > MAX_COMPANIES:
        return None
    if not all(isinstance(c, str) and 0 < len(c) <= MAX_NAME for c in chosen):
        return None
    return list(dict.fromkeys(chosen))


class _SetupApi:
    def __init__(
        self,
        live: LiveSettings,
        store: SettingsStore,
        secrets: SecretStore,
        reader: TallyReader,
        on_saved: Callable[[Settings], object] | None = None,
    ) -> None:
        self._on_saved = on_saved
        self._live = live
        self._store = store
        self._secrets = secrets
        self._reader = reader
        self._guard = SetupGuard(live.current.bind_port, secrets)

    def _refused_api(self, request: Request, *, post: bool) -> Response | None:
        key = request.headers.get(KEY_HEADER, "")
        if (no := self._guard.check(request, key=key, write=post)) is not None:
            return no
        if post and not request.headers.get("content-type", "").startswith("application/json"):
            return refused(415)
        return None

    async def _open_companies(self) -> list[str] | None:
        """The companies open in TallyPrime now, or None when Tally does not answer."""
        s = self._live.current
        try:
            if not await asyncio.wait_for(self._reader.is_up(), s.tally_timeout_s):
                return None
            root = await asyncio.wait_for(
                self._reader.post(envelope.collection("BridgeCompanies", company=None)),
                s.call_deadline_s,
            )
        except (TallyDown, TallyTimeout, TallyRefused, TallyAnswerError, TimeoutError):
            return None
        return records.company_names(root)

    async def page(self, request: Request) -> Response:
        key = request.query_params.get("k", "")
        if (no := self._guard.check(request, key=key, write=False)) is not None:
            return no
        # The key goes into the asset links, so that each request carries it.
        # html.escape keeps it inside its attribute.
        body = _asset("setup.html").replace("__KEY__", html.escape(key, quote=True))
        return Response(body, media_type="text/html; charset=utf-8", headers=SECURITY_HEADERS)

    def asset(self, name: str, media: str):
        async def serve(request: Request) -> Response:
            key = request.query_params.get("k", "")
            if (no := self._guard.check(request, key=key, write=False)) is not None:
                return no
            return Response(_asset(name), media_type=media, headers=SECURITY_HEADERS)

        return serve

    async def state(self, request: Request) -> Response:
        if (no := self._refused_api(request, post=False)) is not None:
            return no
        s = self._live.current
        opened = await self._open_companies()
        last = calllog.last_call_at()
        return _json(
            {
                "bridge_version": __version__,
                "tally": {"up": opened is not None, "companies": opened},
                "allowed": list(s.allowed_companies),
                "address": f"https://{s.public_host}/mcp" if s.public_host else None,
                "token_set": self._secrets.read_token_sha256() is not None,
                "last_call": last.isoformat(timespec="seconds") if last else None,
            }
        )

    async def companies(self, request: Request) -> Response:
        if (no := self._refused_api(request, post=True)) is not None:
            return no
        try:
            chosen = _chosen(await request.json())
        except ValueError:
            chosen = None
        if chosen is None:
            return _json({"error": "Send a list of company names."}, 400)
        opened = await self._open_companies()
        if opened is None:
            return _json({"error": "TallyPrime is not running. Open it, then choose."}, 409)
        if any(c not in opened for c in chosen):
            return _json({"error": "Choose only companies that are open in TallyPrime."}, 400)
        new = dataclasses.replace(self._live.current, allowed_companies=tuple(chosen))
        try:
            self._store.save(new)
        except SettingsError as exc:
            return _json({"error": str(exc)}, 500)
        self._live.replace(new)
        if self._on_saved is not None:
            self._on_saved(new)
        return _json({"allowed": list(new.allowed_companies)})

    async def token(self, request: Request) -> Response:
        if (no := self._refused_api(request, post=True)) is not None:
            return no
        fresh = new_token()
        try:
            self._secrets.set_read_token_sha256(token_sha256(fresh))
        except ReadOnlySecretStore:
            return _json({"error": "This bridge cannot store a token. Use the product store."}, 500)
        # The one time that the token leaves the bridge. It is never logged.
        return _json({"token": fresh, "shown_once": True})
