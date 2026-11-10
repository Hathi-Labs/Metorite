"""WS-29 — ``mcp_servers`` org-scope filter (app-level; the table stays RLS-exempt).

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H4 (MT-1d) · the
MT-0d ``mcp_servers`` cross-tenant read gap · D15. ``mcp_servers`` is RLS-EXEMPT
by design (``gen_tenant_migration.EXEMPT`` — "keyed (organization_id, name) by
MT-0d / 158"): it has no FORCE-RLS policy, so the phase-4 RLS cutover does NOT
isolate it. The MCP-registry read ``_inject_mcp_servers`` therefore closes its
cross-tenant exposure at the APP level, behind the same ``ACB_GRAPH_TENANT_BIND``
flag as slices 3-7: an explicit ``organization_id`` filter so an agent in org A
is never injected org B's MCP server endpoints/config once a second tenant exists.

The run's org is resolved SERVER-SIDE on the event-loop frame by
``executor._current_run_org`` (``_RUN_ORG`` keyed by ``_stream_relay_thread_id``,
else the async ``current_tenant()``) — NEVER from tool args / the message (R11).
``mcp_servers.organization_id`` is NOT NULL (migration 158), so there are NO
org-less "global" servers: the filter is strictly ``organization_id = :org`` and
the fail-closed subset (flag ON, no resolvable org) is EMPTY.

⚠️ **WS-54 IN-4 (2026-10-12): the servers moved into the connection
registry.** ``integration_connections`` has FORCE RLS, and the one reader
``gateway.connections.mcp.servers_for_org`` opens ``tenant_session(org)``. So
these fences seed the registry through the real writer, and run the reader
as the non-privileged role through :func:`registry_on_promoted`. The
"not vacuous" check reads both orgs' rows as the admin, who bypasses RLS.

⚠️ **WS-8c review round 2 (2026-10-11): registry MCP is MAF-only.** A
Copilot agent now gets NO registry server. It logs each in-scope row of the
run's org as ``mcp.copilot_registry_skipped`` and drops it. So these fences
read the org filter from those log lines: the names logged are exactly the
rows that the filtered read returned. The MAF half is
``tests/unit/test_maf_mcp_org_scope_r8.py``.

Fences (R7):

* ``mcp-servers-scoped-to-run-org`` (R8 — real Postgres, the two-org phase-4
  fixture reused from ``test_h3_rls_promotion_rehearsal.py`` via the
  ``graph_on_promoted`` reroute): flag ON + org A bound, the REAL
  ``_inject_mcp_servers`` injects org A's server and NOT org B's; the SAME rows
  read WITHOUT the filter (the pre-fix query a revert restores) show BOTH, so the
  filter is load-bearing (RED-on-removal: drop ``AND organization_id = :org`` →
  org B's server appears and the isolation assertion goes RED).
* **Inverted by WS-8c (2026-10-11).** This file pinned "flag OFF reads
  every org's rows", which was a cross-tenant read of server URLs and
  headers. The filter is now on whatever ``ACB_GRAPH_TENANT_BIND`` says:
  with the flag OFF and org A bound, only org A's server injects.
* No resolvable org, with the flag ON or OFF: fail closed to EMPTY.
* The rows are ``http-sse``, because WS-8c drops a stdio row on both paths
  (D85, ``tests/unit/test_no_mcp_stdio.py``).

Run (Windows/psycopg, real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_mcp_servers_org_scope.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import bind_tenant, clear_tenant, release_tenant
from orchestrator import executor
from orchestrator._tool_injection import _inject_mcp_servers
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from tests.unit._tenant_ladder import tenant_engine_scope

# Reuse the two-org phase-4 fixture + its DB gate (the SAME dedicated catalog the
# slice-7 reads use, so mcp_servers has the 158 org column). ``promoted`` is used
# by name for pytest fixture injection — the import is load-bearing even though it
# reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_AGENT = "orchestrator"


@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
    """Point the ``acb_graph`` SYNC engine at the dedicated two-org catalog as the
    non-priv ``acb_app_h3rls`` role — the same reroute the slice-7 read fence
    uses. ``get_session()`` calls ``_session_factory()`` at open time, so
    rerouting that one attribute reroutes the read. ``mcp_servers`` is RLS-EXEMPT,
    so the non-priv role reads all rows regardless of the GUC — which is precisely
    the cross-tenant exposure the app-level filter closes. Yields ``promoted``."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield promoted
    finally:
        eng.dispose()

