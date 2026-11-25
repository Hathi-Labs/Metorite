"""The link of the running bridge: the setup page pairs it, and ``serve``
runs it (TLY-2b, ``integrations_console.md`` §3.8.13).

One controller lives in the bridge process. It owns three things:

- **The pairing.** ``pair`` starts the device grant in the background and
  returns as soon as the user code is known. The page shows the code, and
  then the company name that the approval returned.
- **The confirmation (rule j).** ``confirm`` needs the exact company name.
  Only then does the long poll start. ``unlink`` revokes the credential at
  Metorite, deletes it here and stops the poll at once.
- **The long poll.** ``run`` waits until a confirmed link exists, runs
  ``runner.run_link``, and waits again when the link ends.

``state`` is what the page may show. It never holds the credential or the
device code.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from tally_bridge.transport.link import protocol as p
from tally_bridge.transport.link.api import LinkApi
from tally_bridge.transport.link.pairing import pair
from tally_bridge.transport.link.runner import REVOKED, run_link
from tally_bridge.transport.link.store import LinkState, LinkStore

log = logging.getLogger("tally_bridge.link")

OFF = "off"  # LINK_URL is empty
NOT_LINKED = "not_linked"
WAITING_APPROVAL = "waiting_approval"
WAITING_CONFIRM = "waiting_confirm"
LINKED = "linked"
DISCONNECTED = "disconnected"  # the admin revoked it

#: The pairing start answers within this time, or the page says so.
CODE_WAIT_S = 15.0
#: While no confirmed link exists, the controller looks at the store this
#: often, so a ``confirm-link`` from the command line starts the poll too.
RECHECK_S = 30.0
UNREADABLE = "The link state of this computer cannot be read now. Try again in a minute."


Result = tuple[dict[str, Any], str | None]


class LinkController:
    def __init__(
        self,
        store: LinkStore,
        link_url: Callable[[], str],
        tally_up: Callable[[], Awaitable[bool]],
        version: str,
        machine: str,
        api_factory: Callable[[str], LinkApi] = LinkApi,
        copy_at: Callable[[], str | None] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._store = store
        self._link_url = link_url
        self._tally_up = tally_up
        self._version = version
        self._machine = machine
        self._api_factory = api_factory
        self._copy_at = copy_at
        self._sleep = sleep
        #: Set after the app is built: ``dispatch.McpDispatcher`` over its MCP app.
        self.dispatch: Callable[[p.LinkRequest], Awaitable[p.LinkReply]] | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._code: str | None = None
        self._error: str | None = None
        self._disconnected = False
        self._wake = asyncio.Event()
        self._loop_stop: asyncio.Event | None = None
        #: True from the first check of ``pair`` until its task exists, so a
        #: second request cannot start a second pairing in that gap.
        self._starting = False

    # --- What the page may show -------------------------------------------------

    def _load(self) -> LinkState | None:
        try:
            return self._store.load()
        except Exception:
            log.exception("could not read the link state")
            return None

    def state(self) -> dict[str, Any]:
        """The link state for the setup page. No credential, no device code."""
        if not self._link_url():
            return {"mode": OFF}
        error = self._error
        try:
            stored = self._store.load()
        except Exception:
            stored, error = None, UNREADABLE
        out: dict[str, Any] = {
            "mode": NOT_LINKED,
            "code": None,
            "company": None,
            "error": error,
        }
        if self._pair_task is not None and not self._pair_task.done():
            out.update(mode=WAITING_APPROVAL, code=self._code)
        elif stored is not None and not stored.confirmed:
            out.update(mode=WAITING_CONFIRM, company=stored.company_name)
        elif stored is not None:
            out.update(mode=LINKED, company=stored.company_name)
        elif self._disconnected:
            out.update(mode=DISCONNECTED)
        return out

    # --- The pairing, the confirmation and the unlink ---------------------------

    async def pair(self, domain: str) -> Result:
        """Start the pairing. Returns the state, and an error text or None."""
        url = self._link_url()
        if not url:
            return self.state(), "Link mode is off. Set LINK_URL first."
        if self._starting or (self._pair_task is not None and not self._pair_task.done()):
            return self.state(), "A pairing runs already. Wait for it, or cancel it."
        try:
            old = self._store.load()
        except Exception:  # an unreadable link may be a live one
            return self.state(), UNREADABLE
        if old is not None and old.confirmed:
            return self.state(), f"This machine is linked to {old.company_name}. Unlink first."
        # Busy from here until the task exists: the revoke below awaits.
        self._starting = True
        try:
            return await self._start_pairing(url, domain, old)
        finally:
            self._starting = False

    async def _start_pairing(self, url: str, domain: str, old: LinkState | None) -> Result:
        api = self._api_factory(url)
        if old is not None:  # an unconfirmed pairing: revoke it (§3.8.4 step 11)
            await self._revoke_quietly(api, old.credential)
            self._store.delete()
        self._code, self._error, self._disconnected = None, None, False
        shown = asyncio.Event()

        def show(code: str) -> None:
            self._code = code
            shown.set()

        async def work() -> None:
            try:
                paired = await pair(api, domain, self._machine, self._version, show, self._sleep)
                self._store.save(
                    LinkState(paired.credential, paired.connector_id, paired.company_name)
                )
            except p.LinkError as exc:
                self._error = str(exc)
            except Exception as exc:
                log.warning("the pairing failed (%s)", type(exc).__name__)
                self._error = "The pairing failed. Check the address of Metorite, then try again."
            finally:
                self._code = None
                shown.set()

        self._pair_task = asyncio.create_task(work())
        try:
            await asyncio.wait_for(shown.wait(), CODE_WAIT_S)
        except TimeoutError:
            self._error = "Metorite did not answer. Try again later."
            self._pair_task.cancel()
            await asyncio.gather(self._pair_task, return_exceptions=True)
        return self.state(), self._error

    def confirm(self, company: str) -> Result:
        """Rule j: the exact company name that the approval returned."""
        try:
            stored = self._store.load()
        except Exception:
            return self.state(), UNREADABLE
        if stored is None:
            return self.state(), "This machine has no pairing. Pair it first."
        if company.strip() != stored.company_name:
            return self.state(), (
                f"Type the company name that the pairing showed: {stored.company_name}"
            )
        if not stored.confirmed:
            self._store.save(stored.confirm())
        self._wake.set()
        return self.state(), None

    async def unlink(self) -> Result:
        """Revoke at Metorite, then delete here and stop the poll. The error
        text says when Metorite did not take the revoke."""
        # A store that cannot be read may hold a live link. Then nothing
        # changes, and the page says so: an unlink that did nothing must not
        # look done, and the poll must not start again by itself.
        try:
            self._store.load()
        except Exception:
            return self.state(), UNREADABLE
        if self._pair_task is not None and not self._pair_task.done():
            self._pair_task.cancel()
            # Wait for the cancel, so the state below is final (no code box).
            await asyncio.gather(self._pair_task, return_exceptions=True)
        try:
            stored = self._store.load()
        except Exception:
            return self.state(), UNREADABLE
        revoked = True
        if stored is not None:
            url = self._link_url()
            if url:
                revoked = await self._revoke_quietly(self._api_factory(url), stored.credential)
            self._store.delete()
        if self._loop_stop is not None:
            self._loop_stop.set()
        self._code, self._error, self._disconnected = None, None, False
        note = (
            None
            if revoked
            else ("Metorite did not take the revoke. An admin can disconnect the connector there.")
        )
        return self.state(), note

    async def _revoke_quietly(self, api: LinkApi, credential: str) -> bool:
        try:
            await api.revoke(credential)
            return True
        except Exception as exc:
            log.warning("the revoke did not reach Metorite (%s)", type(exc).__name__)
            return False

    # --- The long poll -------------------------------------------------------------

    def _ready(self) -> bool:
        stored = self._load()
        return (
            bool(self._link_url())
            and self.dispatch is not None
            and (stored is not None and stored.confirmed)
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Run the long poll while a confirmed link exists, until ``stop``."""
        while not stop.is_set():
            if not self._ready():
                self._wake.clear()
                waiters = {
                    asyncio.ensure_future(self._wake.wait()),
                    asyncio.ensure_future(stop.wait()),
                }
                _, pending = await asyncio.wait(
                    waiters, return_when=asyncio.FIRST_COMPLETED, timeout=RECHECK_S
                )
                for task in pending:
                    task.cancel()
                continue
            self._loop_stop = asyncio.Event()
            relay = asyncio.ensure_future(self._relay(stop, self._loop_stop))
            try:
                result = await run_link(
                    self._api_factory(self._link_url()),
                    self._store,
                    self.dispatch,  # type: ignore[arg-type]
                    self._tally_up,
                    self._version,
                    self._loop_stop,
                    self._sleep,
                    copy_at=self._copy_at,
                )
            except Exception:
                log.exception("the link loop failed")
                result = None
                await self._sleep(5)
            finally:
                relay.cancel()
                self._loop_stop = None
            if result == REVOKED:
                self._disconnected = True
                log.warning("Disconnected by your admin. Pair this machine again to reconnect.")

    @staticmethod
    async def _relay(outer: asyncio.Event, inner: asyncio.Event) -> None:
        await outer.wait()
        inner.set()
