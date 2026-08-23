"""WS-29 acb_graph slice 5 — bind ``audit_event`` writes to the tenant.

Spec: ``project-docs/specs/saas_multitenancy_handover.md`` §H4 (MT-1d) · the
``acb_graph`` tenant-isolation effort · D15/D48. Behind ``ACB_GRAPH_TENANT_BIND``
(default OFF → byte-identical), ``acb_audit.log._persist`` opens
``acb_graph.tenant_session(org)`` instead of the unbound ``get_session()`` so the
best-effort audit write is RLS-safe on a FORCE-RLS'd (phase-4) catalog.

**This is the highest-risk slice: audit writes are best-effort and SWALLOWED**
(``_persist`` catches every exception), so a broken bind fails SILENTLY — a
swallowed RLS refusal looks exactly like success. Every DB fence here therefore
asserts the ROW LANDED (queries the table via the RLS-bypassing admin and/or the
tenant-bound non-priv role), never merely that no exception was raised.

**Owner-ratified Option-A:** ``audit_event`` is tenant-SCOPED, AND a tenant-less
system/cron event is BOUND to the operator/DEFAULT org so it is RETAINED, never
dropped. So — unlike slices 3/4 which SKIP on no-org — audit FALLS BACK to the
operator org and only skips if BOTH the event org and the default-org lookup are
unavailable (near-impossible: the ``default`` org is seeded by migration 130).

Fences (R7):

* ``audit-event-write-bound-under-rls`` (R8, phase-4 two-org catalog, non-priv
  role ``acb_app_h3rls``): flag ON + an event carrying org A → the row LANDS,
  is visible ONLY to org A, and is stamped org A. RED-on-removal: reverting
  ``_persist`` to the unbound ``get_session()`` under the phase-4 catalog is
  NOT-NULL / RLS refused (``test_an_unbound_get_session_write_is_refused``), so
  the row never lands and the visibility assertion goes RED.
* ``audit-system-event-falls-back-to-operator-org`` (R8): flag ON + an event
  with NO org → the row LANDS under the operator/DEFAULT org (retained, not
  refused), stamped that org.
* flag-OFF regression: ``_persist`` opens ``get_session()`` unchanged and a real
  row lands on the pre-phase-4 shared ladder, byte-identical to today.
* the flag → opener decision (no DB): OFF → ``get_session``; ON + event org →
  ``tenant_session(event org)``; ON + no event org → ``tenant_session(operator
  org)``; ON + neither → skip + log, no session opened.

Run (Windows/psycopg, real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5443/acb_tenant \
        uv run pytest tests/unit/test_acb_graph_audit_bind.py -v -rs
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# Reuse the two-org phase-4 fixture + its DB gate (non-priv role acb_app_h3rls).
# ``promoted`` is used by name for pytest fixture injection — the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    promoted,
)


# ── A recording logger so the fail-closed skip / failure warnings are observable ─
class _FakeLog:
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kw: object) -> None:
        self.warnings.append((event, kw))

    def __getattr__(self, _name: str):  # info/debug/exception → no-ops
        return lambda *a, **k: None


# ── A spy over the acb_graph session openers (no DB) ─────────────────────────
class _FakeSession:
    def add(self, _row: object) -> None:  # ORM add is a no-op for the decision test
        pass


class _OpenerSpy:
    """Records which opener ``_persist`` chose and with what org, without a DB."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, str | None]] = []

    @contextmanager
    def _cm(self):
        yield _FakeSession()

    def get_session(self):
        self.opened.append(("get", None))
        return self._cm()

    def tenant_session(self, org: str):
        self.opened.append(("tenant", org))
        return self._cm()


def _event(**kw: object) -> audit.AuditEvent:
    base: dict = {"actor": "user:t@x", "action": "slice5", "target": "thing:1"}
    base.update(kw)
    return audit.AuditEvent(**base)


