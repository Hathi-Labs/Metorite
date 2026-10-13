"""The bearer check of ``/mcp`` (WS-55 EA-3b-1, spec §7.1 steps 1 to 4).

Spec ``project-docs/specs/external_agent_access.md`` §7.1 and §9.6, check 6.

1. Read the bearer token of the request. No token, or a token that
   ``agent_tokens.lookup_token`` does not find live, is a 401.
2. **An outage is a 503 (EA3-10).** When the lookup raises and
   ``acb_common.db_busy.is_db_outage`` says the database could not be reached,
   the answer is HTTP 503 with ``Retry-After: 2``. It is never a 401, because
   a 401 makes an MCP client drop a good token and sign in again. Any other
   error of the lookup is a 401.
3. The token audience must equal ``agent_oauth.resource()``. The lookup does
   not check it, so the edge must.
4. The effective scopes are the token scope intersected with the connection
   scopes. ``projects:write`` covers ``projects:read`` on both sides.

The tenant comes from the token row and from nowhere else. This module reads
one request input, the ``Authorization`` header, and it never sends that
token on: the gateway gets the signed headers of §10.1 only.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from acb_auth import agent_oauth, agent_tokens
from acb_auth.agent_tokens import TokenLookup
from acb_common import get_logger
from acb_common.db_busy import is_db_outage

_log = get_logger("agent_edge.bearer")

#: The longest token the edge looks up. A real one is 43 characters.
MAX_TOKEN_CHARS = 512

#: The ``Retry-After`` of a database outage (EA3-10).
OUTAGE_RETRY_AFTER_S = 2


@dataclass(frozen=True)
class Caller:
    """A live token, its connection, and the scopes it may use."""

    lookup: TokenLookup
    scopes: frozenset[str]


class BearerRefused(Exception):
    """The request carries no usable token (401), or the store is down (503).

    ``sent`` is true when the request carried a token, so the 401 challenge
    adds ``error="invalid_token"``.
    """

    def __init__(self, status: int, *, sent: bool, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.sent = sent
        self.reason = reason


def token_of(header_value: str | None) -> str | None:
    """The token of an ``Authorization: Bearer <token>`` value, else ``None``.

    The scheme is case-insensitive (RFC 7235 §2.1). Another scheme, or a
    bearer with no token, counts as no token.
    """
    scheme, _, rest = (header_value or "").strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    return token or None


def _expand(scopes: Iterable[str]) -> set[str]:
    held = {s for s in scopes if s in agent_oauth.RESOURCE_SCOPES}
    if agent_oauth.PROJECTS_WRITE in held:
        held.add(agent_oauth.PROJECTS_READ)
    return held


def effective_scopes(token_scope: str | None, connection_scopes: Iterable[str]) -> frozenset[str]:
    """The token scope intersected with the connection scopes (§7.1 step 4).

    A broader scope covers a narrower one on each side before the
    intersection, so a ``projects:write`` token on a ``projects:read``
    connection may read. A malformed token scope gives no scope.
    """
    try:
        token = _expand(agent_oauth.parse_scope(token_scope))
    except ValueError:
        token = set()
    return frozenset(token & _expand(connection_scopes))


def covers(scopes: frozenset[str], required: str | None) -> bool:
    """True when *scopes* hold *required*. ``None`` means any live token."""
    return required is None or required in scopes


async def authenticate(header_value: str | None) -> Caller:
    """§7.1 steps 1 to 4, or raise :class:`BearerRefused`."""
    raw = token_of(header_value)
    if raw is None:
        raise BearerRefused(401, sent=False, reason="no_token")
    if len(raw) > MAX_TOKEN_CHARS:
        raise BearerRefused(401, sent=True, reason="token_too_long")
    try:
        found = await agent_tokens.lookup_token(raw, kind=agent_tokens.ACCESS)
    except Exception as exc:
        if is_db_outage(exc):
            _log.warning("agent_edge.token_lookup_outage", error=type(exc).__name__)
            raise BearerRefused(503, sent=True, reason="outage") from None
        _log.warning("agent_edge.token_lookup_failed", error=type(exc).__name__)
        raise BearerRefused(401, sent=True, reason="lookup_failed") from None
    if found is None:
        raise BearerRefused(401, sent=True, reason="not_live")
    if found.resource != agent_oauth.resource():
        _log.info("agent_edge.token_wrong_audience", connection_id=found.connection_id)
        raise BearerRefused(401, sent=True, reason="audience")
    return Caller(lookup=found, scopes=effective_scopes(found.scope, found.scopes))


__all__ = [
    "MAX_TOKEN_CHARS",
    "OUTAGE_RETRY_AFTER_S",
    "BearerRefused",
    "Caller",
    "authenticate",
    "covers",
    "effective_scopes",
    "token_of",
]
