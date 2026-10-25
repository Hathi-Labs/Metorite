# Agent edge — the public door for outside agents

## Purpose

This unit serves outside agents on `https://mcp.metorite.com` (WS-55). The spec is `project-docs/specs/external_agent_access.md`. EA-2a built the public half of the authorization server. EA-3 adds the MCP endpoint `/mcp`.

## Routes (EA-2a)

- `GET /.well-known/oauth-authorization-server` is the RFC 8414 document.
- `GET /.well-known/oauth-protected-resource` is the RFC 9728 document. The same document also answers at the path of the resource, `/.well-known/oauth-protected-resource/mcp`.
- `POST /oauth/token` serves `authorization_code` with PKCE S256 and `refresh_token`.
- `POST /oauth/revoke` answers 200 for an unknown token (RFC 7009).

## The MCP endpoint (EA-3b-1 and EA-3b-2)

- `POST /mcp` is the stateless MCP endpoint. `mcp.py` holds the transport and the order of the checks of spec §9.6.
- `bearer.py` reads the bearer token, looks it up, checks the audience and computes the effective scopes.
- `gateway_client.py` is the only client of the gateway. It sends the four signed headers of `acb_auth.agent_signing` and no other header that it sets.
- `cursor.py` signs and reads the page cursor of a list tool. The cursor binds the tool name, so a cursor of one tool never pages another.
- `tools.py` holds the tool records and is the ONE module that maps a gateway response to a tool result (EA3-9). It holds the seven read tools of spec §9.1: `whoami`, `list_projects`, `get_project`, `list_tasks`, `get_task`, `get_task_timeline` and `search_tasks`. EA-6a adds the two write tools of spec §9.2, `set_task_status` and `add_task_reference`. Each needs `projects:write`, and branch 0 of the gateway checks the nonce, the scope, the writes flag and the cap. EA-12b adds the six agent-run tools of spec §12.1 and `agent_runs` in `get_task`. They show only while `agent_runs_enabled` is on, and the edge reads that flag through `agent_oauth.runs_enabled`. So the edge and the gateway need the same value.
- EA-6d adds two more write tools to `tools.py`, `create_task` and `update_task`. `idempotency.py` holds the slot of the `idempotency_key` of `create_task`, and it is the only edge module that reaches Redis.
- EA-6c adds the write tool `add_comment` to `tools.py`. Its `idempotency_key` uses the same slot. `tools._claim_slot` and `tools._keyed_write` hold the slot rules for both tools. Do not copy them into a third tool. Call them.
- EA-6e adds `prompts.py`, the one MCP prompt `work_on_task` (spec EA3-39). Its message holds only server text and the validated argument, and it makes no gateway request. `mcp.py` only declares the capability and dispatches `prompts/list` and `prompts/get`. The prompt and the routine part of the `initialize` instructions show on one test, `prompts.offered`.
- EA-6b adds `claim_task` over `POST /projects/tasks/{task_id}/claim`. `_write` maps each code of a 422 of that route to a fixed text of `CLAIM_TEXTS`. EA-6f lets `set_task_status` move a task to done. A move refuses only `AGENT_MOVE_REFUSED` (cancelled), and `create_task` refuses every category of `CLOSING_CATEGORIES`. Each copy of a gateway constant has a pin test.
- EA-13f: each task that a tool returns also holds `url` and `short_id` (`tools.task_url` and `tools.task_short_id`). The url has the host of `app_public_url`. The short id is `mt-` and eight hex characters. The routine tells the agent to put the url in each pull request description. It also tells the agent to put the short id in the branch name. GitHub then links the pull request to the task. The text of `prompts.py` names the two fields and holds no url.
- EA-13c: `claim_task` also returns `agent_run_id`, the run that the claim opened or found. The edge returns it only in the shape of an id, and null while agent runs are off.
- EA-6g adds `request_task_archive` over `POST /projects/tasks/{task_id}/archive-request`. It only asks. The run's member approves on the task in Metorite, through a route that is not in `MCP_ROUTES`. The edge never names that route. The gateway keeps the `idempotency_key`, as for `ask_on_task`, so the tool has no edge input. It shows only while the runs flag is on.
- EA-15 (spec §12.4) adds two reads that compose routes already in `MCP_ROUTES`, with no new pair.
  - `get_task_context` returns the task, its parents, its project, its blockers and the newest 20 comments. A hidden parent is one stub `{"hidden": true}`. The tool makes at most 12 gateway calls (`tools.CONTEXT_CALL_CEILING`).
  - `my_work` returns the next work of this client. The waiting runs come first. Then come the tasks of a quiet run of this client (derived state `stale`), and a live run of this client never shows. SW-13b: both groups keep a run of the caller's own connection only, so the work of another machine never shows. Then come the assigned tasks with no live holder and a `blocked_by_count` of zero. It filters the runs by the caller's stamp, `gateway_client.actor_via`. The same function signs `X-Actor-Via`. With the runs flag off, it reads no run.
  - The server instructions and the prompt `work_on_task` give the six steps of §12.4. A session starts with `my_work`.
