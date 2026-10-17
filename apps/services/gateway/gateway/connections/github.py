"""GitHub as the connection of one organization (WS-54 IN-7).

Spec: ``project-docs/specs/integrations_console.md`` §2.2 (IN-D10, IN-D18),
§3.2, §3.3 and §7.2 (IN-7).

* **One row per org.** A connect saves the org connection in
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

**Two modes, one reader (WS-54 GH-3, ``github_integration.md`` §6.3).** The
row gains ``provider_meta.auth``: ``"app"`` or ``"token"``. A row with no
``auth`` is a token row, so the rows of today keep working (GH-E1).

* An ``app`` row holds NO blob. The App's key is the operator's (D56). The row
  holds ``installation_id``, ``account_login``, ``account_type``,
  ``repository_selection``, ``app_slug`` and ``connected_by``, none of them a
  secret. :func:`token_or_none` answers an installation token from
  ``connections/github_app.py``, which keeps it in process memory only.
* A ``token`` row holds the pasted token in the blob, as before.

⚠️ An installation token is not a user token. ``GET /user`` refuses it, and it
reads only what the installation reaches. :func:`stored_token_or_none` gives
the token of a ``token`` row and nothing for an ``app`` row, for the one caller
that needs a user token's reach (``routes/agent.py``, a private agent repo).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_common import get_logger
from sqlalchemy import text

from gateway.connections import store
from gateway.crm_sources.base import SourceCredential
from gateway.db import tenant_session

_log = get_logger("connections.github")

PROVIDER = "github"
OWNING_APP = "integrations"

#: The ``provider_keys`` row that the device flow wrote before IN-7.
LEGACY_PROVIDER_KEY = "github:token"

#: ``provider_meta.auth`` of the two modes (§6.3). No ``auth`` reads as token.
AUTH_APP = "app"
AUTH_TOKEN = "token"

#: What a tool says when the organization has no live GitHub connection.
NOT_CONNECTED = "GitHub is not connected for your organization."

#: A device-flow token does not expire, so the blob holds a far date.
_NO_EXPIRY = datetime(9999, 12, 31, tzinfo=UTC)


#: One writer at a time for each org, until the commit. Each writer and the
#: move of an old row both take it (the pattern of ``routes/crm/connection.py``).
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"


async def lock(db: Any, org: str) -> None:
    """Take the advisory lock of the GitHub connection of *org* for this transaction."""
    await db.execute(text(_LOCK_SQL), {"key": f"integrations:github:{org}"})


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
        # WS-54 GH-7: a token row says so, beside an App row's `auth = "app"`.
        provider_meta={"login": login or "", "auth": AUTH_TOKEN},
    )


async def save_token(
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
    """Save a pasted token as the org connection, or update it (WS-54 GH-7).

    The one writer of a ``token`` row. ``PUT /integrations/github/token``
    calls it, and so does the move of an old ``provider_keys`` row. An App row
    becomes a token row: the upsert replaces ``provider_meta``, so the
    installation id goes and the unique index of migration 282 lets the
    installation go too. The caller drops the cached installation token.
    """
    await lock(db, org)
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
    # Under the lock, look again. A connect that finished a moment ago has
    # a newer token than the old row, and the old row must never replace it.
    await lock(db, org)
    if await store.get(db, org, PROVIDER, OWNING_APP) is not None:
        await _drop_legacy(keys, org)
        return False
    legacy = await keys.get(LEGACY_PROVIDER_KEY, organization_id=org)
    if not legacy:
        return False
    await save_token(
        db, org, access_token=legacy, login="", scopes=(),
        connected_by=connected_by or "migrated-from-provider-keys",
    )
    await _drop_legacy(keys, org)
    _log.info("github.legacy_token_migrated")
    return True


async def _drop_legacy(keys: Any, org: str) -> None:
    """Delete the old row. A failure is logged and the next read tries again."""
    try:
        await keys.delete(LEGACY_PROVIDER_KEY, organization_id=org)
    except Exception as exc:
        _log.warning("github.legacy_delete_failed (%s)", type(exc).__name__)


async def _live_credential(db: Any, org: str) -> SourceCredential | None:
    credential = await store.load_credential(db, org, PROVIDER, OWNING_APP)
    if credential is not None:
        return credential
    # Only a missing connection falls back to the old row. A disconnected
    # connection must stay disconnected, so the old row is checked only when
    # the registry holds no row at all.
    if await store.get(db, org, PROVIDER, OWNING_APP) is not None:
        return None
    # The move may find a connection that a connect saved a moment ago.
    # Read again either way, so that connection wins over the old row.
    await migrate_legacy_token(db, org)
    return await store.load_credential(db, org, PROVIDER, OWNING_APP)


def is_app_row(row: store.Connection | None) -> bool:
    """True for a row that the GitHub App connect wrote (``auth = "app"``)."""
    return row is not None and row.provider_meta.get("auth") == AUTH_APP


def installation_of(row: store.Connection | None) -> int | None:
    """The installation id of a live ``app`` row, or ``None``."""
    from gateway.connections import github_app

    if not is_app_row(row) or row is None or row.status != store.STATUS_CONNECTED:
        return None
    return github_app.installation_id_of(row.provider_meta.get("installation_id"))


async def connection(org: str) -> store.Connection | None:
    """The org's GitHub row, with no blob, or ``None``. It moves an old row."""
    if not org:
        return None
    async with tenant_session(org) as db:
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if row is None:
            await _live_credential(db, org)  # an old provider_keys row moves here
            row = await store.get(db, org, PROVIDER, OWNING_APP)
    return row


