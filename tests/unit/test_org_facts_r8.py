"""R8 — the org-facts seam on a real Postgres (WS-53 CRM-Q32-5 item 6).

Spec: ``project-docs/specs/crm_platform.md`` §18.14.4.

As the non-privileged role of the phase-4 rehearsal, with the seed of
``test_crm_questions_r8.py``. Two orgs with different modules and plans:

* each reads only its own facts, through ``GET /org/facts`` and through the
  agent tool ``org_facts``, which reads the route as the member;
* a member that the CRM access rule does not admit reads no CRM fact;
* an org outside ``CRM_QUESTIONS_ORGS`` reads no CRM fact;
* the CRM facts block of a run reads the facts of the member's own org.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_crm_questions_r8 import (  # noqa: F401
    _user,
    q_env,
    seed_a,
    seed_b,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# Without the gate the suite ERRORS where no ladder URL is set (deploy.yml).
pytestmark = _DB_GATE


def _app(users: dict[str, Any]) -> Any:
    """The org-facts router, with the member of each ``X-User-Email``."""
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes.org_facts import router

    async def _caller(request: Request) -> Any:
        user = users[request.headers.get("x-user-email", "")]
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = _caller
    return app


async def facts_of(p: Any, user: Any, query: str = "?app=crm") -> Any:
    import httpx
    from acb_common.db import clear_tenant, release_tenant

    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(p.app_url.render_as_string(hide_password=False)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=_app({user.email: user})),
                              base_url="http://gw.test") as client,
        ):
            return await client.get(f"/org/facts{query}", headers={"X-User-Email": user.email})
    finally:
        release_tenant(scope)


async def through_the_tool(p: Any, user: Any, monkeypatch: Any, fn: Any) -> Any:
    """Run *fn* (a coroutine factory) with the tool transport pointed at the
    real route, as *user*."""
    import httpx
    from acb_common.db import clear_tenant, release_tenant
    from acb_skills import crm_tools

    app = _app({user.email: user})
    real = httpx.AsyncClient

    def client(*args: Any, **kwargs: Any) -> Any:
        return real(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")

    monkeypatch.setattr(crm_tools.httpx, "AsyncClient", client)
    monkeypatch.setattr(crm_tools, "_current_user_email", lambda: user.email)
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await fn()
    finally:
        release_tenant(scope)
        monkeypatch.setattr(crm_tools.httpx, "AsyncClient", real)


def _org_b_member(org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email="b-admin@crm-q32-r8.test", role=UserRole.EMPLOYEE,
                       access=build_access(["feature:crm", "admin:access:manage"]),
                       organization_id=org)


def plan_b(p: Any) -> None:
    """Org B: an admin says what its one module is for."""
    from tests.unit.test_crm_questions_r8 import plan

    plan(p, p.org_b, [{"kind": "catalogue", "module": "Ledgers", "purpose": "payment_out",
                       "amount": "Spent", "by": {"purpose": "admin", "amount": "admin"}}])


async def test_each_org_reads_only_its_own_facts_through_the_route(q_env, app_engine) -> None:
    p = q_env
    seed_a(p)
    seed_b(p)
    plan_b(p)
    a = await facts_of(p, _user(p.org_a))
    b = await facts_of(p, _org_b_member(p.org_b))
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    facts_a = {(f["subject"], f["key"]): f for f in a.json()["facts"]}
    assert facts_a[("term:revenue", "revenue")]["meaning"] == (
        "Revenue means billed: the amount of Invoices.")
    amount = facts_a[("field:Invoices.Net_Value", "amount")]
    assert (amount["set_by"], amount["source"]) == ("admin", "admin")
    assert amount["set_at"] is not None
    assert ("slot:deal", "role") in facts_a
    assert ("field:Commercials.Status", "stage:Won") in facts_a
    body_b = json.dumps(b.json())
    assert "The amount of Expense Ledger is Spent (Spent)." in body_b
    for word in ("Invoices", "Collection", "Commercials", "Net_Value"):
        assert word not in body_b, word
    # No email and no record value reaches a fact.
    assert "@" not in json.dumps(a.json()) and "4242" not in a.text
    narrowed = await facts_of(p, _user(p.org_a), "?app=crm&q=REVENUE")
    assert [f["key"] for f in narrowed.json()["facts"]] == ["revenue"]
    other_app = await facts_of(p, _user(p.org_a), "?app=email")
    assert other_app.json()["facts"] == []


async def test_a_member_with_no_crm_access_and_an_org_off_the_list_read_none(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_common import get_settings

    p = q_env
    seed_a(p)
    with p.admin_engine.begin() as c:
        c.execute(text("UPDATE crm_settings SET access = 'admins' WHERE organization_id = "
                       "CAST(:o AS uuid)"), {"o": p.org_a})
    member = await facts_of(p, _user(p.org_a, admin=False))
    assert member.status_code == 200 and member.json()["facts"] == []
    admin = await facts_of(p, _user(p.org_a))
    assert admin.json()["facts"]
    monkeypatch.setattr(get_settings(), "crm_questions_orgs", "", raising=False)
    off = await facts_of(p, _user(p.org_a))
    assert off.json()["facts"] == []


async def test_each_org_reads_only_its_own_facts_through_the_tool(
    q_env, app_engine, monkeypatch,
) -> None:
    from acb_skills.org_facts_tools import crm_facts_block_for_run, org_facts

    p = q_env
    seed_a(p)
    seed_b(p)
    plan_b(p)
    a = await through_the_tool(p, _user(p.org_a), monkeypatch, lambda: org_facts("crm"))
    b = await through_the_tool(p, _org_b_member(p.org_b), monkeypatch,
                               lambda: org_facts("crm"))
    assert "Revenue means billed: the amount of Invoices." in a
    assert "Invoices" not in b and "Collection" not in b and "Expense Ledger" in b
    block_a = await through_the_tool(p, _user(p.org_a), monkeypatch, crm_facts_block_for_run)
    block_b = await through_the_tool(p, _org_b_member(p.org_b), monkeypatch,
                                     crm_facts_block_for_run)
    assert block_a.startswith("## CRM facts") and len(block_a.splitlines()) <= 12
    assert "Net Value" in block_a
    assert "Net Value" not in block_b and "Invoices" not in block_b
    assert "The amount of Expense Ledger is Spent (Spent)." in block_b
