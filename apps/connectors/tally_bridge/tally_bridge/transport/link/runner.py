"""The long poll of link mode (§3.8.5 and §3.8.6).

Each poll carries "Tally up" or "Tally down" and the version. Metorite holds
it up to 25 s, and answers with the waiting requests. The program runs each
one against its own MCP layer and posts the reply by its id.

- **Rule j.** ``run_link`` refuses to start before the customer confirms the
  company name.
- **Revoke.** A 401 (Disconnect, a delete, or 90 days with no poll) deletes
  the stored state, and the loop ends with "Disconnected by your admin".
- **A cut link** comes back with no manual step: the loop waits 1 s, then 2,
  4 and so on up to 60 s, and the first good poll resets the wait.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import httpx

from tally_bridge.transport.link import protocol as p
from tally_bridge.transport.link.api import LinkApi
from tally_bridge.transport.link.store import LinkStore

log = logging.getLogger("tally_bridge.link")

Dispatch = Callable[[p.LinkRequest], Awaitable[p.LinkReply]]
Sleep = Callable[[float], Awaitable[None]]

BACKOFF_FIRST_S = 1.0
BACKOFF_MAX_S = 60.0

REVOKED = "revoked"
STOPPED = "stopped"


class NotConfirmed(p.LinkError):
    """Rule j: the customer has not confirmed the company name."""


async def run_link(
    api: LinkApi,
    store: LinkStore,
    dispatch: Dispatch,
    tally_up: Callable[[], Awaitable[bool]],
    version: str,
    stop: asyncio.Event | None = None,
    sleep: Sleep = asyncio.sleep,
) -> str:
    """Poll until ``stop`` is set (``"stopped"``) or Metorite revokes the
    credential (``"revoked"``)."""
    state = store.load()
    if state is None or not state.confirmed:
        raise NotConfirmed("Confirm the company name first. The program serves nothing before.")
    stop = stop or asyncio.Event()
    wait = BACKOFF_FIRST_S
    while not stop.is_set():
        try:
            up = await _tally_state(tally_up)
            requests = await _until_stop(api.poll(state.credential, up, version), stop)
            if requests is None:
                break
            wait = BACKOFF_FIRST_S
            if requests:
                await asyncio.gather(
                    *(_serve_one(api, state.credential, dispatch, r) for r in requests)
                )
        except p.Revoked:
            store.delete()
            log.warning("the link credential was revoked: disconnected by the admin")
            return REVOKED
        except (httpx.HTTPError, p.LinkError) as exc:
            log.warning("the link poll failed (%s); next try in %.0f s", type(exc).__name__, wait)
            await sleep(wait * random.uniform(0.8, 1.2))
            wait = min(wait * 2, BACKOFF_MAX_S)
    return STOPPED


async def _until_stop(call: Awaitable[list[p.LinkRequest]], stop: asyncio.Event):
    """The answer of *call*, or None when ``stop`` comes first. A held poll
    waits up to 25 s, and a service stop must not wait for it."""
    work = asyncio.ensure_future(call)
    halt = asyncio.ensure_future(stop.wait())
    try:
        done, _ = await asyncio.wait({work, halt}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (work, halt):
            if not task.done():
                task.cancel()
    return work.result() if work in done else None


async def _tally_state(tally_up: Callable[[], Awaitable[bool]]) -> bool:
    try:
        return await asyncio.wait_for(tally_up(), 5.0)
    except Exception:
        return False


async def _serve_one(
    api: LinkApi, credential: str, dispatch: Dispatch, request: p.LinkRequest
) -> None:
    if request.deadline_s <= 0:
        return  # Metorite failed it already; a late reply is dropped there too
    reply = await dispatch(request)
    await api.reply(credential, reply)
