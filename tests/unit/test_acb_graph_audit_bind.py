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

**Task 2221 slice 3 (owner answer A) narrows Option-A to a MARKED event.** An
``AuditEvent`` carries ``system: bool = False``. With the bind on,
``acb_audit.record`` refuses a tenant-less event with no marker: it logs
``audit.refused_unmarked_tenantless`` (the action, the actor and the writer
module, never the target or the payload), it does not raise, and it never
reaches ``_persist``. ``_persist`` also takes the operator org only for a
marked event, so a direct caller cannot misfile a row either. With the bind
off, every event is written as before. Sections 1b and 4b hold the fences.

Fences (R7):

* ``audit-event-write-bound-under-rls`` (R8, phase-4 two-org catalog, non-priv
  role ``acb_app_h3rls``): flag ON + an event carrying org A → the row LANDS,
  is visible ONLY to org A, and is stamped org A. RED-on-removal: reverting
  ``_persist`` to the unbound ``get_session()`` under the phase-4 catalog is
  NOT-NULL / RLS refused (``test_an_unbound_get_session_write_is_refused``), so
  the row never lands and the visibility assertion goes RED.
* ``audit-system-event-falls-back-to-operator-org`` (R8): flag ON + a MARKED
  event with NO org → the row LANDS under the operator/DEFAULT org (retained,
  not refused), stamped that org.
* ``audit-unmarked-tenantless-refused`` (R8, slice 3): flag ON + ``record`` of
  an unmarked event with no org → no row in any org. A marked event through
  ``record`` lands under the operator org, and org B cannot read it.
* flag-OFF regression: ``_persist`` opens ``get_session()`` unchanged and a real
  row lands on the pre-phase-4 shared ladder, byte-identical to today.
* the flag → opener decision (no DB): OFF → ``get_session``; ON + event org →
  ``tenant_session(event org)``; ON + no event org → ``tenant_session(operator
  org)`` for a marked event; ON + an unmarked event with no org, or neither
  org → skip + log, no session opened.

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
    """flag ON + a tenant-less MARKED system event → ``_persist`` binds the
    OPERATOR/DEFAULT org (Option-A) so the row is retained — it must NOT skip
    when a fallback exists."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    monkeypatch.setattr(audit, "_resolve_operator_org_id", lambda: "op-org")

    audit._persist(_event(organization_id=None, system=True))

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

    audit._persist(_event(organization_id=None, system=True))

    assert spy.opened == [], "no org anywhere must open NO session, not an unbound one"
    assert any("skipped_no_org" in ev for ev, _ in fake.warnings), (
        f"the fail-closed skip must be LOGGED: {[e for e, _ in fake.warnings]}"
    )


def test_flag_on_unmarked_tenantless_event_at_persist_is_skipped(monkeypatch):
    """Slice 3, the second layer: flag ON + a tenant-less event with NO marker
    that reaches ``_persist`` directly → no session, and the skip is logged.
    The operator org is never resolved for it."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")

    def _no_lookup() -> str:
        raise AssertionError("an unmarked event must not reach the operator org")

    monkeypatch.setattr(audit, "_resolve_operator_org_id", _no_lookup)

    audit._persist(_event(organization_id=None))

    assert spy.opened == [], "an unmarked tenant-less event opened a session"
    assert any("skipped_no_org" in ev for ev, _ in fake.warnings)


# ==========================================================================
# 1b. Task 2221 slice 3: ``record`` refuses an unmarked tenant-less event
#     (no DB; always runs).
# ==========================================================================
REFUSED = "audit.refused_unmarked_tenantless"
#: Values that stand for tenant data. The refusal line must hold none of them.
SECRET_TARGET = "gmail:asha@orgb.test"
SECRET_PAYLOAD = {"error": "org B deal Acme owes 12 lakh"}


class _RecLog(_FakeLog):
    """A logger that also keeps each ``info`` line."""

    def __init__(self) -> None:
        super().__init__()
        self.infos: list[tuple[str, dict]] = []

    def info(self, event: str, **kw: object) -> None:
        self.infos.append((event, kw))


