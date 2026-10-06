"""Is the database out of reach right now? One answer, for the whole process.

Measured on production, 2026-10-05 and 2026-10-06: about 590 refusals in two
days from Supabase's pooler, in bursts, during deploys and at busy moments::

    asyncpg.exceptions.InternalServerError: (EMAXCONNSESSION) max clients
    reached in session mode - max clients are limited to pool_size: 15

Each one surfaced to a member as "The server had an error (500). Nothing was
saved." A refused CONNECTION is not a bug in the route. It is the service
being briefly unavailable, and the honest answer is 503 with a short
`Retry-After`. Spec: `project-docs/specs/navigation_shell.md` §7.3.

Two parts:

* :func:`is_db_unavailable` decides whether an exception is that kind of
  failure. It walks the chain of causes, because SQLAlchemy wraps the
  driver's error.
* :func:`mark` and :func:`recently_busy` hold one timestamp in memory, so
  ``GET /health`` can say "the database was busy a moment ago" with NO
  database call. A probe that opened a connection would add load at the
  one moment there is none to spare.
"""

from __future__ import annotations

import time

#: How long one refusal keeps `/health` saying "busy". Long enough for the
#: shell's probe (every 3 s) to see it, short enough to clear on its own.
BUSY_WINDOW_S = 15.0

_busy_until = 0.0

# Lower-case fragments of the messages a refused or failed CONNECT carries.
# Only connect-time failures are here: a query that fails on a live
# connection is a real error, and it stays a 500.
_MARKERS = (
    "emaxconnsession",
    "max clients reached",
    "too many connections",
    "too many clients",
    "remaining connection slots are reserved",
    "the database system is starting up",
    "the database system is shutting down",
    "the database system is in recovery mode",
    "connection refused",
    "could not connect to server",
    "connection to server at",
    "queuepool limit of size",
)

# Exception class names that mean "could not get a connection", whatever the
# message says. Matched by name, so this module imports no driver.
# `ConnectionDoesNotExistError` is the one exception to "connect-time only":
# the server dropped a live connection, which is retryable, and a 503 is more
# honest than a 500 that says "nothing was saved".
_CLASS_NAMES = frozenset({
    "TooManyConnectionsError",  # asyncpg
    "CannotConnectNowError",  # asyncpg
    "ConnectionDoesNotExistError",  # asyncpg, the server dropped it
    "PoolTimeout",  # psycopg_pool
})

# SQLSTATEs that mean "no connection": class 08 (connection exception),
# too_many_connections, and the three shutdown and startup states.
_SQLSTATES = frozenset({"53300", "57P01", "57P02", "57P03"})

# A driver message is read for a marker ONLY when its SQLSTATE allows it:
# none, internal error (XX, which is how Supavisor's EMAXCONNSESSION arrives)
# or a connection class. A unique violation, whose psycopg message can carry
# the member's own values in DETAIL, is never read.
_TEXT_OK_PREFIXES = ("XX", "08", "53", "57")


def _chain(exc: BaseException | None, limit: int = 8):
    """The exception, its driver error, and what it was raised FROM.

    ⚠️ Not `__context__`. A bug raised inside an `except` block that handled
    a refusal has the refusal as its context, and it must stay a 500.
    """
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen and len(seen) < limit:
        seen.add(id(exc))
        yield exc
        orig = getattr(exc, "orig", None)
        nxt = orig if isinstance(orig, BaseException) else None
        exc = nxt or exc.__cause__


#: Only an exception raised by a database library counts. "Connection
#: refused" from an HTTP client is another service, not the database.
_DB_MODULES = ("sqlalchemy", "asyncpg", "psycopg", "psycopg_pool")


def _library(e: BaseException) -> str:
    return type(e).__module__.split(".", 1)[0]


def _sqlstate(e: BaseException) -> str | None:
    state = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
    return state if isinstance(state, str) else None


def is_db_unavailable(exc: BaseException) -> bool:
    """True when ``exc`` means "no database connection could be had"."""
    for e in _chain(exc):
        lib = _library(e)
        if lib not in _DB_MODULES:
            continue
        name = type(e).__name__
        if lib == "sqlalchemy":
            # SQLAlchemy's own pool queue ran out of time.
            if name == "TimeoutError":
                return True
            # ⚠️ Never read SQLAlchemy's message. `str(StatementError)` holds
            # the SQL and the bound parameters, which can hold any text a
            # member typed. Follow `.orig` to the driver's error instead.
            continue
        if name in _CLASS_NAMES:
            return True
        state = _sqlstate(e)
        if state is not None and (state.startswith("08") or state in _SQLSTATES):
            return True
        if state is not None and not state.startswith(_TEXT_OK_PREFIXES):
            continue
        text = str(e).lower()
        if any(m in text for m in _MARKERS):
            return True
    return False


def mark(now: float | None = None) -> None:
    """Record that the database refused a connection just now."""
    global _busy_until
    _busy_until = (time.monotonic() if now is None else now) + BUSY_WINDOW_S


def recently_busy(now: float | None = None) -> bool:
    """Did the database refuse a connection in the last :data:`BUSY_WINDOW_S`?"""
    return (time.monotonic() if now is None else now) < _busy_until


def reset() -> None:
    """For tests only."""
    global _busy_until
    _busy_until = 0.0
