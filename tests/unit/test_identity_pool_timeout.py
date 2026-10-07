"""A pool timeout in the identity read is a retryable 503, never "nobody".

🔴 Measured on production, 2026-10-07. The email sync rewrote every listed
message on every pass, and that starved the IO budget of the database. The
pool queue then timed out on sign-in. ``resolve_access`` swallowed it into NO
ACCESS and ``resolve_identity`` into ``(None, None)``, so a live member signed
in as nobody in no org, and ``/auth/me`` answered 200 with that.

The rule (R7): a pool or connect TIMEOUT raises
:class:`acb_auth.access.IdentityUnavailable`, and
``acb_auth.deps._with_resolved_access`` answers 503 with ``Retry-After``.
Every other failure, and a real "no such member", keeps its old answer.

Mutation: in ``acb_auth.access``, delete the ``_raise_if_pool_timeout`` call
in the ``except`` block of ``resolve_identity`` or ``resolve_access``. The
matching ``test_*_timeout_raises`` tests then fail, because the read returns
an empty identity again.

DB-free: a fake session factory raises the exact driver exception.
"""
from __future__ import annotations

import asyncio

import pytest
import sqlalchemy.exc
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

_POOL_TIMEOUT = sqlalchemy.exc.TimeoutError(
    "QueuePool limit of size 5 overflow 10 reached, connection timed out,"
    " timeout 30.00"
)


class _Session:
    def __init__(self, exc: BaseException | None, rows: list | None = None):
        self._exc = exc
        self._rows = rows or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        if self._exc is not None:
            raise self._exc
        rows = self._rows

        class _Result:
            def mappings(self):
                class _M:
                    def first(self):
                        return rows[0] if rows else None

                    def all(self):
                        return list(rows)

                return _M()

        return _Result()


def _factory(exc: BaseException | None, rows: list | None = None):
    """``_get_session_factory`` stand-in: returns a callable session maker."""
    return lambda: (lambda: _Session(exc, rows))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import acb_auth.access as access_mod
    from acb_common import db_busy

    monkeypatch.delenv("IDENTITY_CUTOVER", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    token = access_mod._identity_read_failed.set(False)
    db_busy.reset()
    yield
    access_mod._identity_read_failed.reset(token)
    db_busy.reset()


@pytest.mark.parametrize("exc", [
    _POOL_TIMEOUT,
    TimeoutError(),             # asyncpg connect or command timeout
    asyncio.TimeoutError(),     # the same class on 3.11 and later
], ids=["sqlalchemy_pool", "builtin_timeout", "asyncio_timeout"])
async def test_resolve_identity_timeout_raises(monkeypatch, exc):
    import acb_auth.access as access_mod

    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(exc))
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.resolve_identity("owner@example.com")


async def test_resolve_identity_timeout_raises_on_the_cutover_leg(monkeypatch):
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(_POOL_TIMEOUT))
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.resolve_identity("owner@example.com")


async def test_resolve_access_timeout_raises(monkeypatch):
    import acb_auth.access as access_mod

    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(_POOL_TIMEOUT))
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.resolve_access("owner@example.com", use_cache=False)


async def test_a_timeout_wrapped_by_the_driver_is_still_found(monkeypatch):
    """SQLAlchemy wraps a driver error and keeps it as ``.orig``."""
    import acb_auth.access as access_mod

    wrapped = sqlalchemy.exc.OperationalError("SELECT 1", {}, TimeoutError())
    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(wrapped))
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.resolve_identity("owner@example.com")


async def test_a_timeout_marks_the_database_busy(monkeypatch):
    import acb_auth.access as access_mod
    from acb_common import db_busy

    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(_POOL_TIMEOUT))
    with pytest.raises(access_mod.IdentityUnavailable):
        await access_mod.resolve_identity("owner@example.com")
    assert db_busy.recently_busy() is True


# ── what must NOT change ────────────────────────────────────────────────────


async def test_any_other_failure_keeps_its_old_answer(monkeypatch):
    """A failure that is not a timeout still returns ``(None, None)`` and
    sets the flag, as before. Routes that need no tenant keep degrading."""
    import acb_auth.access as access_mod

    monkeypatch.setattr(
        access_mod, "_get_session_factory", _factory(RuntimeError("boom")))
    assert await access_mod.resolve_identity("owner@example.com") == (None, None)
    assert access_mod.identity_read_failed() is True

    access = await access_mod.resolve_access("owner@example.com", use_cache=False)
    assert access.is_active is False


async def test_no_such_member_is_still_nobody(monkeypatch):
    import acb_auth.access as access_mod

    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(None, []))
    assert await access_mod.resolve_identity("nobody@example.com") == (None, None)
    assert access_mod.identity_read_failed() is False
    access = await access_mod.resolve_access("nobody@example.com", use_cache=False)
    assert access.is_active is False


