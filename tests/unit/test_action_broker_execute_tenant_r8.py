"""Task 2220 — ``broker.execute`` runs no handler without a bound tenant.

A registered handler is an OUTWARD write: a mail, a CRM push, a broadcast.
Since task 2189, ``broker._audit`` skips the ``execute:<action>`` row when no
tenant is bound and ``ACB_GRAPH_TENANT_BIND`` is on. Before this fix
``execute`` then ran the handler all the same, so an outward write had only
the ``audit.skipped_no_tenant`` log line as its trace. That breaks the rule
that an executed act has an audit row (``core_brief.md`` §8).

The fix:

* ``execute`` asks ``acb_audit.refuse_tenantless`` first, with the event of
  its refusal. When that event would be skipped (the bind on and no tenant),
  ``execute`` runs nothing and answers ``status: refused``,
  ``reason: tenant_unbound``. With the bind off it runs as before: the
  pre-phase-4 ``audit_event`` has no tenant column, so nothing can leak.
* ``approve`` passes ``reviewed_by`` (the session's member, never a payload
  field) to ``execute``, so the ``execute:<action>`` row carries
  ``payload.member``. ``submit``'s auto-apply path has no reviewer, so its row
  has no ``member`` key: that act is not a member's act.

⚠️ **Two bindings, one that ``execute`` can see.** ``bind_tenant`` sets the
``acb_common.db`` ContextVar that ``current_tenant()`` reads. The two
``tenant_session(org)`` seams (``acb_common.db`` async and ``acb_graph`` sync)
set only the ``app.tenant_id`` GUC on their own connection. They never touch
the ContextVar. So a caller that binds only through ``tenant_session(org)`` is
tenant-less to the broker, and ``execute`` refuses it. Section 1 proves it for
both seams.

Fences (R7):

* ``broker-execute-refuses-tenantless`` (no DB + R8): with the bind on and no
  tenant, the handler is not called and the pending row stays ``pending``.
  RED when the check in ``execute`` is dropped.
* ``broker-execute-row-has-reviewer`` (R8): an approve by org B's reviewer
  writes ``execute:<a>`` in org B with ``payload.member`` = the reviewer.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_action_broker_execute_tenant_r8.py -v -rs
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

ACTION = "t2220.write"


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    """Bind *org* the way the auth dependency does, or clear the scope."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


