# Agent edge — the public door for outside agents

## Purpose

This unit serves outside agents on `https://mcp.metorite.com` (WS-55). The spec is `project-docs/specs/external_agent_access.md`. EA-2a built the public half of the authorization server. EA-3 adds the MCP endpoint `/mcp`.

## Routes (EA-2a)

- `GET /.well-known/oauth-authorization-server` is the RFC 8414 document.
- `GET /.well-known/oauth-protected-resource` is the RFC 9728 document. The same document also answers at the path of the resource, `/.well-known/oauth-protected-resource/mcp`.
- `POST /oauth/token` serves `authorization_code` with PKCE S256 and `refresh_token`.
- `POST /oauth/revoke` answers 200 for an unknown token (RFC 7009).

## The MCP endpoint (EA-3b-1)

- `POST /mcp` is the stateless MCP endpoint. `mcp.py` holds the transport and the order of the checks of spec §9.6.
- `bearer.py` reads the bearer token, looks it up, checks the audience and computes the effective scopes.
- `gateway_client.py` is the only client of the gateway. It sends the four signed headers of `acb_auth.agent_signing` and no other header that it sets.
- `cursor.py` signs and reads the page cursor of a list tool.
- `tools.py` holds the tool records and is the ONE module that maps a gateway response to a tool result (EA3-9). EA-3b-1 holds `list_tasks` and `search_tasks`. EA-3b-2 adds the other five read tools.

## Local contract

1. **The flag.** While `agent_access_enabled` is false, every request answers 404 and does nothing else. That covers every path and every method, `OPTIONS` and `HEAD` too. The ASGI middleware `EdgeGate` in `app.py` answers first, before the router. A wrong method then cannot leak a 405 with an `Allow` header.
2. **No import of the gateway.** This package never imports `gateway`, and never imports `gateway.outbound_guard`. The gateway half fetches a client's metadata. The edge reads the cache in `agent_client` only.
3. **The rules live in `acb_auth`.** Take the scopes, the addresses, the PKCE check and the flag from `acb_auth.agent_oauth`. Take every token act from `acb_auth.agent_tokens`. Do not copy a rule here.
4. **The tenant comes from a token row.** No route reads an organization or a member from a header, a query or a form field. The store binds the tenant from the row it finds.
5. **No token in a URL.** `token` and `revoke` refuse any query string. Every route refuses `access_token` in the query.
6. **One error for a bad grant.** Every refused code or refresh token answers `invalid_grant`. The log names the reason, and the response does not. The code spend matches four values: the client, the redirect URI, the S256 of the verifier and the resource. A wrong presentation spends nothing.
7. **No token, code or hash goes into a log line.** Log ids and reasons only.
8. **No rate limit yet.** EA-7 owns the limits. Do not add a limiter here before EA-7. The body cap of `token` and `revoke` is `agent_oauth_max_body_bytes`. The body cap of `/mcp` is `mcp.MAX_BODY_BYTES`, which is 1 MiB.
9. **No control character.** A C0 control character or DEL in a form field is `invalid_request`. Postgres refuses a NUL in text, so the edge must refuse it first.
10. **The edge holds no bearer.** A gateway request carries the four signed headers and nothing else that the edge sets. Never send an `Authorization` header or a member header to the gateway. Never copy a header of the client request into a gateway request.
11. **Keep the order of the checks.** The order of spec §9.6 is the flag, the key, the method, the origin, the body, the token, the version and the scope. Do not move a check.
12. **An outage is a 503, never a 401.** A 401 makes a client drop a good token. `acb_common.db_busy.is_db_outage` decides.
13. **Only check 8 sends `insufficient_scope`.** A gateway 403 is a tool error with a fixed text. Never send a gateway error text to an agent.
14. **Free text is untrusted.** Each free-text value goes out as `{"untrusted_text": ...}`. A tool returns only the keys that spec §9.1 names.

## Deploy

This unit is not deployed. EA-10 writes its unit file, its Caddy block and its DNS record, and those acts are OWNER-GATE. Run it locally with `uv run uvicorn agent_edge.app:app`.

## Fence

`tests/unit/test_agent_oauth_server.py` holds the behaviour of the OAuth routes, and it holds the structural fences of rules 2 and 4. `tests/unit/test_mcp_edge_protocol.py` holds rules 10 to 14 and the wire contract of `/mcp`. `tests/unit/test_mcp_edge_coverage.py` holds the tool records against `MCP_ROUTES` and the gateway routes.

## Child DOX Index

None. This is a leaf directory.
