"""WS-29 acb_graph slice 3 — bind the executor's ``chat_session`` writes/reads.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H4 (MT-1d) · the
``acb_graph`` tenant-isolation effort · D15/D48. Behind ``ACB_GRAPH_TENANT_BIND``
(default OFF → byte-identical), the executor's ``chat_session`` bookkeeping opens
``acb_graph.tenant_session(org)`` instead of the unbound ``get_session()`` so it
is RLS-safe on a FORCE-RLS'd (phase-4) catalog, capturing the run's tenant on the
event loop BEFORE the ``run_in_executor`` worker-thread hop.

Three fences (R7):

* ``chat-session-write-bound-under-rls`` (R8 — real Postgres, the two-org phase-4
  fixture reused from ``test_h3_rls_promotion_rehearsal.py``, non-priv role
  ``acb_app_h3rls``): with the flag ON and org bound to A, a ``chat_session``
  UPSERT lands and is visible ONLY to A (invisible to B). With the flag ON and no
  resolvable org, the write is SKIPPED (no row) and logged, and does NOT raise.
  RED-on-removal: reverting the seam to the unbound ``get_session()`` under the
  phase-4 catalog is RLS/NOT-NULL-refused, so the row never lands and the
  visibility assertion goes RED.
* ``run-org-guarded-pop``: a superseded run's late ``finally`` must not delete a
  newer same-thread run's org. Goes RED if the pop is made unconditional.
* flag-OFF regression: unset flag → the write/read path opens ``get_session()``,
  byte-identical to today.

Run (Windows/psycopg, real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_acb_graph_chatsession_bind.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("sqlalchemy")

from orchestrator import executor
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
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

    def __getattr__(self, _name: str):  # info/debug/exception → no-ops
        return lambda *a, **k: None


# ==========================================================================
# 1. The flag → session-opener decision (no DB; always runs).
# ==========================================================================
def test_flag_off_opener_is_plain_get_session(monkeypatch):
    """flag-OFF regression: unset flag → the opener IS the unbound
    ``acb_graph.get_session`` even when a tenant is available, so the converted
    path is byte-identical to pre-slice runtime."""
    import acb_graph

    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    tid = "off-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG[tid] = "org-should-be-ignored-when-off"
    try:
        assert executor._graph_session_opener(tid) is acb_graph.get_session, (
            "flag OFF must open the unbound get_session — byte-identical runtime"
        )
    finally:
        executor._RUN_ORG.pop(tid, None)


def test_flag_on_with_org_opens_tenant_session(monkeypatch):
    """flag ON + a resolvable tenant → the opener binds THAT tenant via
    ``tenant_session`` — and it reads the org captured on THIS frame."""
    import acb_graph

    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    tid = "on-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG[tid] = "org-x"
    seen: dict[str, object] = {}
    monkeypatch.setattr(
        acb_graph, "tenant_session",
        lambda org: seen.setdefault("org", org),
    )
    try:
        opener = executor._graph_session_opener(tid)
        assert callable(opener)
        opener()  # invoking the opener opens tenant_session(org)
        assert seen["org"] == "org-x", (
            "the opener bound the wrong tenant — it must use _RUN_ORG[thread_id]"
        )
    finally:
        executor._RUN_ORG.pop(tid, None)


def test_flag_on_without_org_returns_none(monkeypatch):
    """flag ON + NO resolvable tenant → None: the caller MUST fail closed rather
    than open an unbound session on a FORCE-RLS'd table."""
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    tid = "none-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG.pop(tid, None)
    assert executor._graph_session_opener(tid) is None


# ==========================================================================
# 2. run-org-guarded-pop (no DB; always runs).
# ==========================================================================
def test_guarded_pop_preserves_a_newer_runs_org():
    """``run-org-guarded-pop``: a superseded run's late ``finally`` must not
    delete a newer same-thread run's org. RED if the pop is unconditional."""
    tid = "guard-" + uuid.uuid4().hex[:8]
    org_superseded = "org-A-superseded"
    org_newer = "org-B-newer"
    # The superseded run set org A; the newer run then overwrote it with org B.
    executor._RUN_ORG[tid] = org_newer
    try:
        # Now the superseded run's late finally fires with ITS own org (A):
        executor._guarded_pop_run_org(tid, org_superseded)
        assert executor._RUN_ORG.get(tid) == org_newer, (
            "the guarded pop deleted a newer run's org — a superseded run's late "
            "finally clobbered the live run's tenant (P2)"
        )
    finally:
        executor._RUN_ORG.pop(tid, None)


def test_guarded_pop_removes_its_own_org():
    """The normal path still cleans up: when the live value IS this run's org,
    the finally removes it."""
    tid = "guard-own-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG[tid] = "org-self"
    executor._guarded_pop_run_org(tid, "org-self")
    assert tid not in executor._RUN_ORG


def test_guarded_pop_no_org_run_never_pops():
    """A run that carried no org never set the key, so it must never pop a value a
    newer run installed."""
    tid = "guard-noorg-" + uuid.uuid4().hex[:8]
    executor._RUN_ORG[tid] = "org-newer"
    try:
        executor._guarded_pop_run_org(tid, None)
        assert executor._RUN_ORG.get(tid) == "org-newer"
    finally:
        executor._RUN_ORG.pop(tid, None)


# ==========================================================================
# 3. chat-session-write-bound-under-rls (R8 — real phase-4 Postgres).
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


