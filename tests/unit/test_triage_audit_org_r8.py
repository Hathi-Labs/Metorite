"""Task 2173 — the email-triage audit row lands in the tenant of the mail.

``orchestrator.triage.email.classify`` records one ``audit_event`` row
(``action='email_triage'``) that carries the sender address and the subject of
the message. Before this fix the event carried NO ``organization_id``. With
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit.log._persist`` then files a
tenant-less event under the OPERATOR org (Option-A, fenced by
``test_acb_graph_audit_bind.py``). So the mail metadata of any tenant would
land in the operator org: a cross-tenant leak.

The fix: ``classify`` takes a required keyword ``organization_id``. The caller
passes the tenant that its run is bound to. The audit event carries it. A call
that asks for the audit row with no tenant raises before any work, so it can
never fall back to the operator org.

Fences (R7):

* ``triage-audit-row-in-mail-tenant`` (R8, phase-4 two-org catalog, non-priv
  role ``acb_app_h3rls``): triage for org B lands ONE row stamped org B, visible
  to org B only, and NO ``email_triage`` row for that message under the
  operator org. RED when the stamp is dropped from the event.
* ``triage-audit-tenantless-fails-closed`` (R8 + no-DB): ``organization_id=None``
  or ``""`` with ``audit=True`` raises ``ValueError``, ``record`` is never
  called, and no row lands in any org.
* ``organization_id`` is keyword-required: a call that omits it is a
  ``TypeError``, so no new caller can forget the tenant.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \
        uv run pytest tests/unit/test_triage_audit_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from orchestrator.triage import EmailMessage, classify
from orchestrator.triage import email as triage_email
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)


def _msg(message_id: str) -> EmailMessage:
    return EmailMessage(
        message_id=message_id,
        from_addr="buyer@org-b-customer.example",
        to_addrs=["sales@org-b.example"],
        subject="Quotation request for 100 units",
        snippet="",
        body="",
        received_at=datetime(2026, 11, 7, tzinfo=UTC),
        headers={},
    )


async def _classify_and_drain(msg: EmailMessage, **kw: object):
    """Run ``classify`` on a loop, then wait for the off-loop audit write."""
    decision = await classify(msg, **kw)  # type: ignore[arg-type]
    await audit.drain()
    return decision


# ==========================================================================
# 1. No DB: the keyword contract and the fail-closed refusal (always runs).
# ==========================================================================
def test_organization_id_is_a_required_keyword() -> None:
    """A caller that omits the tenant gets a ``TypeError``, not a silent
    fall-back to the operator org."""
    with pytest.raises(TypeError):
        asyncio.run(classify(_msg("m-kw")))  # type: ignore[call-arg]


@pytest.mark.parametrize("org", [None, ""])
def test_tenantless_audit_call_raises_and_records_nothing(monkeypatch, org) -> None:
    """``audit=True`` with no tenant raises ``ValueError`` and never reaches
    ``record`` — so ``_persist`` cannot file it under the operator org."""
    calls: list[object] = []
    monkeypatch.setattr(triage_email, "record", lambda ev: calls.append(ev))
    with pytest.raises(ValueError, match="organization_id"):
        asyncio.run(classify(_msg("m-none"), organization_id=org))
    assert calls == [], "a tenant-less triage call reached the audit seam"


def test_event_carries_the_passed_organization(monkeypatch) -> None:
    """The audit event carries the org the caller passed, on the EVENT."""
    calls: list[audit.AuditEvent] = []
    monkeypatch.setattr(triage_email, "record", lambda ev: calls.append(ev))
    asyncio.run(classify(_msg("m-ev"), organization_id="org-b-id"))
    assert len(calls) == 1
    assert calls[0].action == "email_triage"
    assert calls[0].organization_id == "org-b-id"


def test_audit_false_needs_no_tenant(monkeypatch) -> None:
    """An offline classification (no audit row) needs no tenant."""
    calls: list[object] = []
    monkeypatch.setattr(triage_email, "record", lambda ev: calls.append(ev))
    d = asyncio.run(classify(_msg("m-off"), organization_id=None, audit=False))
    assert d.label == "sales_lead"
    assert calls == []


# ==========================================================================
# 2. R8: the real ``_persist`` under the phase-4 catalog.
# ==========================================================================
@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
    """Point the ``acb_graph`` sync engine at the phase-4 catalog as the
    non-priv ``acb_app_h3rls`` role. Same shape as
    ``test_acb_graph_audit_bind.graph_on_promoted``."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setattr(audit, "_OPERATOR_ORG_ID", None)
    monkeypatch.delenv("OPERATOR_ORG_ID", raising=False)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    try:
        yield promoted
    finally:
        eng.dispose()


def _rows_by_org(admin_engine, target: str) -> list[str]:
    """``organization_id`` of every ``email_triage`` row for *target*, read by
    the RLS-bypassing admin: the ground truth."""
    with admin_engine.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text(
                    "SELECT organization_id::text FROM audit_event "
                    "WHERE action = 'email_triage' AND target = :t"
                ),
                {"t": target},
            )
        ]


def _visible_as(app_url, org: str, target: str) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return c.execute(
                text(
                    "SELECT count(*) FROM audit_event "
                    "WHERE action = 'email_triage' AND target = :t"
                ),
                {"t": target},
            ).scalar_one()
    finally:
        eng.dispose()


def _default_org(admin_engine) -> str:
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT id::text FROM organization WHERE slug = 'default'")
        ).scalar_one()


def _delete(admin_engine, target: str) -> None:
    with admin_engine.begin() as c:
        c.execute(
            text("DELETE FROM audit_event WHERE action = 'email_triage' AND target = :t"),
            {"t": target},
        )


@_DB_GATE
class TestTriageAuditOrgUnderRls:
    def test_triage_for_org_b_lands_in_org_b_not_operator(self, graph_on_promoted):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        mid = f"r8-{uuid.uuid4()}"
        target = f"email:{mid}"
        try:
            asyncio.run(_classify_and_drain(_msg(mid), organization_id=p.org_b))

            orgs = _rows_by_org(p.admin_engine, target)
            assert operator not in orgs, (
                "org B's mail metadata landed under the OPERATOR org — "
                "cross-tenant leak (the event carried no organization_id)"
            )
            assert orgs == [p.org_b], (
                f"expected ONE email_triage row stamped org B, got {orgs}"
            )
            assert _visible_as(p.app_url, p.org_b, target) == 1
            assert _visible_as(p.app_url, operator, target) == 0
            assert _visible_as(p.app_url, p.org_a, target) == 0
        finally:
            _delete(p.admin_engine, target)

    @pytest.mark.parametrize("org", [None, ""])
    def test_tenantless_call_fails_closed_and_writes_no_row(
        self, graph_on_promoted, org,
    ):
        p = graph_on_promoted
        mid = f"r8-none-{uuid.uuid4()}"
        target = f"email:{mid}"
        try:
            with pytest.raises(ValueError, match="organization_id"):
                asyncio.run(_classify_and_drain(_msg(mid), organization_id=org))
            assert _rows_by_org(p.admin_engine, target) == [], (
                "a tenant-less triage call wrote an audit row (the operator "
                "org fallback took it) — fail-closed broke"
            )
        finally:
            _delete(p.admin_engine, target)
