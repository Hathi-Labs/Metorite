"""The kit download route (KIT-4a). Database-free: the route reads no table.

Mutations (R7). ``R`` is ``apps/services/gateway/gateway/routes/agent_kit.py``.

* K1. ``R`` ``require_kit``: return at once. ``test_dark_by_default_is_404`` fails.
* K2. ``R`` ``require_kit``: read the organization from the query. ``test_the_tenant_comes_from_the_session`` fails.
* K3. ``R`` drop the ``no-store`` header. ``test_a_listed_org_gets_the_zip`` fails.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from acb_auth import UserContext, UserRole, build_access, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, clear_tenant
from acb_playbook import kit_version
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes.agent_kit import router

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def _user(org: str = ORG) -> UserContext:
    return UserContext(email="m@x", role=UserRole.EMPLOYEE, access=build_access(["feature:*"]), organization_id=org)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(get_settings(), "playbook_orgs", "")
    app = FastAPI()
    app.include_router(router)

    async def _session():
        user = _user()
        bind_tenant(user.organization_id)  # as get_current_user does
        return user

    app.dependency_overrides[get_current_user] = _session
    yield TestClient(app)
    clear_tenant()


def _list(monkeypatch, value: str) -> None:
    monkeypatch.setattr(get_settings(), "playbook_orgs", value)


def test_dark_by_default_is_404(client):
    assert client.get("/agent-kit").status_code == 404
    assert client.get("/agent-kit/codex.zip").status_code == 404


def test_a_listed_org_gets_the_zip(client, monkeypatch):
    _list(monkeypatch, ORG)
    res = client.get("/agent-kit/codex.zip")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["content-disposition"] == (
        f'attachment; filename="metorite-agent-kit-codex-v{kit_version()}.zip"'
    )
    assert res.headers["cache-control"] == "no-store"
    with zipfile.ZipFile(BytesIO(res.content)) as zf:
        assert zf.read("KIT_VERSION").decode().strip() == str(kit_version())
        assert ".codex/config.toml.example" in zf.namelist()


def test_the_index_lists_the_clients(client, monkeypatch):
    _list(monkeypatch, "*")
    body = client.get("/agent-kit").json()
    assert body["kit_version"] == kit_version()
    assert body["clients"] == ["claude", "codex", "copilot", "gemini", "antigravity", "generic"]


def test_unknown_client_is_a_fixed_404(client, monkeypatch):
    _list(monkeypatch, ORG)
    res = client.get("/agent-kit/evil-client.zip")
    assert res.status_code == 404
    assert "evil" not in res.text


def test_the_tenant_comes_from_the_session(client, monkeypatch):
    _list(monkeypatch, OTHER)  # the session org is ORG, so a query value must not help
    assert client.get(f"/agent-kit/codex.zip?organization_id={OTHER}").status_code == 404
    _list(monkeypatch, ORG)
    assert client.get(f"/agent-kit/codex.zip?organization_id={OTHER}").status_code == 200


def test_no_session_is_401():
    app = FastAPI()
    app.include_router(router)
    res = TestClient(app).get("/agent-kit/codex.zip")
    assert res.status_code == 401


def test_the_route_is_registered_in_the_gateway():
    from gateway.main import app

    from tests.unit._routes import served_routes

    assert "/agent-kit/{client}.zip" in {getattr(r, "path", "") for r in served_routes(app.routes)}
