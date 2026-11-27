"""Task 2189 (1 of 3) — each Action Broker audit row lands in the bound tenant.

``action_broker.broker`` records eight kinds of ``audit_event`` row:
``propose:<action>`` (it spreads the caller payload), ``execute:<action>``,
two ``execute_refused:<action>``, ``enqueue:<action>``,
``enqueue_failed:<action>``, ``<act>_refused`` (no person present) and
``action_rejected``. Before this fix no event carried an
``organization_id``. With ``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit.log
._persist`` then files each one under the OPERATOR org (Option-A, fenced by
``test_acb_graph_audit_bind.py``). So the action and the payload of any
tenant landed in the operator org: a cross-tenant leak.

The fix: ``broker._audit`` stamps ``organization_id`` from the BOUND tenant
(``acb_common.db.current_tenant()``), the same value that the queue's own
``tenant_session`` binds. It never reads a payload field. With no bound tenant
and the flag on, ``acb_audit.refuse_tenantless`` skips the row and logs
``audit.skipped_no_tenant``, so nothing reaches the operator org.

Fences (R7):

* ``broker-audit-row-in-bound-tenant`` (R8, phase-4 two-org catalog, non-priv
  role ``acb_app_h3rls``): a propose, an enqueue, an approve, a refusal and a
  reject under org B land stamped org B, visible to org B only, and none under
  the operator org. RED when the stamp is dropped.
* ``broker-audit-tenantless-skips`` (R8 + no-DB): with no bound tenant and the
  flag on, ``record`` is never called and no row lands in any org.
* A caller payload that names another org does not move the row.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_action_broker_audit_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from action_broker import broker
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    """Bind *org* the way the auth dependency does, or clear the scope."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture
def events(monkeypatch) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(broker, "record", calls.append)
    broker.clear_action_handlers()
    yield calls
    broker.clear_action_handlers()


def _propose(authority=broker.AuthorityTier.SUGGEST, action="t2189.write",
             target="deal:t2189", payload=None):
    return broker.propose("agent:t2189", action, target,
                          payload if payload is not None else {"body": "hi"},
                          authority=authority)


async def _ok_handler(_p):
    return "written"


