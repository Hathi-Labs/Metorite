"""DB-backed store for runtime-mutable model configuration.

Replaces git-tracked files (``infra/enabled_models.json`` and
``infra/litellm/tier_overrides.yaml``) that ``git reset --hard origin/main``
wiped on every deploy — the reason hidden models kept reappearing and tier
assignments kept reverting.  Config now lives in the ``model_config`` Postgres
table (migration ``35_model_config.sql``) so it survives deploys, restarts, and
reboots.

Blobs are JSON, keyed by config key:
  - ``"enabled_models"`` → ``{"enabled": [...], "hidden": [...]}``
  - ``"tier_overrides"`` → ``{"model_list": [...]}``

Uses a synchronous psycopg connection (no event loop, no extra dependency) so
it can be called from both sync helpers and FastAPI async handlers, mirroring
the URL-parsing approach in ``key_store.py``.
"""
from __future__ import annotations

import json
from typing import Any

from acb_common import get_logger, get_settings
from acb_common.dsn import conninfo as dsn_conninfo

_log = get_logger("model_config")


def _conninfo() -> str:
    """Translate the SQLAlchemy ``database_url`` into a psycopg conninfo string."""
    return dsn_conninfo(str(get_settings().database_url))


#: MT-0d — resolve the owning organization for a config read/write.
#: Mirrors ``key_store._resolve_org`` deliberately: ``None`` means "the sole
#: organization", which stops resolving the moment a second one exists, so an
#: untenanted read fails CLOSED rather than serving another tenant's model
#: config. A "default org" fallback would keep answering after tenant #2 and is
#: exactly the leak MT-0d closes (``saas_multitenancy.md`` §6.3).
_SOLE_ORG_SQL = "SELECT id FROM organization WHERE (SELECT count(*) FROM organization) = 1"


def _resolve_org(cur: Any, organization_id: str | None) -> str:
    if organization_id:
        return str(organization_id)
    cur.execute(_SOLE_ORG_SQL)
    row = cur.fetchone()
    return str(row[0]) if row else ""


def load_blob(
    key: str, default: Any = None, organization_id: str | None = None,
) -> Any:
    """Return the JSON blob stored under ``key`` for an organization.

    Returns ``default`` when the row is absent, no tenant resolves, or the DB is
    unreachable, so callers can fall back to a legacy file for one-time seeding.
    """
    import psycopg

    try:
        with psycopg.connect(_conninfo(), connect_timeout=5) as conn, conn.cursor() as cur:
            org = _resolve_org(cur, organization_id)
            if not org:
                _log.warning("model_config.no_tenant_resolved", key=key)
                return default
            cur.execute(
                "SELECT value FROM model_config "
                " WHERE organization_id = %s AND key = %s",
                (org, key),
            )
            row = cur.fetchone()
        if row is None or row[0] is None:
            return default
        val = row[0]
        # psycopg adapts jsonb → dict/list, but tolerate a text value too.
        return json.loads(val) if isinstance(val, str) else val
    except Exception as exc:
        _log.warning("model_config.load_failed", key=key, error=str(exc))
        return default


def save_blob(key: str, value: Any, organization_id: str | None = None) -> None:
    """Upsert a JSON blob under ``key`` for an organization.

    Raises on failure so the caller can surface a real save error instead of
    silently losing the change — including when no tenant resolves, because a
    write with no owner is how a row ends up readable by the wrong tenant.
    """
    import psycopg

    with psycopg.connect(_conninfo(), connect_timeout=5) as conn, conn.cursor() as cur:
        org = _resolve_org(cur, organization_id)
        if not org:
            raise RuntimeError(
                "model_config.save_blob requires an organization_id once more "
                "than one organization exists (MT-0d)"
            )
        cur.execute(
            "INSERT INTO model_config (organization_id, key, value, updated_at) "
            "VALUES (%s, %s, %s::jsonb, now()) "
            "ON CONFLICT (organization_id, key) DO UPDATE "
            "SET value = EXCLUDED.value, updated_at = now()",
            (org, key, json.dumps(value)),
        )
        conn.commit()
    _log.info("model_config.saved", key=key)