#: The master key of the registry blob in these suites (IN-4).
R8_MASTER_KEY = "r8-master-key-in4-mcp"


@pytest.fixture
async def registry_on_promoted(graph_on_promoted, monkeypatch):
    """``graph_on_promoted``, plus the async seam of the MCP registry (IN-4).

    The reader opens ``tenant_session``, so ``acb_common.db`` points at the
    two-org catalog as the NON-privileged role. The blob needs a master key.
    Each registry row of an MCP server in the two orgs is deleted after.
    """
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    p = graph_on_promoted
    app_dsn = p.app_url.render_as_string(hide_password=False)
    settings = get_settings()
    monkeypatch.setattr(settings, "acb_master_key", R8_MASTER_KEY, raising=False)
    # The key-store reads of the read model go to the same catalog.
    monkeypatch.setattr(settings, "database_url", app_dsn, raising=False)
    monkeypatch.setattr(key_store, "_store", None)
    try:
        async with tenant_engine_scope(app_dsn):
            yield p
    finally:
        with p.admin_engine.begin() as c:
            for org in (p.org_a, p.org_b):
                c.execute(text(
                    "DELETE FROM integration_connections WHERE organization_id = "
                    "CAST(:o AS uuid) AND provider LIKE 'mcp:%'"), {"o": org})
                c.execute(text(
                    "DELETE FROM mcp_servers WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def seed_registry(org: str, name: str, *, headers: dict | None = None,
                        url: str = "https://mcp.example.com/mcp") -> None:
    """Register one enabled http-sse server through the REAL writer (IN-4)."""
    from gateway.connections import mcp

    await mcp.save(org, name=name, url=url, headers=headers or {}, connected_by="r8-seed")


def registry_names(admin_engine) -> set[str]:
    """Every MCP server name of the registry, read as the admin (no RLS)."""
    with admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT provider FROM integration_connections WHERE provider LIKE 'mcp:%'"
        )).fetchall()
    return {r[0].removeprefix("mcp:") for r in rows}


async def _rows_read(agent) -> set[str]:
    """Run the REAL injection, and return the names the read returned.

    The Copilot path writes nothing (WS-8c). The rows the filtered read
    returned are the ones it logs as ``mcp.copilot_registry_skipped``."""
    import structlog

    with structlog.testing.capture_logs() as logs:
        await _inject_mcp_servers(agent, _AGENT)
    assert agent._default_options == {}, "a registry row reached a Copilot agent"
    return {e["name"] for e in logs if e["event"] == "mcp.copilot_registry_skipped"}


class _Agent:
    """A bare agent stub: ``merge_mcp_servers`` only reads/writes
    ``_default_options["mcp_servers"]`` (the 2.0 wrapper's field, H-181)."""

    def __init__(self) -> None:
        self._default_options: dict = {}


