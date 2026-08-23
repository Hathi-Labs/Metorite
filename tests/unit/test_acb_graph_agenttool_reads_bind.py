"""WS-29 acb_graph slice 7 — bind the agent-tool READS through the sync engine.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H4 (MT-1d) · the
``acb_graph`` tenant-isolation effort · D15/D48. Behind ``ACB_GRAPH_TENANT_BIND``
(default OFF → byte-identical), the reads an agent run makes through the
``acb_graph`` sync engine open ``acb_graph.tenant_session(org)`` instead of the
unbound ``get_session()`` — so on a FORCE-RLS'd (phase-4) catalog they RETURN
ROWS instead of silently returning 0. An unbound read of a FORCE-RLS'd tenant
table returns nothing post-cutover, which would strip the agent's entity context,
its granted apps, and its skill toggles.

The run's tenant is resolved SERVER-SIDE on the event-loop frame (never from tool
arguments / the event payload, R11) by ``executor._graph_session_opener_current``
→ ``_current_run_org`` (``_RUN_ORG`` keyed by ``_stream_relay_thread_id``, else
the async bound tenant), and captured BEFORE any ``asyncio.to_thread`` hop.

Fences (R7):

* ``agent-retrieval-reads-bound-return-rows`` (R8 — real Postgres, the two-org
  phase-4 fixture reused from ``test_h3_rls_promotion_rehearsal.py``, non-priv
  role ``acb_app_h3rls``): with the flag ON and org bound to A, the REAL
  ``retrieve_entity_context`` tool and the REAL ``_granted_live_apps`` injection
  read RETURN org A's rows; bound to B they see nothing; and the SAME read via the
  plain unbound ``get_session()`` under the phase-4 catalog returns 0 rows —
  proving the bind is load-bearing (RED-on-removal).
* flag-OFF regression: unset flag → ``_graph_session_opener_current`` IS the
  unbound ``acb_graph.get_session``, byte-identical to today. Plus the
  opener-decision matrix (flag ON + org → ``tenant_session``; flag ON + no org →
  ``None`` → fail closed).

Run (Windows/psycopg, real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_acb_graph_agenttool_reads_bind.py -v -rs
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

import acb_graph
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from orchestrator import app_tools, executor
from orchestrator.agents import retrieve_entity_context
from orchestrator.retrieval import retrieve
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` is used by name for pytest fixture injection — the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)


# ==========================================================================
# 1. The opener-decision matrix (no DB; always runs).
# ==========================================================================
def test_flag_off_current_opener_is_plain_get_session(monkeypatch):
    """flag-OFF regression: unset flag → ``_graph_session_opener_current`` IS the
    unbound ``acb_graph.get_session`` EVEN with a tenant bound — the converted
    reads are byte-identical to pre-slice runtime."""
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    tok = bind_tenant("org-should-be-ignored-when-off")
    try:
        assert (
            executor._graph_session_opener_current() is acb_graph.get_session
        ), "flag OFF must open the unbound get_session — byte-identical runtime"
    finally:
        release_tenant(tok)


def test_flag_on_with_bound_tenant_opens_tenant_session(monkeypatch):
    """flag ON + the run's tenant on the async frame (the ``/copilot/chat`` and
    ``run_agent_stream`` shape) → the opener binds THAT tenant via
    ``tenant_session``, resolved from the current frame."""
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        acb_graph, "tenant_session",
        lambda org: seen.setdefault("org", org),
    )
    clear_tenant()
    tok = bind_tenant("org-frame")
    try:
        opener = executor._graph_session_opener_current()
        assert callable(opener)
        opener()  # invoking the opener opens tenant_session(org)
        assert seen["org"] == "org-frame"
    finally:
        release_tenant(tok)


def test_flag_on_reads_run_org_by_stream_thread(monkeypatch):
    """flag ON: the PRIMARY resolution path — ``_RUN_ORG`` keyed by the run's
    ``_stream_relay_thread_id`` — is preferred over the bound-tenant fallback, so
    a worker-thread run resolves the right tenant on its own frame."""
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        acb_graph, "tenant_session",
        lambda org: seen.setdefault("org", org),
    )
    tid = "curr-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG[tid] = "org-run"
    token = executor._stream_relay_thread_id.set(tid)
    try:
        executor._graph_session_opener_current()()
        assert seen["org"] == "org-run", (
            "the current-frame opener did not read _RUN_ORG[_stream_relay_thread_id]"
        )
    finally:
        executor._stream_relay_thread_id.reset(token)
        executor._RUN_ORG.pop(tid, None)


def test_flag_on_without_org_returns_none(monkeypatch):
    """flag ON + NO resolvable tenant → ``None``: the read MUST fail closed
    (return empty / no tools) rather than read a FORCE-RLS'd table unbound."""
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    clear_tenant()
    assert executor._stream_relay_thread_id.get(None) is None
    assert executor._graph_session_opener_current() is None


