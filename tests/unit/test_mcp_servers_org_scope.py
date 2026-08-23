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

Fences (R7):

* ``mcp-servers-scoped-to-run-org`` (R8 — real Postgres, the two-org phase-4
  fixture reused from ``test_h3_rls_promotion_rehearsal.py`` via the
  ``graph_on_promoted`` reroute): flag ON + org A bound, the REAL
  ``_inject_mcp_servers`` injects org A's server and NOT org B's; the SAME rows
  read WITHOUT the filter (the pre-fix query a revert restores) show BOTH, so the
  filter is load-bearing (RED-on-removal: drop ``AND organization_id = :org`` →
  org B's server appears and the isolation assertion goes RED).
* flag-OFF regression: no filter, both orgs' servers inject — byte-identical.
* flag ON + no resolvable org: fail closed to EMPTY, never another org's server.

Run (Windows/psycopg, real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_mcp_servers_org_scope.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from orchestrator import executor
from orchestrator._tool_injection import _inject_mcp_servers
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

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

# The pre-fix query (no org filter) a revert restores — used ONLY to prove the
# seeded org-B row is visible to an unfiltered read, so the isolation assertions
# are not vacuously passing because org B's server is invisible.
_UNFILTERED_SQL = "SELECT name FROM mcp_servers WHERE enabled = true"


class _Agent:
    """A bare agent stub: ``merge_mcp_servers`` only reads/writes ``_mcp_servers``."""


def _seed_mcp(admin_engine, *, org: str, name: str) -> None:
    """Seed one enabled stdio ``mcp_servers`` row for *org* as the RLS-bypassing
    admin, stamping ``organization_id`` explicitly (NOT NULL since 158)."""
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO mcp_servers (name, label, transport, command, "
                "enabled, organization_id) VALUES "
                "(:name, :name, 'stdio', 'npx -y srv', true, :org)"
            ),
            {"name": name, "org": org},
        )


def _cleanup_mcp(admin_engine, *, names: list[str]) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM mcp_servers WHERE name = ANY(:names)"),
            {"names": names},
        )


@_DB_GATE
class TestMcpServersScopedToRunOrg:
    """``mcp-servers-scoped-to-run-org``: the REAL MCP injection under the two-org
    catalog, gated by ``ACB_GRAPH_TENANT_BIND``."""

    async def test_flag_on_injects_only_the_run_orgs_servers(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + org A bound: ``_inject_mcp_servers`` injects org A's server
        and NOT org B's. RED-on-removal: the SAME rows read WITHOUT the filter
        (the pre-fix query) show BOTH — so the filter is load-bearing."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        marker = uuid.uuid4().hex[:8]
        srv_a = f"srv-a-{marker}"
        srv_b = f"srv-b-{marker}"
        _seed_mcp(p.admin_engine, org=p.org_a, name=srv_a)
        _seed_mcp(p.admin_engine, org=p.org_b, name=srv_b)
        try:
            agent = _Agent()
            clear_tenant()
            tok = bind_tenant(p.org_a)
            try:
                await _inject_mcp_servers(agent, _AGENT)
            finally:
                release_tenant(tok)

            injected = set(getattr(agent, "_mcp_servers", {}) or {})
            assert srv_a in injected, (
                "the org-A-bound injection did not include org A's own MCP "
                f"server: {sorted(injected)!r}"
            )
            assert srv_b not in injected, (
                "org B's MCP server leaked into an org-A-bound injection — the "
                f"cross-tenant read is not closed: {sorted(injected)!r}"
            )

            # RED-on-removal proof: the pre-fix query (no org filter) SEES org
            # B's row, so the assertion above is not vacuous.
            with acb_graph.get_session() as s:
                unfiltered = {
                    r[0] for r in s.execute(text(_UNFILTERED_SQL)).fetchall()
                }
            assert srv_a in unfiltered and srv_b in unfiltered, (
                "the seeded org-B server is not visible to an unfiltered read — "
                "dropping the filter would NOT leak it, so the isolation "
                f"assertion is vacuous: {sorted(unfiltered)!r}"
            )
        finally:
            clear_tenant()
            _cleanup_mcp(p.admin_engine, names=[srv_a, srv_b])

    async def test_flag_off_injects_all_orgs_servers_byte_identical(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag-OFF regression: no filter — EVEN with org A bound, both orgs'
        servers inject, byte-identical to the pre-slice runtime."""
        monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        p = graph_on_promoted
        marker = uuid.uuid4().hex[:8]
        srv_a = f"srv-a-{marker}"
        srv_b = f"srv-b-{marker}"
        _seed_mcp(p.admin_engine, org=p.org_a, name=srv_a)
        _seed_mcp(p.admin_engine, org=p.org_b, name=srv_b)
        try:
            agent = _Agent()
            clear_tenant()
            tok = bind_tenant(p.org_a)  # bound, but the flag is OFF → ignored
            try:
                await _inject_mcp_servers(agent, _AGENT)
            finally:
                release_tenant(tok)

            injected = set(getattr(agent, "_mcp_servers", {}) or {})
            assert srv_a in injected and srv_b in injected, (
                "flag OFF must inject every org's servers (no filter) — "
                f"byte-identical to today: {sorted(injected)!r}"
            )
        finally:
            clear_tenant()
            _cleanup_mcp(p.admin_engine, names=[srv_a, srv_b])

    async def test_flag_on_without_org_fails_closed_to_empty(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + NO resolvable tenant → fail closed to EMPTY. ``mcp_servers``
        has no org-less "global" rows (organization_id is NOT NULL), so the safe
        subset is nothing — NEVER another org's server."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        marker = uuid.uuid4().hex[:8]
        srv_a = f"srv-a-{marker}"
        srv_b = f"srv-b-{marker}"
        _seed_mcp(p.admin_engine, org=p.org_a, name=srv_a)
        _seed_mcp(p.admin_engine, org=p.org_b, name=srv_b)
        try:
            agent = _Agent()
            clear_tenant()
            # Precondition: no tenant resolves on this frame.
            assert executor._stream_relay_thread_id.get(None) is None
            assert executor._current_run_org() is None

            await _inject_mcp_servers(agent, _AGENT)

            injected = set(getattr(agent, "_mcp_servers", {}) or {})
            assert injected == set(), (
                "flag ON + no resolvable org must inject NOTHING (fail closed), "
                f"never another org's servers: {sorted(injected)!r}"
            )
        finally:
            clear_tenant()
            _cleanup_mcp(p.admin_engine, names=[srv_a, srv_b])
