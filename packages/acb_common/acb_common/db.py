"""The one shared async database seam for a process (BO-10).

The gateway had **twelve** module-level ``create_async_engine`` call sites, one
per app package, each with its own pool that nobody could size, drain or observe
as a whole. Their ceilings summed to ~165 connections from a single process,
against a stock Postgres ``max_connections`` of 100 that Langfuse, LiteLLM and
the ingestion services also draw from — so the old arrangement could not
actually be spent, it could only fail. This module is the single seam they now
all resolve through: **one engine, one pool, per process.**

It lives in ``acb_common`` rather than in the gateway because ``acb_common`` is
the one package every service and every ``acb_*`` library already depends on.
``acb_auth.access`` resolves permissions from Postgres on the request path and
runs *inside* the gateway process; while the seam lived in ``gateway.db`` it
could not reach it, so the gateway had two pools no matter how many route
packages were converted. Putting the seam here is what makes "one pool" true
rather than approximately true.

Two rules worth keeping:

* ``sqlalchemy.ext.asyncio`` is imported **inside** the functions, not at module
  scope. Every caller does it that way and the import graphs depend on it —
  importing this module must not drag SQLAlchemy's async stack into a process
  that never opens a connection. That is also why importing ``acb_common`` does
  not import this module.
* The engine is created on first use and cached for the process. It is never
  disposed here: the pool's lifetime is the process's, and a ``dispose()`` seam
  would be a way to close connections other request handlers are still using.
  A process that genuinely owns a short-lived engine (the ingestion scheduler's
  per-run engines) should keep building its own and disposing it, not reach for
  this one.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any

from acb_common.settings import get_settings

#: Process-wide, created on first use. Module-level rather than app-state so a
#: background loop or a broker handler with no ``Request`` reaches the same pool.
_ENGINE: Any = None
_SESSION_FACTORY: Any = None


def async_database_url() -> str:
    """The configured database URL, coerced onto the asyncpg driver.

    ``DATABASE_URL`` wins over the setting because LiteLLM's Prisma client needs
    the plain ``postgresql://`` form in the environment, so the two disagree by
    design and the environment is the one deploy sets.
    """
    settings = get_settings()
    db_url = os.environ.get("DATABASE_URL", settings.database_url)
    if "postgresql+psycopg" in db_url:
        db_url = db_url.replace("postgresql+psycopg", "postgresql+asyncpg")
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")
    return _translate_sslmode_for_asyncpg(db_url)


def _translate_sslmode_for_asyncpg(url: str) -> str:
    """Rename a ``sslmode=`` query param to asyncpg's ``ssl=``.

    SQLAlchemy's asyncpg dialect forwards URL query params verbatim as
    ``asyncpg.connect()`` keyword arguments, and asyncpg spells the TLS knob
    ``ssl=`` where libpq spells it ``sslmode=``. Left untranslated, a DSN like
    ``…/db?sslmode=require`` — the exact form managed Postgres providers hand
    out, and one psycopg accepts — makes EVERY async connection raise
    ``TypeError: connect() got an unexpected keyword argument 'sslmode'``
    while ``/health`` (which never touches the DB) stays green. asyncpg
    accepts the libpq mode names as ``ssl`` string values, so the rename is
    lossless. Fence: ``tests/unit/test_dsn.py``.
    """
    if "sslmode=" not in url:
        return url
    scheme, sep, rest = url.partition("://")
    if not sep or "?" not in rest:
        return url
    base, _, query = rest.partition("?")
    parts = [
        ("ssl" + p[len("sslmode"):]) if p.startswith("sslmode=") else p
        for p in query.split("&")
        if p
    ]
    return f"{scheme}://{base}?{'&'.join(parts)}"


#: Ceiling on how long one of OUR sessions may sit `idle in transaction`, in ms.
#:
#: This is a lock-release deadline, not a performance knob. SQLAlchemy's
#: ``AsyncSession`` opens a transaction on first ``execute()`` and holds it until
#: commit/rollback/close, so a handler that reads a row and then awaits a slow
#: network call is `idle in transaction` — holding an ACCESS SHARE lock — for the
#: whole call. That is normal and fine. What is not fine is an await that never
#: returns: on 2026-08-06 a hung LLM call pinned one such transaction for 14h44m,
#: a migration's ``ALTER TABLE`` queued behind its lock, and because Postgres's
#: lock queue is FIFO every later reader of that table queued behind the *waiting*
#: ALTER. Sending mail stopped, and the pool drained behind the blocked readers.
#:
#: ⚠️ MUST stay comfortably above the LLM wall-clock worst case, because the email
#: automation package legitimately awaits completions with a session open.
#: ``acb_llm.client`` bounds one call at 3 attempts x 90s + 6s backoff ≈ 276s. At
#: 600s a genuine retrying completion can never trip this, while a hang is capped
#: at ten minutes instead of unbounded. Raise ``LLM_REQUEST_TIMEOUT_SECS`` and you
#: must raise this too — that coupling is the whole reason both numbers are
#: written down next to their reasoning.
_IDLE_IN_TXN_TIMEOUT_MS = "600000"  # 10 minutes


def engine_connect_args() -> dict[str, Any]:
    """Driver-level connect args every engine from this seam shares.

    Two bounds, both about failing instead of hanging:

    * ``timeout`` — asyncpg's CONNECT-phase ceiling, so a slow or unreachable DB
      fails fast rather than stalling request handlers.
    * ``idle_in_transaction_session_timeout`` — the server-side deadline above.
      Set through asyncpg's ``server_settings`` so it rides the connection's
      startup packet and applies to every session from this pool, with no
      migration and no ``ALTER ROLE``. Scoping it to the app's own connections is
      deliberate: ``pg_dump`` and the migration runner connect as the same role
      and must NOT inherit an app-tuned deadline.
    """
    settings = get_settings()
    args: dict[str, Any] = {
        "timeout": settings.db_connect_timeout,
        "server_settings": {
            "idle_in_transaction_session_timeout": _IDLE_IN_TXN_TIMEOUT_MS,
        },
    }
    # Transaction-mode poolers (PgBouncer / Supabase :6543) break asyncpg's
    # server-side prepared statements; DB_STATEMENT_CACHE_SIZE=0 disables the
    # cache for exactly that topology. None = driver default, no override.
    if settings.db_statement_cache_size is not None:
        args["statement_cache_size"] = settings.db_statement_cache_size
    return args


def get_engine() -> Any:
    """The shared pooled async engine, created on first use."""
    global _ENGINE
    if _ENGINE is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        settings = get_settings()
        _ENGINE = create_async_engine(
            async_database_url(), echo=False, pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=1800,
            connect_args=engine_connect_args(),
        )
    return _ENGINE


def get_session_factory() -> Any:
    """The shared ``async_sessionmaker`` over :func:`get_engine`."""
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        _SESSION_FACTORY = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _SESSION_FACTORY


async def get_db() -> Any:
    """Return a new async session from the shared, pooled engine.

    ⚠️ **Not tenant-bound.** Under D15 (`saas_multitenancy.md` §1) tenant
    isolation is a Postgres RLS policy keyed on the ``app.tenant_id`` setting,
    and this session sets nothing — so once phase-4 policies apply it reads
    **zero rows**, by design (§0.1's fail-closed property). Reach for
    :func:`tenant_session` instead. This entry point stays for the ~200 existing
    call sites, which MT-1c converts; it is not a second sanctioned way in.
    """
    return get_session_factory()()


# ── Tenant binding (MT-1c) ──────────────────────────────────────────────────
#
# `saas_multitenancy.md` §0.1 / §1.3. RLS enforces isolation server-side, but
# only against a session that has told the server which tenant it is acting for.
# This is that seam — and it is the ONE place the binding happens, which is what
# makes the pooled decision defensible across ten connection paths nobody can
# hold in their head (§0.1's inventory was itself wrong twice).

_TENANT: ContextVar[str | None] = ContextVar("acb_tenant", default=None)


class TenantUnbound(RuntimeError):
    """No tenant in context and none supplied. **Never defaulted.**

    A session that cannot say which tenant it acts for must not open. The
    alternative — picking "the usual one" — is how a background job writes one
    customer's data into another customer's account.
    """


def bind_tenant(organization_id: str) -> Token[str | None]:
    """Bind *organization_id* for this async context. Returns a reset token.

    Request handlers bind from the authenticated session; jobs bind from their
    own record (MT-1d). **Never from a header, query parameter or body field** —
    `user_management_contract.md` **R11**.
    """
    return _TENANT.set(str(organization_id))


def clear_tenant() -> Token[str | None]:
    """Open a fresh, empty tenant scope. Returns a reset token.

    The gateway's request middleware calls this at the top of every HTTP
    request and :func:`release_tenant` after the response, so one request can
    never inherit another's binding — whatever the server's task model does
    with context propagation. Inside the scope, the auth dependency's
    :func:`bind_tenant` is what fills it in.
    """
    return _TENANT.set(None)


def release_tenant(token: Token[str | None] | None) -> None:
    """Undo :func:`bind_tenant` / :func:`clear_tenant`. Never raises."""
    if token is None:
        return
    try:
        _TENANT.reset(token)
    except (ValueError, RuntimeError):
        _TENANT.set(None)


def current_tenant() -> str | None:
    """The tenant bound to this context, if any."""
    return _TENANT.get()


@asynccontextmanager
async def tenant_session(organization_id: str | None = None) -> AsyncIterator[Any]:
    """A session bound to a tenant for the life of one transaction.

    ⚠️ **``SET LOCAL``, never ``SET`` — this is the highest-consequence line in
    the whole tenancy migration.** The engine pools connections
    (``pool_size`` + ``max_overflow`` above). A session-scoped ``SET`` survives
    the connection's return to the pool, so the *next* borrower — a different
    request, a different customer — inherits it and reads their data. ``SET
    LOCAL`` is transaction-scoped and resets on commit or rollback.

    ⚠️ **And it needs a real transaction.** ``SET LOCAL`` outside one is a silent
    no-op: Postgres warns, the policy then sees an unset GUC, and every query
    returns nothing. That presents as *"the feature is broken"*, not as
    *"tenancy is broken"*, which is why ``begin()`` is explicit here rather than
    left to SQLAlchemy's autobegin.

    Usage::

        async with tenant_session() as db:          # tenant from context
            rows = await db.execute(text("SELECT ..."))

        async with tenant_session(org_id) as db:    # explicit, e.g. a job
            ...

    Raises:
        TenantUnbound: nothing bound and nothing supplied.
    """
    from sqlalchemy import text

    tenant = organization_id or _TENANT.get()
    if not tenant:
        raise TenantUnbound(
            "no tenant bound — a caller outside a request or job must pass one "
            "explicitly (saas_multitenancy.md §0.1 / MT-1c)"
        )

    session = get_session_factory()()
    try:
        await session.begin()
        # ``set_config(..., is_local := true)`` IS ``SET LOCAL`` — same
        # transaction-scoped reset on commit/rollback — but it is a function
        # call, so the tenant can be a BOUND parameter. The literal
        # ``SET LOCAL app.tenant_id = :tenant`` form is a Postgres syntax
        # error through the extended protocol (``SET`` takes no bind
        # parameters), which every hermetic test missed because a Python fake
        # does not parse SQL; the first live run found it (2026-08-10, the
        # same lesson as WS-27r's CAST). Interpolating the id into the
        # statement instead would be the injection seam this module exists to
        # avoid handing out.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
