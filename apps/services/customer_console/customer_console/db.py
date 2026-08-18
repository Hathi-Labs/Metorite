"""The Customer Console's own database handle.

⚠️ **This is deliberately NOT ``acb_common.db``, and that is not a shortcut.**

``acb_common``'s engine is the *tenant* seam: one engine, bound per request to
one ``organization_id`` via ``set_config('app.tenant_id', …)``, with FORCE ROW
LEVEL SECURITY underneath so a query that forgets its tenant returns zero rows.
Every rule in R5 exists to keep that seam single and unforgettable.

This module connects to a **different database on a different plane**
(``saas_multitenancy.md`` §0.9.2). The Customer Console is *cross-tenant by design*
— it has to answer "how many customers do we have and what do they owe us",
which is the exact question RLS exists to make unanswerable. Routing it through
the tenant seam would either bind a tenant (and return one customer's row where
the operator asked for all of them) or bind none (and return nothing at all).

So R5(b) is satisfied by **declaring** this site rather than by hiding it:
``tests/unit/test_db_engine_seam.py::_ALLOWED_SYNC`` carries an entry naming
this file and this reason, which is exactly the "cited reason a reviewer is
expected to challenge" that R5 asks for.

Isolation for this plane is a **network and credential boundary** — a separate
database, separate credentials, reachable only from the Customer Console service —
not a row predicate. Nothing here holds tenant business data, so the blast
radius of that choice is contracts and usage counts, never customers' content.

Sync engine on purpose: the endpoints are declared ``def`` (not ``async def``),
so FastAPI runs them in its threadpool. A customer console serves operators and
sign-ins, not a token-rate hot path; the async complexity would buy nothing and
cost a second connection idiom.
"""
from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

__all__ = ["control_plane_url", "get_engine"]

#: Deliberately its own variable, not `DATABASE_URL`. Sharing the tenant
#: database's URL is precisely the mistake this module exists to prevent, and an
#: env var that silently falls back to the tenant DSN would make that mistake
#: invisible — the service would start, queries would succeed, and the control
#: plane's tables would quietly be in the customer's database.
_ENV_VAR = "CUSTOMER_CONSOLE_DATABASE_URL"


def control_plane_url() -> str:
    """The Customer Console DSN, or raise.

    Fails closed with a named variable rather than defaulting to localhost: a
    default that happens to work in development is how a misconfigured
    deployment reaches the wrong database and reports itself healthy.
    """
    url = os.environ.get(_ENV_VAR, "").strip()
    if not url:
        raise RuntimeError(
            f"{_ENV_VAR} is not set. The Customer Console uses its OWN database, "
            "separate from the tenant one (customer_console.md §3, "
            "saas_multitenancy.md §0.9.2) — there is deliberately no default."
        )
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """The process-wide Customer Console engine."""
    return create_engine(
        control_plane_url(),
        pool_pre_ping=True,
        future=True,
    )
