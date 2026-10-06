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
_CLASS_NAMES = frozenset({
    "TooManyConnectionsError",  # asyncpg
    "CannotConnectNowError",  # asyncpg
    "ConnectionDoesNotExistError",  # asyncpg, the server dropped it
    "PoolTimeout",  # psycopg_pool
})


def _chain(exc: BaseException | None, limit: int = 8):
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen and len(seen) < limit:
        seen.add(id(exc))
        yield exc
        orig = getattr(exc, "orig", None)
        nxt = orig if isinstance(orig, BaseException) else None
        exc = nxt or exc.__cause__ or exc.__context__


#: Only an exception raised by a database library counts. "Connection
#: refused" from an HTTP client is another service, not the database.
_DB_MODULES = ("sqlalchemy", "asyncpg", "psycopg", "psycopg_pool")


def _from_db_library(e: BaseException) -> bool:
    return type(e).__module__.split(".", 1)[0] in _DB_MODULES


def is_db_unavailable(exc: BaseException) -> bool:
    """True when ``exc`` means "no database connection could be had"."""
    for e in _chain(exc):
        if not _from_db_library(e):
            continue
        name = type(e).__name__
        if name in _CLASS_NAMES:
            return True
        # SQLAlchemy's own pool queue ran out of time.
        if name == "TimeoutError" and type(e).__module__.startswith("sqlalchemy"):
            return True
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
