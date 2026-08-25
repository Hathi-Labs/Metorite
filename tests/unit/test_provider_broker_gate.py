"""Provider writes route through the Action Broker (audit BO-1 / A2).

Locks the ``BaseTaskProvider._broker_gate`` contract that wraps every outward
provider write:

  * default (no ``ACTION_BROKER_ENFORCE``) → AUTO-APPLY: the real write runs
    exactly once and its result is returned unchanged (chokepoint + audit only,
    zero behaviour change — these writes are already user-approved);
  * enforced → the write is QUEUED (``do_write`` never runs, a pending marker
    comes back);
  * fail-safe → a broker-layer error never blocks the user-approved write.

Exercises ``_broker_gate`` directly with a stub ``do_write`` (no HTTP, no DB).

⚠️ **Re-cut 2026-08-24 by D52 (board WS-39 S1).** This file used to instantiate
``ClickUpProvider``, which is deleted along with the rest of the connector. The
CONTRACT it locks is not ClickUp's — it is ``BaseTaskProvider._broker_gate``'s,
and that survives — so the concrete class was replaced by a local stub rather
than the file being deleted with the connector. The action names below are
arbitrary strings the gate never interprets; keeping the old ones would have
implied a connector that no longer exists.
"""
from __future__ import annotations

import asyncio

from gateway.routes.tasks.providers import BaseTaskProvider


class _StubProvider(BaseTaskProvider):
    """A concrete `BaseTaskProvider` that implements nothing.

    `_broker_gate` is defined on the base and touches none of the abstract
    surface, so clearing `__abstractmethods__` is enough to instantiate — and
    it keeps this test honest about what it covers: the gate, not a connector.
    """

    provider = "stub"


_StubProvider.__abstractmethods__ = frozenset()


def _provider() -> BaseTaskProvider:
    return _StubProvider()


def _no_db(monkeypatch):
    """Make acb_graph.get_session raise so acb_audit.record() no-ops (it catches)
    — keeps propose()/enqueue() off a real DB without changing their logic."""
    import acb_graph

    def _raise():
        raise ConnectionError("no db in test")

    monkeypatch.setattr(acb_graph, "get_session", _raise)


def test_gate_auto_applies_by_default(monkeypatch):
    monkeypatch.delenv("ACTION_BROKER_ENFORCE", raising=False)
    _no_db(monkeypatch)
    calls: list[int] = []

    async def do_write():
        calls.append(1)
        return {"provider_task_id": "T1", "provider_url": "u"}

    res = asyncio.run(
        _provider()._broker_gate("stub.create_task", "list:1", {"title": "x"}, do_write)
    )
    assert res == {"provider_task_id": "T1", "provider_url": "u"}
    assert calls == [1]  # ran exactly once, result passed through


def test_gate_queues_when_enforced(monkeypatch):
    monkeypatch.setenv("ACTION_BROKER_ENFORCE", "all")
    _no_db(monkeypatch)
    import action_broker
    monkeypatch.setattr(action_broker, "enqueue", lambda p: "act-9")
    calls: list[int] = []

    async def do_write():
        calls.append(1)
        return {"provider_task_id": "T1"}

    res = asyncio.run(
        _provider()._broker_gate("stub.create_task", "list:1", {"title": "x"}, do_write)
    )
    assert res["pending"] is True
    assert res["pending_action_id"] == "act-9"
    assert calls == []  # the write is NOT executed while queued


def test_gate_enforce_specific_action_only(monkeypatch):
    # Comma-list enforces only the named action; others still auto-apply.
    monkeypatch.setenv("ACTION_BROKER_ENFORCE", "stub.update_task")
    _no_db(monkeypatch)
    import action_broker
    monkeypatch.setattr(action_broker, "enqueue", lambda p: "act-x")
    ran: list[str] = []

    async def do_write():
        ran.append("create")
        return {"provider_task_id": "T1"}

    res = asyncio.run(
        _provider()._broker_gate("stub.create_task", "list:1", {}, do_write)
    )
    assert ran == ["create"]  # create_task not in the list → auto-applies
    assert "pending" not in res


def test_gate_fail_safe_on_broker_error(monkeypatch):
    monkeypatch.delenv("ACTION_BROKER_ENFORCE", raising=False)
    import action_broker

    def _boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(action_broker, "propose", _boom)
    calls: list[int] = []

    async def do_write():
        calls.append(1)
        return {"provider_task_id": "T1"}

    res = asyncio.run(
        _provider()._broker_gate("stub.create_task", "list:1", {"title": "x"}, do_write)
    )
    assert res == {"provider_task_id": "T1"}
    assert calls == [1]  # write still ran despite the broker error
