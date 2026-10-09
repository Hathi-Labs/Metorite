"""Action Broker core — authority policy + fail-closed executor (audit BO-1).

The broker is the one component allowed to write back to source systems
(AGENTS.md non-negotiable #4). These lock the authority-tier disposition policy
and the fail-closed executor. No handlers are registered by default, so the
broker cannot perform any real write — it is inert until wired in.
"""
from __future__ import annotations

import asyncio

import pytest
from action_broker import (
    ActionProposal,
    AuthorityTier,
    Disposition,
    approve,
    clear_action_handlers,
    decide_disposition,
    enqueue,
    execute,
    list_pending,
    propose,
    register_action_handler,
    reject,
    submit,
)


@pytest.fixture(autouse=True)
def _clean_handlers():
    clear_action_handlers()
    yield
    clear_action_handlers()


# ── Fake DB — makes this whole file hermetic (no live Postgres) ───────────────
# The broker's persistence + acb_audit.record() both go through
# ``acb_graph.get_session``. Patching it to an in-memory fake keeps every test
# DB-free (also fixes the Windows "unit run hangs on a real connect" foot-gun).

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Records executed statements; returns preset rows for SELECTs."""

    def __init__(self, select_rows=None):
        self.executed: list[tuple[str, dict | None]] = []
        self.select_rows = select_rows if select_rows is not None else []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeResult(self.select_rows)
        return _FakeResult([])

    def add(self, *_a, **_k):  # acb_audit.record() calls sess.add(AuditRow)
        pass

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def statements(self, verb: str) -> list[dict | None]:
        return [p for s, p in self.executed if s.lstrip().upper().startswith(verb)]


_ORG = "00000000-0000-0000-0000-0000000000a1"


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    """Route the broker's ``tenant_session`` and the audit's ``get_session``
    to one fake session, with a tenant bound (H-201). The fake seam refuses
    no tenant, as the real one does, and records each tenant it binds."""
    from contextlib import contextmanager

    import acb_graph
    from acb_common.db import bind_tenant, release_tenant
    from acb_graph.db import TenantUnbound

    fake = _FakeSession()
    fake.tenants = []

    @contextmanager
    def _tenant_session(org=None):
        if not org:
            raise TenantUnbound("no tenant")
        fake.tenants.append(org)
        yield fake

    monkeypatch.setattr(acb_graph, "get_session", lambda: fake)
    monkeypatch.setattr(acb_graph, "tenant_session", _tenant_session)
    token = bind_tenant(_ORG)
    try:
        yield fake
    finally:
        release_tenant(token)


# ── Authority-tier policy ────────────────────────────────────────────────────

def test_read_authority_rejects_all_writes():
    assert decide_disposition(AuthorityTier.READ, destructive=False) is Disposition.REJECTED
    assert decide_disposition(AuthorityTier.READ, destructive=True) is Disposition.REJECTED


def test_autonomous_auto_applies():
    assert decide_disposition(AuthorityTier.AUTONOMOUS, destructive=True) is Disposition.AUTO_APPLY


def test_suggest_always_needs_approval():
    assert decide_disposition(AuthorityTier.SUGGEST, destructive=False) is Disposition.NEEDS_APPROVAL


def test_suggest_apply_fails_closed_on_destructive():
    # Reversible → auto; destructive/outward → human (fail closed).
    assert decide_disposition(AuthorityTier.SUGGEST_APPLY, destructive=False) is Disposition.AUTO_APPLY
    assert decide_disposition(AuthorityTier.SUGGEST_APPLY, destructive=True) is Disposition.NEEDS_APPROVAL


def test_propose_defaults_to_destructive_and_computes_disposition():
    # Un-annotated action defaults destructive=True → SUGGEST_APPLY needs a human.
    p = propose("agent:sales", "zoho.email", "deal:1", {"body": "hi"})
    assert p.destructive is True
    assert p.disposition is Disposition.NEEDS_APPROVAL
    # An action explicitly marked reversible auto-applies under SUGGEST_APPLY.
    p2 = propose("agent:sales", "clickup.status_read_cache", "task:1", {}, destructive=False)
    assert p2.disposition is Disposition.AUTO_APPLY


# ── Fail-closed executor ─────────────────────────────────────────────────────

def _mk(action: str, disposition: Disposition) -> ActionProposal:
    from uuid import uuid4
    return ActionProposal(
        id=uuid4(), actor="agent:x", action=action, target="t:1",
        payload={}, authority=AuthorityTier.SUGGEST_APPLY, disposition=disposition,
    )


def test_execute_refuses_action_with_no_handler():
    res = asyncio.run(execute(_mk("clickup.comment", Disposition.AUTO_APPLY)))
    assert res["ok"] is False
    assert "no handler" in res["error"]


def test_execute_never_runs_a_rejected_proposal():
    called = []
    register_action_handler("clickup.comment", lambda p: called.append(p))
    res = asyncio.run(execute(_mk("clickup.comment", Disposition.REJECTED)))
    assert res["ok"] is False
    assert not called  # rejected → handler never invoked


def test_execute_runs_registered_handler():
    seen: list[ActionProposal] = []

    async def _handler(p: ActionProposal):
        seen.append(p)
        return "written"

    register_action_handler("clickup.comment", _handler)
    res = asyncio.run(execute(_mk("clickup.comment", Disposition.AUTO_APPLY)))
    assert res["ok"] is True
    assert res["result"] == "written"
    assert len(seen) == 1


# ── Approval queue: enqueue / submit routing / approve / reject ───────────────

def _pending_row(action: str = "clickup.comment") -> dict:
    """A row shaped like a SELECT from pending_actions (JSONB payload → dict)."""
    from uuid import uuid4
    return {
        "id": uuid4(),
        "actor": "agent:sales",
        "action": action,
        "target": "task:1",
        "payload": {"body": "hi"},
        "authority": "suggest+apply",
        "destructive": True,
        "disposition": "needs_approval",
        "status": "pending",
    }


def test_enqueue_persists_and_returns_proposal_id(_fake_db):
    p = propose("agent:sales", "clickup.comment", "task:1", {"body": "hi"})
    row_id = enqueue(p)
    assert row_id == str(p.id)
    inserts = _fake_db.statements("INSERT")
    assert any(params and params.get("action") == "clickup.comment" for params in inserts)


def test_submit_auto_apply_executes_now():
    seen: list = []

    async def _h(p):
        seen.append(p)
        return "written"

    register_action_handler("clickup.comment", _h)
    p = propose("agent:x", "clickup.comment", "task:1", {}, authority=AuthorityTier.AUTONOMOUS)
    res = asyncio.run(submit(p))
    assert res["status"] == "applied" and res["ok"] is True
    assert len(seen) == 1  # handler actually ran


def test_submit_needs_approval_enqueues_without_executing(_fake_db):
    called: list = []
    register_action_handler("clickup.comment", lambda p: called.append(p))
    # suggest+apply + destructive → NEEDS_APPROVAL
    p = propose("agent:sales", "clickup.comment", "task:1", {"body": "hi"})
    assert p.disposition is Disposition.NEEDS_APPROVAL
    res = asyncio.run(submit(p))
    assert res["status"] == "pending"
    assert res["action_id"] == str(p.id)
    assert not called                       # nothing executed
    assert _fake_db.statements("INSERT")     # it was queued instead


def test_submit_rejected_is_refused():
    called: list = []
    register_action_handler("clickup.comment", lambda p: called.append(p))
    p = propose("agent:ro", "clickup.comment", "task:1", {}, authority=AuthorityTier.READ)
    res = asyncio.run(submit(p))
    assert res["status"] == "rejected" and res["ok"] is False
    assert not called


def test_approve_missing_action_fails_closed():
    # fake_db has no rows → _load_proposal returns (None, None)
    res = asyncio.run(approve("00000000-0000-0000-0000-000000000000", "user:vijay"))
    assert res["ok"] is False
    assert "no pending action" in res["error"]


def test_approve_runs_handler_and_marks_applied(_fake_db):
    row = _pending_row()
    _fake_db.select_rows = [row]
    seen: list = []

    async def _h(p):
        seen.append(p)
        return "ok"

    register_action_handler("clickup.comment", _h)
    res = asyncio.run(approve(str(row["id"]), "user:vijay"))
    assert res["ok"] is True and res["status"] == "applied"
    assert len(seen) == 1
    # last status write transitions the row to 'applied'
    updates = _fake_db.statements("UPDATE")
    assert any(p and p.get("status") == "applied" for p in updates)


def test_approve_non_pending_is_refused(_fake_db):
    row = _pending_row()
    row["status"] = "applied"          # already handled
    _fake_db.select_rows = [row]
    called: list = []
    register_action_handler("clickup.comment", lambda p: called.append(p))
    res = asyncio.run(approve(str(row["id"]), "user:vijay"))
    assert res["ok"] is False
    assert not called


def test_reject_marks_rejected_and_never_executes(_fake_db):
    called: list = []
    register_action_handler("clickup.comment", lambda p: called.append(p))
    res = reject("11111111-1111-1111-1111-111111111111", "user:vijay")
    assert res["status"] == "rejected"
    assert not called
    updates = _fake_db.statements("UPDATE")
    assert any(p and p.get("status") == "rejected" for p in updates)


def test_list_pending_returns_rows(_fake_db):
    _fake_db.select_rows = [_pending_row(), _pending_row("zoho.email")]
    rows = list_pending()
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"clickup.comment", "zoho.email"}


# ── H-201: the queue binds the tenant of the context, and only that one ──────

def test_every_queue_call_binds_the_context_tenant(_fake_db):
    _fake_db.select_rows = [_pending_row()]
    list_pending()
    enqueue(propose("agent:sales", "clickup.comment", "task:1", {}))
    reject("11111111-1111-1111-1111-111111111111", "user:vijay")
    asyncio.run(approve(str(_fake_db.select_rows[0]["id"]), "user:vijay"))
    assert _fake_db.tenants and set(_fake_db.tenants) == {_ORG}


def test_no_tenant_reads_empty_and_writes_nothing(_fake_db):
    from acb_common.db import clear_tenant, release_tenant

    _fake_db.select_rows = [_pending_row()]
    called: list = []
    register_action_handler("clickup.comment", lambda p: called.append(p))
    token = clear_tenant()
    try:
        assert list_pending() == []
        assert enqueue(propose("agent:sales", "clickup.comment", "task:1", {})) is None
        res = asyncio.run(approve(str(_fake_db.select_rows[0]["id"]), "user:vijay"))
        assert res["ok"] is False and not called
        reject("11111111-1111-1111-1111-111111111111", "user:vijay")
    finally:
        release_tenant(token)
    assert _fake_db.tenants == []
    assert not _fake_db.statements("INSERT") and not _fake_db.statements("UPDATE")


def test_the_queue_opens_no_unbound_session() -> None:
    """The source fence. It reads the code, so it runs with no database."""
    import inspect

    from action_broker import broker

    for fn in (broker.enqueue, broker.list_pending, broker._load_proposal, broker._mark):
        src = inspect.getsource(fn)
        assert "get_session" not in src, fn.__name__
        assert "tenant_session(" in src, fn.__name__
    # The tenant comes from the bound context, never from an argument.
    for fn in (broker.enqueue, broker.list_pending, broker.approve, broker.reject):
        assert "organization_id" not in inspect.signature(fn).parameters, fn.__name__
