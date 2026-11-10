"""The SQL of the source profile (WS-53 CRM-Q1, migration 247).

Every function takes the ``db`` that ``tenant_session(org)`` gave the caller.
Row level security binds the tenant. Each query also names the organization, so
a session with row level security off still reads one organization. The
organization comes from the server, never from request input (R5).

**One version for each change.** :func:`store` hashes the profile. When the hash
equals the latest one, it only moves ``fetched_at`` ("checked now"). When the
hash differs, it writes version + 1. The newest :data:`KEEP_VERSIONS` stay.

**The budget.** :func:`sync_budget` turns the edition of the latest profile into
the credits that the sync may spend in 24 hours (10 % of the edition's credits).
With no profile, or an edition that Zoho does not name, it returns ``None``, and
the caller keeps the 500-credit floor of CRM-Z5.

**No record data.** The profile is metadata. This module never reads a record.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

#: A profile that changes often must not grow the table without end.
KEEP_VERSIONS = 20

#: The share of the daily credits that the sync may spend in 24 hours.
BUDGET_FRACTION = 0.10


@dataclass(frozen=True)
class ProfileRow:
    version: int
    fetched_at: datetime | None
    profile: dict[str, Any]
    profile_hash: str
    connection_id: str | None = None


_LATEST_SQL = (
    "SELECT version, fetched_at, profile, profile_hash, connection_id "
    "FROM crm_source_profiles WHERE organization_id = CAST(:org AS uuid) "
    "ORDER BY fetched_at DESC, version DESC LIMIT 1"
)

_INSERT_SQL = (
    "INSERT INTO crm_source_profiles (organization_id, connection_id, version, "
    "profile, profile_hash) VALUES (CAST(:org AS uuid), CAST(:conn AS uuid), "
    ":version, CAST(:profile AS jsonb), :hash)"
)

_TOUCH_SQL = (
    "UPDATE crm_source_profiles SET fetched_at = now() "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:conn AS uuid) AND version = :version"
)

_PRUNE_SQL = (
    "DELETE FROM crm_source_profiles WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:conn AS uuid) AND version <= :cutoff"
)

_NEXT_VERSION_SQL = (
    "SELECT COALESCE(max(version), 0) + 1 AS next FROM crm_source_profiles "
    "WHERE organization_id = CAST(:org AS uuid) "
    "AND connection_id = CAST(:conn AS uuid)"
)


def profile_hash(profile: dict[str, Any]) -> str:
    """A stable hash of the profile. Key order and spacing do not change it."""
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row(row: Any) -> ProfileRow:
    profile = row.profile
    if isinstance(profile, str):
        profile = json.loads(profile)
    return ProfileRow(
        version=int(row.version),
        fetched_at=row.fetched_at,
        profile=dict(profile or {}),
        profile_hash=row.profile_hash,
        connection_id=str(row.connection_id) if getattr(row, "connection_id", None) else None,
    )


async def latest(db: Any, org: str) -> ProfileRow | None:
    """The newest profile of *org*, or ``None`` before the first discovery."""
    row = (await db.execute(text(_LATEST_SQL), {"org": str(org)})).first()
    return _row(row) if row is not None else None


async def store(
    db: Any, org: str, connection_id: str, profile: dict[str, Any],
) -> tuple[int, bool]:
    """Keep *profile* as the latest version. Returns ``(version, changed)``.

    ``changed`` is True when a new version was written. The caller owns the
    transaction.
    """
    digest = profile_hash(profile)
    current = await latest(db, org)
    if (
        current is not None
        and current.connection_id == str(connection_id)
        and current.profile_hash == digest
    ):
        await db.execute(
            text(_TOUCH_SQL),
            {"org": str(org), "conn": str(connection_id), "version": current.version},
        )
        return current.version, False
    version = int((await db.execute(
        text(_NEXT_VERSION_SQL), {"org": str(org), "conn": str(connection_id)},
    )).scalar() or 1)
    await db.execute(text(_INSERT_SQL), {
        "org": str(org), "conn": str(connection_id), "version": version,
        "profile": json.dumps(profile, sort_keys=True, default=str), "hash": digest,
    })
    await db.execute(text(_PRUNE_SQL), {
        "org": str(org), "conn": str(connection_id), "cutoff": version - KEEP_VERSIONS,
    })
    return version, True


def sync_budget(profile: dict[str, Any] | None) -> int | None:
    """Credits the sync may spend in 24 hours, from a profile. ``None`` when the
    edition is not known.

    ``daily_credits`` is the Zoho rule (``crm_platform.md`` §5.2 row 6), read
    through the mapping module of the provider.
    """
    from gateway.crm_sources.registry import mapping_for

    org = (profile or {}).get("org") or {}
    credits = mapping_for("zoho").daily_credits(org.get("edition"), org.get("licences"))
    if credits is None:
        return None
    return max(int(credits * BUDGET_FRACTION), 1)


async def budget_for(db: Any, org: str) -> int | None:
    """:func:`sync_budget` for the latest profile of *org*."""
    current = await latest(db, org)
    return sync_budget(current.profile if current is not None else None)


__all__ = [
    "BUDGET_FRACTION",
    "KEEP_VERSIONS",
    "ProfileRow",
    "budget_for",
    "latest",
    "profile_hash",
    "store",
    "sync_budget",
]
