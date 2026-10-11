"""R8 — the related lists of a CRM record page (WS-53 CRM-Q3).

``GET /crm/deals?company_id=``, ``GET /crm/contacts?company_id=`` and
``GET /crm/deals?contact_id=`` run against a REAL Postgres, through the real
``/crm`` router, as the non-privileged role of the phase-4 rehearsal. The
contact filter is a sub-select on ``crm_deal_contacts``, so no fake can prove
it. The test also proves that the other organization's rows never appear.
"""

from __future__ import annotations

import uuid
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

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE

_TABLES = (
    "crm_deal_contacts", "crm_deals", "crm_contacts", "crm_companies",
    "crm_settings", "crm_deal_statuses", "crm_lead_statuses", "crm_lost_reasons",
)


def _admin(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email="admin@crm-q3-r8.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:crm", "admin:access:manage"]),
        organization_id=org,
    )


async def _call(p: Any, org: str, method: str, path: str, body: dict | None = None) -> Any:
    """One request through the real router. The tenant comes from the user."""
    import httpx
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import crm as crm_package

    user = _admin(org)

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://crm.test",
            ) as client,
        ):
            return await client.request(method, path, json=body)
    finally:
        release_tenant(scope)


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 - a fixture
    from tests.unit._crm_fakes import crm_switch_on

    crm_switch_on(monkeypatch, "*")
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            for table in _TABLES:
                c.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = CAST(:o AS uuid)"),
                    {"o": org})


async def _make(p: Any, org: str, path: str, body: dict) -> str:
    res = await _call(p, org, "POST", path, body)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _names(p: Any, org: str, path: str, key: str) -> list[str]:
    res = await _call(p, org, "GET", path)
    assert res.status_code == 200, res.text
    return sorted(row[key] for row in res.json()["rows"])


async def test_related_lists_filter_through_the_real_catalog(ns, app_engine) -> None:  # noqa: F811
    tag = uuid.uuid4().hex[:6]
    a, b = ns.org_a, ns.org_b
    assert (await _call(ns, a, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200
    assert (await _call(ns, b, "PATCH", "/crm/settings", {"mode": "native"})).status_code == 200

    acme = await _make(ns, a, "/crm/organizations", {"name": f"Acme {tag}"})
    other = await _make(ns, a, "/crm/organizations", {"name": f"Other {tag}"})
    anitha = await _make(ns, a, "/crm/contacts", {"first_name": "Anitha", "company_id": acme})
    priya = await _make(ns, a, "/crm/contacts", {"first_name": "Priya", "company_id": other})
    bosch = await _make(ns, a, "/crm/deals", {"name": "Bosch", "company_id": acme})
    await _make(ns, a, "/crm/deals", {"name": "Elsewhere", "company_id": other})
    link = await _call(ns, a, "POST", f"/crm/deals/{bosch}/contacts",
                       {"contact_id": anitha, "role": "Buyer"})
    assert link.status_code in (200, 201), link.text

    # Organization B holds a record of its own. It must never appear.
    await _make(ns, b, "/crm/contacts", {"first_name": "Bee"})

    assert await _names(ns, a, f"/crm/deals?company_id={acme}", "name") == ["Bosch"]
    assert await _names(ns, a, f"/crm/contacts?company_id={acme}", "first_name") == ["Anitha"]
    assert await _names(ns, a, f"/crm/contacts?company_id={other}", "first_name") == ["Priya"]
    assert await _names(ns, a, f"/crm/deals?contact_id={anitha}", "name") == ["Bosch"]
    assert await _names(ns, a, f"/crm/deals?contact_id={priya}", "name") == []

    # Another organization's ids find nothing, and the filter leaks nothing.
    assert await _names(ns, b, f"/crm/deals?company_id={acme}", "name") == []
    assert await _names(ns, b, f"/crm/deals?contact_id={anitha}", "name") == []

    refused = await _call(ns, a, "GET", f"/crm/leads?company_id={acme}")
    assert refused.status_code == 422

    # The export carries the filter, so the file is the list on screen.
    export = await _call(ns, a, "GET", f"/crm/export/deals.csv?contact_id={anitha}")
    assert export.status_code == 200, export.text
    assert "Bosch" in export.text
    assert "Elsewhere" not in export.text
