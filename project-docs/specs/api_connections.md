# API connections: any outside API that a company connects, usable in the chat

**Status: PROPOSED, 2026-11-18. Nothing is built.** Task 3562 (CHAT-REACH-5).
Verified against the code on 2026-11-18 (`origin/main` at `7ec269e82`). The
owner decides D-API-1 to D-API-8 (§8). The defaults hold until the owner
answers.

**Owner ask, 2026-11-16, and the customer ask of 2026-11-17.** The words are
the owner's, so they sit in a block that the linter does not read.

```text
"Any MCP, API, etc. that I add should be accessible via the chat."
```

A member of Shaurrya Tele asked the chat for monthly revenue and capex "from
the books" on 2026-11-17 (task 3730). Shaurrya then connected Zoho Books
through Integrations → API keys → "Add API". The tile said connected. The chat
could read nothing.

## 0. The answer in one screen

**Today, an API that a company adds does not reach the chat.** "Add API"
stores a field list and the keys. No chat code reads them (§1). Plugins and
seven key connectors are in the same state (`chat_reach.md` gap G6).

**The proposal.** An **API connection** is one row of `integration_connections`.
A reviewed **template** makes the row. The template names the hosts, the
sign-in, and a closed list of **operations**. The gateway makes each call with
the org's sealed credential, through `outbound_guard`.

**The chat side.** The chat gets three native tools for each connection. They
act as an attached MCP server acts. They have the same early exits, the same
grant and the same gate facts. They also get the same untrusted mark and the
same brief.

**What it reuses.** The connection store, the egress guard and the shared
token refresh. Also the Zoho OAuth client of the CRM, the row grammar of the
app door, the MCP permission gate and `server_brief`.

**What it adds.** A template file for each API, one gateway route family, one
attach step, and the connect flow in the console.

**It does not wait for IN-3 (task 3176).** The gateway reads the credential in
the tenant session, as GitHub and the calendar sync do. The resolvers of
`acb_skills.integrations` are not on the path (§1, row 9).

**The first demo (rung 1).** A Shaurrya member asks "What was our revenue last
month?" The chat answers from Zoho Books and names the source.

## 1. What exists

| # | Fact | Evidence |
|---|---|---|
| 1 | "Add API" asks a model for a list of credential fields. It stores the list and the keys. No chat path reads either | `routes/integrations.py` `discover_api`, `list_custom_apis`, `_custom_status_row`. `integrations_console.md` IN-D13 |
| 2 | The tile says `configured: true` when each field holds a value. Bug task 3766 | `routes/integrations.py` `_custom_status_row` |
| 3 | Plugins store an OpenAPI document and generated tools. No code outside the route reads them | `routes/integrations.py` `install_plugin`, `_openapi_to_tool_defs` (line 2591) |
| 4 | No chat or agent code reads the keys of Apollo, Google Maps, Instantly, Apify, Anymailfinder, Google Sheets or SMTP | `tests/unit/test_connector_chat_reach.py` names each as `gap` |
| 5 | An org MCP server attaches as tools of a native run. The gate decides a card from the tool hints, "always confirm", the read-only key and the member fact (MCP-NC1) | `orchestrator/_maf_mcp.py` `attach_mcp_tools` and `_McpPermissionGate`. `acb_skills/permission_policy.py` `decide` |
| 6 | The MCP client refuses a non-public address and an address local to the host. So a run cannot reach an MCP endpoint that the gateway serves on the same box | `gateway/outbound_guard.py` `check_mcp_url`, `check_url` |
| 7 | `outbound_guard.request` pins the DNS answer, refuses a redirect, and caps an answer at 64 KiB and 10 s | `outbound_guard.py` `MAX_ANSWER_BYTES`, `request` |
| 8 | `integration_connections` seals a token or a header map with Fernet. One module names the sealed column | `connections/store.py`. `infra/postgres/245_integration_connections.sql` |
| 9 | A gateway reader loads a credential in the tenant session, with no environment read | `connections/github.py` `token_for_org`. `connections/shared_token.py` `fresh_credential` |
| 10 | The CRM holds one Zoho OAuth client for eight data centres: consent, code exchange and refresh. The caller passes the scopes | `crm_sources/zoho/auth.py` `ZOHO_DATA_CENTRES`, `consent_url` |
| 11 | The app door has a row grammar for an operation: method, path, path shapes, query keys, class A, B or C, and paging. `build_call` makes a request from a row | `acb_common/app_manifests/base.py` `Route`, `build_call` |
| 12 | Packs are a static registry. A pack cannot hold tools for one org | `acb_skills/tool_packs.py` `PACKS`, `Pack` |
| 13 | The Studio grammar already has `integration:<name>.<name>` and `mcp:<server>.<tool>`. `action_tool` answers None for both today | `acb_common/studio_manifest.py`. `acb_skills/skill_packs.py` `action_tool` |
| 14 | The grant of a service is `integrations:use:<service>`. The admin role holds `integrations:use:*` | `acb_auth/permissions.py` |

