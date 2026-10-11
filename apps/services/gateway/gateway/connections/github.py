"""GitHub as the connection of one organization (WS-54 IN-7).

Spec: ``project-docs/specs/integrations_console.md`` §2.2 (IN-D10, IN-D18),
§3.2, §3.3 and §7.2 (IN-7).

* **One row per org.** The device flow saves an org connection in
  the connection registry through :mod:`gateway.connections.store`. The
  provider is ``github`` and the owning app is ``integrations``. The login and
  the token scopes are non-secret, so they sit in ``provider_meta`` and
  ``scopes``. The access token sits in the encrypted blob.
* **One reader.** :func:`token_for_org` is the only way that a runtime reader
  gets the GitHub token of a customer. It opens a ``tenant_session`` for the
  organization that the caller names. It never reads ``GITHUB_TOKEN``, the
  settings or the ``gh`` CLI. With no connection it raises
  :class:`GitHubNotConnected`, and the caller does nothing for the customer.
* **The old row.** Before IN-7 the device flow stored ``github:token`` in
  ``provider_keys``. :func:`migrate_legacy_token` moves that row into the
  connection on the next read, then deletes the old row (expand/contract, R6).
  This fallback read stays for one release. After that, a follow-up deletes it.
* **No secret out.** No function here logs a token or puts one in an error.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger

from gateway.connections import store
from gateway.crm_sources.base import SourceCredential
from gateway.db import tenant_session

_log = get_logger("connections.github")

PROVIDER = "github"
OWNING_APP = "integrations"

#: The ``provider_keys`` row that the device flow wrote before IN-7.
LEGACY_PROVIDER_KEY = "github:token"

#: What a tool says when the organization has no live GitHub connection.
NOT_CONNECTED = "GitHub is not connected for your organization."

#: A device-flow token does not expire, so the blob holds a far date.
_NO_EXPIRY = datetime(9999, 12, 31, tzinfo=UTC)


class GitHubNotConnected(Exception):
    """The organization has no live GitHub connection."""

    def __init__(self) -> None:
        super().__init__(NOT_CONNECTED)


def parse_scopes(raw: str | Sequence[str] | None) -> tuple[str, ...]:
    """Split a ``scope`` field or an ``X-OAuth-Scopes`` header into scope names."""
    if raw is None:
        return ()
    parts = raw.replace(" ", ",").split(",") if isinstance(raw, str) else list(raw)
    return tuple(p.strip() for p in parts if p and p.strip())


def _credential(
    access_token: str, login: str, scopes: Sequence[str],
    refresh_token: str = "", expires_in: int | None = None,
) -> SourceCredential:
    expires_at = (
        datetime.now(UTC) + timedelta(seconds=int(expires_in))
        if expires_in else _NO_EXPIRY
    )
    return SourceCredential(
        access_token=access_token,
        refresh_token=refresh_token or "",
        expires_at=expires_at,
        scopes=tuple(scopes),
        provider_meta={"login": login or ""},
    )


async def save_device_token(
    db: Any,
    org: str,
    *,
    access_token: str,
    login: str,
    scopes: Sequence[str],
    connected_by: str,
    refresh_token: str = "",
    expires_in: int | None = None,
) -> store.Connection:
    """Save the org connection that the device flow produced (or update it)."""
    return await store.save_credential(
        db, org, PROVIDER, OWNING_APP,
        _credential(access_token, login, scopes, refresh_token, expires_in),
        connected_by=connected_by, scopes=scopes,
    )


async def migrate_legacy_token(db: Any, org: str, *, connected_by: str = "") -> bool:
    """Move the old ``provider_keys`` ``github:token`` row of *org* into the store.

    Returns ``True`` when it moved a token. The old row is read through the
    per-org key store and deleted after the connection is saved. A save that
    fails leaves the old row, so the next read tries again. A delete that
    fails leaves a row that the next read removes, because the connection
    already exists and the caller does not reach this function.
    """
    keys = store.legacy_keys()
    legacy = await keys.get(LEGACY_PROVIDER_KEY, organization_id=org)
    if not legacy:
        return False
    await save_device_token(
        db, org, access_token=legacy, login="", scopes=(),
        connected_by=connected_by or "migrated-from-provider-keys",
    )
    try:
        await keys.delete(LEGACY_PROVIDER_KEY, organization_id=org)
    except Exception as exc:  # the connection is saved already
        _log.warning("github.legacy_delete_failed (%s)", type(exc).__name__)
    _log.info("github.legacy_token_migrated")
    return True


async def _live_credential(db: Any, org: str) -> SourceCredential | None:
    credential = await store.load_credential(db, org, PROVIDER, OWNING_APP)
    if credential is not None:
        return credential
    # Only a missing connection falls back to the old row. A disconnected
    # connection must stay disconnected, so the old row is checked only when
    # the registry holds no row at all.
    if await store.get(db, org, PROVIDER, OWNING_APP) is not None:
        return None
    if await migrate_legacy_token(db, org):
        return await store.load_credential(db, org, PROVIDER, OWNING_APP)
    return None


async def token_or_none(org: str) -> str | None:
    """The GitHub token of *org*, or ``None``. It never reads the environment."""
    if not org:
        return None
    async with tenant_session(org) as db:
        credential = await _live_credential(db, org)
    return credential.access_token if credential and credential.access_token else None


async def token_for_org(org: str) -> str:
    """The GitHub token of *org*, or raise :class:`GitHubNotConnected`."""
    token = await token_or_none(org)
    if not token:
        raise GitHubNotConnected
    return token


async def account_summary(org: str) -> dict[str, Any]:
    """The non-secret facts of the connection of *org*. No token is in it."""
    async with tenant_session(org) as db:
        await _live_credential(db, org)  # moves an old row, so the card is true
        row = await store.get(db, org, PROVIDER, OWNING_APP)
    if row is None or row.status == store.STATUS_DISCONNECTED:
        return {
            "connected": False, "status": store.STATUS_DISCONNECTED if row else "not_connected",
            "login": None, "scopes": [], "connected_by": None,
        }
    return {
        "connected": row.status == store.STATUS_CONNECTED,
        "status": row.status,
        "login": (row.provider_meta.get("login") or None),
        "scopes": list(row.scopes),
        "connected_by": row.connected_by,
    }


async def disconnect(org: str) -> bool:
    """Delete the blob and set ``disconnected``. ``True`` when a token was removed.

    An old ``provider_keys`` row that was never migrated goes too, so a
    disconnect leaves no token of the organization anywhere.
    """
    removed_legacy = False
    keys = store.legacy_keys()
    if await keys.get(LEGACY_PROVIDER_KEY, organization_id=org):
        await keys.delete(LEGACY_PROVIDER_KEY, organization_id=org)
        removed_legacy = True
    async with tenant_session(org) as db:
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if row is None or row.status == store.STATUS_DISCONNECTED:
            return removed_legacy
        await store.disconnect(db, org, row.id)
        return True
