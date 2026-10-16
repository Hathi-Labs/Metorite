"""``GET /crm/fields/{entity}`` — the field definitions (WS-53 CRM-Q4).

Spec: ``project-docs/specs/crm_platform.md`` §18.3 step 2 item 3 and §18.5.

Hermetic: the real ``/crm`` router on a bare FastAPI app. The DB seam of the
route is a small fake that answers the one SELECT. ``test_crm_fields_r8.py``
proves the two-organization rule against a real Postgres (R8).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from acb_auth import UserContext, UserRole, build_access, get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gateway.routes import crm as crm_package
from gateway.routes.crm import access as crm_access
from gateway.routes.crm import core as crm_core
from gateway.routes.crm import fields as crm_fields

from tests.unit._crm_fakes import CRM_TEST_ORG, FakeCrmDB, bind_db, crm_switch_on


def _row(key: str, position: int, **over: Any) -> SimpleNamespace:
    base = {
        "key": key, "label": key.title(), "type": "text", "options": None,
        "source": "zoho", "provider_api_name": key, "position": position,
        "visible": True,
    }
    return SimpleNamespace(**{**base, **over})


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def fetchall(self) -> list[Any]:
        return self._rows


class _FieldsDB:
    """Answers the SELECT of ``crm_field_defs`` from a dict keyed by entity."""

    def __init__(self, by_entity: dict[str, list[Any]]) -> None:
        self.by_entity = by_entity
        self.asked: list[str] = []

    async def execute(self, statement: Any, params: dict | None = None) -> _Result:
        assert "crm_field_defs" in str(statement)
        entity = (params or {})["entity"]
        self.asked.append(entity)
        return _Result(self.by_entity.get(entity, []))


@pytest.fixture
def fields_db(monkeypatch: pytest.MonkeyPatch) -> _FieldsDB:
    fake = _FieldsDB({})
    bind_db(monkeypatch, FakeCrmDB(), (crm_core, crm_access))
    crm_switch_on(monkeypatch, "*")

    @asynccontextmanager
    async def session() -> Any:
        yield fake

    monkeypatch.setattr(crm_fields, "_tenant_session", session)
    return fake


def _client(*perms: str) -> TestClient:
    user = UserContext(
        email="a@crm-q4.test", role=UserRole.EMPLOYEE,
        access=build_access(list(perms)), organization_id=CRM_TEST_ORG,
    )
    app = FastAPI()
    app.include_router(crm_package.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_each_slug_reads_its_stored_entity(fields_db: _FieldsDB) -> None:
    c = _client("feature:crm", "admin:access:manage")
    for slug in ("leads", "contacts", "organizations", "deals"):
        assert c.get(f"/crm/fields/{slug}").status_code == 200
    assert fields_db.asked == ["lead", "contact", "company", "deal"]


def test_the_response_shape_and_order(fields_db: _FieldsDB) -> None:
    fields_db.by_entity["deal"] = [
        _row("tier", 0, type="select", options=[{"label": "Gold", "value": "gold"}]),
        _row("notes", 1, visible=False, source=None, provider_api_name=None),
    ]
    body = _client("feature:crm", "admin:access:manage").get("/crm/fields/deals").json()
    assert [f["key"] for f in body] == ["tier", "notes"]
    assert body[0] == {
        "key": "tier", "label": "Tier", "type": "select",
        "options": [{"label": "Gold", "value": "gold"}], "position": 0,
        "visible": True, "source": "zoho", "api_name": "tier",
        # CRM-C4a: the columns of the field studio, at their defaults for a
        # row that the sync wrote.
        "section_id": None, "required": False, "help_text": None,
        "default_value": None, "lookup_target": None, "is_system": False,
        "retired": False,
        # CRM-Q17a-1: the fill rank, null for an org outside CRM_FILL_RANK_ORGS.
        "fill": None, "distinct": None,
    }
    assert body[1]["visible"] is False and body[1]["source"] == "native"
    assert body[1]["options"] is None and body[1]["api_name"] is None


def test_an_unknown_entity_is_422(fields_db: _FieldsDB) -> None:
    response = _client("feature:crm", "admin:access:manage").get("/crm/fields/notes")
    assert response.status_code == 422
    assert fields_db.asked == []


def test_a_user_without_the_crm_feature_is_refused(fields_db: _FieldsDB) -> None:
    assert _client().get("/crm/fields/deals").status_code == 403


def test_the_route_reads_and_only_the_studio_creates() -> None:
    """CRM-Q4 made this path read only. CRM-C4a adds ``POST``, the create of
    the field studio (``field_studio.py``, crm_platform.md §20.7 item 4).
    Nothing else may write here."""
    from tests.unit._routes import served_routes

    app = FastAPI()
    app.include_router(crm_package.router)
    by_method = {
        m: r for r in served_routes(app.routes)
        if r.path == "/crm/fields/{entity}" for m in r.methods
    }
    assert set(by_method) == {"GET", "POST"}
    assert by_method["POST"].endpoint.__module__ == "gateway.routes.crm.field_studio"
