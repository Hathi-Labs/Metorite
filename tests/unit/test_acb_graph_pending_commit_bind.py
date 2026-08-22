"""WS-29 acb_graph slice 4 — bind the executor's ``pending_commit`` write/read.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H4 (MT-1d) · the
``acb_graph`` tenant-isolation effort · D15. Behind ``ACB_GRAPH_TENANT_BIND``
(default OFF → byte-identical), the executor's ``pending_commit`` touch points open
``acb_graph.tenant_session(org)`` instead of the unbound ``get_session()`` so they
are RLS-safe on a FORCE-RLS'd (phase-4) catalog. The run's tenant is captured on
the EVENT LOOP frame (``_detect_agent_commits`` runs before ``run_agent_stream``'s
finally pops ``_RUN_ORG``) via ``_graph_session_opener(thread_id)``, and the single
resolved opener is reused for BOTH the dedup read and every ``_register_pending_
commit`` write — the exact slice-3 discipline, one hop earlier.

Fences (R7):

* ``pending-commit-write-bound-under-rls`` (R8 — real Postgres, the two-org
  phase-4 fixture reused from ``test_h3_rls_promotion_rehearsal.py``, non-priv role
  ``acb_app_h3rls``): with the flag ON and org A bound, a ``pending_commit`` INSERT
  lands and is visible ONLY to org A (invisible to org B). With the flag ON and no
  resolvable org, the write is SKIPPED (no row), LOGGED
  (``mutation.pending_commit_skipped_no_org``), and does NOT raise.
  RED-on-removal: reverting the seam to the unbound ``get_session()`` under the
  phase-4 catalog is RLS/NOT-NULL-refused, so the row never lands (the write
  returns ``None``) and the visibility assertion goes RED.
* flag-OFF regression: unset flag → ``_graph_session_opener`` IS the plain
  ``acb_graph.get_session`` object, and the un-converted ``_register_pending_
  commit`` caller (default opener) still opens ``get_session`` — byte-identical.

Run (Windows/psycopg, real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_acb_graph_pending_commit_bind.py -v -rs
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

pytest.importorskip("sqlalchemy")

from orchestrator import executor, mutation
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` is used by name for pytest fixture injection — the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)


# ── A recording logger so the fail-closed skip warnings are observable ────────
class _FakeLog:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw: object) -> None:
        self.warnings.append((event, kw))

    def __getattr__(self, _name: str):  # info/debug/error/exception → no-ops
        return lambda *a, **k: None


def _register_kwargs(commit_sha: str, **over: object) -> dict:
    """The non-tenant kwargs ``_register_pending_commit`` requires — one place so
    every test files the same row shape and only the tenant seam varies."""
    base = dict(
        agent_name="slice4-agent",
        run_id="run-" + uuid.uuid4().hex[:8],
        local_clone_dir="/tmp/slice4-clone",
        commit_sha=commit_sha,
        commit_message="slice-4 fence commit",
        diff_text="",
        test_summary="(fence)",
        status="pending",
    )
    base.update(over)
    return base


# ==========================================================================
# 1. flag-OFF regression + the fail-closed skip (no DB; always run).
# ==========================================================================
def test_flag_off_opener_is_plain_get_session(monkeypatch):
    """flag-OFF regression: unset flag → the opener the pending_commit path
    resolves IS the unbound ``acb_graph.get_session`` even when a tenant is
    available, so the converted read/write is byte-identical to pre-slice."""
    import acb_graph

    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    tid = "pc-off-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG[tid] = "org-ignored-when-off"
    try:
        assert executor._graph_session_opener(tid) is acb_graph.get_session, (
            "flag OFF must open the unbound get_session — byte-identical runtime"
        )
    finally:
        executor._RUN_ORG.pop(tid, None)


async def test_default_opener_falls_back_to_get_session(monkeypatch):
    """The un-converted caller (self-mutation sandbox) passes NO ``opener`` → the
    write opens the unbound ``acb_graph.get_session``, byte-identical. Proven with
    a fake session factory so it runs without a DB."""
    import acb_graph

    captured: dict[str, object] = {}

    class _FakeSession:
        def execute(self, *_a, **_k):
            captured["executed"] = True

        def commit(self):
            captured["committed"] = True

    @contextmanager
    def _fake_get_session():
        captured["opened"] = "get_session"
        yield _FakeSession()

    monkeypatch.setattr(acb_graph, "get_session", _fake_get_session)

    row_id = await mutation._register_pending_commit(
        **_register_kwargs("sha-default-" + uuid.uuid4().hex[:8])
    )

    assert captured.get("opened") == "get_session", (
        "the default (no-opener) path must fall back to the unbound get_session"
    )
    assert captured.get("executed") and captured.get("committed")
    assert row_id is not None


async def test_none_opener_skips_and_logs(monkeypatch):
    """Fail-closed: flag ON + no resolvable tenant → the executor passes
    ``opener=None`` → the write is SKIPPED (returns None), LOGGED, and touches NO
    database (no DB needed). Never raises."""
    import acb_graph

    def _boom():  # get_session must NOT be reached on the skip path
        raise AssertionError("fail-closed skip opened a session anyway")

    monkeypatch.setattr(acb_graph, "get_session", _boom)
    fake = _FakeLog()
    monkeypatch.setattr(mutation, "_log", fake)

    sha = "sha-skip-" + uuid.uuid4().hex[:8]
    row_id = await mutation._register_pending_commit(
        **_register_kwargs(sha), opener=None
    )

    assert row_id is None, "flag ON + no org must SKIP the write and return None"
    assert any(
        ev == "mutation.pending_commit_skipped_no_org" for ev, _kw in fake.warnings
    ), (
        "the fail-closed skip must be LOGGED so the drop is visible: "
        f"{[e for e, _ in fake.warnings]}"
    )


# ==========================================================================
# 2. pending-commit-write-bound-under-rls (R8 — real phase-4 Postgres).
# ==========================================================================
@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
    """Point the ``acb_graph`` SYNC engine at the dedicated phase-4 catalog as the
    non-priv ``acb_app_h3rls`` role, so ``get_session()`` / ``tenant_session()``
    run under FORCE RLS. Both call ``_session_factory()`` at open time, so
    rerouting that one attribute reroutes both. NullPool → every op is a fresh
    backend where an unbound GUC is genuinely NULL (not a pooled ``''`` reset
    artefact, the §H3 hazard). Yields the ``promoted`` namespace."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    try:
        yield promoted
    finally:
        eng.dispose()