async def _fire_and_flush(fn, *args) -> None:
    """Run a fire-and-forget ``acb_graph`` writer to completion deterministically.

    ``_store_session_id`` / ``_clear_stored_session_id`` schedule their ``_write``
    closure on the loop's default executor and do NOT return the future. Swapping
    in a single-worker executor makes the pool FIFO, so a trailing no-op completes
    only AFTER ``_write`` — a deterministic flush with no sleeps."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    fn(*args)
    await loop.run_in_executor(None, lambda: None)


def _count_visible_as(app_url, org: str, sid: str) -> int:
    """Rows with ``service_session_id == sid`` visible to a session bound to
    *org* (as the non-priv role, i.e. under FORCE RLS)."""
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(
                text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org},
            )
            return c.execute(
                text(
                    "SELECT count(*) FROM chat_session "
                    "WHERE service_session_id = :s"
                ),
                {"s": sid},
            ).scalar_one()
    finally:
        eng.dispose()


def _count_bypass(admin_engine, sid: str) -> int:
    """Rows with ``service_session_id == sid`` seen by the RLS-bypassing admin —
    the ground truth of whether anything was written at all."""
    with admin_engine.connect() as c:
        return c.execute(
            text(
                "SELECT count(*) FROM chat_session WHERE service_session_id = :s"
            ),
            {"s": sid},
        ).scalar_one()


@_DB_GATE
class TestChatSessionWriteBoundUnderRls:
    """The R8 half: the REAL ``_store_session_id`` under the phase-4 catalog."""

    async def test_write_lands_bound_and_is_org_isolated(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + org A bound: the UPSERT lands, is visible ONLY to org A, and
        carries org A. RED-on-removal: reverting to the unbound ``get_session()``
        defaults ``organization_id`` to the unset GUC (NULL) → NOT-NULL refusal →
        no row lands → this assertion fails."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        tid = "cs-bind-" + uuid.uuid4().hex[:8]
        sid = "svc-" + uuid.uuid4().hex[:12]
        executor._RUN_ORG[tid] = p.org_a
        try:
            await _fire_and_flush(executor._store_session_id, tid, sid)

            assert _count_visible_as(p.app_url, p.org_a, sid) == 1, (
                "the write did not land visible to its own tenant — the bound "
                "UPSERT was refused, or reverted to the unbound get_session"
            )
            assert _count_visible_as(p.app_url, p.org_b, sid) == 0, (
                "org B saw org A's chat_session row — RLS isolation failed"
            )
            with p.admin_engine.connect() as c:
                stamped = c.execute(
                    text(
                        "SELECT organization_id::text FROM chat_session "
                        "WHERE id = :i"
                    ),
                    {"i": tid},
                ).scalar_one()
            assert stamped == p.org_a, (
                f"the row was stamped {stamped!r}, not the bound tenant"
            )
        finally:
            executor._RUN_ORG.pop(tid, None)
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM chat_session WHERE id = :i"), {"i": tid},
                )

    async def test_write_skipped_when_flag_on_and_no_org(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + no resolvable org: the write is SKIPPED (no row), LOGGED, and
        does NOT raise — a fail-closed drop, not a crash."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        tid = "cs-skip-" + uuid.uuid4().hex[:8]
        sid = "svc-skip-" + uuid.uuid4().hex[:12]
        executor._RUN_ORG.pop(tid, None)  # nothing to resolve
        fake = _FakeLog()
        monkeypatch.setattr(executor, "_log", fake)

        await _fire_and_flush(executor._store_session_id, tid, sid)  # must not raise

        assert _count_bypass(p.admin_engine, sid) == 0, (
            "flag ON + no org must SKIP the write — a row landed unbound"
        )
        assert any("skipped_no_org" in ev for ev, _kw in fake.warnings), (
            "the fail-closed skip must be LOGGED so the drop is visible: "
            f"{[e for e, _ in fake.warnings]}"
        )

    async def test_clear_skipped_when_flag_on_and_no_org(
        self, graph_on_promoted, monkeypatch,
    ):
        """The clear write fails closed identically: flag ON + no org → skip +
        log, no unbound UPDATE on the FORCE-RLS'd table, no raise."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        tid = "cs-clr-" + uuid.uuid4().hex[:8]
        executor._RUN_ORG.pop(tid, None)
        fake = _FakeLog()
        monkeypatch.setattr(executor, "_log", fake)

        await _fire_and_flush(executor._clear_stored_session_id, tid)

        assert any("skipped_no_org" in ev for ev, _kw in fake.warnings), (
            f"clear skip not logged: {[e for e, _ in fake.warnings]}"
        )

    def test_an_unbound_get_session_write_is_rls_refused(
        self, graph_on_promoted, monkeypatch,
    ):
        """R8 mechanism proof for RED-on-removal: the unbound ``get_session()``
        (the flag-OFF opener, and what a revert would restore) cannot write a
        ``chat_session`` row under the phase-4 catalog — the ``organization_id``
        default reads the unset GUC (NULL) and NOT NULL refuses — so
        ``test_write_lands_*`` is genuinely RED if the seam is reverted."""
        import acb_graph

        monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        tid = "cs-unbound-" + uuid.uuid4().hex[:8]
        with pytest.raises(DBAPIError), acb_graph.get_session() as s:
            s.execute(
                text(
                    "INSERT INTO chat_session (id, user_id) "
                    "VALUES (:i, 'system')"
                ),
                {"i": tid},
            )
