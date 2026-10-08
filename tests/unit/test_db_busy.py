"""A refused database connection is a 503 and "busy", never a member's 500.

Measured on the box on 2026-10-05 and 2026-10-06: about 590 refusals in two
days from Supabase's session pooler::

    asyncpg.exceptions.InternalServerError: (EMAXCONNSESSION) max clients
    reached in session mode - max clients are limited to pool_size: 15

Each one reached a member as "The server had an error (500). Nothing was
saved." This file holds the four claims that change that:

1. `is_db_unavailable` recognises a refused or timed-out CONNECT, through
   SQLAlchemy's wrapping, and nothing else. A query that fails on a live
   connection, or an HTTP client that cannot reach another service, stays
   what it was.
2. The gateway answers such a failure with 503, `Retry-After` and the code
   `db_busy`, and never echoes the driver's message (it names the pooler).
3. Every other exception keeps the old answer: a bare 500.
4. `/health` says `db: "busy"` for a moment afterwards, from memory, so the
   shell's update notice can say "Metorite is busy" without a database call.

Spec: `project-docs/specs/navigation_shell.md` §7.3.
"""

from __future__ import annotations

import asyncpg
import httpx
import pytest
import sqlalchemy.exc as sa_exc
from acb_common import db_busy
from fastapi import FastAPI
from fastapi.testclient import TestClient

REFUSAL = (
    "(EMAXCONNSESSION) max clients reached in session mode - max clients are"
    " limited to pool_size: 15"
)


def _wrapped(orig: BaseException) -> sa_exc.DBAPIError:
    """What SQLAlchemy raises around a driver error."""
    return sa_exc.DBAPIError.instance(
        "SELECT 1", {}, orig, Exception, connection_invalidated=False
    )


@pytest.fixture(autouse=True)
def _clean() -> None:
    db_busy.reset()
    yield
    db_busy.reset()


class TestWhatCountsAsUnavailable:
    def test_the_pooler_refusal_measured_in_production(self) -> None:
        orig = asyncpg.exceptions.InternalServerError(REFUSAL)
        assert db_busy.is_db_unavailable(orig)
        assert db_busy.is_db_unavailable(_wrapped(orig))

    def test_a_refusal_raised_from_inside_another_error(self) -> None:
        orig = asyncpg.exceptions.InternalServerError(REFUSAL)
        try:
            try:
                raise orig
            except Exception as inner:
                raise RuntimeError("session failed") from inner
        except RuntimeError as outer:
            assert db_busy.is_db_unavailable(outer)

    def test_the_pool_queue_running_out_of_time(self) -> None:
        exc = sa_exc.TimeoutError(
            "QueuePool limit of size 8 overflow 2 reached, connection timed out"
        )
        assert db_busy.is_db_unavailable(exc)

    def test_a_database_that_refuses_the_socket(self) -> None:
        orig = asyncpg.exceptions.CannotConnectNowError("the database system is starting up")
        assert db_busy.is_db_unavailable(_wrapped(orig))

    def test_a_real_query_error_stays_a_real_error(self) -> None:
        orig = asyncpg.exceptions.UndefinedTableError('relation "x" does not exist')
        assert not db_busy.is_db_unavailable(_wrapped(orig))
        assert not db_busy.is_db_unavailable(ValueError("bad input"))

    def test_another_service_refusing_is_not_the_database(self) -> None:
        # The same words from an HTTP client name another service, not ours.
        assert not db_busy.is_db_unavailable(httpx.ConnectError("connection refused"))
        assert not db_busy.is_db_unavailable(ConnectionRefusedError("connection refused"))

    def test_a_marker_in_the_parameters_is_not_a_refusal(self) -> None:
        # Review P2 (2026-10-06): `str(StatementError)` holds the bound
        # parameters. A task or an integration error can contain any text.
        orig = asyncpg.exceptions.UniqueViolationError("duplicate key value")
        wrapped = sa_exc.DBAPIError.instance(
            "INSERT INTO t VALUES (:e)",
            {"e": "Connection refused by host"},
            orig,
            Exception,
        )
        assert not db_busy.is_db_unavailable(wrapped)

    def test_a_marker_in_the_sql_text_is_not_a_refusal(self) -> None:
        orig = asyncpg.exceptions.UndefinedColumnError('column "x" does not exist')
        wrapped = sa_exc.DBAPIError.instance(
            "SELECT 'too many clients' AS note, x FROM t", {}, orig, Exception
        )
        assert not db_busy.is_db_unavailable(wrapped)

    def test_a_connection_sqlstate_counts_whatever_the_words(self) -> None:
        orig = asyncpg.exceptions.TooManyConnectionsError("sorry")
        assert db_busy.is_db_unavailable(_wrapped(orig))

    def test_a_bug_raised_while_handling_a_refusal_stays_a_bug(self) -> None:
        # `__context__` is not followed: this is a KeyError, not a refusal.
        try:
            try:
                raise asyncpg.exceptions.InternalServerError(REFUSAL)
            except Exception:
                {}["rows"]
        except KeyError as bug:
            assert bug.__context__ is not None
            assert not db_busy.is_db_unavailable(bug)


class TestTheBusyWindow:
    def test_busy_for_the_window_then_clear(self) -> None:
        assert not db_busy.recently_busy(now=100.0)
        db_busy.mark(now=100.0)
        assert db_busy.recently_busy(now=100.0 + db_busy.BUSY_WINDOW_S - 0.1)
        assert not db_busy.recently_busy(now=100.0 + db_busy.BUSY_WINDOW_S + 0.1)


def _app() -> FastAPI:
    """The gateway's handler, mounted on a bare app (the TenantUnbound idiom)."""
    from gateway.main import _db_unavailable

    app = FastAPI()
    app.add_exception_handler(Exception, _db_unavailable)

    @app.get("/refused")
    def refused() -> dict:
        raise _wrapped(asyncpg.exceptions.InternalServerError(REFUSAL))

    @app.get("/bug")
    def bug() -> dict:
        raise KeyError("rows")

    return app


class TestTheGatewayAnswer:
    def test_a_refused_connection_is_a_503_that_names_no_pooler(self) -> None:
        res = TestClient(_app(), raise_server_exceptions=False).get("/refused")
        assert res.status_code == 503
        assert res.headers["retry-after"] == "3"
        body = res.json()
        assert body["code"] == "db_busy"
        for leak in ("EMAXCONNSESSION", "pool_size", "pooler", "supabase"):
            assert leak.lower() not in body["detail"].lower(), leak
        assert db_busy.recently_busy()

    def test_any_other_error_keeps_the_bare_500(self) -> None:
        res = TestClient(_app(), raise_server_exceptions=False).get("/bug")
        assert res.status_code == 500
        assert res.text == "Internal Server Error"
        assert not db_busy.recently_busy()

    def test_health_says_busy_after_a_refusal_with_no_database_call(self, monkeypatch) -> None:
        monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", "test-internal-token")
        from gateway.main import app

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/health").json()["db"] == "ok"
        db_busy.mark()
        assert client.get("/health").json()["db"] == "busy"

    def test_the_gateway_registers_the_handler(self) -> None:
        from gateway.main import _db_unavailable, app

        assert app.exception_handlers.get(Exception) is _db_unavailable