def _count_visible_as(app_url, org: str, sha: str) -> int:
    """Rows with ``commit_sha == sha`` visible to a session bound to *org* (as the
    non-priv role, i.e. under FORCE RLS)."""
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(
                text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org},
            )
            return c.execute(
                text("SELECT count(*) FROM pending_commit WHERE commit_sha = :s"),
                {"s": sha},
            ).scalar_one()
    finally:
        eng.dispose()


def _count_bypass(admin_engine, sha: str) -> int:
    """Rows with ``commit_sha == sha`` seen by the RLS-bypassing admin — the
    ground truth of whether anything was written at all."""
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT count(*) FROM pending_commit WHERE commit_sha = :s"),
            {"s": sha},
        ).scalar_one()


@_DB_GATE
class TestPendingCommitWriteBoundUnderRls:
    """The R8 half: the REAL ``_register_pending_commit`` driven through the REAL
    ``_graph_session_opener`` under the phase-4 catalog."""

    async def test_write_lands_bound_and_is_org_isolated(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + org A bound (via ``_RUN_ORG`` → the executor's opener): the
        INSERT lands, is visible ONLY to org A, and is stamped org A by the
        phase-1 DEFAULT. RED-on-removal: reverting to the unbound ``get_session()``
        makes the DEFAULT read the unset GUC (NULL) → NOT-NULL/RLS refusal → the
        write returns None → no row → this assertion fails."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        tid = "pc-bind-" + uuid.uuid4().hex[:8]
        sha = "sha-bind-" + uuid.uuid4().hex[:12]
        executor._RUN_ORG[tid] = p.org_a
        try:
            # The exact production composition: the executor resolves the opener
            # on its (event-loop) frame, then hands it to the mutation writer.
            _open = executor._graph_session_opener(tid)
            row_id = await mutation._register_pending_commit(
                **_register_kwargs(sha), opener=_open,
            )

            assert row_id is not None, (
                "the bound INSERT did not land — it was RLS-refused, or the "
                "opener reverted to the unbound get_session"
            )
            assert _count_visible_as(p.app_url, p.org_a, sha) == 1, (
                "the write is not visible to its own tenant — the bound INSERT "
                "was refused, or reverted to the unbound get_session"
            )
            assert _count_visible_as(p.app_url, p.org_b, sha) == 0, (
                "org B saw org A's pending_commit row — RLS isolation failed"
            )
            with p.admin_engine.connect() as c:
                stamped = c.execute(
                    text(
                        "SELECT organization_id::text FROM pending_commit "
                        "WHERE commit_sha = :s"
                    ),
                    {"s": sha},
                ).scalar_one()
            assert stamped == p.org_a, (
                f"the row was stamped {stamped!r}, not the bound tenant"
            )
        finally:
            executor._RUN_ORG.pop(tid, None)
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM pending_commit WHERE commit_sha = :s"),
                    {"s": sha},
                )

    async def test_write_skipped_when_flag_on_and_no_org(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + no resolvable org: the executor's opener is None, so the
        write is SKIPPED (no row), LOGGED, and does NOT raise — a fail-closed
        drop, not a crash."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        tid = "pc-skip-" + uuid.uuid4().hex[:8]
        sha = "sha-skip-" + uuid.uuid4().hex[:12]
        executor._RUN_ORG.pop(tid, None)  # nothing to resolve
        fake = _FakeLog()
        monkeypatch.setattr(mutation, "_log", fake)

        _open = executor._graph_session_opener(tid)
        assert _open is None, "flag ON + no org must resolve to a None opener"

        row_id = await mutation._register_pending_commit(
            **_register_kwargs(sha), opener=_open,
        )  # must not raise

        assert row_id is None
        assert _count_bypass(p.admin_engine, sha) == 0, (
            "flag ON + no org must SKIP the write — a row landed unbound"
        )
        assert any(
            ev == "mutation.pending_commit_skipped_no_org"
            for ev, _kw in fake.warnings
        ), (
            "the fail-closed skip must be LOGGED so the drop is visible: "
            f"{[e for e, _ in fake.warnings]}"
        )

    async def test_reverting_to_unbound_get_session_lands_no_row(
        self, graph_on_promoted, monkeypatch,
    ):
        """RED-on-removal, at the writer: the flag-OFF/unbound ``get_session``
        opener (what a revert of the seam would restore) cannot write a
        ``pending_commit`` row under the phase-4 catalog — the ``organization_id``
        DEFAULT reads the unset GUC (NULL), NOT NULL / WITH CHECK refuses, the
        broad except swallows it and the writer returns None. So
        ``test_write_lands_*`` is genuinely RED if the opener is reverted."""
        import acb_graph

        monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        p = graph_on_promoted
        sha = "sha-unbound-" + uuid.uuid4().hex[:12]

        row_id = await mutation._register_pending_commit(
            **_register_kwargs(sha), opener=acb_graph.get_session,
        )

        assert row_id is None, (
            "the unbound get_session write returned a row id under phase-4 RLS — "
            "the RED-on-removal property does not hold (is pending_commit really "
            "FORCE-RLS'd here?)"
        )
        assert _count_bypass(p.admin_engine, sha) == 0, (
            "the unbound write landed a row — pending_commit is not FORCE-RLS'd, "
            "so reverting the seam would ship green (the fence is vacuous)"
        )

    def test_an_unbound_get_session_insert_is_rls_refused(
        self, graph_on_promoted, monkeypatch,
    ):
        """R8 mechanism proof: the raw unbound ``get_session()`` INSERT into
        ``pending_commit`` is refused under the phase-4 catalog — the
        ``organization_id`` DEFAULT reads the unset GUC (NULL) and NOT NULL / the
        WITH CHECK policy refuses. This is the SQL-level ground truth behind the
        writer-level RED-on-removal above."""
        import acb_graph

        monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        sha = "sha-raw-" + uuid.uuid4().hex[:12]
        with pytest.raises((DBAPIError, ProgrammingError)), \
                acb_graph.get_session() as s:
            s.execute(
                text(
                    "INSERT INTO pending_commit "
                    "(agent_name, run_id, local_clone_dir, commit_sha, "
                    " commit_message) "
                    "VALUES ('raw', 'raw', '/tmp/raw', :s, 'raw')"
                ),
                {"s": sha},
            )
