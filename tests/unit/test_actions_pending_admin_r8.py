"""AP-BUG-2 (task 1896): only an org admin reads or settles the approval queue (R8).

``GET /actions/pending`` returned the whole queue of the tenant to any member.
The router demanded ``feature:approvals`` alone, and migration 207 grants
``feature:*`` to ``member`` and ``manager`` in every org. ``pending_actions``
has no requester column, so "only the rows I made" cannot exist. The fix is an
admin gate, ``ADMIN_PERMISSION`` of ``routes/shell/intent.py``, the same test
that the needs provider ``_approvals`` applies.

This suite runs the REAL ``/actions`` router and the REAL broker on the H3
rehearsal's phase-4 catalog, as its NOSUPERUSER NOBYPASSRLS role, with two
orgs. It shows:

* a member of org A gets 403 on the list, and no payload;
* an admin of org A gets 200 with the whole queue of org A;
* an admin of org B never reads a row of org A;
* a member gets 403 on approve and on reject, and the row stays pending;
* an admin of org A rejects the row.

Mutation this suite catches (R7): drop the admin gate from the router, and the
member list test reads org A's queue with its payload.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_actions_pending_admin_r8.py -v -rs
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")
pytest.importorskip("gateway.routes.actions", reason="gateway not installed")

import httpx
from fastapi import Request
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

ADMIN_A = "admin-a@apbug2-r8.test"
MEMBER_A = "member-a@apbug2-r8.test"
ADMIN_B = "admin-b@apbug2-r8.test"
SECRET = "the body of a mail that only an admin may read"


def _principal(email: str, org: str, *, admin: bool) -> Any:
    """A member as migration 207 makes one: ``feature:*``. An admin adds
    ``ADMIN_PERMISSION``."""
    from acb_auth import UserContext, UserRole, build_access
    from gateway.routes.shell.intent import ADMIN_PERMISSION

    perms = ["feature:*", ADMIN_PERMISSION] if admin else ["feature:*"]
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, access=build_access(perms),
        organization_id=org,
    )


@pytest.fixture
def world(promoted, app_engine, monkeypatch):  # noqa: F811
    """The broker's ``acb_graph`` sessions open as the non-privileged role."""
    from acb_graph import db as graph_db

    factory = sessionmaker(bind=app_engine, expire_on_commit=False, future=True)
    monkeypatch.setattr(graph_db, "_session_factory", lambda: factory)
    with promoted.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pending_actions"))
    users = {
        ADMIN_A: _principal(ADMIN_A, promoted.org_a, admin=True),
        MEMBER_A: _principal(MEMBER_A, promoted.org_a, admin=False),
        ADMIN_B: _principal(ADMIN_B, promoted.org_b, admin=True),
    }
    return promoted, users


def _enqueue(org: str, target: str) -> str:
    """One pending row in *org*, through the real broker."""
    from acb_common.db import bind_tenant, release_tenant
    from action_broker import AuthorityTier, enqueue, propose

    token = bind_tenant(org)
    try:
        row_id = enqueue(propose(
            "agent:delivery", "apbug2.write", target, {"body": SECRET},
            authority=AuthorityTier.SUGGEST,
        ))
    finally:
        release_tenant(token)
    assert row_id is not None
    return row_id


def _status(promoted, row_id: str) -> str:  # noqa: F811
    with promoted.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT status FROM pending_actions WHERE id = CAST(:i AS uuid)"),
            {"i": row_id}).scalar_one()


def _app(users: dict[str, Any]) -> Any:
    """The real ``/actions`` router. The caller is the principal that the
    test header names, and the override binds the tenant from it. The tenant
    never comes from the request."""
    from acb_auth import get_current_user, require_internal_auth
    from acb_common.db import bind_tenant
    from fastapi import FastAPI
    from gateway.routes import actions

    async def _caller(request: Request) -> Any:
        user = users[request.headers["x-test-as"]]
        bind_tenant(user.organization_id)
        return user

    async def _transport() -> None:
        return None

    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[get_current_user] = _caller
    app.dependency_overrides[require_internal_auth] = _transport
    return app


def _call(users: dict[str, Any], who: str, method: str, path: str) -> httpx.Response:
    from acb_common.db import clear_tenant, release_tenant

    async def _go() -> httpx.Response:
        scope = clear_tenant()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=_app(users)),
                base_url="http://actions.test",
            ) as client:
                return await client.request(method, path, headers={"x-test-as": who})
        finally:
            release_tenant(scope)

    return asyncio.run(_go())


def test_a_member_gets_403_and_no_payload(world):
    p, users = world
    _enqueue(p.org_a, "deal:apbug2-a")
    res = _call(users, MEMBER_A, "GET", "/actions/pending")
    assert res.status_code == 403, res.text
    assert SECRET not in res.text


def test_an_admin_reads_the_whole_queue_of_the_org(world):
    p, users = world
    one = _enqueue(p.org_a, "deal:apbug2-a1")
    two = _enqueue(p.org_a, "deal:apbug2-a2")
    res = _call(users, ADMIN_A, "GET", "/actions/pending")
    assert res.status_code == 200, res.text
    body = res.json()
    assert {str(r["id"]) for r in body["pending"]} == {one, two}
    assert body["count"] == 2
    assert all(r["payload"] == {"body": SECRET} for r in body["pending"])


def test_an_admin_of_another_org_reads_no_row_of_org_a(world):
    p, users = world
    a_row = _enqueue(p.org_a, "deal:apbug2-a")
    b_row = _enqueue(p.org_b, "deal:apbug2-b")
    res = _call(users, ADMIN_B, "GET", "/actions/pending")
    assert res.status_code == 200, res.text
    assert {str(r["id"]) for r in res.json()["pending"]} == {b_row}
    res = _call(users, ADMIN_A, "GET", "/actions/pending")
    assert {str(r["id"]) for r in res.json()["pending"]} == {a_row}


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_a_member_cannot_settle_a_row(world, verb):
    p, users = world
    row_id = _enqueue(p.org_a, "deal:apbug2-settle")
    res = _call(users, MEMBER_A, "POST", f"/actions/pending/{row_id}/{verb}")
    assert res.status_code == 403, res.text
    assert _status(p, row_id) == "pending"


def test_an_admin_rejects_a_row(world):
    p, users = world
    row_id = _enqueue(p.org_a, "deal:apbug2-reject")
    res = _call(users, ADMIN_A, "POST", f"/actions/pending/{row_id}/reject")
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert _status(p, row_id) == "rejected"