def _install_record_spy(monkeypatch) -> tuple[list, _RecLog]:
    persisted: list[audit.AuditEvent] = []
    fake = _RecLog()
    monkeypatch.setattr(audit, "_persist", persisted.append)
    monkeypatch.setattr(audit, "_log", fake)
    return persisted, fake


def _tenant_event(**kw: object) -> audit.AuditEvent:
    return _event(target=SECRET_TARGET, payload=dict(SECRET_PAYLOAD), **kw)


def _assert_refusal_logged(fake: _RecLog) -> None:
    hits = [kw for ev, kw in fake.warnings if ev == REFUSED]
    assert len(hits) == 1, f"the refusal was not logged once: {fake.warnings}"
    [kw] = hits
    assert set(kw) == {"action", "actor", "writer"}, sorted(kw)
    assert kw["action"] == "slice5"
    assert kw["writer"] == __name__, (
        "the refusal line must name the module that called record"
    )
    text_of = repr(kw)
    assert SECRET_TARGET not in text_of and "Acme" not in text_of, (
        "the refusal line holds the target or the payload"
    )
    assert not any(ev == "audit" for ev, _ in fake.infos), (
        "a refused event still logged its payload line"
    )


def test_record_refuses_an_unmarked_tenantless_event_with_the_bind_on(monkeypatch):
    """flag ON + no org + no marker → refused: logged, no raise, and
    ``_persist`` is never reached. RED when the refusal is dropped."""
    persisted, fake = _install_record_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")

    audit.record(_tenant_event(organization_id=None))  # must not raise

    assert persisted == [], "an unmarked tenant-less event reached _persist"
    _assert_refusal_logged(fake)


def test_record_refuses_on_a_running_event_loop_too(monkeypatch):
    """The async path: the refusal runs before the thread hop, so no write is
    queued in ``_PENDING`` and the caller gets no error."""
    import asyncio

    persisted, fake = _install_record_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    before = set(audit._PENDING)

    async def _go() -> int:
        audit.record(_tenant_event(organization_id=None))
        queued = len(set(audit._PENDING) - before)
        await audit.drain()
        return queued

    assert asyncio.run(_go()) == 0, "a refused event queued a write"
    assert persisted == []
    _assert_refusal_logged(fake)


def test_a_broken_logger_does_not_raise_into_the_writer(monkeypatch):
    """The refusal log line itself must never raise into the caller."""
    persisted: list = []
    monkeypatch.setattr(audit, "_persist", persisted.append)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")

    class _Boom:
        def warning(self, *_a: object, **_k: object) -> None:
            raise RuntimeError("log sink down")

        def info(self, *_a: object, **_k: object) -> None:
            raise AssertionError("a refused event must not log its payload line")

    monkeypatch.setattr(audit, "_log", _Boom())
    audit.record(_tenant_event(organization_id=None))
    assert persisted == []


def test_record_persists_a_marked_system_event_with_the_bind_on(monkeypatch):
    """flag ON + no org + ``system=True`` → it reaches ``_persist``, and
    ``_persist`` binds the operator org. RED when marked events are refused."""
    spy, _ = _install_spy(monkeypatch)
    rec = _RecLog()
    monkeypatch.setattr(audit, "_log", rec)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    monkeypatch.setattr(audit, "_resolve_operator_org_id", lambda: "op-org")

    audit.record(_event(organization_id=None, system=True))

    assert spy.opened == [("tenant", "op-org")], (
        "a marked system event must land under the operator org"
    )
    assert not any(ev == REFUSED for ev, _ in rec.warnings)


