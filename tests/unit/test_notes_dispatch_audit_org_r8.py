"""Task 2189 (3 of 3) — the meeting dispatch audit row lands in the bound tenant.

``gateway.routes.notes.dispatch._audit`` wrote ``notes.action_dispatched``
with a raw ``INSERT INTO audit_event`` on the unbound ``get_db`` session,
outside the ``acb_audit`` seam, under ``contextlib.suppress(Exception)``, and
with no ``organization_id``. On the phase-4 catalog the column is NOT NULL and
FORCE RLS refuses an unbound write, so every dispatch row failed in silence.

The fix: ``_audit`` builds an ``acb_audit.AuditEvent`` stamped with
``current_tenant()`` (the tenant that the request or the job bound, never a
request field) and calls ``acb_audit.record``. With no tenant and
``ACB_GRAPH_TENANT_BIND`` on, ``acb_audit.refuse_tenantless`` skips the row
and logs ``audit.skipped_no_tenant``, so it never lands under the operator
org. A failed audit write still never breaks the dispatch.

Fences (R7):

* ``dispatch-audit-row-in-bound-tenant`` (R8, phase-4 two-org catalog,
  non-priv role ``acb_app_h3rls``): a refused and a done ``_dispatch`` under
  org B land rows stamped org B, visible to org B only, and none under the
  operator org. RED on the old raw INSERT and when the stamp is dropped.
* ``dispatch-audit-tenantless-skips`` (R8 + no-DB): with no tenant and the
  flag on, ``record`` is never called and no row lands in any org.
* ``dispatch-audit-never-breaks``: a ``record`` that raises leaves
  ``_dispatch`` with its own answer.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_notes_dispatch_audit_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from gateway.routes.notes import dispatch
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

ALICE = "alice@t2189.test"
BOB = "bob@t2189.test"


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def _action(kind: str = "task") -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()), meeting_id=str(uuid.uuid4()), kind=kind,
        description="Send the quote", confidence=0.9, payload={},
        segment_ids=[], status="draft", due_hint=None,
    )


def _meeting(owner: str = ALICE) -> SimpleNamespace:
    return SimpleNamespace(id=str(uuid.uuid4()), owner_email=owner,
                           title="Weekly", attendees=[], summary_md="",
                           start_at=None)


@pytest.fixture
def task_rig(monkeypatch):
    """A ``task`` dispatch that reaches no database but the audit."""

    class _Db:
        async def commit(self):
            return None

    class _Cm:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *_a):
            return False

    async def _get_db():
        return _Cm()

    async def _create(_db, _owner, _action):
        return "task-2189"

    async def _mark(*_a, **_k):
        return None

    monkeypatch.setattr(dispatch, "_get_db", _get_db)
    monkeypatch.setattr(dispatch, "_create_task_from_action", _create)
    monkeypatch.setattr(dispatch, "_mark", _mark)


@pytest.fixture
def events(monkeypatch) -> list[Any]:
    calls: list[Any] = []
    # ``raising=False``: the old code had no ``record`` here at all.
    monkeypatch.setattr(dispatch, "record", calls.append, raising=False)
    return calls


async def _dispatch_drained(action, meeting, actor):
    res = await dispatch._dispatch(action, meeting, actor)
    await audit.drain()
    return res


# ==========================================================================
# 1. No DB: the seam, the stamp, the skip and the non-blocking contract.
# ==========================================================================
def test_a_refused_dispatch_stamps_the_bound_tenant(events) -> None:
    a = _action()
    with _as_tenant("org-b-id"):
        ref, error = asyncio.run(dispatch._dispatch(a, _meeting(ALICE), BOB))
    assert ref is None and error
    assert [e.action for e in events] == ["notes.action_dispatched"]
    ev = events[0]
    assert ev.organization_id == "org-b-id"
    assert ev.actor == BOB and ev.target == f"action_item:{a.id}"
    assert ev.payload["kind"] == "task" and ev.payload["error"] == error


def test_a_done_dispatch_stamps_the_bound_tenant(events, task_rig) -> None:
    with _as_tenant("org-b-id"):
        ref, error = asyncio.run(dispatch._dispatch(_action(), _meeting(), ALICE))
    assert (ref, error) == ("task-2189", None)
    assert [e.organization_id for e in events] == ["org-b-id"]
    assert events[0].payload["ref"] == "task-2189"


def test_no_tenant_with_the_bind_on_records_nothing(events, monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant(None):
        asyncio.run(dispatch._dispatch(_action(), _meeting(ALICE), BOB))
    assert events == [], "a tenant-less dispatch reached the audit seam"


def test_a_failed_audit_never_breaks_the_dispatch(monkeypatch, task_rig) -> None:
    def _boom(_ev):
        raise RuntimeError("audit down")

    monkeypatch.setattr(dispatch, "record", _boom, raising=False)
    with _as_tenant("org-b-id"):
        ref, error = asyncio.run(dispatch._dispatch(_action(), _meeting(), ALICE))
    assert (ref, error) == ("task-2189", None)


def test_audit_writes_no_raw_sql() -> None:
    """The row goes through the one ``acb_audit`` seam, never a raw INSERT."""
    import inspect

    src = inspect.getsource(dispatch._audit)
    assert "INSERT INTO audit_event" not in src
    assert "_get_db" not in src


# ==========================================================================
# 2. R8: the real ``acb_audit`` write under the phase-4 catalog.
# ==========================================================================
@pytest.fixture
def graph_on_promoted(promoted, monkeypatch):  # noqa: F811
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


def _rows_by_org(admin_engine, targets: list[str]) -> list[str]:
    with admin_engine.connect() as c:
        return sorted(
            r[0]
            for r in c.execute(
                text(
                    "SELECT organization_id::text FROM audit_event "
                    "WHERE action = 'notes.action_dispatched' AND target = ANY(:t)"
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
                text(
                    "SELECT count(*) FROM audit_event "
                    "WHERE action = 'notes.action_dispatched' AND target = ANY(:t)"
                ),
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
        c.execute(
            text(
                "DELETE FROM audit_event "
                "WHERE action = 'notes.action_dispatched' AND target = ANY(:t)"
            ),
            {"t": targets},
        )


@_DB_GATE
class TestNotesDispatchAuditOrgUnderRls:
    def test_dispatches_for_org_b_land_in_org_b(self, graph_on_promoted, task_rig):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        refused, done = _action(), _action()
        targets = [f"action_item:{refused.id}", f"action_item:{done.id}"]
        try:
            with _as_tenant(p.org_b):
                _, err = asyncio.run(_dispatch_drained(refused, _meeting(ALICE), BOB))
                assert err
                ref, err = asyncio.run(_dispatch_drained(done, _meeting(ALICE), ALICE))
                assert (ref, err) == ("task-2189", None)
            orgs = _rows_by_org(p.admin_engine, targets)
            assert operator not in orgs, (
                "org B's dispatch landed under the OPERATOR org — "
                "cross-tenant leak (the event carried no organization_id)"
            )
            assert orgs == [p.org_b, p.org_b], (
                f"expected TWO rows stamped org B, got {orgs}"
            )
            assert _visible_as(p.app_url, p.org_b, targets) == 2
            assert _visible_as(p.app_url, operator, targets) == 0
            assert _visible_as(p.app_url, p.org_a, targets) == 0
        finally:
            _delete(p.admin_engine, targets)

    def test_a_tenantless_dispatch_writes_no_row(self, graph_on_promoted):
        p = graph_on_promoted
        a = _action()
        target = f"action_item:{a.id}"
        try:
            with _as_tenant(None):
                asyncio.run(_dispatch_drained(a, _meeting(ALICE), BOB))
            assert _rows_by_org(p.admin_engine, [target]) == [], (
                "a tenant-less dispatch wrote an audit row (the operator org "
                "fallback took it) — fail-closed broke"
            )
        finally:
            _delete(p.admin_engine, [target])
