"""Task 2221 — an agent skill-toggle audit row lands in the admin's tenant.

``gateway.routes.agent_skills._record_change`` records
``agents.skill_settings_set`` with the admin's address and each family that
the admin turned on or off. It recorded with no ``organization_id``, so with
``ACB_GRAPH_TENANT_BIND`` on ``acb_audit._persist`` filed it under the
OPERATOR org (Option-A).

The fix: ``_record_change`` hands the event to the one admin audit writer,
``admin._common.record_admin_change``. That writer stamps ``current_tenant()``,
which ``get_current_user`` bound from the identity, and refuses a tenant-less
event with the bind on. One writer, not two copies of the same rule.

Fences (R7):

* ``skills-audit-row-in-bound-tenant`` (R8, phase-4 two-org catalog,
  non-priv role ``acb_app_h3rls``): a toggle under org B lands one row stamped
  org B, visible to org B only, and none under the operator org. RED on main.
* ``skills-audit-tenantless-skips`` (R8): with no tenant it writes no row.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_agent_skills_audit_org_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

pytest.importorskip("sqlalchemy")

from acb_audit import log as audit
from gateway.routes import agent_skills
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

# ``promoted`` is used by name for pytest fixture injection.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    promoted,
)

ADMIN = "admin@t2221.test"


@contextmanager
def _as_tenant(org: str | None) -> Iterator[None]:
    from acb_common.db import bind_tenant, clear_tenant, release_tenant

    token = bind_tenant(org) if org else clear_tenant()
    try:
        yield
    finally:
        release_tenant(token)


def test_the_toggle_audit_uses_the_one_admin_writer() -> None:
    src = inspect.getsource(agent_skills._record_change)
    assert "record_admin_change" in src
    assert "AuditEvent(" not in src, "a second copy of the admin audit writer"


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


def _orgs(admin_engine, target: str) -> list[str]:
    with admin_engine.connect() as c:
        return sorted(
            r[0] for r in c.execute(
                text("SELECT organization_id::text FROM audit_event WHERE target = :t"),
                {"t": target},
            )
        )


def _visible_as(app_url, org: str, target: str) -> int:
    eng = create_engine(app_url, future=True)
    try:
        with eng.connect() as c, c.begin():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            return c.execute(
                text("SELECT count(*) FROM audit_event WHERE target = :t"),
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
        c.execute(text("DELETE FROM audit_event WHERE target = :t"), {"t": target})


async def _toggle(target: str) -> None:
    agent_skills._record_change(
        ADMIN, "agents.skill_settings_set", target,
        settings=[{"family": "memory", "enabled": False, "reason": "t2221"}],
    )
    await audit.drain()


@_DB_GATE
class TestAgentSkillsAuditOrgUnderRls:
    def test_a_toggle_in_org_b_lands_in_org_b(self, graph_on_promoted):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        target = f"agent:t2221-{uuid.uuid4().hex[:8]}"
        try:
            with _as_tenant(p.org_b):
                asyncio.run(_toggle(target))
            orgs = _orgs(p.admin_engine, target)
            assert operator not in orgs, (
                "org B's skill toggle landed under the OPERATOR org — "
                "cross-tenant leak (the event carried no organization_id)"
            )
            assert orgs == [p.org_b], f"expected ONE row stamped org B, got {orgs}"
            assert _visible_as(p.app_url, p.org_b, target) == 1
            assert _visible_as(p.app_url, operator, target) == 0
            assert _visible_as(p.app_url, p.org_a, target) == 0
        finally:
            _delete(p.admin_engine, target)

    def test_a_tenantless_toggle_writes_no_row(self, graph_on_promoted):
        p = graph_on_promoted
        target = f"agent:t2221-none-{uuid.uuid4().hex[:8]}"
        try:
            with _as_tenant(None):
                asyncio.run(_toggle(target))
            assert _orgs(p.admin_engine, target) == [], (
                "a tenant-less skill toggle wrote an audit row (the operator "
                "org fallback took it) — fail-closed broke"
            )
        finally:
            _delete(p.admin_engine, target)
