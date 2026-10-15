"""The modules that the mirror sync holds back (WS-53 CRM-Q16, migration 268).

Spec: ``project-docs/specs/crm_platform.md`` §18.8.

``crm_settings.sync_holds`` maps a count key of ``crm_sync_runs.counts`` to an
entry ``{"held": bool, "by": "auto" | "admin", "count": int | None, "at": iso}``.
The scheduled sync does not read a held key. An admin can sync the key on its
own (``only``), or turn it on. An entry with ``held: false`` records that an
admin turned the key on, so the next import does not hold it again.

This module is the one reader and the one writer of the column. Every function
takes the ``db`` that ``tenant_session(org)`` gave the caller, and each query
also names the organization (R5). A write is an UPDATE only: a mirror org
always has its settings row, and an INSERT here would make one with defaults
that nobody chose.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

#: A module with more records than this is large. The first import holds it,
#: and the card suggests a sync of it on its own. Owner decision, 2026-10-15.
LARGE_MODULE_RECORDS = 5_000

BY_AUTO = "auto"
BY_ADMIN = "admin"

#: The fixed count keys that a hold may name. A generic module adds
#: ``module:<ApiName>``, which the caller checks against the source profile.
FIXED_KEYS: frozenset[str] = frozenset({
    "organizations", "contacts", "leads", "deals", "activities", "tasks",
})
MODULE_PREFIX = "module:"

_READ_SQL = (
    "SELECT sync_holds FROM crm_settings "
    "WHERE organization_id = CAST(:org AS uuid)"
)
_WRITE_SQL = (
    "UPDATE crm_settings SET sync_holds = sync_holds || CAST(:patch AS jsonb) "
    "WHERE organization_id = CAST(:org AS uuid) RETURNING sync_holds"
)
#: The auto-hold writes only a key that has no entry, so an admin's choice
#: made while the run planned is never overwritten.
_WRITE_NEW_SQL = (
    "UPDATE crm_settings SET sync_holds = sync_holds || CAST(:patch AS jsonb) "
    "WHERE organization_id = CAST(:org AS uuid) AND NOT (sync_holds ? :key) "
    "RETURNING sync_holds"
)
#: A start over forgets every hold. The next import decides again.
_CLEAR_SQL = (
    "UPDATE crm_settings SET sync_holds = '{}'::jsonb "
    "WHERE organization_id = CAST(:org AS uuid)"
)


def _clean(raw: Any) -> dict[str, dict[str, Any]]:
    """The entries of the column that have the right shape. Others are dropped."""
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): v for k, v in raw.items()
        if isinstance(v, dict) and isinstance(v.get("held"), bool)
    }


async def read(db: Any, org: str) -> dict[str, dict[str, Any]]:
    """Every entry of the org. An org with no settings row has none."""
    row = (await db.execute(text(_READ_SQL), {"org": str(org)})).first()
    return _clean(row.sync_holds) if row is not None else {}


def held_keys(entries: dict[str, dict[str, Any]]) -> frozenset[str]:
    """The keys that are held now."""
    return frozenset(k for k, v in entries.items() if v.get("held") is True)


async def write(
    db: Any, org: str, key: str, *, held: bool, by: str, count: int | None = None,
    only_new: bool = False,
) -> dict[str, dict[str, Any]] | None:
    """Set one entry. Returns the new map, or None when nothing was written:
    the org has no settings row, or ``only_new`` and the key has an entry."""
    entry = {
        "held": held, "by": by,
        "count": int(count) if count is not None else None,
        "at": datetime.now(UTC).isoformat(),
    }
    row = (await db.execute(text(_WRITE_NEW_SQL if only_new else _WRITE_SQL), {
        "org": str(org), "key": key, "patch": json.dumps({key: entry}),
    })).first()
    return _clean(row.sync_holds) if row is not None else None


async def clear(db: Any, org: str) -> None:
    """Forget every hold of the org. The start over calls this (CRM-X0)."""
    await db.execute(text(_CLEAR_SQL), {"org": str(org)})


def is_large(count: int | None) -> bool:
    return count is not None and count > LARGE_MODULE_RECORDS


__all__ = [
    "BY_ADMIN",
    "BY_AUTO",
    "FIXED_KEYS",
    "LARGE_MODULE_RECORDS",
    "MODULE_PREFIX",
    "clear",
    "held_keys",
    "is_large",
    "read",
    "write",
]
