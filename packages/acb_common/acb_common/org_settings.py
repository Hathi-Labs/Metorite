"""DB-backed store for organisation-wide preference blobs.

Settings that belong to the *organisation* rather than to a person or a
subsystem — currently the theming engine's default look (key ``appearance``),
which decides what every member sees before choosing a theme for themselves.

Blobs are JSON, keyed by setting name, in the ``org_settings`` Postgres table
(migration ``151_org_settings.sql``). Storing them in Postgres rather than a
git-tracked file is the lesson of ``35_model_config.sql``: a config file is
wiped by ``git reset --hard origin/main`` on every deploy, which is how hidden
models kept reappearing. An org-wide theme reverting on deploy would be the
same bug wearing different clothes.

Uses a synchronous psycopg connection — no event loop and no extra dependency —
so it can be called from both sync helpers and FastAPI async handlers, matching
``acb_llm.model_config``.

⚠️ **Every read and write is per organization** (migration 234). The table is
under FORCED row-level security on production, keyed by ``app.tenant_id``. This
module used to bind no tenant, so every write failed with ``invalid input
syntax for type uuid: ""`` (two logo uploads, 2026-10-08) and every read came
back empty. Now each statement runs in a transaction that sets the tenant with
``set_config(..., true)``, the same LOCAL binding ``acb_common.db`` uses, and
names ``organization_id`` in the SQL as well. The tenant comes from the
request's context (``acb_common.db.current_tenant``), never from input.

Fence: ``tests/unit/test_org_settings_tenancy_r8.py`` runs both functions as a
non-privileged role against a phase-4 database with two organizations.
"""
from __future__ import annotations

import json
from typing import Any

from acb_common._log import get_logger
from acb_common.dsn import conninfo as dsn_conninfo
from acb_common.settings import get_settings

_log = get_logger("org_settings")


def _conninfo() -> str:
    """Translate the SQLAlchemy ``database_url`` into a psycopg conninfo string."""
    return dsn_conninfo(str(get_settings().database_url))


def load_org_setting(key: str, default: Any = None) -> Any:
    """Return the JSON blob stored under ``key``.

    Returns ``default`` when the row is absent or the database is unreachable.
    Read failures are deliberately soft: an org-wide *preference* that cannot
    be loaded should leave the app on its built-in default, not break the page
    that asked for it.

    With no tenant bound it returns ``default`` without a query. A read that
    picked "some" organization would hand one company another's logo.
    """
    import psycopg  # noqa: PLC0415

    # Inside, not at module scope: importing `acb_common` must not import the
    # db module (`acb_common/db.py`, its header).
    from acb_common.db import current_tenant  # noqa: PLC0415

    tenant = current_tenant()
    if not tenant:
        _log.warning("org_settings.load_no_tenant", key=key)
        return default
    try:
        with psycopg.connect(_conninfo(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # LOCAL: the binding ends with this transaction (`acb_common.db`).
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
                cur.execute(
                    "SELECT value FROM org_settings WHERE organization_id = %s::uuid AND key = %s",
                    (tenant, key),
                )
                row = cur.fetchone()
        if row is None or row[0] is None:
            return default
        val = row[0]
        # psycopg adapts jsonb → dict/list, but tolerate a text value too.
        return json.loads(val) if isinstance(val, str) else val
    except Exception as exc:  # noqa: BLE001
        _log.warning("org_settings.load_failed", key=key, error=str(exc))
        return default


def save_org_setting(key: str, value: Any, updated_by: str = "") -> None:
    """Upsert a JSON blob under ``key``.

    Raises on failure, unlike :func:`load_org_setting`. A write that silently
    did nothing would tell an admin their change to everyone's UI had been
    applied when it had not.

    Raises:
        TenantUnbound: no tenant in context. A write is never defaulted.
    """
    import psycopg  # noqa: PLC0415

    from acb_common.db import TenantUnbound, current_tenant  # noqa: PLC0415

    tenant = current_tenant()
    if not tenant:
        raise TenantUnbound(f"org_settings.save({key!r}) has no tenant bound")
    with psycopg.connect(_conninfo(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
            cur.execute(
                "INSERT INTO org_settings (organization_id, key, value, updated_by, updated_at) "
                "VALUES (%s::uuid, %s, %s::jsonb, %s, now()) "
                "ON CONFLICT (organization_id, key) DO UPDATE "
                "SET value = EXCLUDED.value, "
                "    updated_by = EXCLUDED.updated_by, "
                "    updated_at = now()",
                (tenant, key, json.dumps(value), updated_by),
            )
        conn.commit()
    _log.info("org_settings.saved", key=key, updated_by=updated_by)
