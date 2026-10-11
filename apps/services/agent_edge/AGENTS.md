# Agent edge — the public door for outside agents

## Purpose

This unit serves outside agents on `https://mcp.metorite.com` (WS-55). The spec is `project-docs/specs/external_agent_access.md`. EA-2a built the public half of the authorization server. EA-3 adds the MCP endpoint `/mcp`.

## Routes (EA-2a)

- `GET /.well-known/oauth-authorization-server` is the RFC 8414 document.
- `GET /.well-known/oauth-protected-resource` is the RFC 9728 document. The same document also answers at the path of the resource, `/.well-known/oauth-protected-resource/mcp`.
- `POST /oauth/token` serves `authorization_code` with PKCE S256 and `refresh_token`.
- `POST /oauth/revoke` answers 200 for an unknown token (RFC 7009).

## Local contract

1. **The flag.** While `agent_access_enabled` is false, every request answers 404 and does nothing else. That covers every path and every method, `OPTIONS` and `HEAD` too. The ASGI middleware `EdgeGate` in `app.py` answers first, before the router. A wrong method then cannot leak a 405 with an `Allow` header.
2. **No import of the gateway.** This package never imports `gateway`, and never imports `gateway.outbound_guard`. The gateway half fetches a client's metadata. The edge reads the cache in `agent_client` only.
3. **The rules live in `acb_auth`.** Take the scopes, the addresses, the PKCE check and the flag from `acb_auth.agent_oauth`. Take every token act from `acb_auth.agent_tokens`. Do not copy a rule here.
4. **The tenant comes from a token row.** No route reads an organization or a member from a header, a query or a form field. The store binds the tenant from the row it finds.
5. **No token in a URL.** `token` and `revoke` refuse any query string. Every route refuses `access_token` in the query.
6. **One error for a bad grant.** Every refused code or refresh token answers `invalid_grant`. The log names the reason, and the response does not. The code spend matches four values: the client, the redirect URI, the S256 of the verifier and the resource. A wrong presentation spends nothing.
7. **No token, code or hash goes into a log line.** Log ids and reasons only.
8. **No rate limit yet.** EA-7 owns the limits. Do not add a limiter here before EA-7. The body cap is `agent_oauth_max_body_bytes`.
9. **No control character.** A C0 control character or DEL in a form field is `invalid_request`. Postgres refuses a NUL in text, so the edge must refuse it first.

## Deploy

This unit is not deployed. EA-10 writes its unit file, its Caddy block and its DNS record, and those acts are OWNER-GATE. Run it locally with `uv run uvicorn agent_edge.app:app`.

## Fence

`tests/unit/test_agent_oauth_server.py` holds the behaviour, and it holds the structural fences of rules 2 and 4.

## Child DOX Index

None. This is a leaf directory.
