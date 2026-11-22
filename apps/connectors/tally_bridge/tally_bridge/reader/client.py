"""The HTTP client for TallyPrime (brief sections 5.4 and 5.5).

**A queue of one.** TallyPrime handles one request at a time. A long export
blocks the next request, and it can freeze the Tally screen of the person at
the desk. So each client holds one lock, and every request waits its turn.
The bridge makes one client for the process. The deadline of a tool call
covers the wait in the queue too.

``is_up`` sends a GET to the Tally address. It reads "up" from any 200
answer. The exact body that TallyPrime sends is not proven yet. The README
lists it as an answer to record on the office PC.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Protocol

import httpx

from tally_bridge.reader.parse import TallyAnswerError, parse_answer


class TallyDown(Exception):
    """TallyPrime does not answer on its port."""


class TallyTimeout(Exception):
    """TallyPrime took longer than the timeout."""


class TallyReader(Protocol):
    async def is_up(self) -> bool:
        """True when TallyPrime answers on its HTTP port."""
        ...

    async def post(self, envelope: bytes) -> ET.Element:
        """Send one envelope from ``reader.envelope``, and read the answer."""
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
        self._turn = asyncio.Lock()

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
        async with self._turn:
            try:
                async with self._client() as client:
                    answer = await client.get(self._url)
            except httpx.HTTPError:
                return False
        return answer.status_code == 200

    async def post(self, envelope: bytes) -> ET.Element:
        async with self._turn:
            try:
                async with self._client() as client:
                    answer = await client.post(
                        self._url,
                        content=envelope,
                        headers={"Content-Type": "text/xml; charset=utf-8"},
                    )
            except httpx.TimeoutException as exc:
                raise TallyTimeout from exc
            except httpx.HTTPError as exc:
                raise TallyDown from exc
        if answer.status_code != 200:
            raise TallyAnswerError(f"TallyPrime answered HTTP {answer.status_code}.")
        return parse_answer(answer.content, answer.headers.get("content-type"))