@pytest.mark.parametrize("system", [False, True], ids=["unmarked", "marked"])
def test_record_with_the_bind_off_persists_as_today(monkeypatch, system):
    """flag OFF → both a marked and an unmarked tenant-less event are written
    through the unbound ``get_session``, exactly as before slice 3."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)

    audit.record(_tenant_event(organization_id=None, system=system))

    assert spy.opened == [("get", None)]
    assert not any(ev == REFUSED for ev, _ in fake.warnings)


@pytest.mark.parametrize("system", [False, True], ids=["unmarked", "marked"])
def test_record_with_an_org_is_unchanged(monkeypatch, system):
    """flag ON + an event with an org → its own org, whatever the marker."""
    spy, fake = _install_spy(monkeypatch)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")

    audit.record(_event(organization_id="org-A", system=system))

    assert spy.opened == [("tenant", "org-A")]
    assert not any(ev == REFUSED for ev, _ in fake.warnings)


def test_the_marker_defaults_to_off() -> None:
    """A writer that says nothing is a tenant writer, never a system one."""
    assert audit.AuditEvent(actor="a", action="b", target="c").system is False


@pytest.fixture
def no_graph(monkeypatch):
    """``acb_graph`` cannot be imported, and the bind env is unset.

    The refusal must then fail closed. A ``None`` entry in ``sys.modules``
    makes ``from acb_graph import ...`` raise ``ImportError``. The real
    ``_persist`` imports ``acb_graph`` too, so a spy stands in for it. Here
    "persists" means "reaches ``_persist``".
    """
    import sys

    monkeypatch.setitem(sys.modules, "acb_graph", None)
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    with pytest.raises(ImportError):
        from acb_graph import tenant_bind_enabled  # noqa: F401


def test_with_no_db_layer_an_unmarked_tenantless_event_is_refused(monkeypatch, no_graph):
    """The ``except`` branch of ``_refuse_unmarked`` sets the bind on, so the
    event is refused and logged. RED when that branch answers ``bind = False``."""
    persisted, fake = _install_record_spy(monkeypatch)

    audit.record(_tenant_event(organization_id=None))  # must not raise

    assert persisted == [], "with no DB layer an unmarked event reached _persist"
    _assert_refusal_logged(fake)


@pytest.mark.parametrize("kw", [
    {"organization_id": None, "system": True},
    {"organization_id": "org-A"},
], ids=["marked", "with-org"])
def test_with_no_db_layer_a_marked_or_owned_event_reaches_persist(
    monkeypatch, no_graph, kw,
):
    """The fail-closed branch refuses only the unmarked tenant-less event."""
    persisted, fake = _install_record_spy(monkeypatch)

    audit.record(_event(**kw))

    assert len(persisted) == 1, "a marked or owned event was refused"
    assert not any(ev == REFUSED for ev, _ in fake.warnings)


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
                       target="job:1", organization_id=None, system=True)
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
            # must not raise
            audit._persist(_event(id=eid, organization_id=None, system=True))
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
# 4b. Task 2221 slice 3 through ``record`` (R8 — real phase-4 Postgres).
# ==========================================================================
def _rows_in_any_org(admin_engine, eid: uuid.UUID) -> int:
    """Rows with ``id == eid`` in ANY org, as the RLS-bypassing admin."""
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT count(*) FROM audit_event WHERE id = :i"), {"i": str(eid)},
        ).scalar_one()


@_DB_GATE
def test_a_refused_push_on_a_sub_run_lands_under_the_parent_org(
    graph_on_promoted, monkeypatch,
):
    """Review P1 of slice 3, on the real table. A delegated sub-run calls
    ``_settle_agent_push`` with no ``thread_id``. The refused push row must
    land under the org of the parent run, and in no other org. RED when the
    row takes its org only from ``_RUN_ORG.get(thread_id)``: ``record``
    then refuses it, and no row lands."""
    import asyncio

    import acb_skills.permission_policy as pp
    import orchestrator.executor as executor

    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    p = graph_on_promoted
    run_id = f"run-sub-{uuid.uuid4()}"
    monkeypatch.setattr(pp, "take_agent_push_request", lambda _r: True)

    async def _gate() -> tuple[bool, str]:
        return False, "not a reviewer"

    monkeypatch.setattr(executor, "_agent_push_allowed", _gate)
    monkeypatch.setitem(executor._RUN_ORG, "t-parent-r8", p.org_a)

    async def _sub_run() -> None:
        token = executor._stream_relay_thread_id.set("t-parent-r8")
        try:
            await executor._settle_agent_push("agent-x", None, run_id, since_sha=None)
        finally:
            executor._stream_relay_thread_id.reset(token)
        await audit.drain()

    asyncio.run(_sub_run())
    with p.admin_engine.connect() as c:
        orgs = [r[0] for r in c.execute(text(
            "SELECT organization_id::text FROM audit_event "
            "WHERE action = 'agent_push_request_refused' AND payload->>'run_id' = :r"),
            {"r": run_id})]
    assert orgs == [p.org_a], f"the sub-run's refused push landed as {orgs}"


@_DB_GATE
class TestRecordRefusesUnmarkedUnderRls:
    def test_an_unmarked_tenantless_record_leaves_no_row(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + ``record`` of an unmarked event with no org → no row in any
        org, and the refusal is logged. RED when the refusal and the
        ``_persist`` gate are both dropped: the row lands under the operator org."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        fake = _FakeLog()
        monkeypatch.setattr(audit, "_log", fake)
        p = graph_on_promoted
        eid = uuid.uuid4()
        try:
            audit.record(
                _event(id=eid, actor="job:new-writer", action="forgot_org",
                       target="deal:org-b", payload={"note": "org B text"})
            )
            assert _rows_in_any_org(p.admin_engine, eid) == 0, (
                "an unmarked tenant-less event wrote a row — the operator org "
                "took a row that a writer forgot to stamp"
            )
            assert any(ev == "audit.refused_unmarked_tenantless"
                       for ev, _ in fake.warnings), fake.warnings
        finally:
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM audit_event WHERE id = :i"), {"i": str(eid)},
                )

    def test_a_marked_system_record_lands_under_the_operator_org_only(
        self, graph_on_promoted, monkeypatch,
    ):
        """flag ON + ``record`` of a marked system event → the row lands under
        the operator org, and neither org A nor org B can read it. RED when
        ``record`` refuses marked events too."""
        monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
        p = graph_on_promoted
        with p.admin_engine.connect() as c:
            default_org = c.execute(
                text("SELECT id::text FROM organization WHERE slug = 'default'")
            ).scalar_one()
        eid = uuid.uuid4()
        try:
            audit.record(
                _event(id=eid, actor="job:scheduler", action="scheduler_started",
                       target="scheduler", organization_id=None, system=True)
            )
            assert _stamped_org(p.admin_engine, eid) == default_org, (
                "a marked system event did not land under the operator org"
            )
            assert _count_visible_as(p.app_url, default_org, eid) == 1
            assert _count_visible_as(p.app_url, p.org_b, eid) == 0, (
                "org B read a system row of the operator org"
            )
            assert _count_visible_as(p.app_url, p.org_a, eid) == 0
        finally:
            with p.admin_engine.begin() as c:
                c.execute(
                    text("DELETE FROM audit_event WHERE id = :i"), {"i": str(eid)},
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

        from tests.unit._tenant_ladder import apply_ladder

        eng = create_engine(_URL, future=True, poolclass=NullPool)
        # Build the base (pre-phase-4) tenant schema on _URL the CANONICAL way.
        # ``apply_ladder`` applies 01_schema.sql (uuid-ossp + vector + the core
        # tables incl. ``audit_event``) on an EMPTY database and the idempotent
        # numbered ladder otherwise, so this flag-OFF fence is hermetic across
        # environments (CI's unit-test DB does not pre-build the schema).
        # ⚠️ Do NOT partially create just ``audit_event`` here: a non-empty DB
        # makes ``apply_ladder`` SKIP 01_schema (it is init-only, not re-runnable),
        # so uuid-ossp is never created and LATER suites sharing this DB (e.g.
        # ``test_app_user_upserts``) fail on ``uuid_generate_v4()``.
        with eng.begin() as conn:
            apply_ladder(conn)
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
