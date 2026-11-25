"""The long poll of link mode (§3.8.5, §3.8.6 and §3.8.14).

Each poll carries "Tally up" or "Tally down" and the version. Metorite holds
it up to 25 s, and answers with the waiting requests. The program runs each
request against its own MCP layer, and posts the reply by its id.

- **Rule j.** ``run_link`` serves nothing before the customer confirms the
  company name. It reads the store again before each poll, so an ``unlink``
  or a new pairing stops a running loop at once.
- **Requests run beside the poll.** At most ``MAX_IN_FLIGHT`` run at once,
  and the loop polls again at once. One slow report does not hold up the
  next connect of another member.
- **Revoke.** Only a 401 with ``{"error": "revoked"}`` ends the link. The
  loop deletes the stored state only when it still holds this credential.
- **Any other failure** (the network, a 5xx, a refusal, a bad answer, an
  error of this program) is logged, and the loop waits 1 s, then 2, 4 and so
  on up to 60 s. The first good poll resets the wait. A cut link comes back
  with no manual step.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from tally_bridge.transport.link import protocol as p
from tally_bridge.transport.link.api import LinkApi
from tally_bridge.transport.link.store import LinkState, LinkStore

log = logging.getLogger("tally_bridge.link")

Dispatch = Callable[[p.LinkRequest], Awaitable[p.LinkReply]]
Sleep = Callable[[float], Awaitable[None]]

BACKOFF_FIRST_S = 1.0
BACKOFF_MAX_S = 60.0
#: The most requests that run at once.
MAX_IN_FLIGHT = 8
#: Metorite holds each poll. When an empty answer comes back at once anyway,
#: the loop waits this long, so it never spins.
MIN_POLL_GAP_S = 1.0

REVOKED = "revoked"
STOPPED = "stopped"
UNLINKED = "unlinked"


class NotConfirmed(p.LinkError):
    """Rule j: the customer has not confirmed the company name."""


#: The most requests that wait or run. Past it, the loop does not poll until
#: one ends, so the backlog stays bounded.
MAX_BACKLOG = MAX_IN_FLIGHT * 2


def _current(store: LinkStore, credential: str) -> bool:
    """True when the store still holds this confirmed credential. A store that
    cannot be read answers False (it serves nothing)."""
    try:
        state = store.load()
    except Exception:
        return False
    return state is not None and state.confirmed and state.credential == credential


def _unlinked(store: LinkStore, credential: str) -> bool:
    """True when the store says that this link ended: no state, another
    credential, or no confirmation. A read error raises, so the loop backs
    off and tries again: a scanner that holds the file for a moment does not
    end the link."""
    state = store.load()
    return state is None or not state.confirmed or state.credential != credential


async def run_link(
    api: LinkApi,
    store: LinkStore,
    dispatch: Dispatch,
    tally_up: Callable[[], Awaitable[bool]],
    version: str,
    stop: asyncio.Event | None = None,
    sleep: Sleep = asyncio.sleep,
    min_gap_s: float = MIN_POLL_GAP_S,
) -> str:
    """Poll until ``stop`` is set (``"stopped"``), the customer unlinks or
    pairs again (``"unlinked"``), or Metorite revokes the credential
    (``"revoked"``)."""
    state: LinkState | None = store.load()
    if state is None or not state.confirmed:
        raise NotConfirmed("Confirm the company name first. The program serves nothing before.")
    credential = state.credential
    stop = stop or asyncio.Event()
    slots = asyncio.Semaphore(MAX_IN_FLIGHT)
    revoked = asyncio.Event()
    running: set[asyncio.Task[None]] = set()
    wait = BACKOFF_FIRST_S
    result = STOPPED
    try:
        while not stop.is_set():
            # Yield once each pass, so the loop never starves the event loop,
            # even when an answer comes back in the same step.
            await asyncio.sleep(0)
            if stop.is_set():
                break
            if revoked.is_set():
                log.warning("the link credential was revoked: disconnected by the admin")
                result = REVOKED
                break
            started = time.monotonic()
            try:
                if _unlinked(store, credential):
                    result = UNLINKED
                    break
                if len(running) >= MAX_BACKLOG:
                    await _until_stop(
                        asyncio.wait(set(running), return_when=asyncio.FIRST_COMPLETED), stop
                    )
                    continue
                up = await _tally_state(tally_up)
                requests = await _until_stop(api.poll(credential, up, version), stop)
                if requests is None:
                    break
                wait = BACKOFF_FIRST_S
                for request in requests:
                    expires = time.monotonic() + request.deadline_s
                    task = asyncio.create_task(
                        _serve_one(
                            api, credential, dispatch, request, expires, slots, store, revoked
                        )
                    )
                    running.add(task)
                    task.add_done_callback(running.discard)
                if not requests and time.monotonic() - started < min_gap_s:
                    await _until_stop(asyncio.sleep(min_gap_s), stop)
            except p.Revoked:
                _forget(store, credential)
                log.warning("the link credential was revoked: disconnected by the admin")
                result = REVOKED
                break
            except Exception as exc:
                log.warning(
                    "the link poll failed (%s: %s); next try in %.0f s",
                    type(exc).__name__,
                    exc,
                    wait,
                )
                await _until_stop(sleep(wait * random.uniform(0.8, 1.2)), stop)
                wait = min(wait * 2, BACKOFF_MAX_S)
    finally:
        for task in list(running):
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
    return result


def _forget(store: LinkStore, credential: str) -> None:
    """Delete the stored state only when it still holds this credential, so a
    revoke of an old link never deletes a new pairing."""
    try:
        state = store.load()
        if state is not None and state.credential == credential:
            store.delete()
    except Exception:
        log.exception("could not delete the revoked link state")


async def _until_stop(call: Awaitable[Any], stop: asyncio.Event) -> Any:
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
    api: LinkApi,
    credential: str,
    dispatch: Dispatch,
    request: p.LinkRequest,
    expires: float,
    slots: asyncio.Semaphore,
    store: LinkStore,
    revoked: asyncio.Event,
) -> None:
    try:
        async with slots:
            # The wait for a slot comes off the deadline. With no time left,
            # Metorite has failed the call already, so the bridge skips it.
            left = expires - time.monotonic()
            if left <= 0 or revoked.is_set() or not _current(store, credential):
                return
            reply = await dispatch(replace(request, deadline_s=left))
            await api.reply(credential, reply)
    except p.Revoked:
        _forget(store, credential)
        revoked.set()  # the loop ends with "revoked" at its next pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("a link request failed (%s)", type(exc).__name__)
