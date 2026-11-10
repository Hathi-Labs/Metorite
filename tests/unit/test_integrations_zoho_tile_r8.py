"""R8 - the Zoho CRM tile reads the CRM's own connection, per org (WS-54).

Spec: ``project-docs/specs/integrations_console.md`` §3.1 and §3.3 rule 3.

What this proves against a REAL Postgres, through the real ``/integrations``
router, as the NON-privileged role of the phase-4 rehearsal:

- org A holds a CRM Zoho row (provider ``zoho``, owning app ``crm``, status
  ``connected``) that the real store wrote. Its tile reads configured;
- org B holds none, so its tile reads not configured, although the operator
  env holds every ZOHO_* key;
- neither tile offers a key form or names a missing key;
- the test button of B says "not connected", and the test of A passes from
  the CRM's credential. Neither sends a request to a provider, and no request
  carries the operator's ZOHO_* values.

Hermetic fakes agree with any SQL they are handed, so none of this is proved
without the database. ``test_integrations_zoho_tile.py`` holds the rules that
need no database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Without this gate the suite ERRORS where no ladder URL is set (the deploy
# run), and the deploy stops.
pytestmark = _DB_GATE

REAL_CLIENT = httpx.AsyncClient  # the test's own client, taken before any patch

ADMIN_A = "admin-a@zoho-tile-r8.test"
ADMIN_B = "admin-b@zoho-tile-r8.test"

#: The operator's ZOHO_* values. No tile and no request may use them.
OPERATOR_SENTINEL = "zoho_OPERATOR_SENTINEL_must_never_configure_a_tenant"


def _app_dsn(ns: Any) -> str:
    return ns.app_url.render_as_string(hide_password=False)


def _admin_dsn(ns: Any) -> str:
    return ns.admin_engine.url.render_as_string(hide_password=False)


def _user(email: str, org: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(
        email=email, role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations", "integrations:manage"]),
        organization_id=org,
    )


@pytest.fixture
def ns(promoted, monkeypatch):  # noqa: F811 — a fixture
    """The promoted catalog, the operator's ZOHO_* keys, no network, cleaned after."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    settings = get_settings()
    for name, value in {
        "acb_master_key": "r8-master-key-zoho-tile",
        "database_url": _admin_dsn(promoted),
        "zoho_client_id": OPERATOR_SENTINEL,
        "zoho_client_secret": OPERATOR_SENTINEL,
        "zoho_refresh_token": OPERATOR_SENTINEL,
        "crm_zoho_client_id": "1000.R8CLIENT",
        "crm_zoho_client_secret": "r8-client-secret",
    }.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    for name in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"):
        monkeypatch.setenv(name, OPERATOR_SENTINEL)
    monkeypatch.setattr(key_store, "_store", None)

    sent: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(500, json={})

    def _client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return REAL_CLIENT(*args, transport=httpx.MockTransport(_handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    promoted.sent = sent
    yield promoted
    with promoted.admin_engine.begin() as c:
        for org in (promoted.org_a, promoted.org_b):
            c.execute(text(
                "DELETE FROM integration_connections "
                "WHERE organization_id = CAST(:o AS uuid)"), {"o": org})


async def _call(p: Any, user: Any, path: str, params: dict | None = None) -> Any:
    """One GET through the real ``/integrations`` router, as *user*.

    The override binds the tenant from the user. The tenant never comes from
    the request.
    """
    from acb_auth import get_current_user
    from acb_common.db import bind_tenant, clear_tenant, release_tenant
    from fastapi import FastAPI
    from gateway.routes import integrations

    async def _caller() -> Any:
        bind_tenant(user.organization_id)
        return user

    app = FastAPI()
    app.include_router(integrations.router)
    app.dependency_overrides[get_current_user] = _caller
    scope = clear_tenant()
    try:
        async with tenant_engine_scope(_app_dsn(p)):
            transport = httpx.ASGITransport(app=app)
            async with REAL_CLIENT(
                transport=transport, base_url="http://integrations.test",
            ) as client:
                return await client.get(path, params=params)
    finally:
        release_tenant(scope)


async def _connect_crm_zoho(p: Any, org: str, email: str) -> None:
    """Write a live CRM Zoho row as the CRM's callback does, through the store."""
    from acb_common.db import tenant_session
    from gateway.connections import store
    from gateway.crm_sources.base import SourceCredential
    from gateway.routes.crm import connection as crm_connection

    credential = SourceCredential(
        access_token="zoho-access-of-org-a", refresh_token="zoho-refresh-of-org-a",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        provider_meta={"location": "in"},
    )
    async with tenant_engine_scope(_app_dsn(p)), tenant_session(org) as db:
        await store.save_credential(
            db, org, crm_connection.PROVIDER, crm_connection.OWNING_APP,
            credential, connected_by=email,
        )


def _zoho(res: Any) -> dict[str, Any]:
    assert res.status_code == 200, res.text
    return next(r for r in res.json() if r["service"] == "zoho-crm")


async def test_the_tile_reads_the_crm_row_of_its_own_org(ns, app_engine) -> None:  # noqa: F811
    await _connect_crm_zoho(ns, ns.org_a, ADMIN_A)

    a = _zoho(await _call(ns, _user(ADMIN_A, ns.org_a), "/integrations/status"))
    b = _zoho(await _call(ns, _user(ADMIN_B, ns.org_b), "/integrations/status"))
    assert a["configured"] is True
    assert b["configured"] is False  # the operator env holds every ZOHO_* key
    for tile in (a, b):
        assert (tile["env_vars"], tile["operator_env_vars"], tile["missing_keys"]) == ([], [], [])
        assert tile["instructions"] == "" and tile["setup_url"] == ""
    assert ns.sent == []


async def test_a_disconnected_row_reads_not_configured(ns, app_engine) -> None:  # noqa: F811
    await _connect_crm_zoho(ns, ns.org_a, ADMIN_A)
    with ns.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE integration_connections SET status = 'disconnected' "
            "WHERE organization_id = CAST(:o AS uuid)"), {"o": ns.org_a})
    a = _zoho(await _call(ns, _user(ADMIN_A, ns.org_a), "/integrations/status"))
    assert a["configured"] is False


async def test_the_test_button_reads_the_crm_connection_and_never_the_operator_env(ns, app_engine) -> None:  # noqa: F811
    from gateway.routes import integrations

    await _connect_crm_zoho(ns, ns.org_a, ADMIN_A)
    params = {"service": "zoho-crm"}

    b = await _call(ns, _user(ADMIN_B, ns.org_b), "/integrations/test", params)
    assert b.status_code == 200, b.text
    assert b.json() == {
        "service": "zoho-crm", "ok": False, "detail": integrations.ZOHO_CRM_NOT_CONNECTED,
    }

    a = await _call(ns, _user(ADMIN_A, ns.org_a), "/integrations/test", params)
    assert a.status_code == 200, a.text
    assert a.json() == {
        "service": "zoho-crm", "ok": True, "detail": "The CRM holds a live Zoho connection.",
    }
    # The token was not due, so nothing went out. Above all, no operator value did.
    assert ns.sent == []
    assert OPERATOR_SENTINEL not in a.text + b.text
