"""SQLAlchemy engine + session factory. Schema lives in infra/postgres/01_schema.sql."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

# The SAME exception the async seam raises — imported, never re-declared, so a
# caller can `except TenantUnbound` across both engines and a fence can assert
# the two are one type (root CLAUDE.md §5: no second grant/scoping vocabulary).
from acb_common import get_settings
from acb_common.db import TenantUnbound
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["TenantUnbound", "get_engine", "get_session", "tenant_session"]


def _engine_kwargs(settings) -> dict:
    """Engine kwargs, with a libpq connect_timeout for Postgres URLs.

    Bounding the CONNECT phase means a slow/firewalled DB host can never hang a
    caller indefinitely (e.g. a best-effort ``acb_audit.record`` write) — it
    fails fast and the caller's error handling takes over. ``connect_timeout``
    is a libpq/psycopg param, so it is only applied to Postgres URLs; sqlite or
    other dialects used in tests are left untouched.
    """
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if settings.database_url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": settings.db_connect_timeout}
    return kwargs


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.database_url, **_engine_kwargs(settings))


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session; commit on success, rollback on error.

    ⚠️ **Unbound — binds NO ``app.tenant_id``.** Kept for genuinely RLS-exempt
    reads (tenant discovery, the exempt shadow/organization tables) and the
    best-effort audit path until each specific caller is converted. Once the
    phase-4 policies apply, a session opened here reads ZERO rows / refuses
    writes on any FORCE-RLS'd tenant table. A caller that touches tenant data
    must move to :func:`tenant_session`.
    """
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def tenant_session(organization_id: str | None = None) -> Iterator[Session]:
    """A **sync** session bound to a tenant for the life of one transaction.

    The synchronous twin of :func:`acb_common.db.tenant_session` for the
    entity-graph engine (``saas_multitenancy.md`` §0.1 path 4 / MT-1c). It runs
    the **identical** GUC bind, with the ``:tenant`` id as a BOUND parameter, so
    the acb_graph write paths can be RLS-bound with the same discipline the async
    seam uses — one GUC name, one statement, one
    :class:`~acb_common.db.TenantUnbound`, no parallel doctrine.

    ⚠️ **``set_config(..., is_local := true)`` IS ``SET LOCAL``** — transaction-
    scoped, reset on commit/rollback, so it never survives the connection's
    return to the pool for the next borrower to inherit. And it needs a real
    transaction: ``SET LOCAL`` outside one is a silent no-op, after which the
    policy sees an unset GUC and every query returns nothing — which presents as
    *"the feature is broken"*, not *"tenancy is broken"*. That is why
    ``session.begin()`` is explicit here rather than left to SQLAlchemy's
    autobegin. The literal ``SET LOCAL app.tenant_id = <id>`` form is a Postgres
    syntax error through the extended protocol (``SET`` takes no bind
    parameters); interpolating the id into the statement instead would be the
    injection seam this function exists to avoid.

    ⚠️ **Explicit tenant only — no ambient inheritance.** Unlike the async seam,
    this one does NOT fall back to a request-scoped ContextVar: the sync engine
    serves background/service paths (the audit write, orchestrator agent runs)
    that must NOT inherit whatever tenant happened to be bound upstream (H4). A
    caller passes the tenant it resolved, or this refuses.

    Usage::

        with tenant_session(org_id) as db:      # explicit, e.g. a job
            db.execute(text("SELECT ..."))

    Raises:
        TenantUnbound: no ``organization_id`` supplied.
    """
    from sqlalchemy import text

    if not organization_id:
        raise TenantUnbound(
            "no tenant supplied — the acb_graph sync engine binds only an "
            "EXPLICIT organization_id, never an ambient one; a caller outside a "
            "request or job must pass one explicitly "
            "(saas_multitenancy.md §0.1 / MT-1c)"
        )

    session = _session_factory()()
    try:
        session.begin()
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant, true)"),
            {"tenant": str(organization_id)},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