- EA-6h adds `delete_task` over `POST /projects/tasks/{task_id}/agent-delete` (EA3-51). It is the one tool with `destructiveHint: true` (`DELETE_ANNOTATIONS`). `tools/list` shows it only while the member's switch is on. `tools.listed_for` reads the switch through one signed `GET /auth/me/agent-access`, and any failure reads as off. The `initialize` instructions and the prompt never name a delete. The tool sends `expected_version`, `confirm_title` and `subtasks_to_delete` (`none`, `all` or a list of ids, D-OP-10). The edge never sends the `PUT` of the switch.

- SW-7 (spec §12.5): `initialize` answers an `Mcp-Session-Id` that `agent_signing.mint_mcp_session` makes. It binds the connection and carries an HMAC, so the edge keeps no state. `mcp._session_of` reads it back on each later request (check 7b). An id that does not verify gets 404. The session key goes into `tools.Context.session`, and `gateway_client` signs it in as `X-Agent-Session`. `my_work` keeps the runs of this session and the runs with no key.

## Local contract

1. **The flag.** While `agent_access_enabled` is false, every request answers 404 and does nothing else. That covers every path and every method, `OPTIONS` and `HEAD` too. The ASGI middleware `EdgeGate` in `app.py` answers first, before the router. A wrong method then cannot leak a 405 with an `Allow` header.
   **The organization allowlist (EA-10p, spec §10.6).** `agent_access_orgs` limits the flag to the organizations that it names, and the default names none. The token endpoint answers `invalid_grant` for an organization off the list, because the store refuses the issue. The bearer check of `/mcp` answers 401 with `invalid_token`. Take the rule from `agent_oauth.org_allowed`, and take the organization from the token row. `/oauth/revoke` does not check the list.
2. **No import of the gateway.** This package never imports `gateway`, and never imports `gateway.outbound_guard`. The gateway half fetches a client's metadata. The edge reads the cache in `agent_client` only.
3. **The rules live in `acb_auth`.** Take the scopes, the addresses, the PKCE check and the flag from `acb_auth.agent_oauth`. Take every token act from `acb_auth.agent_tokens`. Do not copy a rule here.
4. **The tenant comes from a token row.** No route reads an organization or a member from a header, a query or a form field. The store binds the tenant from the row it finds.
5. **No token in a URL.** `token` and `revoke` refuse any query string. Every route refuses `access_token` in the query.
6. **One error for a bad grant.** Every refused code or refresh token answers `invalid_grant`. The log names the reason, and the response does not. The code spend matches four values: the client, the redirect URI, the S256 of the verifier and the resource. A wrong presentation spends nothing.
7. **No token, code or hash goes into a log line.** Log ids and reasons only.
8. **No rate limit yet.** EA-7 owns the limits. Do not add a limiter here before EA-7. The body cap of `token` and `revoke` is `agent_oauth_max_body_bytes`. The body cap of `/mcp` is `mcp.MAX_BODY_BYTES`, which is 1 MiB.
9. **No control character.** A C0 control character or DEL in a form field is `invalid_request`. Postgres refuses a NUL in text, so the edge must refuse it first.
10. **The edge holds no bearer.** A gateway request carries the four signed headers and nothing else that the edge sets. In an MCP session (SW-7) it also carries the signed `X-Agent-Session`. A write (EA-6a) also carries the signed `X-Agent-Nonce`, a JSON `Content-Type` and `If-Match` when the tool sends a version. Only `gateway_client.write` sends a write, and only for a pair of `agent_oauth.MCP_WRITE_ROUTES`. Never send an `Authorization` header or a member header to the gateway. Never copy a header of the client request into a gateway request.
11. **Keep the order of the checks.** The order of spec §9.6 is the flag, the key, the method, the origin, the body, the token, the version and the scope. Do not move a check.
12. **An outage is a 503, never a 401.** A 401 makes a client drop a good token. `acb_common.db_busy.is_db_outage` decides.
13. **Only check 8 sends `insufficient_scope`.** A gateway 403 is a tool error with a fixed text. Never send a gateway error text to an agent.
14. **Free text is untrusted.** Each free-text value goes out as `{"untrusted_text": ...}`. A tool returns only the keys that spec §9.1 names. A value that a member writes and the gateway does not shape-check leaves as a plain string only in its shape (spec §9.1, mark (S)). Today these are `task_prefix`, `timezone` and each assignee.
15. **One mapping module (EA3-9).** Only `tools.py` may hold an identity literal such as `"email"`, and only as a `.get()` argument, a subscript index or a dict-display key. `tools.py` reads no request header, query, form, cookie or path parameter, and imports nothing from `fastapi` or `starlette`. Every other edge module holds no identity literal at all. One position is exempt (EA-6e). In `prompts.py`, `"user"` may be the value of the `"role"` key of a prompt message. That value is the MCP role, not an identity. ⚠️ The AST tests of this rule are a heuristic that catches the common shapes. They miss a literal that reaches a read through a variable, such as a value from a dict key, and an attribute name that the code computes without `getattr`. The real guards are that `tools.py` receives no `Request` object and that coverage rule 4 holds.
16. **An edge input needs a spec amendment.** An input that the edge applies itself, because the route takes no such parameter, goes in the tool record's `edge_inputs`. Spec §9.5 rule 4 names the seven that exist. Four are of `list_projects`. The others are the `idempotency_key` of `create_task` (EA-6d) and of `add_comment` (EA-6c), and the `limit` of `my_work` (EA-15b).
17. **Redis through the tenant wrapper only (EA-6d, R5).** Only `idempotency.py` reaches Redis. It uses `acb_common.tenant_redis` and binds the organization of the token row, never a request input. A slot key holds the tool name and the SHA-256 of the agent's key (EA3-35), and no log line holds the raw key. `Slot` takes the tool as a keyword with no default. A store outage at the claim gives the busy text and no write. A call with no key touches no store.
18. **A write body holds only the named keys (EA-6d).** Each write tool builds its body from a fixed tuple of names (`CREATE_BODY_KEYS`, `UPDATE_BODY_KEYS`, `COMMENT_BODY_KEYS`), never from the whole input. The gateway refuses any other key of a signed agent write too (`routes/projects/tasks.py`).

