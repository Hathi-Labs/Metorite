"""MCP OAuth: the CIMD document (WS-54 IN-8a, row O11).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.3. Metorite
publishes one JSON document at ``<workbench>/oauth/mcp-client.json``. The MCP
text requires an ``https`` URL with a path, and a ``client_id`` that equals
that URL exactly. Its one redirect URI is the callback of §3.6.4.

The BFF route that serves it is ``src/app/oauth/mcp-client.json/route.ts``,
and ``proxy.ts`` lets it pass with no session. Mutation M11 of
``test_mcp_oauth_flow.py`` fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit import _mcp_oauth_fakes as f

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "workbench" / "control_plane" / "src"


@pytest.fixture
def configured(monkeypatch) -> None:
    f.configure(monkeypatch)


def test_the_client_id_is_the_url_of_the_document(configured) -> None:
    from gateway.connections import mcp_oauth

    doc = mcp_oauth.client_metadata_document()
    assert doc["client_id"] == mcp_oauth.client_metadata_url() == f.CIMD_URL
    assert doc["redirect_uris"] == [mcp_oauth.redirect_uri()] == [f.REDIRECT_URI]
    assert doc["client_name"] == "Metorite" and doc["client_uri"] == f.WORKBENCH
    assert doc["grant_types"] == ["authorization_code", "refresh_token"]
    assert doc["response_types"] == ["code"]
    assert doc["token_endpoint_auth_method"] == "none"


def test_the_document_url_is_a_valid_cimd_url(configured) -> None:
    from gateway.connections import mcp_oauth
    from mcp.client.auth.utils import is_valid_client_metadata_url

    assert is_valid_client_metadata_url(mcp_oauth.client_metadata_url())


def test_staging_serves_a_document_of_its_own(monkeypatch, configured) -> None:
    from gateway.connections import mcp_oauth

    monkeypatch.setenv("WORKBENCH_PUBLIC_URL", "https://app.staging.metorite.test")
    doc = mcp_oauth.client_metadata_document()
    assert doc["client_id"] == "https://app.staging.metorite.test/oauth/mcp-client.json"
    assert doc["redirect_uris"] == [
        "https://app.staging.metorite.test/api/integrations/mcp/oauth/callback"]


def test_the_paths_are_the_routes_that_serve_them(configured) -> None:
    """The callback path and the document path each have a BFF route, and the
    document path is public in ``proxy.ts``."""
    from gateway.connections import mcp_oauth
    from gateway.routes import integrations_mcp_oauth as routes

    callback = CONTROL_PLANE / "app" / mcp_oauth.REDIRECT_PATH.lstrip("/") / "route.ts"
    assert callback.exists(), callback
    assert f"/integrations{routes.CALLBACK_ROUTE}" in callback.read_text(encoding="utf-8")
    document = CONTROL_PLANE / "app" / mcp_oauth.CLIENT_METADATA_PATH.lstrip("/") / "route.ts"
    assert document.exists(), document
    assert f"/integrations{routes.CLIENT_METADATA_ROUTE}" in document.read_text(encoding="utf-8")
    proxy = (CONTROL_PLANE / "proxy.ts").read_text(encoding="utf-8")
    assert f'"{mcp_oauth.CLIENT_METADATA_PATH}",' in proxy


async def test_the_gateway_route_serves_the_document_to_the_service_bearer(configured) -> None:
    """No member: the BFF asks with the service bearer. No secret in it."""
    from gateway.connections import mcp_oauth

    res = await f.call(None, "GET", "/integrations/mcp/oauth/client-metadata")
    assert res.status_code == 200, res.text
    assert res.json() == mcp_oauth.client_metadata_document()
    assert "secret" not in res.text


async def test_the_document_is_404_with_the_flag_off(monkeypatch) -> None:
    f.configure(monkeypatch, enabled=False)
    res = await f.call(None, "GET", "/integrations/mcp/oauth/client-metadata")
    assert res.status_code == 404
