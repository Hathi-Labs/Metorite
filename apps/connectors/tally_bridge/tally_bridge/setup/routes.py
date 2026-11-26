"""The setup page and its API (brief section 6.7), in its address-mode form.

| Step | Route | What the bridge does |
|---|---|---|
| 1. Find Tally | ``GET /setup/api/state`` | Asks Tally if it is up, and which companies are open |
| 2. Choose companies | ``POST /setup/api/companies`` | Writes the allow-list (section 6.5 rule 4) |
| 3. Connect to Metorite | ``POST /setup/api/token`` | Makes a new read token, stores its hash, shows the token once |
| 4. Done | ``GET /setup/api/state`` | Tally up or down, the companies, the address, the last call |

Link mode (rung 2, TLY-2b) adds a step beside step 3, when LINK_URL is set:

| Step | Route | What the bridge does |
|---|---|---|
| Pair | ``POST /setup/api/link/pair`` | Starts the pairing with the typed domain, shows the user code |
| Confirm | ``POST /setup/api/link/confirm`` | Rule j: the exact company name that the approval returned |
| Unlink | ``POST /setup/api/link/unlink`` | Revokes the credential at Metorite and deletes it here, or cancels a pairing |
| Wait | ``GET /setup/api/link/state`` | Only the link block, which the page polls while it waits |

``state`` holds ``link``: the mode, the user code while it waits, and the
company name. It never holds the credential or the device code. The link
controller (``transport.link.controller``) comes in as a plain object, so
this module imports nothing of the transport.

The page never shows a stored secret again. ``state`` says only whether a
read token exists. A new token replaces the old one, so the Metorite admin
must enter the new value.

Every route passes ``SetupGuard`` first (``setup/guard.py``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import html
import inspect
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
#: TLY-2d: the page never sets the path of TallyPrime (see ``machine``).
ADMIN_PATH = "Ask an administrator to set the path of TallyPrime (TALLY_EXE in the settings file)."


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
    link: Any = None,
    on_machine_saved: Callable[[Settings], str | None] | None = None,
    machine_check: Callable[[Settings], str | None] | None = None,
) -> list[Route]:
    """``on_saved`` runs after a new allow-list is saved. Address mode passes
    the delete of the local copy of each company that left the list (TLY-4).
    ``link`` is the link controller of TLY-2b, or None. ``on_machine_saved``
    (TLY-2d) applies the two machine switches and returns an error text, or
    None. Only the entry point makes it, because ``setup`` must not import the
    Windows side."""
    api = _SetupApi(live, store, secrets, reader, on_saved, link, on_machine_saved, machine_check)
    return [
        Route("/setup", api.page, methods=["GET"]),
        Route("/setup/app.js", api.asset("setup.js", "text/javascript; charset=utf-8")),
        Route("/setup/app.css", api.asset("setup.css", "text/css; charset=utf-8")),
        Route("/setup/api/state", api.state, methods=["GET"]),
        Route("/setup/api/companies", api.companies, methods=["POST"]),
        Route("/setup/api/token", api.token, methods=["POST"]),
        Route("/setup/api/link/state", api.link_state, methods=["GET"]),
        Route("/setup/api/link/pair", api.link_pair, methods=["POST"]),
        Route("/setup/api/link/confirm", api.link_confirm, methods=["POST"]),
        Route("/setup/api/link/unlink", api.link_unlink, methods=["POST"]),
        Route("/setup/api/machine", api.machine, methods=["POST"]),
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
        link: Any = None,
        on_machine_saved: Callable[[Settings], str | None] | None = None,
        machine_check: Callable[[Settings], str | None] | None = None,
    ) -> None:
        #: Task 3974: why the sign-in task is off, or None. The page shows it
        #: whenever the start switch is on, so a refusal at install shows too.
        self._machine_check = machine_check
        self._on_machine_saved = on_machine_saved
        self._on_saved = on_saved
        self._link = link
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

    def _problem(self, s: Settings) -> str | None:
        if self._machine_check is None or not s.start_tally_at_signin:
            return None
        try:
            return self._machine_check(s)
        except Exception:
            return "The rights of the TallyPrime folder cannot be checked now."

    async def state(self, request: Request) -> Response:
        if (no := self._refused_api(request, post=False)) is not None:
            return no
        s = self._live.current
        opened = await self._open_companies()
        last = calllog.last_call_at()
        link = self._link.state() if self._link is not None else {"mode": "off"}
        machine = (
            {
                "start_tally_at_signin": s.start_tally_at_signin,
                "tally_exe": s.tally_exe,
                "keep_awake": s.keep_awake,
                # The DACL reads run off the event loop, as the save does.
                "problem": await asyncio.to_thread(self._problem, s),
            }
            if link.get("mode") != "off"
            else None
        )
        return _json(
            {
                "bridge_version": __version__,
                "tally": {"up": opened is not None, "companies": opened},
                "allowed": list(s.allowed_companies),
                "address": f"https://{s.public_host}/mcp" if s.public_host else None,
                "token_set": self._secrets.read_token_sha256() is not None,
                "last_call": last.isoformat(timespec="seconds") if last else None,
                "link": link,
                **({"machine": machine} if machine is not None else {}),
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

    # --- Link mode (TLY-2b) ----------------------------------------------------------

    async def _link_body(self, request: Request) -> tuple[dict[str, Any] | None, Response | None]:
        if (no := self._refused_api(request, post=True)) is not None:
            return None, no
        if self._link is None or self._link.state().get("mode") == "off":
            return None, _json({"error": "Link mode is off. Set LINK_URL first."}, 409)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return None, _json({"error": "Send a JSON object."}, 400)
        return body, None

    async def link_state(self, request: Request) -> Response:
        """Only the link block. The page polls it while it waits for the
        approval, so it does not rebuild the page or ask TallyPrime."""
        if (no := self._refused_api(request, post=False)) is not None:
            return no
        return _json({"link": self._link.state() if self._link is not None else {"mode": "off"}})

    async def link_pair(self, request: Request) -> Response:
        body, no = await self._link_body(request)
        if no is not None:
            return no
        domain = body.get("domain") if body else None
        if not isinstance(domain, str) or not 0 < len(domain) <= 253:
            return _json({"error": "Type the company domain, such as example.com."}, 400)
        state, error = await self._link.pair(domain)
        return _json({"error": error, "link": state}, 409) if error else _json({"link": state})

    async def link_confirm(self, request: Request) -> Response:
        body, no = await self._link_body(request)
        if no is not None:
            return no
        company = body.get("company") if body else None
        if not isinstance(company, str) or not 0 < len(company) <= MAX_NAME:
            return _json({"error": "Type the company name that the pairing showed."}, 400)
        state, error = self._link.confirm(company)
        return _json({"error": error, "link": state}, 409) if error else _json({"link": state})

    async def link_unlink(self, request: Request) -> Response:
        _, no = await self._link_body(request)
        if no is not None:
            return no
        state, error = await self._link.unlink()
        # The local delete happened in any case; ``note`` says whether
        # Metorite took the revoke.
        return _json({"link": state, "note": error})

    # --- The machine switches (TLY-2d) -----------------------------------------------

    async def machine(self, request: Request) -> Response:
        """Save the two switches of the setup guide, step 6: start TallyPrime at
        sign-in, and keep the computer awake. Dark with link mode.

        The path of TallyPrime is NOT set here. The sign-in task runs it for
        each person who signs in, and the person at this page may not be an
        administrator. So only an administrator sets TALLY_EXE, in the settings
        file (which users cannot write). The page shows it, read-only."""
        body, no = await self._link_body(request)
        if no is not None:
            return no
        start = body.get("start_tally_at_signin") if body else None
        awake = body.get("keep_awake") if body else None
        if not isinstance(start, bool) or not isinstance(awake, bool):
            return _json({"error": "Send the two switches."}, 400)
        saved = self._live.current
        if "tally_exe" in body and body["tally_exe"] != saved.tally_exe:
            return _json({"error": ADMIN_PATH}, 403)
        if start and not saved.tally_exe:
            return _json({"error": ADMIN_PATH}, 409)
        new = dataclasses.replace(saved, start_tally_at_signin=start, keep_awake=awake)
        try:
            new.validate()
            self._store.save(new)
        except SettingsError as exc:
            return _json({"error": str(exc)}, 400)
        self._live.replace(new)
        note = None
        if self._on_machine_saved is not None:
            note = self._on_machine_saved(new)
            if inspect.isawaitable(note):
                note = await note
        return _json(
            {
                "machine": {
                    "start_tally_at_signin": new.start_tally_at_signin,
                    "tally_exe": new.tally_exe,
                    "keep_awake": new.keep_awake,
                },
                "note": note,
            }
        )
