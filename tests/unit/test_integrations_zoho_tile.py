"""WS-54 - the Zoho CRM tile reads the CRM's connection, not the operator env.

Spec: ``project-docs/specs/integrations_console.md`` §3.1 and §3.3 rule 3.

The owner saw "Zoho CRM - Not set up" on the APIs tab while the CRM held a
live Zoho connection. ``GET /integrations/status`` decided ``zoho-crm`` from
the operator's ``ZOHO_*`` keys, which belong to no customer. The tile now
reads the org's CRM Zoho row in ``integration_connections`` (provider
``zoho``, owning app ``crm``), in a tenant session, as IN-7 does for GitHub.

These rules need no database. ``test_integrations_zoho_tile_r8.py`` proves the
two-org case against a real Postgres.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from acb_auth import UserContext, UserRole, build_access
from acb_common.db import bind_tenant, clear_tenant, release_tenant
from fastapi import HTTPException
from gateway.connections import store
from gateway.routes import integrations

ORG_WITH = "11111111-1111-1111-1111-111111111111"
ORG_WITHOUT = "22222222-2222-2222-2222-222222222222"


def _user(org: str, *perms: str) -> UserContext:
    return UserContext(
        email="admin@zoho-tile.test", role=UserRole.EMPLOYEE,
        access=build_access(["feature:integrations", *perms]), organization_id=org,
    )


def _row(org: str, status: str) -> store.Connection:
    now = datetime.now(UTC)
    return store.Connection(
        id="33333333-3333-3333-3333-333333333333", organization_id=org,
        provider="zoho", owning_app="crm", scope_kind="org", member_email=None,
        status=status, provider_meta={"location": "in"}, scopes=(),
        connected_by="admin@zoho-tile.test", last_sync_at=None, last_error=None,
        created_at=now, updated_at=now,
    )


class _FakeKeys:
    async def get_by_type(self, *_: Any, **__: Any) -> dict[str, str]:
        return {}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """One org with a CRM Zoho row, and the operator's ZOHO_* keys set."""
    import acb_llm.key_store as key_store
    from acb_common import get_settings

    settings = get_settings()
    for name in ("zoho_client_id", "zoho_client_secret", "zoho_refresh_token"):
        monkeypatch.setattr(settings, name, "OPERATOR_SENTINEL", raising=False)
    for name in ("ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"):
        monkeypatch.setenv(name, "OPERATOR_SENTINEL")

    rows: dict[str, store.Connection] = {ORG_WITH: _row(ORG_WITH, store.STATUS_CONNECTED)}
    sessions: list[str | None] = []

    @asynccontextmanager
    async def _session(org: str | None = None):
        sessions.append(org)
        yield object()

    async def _get(_db: Any, org: str, provider: str, owning_app: str, **_: Any):
        row = rows.get(str(org))
        if row and (row.provider, row.owning_app) == (provider, owning_app):
            return row
        return None

    async def _no_custom(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(integrations, "tenant_session", _session)
    monkeypatch.setattr(store, "get", _get)
    monkeypatch.setattr(integrations, "_db_query", _no_custom)
    monkeypatch.setattr(key_store, "get_key_store", lambda: _FakeKeys())
    return {"rows": rows, "sessions": sessions}


async def _status(org: str) -> dict[str, Any]:
    scope = clear_tenant()
    bind_tenant(org)
    try:
        out = await integrations.integration_status(agent=None, user=_user(org))
    finally:
        release_tenant(scope)
    return next(r for r in out if r["service"] == "zoho-crm")


async def _test(org: str, *perms: str) -> dict[str, Any]:
    scope = clear_tenant()
    bind_tenant(org)
    try:
        return await integrations.test_integration(service="zoho-crm", user=_user(org, *perms))
    finally:
        release_tenant(scope)


# ── The status ──────────────────────────────────────────────────────────────

async def test_an_org_with_a_connected_crm_row_reads_configured(world) -> None:
    zoho = await _status(ORG_WITH)
    assert zoho["configured"] is True
    assert world["sessions"] == [ORG_WITH]  # read in the tenant session of the org


async def test_an_org_without_a_row_reads_not_configured_despite_the_operator_env(world) -> None:
    zoho = await _status(ORG_WITHOUT)
    assert zoho["configured"] is False


async def test_a_row_that_needs_a_reconnect_is_not_configured(world) -> None:
    world["rows"][ORG_WITH] = _row(ORG_WITH, store.STATUS_NEEDS_RECONNECT)
    assert (await _status(ORG_WITH))["configured"] is False


async def test_no_bound_org_is_not_configured_and_opens_no_session(world) -> None:
    scope = clear_tenant()
    try:
        out = await integrations.integration_status(agent=None, user=_user(ORG_WITH))
    finally:
        release_tenant(scope)
    assert next(r for r in out if r["service"] == "zoho-crm")["configured"] is False
    assert world["sessions"] == []


@pytest.mark.parametrize("org", [ORG_WITH, ORG_WITHOUT])
async def test_the_tile_offers_no_key_form_and_no_missing_keys(world, org) -> None:
    zoho = await _status(org)
    assert zoho["env_vars"] == []
    assert zoho["operator_env_vars"] == []
    assert zoho["missing_keys"] == []


async def _agent_status(org: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``?agent=sales``. The sales agent declares zoho-crm (agent_registry.json)."""
    from gateway.routes import agent as agent_routes

    monkeypatch.setattr(agent_routes, "_AGENT_REGISTRY", [
        {"name": "sales", "integrations": ["zoho-crm"], "optional_integrations": []},
    ])

    async def _no_github() -> bool:
        return False

    monkeypatch.setattr(integrations, "_github_connected", _no_github)
    scope = clear_tenant()
    bind_tenant(org)
    try:
        out = await integrations.integration_status(agent="sales", user=_user(org))
    finally:
        release_tenant(scope)
    return next(r for r in out if r["service"] == "zoho-crm")


async def test_the_agent_gate_reads_the_env_the_run_uses_not_the_crm_row(world, monkeypatch) -> None:
    # The agent runtime (`acb_skills.integrations._zoho_crm`) reads only the
    # operator env until IN-3. So the gate does too, in both directions.
    from acb_common import get_settings

    without_row = await _agent_status(ORG_WITHOUT, monkeypatch)
    assert without_row["configured"] is True  # the env holds the keys, no row
    monkeypatch.setattr(get_settings(), "zoho_refresh_token", "", raising=False)
    with_row = await _agent_status(ORG_WITH, monkeypatch)
    assert with_row["configured"] is False  # a live CRM row, but no env keys
    assert world["sessions"] == []  # the agent gate never read the CRM row
    for tile in (without_row, with_row):
        assert (tile["env_vars"], tile["missing_keys"]) == ([], [])


def test_the_guide_holds_no_self_client_steps() -> None:
    guide = integrations._SETUP_GUIDES["zoho-crm"]
    assert (guide["label"], integrations._GUIDE_CATEGORIES["zoho-crm"]) == ("Zoho CRM", "crm")
    assert guide["instructions"] == ""
    assert guide["setup_url"] == "" and guide["docs_url"] == ""
    assert "Self Client" not in repr(guide)


def test_the_app_owned_key_is_the_crm_connection_key() -> None:
    from gateway.routes.crm import connection as crm_connection

    assert integrations.APP_OWNED_SERVICES["zoho-crm"] == (
        crm_connection.PROVIDER, crm_connection.OWNING_APP,
    )


# ── The test button ─────────────────────────────────────────────────────────

async def test_the_test_needs_the_manage_right(world) -> None:
    with pytest.raises(HTTPException) as caught:
        await _test(ORG_WITH)
    assert caught.value.status_code == 403


async def test_the_test_of_an_org_without_a_row_says_not_connected_and_sends_nothing(
    world, monkeypatch,
) -> None:
    from gateway.routes.crm import connection as crm_connection

    calls: list[str] = []

    async def _fresh(org: str, **_: Any) -> Any:
        calls.append(org)
        return None

    def _no_http(*_: Any, **__: Any) -> Any:
        raise AssertionError("the zoho-crm test must not call a provider")

    monkeypatch.setattr(crm_connection, "fresh_zoho_credential", _fresh)
    monkeypatch.setattr(integrations.httpx, "AsyncClient", _no_http)
    out = await _test(ORG_WITHOUT, "integrations:manage")
    assert out["ok"] is False
    assert out["detail"] == integrations.ZOHO_CRM_NOT_CONNECTED
    assert calls == []  # no row: no lock, no refresh, no provider call


async def test_the_test_of_a_live_row_uses_the_crm_credential(world, monkeypatch) -> None:
    from gateway.routes.crm import connection as crm_connection

    async def _fresh(org: str, **_: Any) -> Any:
        return object() if org == ORG_WITH else None

    monkeypatch.setattr(crm_connection, "fresh_zoho_credential", _fresh)
    out = await _test(ORG_WITH, "integrations:manage")
    assert out == {
        "service": "zoho-crm", "ok": True,
        "detail": "The CRM holds a live Zoho connection.",
    }


async def test_a_row_that_needs_a_reconnect_tests_red_without_a_refresh(world, monkeypatch) -> None:
    from gateway.routes.crm import connection as crm_connection

    async def _fresh(org: str, **_: Any) -> Any:
        raise AssertionError("a row that needs a reconnect must not refresh")

    monkeypatch.setattr(crm_connection, "fresh_zoho_credential", _fresh)
    world["rows"][ORG_WITH] = _row(ORG_WITH, store.STATUS_NEEDS_RECONNECT)
    out = await _test(ORG_WITH, "integrations:manage")
    assert out["ok"] is False and "CRM settings" in out["detail"]


async def test_a_dead_token_tells_the_admin_to_reconnect_in_the_crm(world, monkeypatch) -> None:
    from gateway.crm_sources.base import NeedsReconnect
    from gateway.routes.crm import connection as crm_connection

    async def _fresh(org: str, **_: Any) -> Any:
        raise NeedsReconnect("The zoho connection stopped (invalid_code)")

    monkeypatch.setattr(crm_connection, "fresh_zoho_credential", _fresh)
    out = await _test(ORG_WITH, "integrations:manage")
    assert out["ok"] is False
    assert "CRM settings" in out["detail"]
    assert "invalid_code" not in out["detail"]