## Deploy

Spec §15.3 pins the deploy (EA-10). The unit is `deploy/hostinger/acb-agent-edge.service` on `127.0.0.1:8091`. It runs as a systemd `DynamicUser`, never as `acb`, so the edge cannot read a 0600 file of `acb`. systemd loads `apps/services/agent_edge/.env` as root. The process starts in the empty `/run/acb-agent-edge`, so `Settings` opens no `.env`. Never set `WorkingDirectory` to the checkout root or to this directory.

The deploy starts the edge only after the owner enables the unit. Caddy serves it as `mcp.metorite.com`, and the wildcard DNS record already covers that host. Run it locally with `uv run uvicorn agent_edge.app:app`.

## Fence

`tests/unit/test_agent_oauth_server.py` holds the behaviour of the OAuth routes, and it holds the structural fences of rules 2, 4 and 15. `tests/unit/test_mcp_edge_protocol.py` holds rules 10 to 14 and the wire contract of `/mcp`. `tests/unit/test_mcp_edge_coverage.py` holds the tool records against `MCP_ROUTES` and the gateway routes, and rule 16. `tests/unit/test_mcp_edge_writes.py` holds the write tools of EA-6a, with R8 classes through the real gateway app. `tests/unit/test_mcp_edge_agent_runs.py` holds the agent-run tools of EA-12b, the flag, the long poll and the text of the tool descriptions. `tests/unit/test_mcp_edge_task_writes.py` holds the tools of EA-6d and rules 17 and 18, with R8 classes and a real Redis class. `tests/unit/test_mcp_edge_comments.py` holds `add_comment` of EA-6c, with R8 classes through the real gateway app. `tests/unit/test_mcp_edge_claim.py` holds `claim_task` and the done rule of EA-6f, with R8 classes. `tests/unit/test_mcp_edge_archive.py` holds `request_task_archive` and the member's approval of EA-6g, with R8 classes. `tests/unit/test_mcp_edge_delete.py` holds `delete_task`, the switch and the subtask choice of EA-6h, with R8 classes.

`tests/unit/test_mcp_edge_prompts.py` holds the prompt of EA-6e and the routine part of the instructions. Its class `TestTheProtocol` holds the six steps of EA-15c. `tests/unit/test_mcp_edge_context.py` holds `get_task_context` and `my_work` of EA-15, with one test for each rule of spec §12.4.

## Child DOX Index

None. This is a leaf directory.
