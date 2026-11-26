"""The pairing (§3.8.4): the device grant of RFC 8628.

1. Start with the domain that the customer typed. Metorite answers with a
   device code (private) and a user code (shown).
2. The customer gives the user code to their admin, who approves it.
3. Poll with the device code at the interval. ``slow_down`` adds 5 s.
4. The first poll after the approval returns the credential, the connector
   id and the company name. The program then waits for the customer to
   confirm that name (rule j): ``store.LinkState.confirmed`` is False.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from tally_bridge.transport.link import protocol as p
from tally_bridge.transport.link.api import LinkApi

Sleep = Callable[[float], Awaitable[None]]


async def pair(
    api: LinkApi,
    domain: str,
    machine: str,
    version: str,
    show_code: Callable[[str], None],
    sleep: Sleep,
    clock: Callable[[], float] = time.monotonic,
) -> p.Paired:
    """Pair this machine. ``show_code`` gets the user code as ``XXXX-XXXX``.
    The device code never leaves this function."""
    domain = domain.strip().lower()
    if not domain or "/" in domain or " " in domain:
        raise p.LinkError("Type the company domain only, such as example.com.")
    start = await api.pair_start(domain, machine, version)
    show_code(start.shown_code())
    interval = start.interval
    ends = clock() + start.expires_in
    while True:
        await sleep(interval)
        if clock() >= ends:
            raise p.PairExpired("The code ended after 10 minutes. Start the pairing again.")
        answer = await api.pair_poll(start.device_code)
        if isinstance(answer, p.Paired):
            return answer
        if answer == "slow_down":
            interval += p.SLOW_DOWN_STEP_S
        elif answer == "expired_token":
            raise p.PairExpired("The code ended after 10 minutes. Start the pairing again.")
        elif answer == "access_denied":
            raise p.PairDenied("The admin refused this machine.")
