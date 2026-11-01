"""Two pieces of process state never cross a test boundary (task 1332).

pr-check run 41305223510 (shard 4/6) failed four admit tests in
``test_h3_rls_promotion_rehearsal.py`` and ``test_h6_identity_shadow.py``.
They passed alone. A replay of that shard found a chain of two leaks:

1. ``test_crm_z10d_modules_r8.py`` left DATABASE_URL naming a database that
   its module then dropped.
2. ``test_delegated_artifact_card.py`` ran an agent run. Its access read met
   that missing database and set ``acb_auth.access._tables_missing``, a
   process-wide latch. From then on, every member resolved to
   ``is_active=False`` with no query.

``tests/conftest.py`` holds one guard for each:
``_restore_the_database_url`` and ``_reset_the_access_tables_latch``. Each
pair below is the fence of one guard (R7). The tests run in file order. The
first test of a pair leaks on purpose and does not clean up. The second
needs the state back.

Mutation: delete a guard, and the second test of its pair fails.
"""
from __future__ import annotations

import os

import acb_auth.access as access


class _Result:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict | None:
        return self._row


class _Session:
    """The one ``execute`` that ``resolve_access`` makes, with no database."""

    def __init__(self, *, row: dict | None = None,
                 error: Exception | None = None) -> None:
        self._row = row
        self._error = error

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, *_a: object, **_k: object) -> _Result:
        if self._error is not None:
            raise self._error
        return _Result(self._row)


def _factory(session: _Session):
    """``_get_session_factory()()`` returns the session."""
    return lambda: (lambda: session)


_ACTIVE_ROW = {
    "user_id": "u-1",
    "organization_id": "org-1",
    "status": "active",
    "legacy_role": "employee",
    "roles": ["member"],
    "role_permissions": ["feature:chat"],
    "overrides": [],
}


async def test_a_test_that_leaves_the_latch_set(monkeypatch) -> None:
    """The leak, made on purpose. ``monkeypatch`` restores the factory and
    the flag, but not the latch: ``resolve_access`` sets it by a ``global``
    assignment. The error text is the one the replay measured."""
    monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    missing = RuntimeError('database "acb_tenant_h3rls" does not exist')
    monkeypatch.setattr(access, "_get_session_factory",
                        _factory(_Session(error=missing)))

    got = await access.resolve_access("leak@latch.test", use_cache=False)

    assert got.is_active is False
    assert access._tables_missing is True, (
        "the failure path no longer sets the latch, so this fence is vacuous")


async def test_the_next_test_starts_with_the_latch_off(monkeypatch) -> None:
    """The guard cleared what the test above left. A live member resolves."""
    monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)

    assert access._tables_missing is False, (
        "the access-tables latch crossed a test boundary: "
        "tests/conftest.py::_reset_the_access_tables_latch is gone or broken")

    monkeypatch.setattr(access, "_get_session_factory",
                        _factory(_Session(row=_ACTIVE_ROW)))
    got = await access.resolve_access("live@latch.test", use_cache=False)
    assert got.is_active is True
    assert "member" in got.roles


# ── DATABASE_URL ─────────────────────────────────────────────────────────────

#: What DATABASE_URL held before the leak below. ``_MISSING`` stands for unset.
_MISSING = object()
_BEFORE_THE_LEAK: list[object] = []
_DROPPED_DSN = "postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant_dropped"


def test_a_test_that_leaves_the_database_url_set() -> None:
    """The leak, made on purpose: a direct write, as the late exit of a
    second concurrent ``tenant_engine_scope`` made one."""
    _BEFORE_THE_LEAK[:] = [os.environ.get("DATABASE_URL", _MISSING)]
    os.environ["DATABASE_URL"] = _DROPPED_DSN


def test_the_next_test_starts_with_the_database_url_it_had() -> None:
    """Alone, or after a shard boundary, nothing leaked and nothing is owed."""
    if not _BEFORE_THE_LEAK:
        return
    assert os.environ.get("DATABASE_URL", _MISSING) == _BEFORE_THE_LEAK[0], (
        "DATABASE_URL crossed a test boundary: "
        "tests/conftest.py::_restore_the_database_url is gone or broken")