async def _installation_token_or_none(installation_id: int) -> str | None:
    """An installation token, or ``None`` on any failure. Never raises."""
    from gateway.connections import github_app

    try:
        return await github_app.installation_token(installation_id)
    except (github_app.GitHubAppNotConfigured, github_app.GitHubCallFailed) as exc:
        _log.warning("github.installation_token_unavailable", error=type(exc).__name__)
        return None


async def token_or_none(org: str) -> str | None:
    """The GitHub token of *org*, or ``None``. It never reads the environment.

    An ``app`` row answers an installation token (§6.3). The GitHub call runs
    after the session closes, so no connection waits on GitHub.
    """
    if not org:
        return None
    installation: int | None = None
    credential: SourceCredential | None = None
    async with tenant_session(org) as db:
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        if is_app_row(row):
            installation = installation_of(row)
            if installation is None:
                return None
        else:
            credential = await _live_credential(db, org)
    if installation is not None:
        return await _installation_token_or_none(installation)
    return credential.access_token if credential and credential.access_token else None


async def stored_token_or_none(org: str) -> str | None:
    """The token of a ``token`` row of *org*, or ``None``. An ``app`` row has
    none (§6.3): its installation token cannot read a private agent repo,
    because the App asks for no Contents permission."""
    if not org:
        return None
    async with tenant_session(org) as db:
        if is_app_row(await store.get(db, org, PROVIDER, OWNING_APP)):
            return None
        credential = await _live_credential(db, org)
    return credential.access_token if credential and credential.access_token else None


async def save_app_installation(
    db: Any,
    org: str,
    *,
    installation_id: int,
    account_login: str,
    account_type: str,
    repository_selection: str,
    app_slug: str,
    connected_by: str,
    permissions: dict[str, str] | None = None,
) -> store.Connection:
    """Write the org's ``app`` row (§7.4), under the lock of the org.

    A token row becomes an App row, and its token goes. The unique index of
    migration 282 refuses an installation that another org holds, with an
    ``IntegrityError`` the caller maps to ``other_org``.
    """
    await lock(db, org)
    meta: dict[str, Any] = {
        "auth": AUTH_APP,
        "installation_id": str(int(installation_id)),
        "account_login": account_login,
        "account_type": account_type,
        "repository_selection": repository_selection,
        "app_slug": app_slug,
        "connected_by": (connected_by or "").strip().lower(),
    }
    if permissions:
        meta["permissions"] = dict(permissions)
    return await store.save_no_secret(
        db, org, PROVIDER, OWNING_APP, provider_meta=meta, connected_by=connected_by,
    )


async def token_for_org(org: str) -> str:
    """The GitHub token of *org*, or raise :class:`GitHubNotConnected`."""
    token = await token_or_none(org)
    if not token:
        raise GitHubNotConnected
    return token


#: The org's own name, for the confirm dialog "Connect <account> to <org>?".
#: `organization` is RLS-exempt, and the read names the bound org.
_ORG_NAME_SQL = "SELECT display_name FROM organization WHERE id = CAST(:o AS uuid)"


async def account_summary(org: str) -> dict[str, Any]:
    """The non-secret facts of the connection of *org*. No token is in it."""
    async with tenant_session(org) as db:
        await _live_credential(db, org)  # moves an old row, so the card is true
        row = await store.get(db, org, PROVIDER, OWNING_APP)
        name = (await db.execute(text(_ORG_NAME_SQL), {"o": org})).scalar()
    summary = _summary(row)
    summary["organization_name"] = str(name) if name else None
    return summary


def _summary(row: store.Connection | None) -> dict[str, Any]:
    if row is None or row.status == store.STATUS_DISCONNECTED:
        return {
            "connected": False, "status": store.STATUS_DISCONNECTED if row else "not_connected",
            "login": None, "scopes": [], "connected_by": None,
            "auth": None, "account_type": None, "repository_selection": None,
            "installation_url": None, "last_error": row.last_error if row else None,
        }
    if is_app_row(row):
        return {
            "connected": row.status == store.STATUS_CONNECTED,
            "status": row.status,
            "login": row.provider_meta.get("account_login") or None,
            "scopes": [],
            "connected_by": row.connected_by,
            "auth": AUTH_APP,
            "account_type": row.provider_meta.get("account_type") or None,
            "repository_selection": row.provider_meta.get("repository_selection") or None,
            "installation_url": _installation_url(row),
            "last_error": row.last_error,
        }
    return {
        "connected": row.status == store.STATUS_CONNECTED,
        "status": row.status,
        "login": (row.provider_meta.get("login") or None),
        "scopes": list(row.scopes),
        "connected_by": row.connected_by,
        "auth": AUTH_TOKEN,
        "account_type": None, "repository_selection": None, "installation_url": None,
        "last_error": row.last_error,
    }


def _installation_url(row: store.Connection) -> str | None:
    """The page at GitHub where the admin uninstalls the App (GH-E10)."""
    from gateway.connections import github_app

    iid = github_app.installation_id_of(row.provider_meta.get("installation_id"))
    if iid is None:
        return None
    return github_app.installation_settings_url(
        str(row.provider_meta.get("account_login") or ""),
        str(row.provider_meta.get("account_type") or ""), iid,
    )


async def disconnect(org: str) -> bool:
    """Delete the blob and set ``disconnected``. ``True`` when a token was removed.

    An old ``provider_keys`` row that was never migrated goes too, so a
    disconnect leaves no token of the organization anywhere.

    An ``app`` row goes to ``disconnected`` too, and the cached installation
    token of this process goes with it. Metorite does NOT uninstall the App at
    GitHub (GH-E10). The route gives the admin the link to do it.
    """
    from gateway.connections import github_app

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
    if is_app_row(row):
        github_app.drop_installation(row.provider_meta.get("installation_id"))
    return True
