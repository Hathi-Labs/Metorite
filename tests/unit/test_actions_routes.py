"""Action Broker approval inbox routes (audit BO-1 / A2).

Locks that the gateway ``/actions`` handlers delegate to the broker and thread
the reviewer identity through — without a live app/DB/auth (the broker logic
itself is covered by ``test_action_broker.py``). Calls the handler coroutines
directly with the broker functions monkeypatched.
"""
from __future__ import annotations

import asyncio

import gateway.routes.actions as actions


def test_list_pending_delegates_to_broker(monkeypatch):
    import action_broker
    monkeypatch.setattr(
        action_broker, "list_pending",
        lambda: [{"id": "1", "action": "clickup.comment"}],
    )
    res = asyncio.run(actions.list_pending_actions(_user=None))
    assert res["count"] == 1
    assert res["pending"][0]["action"] == "clickup.comment"


def test_approve_delegates_and_passes_reviewer(monkeypatch):
    import action_broker
    seen: dict = {}

    async def _approve(action_id, reviewer):
        seen["id"], seen["reviewer"] = action_id, reviewer
        return {"ok": True, "status": "applied"}

    monkeypatch.setattr(action_broker, "approve", _approve)
    user = type("U", (), {"email": "vijay@x"})()
    res = asyncio.run(actions.approve_action("act-1", user=user))
    assert res["status"] == "applied"
    assert seen == {"id": "act-1", "reviewer": "vijay@x"}


def test_reject_delegates_and_falls_back_to_operator(monkeypatch):
    import action_broker
    seen: dict = {}

    def _reject(action_id, reviewer):
        seen["id"], seen["reviewer"] = action_id, reviewer
        return {"ok": True, "status": "rejected"}

    monkeypatch.setattr(action_broker, "reject", _reject)
    res = asyncio.run(actions.reject_action("act-2", user=None))
    assert res["status"] == "rejected"
    assert seen["id"] == "act-2"
    assert seen["reviewer"] == "operator"  # no identity → best-effort fallback


def test_all_write_routes_require_internal_auth():
    """approve/reject must carry the internal-auth dependency (never anonymous)."""
    from acb_auth import require_internal_auth

    paths = {r.path: r for r in actions.router.routes}
    for p in ("/actions/pending/{action_id}/approve", "/actions/pending/{action_id}/reject",
              "/actions/pending"):
        route = paths[p]
        dep_calls = [d.call for d in route.dependant.dependencies]
        assert require_internal_auth in dep_calls, f"{p} missing require_internal_auth"



def test_the_router_demands_the_admin_permission(monkeypatch):
    """AP-BUG-2 (task 1896): ``feature:approvals`` alone reaches every member,
    so the router also demands ``ADMIN_PERMISSION``. A member gets 403 on the
    list, on approve and on reject. This fence runs with no database. The R8
    proof is ``test_actions_pending_admin_r8.py``."""
    import action_broker
    from acb_auth import (
        UserContext,
        UserRole,
        build_access,
        get_current_user,
        require_internal_auth,
    )
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from gateway.routes.shell.intent import ADMIN_PERMISSION

    monkeypatch.setattr(action_broker, "list_pending", lambda: [{"id": "1"}])
    member = UserContext(email="m@x", role=UserRole.EMPLOYEE, access=build_access(["feature:*"]))
    admin = UserContext(
        email="a@x", role=UserRole.EMPLOYEE,
        access=build_access(["feature:*", ADMIN_PERMISSION]),
    )
    app = FastAPI()
    app.include_router(actions.router)
    app.dependency_overrides[require_internal_auth] = lambda: None
    client = TestClient(app)

    app.dependency_overrides[get_current_user] = lambda: member
    assert client.get("/actions/pending").status_code == 403
    assert client.post("/actions/pending/x/approve").status_code == 403
    assert client.post("/actions/pending/x/reject").status_code == 403

    app.dependency_overrides[get_current_user] = lambda: admin
    res = client.get("/actions/pending")
    assert res.status_code == 200
    assert res.json()["count"] == 1
