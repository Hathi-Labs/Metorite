"""Observability access + durable history (fix for "observability doesn't work").

The live views were EXECUTIVE-gated once, and the SSO proxy sends
role=employee unless the operator's email is in EXECUTIVE_EMAILS. So the
operator's own dashboard gave 403, and the page was blank. The role check now
admits every member role. Task 4110 added the feature gate: each live view
takes ``feature:observability`` at the router. These tests assert both. An
employee that holds the feature reads the views, and one without it gets 403.
``/runs`` degrades to an empty list, not a 500, when the DB is not there.
The full route map is ``test_agent_observability_route_gate.py``.
"""
from __future__ import annotations

from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import observability as obs

HOLDER = ["feature:observability"]


def _client(grants: list[str]) -> TestClient:
    app = FastAPI()
    app.include_router(obs.router)
    # No org: each view answers empty, and no test opens a store.
    user = UserContext(
        email="ops@example.test",
        role=UserRole.EMPLOYEE,  # the default role of the SSO proxy
        access=build_access(grants, []),
    )
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_runs_is_open_to_an_employee_with_the_feature():
    # EMPLOYEE is the default role the SSO proxy forwards. Before → 403.
    r = _client(HOLDER).get("/observability/runs?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert "runs" in body and isinstance(body["runs"], list)


def test_cost_is_open_to_an_employee_with_the_feature():
    r = _client(HOLDER).get("/observability/cost?days=3")
    assert r.status_code == 200
    assert "totals" in r.json()


def test_an_employee_without_the_feature_gets_403_on_runs_and_cost():
    c = _client(["feature:chat"])
    for path in ("/observability/runs?limit=5", "/observability/cost?days=3"):
        r = c.get(path)
        assert r.status_code == 403, path
        assert "feature:observability" in r.json()["detail"]


def test_runs_degrades_to_empty_without_db():
    # No Postgres in the unit env → the endpoint must return [] (200), never 500.
    r = _client(HOLDER).get("/observability/runs")
    assert r.status_code == 200
    assert r.json()["runs"] == []


def test_runs_rejects_bad_status_filter_silently():
    # An unknown status is ignored (not applied), not an error.
    r = _client(HOLDER).get("/observability/runs?status=bogus")
    assert r.status_code == 200
