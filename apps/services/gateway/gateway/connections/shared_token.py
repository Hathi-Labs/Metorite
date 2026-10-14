"""One fresh access token for every worker (WS-53 CRM-Z2, ``crm_platform.md`` §5.2 item 11).

Zoho allows 10 token requests in 10 minutes. The single-flight lock of
``ZohoSource`` covers one instance only, so two workers could each refresh.
This helper keeps the token in the row instead:

1. It opens a tenant session and takes the row with ``FOR UPDATE``.
2. If the access token is still good, it returns the stored credential.
3. If not, it refreshes, and it writes the new token back to the row in the
   same transaction. A second worker waits on the row lock, then reads the
   fresh token and sends no request.

A dead refresh token (``NeedsReconnect``) and a refused client (``invalid_client``)
set ``needs_reconnect`` with the safe code in ``last_error``. After that, the
helper raises ``NeedsReconnect`` at once and sends nothing: **a dead token is
never retried**. A throttle or a transport failure changes nothing in the row.

The helper is provider-neutral. The caller passes the two provider functions.
The organization comes from the server. Row level security binds it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime

from acb_common import get_logger
from acb_common.db import tenant_session

from gateway.connections import store
from gateway.crm_sources.base import NeedsReconnect, SourceCredential, SourceError

_log = get_logger("connections.shared_token")

#: A refusal that a new consent repairs, or that stops the sync until an admin
#: looks. ``invalid_client`` is the platform client being wrong, but the sync
#: must stop all the same and never retry.
_STOP_CODES = frozenset({"invalid_code", "invalid_grant", "invalid_client"})


async def fresh_credential(
    org: str,
    *,
    provider: str,
    owning_app: str,
    refresh: Callable[[SourceCredential], Awaitable[SourceCredential]],
    needs_refresh: Callable[[SourceCredential, datetime], bool],
    now: Callable[[], datetime],
    accept: Callable[[store.Connection], bool] | None = None,
) -> SourceCredential | None:
    """The credential of the org connection, refreshed and stored when due.

    *accept* (WS-54 IN-8b) checks the row before its blob is read. A row
    that it refuses gives ``None`` and is never marked. The MCP runtime
    passes "still an OAuth row": a server deleted and registered again with
    headers during the wait must not be marked ``undecryptable``. The lock
    is by row id, so a delete and a new insert in the gap give ``None`` too.

    Returns ``None`` when the org has no live connection. Raises
    :class:`NeedsReconnect` when the connection needs an admin, and any other
    :class:`SourceError` from ``refresh`` for a failure that is not final.
    """
    stop: SourceError | None = None
    code = ""
    async with tenant_session(org) as db:
        row = await store.get(db, org, provider, owning_app)
        if row is None or row.status == store.STATUS_DISCONNECTED:
            return None
        if accept is not None and not accept(row):
            return None
        if row.status == store.STATUS_NEEDS_RESET:
            # CRM-X0: the stored token belongs to an account whose records the
            # CRM holds, and the admin chose another one. No run reads either.
            return None
        if row.status == store.STATUS_NEEDS_RECONNECT:
            raise NeedsReconnect(
                f"The {provider} connection needs an admin to connect again "
                f"({row.last_error or 'unknown'})"
            )
        try:
            credential = await store.load_credential_locked(db, org, row.id)
        except store.Undecryptable:
            # The master key changed or the blob is damaged. Only a new
            # consent repairs it (CRM-Z2 review P1).
            credential, code = None, "undecryptable"
        if credential is None and not code:
            return None
        if credential is not None:
            if not needs_refresh(credential, now()):
                return credential
            try:
                fresh = await refresh(credential)
            except SourceError as exc:
                if isinstance(exc, NeedsReconnect) or exc.code in _STOP_CODES:
                    code = exc.code or "invalid_code"
                    stop = exc
                else:
                    raise
            else:
                await store.update_tokens(db, org, row.id, fresh)
                return fresh
        # The mark is written inside the session and commits when it ends. An
        # exception here would roll it back, so it is raised after the block.
        await store.mark_needs_reconnect(db, org, row.id, code)
    _log.warning("%s connection needs reconnect (%s)", provider, store.safe_error_code(code))
    if isinstance(stop, NeedsReconnect):
        raise stop
    raise NeedsReconnect(f"The {provider} connection stopped ({store.safe_error_code(code)})")
