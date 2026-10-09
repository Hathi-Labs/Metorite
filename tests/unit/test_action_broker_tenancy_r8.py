"""H-201 — the Action Broker queue binds the tenant (R8).

``pending_actions`` has FORCE row level security. Before this fence the broker
opened the unbound ``acb_graph.get_session()``. As the non-privileged role an
unbound read finds no row, so the Approvals queue read empty, and the database
refused every ``enqueue``. As an owner role the same read returned the queue of
every org (leak-audit S2-7).

This suite runs the REAL broker on the H3 rehearsal's phase-4 catalog, as its
NOSUPERUSER NOBYPASSRLS role, with two orgs. It shows:

* org A enqueues a row, the row carries org A, and org A lists it;
* org B lists none of org A's rows;
* a reject or an approve of org A's id under org B changes nothing;
* with no tenant the list is empty, and an enqueue writes nothing.

Mutations this suite catches (R7):

* ``list_pending`` back to ``get_session()``: org A's own row is not listed;
* ``enqueue`` back to ``get_session()``: org A's enqueue returns ``None``;
* ``_load_proposal`` back to ``get_session()``: org A's approve finds no row;
* ``_mark`` back to ``get_session()``: org A's reject leaves the row pending.

The source fence lives in ``tests/unit/test_action_broker.py``, so it runs
with no database.

Run::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5550/acb_tenant \\
        uv run pytest tests/unit/test_action_broker_tenancy_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

# Resolved by name as fixtures, so ruff sees them as unused (F401) and the
# test signatures as redefinitions (F811). The imports are load-bearing.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE


@pytest.fixture
def broker_as_app(promoted, app_engine, monkeypatch):  # noqa: F811
    """The broker's ``acb_graph`` sessions open as the non-privileged role."""
    from acb_graph import db as graph_db

    factory = sessionmaker(bind=app_engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    from action_broker import clear_action_handlers

    clear_action_handlers()
    try:
        yield promoted
    finally:
        clear_action_handlers()


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    """Bind *org* the way the auth dependency does, or clear the scope."""
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def _proposal(target: str = "deal:h201"):
    from action_broker import AuthorityTier, propose

    return propose("agent:h201", "h201.write", target, {"body": "hi"},
                   authority=AuthorityTier.SUGGEST)


def _row(promoted, row_id: str):  # noqa: F811
    """The row as the superuser sees it, past every policy."""
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status, organization_id::text AS org FROM pending_actions "
            "WHERE id = CAST(:i AS uuid)"), {"i": row_id}).first()


def _ids(rows) -> set[str]:
    return {str(r["id"]) for r in rows}


def test_an_org_enqueues_and_lists_its_own_row(broker_as_app):
    from action_broker import enqueue, list_pending

    a = broker_as_app.org_a
    p = _proposal()
    with _as_tenant(a):
        row_id = enqueue(p)
        assert row_id == str(p.id)
        assert row_id in _ids(list_pending())
    stored = _row(broker_as_app, row_id)
    assert stored is not None and stored.status == "pending"
    # The column DEFAULT reads the GUC that tenant_session set.
    assert stored.org == a


def test_another_org_lists_none_of_its_rows(broker_as_app):
    from action_broker import enqueue, list_pending

    a, b = broker_as_app.org_a, broker_as_app.org_b
    with _as_tenant(a):
        row_id = enqueue(_proposal())
    assert row_id is not None
    with _as_tenant(b):
        assert row_id not in _ids(list_pending())
        # B's own row is visible to B, and to B only.
        own = enqueue(_proposal("deal:h201-b"))
        assert own in _ids(list_pending())
    with _as_tenant(a):
        assert own not in _ids(list_pending())


def test_another_org_cannot_reject_or_approve_a_row(broker_as_app):
    from action_broker import approve, enqueue, register_action_handler, reject
    from action_broker.broker import _mark

    a, b = broker_as_app.org_a, broker_as_app.org_b
    called: list = []

    async def _handler(p):
        called.append(p)
        return "written"

    register_action_handler("h201.write", _handler)
    with _as_tenant(a):
        row_id = enqueue(_proposal())
    assert row_id is not None

    with _as_tenant(b):
        reject(row_id, "carol@b.test")
        _mark(row_id, "applied", reviewed_by="carol@b.test")
        res = asyncio.run(approve(row_id, "carol@b.test"))
    assert res["ok"] is False and "no pending action" in res["error"]
    assert not called
    assert _row(broker_as_app, row_id).status == "pending"

    # Org A's own reviewer does reach the row.
    with _as_tenant(a):
        res = asyncio.run(approve(row_id, "alice@a.test"))
    assert res["ok"] is True and res["status"] == "applied"
    assert len(called) == 1
    assert _row(broker_as_app, row_id).status == "applied"


def test_an_org_rejects_its_own_row(broker_as_app):
    from action_broker import enqueue, list_pending, reject

    a = broker_as_app.org_a
    with _as_tenant(a):
        row_id = enqueue(_proposal())
        reject(row_id, "alice@a.test")
        assert row_id not in _ids(list_pending())
    assert _row(broker_as_app, row_id).status == "rejected"


def test_no_tenant_lists_nothing_and_enqueues_nothing(broker_as_app):
    from action_broker import approve, enqueue, list_pending, reject

    a = broker_as_app.org_a
    with _as_tenant(a):
        row_id = enqueue(_proposal())
    assert row_id is not None

    p = _proposal("deal:h201-none")
    with _as_tenant(None):
        assert list_pending() == []
        assert enqueue(p) is None
        reject(row_id, "nobody")
        res = asyncio.run(approve(row_id, "nobody"))
    assert res["ok"] is False
    assert _row(broker_as_app, str(p.id)) is None
    assert _row(broker_as_app, row_id).status == "pending"