# ==========================================================================
# 0. No DB: the shared check ``acb_audit.refuse_tenantless``.
# ==========================================================================
def test_refuse_tenantless_keeps_an_event_with_an_org(monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    ev = audit.AuditEvent(actor="a", action="x", target="t", organization_id="o")
    assert audit.refuse_tenantless(ev) is False


@pytest.mark.parametrize("org", [None, ""])
def test_refuse_tenantless_refuses_no_org_with_the_bind_on(monkeypatch, org) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    ev = audit.AuditEvent(actor="a", action="x", target="t", organization_id=org)
    assert audit.refuse_tenantless(ev) is True


def test_refuse_tenantless_keeps_no_org_with_the_bind_off(monkeypatch) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    ev = audit.AuditEvent(actor="a", action="x", target="t")
    assert audit.refuse_tenantless(ev) is False


# ==========================================================================
# 1. No DB: each site stamps the bound tenant.
# ==========================================================================
def test_propose_stamps_the_bound_tenant_not_the_payload(events) -> None:
    with _as_tenant("org-b-id"):
        _propose(payload={"organization_id": "org-a-id", "body": "hi"})
    assert [e.action for e in events] == ["propose:t2189.write"]
    assert events[0].organization_id == "org-b-id"


def test_each_execute_site_stamps_the_bound_tenant(events) -> None:
    broker.register_action_handler("t2189.ok", _ok_handler)
    with _as_tenant("org-b-id"):
        rejected = _propose(authority=broker.AuthorityTier.READ)
        no_handler = _propose(authority=broker.AuthorityTier.AUTONOMOUS)
        handled = _propose(authority=broker.AuthorityTier.AUTONOMOUS,
                           action="t2189.ok")
        events.clear()
        asyncio.run(broker.execute(rejected))
        asyncio.run(broker.execute(no_handler))
        asyncio.run(broker.execute(handled))
    assert [e.action for e in events] == [
        "execute_refused:t2189.write",
        "execute_refused:t2189.write",
        "execute:t2189.ok",
    ]
    assert {e.organization_id for e in events} == {"org-b-id"}


def test_enqueue_failure_stamps_the_bound_tenant(events, monkeypatch) -> None:
    import acb_graph

    def _boom(_org):
        raise RuntimeError("db down")

    monkeypatch.setattr(acb_graph, "tenant_session", _boom)
    with _as_tenant("org-b-id"):
        p = _propose()
        events.clear()
        assert broker.enqueue(p) is None
    assert [e.action for e in events] == ["enqueue_failed:t2189.write"]
    assert events[0].organization_id == "org-b-id"


def test_no_person_refusal_stamps_the_bound_tenant(events) -> None:
    from acb_common.no_person import no_person_scope

    with _as_tenant("org-b-id"), no_person_scope():
        broker.reject("a-id", "alice@b.test")
        asyncio.run(broker.approve("a-id", "alice@b.test"))
    assert [e.action for e in events] == ["reject_refused", "approve_refused"]
    assert {e.organization_id for e in events} == {"org-b-id"}


def test_no_tenant_with_the_bind_on_records_nothing(events, monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant(None):
        p = _propose(authority=broker.AuthorityTier.READ)
        asyncio.run(broker.execute(p))
    assert events == [], "a tenant-less broker event reached the audit seam"


def test_no_tenant_with_the_bind_off_keeps_the_old_row(events, monkeypatch) -> None:
    """Pre-phase-4 ``audit_event`` has no tenant column: nothing can leak."""
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    with _as_tenant(None):
        _propose()
    assert [e.organization_id for e in events] == [None]


# ==========================================================================
# 2. R8: the real broker and the real ``_persist`` under the phase-4 catalog.
# ==========================================================================
@pytest.fixture
def broker_on_promoted(promoted, monkeypatch):  # noqa: F811
    """The broker and ``acb_audit`` open ``acb_graph`` sessions as the
    non-priv ``acb_app_h3rls`` role, with the tenant bind on."""
    from acb_graph import db as graph_db

    dsn = promoted.app_url.render_as_string(hide_password=False)
    eng = create_engine(dsn, future=True, poolclass=NullPool)
    factory = sessionmaker(bind=eng, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    monkeypatch.setattr(audit, "_OPERATOR_ORG_ID", None)
    monkeypatch.delenv("OPERATOR_ORG_ID", raising=False)
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    broker.clear_action_handlers()
    try:
        yield promoted
    finally:
        broker.clear_action_handlers()
        eng.dispose()


def _rows(admin_engine, targets: list[str]) -> list[tuple[str, str]]:
    """``(action, organization_id)`` of each row for *targets*, read by the
    RLS-bypassing admin: the ground truth."""
    with admin_engine.connect() as c:
        return sorted(
            (r[0], r[1])
            for r in c.execute(
                text(
                    "SELECT action, organization_id::text FROM audit_event "
                    "WHERE target = ANY(:t)"
                ),
                {"t": targets},
            )
        )


def _visible_as(app_url, org: str, targets: list[str]) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return c.execute(
                text("SELECT count(*) FROM audit_event WHERE target = ANY(:t)"),
                {"t": targets},
            ).scalar_one()
    finally:
        eng.dispose()


def _default_org(admin_engine) -> str:
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT id::text FROM organization WHERE slug = 'default'")
        ).scalar_one()


def _delete(admin_engine, targets: list[str]) -> None:
    with admin_engine.begin() as c:
        c.execute(text("DELETE FROM audit_event WHERE target = ANY(:t)"), {"t": targets})
        c.execute(
            text("DELETE FROM pending_actions WHERE target = ANY(:t)"),
            {"t": targets},
        )


async def _drained(coro):
    """Run a broker coroutine, then wait for its off-loop audit writes."""
    res = await coro
    await audit.drain()
    return res


@_DB_GATE
class TestBrokerAuditOrgUnderRls:
    def test_every_broker_row_for_org_b_lands_in_org_b(self, broker_on_promoted):
        from acb_common.no_person import no_person_scope

        p = broker_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        tag = uuid.uuid4().hex[:8]
        approved_t = f"deal:t2189-ap-{tag}"
        rejected_t = f"deal:t2189-rj-{tag}"
        refused_t = f"deal:t2189-nh-{tag}"
        broker.register_action_handler("t2189.ok", _ok_handler)
        rj_id = ""
        try:
            with _as_tenant(p.org_b):
                ap = _propose(action="t2189.ok", target=approved_t,
                              payload={"organization_id": p.org_a})
                ap_id = broker.enqueue(ap)
                assert ap_id is not None
                res = asyncio.run(_drained(broker.approve(ap_id, "bob@b.test")))
                assert res["ok"] is True, res

                rj = _propose(target=rejected_t)
                rj_id = broker.enqueue(rj)
                assert broker.reject(rj_id, "bob@b.test")["ok"] is True
                with no_person_scope():
                    broker.reject(rj_id, "bob@b.test")

                nh = _propose(authority=broker.AuthorityTier.AUTONOMOUS,
                              target=refused_t)
                asyncio.run(_drained(broker.execute(nh)))
            targets = [approved_t, rejected_t, refused_t]
            rows = _rows(p.admin_engine, targets)
            actions = {a for a, _ in rows}
            assert {
                "propose:t2189.ok", "enqueue:t2189.ok", "execute:t2189.ok",
                "propose:t2189.write", "enqueue:t2189.write",
                "execute_refused:t2189.write",
            } <= actions, rows
            orgs = {o for _, o in rows}
            assert operator not in orgs, (
                "org B's broker rows landed under the OPERATOR org — "
                f"cross-tenant leak (the event carried no organization_id): {rows}"
            )
            assert orgs == {p.org_b}, f"expected every row stamped org B, got {rows}"
            # ``action_rejected`` and ``reject_refused`` target the row id.
            id_rows = _rows(p.admin_engine, [rj_id])
            assert {a for a, _ in id_rows} == {"action_rejected", "reject_refused"}
            assert {o for _, o in id_rows} == {p.org_b}, id_rows

            all_t = [*targets, rj_id]
            assert _visible_as(p.app_url, p.org_b, all_t) == len(rows) + len(id_rows)
            assert _visible_as(p.app_url, operator, all_t) == 0
            assert _visible_as(p.app_url, p.org_a, all_t) == 0
        finally:
            _delete(p.admin_engine, [approved_t, rejected_t, refused_t, rj_id])

    def test_a_tenantless_propose_writes_no_row(self, broker_on_promoted):
        p = broker_on_promoted
        target = f"deal:t2189-none-{uuid.uuid4().hex[:8]}"
        try:
            with _as_tenant(None):
                nh = _propose(authority=broker.AuthorityTier.READ, target=target)
                asyncio.run(_drained(broker.execute(nh)))
            assert _rows(p.admin_engine, [target]) == [], (
                "a tenant-less broker event wrote an audit row (the operator "
                "org fallback took it) — fail-closed broke"
            )
        finally:
            _delete(p.admin_engine, [target])


def test_only_the_audit_helper_calls_record() -> None:
    """Fence ``broker-audit-has-one-record-call`` (R7, task 2189 review P2).

    The ``events`` fixture patches ``broker.record`` and drives only today's
    sites, so a NEW ``record(AuditEvent(...))`` in broker.py would stay green.
    This scan fails on it: ``broker._audit`` is the one function that may call
    ``record``, because it stamps the bound tenant and asks
    ``refuse_tenantless`` first. Any other call is a tenant-less audit row.
    """
    import ast
    import pathlib

    from action_broker import broker

    tree = ast.parse(pathlib.Path(broker.__file__).read_text(encoding="utf-8"))
    callers: list[str] = []

    class Scan(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name == "record":
                callers.append(self.stack[-1] if self.stack else "<module>")
            self.generic_visit(node)

    Scan().visit(tree)
    assert callers == ["_audit"], (
        "broker.py may call record() only inside _audit, which stamps the bound "
        f"tenant and asks refuse_tenantless first. Calls found in: {callers}"
    )
