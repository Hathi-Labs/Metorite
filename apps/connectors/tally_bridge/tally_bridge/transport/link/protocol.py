"""The wire format of link mode (§3.8.4 and §3.8.5).

The Metorite side (LNK-1 to LNK-3) does not exist yet. This module is the
program's side of the contract, and ``integrations_console.md`` §3.8.14 writes
it down for the LNK-2 audit to confirm. Every body is JSON.

| Route                         | Auth       | Body                                   |
|-------------------------------|------------|----------------------------------------|
| ``POST /connectors/pair/start`` | none     | domain, machine, version, kind         |
| ``POST /connectors/pair/poll``  | none     | device_code                            |
| ``POST /connectors/link/poll``  | credential | tally, version                       |
| ``POST /connectors/link/reply`` | credential | id, status, headers, body            |
| ``POST /connectors/link/revoke``| credential | none                                 |
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PAIR_START = "/connectors/pair/start"
PAIR_POLL = "/connectors/pair/poll"
LINK_POLL = "/connectors/link/poll"
LINK_REPLY = "/connectors/link/reply"
LINK_REVOKE = "/connectors/link/revoke"

KIND = "tally"

#: RFC 8628 §3.5: a poll too soon adds 5 seconds to the interval.
SLOW_DOWN_STEP_S = 5

#: Crockford base 32 has no I, L, O or U. A user code is 8 of these (§3.8.4).
_CROCKFORD = re.compile(r"^[0-9A-HJKMNP-TV-Z]{8}$")
#: The link credential: ``mlc_`` and 43 URL-safe characters (§3.8.4).
_CREDENTIAL = re.compile(r"^mlc_[A-Za-z0-9_-]{43}$")


class LinkError(Exception):
    """Metorite gave an answer that this program does not accept."""


class Revoked(LinkError):
    """Metorite refused the credential (401): Disconnect, delete or 90 days."""


class PairExpired(LinkError):
    """The pairing took more than its 10 minutes. Start again."""


class PairDenied(LinkError):
    """The admin refused the pairing."""


@dataclass(frozen=True)
class PairStart:
    device_code: str
    user_code: str
    expires_in: int
    interval: int

    def shown_code(self) -> str:
        """The user code as the customer sees it: ``XXXX-XXXX``."""
        return f"{self.user_code[:4]}-{self.user_code[4:]}"


@dataclass(frozen=True)
class Paired:
    credential: str
    connector_id: str
    company_name: str

    def __repr__(self) -> str:  # the credential never reaches a log
        return f"Paired(connector_id={self.connector_id!r}, company_name={self.company_name!r})"


@dataclass(frozen=True)
class LinkRequest:
    """One HTTP request of the MCP client of Metorite, for the MCP layer here."""

    id: str
    method: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    deadline_s: float = 30.0


@dataclass(frozen=True)
class LinkReply:
    id: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""


def pair_start_from(data: object) -> PairStart:
    if not isinstance(data, dict):
        raise LinkError("The pairing start is not a JSON object.")
    code = str(data.get("user_code", "")).replace("-", "").upper()
    device = data.get("device_code")
    if not isinstance(device, str) or len(device) < 32:
        raise LinkError("The pairing start has no device code.")
    if not _CROCKFORD.match(code):
        raise LinkError("The user code is not 8 characters of Crockford base 32.")
    try:
        expires_in = int(data.get("expires_in", 600))
        interval = int(data.get("interval", 5))
    except (TypeError, ValueError) as exc:
        raise LinkError("The pairing start has a bad expires_in or interval.") from exc
    return PairStart(device, code, max(1, min(expires_in, 600)), max(5, min(interval, 60)))


def paired_from(data: object) -> Paired:
    if not isinstance(data, dict):
        raise LinkError("The pairing answer is not a JSON object.")
    credential = data.get("credential")
    connector_id = data.get("connector_id")
    company = data.get("company_name")
    if not isinstance(credential, str) or not _CREDENTIAL.match(credential):
        raise LinkError("The pairing answer has no link credential.")
    if not isinstance(connector_id, str) or not connector_id:
        raise LinkError("The pairing answer has no connector id.")
    if not isinstance(company, str) or not company.strip():
        raise LinkError("The pairing answer has no company name.")
    return Paired(credential, connector_id, company.strip())


def requests_from(data: object) -> list[LinkRequest]:
    if not isinstance(data, dict) or not isinstance(data.get("requests", []), list):
        raise LinkError("The poll answer is not a JSON object with a list of requests.")
    out = []
    for item in data.get("requests", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise LinkError("A request of the poll answer has no id.")
        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise LinkError("A request of the poll answer has bad headers.")
        out.append(
            LinkRequest(
                id=item["id"],
                method=str(item.get("method", "")).upper(),
                headers={str(k).lower(): str(v) for k, v in headers.items()},
                body=str(item.get("body", "")),
                deadline_s=float(item.get("deadline_s", 30.0)),
            )
        )
    return out
