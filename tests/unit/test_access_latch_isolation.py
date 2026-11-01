"""The access-tables latch never crosses a test boundary (task 1332).

``acb_auth.access._tables_missing`` is a process-wide latch. The first
``resolve_access`` that fails with "does not exist" sets it. From then on,
every member in the process resolves to ``is_active=False`` with no query.

pr-check run 41305223510 (shard 4/6) failed four admit tests in
``test_h3_rls_promotion_rehearsal.py`` and ``test_h6_identity_shadow.py``.
An earlier suite in the same process had left the latch set. Both suites
passed alone.

The guard is ``tests/conftest.py::_reset_the_access_tables_latch``. These two
tests are its fence (R7). They run in file order. The first sets the latch
through the real failure path and does not clean up. The second needs it OFF.

Mutation: delete the guard, and the second test fails.
"""
from __future__ import annotations

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
    assignment, exactly as a real database with no ``app_user`` does."""
    monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    missing = RuntimeError('relation "app_user" does not exist')
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
