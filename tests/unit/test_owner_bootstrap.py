"""The ownership bootstrap always leaves a way back in.

Born from the 2026-07-30 production lockout: `app_user` was empty, so
migration 128's promote-an-existing-row bootstrap silently did nothing, and
the invite-only model had zero members, zero owners — **no inviter**. Every
authenticated sign-in resolved unprovisioned; the only fix was hand-run SQL.

`ensure_owner_bootstrap` closes that loop at gateway startup, the first
place the database AND the environment are both readable. The properties
that matter, asserted against a live database:

1. **Empty deployment → EXECUTIVE_EMAILS becomes the owner**, row created,
   active, owner role attached.
2. **Any existing owner makes it a no-op forever** — a stale or placeholder
   EXECUTIVE_EMAILS can never overwrite real membership.
3. **No candidate → warn, don't crash.** An ownerless deployment with a
   broken bootstrap must still boot and serve /health.
4. **Unprovisioned sign-ins are visible and cached** — the refusal is
   logged (once per TTL, not per request) and negative results stop
   re-querying the DB on every call.

⚠️ **RE-GATED 2026-08-19 by WS-29 MT-1j slice 2, and the reason is a measured
failure, not tidiness.** This suite used to gate on ``DATABASE_URL``
reachability through ``acb_graph.get_session``. **No CI job sets that
variable** — pr-check.yml refuses to, deliberately, because it would arm
``test_tenant_coverage.py``'s two DB-gated tests, which fail by construction
against a freshly-replayed ladder. So all five tests skipped in **every run
ever made**, and that is exactly how slice 6's defect reached main: the two
``app_user`` upserts named ``ON CONFLICT (email)`` after migration 162 dropped
the byte-exact constraint, ``ensure_owner_bootstrap()`` swallowed the resulting
42P10, and an ownerless box logged ``ownership_bootstrap_failed`` and served on
with no owner and no inviter. *A fence that never executes is not a fence.*

Two changes make it run, and MT-1j slice 2 argues both in
``saas_multitenancy.md`` §11 as a recorded deviation from its own "green
without edit" clause:

* the gate is now ``TENANT_LADDER_DATABASE_URL`` — the same variable
  ``test_app_user_upserts.py`` and ``test_billing_purchase_capability.py``
  answer to, and the one pr-check.yml *does* set — and the schema is built by
  ``_tenant_ladder.apply_ladder`` rather than assumed; and
* the fixture's own ``INSERT … ON CONFLICT (email) DO NOTHING`` at what was
  ``:144`` is repaired to ``(lower(email))``. That statement is a **test
  fixture**, not the pinned production surface: the "without edit" clause
  exists to stop the bootstrap's semantics drifting, and a fixture that raises
  42P10 in setup pins nothing at all. Measured before the repair, pointed at a
  real ladder: **4 passed, 1 failed**.

Nothing about ``ensure_owner_bootstrap()`` itself changed, and D36.3 is intact:
``_BOOTSTRAP_ORG_SLUG`` still names ``default`` and is still the fresh-box
first-run path. A *provisioned* organization gets its owner from
``provision_organization()`` instead — fenced in ``test_org_provisioning.py``.

Run::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant
    uv run pytest tests/unit/test_owner_bootstrap.py -v -rs
"""
from __future__ import annotations

import os

import pytest

pytest.importorskip("sqlalchemy")

#: The organization ``ensure_owner_bootstrap()`` provisions into. Imported
#: rather than transcribed so a re-point (which D36.3 forbids) is visible here
#: too, and so every query below asks about the SAME organization the code
#: under test does — MT-1i's whole finding was a guard and an insert that
#: disagreed about which one they meant.
from acb_auth.access import _BOOTSTRAP_ORG_SLUG
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import apply_ladder

#: The launch snapshot (``tests/conftest.py``), not the live variable:
#: ``import litellm`` calls ``load_dotenv()`` mid-collection, and a dev
#: machine's ``.env`` must not be able to aim an R8 gate at a local database.
_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

#: The async half of the suite drives the SAME driver production drives.
#: ``acb_common.db.async_database_url():59-60`` rewrites ``postgresql+psycopg``
#: to ``postgresql+asyncpg`` for every async engine the platform opens, so
#: mirroring the rewrite here is fidelity, not a workaround — the sync ladder
#: replay stays on psycopg because that is what ``_tenant_ladder`` needs.
#:
#: ⚠️ It is also the only thing that works on the primary dev box: psycopg's
#: async mode refuses Windows' default ``ProactorEventLoop``
#: (``psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'``),
#: which is the loop ``pytest-asyncio`` hands every test there. Measured
#: 2026-08-19 while re-gating this suite.
_ASYNC_URL = _URL.replace("postgresql+psycopg", "postgresql+asyncpg")

_needs_db = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see this module's docstring."
    ),
)

_OWNER = "pytest-bootstrap-owner@fracktal.in"
_PREEXISTING = "pytest-bootstrap-existing@fracktal.in"


