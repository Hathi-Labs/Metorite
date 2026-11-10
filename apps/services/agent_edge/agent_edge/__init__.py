"""The public edge for outside agents (WS-55, spec ``external_agent_access.md``).

EA-2a serves the authorization server's public half here: the two metadata
documents, ``POST /oauth/token`` and ``POST /oauth/revoke``. EA-3 adds
``/mcp``. The app is :func:`agent_edge.app.create_app`.

⚠️ This package never imports the gateway, and never imports
``gateway.outbound_guard``. A fetch of a client's metadata happens in the
gateway half only. Fence: ``tests/unit/test_agent_oauth_server.py``.
"""