# ==========================================================================
# 1. The flag → opener decision in ``_persist`` (no DB; always runs).
# ==========================================================================
def _install_spy(monkeypatch) -> tuple[_OpenerSpy, _FakeLog]:
    import acb_graph

    spy = _OpenerSpy()
    fake = _FakeLog()
    monkeypatch.setattr(acb_graph, "get_session", spy.get_session)
    monkeypatch.setattr(acb_graph, "tenant_session", spy.tenant_session)
    monkeypatch.setattr(audit, "_log", fake)
    return spy, fake


def test_flag_off_persist_opens_plain_get_session(monkeypatch):
    """flag-OFF regression: unset flag → ``_persist`` opens the unbound
    ``get_session`` even when the event carries an org, so the write path is
    byte-identical to the pre-slice runtime."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)

    audit._persist(_event(organization_id="org-should-be-ignored-when-off"))

    assert spy.opened == [("get", None)], (
        "flag OFF must open the unbound get_session — byte-identical runtime"
    )
    assert not any("persist_failed" in ev for ev, _ in fake.warnings)


def test_flag_on_with_event_org_binds_that_org(monkeypatch):
    """flag ON + the event carries an org → ``_persist`` binds THAT org via
    ``tenant_session`` — reading it OFF THE EVENT, never a ContextVar."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")

    audit._persist(_event(organization_id="org-A"))

    assert spy.opened == [("tenant", "org-A")], (
        "flag ON + event org must bind tenant_session(event.organization_id)"
    )
    assert not any("persist_failed" in ev for ev, _ in fake.warnings)


def test_flag_on_no_event_org_falls_back_to_operator_org(monkeypatch):
    """flag ON + a tenant-less event → ``_persist`` binds the OPERATOR/DEFAULT
    org (Option-A) so the row is retained — it must NOT skip when a fallback
    exists."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    monkeypatch.setattr(audit, "_resolve_operator_org_id", lambda: "op-org")

    audit._persist(_event(organization_id=None))

    assert spy.opened == [("tenant", "op-org")], (
        "a tenant-less event must fall back to the operator org, not skip"
    )
    assert not any("skipped_no_org" in ev for ev, _ in fake.warnings)


def test_flag_on_no_org_and_no_operator_org_skips_and_logs(monkeypatch):
    """flag ON + neither an event org NOR a resolvable operator org → skip + log,
    no session opened (fail-closed; near-impossible in practice)."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    monkeypatch.setattr(audit, "_resolve_operator_org_id", lambda: None)

    audit._persist(_event(organization_id=None))

    assert spy.opened == [], "no org anywhere must open NO session, not an unbound one"
    assert any("skipped_no_org" in ev for ev, _ in fake.warnings), (
        f"the fail-closed skip must be LOGGED: {[e for e, _ in fake.warnings]}"
    )


# ==========================================================================
# 2. Operator-org resolution (no DB; env override + caching semantics).
# ==========================================================================
def test_operator_org_env_override_wins_and_needs_no_db(monkeypatch):
    """``OPERATOR_ORG_ID`` env short-circuits the DB read entirely."""
    monkeypatch.setattr(audit, "_OPERATOR_ORG_ID", None)
    monkeypatch.setenv("OPERATOR_ORG_ID", "env-op-org")
    # Make any DB read explode, to prove the env path never touches it.
    import acb_graph

    def _boom():
        raise AssertionError("the env override must not read the database")

    monkeypatch.setattr(acb_graph, "get_session", _boom)
    assert audit._resolve_operator_org_id() == "env-op-org"


def test_operator_org_failed_lookup_is_not_cached(monkeypatch):
    """A failed/absent lookup returns None WITHOUT caching, so a transient outage
    at first-audit does not permanently disable the fallback."""
    monkeypatch.setattr(audit, "_OPERATOR_ORG_ID", None)
    monkeypatch.delenv("OPERATOR_ORG_ID", raising=False)
    import acb_graph

    def _boom_session():
        raise RuntimeError("audit db down")

    monkeypatch.setattr(acb_graph, "get_session", _boom_session)
    fake = _FakeLog()
    monkeypatch.setattr(audit, "_log", fake)

    assert audit._resolve_operator_org_id() is None
    assert audit._OPERATOR_ORG_ID is None, "a failed lookup must not be cached"
    assert any("operator_org_lookup_failed" in ev for ev, _ in fake.warnings)


