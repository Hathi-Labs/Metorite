"""Task 2221 — an access-change audit row lands in the tenant it changed.

``gateway.routes.admin._common.record_admin_change`` is the one writer of the
admin audit trail: member invites, role and override changes, removals and
purges, groups, roles, the org rename, access-request decisions. The payload
names that tenant's members and grants. It recorded with no
``organization_id``, so with ``ACB_GRAPH_TENANT_BIND`` on ``acb_audit._persist``
filed every tenant's access changes under the OPERATOR org (Option-A).

The fix: ``record_admin_change`` stamps the org of the change. An admin route
passes nothing, and the writer reads ``current_tenant()``, which
``get_current_user`` bound from the identity. The operator door has no bound
tenant, so it passes ``organization_id=`` of the org row that it wrote through
``tenant_session(org_id)``. It is never a request field. With no org and the
bind on, ``acb_audit.refuse_tenantless`` skips the row and logs
``audit.skipped_no_tenant``. A failed audit still never blocks an admin.

Fences (R7):

* ``admin-audit-row-in-bound-tenant`` (R8, phase-4 two-org catalog, non-priv
  role ``acb_app_h3rls``): a change under org B, and an operator change that
  names org B with no bound tenant, land in org B, visible to org B only, and
  none under the operator org. RED when the stamp is dropped.
* ``admin-audit-tenantless-skips`` (R8 + no-DB): no org and the bind on means
  ``record`` is never called and no row lands.

Run (real Postgres)::

    TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/acb_tenant \\
        uv run pytest tests/unit/test_admin_audit_org_r8.py -v -rs
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
from gateway.routes.admin import _common
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


@pytest.fixture
def events(monkeypatch) -> list[Any]:
    """Catch each event that reaches ``acb_audit.record``.

    ``record_admin_change`` imports ``record`` inside its body, so the patch
    goes on the ``acb_audit`` package that the import reads.
    """
    import acb_audit

    calls: list[Any] = []
    monkeypatch.setattr(acb_audit, "record", calls.append)
    return calls


# ==========================================================================
# 1. No DB: the stamp, the explicit org, the skip, and best-effort.
# ==========================================================================
def test_an_admin_change_stamps_the_bound_tenant(events) -> None:
    with _as_tenant("org-b-id"):
        _common.record_admin_change(ADMIN, "org.group_created", "group:ops")
    assert [e.organization_id for e in events] == ["org-b-id"]
    assert events[0].actor == f"user:{ADMIN}"


def test_the_operator_door_names_the_org_it_wrote(events) -> None:
    with _as_tenant(None):
        _common.record_admin_change(
            "operator", "org.renamed", "org:acme",
            organization_id="org-b-id", display_name="Acme", via="operator",
        )
    [ev] = events
    assert ev.organization_id == "org-b-id"
    # The org rides on the event, never in the payload.
    assert ev.payload == {"display_name": "Acme", "via": "operator"}


def test_no_tenant_with_the_bind_on_records_nothing(events, monkeypatch) -> None:
    monkeypatch.setenv("ACB_GRAPH_TENANT_BIND", "1")
    with _as_tenant(None):
        _common.record_admin_change(ADMIN, "org.group_created", "group:ops")
    assert events == [], "a tenant-less admin change reached the audit seam"


def test_a_failed_audit_never_blocks_the_admin(monkeypatch) -> None:
    import acb_audit

    def _boom(_ev):
        raise RuntimeError("audit down")

    monkeypatch.setattr(acb_audit, "record", _boom)
    with _as_tenant("org-b-id"):
        _common.record_admin_change(ADMIN, "org.group_created", "group:ops")


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
        c.execute(
            text("DELETE FROM audit_event WHERE target = ANY(:t)"), {"t": targets}
        )


async def _record_drained(*args: Any, **kwargs: Any) -> None:
    _common.record_admin_change(*args, **kwargs)
    await audit.drain()


@_DB_GATE
class TestAdminAuditOrgUnderRls:
    def test_changes_for_org_b_land_in_org_b(self, graph_on_promoted):
        p = graph_on_promoted
        operator = _default_org(p.admin_engine)
        assert operator not in (p.org_a, p.org_b)
        tag = uuid.uuid4().hex[:8]
        bound_t, door_t = f"group:t2221-{tag}", f"org:t2221-{tag}"
        targets = [bound_t, door_t]
        try:
            with _as_tenant(p.org_b):
                asyncio.run(_record_drained(ADMIN, "org.group_created", bound_t))
            with _as_tenant(None):
                asyncio.run(_record_drained(
                    "operator", "org.renamed", door_t,
                    organization_id=p.org_b, display_name="B", via="operator",
                ))
            orgs = _rows_by_org(p.admin_engine, targets)
            assert operator not in orgs, (
                "org B's access change landed under the OPERATOR org — "
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

    def test_a_tenantless_change_writes_no_row(self, graph_on_promoted):
        p = graph_on_promoted
        target = f"group:t2221-none-{uuid.uuid4().hex[:8]}"
        try:
            with _as_tenant(None):
                asyncio.run(_record_drained(ADMIN, "org.group_created", target))
            assert _rows_by_org(p.admin_engine, [target]) == [], (
                "a tenant-less admin change wrote an audit row (the operator "
                "org fallback took it) — fail-closed broke"
            )
        finally:
            _delete(p.admin_engine, [target])