@_DB_GATE
class TestMcpServersScopedToRunOrg:
    """``mcp-servers-scoped-to-run-org``: the REAL MCP injection under the two-org
    catalog, gated by ``ACB_GRAPH_TENANT_BIND``."""

    async def test_flag_on_injects_only_the_run_orgs_servers(
        self, registry_on_promoted, monkeypatch,
    ):
        """flag ON + org A bound: ``_inject_mcp_servers`` injects org A's server
        and NOT org B's. RED-on-removal: the SAME rows read WITHOUT the filter
        (the pre-fix query) show BOTH — so the filter is load-bearing."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = registry_on_promoted
        marker = uuid.uuid4().hex[:8]
        srv_a = f"srv-a-{marker}"
        srv_b = f"srv-b-{marker}"
        await seed_registry(p.org_a, srv_a)
        await seed_registry(p.org_b, srv_b)
        try:
            agent = _Agent()
            clear_tenant()
            tok = bind_tenant(p.org_a)
            try:
                injected = await _rows_read(agent)
            finally:
                release_tenant(tok)

            assert srv_a in injected, (
                "the org-A-bound injection did not include org A's own MCP "
                f"server: {sorted(injected)!r}"
            )
            assert srv_b not in injected, (
                "org B's MCP server leaked into an org-A-bound injection — the "
                f"cross-tenant read is not closed: {sorted(injected)!r}"
            )

            # Not vacuous: a read with no tenant and no filter (the admin,
            # who bypasses RLS) SEES org B's row.
            unfiltered = registry_names(p.admin_engine)
            assert srv_a in unfiltered and srv_b in unfiltered, (
                "the seeded org-B server is not visible to an unfiltered read — "
                "dropping the filter would NOT leak it, so the isolation "
                f"assertion is vacuous: {sorted(unfiltered)!r}"
            )
        finally:
            clear_tenant()

    async def test_flag_off_still_injects_only_the_run_orgs_servers(
        self, registry_on_promoted, monkeypatch,
    ):
        """WS-8c inverted this fence. With ``ACB_GRAPH_TENANT_BIND`` OFF and
        org A bound, the filter still applies: org A's server injects, and
        org B's does not. Before WS-8c, the flag-OFF path read every org."""
        monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        p = registry_on_promoted
        marker = uuid.uuid4().hex[:8]
        srv_a = f"srv-a-{marker}"
        srv_b = f"srv-b-{marker}"
        await seed_registry(p.org_a, srv_a)
        await seed_registry(p.org_b, srv_b)
        try:
            agent = _Agent()
            clear_tenant()
            tok = bind_tenant(p.org_a)  # bound, and the flag is OFF
            try:
                injected = await _rows_read(agent)
            finally:
                release_tenant(tok)

            assert srv_a in injected, (
                f"org A's own server did not inject: {sorted(injected)!r}"
            )
            assert srv_b not in injected, (
                "flag OFF leaked org B's MCP server into an org-A run — the "
                f"org filter must not depend on the flag: {sorted(injected)!r}"
            )
        finally:
            clear_tenant()

    @pytest.mark.parametrize("bind_flag", ["1", None], ids=["flag-on", "flag-off"])
    async def test_without_org_fails_closed_to_empty(
        self, registry_on_promoted, monkeypatch, bind_flag,
    ):
        """NO resolvable tenant → fail closed to EMPTY, with the flag ON or
        OFF (WS-8c). ``mcp_servers`` has no org-less "global" rows
        (organization_id is NOT NULL), so the safe subset is nothing — NEVER
        another org's server."""
        if bind_flag:
            monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", bind_flag)
        else:
            monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        p = registry_on_promoted
        marker = uuid.uuid4().hex[:8]
        srv_a = f"srv-a-{marker}"
        srv_b = f"srv-b-{marker}"
        await seed_registry(p.org_a, srv_a)
        await seed_registry(p.org_b, srv_b)
        try:
            agent = _Agent()
            clear_tenant()
            # Precondition: no tenant resolves on this frame.
            assert executor._stream_relay_thread_id.get(None) is None
            assert executor._current_run_org() is None

            injected = await _rows_read(agent)
            assert injected == set(), (
                "flag ON + no resolvable org must inject NOTHING (fail closed), "
                f"never another org's servers: {sorted(injected)!r}"
            )
        finally:
            clear_tenant()