# ── the auth dependency: the timeout becomes a 503 ──────────────────────────


def _app():
    from acb_auth import deps
    from acb_auth.roles import UserContext, UserRole

    async def _me():
        return await deps._with_resolved_access(
            UserContext(email="owner@example.com", role=UserRole.EMPLOYEE))

    app = FastAPI()

    @app.get("/me")
    async def me(user=Depends(_me)):  # noqa: B008
        return {"org": user.organization_id, "active": user.access.is_active}

    return app


def test_the_auth_dependency_answers_503_with_retry_after(monkeypatch):
    import acb_auth.access as access_mod

    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(_POOL_TIMEOUT))
    res = TestClient(_app()).get("/me")
    assert res.status_code == 503
    assert res.headers["Retry-After"] == "2"
    # The driver's message names the pool and its limits. It never leaks.
    assert "QueuePool" not in res.text


def test_the_auth_dependency_keeps_nobody_for_a_real_nobody(monkeypatch):
    import acb_auth.access as access_mod

    monkeypatch.setattr(access_mod, "_get_session_factory", _factory(None, []))
    res = TestClient(_app()).get("/me")
    assert res.status_code == 200
    assert res.json() == {"org": None, "active": False}


# ── the classifier ──────────────────────────────────────────────────────────


def test_is_db_timeout_names_only_timeouts():
    from acb_common import db_busy

    assert db_busy.is_db_timeout(_POOL_TIMEOUT) is True
    assert db_busy.is_db_timeout(TimeoutError()) is True
    assert db_busy.is_db_timeout(RuntimeError("timed out")) is False
    assert db_busy.is_db_timeout(ConnectionRefusedError()) is False
    assert db_busy.is_db_timeout(
        sqlalchemy.exc.ProgrammingError("SELECT 1", {}, Exception("syntax"))
    ) is False


# ── the shared-session fold must fail CLOSED (review round 1) ───────────────


class _RoomSession:
    """Session-subject lookup: the room holds one other member, Bob."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        class _R:
            def fetchall(self):
                return [("bob@example.com",)]

            def mappings(self):
                return self

        return _R()


async def test_a_participant_timeout_caps_a_shared_run_to_nothing(monkeypatch):
    """🔴 Found in review. ``executor._integration_authorizer`` returns
    ``None`` (NO filter) on any exception. A participant timeout that left
    ``resolve_session_access`` gave a shared-room run every credential.

    Mutation: in ``resolve_session_access``, call ``resolve_access`` in the
    fold loop again instead of ``_access_or_inactive``. The authorizer is
    then ``None`` and this test fails."""
    import acb_auth
    import acb_auth.access as access_mod
    from acb_auth.permissions import build_access
    from orchestrator import executor

    alice = build_access(
        ["integrations:use:*"], [], roles=["member"], is_active=True)

    async def _resolve(email, **_k):
        if email == "alice@example.com":
            return alice
        raise access_mod.IdentityUnavailable("access") from _POOL_TIMEOUT

    monkeypatch.setattr(access_mod, "resolve_access", _resolve)
    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)
    monkeypatch.setattr(
        access_mod, "_get_session_factory", lambda: (lambda: _RoomSession()))

    authz = await executor._integration_authorizer(
        {"user_email": "alice@example.com"}, "thread-1")

    assert authz is not None, "no filter at all: every credential reaches the run"
    assert authz("zoho-crm") is False


async def test_an_actor_timeout_denies_every_credential(monkeypatch):
    """🔴 Found in the security check of #708 (it predates the PR). The
    ACTOR's own ``resolve_access`` timed out, and the broad catch in
    ``executor._integration_authorizer`` returned ``None``: no filter, so the
    run got every credential. A background email-automation run during IO
    starvation is the likely case.

    Mutation: delete the ``except IdentityUnavailable`` clause in
    ``_integration_authorizer``. The authorizer is then ``None`` and this
    test fails."""
    import acb_auth
    import acb_auth.access as access_mod
    from orchestrator import executor

    async def _resolve(email, **_k):
        raise access_mod.IdentityUnavailable("access") from _POOL_TIMEOUT

    monkeypatch.setattr(access_mod, "resolve_access", _resolve)
    monkeypatch.setattr(acb_auth, "resolve_access", _resolve)

    for thread_id in (None, "thread-1"):
        authz = await executor._integration_authorizer(
            {"user_email": "alice@example.com"}, thread_id)
        assert authz is not None, "no filter at all: every credential reaches the run"
        for service in ("zoho-crm", "gmail", "slack", "github"):
            assert authz(service) is False
