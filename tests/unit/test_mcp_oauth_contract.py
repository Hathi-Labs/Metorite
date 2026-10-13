"""MCP OAuth: the contract with the ``mcp`` library and MAF (WS-54 IN-8a, O13).

Spec: ``project-docs/specs/integrations_console.md`` §3.6.7 and §3.6.8. The
flow reuses only the pure helpers of ``mcp.client.auth.utils`` (``mcp`` 1.30)
and the pydantic models of ``mcp.shared.auth``. All I/O goes through the
guard. This file pins each name and signature, so an upgrade of ``mcp`` or of
``agent_framework`` that changes one fails here, and not in front of an admin.

It also proves the two helpers that read an answer accept the answer of the
guard (``outbound_guard.Answer``), and that no helper opens a socket.
"""

from __future__ import annotations

import inspect
import socket
from typing import Any

import httpx
import pytest

#: The helpers of §3.6.8, with their parameter names.
HELPERS: dict[str, tuple[str, ...]] = {
    "extract_resource_metadata_from_www_auth": ("response",),
    "extract_scope_from_www_auth": ("response",),
    "build_protected_resource_metadata_discovery_urls": ("www_auth_url", "server_url"),
    "build_oauth_authorization_server_metadata_discovery_urls": (
        "auth_server_url", "server_url"),
    "validate_metadata_issuer": ("oauth_metadata", "expected_issuer"),
    "issuers_match": ("a", "b"),
    "is_valid_client_metadata_url": ("url",),
    "should_use_client_metadata_url": ("oauth_metadata", "client_metadata_url"),
}

#: The model fields that the flow reads.
MODEL_FIELDS: dict[str, tuple[str, ...]] = {
    "ProtectedResourceMetadata": ("resource", "authorization_servers", "scopes_supported"),
    "OAuthMetadata": (
        "issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint",
        "revocation_endpoint", "code_challenge_methods_supported",
        "token_endpoint_auth_methods_supported", "client_id_metadata_document_supported"),
    "OAuthClientInformationFull": ("client_id", "client_secret", "token_endpoint_auth_method"),
    "OAuthToken": ("access_token", "token_type", "expires_in", "scope", "refresh_token"),
}


@pytest.mark.parametrize("name", sorted(HELPERS))
def test_each_helper_keeps_its_name_and_signature(name: str) -> None:
    from mcp.client.auth import utils

    helper = getattr(utils, name, None)
    assert helper is not None, f"mcp.client.auth.utils.{name} is gone"
    assert not inspect.iscoroutinefunction(helper), f"{name} became async (I/O?)"
    assert tuple(inspect.signature(helper).parameters) == HELPERS[name]


def test_the_flow_imports_exactly_these_helpers() -> None:
    """A new helper in the flow is a decision: add it to §3.6.8 and here."""
    import ast
    from pathlib import Path

    path = (Path(__file__).resolve().parents[2] / "apps" / "services" / "gateway"
            / "gateway" / "connections" / "mcp_oauth.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
            and n.module == "mcp.client.auth.utils" for a in n.names}
    assert used == set(HELPERS), used ^ set(HELPERS)
    assert "OAuthClientProvider" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("model", sorted(MODEL_FIELDS))
def test_each_model_keeps_the_fields_the_flow_reads(model: str) -> None:
    from mcp.shared import auth

    cls = getattr(auth, model)
    missing = set(MODEL_FIELDS[model]) - set(cls.model_fields)
    assert not missing, f"{model} lost {missing}"


def test_the_helpers_read_the_answer_of_the_guard() -> None:
    from gateway.outbound_guard import Answer
    from mcp.client.auth.utils import (
        extract_resource_metadata_from_www_auth,
        extract_scope_from_www_auth,
    )

    answer = Answer(status_code=401, body=b"", headers=httpx.Headers({
        "www-authenticate": 'Bearer resource_metadata="https://x.test/.well-known/p", '
                            'scope="read write"'}))
    assert extract_resource_metadata_from_www_auth(answer) == "https://x.test/.well-known/p"  # type: ignore[arg-type]
    assert extract_scope_from_www_auth(answer) == "read write"  # type: ignore[arg-type]
    # A 200 names no metadata, as the helper says for a real response.
    ok = Answer(status_code=200, body=b"", headers=httpx.Headers())
    assert extract_resource_metadata_from_www_auth(ok) is None  # type: ignore[arg-type]


def test_no_helper_opens_a_socket(monkeypatch) -> None:
    from mcp.client.auth import utils
    from mcp.shared.auth import OAuthMetadata

    def _refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a helper opened a socket")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    urls = utils.build_protected_resource_metadata_discovery_urls(None, "https://m.test/mcp")
    assert urls == ["https://m.test/.well-known/oauth-protected-resource/mcp",
                    "https://m.test/.well-known/oauth-protected-resource"]
    urls = utils.build_oauth_authorization_server_metadata_discovery_urls(
        "https://github.test/login/oauth", "https://m.test/mcp")
    assert urls[0] == "https://github.test/.well-known/oauth-authorization-server/login/oauth"
    meta = OAuthMetadata.model_validate({
        "issuer": "https://a.test", "authorization_endpoint": "https://a.test/a",
        "token_endpoint": "https://a.test/t", "client_id_metadata_document_supported": True})
    utils.validate_metadata_issuer(meta, "https://a.test")
    assert utils.should_use_client_metadata_url(meta, "https://app.test/oauth/c.json")
    assert utils.is_valid_client_metadata_url("https://app.test/oauth/c.json")
    assert not utils.is_valid_client_metadata_url("http://app.test/oauth/c.json")


def test_the_header_provider_of_maf_keeps_its_signature() -> None:
    """§3.6.7: MAF calls ``header_provider`` synchronously, with the runtime
    keyword arguments, and wants a header map back. IN-8b builds on it."""
    pytest.importorskip("agent_framework")
    from agent_framework import MCPStreamableHTTPTool

    params = inspect.signature(MCPStreamableHTTPTool.__init__).parameters
    assert "header_provider" in params and "static_headers" in params
    annotation = str(params["header_provider"].annotation)
    assert "Callable[[dict[str, Any]], dict[str, str]]" in annotation, annotation