@pytest.fixture(scope="module")
def replayed():
    """Build the tenant schema from ``infra/postgres/``, then replay it."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def _exec(engine, sql: str, **params):
    with engine.begin() as conn:
        result = conn.execute(text(sql), params)
        try:
            return result.fetchall()
        except Exception:
            return []


def _purge(engine) -> None:
    _exec(engine, "DELETE FROM app_user WHERE email LIKE 'pytest-bootstrap-%'")


def _owner_emails(engine) -> set[str]:
    """Owners **of the bootstrap organization**, not of the deployment.

    Scoped for the reason ``_HAS_OWNER_SQL``'s own comment records (MT-1i):
    ``org_role`` is per-organization, so an unscoped ``r.slug = 'owner'``
    answers a question nobody asked — and once MT-1j can create a second
    organization, an owner over there would make these assertions lie about
    this one.
    """
    return {
        r[0]
        for r in _exec(
            engine,
            "SELECT u.email FROM user_role ur "
            "JOIN org_role r ON r.id = ur.role_id AND r.slug = 'owner' "
            "JOIN organization o ON o.id = r.organization_id AND o.slug = :s "
            "JOIN app_user u ON u.id = ur.user_id",
            s=_BOOTSTRAP_ORG_SLUG,
        )
    }


@pytest.fixture
def ownerless(monkeypatch, replayed):
    """A deployment with no owner at all, and a session factory bound to the
    ladder database rather than to ``DATABASE_URL``.

    ``acb_auth.access`` imports ``get_session_factory`` at module load, so the
    substitution happens on the importing module's name — that is the single
    seam all five of its query sites go through. The engine is built lazily
    inside the running event loop and uses ``NullPool``: ``pytest-asyncio``
    gives each test a fresh loop, and a pooled async connection bound to a
    closed one is the "attached to a different loop" failure the previous
    fixture worked around by resetting ``acb_common.db``'s globals.
    """
    import acb_auth.access as access_mod
    import acb_common.db as db_mod
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _purge(replayed)
    # Detach every owner-role assignment, remembering enough to restore.
    saved = _exec(
        replayed,
        "SELECT ur.user_id::text, ur.role_id::text, ur.assigned_by "
        "FROM user_role ur JOIN org_role r ON r.id = ur.role_id "
        "WHERE r.slug = 'owner'",
    )
    _exec(
        replayed,
        "DELETE FROM user_role ur USING org_role r "
        "WHERE r.id = ur.role_id AND r.slug = 'owner'",
    )
    access_mod.invalidate()
    monkeypatch.setattr(access_mod, "CACHE_TTL_SECONDS", 0.0)

    # ⚠️ ``_tables_missing`` is a process-global LATCH: the first resolve that
    # fails with "does not exist" sets it permanently (access.py:298-299) and
    # every later call short-circuits to ``_degraded()`` without touching a
    # database. Any earlier test in a directory run that resolves against a
    # schema-less database arms it, and then ``resolve_access`` here returns
    # ``is_active=False`` for an owner that was just successfully bootstrapped
    # — a failure that points at the bootstrap and is nothing to do with it.
    #
    # Found 2026-08-19 by MT-1j slice 2, and it could not have been found
    # earlier: this suite skipped in every directory run ever made, so it had
    # never once been exposed to another module's leftovers. Same class of
    # reset as the old fixture's ``acb_common.db._ENGINE`` clearing, one module
    # over.
    monkeypatch.setattr(access_mod, "_tables_missing", False)

    built: list = []

    def _ladder_session_factory():
        if not built:
            built.append(
                create_async_engine(_ASYNC_URL, poolclass=NullPool, future=True)
            )
        return async_sessionmaker(built[0], expire_on_commit=False)

    monkeypatch.setattr(access_mod, "_get_session_factory", _ladder_session_factory)
    # ⚠️ H6 RLS-bind hardening (WS-29): `ensure_owner_bootstrap` now runs the
    # has-owner read + the bootstrap INSERT inside `tenant_session(default_org)`
    # (both touch the FORCE-RLS'd `app_user`/`user_role`). `tenant_session` opens
    # its session through `acb_common.db.get_session_factory` — the ORIGINAL
    # module-global, NOT the `access_mod._get_session_factory` name substituted
    # above (that alias is a separate binding). So the same lazy ladder factory
    # must be pinned on the real seam too, or the bound statements would open
    # against the default engine (`DATABASE_URL`) instead of the ladder DB and
    # the bootstrap would silently no-op. Same lazy build, same cached engine,
    # so still one engine, disposed in teardown.
    monkeypatch.setattr(db_mod, "get_session_factory", _ladder_session_factory)

    yield replayed

    for engine in built:
        engine.sync_engine.dispose()
    _purge(replayed)
    for user_id, role_id, assigned_by in saved:
        _exec(
            replayed,
            "INSERT INTO user_role (user_id, role_id, assigned_by) "
            "VALUES (CAST(:u AS uuid), CAST(:r AS uuid), :b) "
            "ON CONFLICT DO NOTHING",
            u=user_id, r=role_id, b=assigned_by,
        )
    access_mod.invalidate()


@_needs_db
@pytest.mark.asyncio
async def test_empty_deployment_bootstraps_the_owner(
    ownerless, monkeypatch,
) -> None:
    from acb_auth import ensure_owner_bootstrap, resolve_access

    monkeypatch.setenv("EXECUTIVE_EMAILS", f"{_OWNER}, second@fracktal.in")

    assert await ensure_owner_bootstrap() == _OWNER
    assert _OWNER in _owner_emails(ownerless)

    access = await resolve_access(_OWNER, use_cache=False)
    assert access.is_active
    assert access.has("admin:members:invite")  # the way back in: can invite


@_needs_db
@pytest.mark.asyncio
async def test_an_existing_owner_makes_it_a_noop(ownerless, monkeypatch) -> None:
    """A placeholder EXECUTIVE_EMAILS must never overwrite real membership."""
    from acb_auth import ensure_owner_bootstrap

    # ⚠️ `(lower(email))`, and the organization named rather than `LIMIT 1`.
    # This statement is where the suite died against a real ladder: migration
    # 162 dropped `app_user_email_key`, so the bare target raised 42P10 at PLAN
    # time (MT-1j slice 2's recorded deviation). The `LIMIT 1` was the same
    # class of latent defect one table over — it happened to pick the right
    # organization only while there was exactly one, and the whole point of the
    # ticket that repaired this line is that there will not be.
    _exec(
        ownerless,
        "INSERT INTO app_user (email, display_name, role, status, "
        "organization_id) "
        "SELECT :e, :e, 'executive', 'active', o.id FROM organization o "
        "WHERE o.slug = :s "
        "ON CONFLICT (lower(email)) DO NOTHING",
        e=_PREEXISTING, s=_BOOTSTRAP_ORG_SLUG,
    )
    _exec(
        ownerless,
        "INSERT INTO user_role (user_id, role_id, assigned_by) "
        "SELECT u.id, r.id, 'pytest' FROM app_user u, org_role r "
        "JOIN organization o ON o.id = r.organization_id AND o.slug = :s "
        "WHERE u.email = :e AND r.slug = 'owner' ON CONFLICT DO NOTHING",
        e=_PREEXISTING, s=_BOOTSTRAP_ORG_SLUG,
    )

    monkeypatch.setenv("EXECUTIVE_EMAILS", "ceo@fracktal.in")
    assert await ensure_owner_bootstrap() is None
    assert "ceo@fracktal.in" not in _owner_emails(ownerless)


@_needs_db
@pytest.mark.asyncio
async def test_no_candidate_warns_and_does_nothing(ownerless, monkeypatch) -> None:
    from acb_auth import ensure_owner_bootstrap

    monkeypatch.setenv("EXECUTIVE_EMAILS", "not-an-address")
    assert await ensure_owner_bootstrap() is None
    assert _owner_emails(ownerless) == set()


@_needs_db
@pytest.mark.asyncio
async def test_rerunning_after_bootstrap_is_inert(ownerless, monkeypatch) -> None:
    """Startup runs this every boot — the second boot must change nothing."""
    from acb_auth import ensure_owner_bootstrap

    monkeypatch.setenv("EXECUTIVE_EMAILS", _OWNER)
    assert await ensure_owner_bootstrap() == _OWNER
    assert await ensure_owner_bootstrap() is None
    assert _owner_emails(ownerless) == {_OWNER}


@_needs_db
@pytest.mark.asyncio
async def test_unprovisioned_signin_is_cached(ownerless) -> None:
    """The refusal caches like every other resolution, so the DB is not
    re-queried per request and the warning fires once per TTL."""
    import acb_auth.access as access_mod

    access_mod.CACHE_TTL_SECONDS = 60.0
    ghost = "pytest-bootstrap-ghost@fracktal.in"
    first = await access_mod.resolve_access(ghost)
    assert not first.is_active
    assert access_mod._cache_get(ghost) is not None


@_needs_db
def test_the_bootstrap_org_is_still_the_default_one(ownerless) -> None:
    """D36.3, asserted from the side that would notice a re-point first.

    If somebody "fixed" org #2's bootstrap by aiming the constant at a
    customer, every assertion above would keep passing against the wrong
    organization and this suite would certify a first-party bypass.
    """
    assert _BOOTSTRAP_ORG_SLUG == "default"


def test_the_ladder_dsn_does_not_leak_out_of_this_suite() -> None:
    """``DATABASE_URL`` must be untouched by this suite.

    It never sets it — the session factory is substituted on
    ``acb_auth.access`` instead — and if a later edit takes the shortcut,
    ``test_tenant_coverage.py``'s two DB-gated tests re-arm and fail by
    construction, which would read as this suite's fault.
    """
    assert not _URL or os.environ.get("DATABASE_URL", "") != _URL


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """It was absent for its whole life, and skipped for its whole life.

    Structural, so it runs with no database — the property being pinned is
    that CI *notices* when this suite stops executing.
    """
    from pathlib import Path

    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/pr-check.yml"
    ).read_text(encoding="utf-8")
    assert "tests/unit/test_owner_bootstrap.py" in workflow