class _Recorder:
    """A handler that records each call: the outward write under test."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def __call__(self, proposal: Any) -> str:
        self.calls.append(proposal)
        return "written"


@pytest.fixture
def events(monkeypatch) -> Iterator[list[Any]]:
    calls: list[Any] = []
    monkeypatch.setattr(broker, "record", calls.append)
    broker.clear_action_handlers()
    yield calls
    broker.clear_action_handlers()


@pytest.fixture
def handler() -> _Recorder:
    rec = _Recorder()
    broker.register_action_handler(ACTION, rec)
    return rec



def _proposal(authority=broker.AuthorityTier.AUTONOMOUS, target="deal:t2220"):
    return broker.ActionProposal(
        id=uuid.uuid4(), actor="agent:t2220", action=ACTION, target=target,
        payload={"body": "hi"}, authority=authority, destructive=True,
        disposition=broker.decide_disposition(authority, destructive=True),
    )


# ==========================================================================
# 0. No DB: the check in ``execute``.
# ==========================================================================
def test_bind_on_and_no_tenant_runs_no_handler(events, handler, monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant(None):
        res = asyncio.run(broker.execute(_proposal()))
    assert handler.calls == [], "a tenant-less execute ran the outward write"
    assert res["ok"] is False
    assert res["status"] == "refused"
    assert res["reason"] == "tenant_unbound"
    assert res["error"]
    assert events == [], "a tenant-less event reached the audit seam"


def test_bind_on_and_no_tenant_submit_auto_apply_runs_nothing(
    events, handler, monkeypatch,
) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant(None):
        res = asyncio.run(broker.submit(_proposal()))
    assert handler.calls == []
    assert res["status"] == "refused"
    assert res["reason"] == "tenant_unbound"


def test_bind_off_and_no_tenant_still_runs(events, handler, monkeypatch) -> None:
    """Pre-phase-4: ``audit_event`` has no tenant column, the row is kept."""
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    with _as_tenant(None):
        res = asyncio.run(broker.execute(_proposal()))
    assert res == {"ok": True, "result": "written"}
    assert len(handler.calls) == 1
    assert [e.action for e in events] == [f"execute:{ACTION}"]


def test_a_bound_tenant_runs_and_the_row_has_the_org(events, handler, monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant("org-b-id"):
        res = asyncio.run(broker.execute(_proposal()))
    assert res == {"ok": True, "result": "written"}
    assert len(handler.calls) == 1
    assert [(e.action, e.organization_id) for e in events] == [
        (f"execute:{ACTION}", "org-b-id"),
    ]


def test_execute_stamps_the_member_it_is_given(events, handler) -> None:
    with _as_tenant("org-b-id"):
        asyncio.run(broker.execute(_proposal(), member="bob@b.test"))
    assert events[0].payload["member"] == "bob@b.test"


def test_auto_apply_has_no_member(events, handler) -> None:
    """No person decided an auto-applied act, so it names no member."""
    with _as_tenant("org-b-id"):
        res = asyncio.run(broker.submit(_proposal()))
    assert res["status"] == "applied"
    assert "member" not in events[0].payload


def test_approve_passes_the_reviewer_not_the_payload(events, handler, monkeypatch) -> None:
    p = _proposal(authority=broker.AuthorityTier.SUGGEST)
    p.payload = {"member": "mallory@a.test"}
    monkeypatch.setattr(broker, "_load_proposal", lambda _id: (p, "pending"))
    monkeypatch.setattr(broker, "_mark", lambda *a, **k: 1)
    with _as_tenant("org-b-id"):
        res = asyncio.run(broker.approve(str(p.id), "bob@b.test"))
    assert res["ok"] is True, res
    rows = [e for e in events if e.action == f"execute:{ACTION}"]
    assert [e.payload["member"] for e in rows] == ["bob@b.test"]


# ==========================================================================
# 1. No DB: a ``tenant_session(org)`` alone is not a broker binding.
# ==========================================================================
class _SyncFake:
    def begin(self): ...
    def execute(self, *a, **k): ...
    def commit(self): ...
    def rollback(self): ...
    def close(self): ...


class _AsyncFake:
    async def begin(self): ...
    async def execute(self, *a, **k): ...
    async def commit(self): ...
    async def rollback(self): ...
    async def close(self): ...


def test_acb_graph_tenant_session_alone_is_refused(events, handler, monkeypatch) -> None:
    """The sync seam sets the GUC only: ``current_tenant()`` stays empty."""
    from acb_common.db import current_tenant
    from acb_graph import db as graph_db

    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    monkeypatch.setattr(graph_db, "_session_factory", lambda: _SyncFake)
    with _as_tenant(None), graph_db.tenant_session("org-b-id"):
        assert current_tenant() is None
        res = asyncio.run(broker.execute(_proposal()))
    assert handler.calls == []
    assert res["reason"] == "tenant_unbound"


def test_acb_common_tenant_session_alone_is_refused(events, handler, monkeypatch) -> None:
    """The async seam sets the GUC only: ``current_tenant()`` stays empty."""
    from acb_common import db as common_db

    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    monkeypatch.setattr(common_db, "get_session_factory", lambda: _AsyncFake)

    async def run() -> dict[str, Any]:
        async with common_db.tenant_session("org-b-id"):
            assert common_db.current_tenant() is None
            return await broker.execute(_proposal())

    with _as_tenant(None):
        res = asyncio.run(run())
    assert handler.calls == []
    assert res["reason"] == "tenant_unbound"


# ==========================================================================
# 2. R8: the real broker, the real queue and ``_persist`` under phase 4.
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


def _rows(admin_engine, targets: list[str]) -> list[tuple[str, str, str | None]]:
    """``(action, organization_id, payload.member)`` per row, read by the
    RLS-bypassing admin: the ground truth."""
    with admin_engine.connect() as c:
        return sorted(
            (r[0], r[1], r[2])
            for r in c.execute(
                text(
                    "SELECT action, organization_id::text, payload->>'member' "
                    "FROM audit_event WHERE target = ANY(:t)"
                ),
                {"t": targets},
            )
        )


def _status(admin_engine, action_id: str) -> str:
    with admin_engine.connect() as c:
        return c.execute(
            text("SELECT status FROM pending_actions WHERE id = :i"), {"i": action_id},
        ).scalar_one()


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
class TestBrokerExecuteTenantUnderRls:
    def test_approve_by_org_b_writes_the_reviewer_in_org_b(self, broker_on_promoted):
        p = broker_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        target = f"deal:t2220-ap-{uuid.uuid4().hex[:8]}"
        rec = _Recorder()
        broker.register_action_handler(ACTION, rec)
        try:
            with _as_tenant(p.org_b):
                prop = broker.propose(
                    "agent:t2220", ACTION, target,
                    {"body": "hi", "member": "mallory@a.test"},
                    authority=broker.AuthorityTier.SUGGEST,
                )
                action_id = broker.enqueue(prop)
                assert action_id is not None
                res = asyncio.run(_drained(broker.approve(action_id, "bob@b.test")))
            assert res["ok"] is True, res
            assert len(rec.calls) == 1
            assert _status(p.admin_engine, action_id) == "applied"
            rows = _rows(p.admin_engine, [target])
            ex = [r for r in rows if r[0] == f"execute:{ACTION}"]
            assert ex == [(f"execute:{ACTION}", p.org_b, "bob@b.test")], rows
            assert operator not in {o for _, o, _ in rows}, rows
        finally:
            _delete(p.admin_engine, [target])

    def test_a_tenantless_execute_leaves_the_row_pending(self, broker_on_promoted):
        from acb_graph import tenant_session

        p = broker_on_promoted
        target = f"deal:t2220-none-{uuid.uuid4().hex[:8]}"
        rec = _Recorder()
        broker.register_action_handler(ACTION, rec)
        try:
            with _as_tenant(p.org_b):
                prop = broker.propose("agent:t2220", ACTION, target, {"body": "hi"},
                                      authority=broker.AuthorityTier.SUGGEST)
                action_id = broker.enqueue(prop)
                assert action_id is not None
            before = _rows(p.admin_engine, [target])

            with _as_tenant(None):
                res = asyncio.run(_drained(broker.execute(prop)))
                assert res["reason"] == "tenant_unbound"
                # A real ``acb_graph.tenant_session(org_b)`` alone is no binding.
                with tenant_session(p.org_b):
                    res2 = asyncio.run(_drained(broker.execute(prop)))
                assert res2["reason"] == "tenant_unbound"
                res3 = asyncio.run(_drained(broker.approve(action_id, "bob@b.test")))
                assert res3["ok"] is False

            assert rec.calls == [], "a tenant-less execute ran the outward write"
            assert _status(p.admin_engine, action_id) == "pending", (
                "the refusal must leave the row retryable"
            )
            assert _rows(p.admin_engine, [target]) == before, (
                "a tenant-less execute wrote an audit row"
            )

            # The row is still retryable: org B's reviewer approves it now.
            with _as_tenant(p.org_b):
                ok = asyncio.run(_drained(broker.approve(action_id, "bob@b.test")))
            assert ok["ok"] is True, ok
            assert len(rec.calls) == 1
        finally:
            _delete(p.admin_engine, [target])
