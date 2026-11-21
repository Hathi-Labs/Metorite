"""The HTTP client for TallyPrime.

Rung 1, PR 1 has one call: ``is_up``. PR 2 adds the XML envelopes (brief
section 5.4), the queue of one and the parser.

``is_up`` sends a GET to the Tally address. It reads "up" from any 200
answer. The exact body that TallyPrime sends is not proven yet. The README
lists it as an answer to record on the office PC.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class TallyReader(Protocol):
    async def is_up(self) -> bool:
        """True when TallyPrime answers on its HTTP port."""
        ...


class TallyClient:
    def __init__(
        self,
        url: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = url
        self._timeout = httpx.Timeout(timeout_s)
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        # trust_env=False: a proxy setting of the machine must never carry a
        # call to 127.0.0.1 somewhere else.
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
        )

    async def is_up(self) -> bool:
        try:
            async with self._client() as client:
                answer = await client.get(self._url)
        except httpx.HTTPError:
            return False
        return answer.status_code == 200
