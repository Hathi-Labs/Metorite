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
    """
    import psycopg  # noqa: PLC0415

    try:
        with psycopg.connect(_conninfo(), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM org_settings WHERE key = %s", (key,))
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
    """
    import psycopg  # noqa: PLC0415

    with psycopg.connect(_conninfo(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO org_settings (key, value, updated_by, updated_at) "
                "VALUES (%s, %s::jsonb, %s, now()) "
                "ON CONFLICT (key) DO UPDATE "
                "SET value = EXCLUDED.value, "
                "    updated_by = EXCLUDED.updated_by, "
                "    updated_at = now()",
                (key, json.dumps(value), updated_by),
            )
        conn.commit()
    _log.info("org_settings.saved", key=key, updated_by=updated_by)