## 2. The shape

### 2.1 The template: a closed list of operations

A template is a reviewed file in the repository, one for each API. A template
holds these parts:

- **Identity.** An id such as `zoho_books`, a label, and one line that says
  what the API holds.
- **Hosts.** The https hosts, for each region where the API has one. A call
  reaches only a host on this list.
- **Sign-in.** One of: OAuth with a code grant, an API key in a header, an API
  key in the query, or basic. The template names the header scheme, such as
  `Zoho-oauthtoken`. It can name a client that Metorite already holds, such as
  the CRM's Zoho client.
- **Context.** The values that each call needs and the connect step finds,
  such as the Zoho `organization_id`.
- **Operations.** Each operation is one row of the door's `Route` grammar
  (§1, row 11). It has an op name, a method, a path and the argument shapes.
  It also has a class (A reads, B and C write), a page size and an answer cap. A row can mark
  itself a **shape read**: a class A read with no required argument.
  `semantic_model.md` uses these reads to profile the connection.
- **Limits.** The rate limit of the API, such as calls each minute.

**The chat never sends a path, a host or a method.** It sends an op name and
arguments. The gateway checks the arguments against the row. Without this
rule, the egress check and the read or write class mean nothing.

**Where a template comes from (D-API-1).**

| Tier | Source | Who sets it up | Sign-in |
|---|---|---|---|
| T1 | A first-party template, merged by a reviewed pull request | Metorite | One click (owner rule: a customer does no technical setup) |
| T2 | The OpenAPI document of the API. The admin gives its address. The gateway fetches it through the guard and converts it with the plugin converter. The admin picks the operations | The admin | API key or basic. OAuth only where a T1 client exists |
| T3 | No OpenAPI document. The AI drafts operations from the API's docs page. The admin approves each one | The admin | As T2 |

Each T2 and T3 operation starts as class A for GET and class C for any other
method. A connection shows "connected" only after one test read succeeds. This
also closes bug 3766.

### 2.2 The connection row

- `provider` is `api:<template id>`. `owning_app` is `integrations`.
  `scope_kind` is `org`. A member connection is a later slice.
- `provider_meta` holds only public facts: the region, the API host, the
  context values, the template version and the hash of the approved
  operations.
- `credentials_encrypted` holds the token blob or the header map, as
  `connections/store.py` seals them today.
- A template change that adds or changes an operation asks the admin again.
  STU-B1b-3 does this for a changed MCP tool. This spec reuses that rule.

### 2.3 The gateway route

Three routes, under the tenant session of the signed member:

- `GET /connections/api/{name}/ops?q=` finds operations by words.
- `GET /connections/api/{name}/ops/{op}` describes one operation.
- `POST /connections/api/{name}/call` takes `{op, args}`.

A call does these steps in order:

1. Load the row and the template. Refuse an op that the admin did not approve.
2. Check the arguments against the row. Build the request with `build_call`.
3. Get a fresh credential with `shared_token.fresh_credential` and the
   template's refresh.
4. Send the request through `outbound_guard.request` to a template host only.
5. Remove hidden characters. Remove each field that the template marks
   secret. Cap the answer at the row's cap.
6. Write one log line, `api.call`: the connection id, the op, the status, the
   time and the size. Never an argument value, a header or a token.

An answer over its cap says so, and names the page argument. The chat then
asks for a smaller page or a narrower filter.

### 2.4 The chat side

**Three native tools for each attached connection**, with the connection name
as a prefix:

- `<name>_find_operations(words)`
- `<name>_describe_operation(op)`
- `<name>_call_operation(op, args)`

This is the "dynamic tools" shape of large API servers (§7). It keeps the
schema small, whatever the size of the API.

**The attach step runs with `_inject_mcp_servers`, and takes its rules:**

