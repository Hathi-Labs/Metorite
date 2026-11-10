"""DB engine must bound the CONNECT phase (audit BO robustness follow-up).

A slow / firewalled Postgres host must never hang a caller indefinitely — most
critically the best-effort ``acb_audit.record`` write, whose whole contract is
"never block the caller". That guarantee only holds if the engine gives libpq a
``connect_timeout``; a bare ``create_engine`` retries a non-blocking connect in a
``select`` loop with no deadline (observed: a full unit run wedged for 90s on a
single audit write to an unreachable host).

These lock that the timeout is (1) configured with a sane positive default and
(2) actually threaded into the engine's ``connect_args`` for Postgres URLs, while
leaving non-Postgres (e.g. sqlite) URLs untouched.
"""
from __future__ import annotations

from types import SimpleNamespace

from acb_common import get_settings
from acb_graph.db import _engine_kwargs


def test_default_connect_timeout_is_positive():
    # A zero/absent timeout would re-introduce the indefinite hang.
    assert get_settings().db_connect_timeout > 0


def test_postgres_url_gets_connect_timeout():
    s = SimpleNamespace(
        database_url="postgresql+psycopg://u:p@db:5432/x", db_connect_timeout=7,
        db_sync_pool_size=2, db_sync_max_overflow=1, db_pool_timeout=10,
    )
    kw = _engine_kwargs(s)
    assert kw["connect_args"] == {"connect_timeout": 7}
    assert kw["pool_pre_ping"] is True


def test_non_postgres_url_left_untouched():
    # sqlite has no connect_timeout param — applying it would raise.
    s = SimpleNamespace(database_url="sqlite:///./x.db", db_connect_timeout=7)
    kw = _engine_kwargs(s)
    assert "connect_args" not in kw
    # The pool budget binds the Postgres pooler only (2026-10-06).
    assert "pool_size" not in kw
