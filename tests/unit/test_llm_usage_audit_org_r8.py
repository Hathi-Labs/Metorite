"""Task 2189 (2 of 3) — the ``llm_completion`` audit row lands in the run's tenant.

``acb_llm.client._emit_usage`` records one ``audit_event`` row per model call
(``action='llm_completion'``) when ``LLM_USAGE_AUDIT=1``. Its payload carries
the run context: the run id, the agent, the member and the cost. Before this
fix the event carried NO ``organization_id``. With ``ACB_GRAPH_TENANT_BIND``
on, ``acb_audit.log._persist`` then files it under the OPERATOR org
(Option-A). So the model use of any tenant landed in the operator org.

The fix: ``_emit_usage`` reads ``current_tenant()`` on the CALLING frame,
before the ``run_in_executor`` hop (an executor does not copy contextvars),
and stamps it on the event. With no tenant and the flag on,
``acb_audit.refuse_tenantless`` skips the row and logs
``audit.skipped_no_tenant``. The ``acb_llm.usage`` log line and the activity
event still carry the counts, so the cost views lose nothing.

Fences (R7):

* ``llm-audit-row-in-run-tenant`` (R8, phase-4 two-org catalog, non-priv role
  ``acb_app_h3rls``): a model call under org B lands ONE row stamped org B,
  visible to org B only, and none under the operator org. RED when the stamp
  is dropped.
* ``llm-audit-tenantless-skips`` (R8 + no-DB): with no tenant and the flag on,
  ``record`` is never called and no row lands in any org.
* ``llm-audit-org-survives-the-executor``: a call on an event loop still
  stamps the tenant that the calling frame bound.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_llm_usage_audit_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import acb_audit
import acb_common
from acb_audit import log as audit
from acb_llm import client as llm_client
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

_USAGE = {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


@pytest.fixture(autouse=True)
def _audit_on(monkeypatch):
    monkeypatch.setenv("LLM_USAGE_AUDIT", "1")
    monkeypatch.setattr(acb_common, "publish_activity", lambda **_kw: None)


@pytest.fixture
def events(monkeypatch) -> list[Any]:
    calls: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", calls.append)
    return calls


def _model() -> str:
    return f"t2189-model-{uuid.uuid4().hex[:8]}"


# ==========================================================================
# 1. No DB: the stamp, the executor hop and the skip.
# ==========================================================================
def test_a_sync_call_stamps_the_bound_tenant(events) -> None:
    with _as_tenant("org-b-id"):
        llm_client._emit_usage(_model(), "fast", _USAGE)
    assert [e.action for e in events] == ["llm_completion"]
    assert events[0].organization_id == "org-b-id"


def test_a_call_on_a_loop_stamps_the_tenant_past_the_executor(events) -> None:
    async def _run() -> None:
        with _as_tenant("org-b-id"):
            llm_client._emit_usage(_model(), "fast", _USAGE)

    # ``asyncio.run`` waits for the default executor before it returns.
    asyncio.run(_run())
    assert [e.organization_id for e in events] == ["org-b-id"]


def test_no_tenant_with_the_bind_on_records_nothing(events, monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant(None):
        llm_client._emit_usage(_model(), "fast", _USAGE)
    assert events == [], "a tenant-less llm_completion reached the audit seam"


def test_no_tenant_with_the_bind_off_keeps_the_old_row(events, monkeypatch) -> None:
    monkeypatch.delenv("ACB_GRAPH_TENANT_BIND", raising=False)
    with _as_tenant(None):
        llm_client._emit_usage(_model(), "fast", _USAGE)
    assert [e.organization_id for e in events] == [None]


# ==========================================================================
# 2. R8: the real ``_persist`` under the phase-4 catalog.
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


def _rows_by_org(admin_engine, target: str) -> list[str]:
    with admin_engine.connect() as c:
        return [
            r[0]
            for r in c.execute(
                text(
                    "SELECT organization_id::text FROM audit_event "
                    "WHERE action = 'llm_completion' AND target = :t"
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
                    "WHERE action = 'llm_completion' AND target = :t"
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
            text("DELETE FROM audit_event WHERE action = 'llm_completion' AND target = :t"),
            {"t": target},
        )


@_DB_GATE
class TestLlmUsageAuditOrgUnderRls:
    def test_a_model_call_for_org_b_lands_in_org_b(self, graph_on_promoted):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        model = _model()
        target = f"model:{model}"
        try:
            with _as_tenant(p.org_b):
                llm_client._emit_usage(model, "fast", _USAGE)
            orgs = _rows_by_org(p.admin_engine, target)
            assert operator not in orgs, (
                "org B's model call landed under the OPERATOR org — "
                "cross-tenant leak (the event carried no organization_id)"
            )
            assert orgs == [p.org_b], f"expected ONE row stamped org B, got {orgs}"
            assert _visible_as(p.app_url, p.org_b, target) == 1
            assert _visible_as(p.app_url, operator, target) == 0
            assert _visible_as(p.app_url, p.org_a, target) == 0
        finally:
            _delete(p.admin_engine, target)

    def test_a_tenantless_model_call_writes_no_row(self, graph_on_promoted):
        p = graph_on_promoted
        model = _model()
        target = f"model:{model}"
        try:
            with _as_tenant(None):
                llm_client._emit_usage(model, "fast", _USAGE)
            assert _rows_by_org(p.admin_engine, target) == [], (
                "a tenant-less llm_completion wrote an audit row (the "
                "operator org fallback took it) — fail-closed broke"
            )
        finally:
            _delete(p.admin_engine, target)
