"""R8 — ``tenant_session`` finds an aborted transaction (H-249, WS-29).

The fault this fences. A block catches the error of a failed statement and
goes on. Postgres then refuses each later statement of the transaction
(SQLSTATE 25P02), and the ``COMMIT`` at the end of the block answers
``ROLLBACK`` with no error. So every write of the block is lost in silence.

The seam now marks the connection when a statement fails (a ``handle_error``
listener). At the clean end of a marked block it sends one ``SELECT 1``. When
that fails with 25P02, it rolls back and logs ``db.tenant_session_aborted``.
``TENANT_SESSION_ABORT_MODE=raise`` also raises ``TenantTransactionAborted``.
A clean block sends no probe.

The DB half runs the real seam on a REAL Postgres. A hermetic fake would agree
with any SQL it is handed, and it has no transaction to abort. The fake half
proves that a test double still runs as before.

⚠️ The DB half SKIPS without ``TENANT_LADDER_DATABASE_URL``, and a skip is not a
pass. Run ``eval "$(bash scripts/dev_db.sh --export)"`` first.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import db as shared
from acb_common.db import TenantTransactionAborted, tenant_session
from acb_common.settings import get_settings
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from structlog.testing import capture_logs

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE, _URL

ORG = str(uuid.uuid4())
EVENT = "db.tenant_session_aborted"
PROBE = "SELECT 1"


@pytest.fixture
def table() -> Iterator[str]:
    """A scratch table, dropped after the test. It holds no tenant data."""
    name = f"h249_abort_{uuid.uuid4().hex[:10]}"
    admin = create_engine(_URL)
    with admin.begin() as c:
        c.execute(text(f"CREATE TABLE {name} (v TEXT NOT NULL)"))
    try:
        yield name
    finally:
        with admin.begin() as c:
            c.execute(text(f"DROP TABLE IF EXISTS {name}"))
        admin.dispose()


def _rows(name: str) -> list[str]:
    admin = create_engine(_URL)
    try:
        with admin.connect() as c:
            return [r[0] for r in c.execute(text(f"SELECT v FROM {name} ORDER BY v"))]
    finally:
        admin.dispose()


@contextlib.contextmanager
def _abort_mode(value: str) -> Iterator[None]:
    """Set ``TENANT_SESSION_ABORT_MODE`` for the real settings reader."""
    prior = os.environ.get("TENANT_SESSION_ABORT_MODE")
    os.environ["TENANT_SESSION_ABORT_MODE"] = value
    get_settings.cache_clear()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("TENANT_SESSION_ABORT_MODE", None)
        else:
            os.environ["TENANT_SESSION_ABORT_MODE"] = prior
        get_settings.cache_clear()


@pytest.fixture(params=["log", "raise"])
def mode(request: pytest.FixtureRequest) -> Iterator[str]:
    """Each mode in turn."""
    with _abort_mode(request.param):
        yield request.param


def _statements() -> list[str]:
    """Record each statement that the shared engine sends from now on."""
    seen: list[str] = []

    def _record(conn, cursor, statement, params, context, executemany) -> None:
        seen.append(statement.strip())

    event.listen(shared.get_engine().sync_engine, "before_cursor_execute", _record)
    return seen


async def _fail_and_swallow(db: Any) -> None:
    """The fault: a failed statement whose error the block catches."""
    with contextlib.suppress(DBAPIError):
        await db.execute(text("SELECT 1 / 0"))


def _events(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e.get("event") == EVENT]


# ── 1 and 2: the aborted block ─────────────────────────────────────────────


@_DB_GATE
async def test_raise_mode_raises_and_the_write_is_absent(table: str) -> None:
    with _abort_mode("raise"):
        async with tenant_engine_scope(_URL):
            with capture_logs() as logs, pytest.raises(TenantTransactionAborted) as err:
                async with tenant_session(ORG) as db:
                    await _fail_and_swallow(db)
                    with contextlib.suppress(DBAPIError):
                        await db.execute(text(f"INSERT INTO {table} VALUES ('lost')"))
            assert isinstance(err.value.__cause__, DBAPIError)
            assert len(_events(logs)) == 1
    assert _rows(table) == []


@_DB_GATE
async def test_raise_mode_raises_when_the_block_only_swallows_the_error() -> None:
    with _abort_mode("raise"):
        async with tenant_engine_scope(_URL):
            with pytest.raises(TenantTransactionAborted):
                async with tenant_session(ORG) as db:
                    await _fail_and_swallow(db)


@_DB_GATE
async def test_log_mode_logs_does_not_raise_and_the_write_is_absent(table: str) -> None:
    with _abort_mode("log"):
        async with tenant_engine_scope(_URL):
            with capture_logs() as logs:
                async with tenant_session(ORG) as db:
                    await _fail_and_swallow(db)
                    with contextlib.suppress(DBAPIError):
                        await db.execute(text(f"INSERT INTO {table} VALUES ('lost')"))
    found = _events(logs)
    assert len(found) == 1
    assert found[0]["log_level"] == "warning"
    assert found[0]["tenant"] == ORG
    assert found[0]["mode"] == "log"
    # The caller is the code that holds the session, not the seam.
    assert found[0]["caller"].startswith(__file__ + ":")
    assert _rows(table) == []


@_DB_GATE
async def test_an_unknown_mode_reads_as_log(table: str) -> None:
    with _abort_mode("RAISE-PLEASE"):
        async with tenant_engine_scope(_URL):
            with capture_logs() as logs:
                async with tenant_session(ORG) as db:
                    await _fail_and_swallow(db)
    assert [e["mode"] for e in _events(logs)] == ["log"]


def _orm_row(name: str) -> type:
    """A mapped class over the scratch table, for a pending ORM write."""
    from sqlalchemy import Column, Table, Text
    from sqlalchemy.orm import registry

    reg = registry()

    class Row:
        def __init__(self, v: str) -> None:
            self.v = v

    reg.map_imperatively(Row, Table(name, reg.metadata, Column("v", Text, primary_key=True)))
    return Row


@_DB_GATE
async def test_log_mode_keeps_the_error_of_a_pending_orm_write(table: str) -> None:
    # Before H-249 the commit flushed the pending write, and the flush raised
    # 25P02. Log mode must not turn that error into a silent pass.
    row = _orm_row(table)
    with _abort_mode("log"):
        async with tenant_engine_scope(_URL):
            with capture_logs() as logs, pytest.raises(DBAPIError) as err:
                async with tenant_session(ORG) as db:
                    await _fail_and_swallow(db)
                    db.add(row("lost"))
    assert getattr(err.value.orig, "sqlstate", None) == "25P02"
    assert len(_events(logs)) == 1
    assert _rows(table) == []


@_DB_GATE
async def test_raise_mode_raises_for_a_pending_orm_write(table: str) -> None:
    row = _orm_row(table)
    with _abort_mode("raise"):
        async with tenant_engine_scope(_URL):
            with pytest.raises(TenantTransactionAborted):
                async with tenant_session(ORG) as db:
                    await _fail_and_swallow(db)
                    db.add(row("lost"))
    assert _rows(table) == []


# ── 3: the clean block ─────────────────────────────────────────────────────


@_DB_GATE
async def test_a_clean_block_commits_logs_nothing_and_sends_no_probe(
    table: str, mode: str,
) -> None:
    async with tenant_engine_scope(_URL):
        seen = _statements()
        with capture_logs() as logs:
            async with tenant_session(ORG) as db:
                await db.execute(text(f"INSERT INTO {table} VALUES ('kept')"))
    assert _events(logs) == []
    assert PROBE not in seen, seen
    assert any("set_config" in s for s in seen), seen
    assert _rows(table) == ["kept"]


# ── 4: the savepoint that recovered ───────────────────────────────────────


@_DB_GATE
async def test_a_savepoint_keeps_the_later_write_in_both_modes(
    table: str, mode: str,
) -> None:
    async with tenant_engine_scope(_URL):
        seen = _statements()
        with capture_logs() as logs:
            async with tenant_session(ORG) as db:
                try:
                    async with db.begin_nested():
                        await db.execute(text("SELECT 1 / 0"))
                except DBAPIError:
                    pass
                await db.execute(text(f"INSERT INTO {table} VALUES ('kept')"))
    assert _events(logs) == []
    # The failed statement marked the connection, so the probe ran and passed.
    assert PROBE in seen, seen
    assert _rows(table) == ["kept"]


# ── 5: the next borrower of the pooled connection ─────────────────────────


@_DB_GATE
async def test_the_next_borrower_of_a_marked_connection_starts_clean(
    table: str, mode: str,
) -> None:
    async with tenant_engine_scope(_URL):
        # A session outside the seam fails a statement and gives the
        # connection back to the pool with the mark on it.
        raw = shared.get_session_factory()()
        try:
            conn = await raw.connection()
            info = conn.sync_connection.info
            pid_before = (await raw.execute(text("SELECT pg_backend_pid()"))).scalar()
            await _fail_and_swallow(raw)
        finally:
            await raw.close()
        assert info.get(shared._STMT_ERROR) is True

        seen = _statements()
        with capture_logs() as logs:
            async with tenant_session(ORG) as db:
                pid_after = (await db.execute(text("SELECT pg_backend_pid()"))).scalar()
                await db.execute(text(f"INSERT INTO {table} VALUES ('kept')"))
        # The same pooled connection, and it carried no mark into the session.
        assert pid_after == pid_before
        assert PROBE not in seen, seen
        assert info.get(shared._STMT_ERROR) is None
    assert _events(logs) == []
    assert _rows(table) == ["kept"]


# ── 6: a hermetic fake session ─────────────────────────────────────────────


class _FakeSession:
    """A test double with no transaction, like those of the hermetic suites."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def begin(self) -> None:
        self.calls.append("begin")

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append("execute")

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")

    async def close(self) -> None:
        self.calls.append("close")


async def test_a_fake_session_runs_as_before(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    fake = _FakeSession()
    monkeypatch.setattr(shared, "get_session_factory", lambda: lambda: fake)
    with capture_logs() as logs:
        async with tenant_session(ORG) as db:
            assert db is fake
    assert fake.calls == ["begin", "execute", "commit", "close"]
    assert _events(logs) == []
