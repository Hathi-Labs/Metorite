"""WS-29 — the acb_graph **sync** ``tenant_session`` seam (H4 foundation).

R7 fences for the slice-1 seam that gives the entity-graph sync engine the same
tenant binding the async seam already has (``saas_multitenancy.md`` §0.1 path 4 /
MT-1c). Two R7 names:

  * ``acb-graph-sync-bind-sets-guc`` — R8, against the REAL phase-4-promoted
    two-org catalog as the non-privileged ``acb_app_h3rls`` role (reusing
    ``test_h3_rls_promotion_rehearsal``'s ``promoted`` fixture verbatim — no
    second catalog builder, root ``CLAUDE.md`` §5). A write through
    ``acb_graph.tenant_session(orgA)`` lands and is visible ONLY when bound to
    org A; the same read via the plain unbound ``get_session()`` sees zero rows
    and the same write is refused; ``tenant_session()`` with no org RAISES
    ``TenantUnbound``. If the ``set_config`` bind is deleted from
    ``tenant_session``, the org-A INSERT's WITH CHECK compares against the unset
    GUC (NULL) and is refused → the bound-write assertion goes RED.

  * ``acb-graph-cross-engine-bind-drift`` — a source ratchet (grep) that BOTH
    ``acb_common/db.py`` and ``acb_graph/db.py`` bind via the identical
    ``set_config('app.tenant_id', :tenant, true)`` statement, and that acb_graph
    IMPORTS the shared ``TenantUnbound`` rather than re-declaring one. Removing
    the bind from either engine, or forking a second exception type, goes RED.

⚠️ **R8, real Postgres only.** A hermetic fake agrees with any SQL it is handed,
so the bind "proof" would prove nothing against one — the whole point is that
FORCE ROW LEVEL SECURITY behaves as the generated policies claim. The DB tests
skip (not pass) without ``TENANT_LADDER_DATABASE_URL`` (``_DB_GATE``).

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_acb_graph_tenant_seam.py -v -rs
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

import acb_common.db as common_db
import acb_graph.db as graph_db
from acb_common.db import TenantUnbound
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# The two-org phase-4 promotion fixture + the R8 gate, reused verbatim. Importing
# the fixtures binds them into this module's namespace so pytest resolves them by
# name; ``promoted`` builds its own dedicated ``<db>_h3rls`` scratch catalog and
# the non-privileged ``acb_app_h3rls`` role, and tears it down after.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_COMMON_DB_SRC = _ROOT / "packages" / "acb_common" / "acb_common" / "db.py"
_GRAPH_DB_SRC = _ROOT / "packages" / "acb_graph" / "acb_graph" / "db.py"

#: The ONE binding statement both engines must run, character for character.
#: The ``SELECT `` prefix is the CODE form (``text("SELECT set_config(...)")``),
#: which neither docstring quotes — so this grep goes RED when the runtime bind
#: is deleted, not merely when the prose is, keeping the ratchet non-vacuous.
_BIND_STMT = "SELECT set_config('app.tenant_id', :tenant, true)"


# ==========================================================================
# ``acb-graph-cross-engine-bind-drift`` — source ratchet, no DB needed.
# ==========================================================================
def test_no_org_raises_tenant_unbound() -> None:
    """Fail-closed: no resolvable org → ``TenantUnbound`` at ``__enter__``,
    before any session is opened. It never defaults and never reads an ambient
    value — the H4 rule the sync engine exists to keep."""
    for bad in (None, ""):
        with pytest.raises(TenantUnbound), graph_db.tenant_session(bad):
            pass
    # the default argument is the same fail-closed path
    with pytest.raises(TenantUnbound), graph_db.tenant_session():
        pass


def test_both_engines_bind_via_identical_set_config() -> None:
    """Both the async and the sync seam run the IDENTICAL GUC bind statement.

    Grepped from source (spec's method): a divergence — a different GUC name, a
    ``SET`` literal, a dropped ``, true`` — is the exact drift that ships a seam
    which no-ops under RLS. Deleting the bind from either engine goes RED here.
    """
    common_src = _COMMON_DB_SRC.read_text(encoding="utf-8")
    graph_src = _GRAPH_DB_SRC.read_text(encoding="utf-8")
    assert _BIND_STMT in common_src, (
        "acb_common.db no longer binds via "
        f"{_BIND_STMT!r} — the async seam drifted"
    )
    assert _BIND_STMT in graph_src, (
        "acb_graph.db no longer binds via "
        f"{_BIND_STMT!r} — the sync seam drifted or the bind was removed"
    )


def test_both_engines_raise_the_same_tenant_unbound_type() -> None:
    """One exception type across both engines — imported, never re-declared.

    A ``except TenantUnbound`` must catch a refusal from either engine, and a
    second class would silently break that. RED if acb_graph forks its own type.
    """
    assert graph_db.TenantUnbound is common_db.TenantUnbound, (
        "acb_graph.TenantUnbound is a DIFFERENT type from acb_common's — "
        "import the shared one, do not re-declare it"
    )
    graph_src = _GRAPH_DB_SRC.read_text(encoding="utf-8")
    assert "from acb_common.db import TenantUnbound" in graph_src, (
        "acb_graph.db does not import the shared TenantUnbound"
    )
    assert "class TenantUnbound" not in graph_src, (
        "acb_graph.db re-declares TenantUnbound — it must import the shared one"
    )


# ==========================================================================
# ``acb-graph-sync-bind-sets-guc`` — R8 on the phase-4-promoted two-org catalog.
# ==========================================================================
@_DB_GATE
class TestAcbGraphSyncBindSetsGuc:
    """The sync seam's GUC bind, proven against real FORCE-RLS'd tables.

    Points the REAL ``acb_graph.db._session_factory`` at the dedicated phase-4
    catalog as the non-privileged ``acb_app_h3rls`` role (NullPool, so every op
    is a fresh backend where an unbound GUC is genuinely NULL — not a pooled
    ``''`` reset artefact, the hazard §H3 records), then drives the REAL
    ``tenant_session`` / ``get_session`` functions.
    """

    @pytest.fixture
    def graph_bound_to_scratch(self, promoted, monkeypatch):  # noqa: F811
        """Repoint acb_graph's session factory at the scratch catalog + acb_app.

        Monkeypatches the module-level ``_session_factory`` so ``tenant_session``
        and ``get_session`` (which resolve it at call time) open on THIS engine —
        the real functions, just aimed at the promoted DB as the non-priv role.
        """
        dsn = promoted.app_url.render_as_string(hide_password=False)
        eng = create_engine(dsn, future=True, poolclass=NullPool)
        factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
        monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
        try:
            yield eng
        finally:
            eng.dispose()

    def test_the_role_really_cannot_bypass_rls(self, graph_bound_to_scratch):
        """Precondition: without this, every assertion below is vacuous."""
        with graph_bound_to_scratch.connect() as c:
            row = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert row is not None
        assert not row[0], "the seam test connects as a SUPERUSER — RLS bypassed"
        assert not row[1], "the seam test role has BYPASSRLS — RLS bypassed"

    def test_a_bound_write_lands_and_is_isolated_to_that_tenant(
        self, graph_bound_to_scratch, promoted,  # noqa: F811
    ):
        """GREEN: a write through ``tenant_session(orgA)`` lands (its WITH CHECK
        passes because the GUC IS org A) and is visible ONLY bound to org A —
        invisible bound to org B. RED if the bind is removed: the INSERT's
        WITH CHECK then compares org A against the unset GUC and is refused."""
        marker = f"acbgraph-seam-{uuid.uuid4().hex[:8]}"
        try:
            with graph_db.tenant_session(promoted.org_a) as db:
                db.execute(text(
                    "INSERT INTO apps (slug, name, owner_email, "
                    "workspace_path, organization_id) "
                    "VALUES (:slug, :name, 'x@y', :wp, :org)"),
                    {"slug": marker, "name": marker,
                     "wp": f"/tmp/{marker}", "org": promoted.org_a})

            with graph_db.tenant_session(promoted.org_a) as db:
                seen_a = db.execute(text(
                    "SELECT count(*) FROM apps WHERE name = :m"),
                    {"m": marker}).scalar_one()
                stamped = db.execute(text(
                    "SELECT organization_id::text FROM apps WHERE name = :m"),
                    {"m": marker}).scalar_one()

            with graph_db.tenant_session(promoted.org_b) as db:
                seen_b = db.execute(text(
                    "SELECT count(*) FROM apps WHERE name = :m"),
                    {"m": marker}).scalar_one()

            assert seen_a == 1, (
                "org A cannot see the row it wrote through tenant_session — the "
                "sync bind did not set the GUC the USING clause reads")
            assert stamped == promoted.org_a
            assert seen_b == 0, (
                "org B saw org A's row — the bound read is not isolated")
        finally:
            with promoted.admin_engine.begin() as conn:
                conn.execute(text("DELETE FROM apps WHERE name = :m"),
                             {"m": marker})

    def test_the_plain_unbound_get_session_is_fail_closed(
        self, graph_bound_to_scratch, promoted,  # noqa: F811
    ):
        """The untouched ``get_session()`` binds NOTHING: under phase-4 it reads
        ZERO rows of org A's data and its write is WITH-CHECK-refused — the
        contrast that makes the bind above load-bearing rather than cosmetic."""
        marker = f"acbgraph-unbound-{uuid.uuid4().hex[:8]}"
        try:
            # seed a row org A CAN see, through the bound seam
            with graph_db.tenant_session(promoted.org_a) as db:
                db.execute(text(
                    "INSERT INTO apps (slug, name, owner_email, "
                    "workspace_path, organization_id) "
                    "VALUES (:slug, :name, 'x@y', :wp, :org)"),
                    {"slug": marker, "name": marker,
                     "wp": f"/tmp/{marker}", "org": promoted.org_a})

            # unbound READ → fail-closed, 0 rows
            with graph_db.get_session() as db:
                unbound_seen = db.execute(text(
                    "SELECT count(*) FROM apps WHERE name = :m"),
                    {"m": marker}).scalar_one()
            assert unbound_seen == 0, (
                f"an UNBOUND get_session() read {unbound_seen} rows — RLS is not "
                "fail-closed, or get_session silently binds a tenant")

            # unbound WRITE → WITH CHECK refuses
            with pytest.raises((DBAPIError, ProgrammingError)) as exc:  # noqa: SIM117
                with graph_db.get_session() as db:
                    db.execute(text(
                        "INSERT INTO apps (slug, name, owner_email, "
                        "workspace_path, organization_id) "
                        "VALUES (:slug, :name, 'x@y', :wp, :org)"),
                        {"slug": f"{marker}-evil", "name": f"{marker}-evil",
                         "wp": f"/tmp/{marker}-evil", "org": promoted.org_a})
            assert "row-level security" in str(exc.value).lower(), (
                f"expected a WITH CHECK violation unbound, got: {exc.value}")
        finally:
            with promoted.admin_engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM apps WHERE name LIKE :m"),
                    {"m": f"{marker}%"})
