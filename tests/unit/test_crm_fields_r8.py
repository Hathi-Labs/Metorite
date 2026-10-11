"""R8 — ``GET /crm/fields/{entity}`` against a real Postgres (WS-53 CRM-Q4).

Two organizations each hold field definitions, and each sees only its own,
through the real ``/crm`` router and as the non-privileged role of the
phase-4 rehearsal. A hermetic fake agrees with any SQL, so row level security
is not proved without the database. ``test_crm_fields.py`` holds the shape.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database (see test_crm_connection_r8.py).
pytestmark = _DB_GATE


def _user(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    with promoted.admin_engine.begin() as c:
        for org, rows in (
            (promoted.org_a, [("lead", "a_one", "A One", 1), ("lead", "a_zero", "A Zero", 0),
                              ("deal", "a_deal", "A Deal", 0)]),
            (promoted.org_b, [("lead", "b_one", "B One", 0)]),
        ):
            for entity, key, label, position in rows:
                c.execute(text(
                    "INSERT INTO crm_field_defs (organization_id, entity, key, label, "
                    "type, source, provider_api_name, position) VALUES "
                    "(CAST(:o AS uuid), :e, :k, :l, 'text', 'zoho', :k, :p)"),
                    {"o": org, "e": entity, "k": key, "l": label, "p": position})
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM crm_field_defs WHERE organization_id = CAST(:o AS uuid)"),
                {"o": org})


async def _get(p: Any, user: Any, path: str) -> Any:
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client:
                return await client.get(path)
    finally:
        release_tenant(scope)


async def test_each_org_sees_only_its_own_field_defs(ns, app_engine) -> None:  # noqa: F811
    a = await _get(ns, _user("a@crm-q4-r8.test", ns.org_a), "/crm/fields/leads")
    b = await _get(ns, _user("b@crm-q4-r8.test", ns.org_b), "/crm/fields/leads")
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert [f["key"] for f in a.json()] == ["a_zero", "a_one"]
    assert [f["key"] for f in b.json()] == ["b_one"]
    deals_b = await _get(ns, _user("b@crm-q4-r8.test", ns.org_b), "/crm/fields/deals")
    assert deals_b.json() == []
    deals_a = await _get(ns, _user("a@crm-q4-r8.test", ns.org_a), "/crm/fields/deals")
    assert [f["key"] for f in deals_a.json()] == ["a_deal"]