- No attach for a run with no member, a batch run, or a run with no egress.
- No attach when the run holds the email pack (OA-7b). This keeps stranger mail
  away from an outbound API. The WS-CSY precedence fix applies to both kinds.
- No attach without the grant `integrations:use:api-<name>`. A member never
  learns of a connection that the member cannot use.
- The tools carry the MCP marker, so `_from_mcp` sees them. Each answer is
  framed as untrusted, and it marks the session (step 3a of `decide`).

**The gate.** `_McpPermissionGate` learns each connection as a server. Class A
gives the read hint. Class B and C give the write hint. The template sets
"always confirm" on each write op (D-API-4).

**The brief.** `server_brief` (WS-CSY §3.1) takes the template's one line and
the op names as its inputs. This spec adds no second brief.

**Skills.** An operation is the action `integration:<name>.<op>` in a Studio
skill, the grammar that TOF-5b planned. `action_tool` maps it in the same
slice as TOF-5a maps `mcp:`.

### 2.5 Writes

Read-only first. A template can hold class B and C operations, but they stay
off until the owner turns writes on for an org (D-API-4). Then each write asks
a card, also in a member's own run. A write is refused in WhatsApp, in a run
with no member, and on a closed channel, as an MCP write is today.

### 2.6 Secrets, egress and data

- The chat never sees a key, a token, a host or a header.
- A call reaches only a template host. No redirect, a pinned DNS answer, and
  the answer caps of the guard.
- An answer is third-party text. It is framed untrusted and marks the session.
- The data of an API goes to the model vendor of the chat, as MCP data does
  today (D-API-7).

### 2.7 What happens to "Add API", plugins and the key connectors

- **"Add API".** It becomes "Connect an API": the T1 list first, then T2 by an
  OpenAPI address. The discovery call to the model goes (it is not metered,
  IN-D15). An old custom row reads "the chat cannot use this yet" (task 3766),
  with a link to the template that replaces it.
- **Plugins.** T2 takes the OpenAPI converter. The `plugins` table retires
  (D-API-6).
- **The seven key connectors.** Each becomes a T1 template with an API key.
  SMTP is not an HTTP API, so it stays a gap with that reason.

### 2.8 Zoho Books, the first template

Task 3749 owns the books connectors (D-SEM-8). This section only names what
the mechanism must carry for Zoho Books.

- **Sign-in.** The CRM's Zoho client, with the Books read scopes. One click,
  and every Zoho data centre. Check at API-2 that the client may ask for the
  Books scopes.
- **Context.** The connect step reads `GET /organizations` and keeps the
  `organization_id`. The admin picks one when the account has more than one.
- **Header and limits.** The scheme is `Zoho-oauthtoken`. Third-party guides
  give 100 calls a minute for each organization, and a daily cap by plan.
  Check both against Zoho's own page before API-3.
- **Totals.** Check at API-1 whether the Zoho Books API has report
  operations, such as profit and loss. A connector vendor exposes a report of
  that name, but Zoho's API page was not read for this spec. If no report
  operation exists, a total comes from a list read with paging. The model must
  not add numbers by hand (`semantic_model.md`).

## 3. Options not taken

| Option | Why not |
|---|---|
| The gateway serves each connection as an MCP endpoint, and the run attaches it | The MCP client refuses a host-local address (§1, row 6). An exception widens the SSRF surface. The native tools keep the MCP behaviour without the hop |
| A tool pack for each API | A pack is static code. It cannot hold tools for one org (§1, row 12) |
| An API as an app of the door (`App`) | An app needs a router, a pane and a feature. The manifests are static. This spec reuses the door's `Route` rows only |
| A free path on the provider host (TOF-5c as drafted) | The chat could then reach any endpoint, and the read or write class would mean nothing. An admin adds an operation instead (D-API-2) |

## 4. The ladder

Each rung ends in a demo on production (D211).

| Rung | The demo | Slices |
|---|---|---|
| 0 | The chat reads Zoho Books through Zoho's own MCP server, with no code | Task 3767 |
| 1 | A Shaurrya member asks for last month's revenue and the largest unpaid invoices. The chat answers from Zoho Books | API-1 to API-6 |
| 2 | A member reads a Google Sheet in the chat | API-7: T1 templates for the key connectors, Google Sheets first |
| 3 | An admin connects an API by its OpenAPI address and picks operations | API-8: T2, and the end of the plugin store and the discovery call |
| 4 | The chat writes through an API, on a card | API-9: writes, after D-API-4 |
| 5 | A saved skill runs an API operation | With TOF-5a and TOF-5b |

