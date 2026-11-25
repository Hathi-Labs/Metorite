"""One HTTP client for the five routes of link mode.

It follows no redirect: Metorite answers each route itself, and a 3xx is an
error (§3.8.5). It never logs the credential or the device code.
"""

from __future__ import annotations

from typing import Any

import httpx

from tally_bridge.transport.link import protocol as p

#: Metorite holds a poll up to 25 s (§3.8.5). The client waits a little longer.
POLL_TIMEOUT_S = 35.0
CALL_TIMEOUT_S = 15.0


class LinkApi:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            transport=self._transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"user-agent": "tally-bridge"},
        )

    async def _post(
        self,
        path: str,
        body: dict[str, Any] | None,
        *,
        credential: str | None = None,
        timeout: float = CALL_TIMEOUT_S,
    ) -> httpx.Response:
        headers = {"authorization": f"Bearer {credential}"} if credential else {}
        async with self._client(timeout) as client:
            answer = await client.post(path, json=body or {}, headers=headers)
        if 300 <= answer.status_code < 400:
            raise p.LinkError(f"Metorite answered {path} with a redirect.")
        if credential and answer.status_code == 401:
            raise p.Revoked("Disconnected by your admin.")
        return answer

    async def pair_start(self, domain: str, machine: str, version: str) -> p.PairStart:
        answer = await self._post(
            p.PAIR_START, {"domain": domain, "machine": machine, "version": version, "kind": p.KIND}
        )
        if answer.status_code != 200:
            raise p.LinkError(f"The pairing start answered HTTP {answer.status_code}.")
        return p.pair_start_from(_json(answer))

    async def pair_poll(self, device_code: str) -> p.Paired | str:
        """The pairing, or the RFC 8628 error code: ``authorization_pending``,
        ``slow_down``, ``expired_token`` or ``access_denied``."""
        answer = await self._post(p.PAIR_POLL, {"device_code": device_code})
        if answer.status_code == 200:
            return p.paired_from(_json(answer))
        if answer.status_code == 400:
            error = _json(answer).get("error") if isinstance(_json(answer), dict) else None
            if error in ("authorization_pending", "slow_down", "expired_token", "access_denied"):
                return str(error)
        raise p.LinkError(f"The pairing poll answered HTTP {answer.status_code}.")

    async def poll(self, credential: str, tally_up: bool, version: str) -> list[p.LinkRequest]:
        answer = await self._post(
            p.LINK_POLL,
            {"tally": "up" if tally_up else "down", "version": version},
            credential=credential,
            timeout=POLL_TIMEOUT_S,
        )
        if answer.status_code != 200:
            raise p.LinkError(f"The link poll answered HTTP {answer.status_code}.")
        return p.requests_from(_json(answer))

    async def reply(self, credential: str, reply: p.LinkReply) -> None:
        answer = await self._post(
            p.LINK_REPLY,
            {"id": reply.id, "status": reply.status, "headers": reply.headers, "body": reply.body},
            credential=credential,
        )
        # 404: the request belongs to no connection of this credential, or it
        # ended. Metorite drops a late reply, so the program drops it too.
        if answer.status_code not in (200, 404):
            raise p.LinkError(f"The link reply answered HTTP {answer.status_code}.")

    async def revoke(self, credential: str) -> None:
        """RFC 7009: an unknown credential also gets 200."""
        try:
            await self._post(p.LINK_REVOKE, None, credential=credential)
        except p.Revoked:
            return


def _json(answer: httpx.Response) -> Any:
    try:
        return answer.json()
    except ValueError as exc:
        raise p.LinkError("Metorite answered with a body that is not JSON.") from exc
