"""The two discovery documents (RFC 8414 and RFC 9728), built from Settings.

Spec ``external_agent_access.md`` §5.3 and §5.5. Every address comes from
``acb_auth.agent_oauth``, so a test can assert it and a box can move it.
"""
from __future__ import annotations

from typing import Any

from acb_auth import agent_oauth


def authorization_server_metadata() -> dict[str, Any]:
    """RFC 8414 metadata for the issuer.

    ``client_id_metadata_document_supported`` says a client may use the URL of
    its metadata document as its id. Dynamic registration is EA-2b, so this
    document names no ``registration_endpoint``.
    """
    return {
        "issuer": agent_oauth.issuer(),
        "authorization_endpoint": agent_oauth.authorize_url(),
        "token_endpoint": agent_oauth.token_endpoint(),
        "revocation_endpoint": agent_oauth.revocation_endpoint(),
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": [agent_oauth.S256],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(agent_oauth.SUPPORTED_SCOPES),
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


def protected_resource_metadata() -> dict[str, Any]:
    """RFC 9728 metadata for the MCP resource.

    ``scopes_supported`` names the resource scopes only. ``offline_access`` is
    a matter for the authorization server (spec §8).
    """
    return {
        "resource": agent_oauth.resource(),
        "authorization_servers": [agent_oauth.issuer()],
        "scopes_supported": list(agent_oauth.RESOURCE_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "Metorite",
    }
