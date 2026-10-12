"""The store of ``integration_connections`` — the only reader of the blob.

Spec: ``project-docs/specs/crm_platform.md`` §5.3 and §7.1. Migration 245.

⚠️ **This is the ONLY module that names the blob column** (``credentials`` plus
``_encrypted``). The column holds the Fernet blob of the tokens. Every other
module reads a :class:`Connection` (no blob) or a :class:`SourceCredential`
that this module decrypts. The fence is ``tests/unit/test_crm_connection.py``:
an AST scan that fails when another file under ``apps/`` or ``packages/`` names
the column.

* **The key.** The blob uses the Fernet key of ``acb_llm.key_store``
  (``ACB_MASTER_KEY``). This module makes no second key.
* **The session.** Each function takes the ``db`` that ``tenant_session`` gave
  the caller. Row level security binds the tenant. Each query also names the
  organization, so a session with RLS off still reads one organization. The
  organization comes from the server, never from request input (R5).
* **No secret out.** The blob, the access token and the refresh token never
  appear in a return value (except :func:`load_credential`, which exists to
  return them to server code), a log line or an error message.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common import get_logger
from acb_llm.key_store import InvalidToken, get_key_store
from sqlalchemy import text

from gateway.crm_sources.base import SourceCredential

_log = get_logger("connections.store")


class Undecryptable(Exception):
    """The stored blob does not open.

    The master key changed, or the blob is damaged. The message holds the name
    of the exception class only, never a byte of the blob.
    """


#: The ``status`` vocabulary, mirrored from the CHECK in migration 245, and
#: widened by migration 252.
STATUS_CONNECTED = "connected"
STATUS_NEEDS_RECONNECT = "needs_reconnect"
#: CRM-X0: the new consent is for another account than the one the records
#: came from. Nothing syncs until an admin starts over or connects the first
#: account again. ``shared_token`` gives no credential for it.
STATUS_NEEDS_RESET = "needs_reset"
STATUS_DISCONNECTED = "disconnected"

SCOPE_ORG = "org"
SCOPE_MEMBER = "member"

#: Each column except the blob. A SELECT list is built from this string only.
_PUBLIC_COLUMNS = (
    "id, organization_id, provider, owning_app, scope_kind, member_email, "
    "status, provider_meta, scopes, connected_by, last_sync_at, last_error, "
    "created_at, updated_at"
)

#: The where clause of one connection. ``COALESCE`` matches the unique index.
_KEY_WHERE = (
    "organization_id = CAST(:org AS uuid) AND provider = :provider "
    "AND owning_app = :owning_app AND scope_kind = :scope_kind "
    "AND COALESCE(member_email, '') = :member"
)

_GET_SQL = f"SELECT {_PUBLIC_COLUMNS} FROM integration_connections WHERE {_KEY_WHERE}"

_LIST_SQL = (
    f"SELECT {_PUBLIC_COLUMNS} FROM integration_connections "
    "WHERE organization_id = CAST(:org AS uuid) ORDER BY owning_app, provider, created_at"
)

_GET_BY_ID_SQL = (
    f"SELECT {_PUBLIC_COLUMNS} FROM integration_connections "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
)

_BLOB_SQL = (
    "SELECT credentials_encrypted FROM integration_connections "
    f"WHERE {_KEY_WHERE} AND status <> 'disconnected'"
)

_BLOB_BY_ID_SQL = (
    "SELECT credentials_encrypted FROM integration_connections "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status <> 'disconnected' FOR UPDATE"
)

_UPSERT_SQL = (
    "INSERT INTO integration_connections (organization_id, provider, "
    "owning_app, scope_kind, member_email, status, provider_meta, "
    "credentials_encrypted, scopes, connected_by, last_error, created_at, "
    "updated_at) VALUES (CAST(:org AS uuid), :provider, :owning_app, "
    ":scope_kind, :member_email, 'connected', CAST(:provider_meta AS jsonb), "
    ":blob, :scopes, :connected_by, NULL, now(), now()) "
    "ON CONFLICT (organization_id, provider, owning_app, scope_kind, "
    "(COALESCE(member_email, ''))) DO UPDATE SET status = 'connected', "
    "provider_meta = EXCLUDED.provider_meta, "
    "credentials_encrypted = EXCLUDED.credentials_encrypted, "
    "scopes = EXCLUDED.scopes, connected_by = EXCLUDED.connected_by, "
    "last_error = NULL, updated_at = now() "
    f"RETURNING {_PUBLIC_COLUMNS}"
)

_UPDATE_TOKENS_SQL = (
    "UPDATE integration_connections SET credentials_encrypted = :blob, "
    "provider_meta = provider_meta || CAST(:provider_meta AS jsonb), "
    "updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status <> 'disconnected'"
)

_MERGE_META_SQL = (
    "UPDATE integration_connections SET "
    "provider_meta = provider_meta || CAST(:extra AS jsonb), updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status <> 'disconnected'"
)

_NEEDS_RECONNECT_SQL = (
    "UPDATE integration_connections SET status = 'needs_reconnect', "
    "last_error = :code, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status <> 'disconnected'"
)

_NEEDS_RESET_SQL = (
    "UPDATE integration_connections SET status = 'needs_reset', "
    "last_error = :code, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
)

_MARK_SYNCED_SQL = (
    "UPDATE integration_connections SET last_sync_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    "AND status <> 'disconnected'"
)

#: What the sync learned about the account. A new consent or a disconnect drops
#: it, because the next connection may be another Zoho org (CRM-Q1 review).
_DISCOVERED_KEYS = ("domain_name", "edition", "tabs")

#: A new consent drops the profile. A disconnect keeps it (CRM-X0): the next
#: consent compares its account with the profile's, and a different account
#: gets ``needs_reset`` (``routes/crm/connection.py``).

_DROP_PROFILES_SQL = (
    "DELETE FROM crm_source_profiles WHERE connection_id = CAST(:id AS uuid) "
    "AND organization_id = CAST(:org AS uuid)"
)

_DISCONNECT_SQL = (
    "UPDATE integration_connections SET status = 'disconnected', "
    "provider_meta = provider_meta - 'domain_name' - 'edition' - 'tabs', "
    "credentials_encrypted = NULL, last_error = NULL, updated_at = now() "
    "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) "
    f"RETURNING {_PUBLIC_COLUMNS}"
)


@dataclass(frozen=True)
class Connection:
    """One registry row, with no blob."""

    id: str
    organization_id: str
    provider: str
    owning_app: str
    scope_kind: str
    member_email: str | None
    status: str
    provider_meta: dict[str, Any]
    scopes: tuple[str, ...]
    connected_by: str | None
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime | None
    updated_at: datetime | None


def _connection(row: Any) -> Connection:
    meta = row.provider_meta
    if isinstance(meta, str):
        meta = json.loads(meta)
    return Connection(
        id=str(row.id),
        organization_id=str(row.organization_id),
        provider=row.provider,
        owning_app=row.owning_app,
        scope_kind=row.scope_kind,
        member_email=row.member_email,
        status=row.status,
        provider_meta=dict(meta or {}),
        scopes=tuple(row.scopes or ()),
        connected_by=row.connected_by,
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _key(
    org: str, provider: str, owning_app: str, scope_kind: str, member_email: str | None,
) -> dict[str, Any]:
    if scope_kind not in (SCOPE_ORG, SCOPE_MEMBER):
        raise ValueError("scope_kind is 'org' or 'member'")
    member = (member_email or "").strip().lower()
    if (scope_kind == SCOPE_MEMBER) != bool(member):
        raise ValueError("a member connection names a member, an org connection does not")
    return {
        "org": str(uuid.UUID(str(org))),
        "provider": provider,
        "owning_app": owning_app,
        "scope_kind": scope_kind,
        "member": member,
    }


# ── The blob ────────────────────────────────────────────────────────────────


def _keys() -> Any:
    """The one key seam (``ACB_MASTER_KEY``). The blob has no tenant key."""
    return get_key_store()


def legacy_keys() -> Any:
    """The per-org key store, for a migration that moves an old ``provider_keys`` row.

    WS-54 IN-7 reads the old ``github:token`` row through it once, then deletes
    it. Going through this module adds no new key store call site.
    """
    return _keys()


def _seal(credential: SourceCredential) -> str:
    plain = json.dumps(
        {
            "access_token": credential.access_token,
            "refresh_token": credential.refresh_token,
            "expires_at": credential.expires_at.isoformat(),
            "scopes": list(credential.scopes),
            "provider_meta": dict(credential.provider_meta),
        },
        separators=(",", ":"),
    )
    return _keys().encrypt(plain)


def _unseal(blob: str) -> SourceCredential:
    """Open the blob, or raise :class:`Undecryptable` with no cause attached.

    ``from None`` drops the cause, so no trace can carry the blob or a token.

    ⚠️ The key store is fetched OUTSIDE the ``try``. A missing or wrong
    ``ACB_MASTER_KEY`` setting is an operator fault that a restart repairs. It
    must fail loud and write nothing. If it became ``Undecryptable``, every
    org would be marked ``needs_reconnect`` for good (CRM-Z2 re-review P1).
    Only a blob that this key cannot open is ``Undecryptable``.
    """
    keys = _keys()
    try:
        data = json.loads(keys.decrypt(blob))
        return SourceCredential(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            scopes=tuple(data.get("scopes") or ()),
            provider_meta={
                str(k): str(v) for k, v in (data.get("provider_meta") or {}).items()
            },
        )
    # ValueError covers binascii.Error, UnicodeDecodeError and a bad JSON.
    except (InvalidToken, ValueError, KeyError, TypeError) as exc:
        raise Undecryptable(type(exc).__name__) from None


# ── Reads ───────────────────────────────────────────────────────────────────


async def get(
    db: Any,
    org: str,
    provider: str,
    owning_app: str,
    *,
    scope_kind: str = SCOPE_ORG,
    member_email: str | None = None,
) -> Connection | None:
    """The connection row, or ``None``. It carries no blob."""
    params = _key(org, provider, owning_app, scope_kind, member_email)
    row = (await db.execute(text(_GET_SQL), params)).first()
    return _connection(row) if row is not None else None


async def list_all(db: Any, org: str) -> list[Connection]:
    """Every connection row of *org*, with no blob (the Integrations console)."""
    org = str(uuid.UUID(str(org)))
    rows = (await db.execute(text(_LIST_SQL), {"org": org})).fetchall()
    return [_connection(r) for r in rows]


async def get_by_id(db: Any, org: str, connection_id: str) -> Connection | None:
    """One connection by id, in *org* only, or ``None``. It carries no blob.

    A malformed id is ``None``, not an error, so a route can answer 404.
    """
    try:
        params = {"id": str(uuid.UUID(str(connection_id))), "org": str(uuid.UUID(str(org)))}
    except ValueError:
        return None
    row = (await db.execute(text(_GET_BY_ID_SQL), params)).first()
    return _connection(row) if row is not None else None


async def load_credential(
    db: Any,
    org: str,
    provider: str,
    owning_app: str,
    *,
    scope_kind: str = SCOPE_ORG,
    member_email: str | None = None,
) -> SourceCredential | None:
    """The decrypted credential, or ``None`` with no blob or after a disconnect.

    A connection in ``needs_reconnect`` still returns its credential, because
    the callback needs the old refresh token to revoke it.

    A blob that does not open also returns ``None``. A disconnect must still
    clear the row, so nothing on that path may fail on the old blob
    (CRM-Z2 review P1).
    """
    params = _key(org, provider, owning_app, scope_kind, member_email)
    row = (await db.execute(text(_BLOB_SQL), params)).first()
    if row is None or not row.credentials_encrypted:
        return None
    try:
        return _unseal(row.credentials_encrypted)
    except Undecryptable as exc:
        _log.warning("connection blob does not open (%s)", exc)
        return None


async def load_credential_locked(
    db: Any, org: str, connection_id: str,
) -> SourceCredential | None:
    """Like :func:`load_credential`, by id, with ``FOR UPDATE`` on the row.

    The shared token helper uses it: a second worker waits on the row lock,
    then reads the token that the first worker wrote. A blob that does not
    open raises :class:`Undecryptable`, so the helper can stop the connection.
    """
    row = (await db.execute(
        text(_BLOB_BY_ID_SQL), {"id": str(connection_id), "org": str(org)},
    )).first()
    if row is None or not row.credentials_encrypted:
        return None
    return _unseal(row.credentials_encrypted)


# ── Writes ──────────────────────────────────────────────────────────────────


async def save_credential(
    db: Any,
    org: str,
    provider: str,
    owning_app: str,
    credential: SourceCredential,
    *,
    connected_by: str,
    scope_kind: str = SCOPE_ORG,
    member_email: str | None = None,
    scopes: Sequence[str] | None = None,
) -> Connection:
    """Insert the connection, or update the one row that already exists.

    A reconnect updates the row: status ``connected``, a new blob, no error.
    """
    params = _key(org, provider, owning_app, scope_kind, member_email)
    params["member_email"] = params.pop("member") or None
    params.update(
        provider_meta=json.dumps(dict(credential.provider_meta)),
        blob=_seal(credential),
        scopes=list(scopes if scopes is not None else credential.scopes),
        connected_by=(connected_by or "").strip().lower() or None,
    )
    row = (await db.execute(text(_UPSERT_SQL), params)).first()
    # A new consent may be another org or data centre: the profile and the
    # discovered keys are the old account's. Discovery runs again at the next run.
    await db.execute(text(_DROP_PROFILES_SQL), {"id": str(row.id), "org": str(row.organization_id)})
    return _connection(row)


async def update_tokens(
    db: Any, org: str, connection_id: str, credential: SourceCredential,
) -> None:
    """Write a refreshed credential back, so every worker shares it."""
    await db.execute(text(_UPDATE_TOKENS_SQL), {
        "id": str(connection_id),
        "org": str(org),
        "blob": _seal(credential),
        "provider_meta": json.dumps(dict(credential.provider_meta)),
    })


async def merge_provider_meta(
    db: Any, org: str, connection_id: str, extra: dict[str, Any],
) -> None:
    """Add keys to ``provider_meta`` of one connection and keep the others.

    The sync writes what it learned about the account here (``domain_name``,
    ``edition``, ``tabs``). The row holds no token, so the keys are plain
    metadata. A refresh and a reconnect keep them, because both merge into the
    column and never replace it. A key with a ``None`` value is dropped.
    """
    kept = {str(k): v for k, v in extra.items() if v is not None}
    if not kept:
        return
    await db.execute(text(_MERGE_META_SQL), {
        "id": str(connection_id), "org": str(org), "extra": json.dumps(kept),
    })


def safe_error_code(code: str | None) -> str:
    """A provider error code when it looks like one, else ``refresh_failed``."""
    value = (code or "").strip()
    if value and len(value) <= 40 and value.replace("_", "").isalnum():
        return value
    return "refresh_failed"


async def mark_needs_reconnect(db: Any, org: str, connection_id: str, code: str) -> None:
    """Set ``needs_reconnect`` and ``last_error`` to the safe *code*.

    The caller passes a provider error code. This function keeps only a short
    word, so a free text can never reach the column.
    """
    await db.execute(text(_NEEDS_RECONNECT_SQL), {
        "id": str(connection_id), "org": str(org), "code": safe_error_code(code),
    })


async def mark_needs_reset(
    db: Any, org: str, connection_id: str, code: str = "different_org",
) -> None:
    """Set ``needs_reset`` (CRM-X0) and ``last_error`` to the safe *code*.

    It keeps the blob and the profile. The blob is the token of the FIRST
    account, so a disconnect still revokes it, and a consent to the first
    account again still compares with the profile.
    """
    await db.execute(text(_NEEDS_RESET_SQL), {
        "id": str(connection_id), "org": str(org), "code": safe_error_code(code),
    })


async def disconnect(db: Any, org: str, connection_id: str) -> Connection | None:
    """Delete the blob and set ``disconnected``. The row stays as a record.

    The source profile stays too (CRM-X0), so the next consent can tell the
    same account from another one. The start over deletes it.
    """
    row = (await db.execute(
        text(_DISCONNECT_SQL), {"id": str(connection_id), "org": str(org)},
    )).first()
    return _connection(row) if row is not None else None


async def mark_synced(db: Any, org: str, connection_id: str) -> None:
    """Set ``last_sync_at`` to now. The sync engine calls it after a good run."""
    await db.execute(text(_MARK_SYNCED_SQL), {"id": str(connection_id), "org": str(org)})