# ==========================================================================
# 2. R8: reads bound under the phase-4 catalog RETURN ROWS (RED unbound).
# ==========================================================================
@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
    """Point the ``acb_graph`` SYNC engine at the dedicated phase-4 catalog as the
    non-priv ``acb_app_h3rls`` role, so ``get_session()`` / ``tenant_session()``
    run under FORCE RLS. Both call ``_session_factory()`` at open time, so
    rerouting that one attribute reroutes both. NullPool → every op is a fresh
    backend where an unbound GUC is genuinely NULL. Yields ``promoted``."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield promoted
    finally:
        eng.dispose()


def _seed_project(admin_engine, *, org: str, name: str) -> None:
    """Seed one ``project`` row for *org* as the RLS-bypassing admin (the GUC is
    unset for the admin, so ``organization_id`` is stamped EXPLICITLY — the
    phase-1 DEFAULT would read the unset GUC as NULL)."""
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO project (id, name, organization_id) "
                "VALUES (:id, :name, :org)"
            ),
            {"id": str(uuid.uuid4()), "name": name, "org": org},
        )


def _cleanup_project(admin_engine, *, name: str) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM project WHERE name = :name"), {"name": name})


def _seed_granted_app(admin_engine, *, org: str, slug: str, agent: str) -> str:
    """Seed a LIVE app + its live version + a direct ``agent:<name>`` grant for
    *org*, all stamped with *org* explicitly. Returns the app id."""
    app_id = str(uuid.uuid4())
    with admin_engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO apps (id, slug, name, owner_email, workspace_path, "
                "status, live_version, manifest, organization_id) VALUES "
                "(:id, :slug, :slug, 'x@y', :wp, 'live', 1, '{}'::jsonb, :org)"
            ),
            {"id": app_id, "slug": slug, "wp": f"/tmp/{slug}", "org": org},
        )
        c.execute(
            text(
                "INSERT INTO app_versions (app_id, version, manifest, "
                "bundle_html, bundle_sha256, published_by, organization_id) "
                "VALUES (:id, 1, :manifest, '<html/>', 'sha', 'x@y', :org)"
            ),
            {"id": app_id, "manifest": '{"name": "seeded"}', "org": org},
        )
        c.execute(
            text(
                "INSERT INTO app_grants (app_id, subject, granted_by, "
                "organization_id) VALUES (:id, :subject, 'x@y', :org)"
            ),
            {"id": app_id, "subject": f"agent:{agent}", "org": org},
        )
    return app_id


def _cleanup_app(admin_engine, *, slug: str) -> None:
    # app_versions / app_grants cascade on the apps delete (ON DELETE CASCADE).
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug = :slug"), {"slug": slug})


_GRANT_JOIN = (
    "SELECT DISTINCT a.slug FROM apps a "
    "JOIN app_grants g ON g.app_id = a.id "
    "JOIN app_versions v ON v.app_id = a.id AND v.version = a.live_version "
    "WHERE a.status = 'live' AND a.live_version IS NOT NULL "
    "  AND g.subject IN ('agents:*', :subject)"
)


@_DB_GATE
class TestAgentRetrievalReadsBoundReturnRows:
    """``agent-retrieval-reads-bound-return-rows``: the REAL agent-path reads
    under the phase-4 catalog, flag ON."""

    async def test_entity_retrieval_returns_bound_org_rows(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + org A bound: ``retrieve_entity_context`` RETURNS org A's
        project. Bound to org B, the SAME query sees nothing (isolation). And the
        SAME read via the unbound ``get_session()`` returns 0 rows — the
        RED-on-removal proof that the bind is load-bearing, not cosmetic."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        marker = "zzmark" + uuid.uuid4().hex[:8]
        name = f"proj-{marker}"
        _seed_project(p.admin_engine, org=p.org_a, name=name)
        try:
            clear_tenant()
            tok = bind_tenant(p.org_a)
            try:
                out_a = await retrieve_entity_context(marker)
            finally:
                release_tenant(tok)
            assert marker in out_a, (
                "the bound entity read did not return org A's project — the "
                f"FORCE-RLS'd read came back empty: {out_a!r}"
            )

            tok = bind_tenant(p.org_b)
            try:
                out_b = await retrieve_entity_context(marker)
            finally:
                release_tenant(tok)
            assert out_b == "(no matching entities found)", (
                f"org B saw org A's project — RLS isolation failed: {out_b!r}"
            )

            # RED-on-removal: the unbound get_session (the flag-OFF opener, and
            # what a revert restores) reads 0 rows under the phase-4 catalog.
            with acb_graph.get_session() as s:
                hits = retrieve(s, marker)
            assert hits == [], (
                "the unbound get_session read returned rows — either the bind is "
                "not load-bearing or project is not actually FORCE-RLS'd"
            )
        finally:
            clear_tenant()
            _cleanup_project(p.admin_engine, name=name)

    def test_granted_apps_returns_bound_org_rows(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + org A bound: ``_granted_live_apps`` RETURNS the app granted
        to the agent in org A. And the SAME join via the unbound ``get_session()``
        returns 0 rows — the RED-on-removal proof."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        agent = "gbot" + uuid.uuid4().hex[:6]
        slug = "gapp-" + uuid.uuid4().hex[:6]
        _seed_granted_app(p.admin_engine, org=p.org_a, slug=slug, agent=agent)
        try:
            clear_tenant()
            tok = bind_tenant(p.org_a)
            try:
                granted = app_tools._granted_live_apps(agent)
            finally:
                release_tenant(tok)
            slugs = [s for s, _m in granted]
            assert slug in slugs, (
                "the bound grant lookup did not return org A's granted app — the "
                f"FORCE-RLS'd apps⋈app_grants⋈app_versions join came back empty: "
                f"{slugs!r}"
            )

            # RED-on-removal: the same join, unbound, is 0 rows under phase-4.
            with acb_graph.get_session() as s:
                rows = s.execute(
                    text(_GRANT_JOIN), {"subject": f"agent:{agent}"},
                ).fetchall()
            assert rows == [], (
                "the unbound get_session join returned rows — either the bind is "
                "not load-bearing or the apps family is not actually FORCE-RLS'd"
            )
        finally:
            clear_tenant()
            _cleanup_app(p.admin_engine, slug=slug)

    def test_granted_apps_fails_closed_without_org(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + no resolvable org → ``_granted_live_apps`` returns no apps
        (fail closed) rather than an unbound read of the FORCE-RLS'd tables."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        agent = "gbot" + uuid.uuid4().hex[:6]
        slug = "gapp-" + uuid.uuid4().hex[:6]
        _seed_granted_app(p.admin_engine, org=p.org_a, slug=slug, agent=agent)
        try:
            clear_tenant()
            assert executor._stream_relay_thread_id.get(None) is None
            granted = app_tools._granted_live_apps(agent)
            assert granted == [], (
                "flag ON + no org must return no granted apps (fail closed), "
                f"got {granted!r}"
            )
        finally:
            clear_tenant()
            _cleanup_app(p.admin_engine, slug=slug)