# ==========================================================================
# 3. DB fixtures (R8).
# ==========================================================================
@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
    """Point the ``acb_graph`` SYNC engine at the dedicated phase-4 catalog as the
    non-priv ``acb_app_h3rls`` role, so ``get_session()`` / ``tenant_session()``
    run under FORCE RLS. Both call ``_session_factory()`` at open time, so
    rerouting that one attribute reroutes both. NullPool → every op is a fresh
    backend where an unbound GUC is genuinely NULL. Resets the operator-org cache
    so ``_resolve_operator_org_id`` reads THIS catalog's ``default`` org."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setattr(audit, "_OPERATOR_ORG_ID", None)
    try:
        yield promoted
    finally:
        eng.dispose()


def _count_visible_as(app_url, org: str, eid: uuid.UUID) -> int:
    """Rows with ``id == eid`` visible to a session bound to *org* (as the
    non-priv role, i.e. under FORCE RLS)."""
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(
                text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org},
            )
            return c.execute(
                text("SELECT count(*) FROM audit_event WHERE id = :i"),
                {"i": str(eid)},
            ).scalar_one()
    finally:
        eng.dispose()


def _stamped_org(admin_engine, eid: uuid.UUID) -> str | None:
    """The row's ``organization_id`` as seen by the RLS-bypassing admin — the
    ground truth of whether the row landed and how it was stamped."""
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT organization_id::text FROM audit_event WHERE id = :i"),
            {"i": str(eid)},
        ).first()
    return None if row is None else row[0]


# ==========================================================================
# 4. audit-event-write-bound-under-rls + fallback (R8 — real phase-4 Postgres).
# ==========================================================================
@_DB_GATE
class TestAuditWriteBoundUnderRls:
    """The R8 half: the REAL ``_persist`` under the phase-4 catalog."""

    def test_event_with_org_lands_bound_and_is_org_isolated(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + an event carrying org A: the row LANDS, is visible ONLY to
        org A, and carries org A. RED-on-removal: reverting to the unbound
        ``get_session()`` defaults ``organization_id`` to the unset GUC (NULL) →
        NOT-NULL/WITH-CHECK refusal → no row lands → this assertion fails."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        eid = uuid.uuid4()
        try:
            audit._persist(
                _event(id=eid, actor="agent:sales", action="approve",
                       target="deal:1", organization_id=p.org_a)
            )

            assert _stamped_org(p.admin_engine, eid) == p.org_a, (
                "the audit row did NOT land stamped org A — a swallowed RLS "
                "refusal, or the bind reverted to the unbound get_session"
            )
            assert _count_visible_as(p.app_url, p.org_a, eid) == 1, (
                "the row is not visible to its own tenant under FORCE RLS"
            )
            assert _count_visible_as(p.app_url, p.org_b, eid) == 0, (
                "org B saw org A's audit_event row — RLS isolation failed"
            )
        finally:
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM audit_event WHERE id = :i"), {"i": str(eid)},
                )

    def test_system_event_falls_back_to_operator_org(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + a tenant-less system event (no org on the event): the row
        LANDS under the operator/DEFAULT org (RETAINED, not refused, not lost),
        stamped that org — resolved from the RLS-EXEMPT ``organization`` table."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        with p.admin_engine.connect() as c:
            default_org = c.execute(
                text("SELECT id::text FROM organization WHERE slug = 'default'")
            ).scalar_one()
        assert default_org not in (p.org_a, p.org_b), (
            "the default org must be distinct from the two test tenants"
        )
        eid = uuid.uuid4()
        try:
            audit._persist(
                _event(id=eid, actor="system:cron", action="nightly_sweep",
                       target="job:1", organization_id=None)
            )

            assert _stamped_org(p.admin_engine, eid) == default_org, (
                "a tenant-less system event was NOT filed under the operator/"
                "default org — it was dropped or misfiled (Option-A violated)"
            )
            assert _count_visible_as(p.app_url, default_org, eid) == 1, (
                "the retained system row is not visible to the operator org"
            )
            assert _count_visible_as(p.app_url, p.org_a, eid) == 0, (
                "a system row leaked into a customer tenant"
            )
        finally:
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM audit_event WHERE id = :i"), {"i": str(eid)},
                )

    def test_swallowed_failure_still_leaves_no_row(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + neither an event org NOR a default org (monkeypatched away):
        ``_persist`` skips + logs and — crucially — NO row lands. Guards the
        swallow: a naive 'did not raise' test would pass even if a row leaked."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        monkeypatch.setattr(audit, "_resolve_operator_org_id", lambda: None)
        fake = _FakeLog()
        monkeypatch.setattr(audit, "_log", fake)
        p = graph_on_promoted
        eid = uuid.uuid4()
        try:
            audit._persist(_event(id=eid, organization_id=None))  # must not raise
            assert _stamped_org(p.admin_engine, eid) is None, (
                "a row landed despite no resolvable org — fail-closed broke"
            )
            assert any("skipped_no_org" in ev for ev, _ in fake.warnings)
        finally:
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM audit_event WHERE id = :i"), {"i": str(eid)},
                )

    def test_an_unbound_get_session_write_is_refused(
        self, graph_on_promoted,
    ):
        """R8 mechanism proof for RED-on-removal: the unbound ``get_session()``
        (the flag-OFF opener, and what a revert would restore) cannot write an
        ``audit_event`` row under the phase-4 catalog — ``organization_id``'s
        DEFAULT reads the unset GUC (NULL) and NOT NULL / WITH CHECK refuses — so
        ``test_event_with_org_lands_*`` is genuinely RED if the seam is reverted."""
        import acb_graph

        eid = uuid.uuid4()
        with pytest.raises(DBAPIError), acb_graph.get_session() as s:
            s.execute(
                text(
                    "INSERT INTO audit_event (id, actor, action, target) "
                    "VALUES (:i, 'a', 'x', 't')"
                ),
                {"i": str(eid)},
            )


# ==========================================================================
# 5. flag-OFF regression (R8 — a real row lands on the pre-phase-4 ladder).
# ==========================================================================
@_DB_GATE
class TestFlagOffWriteLandsUnbound:
    """flag OFF → ``_persist`` uses the unbound ``get_session()`` and a real row
    LANDS on the shared (pre-phase-4) ladder DB, byte-identical to today —
    ``audit_event`` there has no ``organization_id`` column and no RLS."""

    @pytest.fixture
    def graph_on_ladder(self, monkeypatch):
        from acb_graph import db as graph_db

        eng = create_engine(_URL, future=True, poolclass=NullPool)
        factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
        monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
        try:
            yield eng
        finally:
            eng.dispose()

    def test_flag_off_write_lands(self, graph_on_ladder, monkeypatch):
        monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
        eid = uuid.uuid4()
        try:
            audit._persist(
                _event(id=eid, actor="user:flagoff", action="draft_email",
                       target="deal:9")
            )
            with graph_on_ladder.connect() as c:
                row = c.execute(
                    text("SELECT actor, action FROM audit_event WHERE id = :i"),
                    {"i": str(eid)},
                ).first()
            assert row is not None, (
                "flag OFF did not land a row via get_session — a swallowed "
                "failure, not the byte-identical write today"
            )
            assert (row[0], row[1]) == ("user:flagoff", "draft_email")
        finally:
            with graph_on_ladder.begin() as c:
                c.execute(
                    text("DELETE FROM audit_event WHERE id = :i"), {"i": str(eid)},
                )