**Rung 1 slices.** Each ships dark behind `API_CONNECTIONS_ORGS`.

| Id | What | Tier |
|---|---|---|
| API-1 | The template grammar and its validator, on the door's `Route` row. The Zoho Books template with read operations | STANDARD |
| API-2 | The connect flow: the Zoho client with Books scopes, the context step, the test read, the status | HIGH (identity, a credential) |
| API-3 | The three gateway routes: the op check, the refresh, the guard, the caps and the log line | HIGH (egress, a credential) |
| API-4 | The attach step, the gate facts and the brief inputs | HIGH (permission) |
| API-5 | The console: the connector list rows, the result card (IN-9) and the "used by" sheet (IN-10) | STANDARD |
| API-6 | Bug 3766: a custom row that the chat cannot use says so | LOW |

## 5. Fences (R7)

| Rule | Fence |
|---|---|
| Each template is valid: https hosts, a class on each op, no free path | `tests/unit/test_api_templates.py` (new) |
| A run in org A cannot call a connection of org B | `tests/unit/test_api_connections_r8.py` (new, R8) |
| No answer and no log line holds a credential | A sentinel case in the R8 suite |
| A call never reaches a host outside the template | `tests/unit/test_api_call_egress.py` (new) |
| A write op is refused with no member and in WhatsApp | `tests/unit/test_maf_mcp_confirm.py` (extended) |
| Each `gap` row of G6 becomes a path or a written reason | `tests/unit/test_connector_chat_reach.py`, `test_integration_kinds_chat_reach.py` |

## 6. Seams that other specs own

| Topic | Owner | What this spec takes |
|---|---|---|
| The registry and the console | `integrations_console.md` | IN-6 and Q2 resolve here: the custom APIs get a reader, or retire |
| The brief, the mail precedence, the `mcp.call` line | `chat_connected_systems.md` (WS-CSY) | The brief inputs. The ten CSY rules hold |
| The profile of a connection | `semantic_model.md` (WS-SEM) | Shape reads, marked in the template |
| Skills over connected systems | `agent_sandbox.md` §0b (TOF-5a, TOF-5b, TOF-5c) | TOF-5c becomes D-API-2 here |
| The reach matrix and its fence | `chat_reach.md` (task 3440) | Gap G6 |
| The books connectors | Task 3749 (D-SEM-8) | The Zoho Books template |

## 7. Prior art

- **Dynamic tools for large APIs.** Stainless serves an API of more than 50
  endpoints as three tools: list the endpoints, get one schema, call one.
  https://www.stainless.com/changelog/mcp-dynamic-tools/
- **A curated list beats a full conversion.** The FastMCP docs say models do
  better with curated servers than with a whole OpenAPI document converted.
  https://gofastmcp.com/integrations/openapi
- **A proxy over a connected account.** Pipedream Connect inserts the account's
  credentials and refreshes OAuth tokens. The caller never holds them.
  https://pipedream.com/docs/connect/api-proxy
- **Zoho Books.** Each call needs the `organization_id` and the domain of the
  data centre. https://www.zoho.com/books/api/v3/introduction/

## 8. Owner decisions

| Id | Question | Recommendation |
|---|---|---|
| **D-API-1** | Which APIs: first-party templates only, or also an API by its OpenAPI address (T2), or also an API with no OpenAPI (T3)? | T1 and T2. T3 later, with admin approval of each operation |
| **D-API-2** | A closed operation list, or any path on the provider host (TOF-5c)? | Closed. An admin adds an operation |
| **D-API-3** | Who may use a connection? | As an MCP server: the admin role, and each group the admin grants |
| **D-API-4** | Writes | Read-only first. Then each write asks a card, also in a member's own run |
| **D-API-5** | Zoho Books before Tally (the order of D-SEM-8)? | Yes. A customer asked, the Zoho client exists, and IN-3 is not needed |
| **D-API-6** | Retire the discovery call of "Add API" and the plugin store (IN-6, Q2)? | Yes, when T2 lands. Until then the tile says that the chat cannot use it |
| **D-API-7** | The data notice: an API's data reaches the chat's model vendor | The same notice as MCP and LNK-Q2 |
| **D-API-8** | The flag | `API_CONNECTIONS_ORGS`, to all orgs after the rung 1 demo |
