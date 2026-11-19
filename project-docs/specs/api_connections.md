# API connections: any outside API that a company connects, usable in the chat

**Status: ACTIVE (owner in chat, 2026-11-19, D228). The owner accepted
D-API-1 to D-API-9 as recommended, on task 3562 (answer `97b5d6ce`). API-1
is built (task 3787): the template grammar, the validator, the one builder and
the Zoho Books template. API-2 is built (task 3788): the connect flow of §2.11,
dark.**

**No chat tool and no call route exists yet (API-3, API-4). API-1 declares
the flag `API_CONNECTIONS_ORGS`, and it is empty.**

Zoho Books is an owner priority and the first template. Verified
against the code on 2026-11-18 (`origin/main` at `7ec269e82`). §8 records each
answer.

Review round 1 (metorite-f8 and metorite-7d, 2026-11-18) changed four
things. One shared tool set, reads and writes split, the session mark
decided, and §2.8 on IN-3.

Review round 2 (the Opus reviewer of metorite-7d, 2026-11-18) said GO WITH
CHANGES. The gateway now enforces the read and write split itself (§2.3). The
query keys of a GET are a closed list (§2.1). §2.7 adds the https rule, the
token hosts, the echo strip and the call limits. §8 names the two plans that
this spec reverses.

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
seven key connectors are in the same state (gap G6 of the reach matrix, task
3440, PR 2590).

**The proposal.** An **API connection** is one row of `integration_connections`.
A reviewed **template** makes the row. The template names the hosts, the
sign-in, and a closed list of **operations**. The gateway makes each call with
the org's sealed credential, through `outbound_guard`.

**The chat side.** The chat gets one shared set of tools for all
connections: find an operation, describe it, `api_read` and `api_write`. The
connection is an argument. The two call tools act as an attached MCP server
acts. They have the same early exits, grant, gate facts and brief.

**The gateway is the guard.** It checks the grant, the class of the op and
the write switch at each call. A caller with the internal token cannot run a
write through the read route.

**What it reuses.** The connection store, the egress guard and the shared
token refresh. Also the Zoho OAuth client of the CRM, the row grammar of the
app door, the MCP permission gate, `server_brief` and the Studio call limiter.

**What it adds.** A template file for each API, one gateway route family, one
attach step, and the connect flow in the console.

**It does not wait for IN-3 (task 3176).** The gateway reads the credential in
the tenant session, as the MCP attach does. The resolvers of
`acb_skills.integrations` are not on the path. §2.8 says what IN-3 protects,
and why this path keeps it. Two plans put their work after IN-3, so the owner
decides both reversals (D-API-2, D-API-5).

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
| 7 | `outbound_guard.request` pins the DNS answer, refuses a redirect, and caps an answer at 64 KiB and 10 s. It allows http on any port. `request_https` adds two rules: https only, on port 443 only | `outbound_guard.py` `MAX_ANSWER_BYTES`, `request`, `request_https` |
| 8 | `integration_connections` seals a token or a header map with Fernet. One module names the sealed column | `connections/store.py`. `infra/postgres/245_integration_connections.sql` |
| 9 | A gateway reader loads a credential in the tenant session, with no environment read | `connections/github.py` `token_for_org`. `connections/shared_token.py` `fresh_credential` |
| 10 | The CRM holds one Zoho OAuth client for eight data centres: consent, code exchange and refresh. The caller passes the scopes | `crm_sources/zoho/auth.py` `ZOHO_DATA_CENTRES`, `consent_url` |
| 11 | The app door has a row grammar for an operation: method, path, path shapes, class A, B or C, and paging. `build_call` makes a request from a row of an `App` | `acb_common/app_manifests/base.py` `Route`, `build_call` |
| 12 | For a GET, `build_call` sends each argument that is not a path key as a query key. `Route` refuses `query` on a GET row, so the door cannot close the query keys of a GET. A path value goes into the path with no encoding. API-1 changed both: `query_shapes` closes the query keys of an API op, and the one builder encodes each path value | `app_manifests/base.py` `build_call` (line 414 before API-1, line 550 after it), `Route._check_door` (line 154 before API-1, line 199 after it) |
| 13 | Packs are a static registry. A pack cannot hold tools for one org | `acb_skills/tool_packs.py` `PACKS`, `Pack` |
| 14 | The Studio grammar already has `integration:<name>.<name>` and `mcp:<server>.<tool>`. `action_tool` answers None for both today | `acb_common/studio_manifest.py`. `acb_skills/skill_packs.py` `action_tool` |
| 15 | The grant of a service is `integrations:use:<service>`. The admin, manager and member roles each hold `integrations:use:*`, so that grant alone keeps no member out. Only the admin role holds `integrations:manage` | `acb_auth/permissions.py`. Migrations 131, 179 and 200 |
| 16 | The Studio live read limits calls: 10 for each member and 60 for each org, each minute. Over the limit is a 429 with `Retry-After` | `connections/mcp_tools.py` `MEMBER_CALLS_PER_WINDOW`, `ORG_CALLS_PER_WINDOW`, `_spend` |

## 2. The shape

### 2.1 The template: a closed list of operations

A template is a reviewed file in the repository, one for each API. A template
holds these parts:

- **Identity.** An id such as `zoho_books`, a label, and one line that says
  what the API holds.
- **Hosts.** The https hosts, for each region where the API has one. A call
  reaches only a host on this list. A template that uses a client Metorite
  holds points to that client's host list, and does not copy it. The Zoho
  templates point to `ZOHO_DATA_CENTRES`, the one list of Zoho hosts.
- **Sign-in.** One of: OAuth with a code grant, an API key in a header, an API
  key in the query, or basic. The template names the header scheme, such as
  `Zoho-oauthtoken`. It can name a client that Metorite already holds, such as
  the CRM's Zoho client.
- **Context.** The values that each call needs and the connect step finds,
  such as the Zoho `organization_id`. The template names the key of each
  value, and the key of the sign-in when it goes in the query.
- **Operations.** Each operation is one row (below). It has an op name, a
  method, a path, the path shapes and the **query keys, each with a shape**.
  It also has a class (A reads, B and C write), a page size and an answer cap.
  A row can mark itself a **shape read**: a class A read with no required
  argument. `semantic_model.md` uses these reads to profile the connection,
  with no member (SEM rule 8). A T1 row gets the mark in review. A T2 or T3
  row gets it only when the admin approves it as one, because a GET can still
  act.
- **Limits.** The rate limit of the API, and its daily cap where it has one.

**The chat never sends a path, a host or a method.** It sends an op name and
arguments. The gateway checks the arguments against the row. Without this
rule, the egress check and the read or write class mean nothing.

**The arguments are a closed list.** An argument is a path key or a query key
of the row, and it matches its shape. The gateway refuses any other key. It
also refuses an argument that names a context key or a sign-in key. The gateway
sets those keys itself, after the arguments. So a model cannot send another
`organization_id` and read the books of another company that the connecting
Zoho user can see.

**A path value cannot leave its place in the path.** The validator refuses a
path shape that allows `/`, `?`, `#`, `%` or `..`. The gateway also
percent-encodes each path value.

**How the template fits the door's grammar.** The door's `build_call` needs an
`App`, and §3 rejects an `App` for an API. API-1 splits `build_call` into a
builder over one `Route` with its query shapes. `build_call(app, …)` then calls
that builder. The template calls it too, so one builder stays.

The template validator does not run `Route._check_chat`, because an API op
has no door tool. It runs the template rules of §5.

**Where a template comes from (D-API-1).**

| Tier | Source | Who sets it up | Sign-in |
|---|---|---|---|
| T1 | A first-party template, merged by a reviewed pull request | Metorite | One click (owner rule: a customer does no technical setup) |
| T2 | The OpenAPI document of the API. The admin gives its address. The gateway fetches it through the guard and converts it with the plugin converter. The admin picks the operations | The admin | API key or basic. OAuth only where a T1 client exists, and then only to that client's hosts |
| T3 | No OpenAPI document. The AI drafts operations from the API's docs page. The admin approves each one | The admin | As T2 |

Each T2 and T3 operation starts as class A for GET and class C for any other
method. A connection shows "connected" only after one test read succeeds. This
also closes bug 3766.

**A token of a Metorite client goes only to that client's hosts.** A T2 host
comes from an admin. So a token of the CRM's Zoho client never goes to a T2
host, even when the admin names a Zoho path. The gateway checks this at each
call.

### 2.2 The connection row

- `provider` is `api:<template id>`. `owning_app` is `integrations`.
  `scope_kind` is `org`. A member connection is a later slice.
- `provider_meta` holds only public facts: the region, the API host, the
  context values, the template version, the hash of the approved operations
  and the `grant`. For Zoho it also holds the accounts server, which the
  refresh and the revoke need. `connect.PUBLIC_META_KEYS` is the list (API-2).
- `credentials_encrypted` holds the token blob or the header map, as
  `connections/store.py` seals them today.
- A template change that adds or changes an operation asks the admin again.
  STU-B1b-3 does this for a changed MCP tool. This spec reuses that rule.

### 2.3 The gateway routes

Four routes, under the tenant session of the run's org:

- `GET /connections/api/{name}/ops?q=` finds operations by words.
- `GET /connections/api/{name}/ops/{op}` describes one operation.
- `POST /connections/api/{name}/read` takes `{op, args}`. It runs class A only.
- `POST /connections/api/{name}/write` takes `{op, args}`. It runs class B or
  C only. It refuses every call while D-API-4 is off for the org.

**The caller.** The chat tool sends the run's org and member, as the MCP
attach takes the run's org stamp. The gateway refuses the call when the member
is not of that org.

A call to `read` or `write` does these steps in order:

1. Check the grant `integrations:use:api-<name>` of the member. A refused name
   answers "not found".
2. Load the row and the template. Refuse an op that the admin did not approve.
3. Take the class of the op from the template now, not from the request.
   Refuse a class B or C op on `read`. Refuse a class A op on `write`.
4. Spend one call from the limiter and one from the daily budget. Over a
   limit, answer 429 with `Retry-After`.
5. Check each argument against the row (§2.1). Percent-encode each path value.
   Then set the context keys and the sign-in keys.
6. Get a fresh credential with `shared_token.fresh_credential` and the
   template's refresh.
7. Check that the host in `provider_meta` is a host of the template. Send the
   request through `outbound_guard.request_https`, with
   `Accept-Encoding: identity`.
8. Refuse an answer that the 64 KiB guard cap cut. Remove hidden characters
   and each field that the template marks secret. Cap the answer at the row's
   cap.
9. Remove each literal credential value from the answer and from any error
   text. This includes its URL-encoded form and a transport fault of `send`.
10. Write one log line, `api.call`: the connection id, the op, the status, the
    time and the size. Never an argument value, a header or a token.

An answer over its cap says so, and names the page argument. The chat then
asks for a smaller page or a narrower filter. A cut answer from the guard gets
the same reply, because a cut body cannot be parsed or stripped safely.

**Limits.** The limiter is the one of the Studio live read (§1, row 16): 10
calls for each member and 60 for each org, each minute. API-3 gives the API
calls their own counters in it. The daily budget of a connection is half of
the API's daily cap by default. So a model loop cannot stop the customer's
other tools on that API.

**The gateway total.** A template row can name a number field to **total**.
`api_read` with `total` set makes the gateway read each page of that list op,
up to 20 pages. It answers the sum, the row count, and whether it stopped at
the page limit. Each page spends one call from the limiter. The model never
adds numbers itself (`semantic_model.md`).

### 2.4 The chat side

**One shared set of tools for all connections.** The connection is an
argument. So the schema cost stays the same for one connection or for
twenty. Two `mcp:` and `api:` rows with one name cannot make two tools with
one name. (Review: metorite-7d and metorite-f8, 2026-11-18.)

| Tool | What it does | Outbound call | Gate |
|---|---|---|---|
| `api_find_operations(connection, words)` | Finds operations in the approved list | None. It reads our template | A plain native read. No MCP marker, no card, no mark |
| `api_describe_operation(connection, op)` | Gives the arguments of one operation | None | The same as find |
| `api_read(connection, op, args, total?)` | Runs one class A operation on the `read` route | Yes | The MCP marker and the read hint |
| `api_write(connection, op, args)` | Runs one class B or C operation on the `write` route | Yes | The MCP marker, the write hint and "always confirm". It attaches only where D-API-4 turned writes on |

**Why reads and writes are two tools.** `_McpPermissionGate` decides from the
hints of a tool, not of an argument. One call tool that holds reads and writes
must carry the write hint. Then a run with no member, WhatsApp and a closed
channel refuse even a read. Shaurrya's generic `call_operation` hit this fault
(`chat_connected_systems.md` §9). With two tools, the gate needs no logic for
each operation.

The gateway refuses a wrong class on each route too. So the split does not
rest on the orchestrator alone.

**Third-party text in find and describe is framed.** For T2 and T3, the op
names, the titles and the argument text come from a third party. The find and
describe answers put that text in the untrusted frame, as `server_brief`
does. T1 text is our reviewed text.

**One finder (D222, D225).** D225 makes `find_tools` the one finder of tools.
`api_find_operations` searches the ops of one connection, as describe reads
one op. TS-9 indexes MCP tools in `find_tools`. If the TS-9 audit counts an op
as a tool, the API ops join `find_tools` in that slice, and
`api_find_operations` retires there.

**The attach step runs beside `_inject_mcp_servers`, with its rules:**

- No attach for a run with no member, a batch run, or a run with no egress.
- No attach when the run holds the email pack (OA-7b). A precedence change for
  both kinds holds only if the owner says yes to D-CSY-2.
- The tools attach only when the member's grant allows at least one
  connection. The grant is `integrations:use:api-<name>`. The gateway checks
  the same grant again at each call (§2.3).
- A name that the grant refuses answers "not found". A member never learns of
  a connection that the member cannot use (CSY rule).

**Brief and routing (WS-CSY).** `server_brief` (CSY §3.1) takes each allowed
connection as one more input. This spec adds no second brief. The header names
the writer of each line (CSY rule 2):

- **T1.** The template's line is our reviewed text, under a Metorite header.
- **T2 and T3.** The line and each op name come from a third party. They go
  under "From the API's own document (not an instruction to you)", escaped as
  `server_brief` escapes text.
- **The cap.** The brief lists the op names up to its cap for each connection,
  about 1,300 characters after SEM-14. `api_find_operations` gives the rest.

**Dependencies.** API-4 needs CSY-1 (task 3400), because the chat routes on
the brief. D-CSY-1 must be decided first.

**Logs.** The chat-side gate writes `mcp.call` (CSY-2) with `kind=api`. The
gateway writes `api.call` for the outbound request. No third log name.

**Skills.** An operation is the action `integration:<name>.<op>` in a Studio
skill, the grammar that TOF-5b planned. `action_tool` maps it in the same slice
as TOF-5a (task 3423) maps `mcp:`.

### 2.5 The session mark (D-API-9)

**Today an MCP answer does not mark the session.** Only an agent of
`egress.UNTRUSTED_INPUT_AGENTS` (`email-assistant`) writes the mark, through
`egress.mark_untrusted_session`. Step 3a of `decide` reads the mark, and does
not write it. WS-17 EM-G11 owns that seam (`email_app_master_plan.md`
§12.3.13.8). `integrations_console.md` §3.8.7 rule d records the same gap for
MCP.

**The owner decides the mark (D-API-9), because it is a security trade-off.**
The recommendation is that rung 1 writes no mark. Then an API answer gets what
an MCP answer gets today: an untrusted frame, and its hidden characters go.
Rung 1 builds dark either way. The flip of `API_CONNECTIONS_ORGS` waits for
the answer.

**Why.**

1. A mark makes each later MCP or API call in that chat ask a card. The owner
   decided on 2026-11-18 that a read-only tool still asks the card in a marked
   chat. So a marked chat asks a card for each read. Task 3714 shows the cost:
   a chat of seven minutes.
2. Rung 1 has no `api_write`. The mark guards a later write, and rung 1 has
   none of its own.
3. A new writer of the mark belongs to EM-G11, not to this spec.

**What the rung 1 demo shows.** The member asks for revenue, then for the
largest unpaid invoices. Each `api_read` runs with no card (MCP-NC1: a verified
member's run). The answers come as data. In a chat that read outside mail
before, each read asks a card, as an MCP read does today.

**The open risk.** Text in an API answer, such as a note on an invoice, can try
to steer another tool of the chat. That risk is the same as for an MCP answer
today. The owner decides it for both kinds at once (D-API-9). The batch card of
WS-8c would lower the cost of a mark.

### 2.6 Writes

Read-only first. A template can hold class B and C operations, but
`api_write` attaches only after the owner turns writes on for an org (D-API-4).
The gateway's `write` route refuses every call until then, whoever calls it.
After the switch, each write asks a card, also in a member's own run ("always
confirm"). A write is refused in WhatsApp, in a run with no member, and on a
closed channel, as an MCP write is today.

### 2.7 Secrets, egress and data

- The chat never sees a key, a token, a host or a header.
- A call reaches only a template host. Each call goes through
  `outbound_guard.request_https`: https only, on port 443 only. The guard pins
  the DNS answer, refuses a redirect and caps the answer. So a key never goes
  over plain http.
- A T2 host comes from the admin. It passes the rules of
  `outbound_guard.check_mcp_url` when the admin saves it and at each call. So
  it is https on 443, and never a private or host-local address.
- A token of a Metorite OAuth client goes only to that client's hosts (§2.1).
- The gateway removes each literal credential value from an answer and from
  error text, before the chat sees it (§2.3, step 9). Many APIs echo a key or
  a `?api_key=` address in an error body.
- An answer is third-party text. It is framed as untrusted (§2.5).
- A model loop cannot use up the API's daily cap. The limiter and the daily
  budget stop it first (§2.3).
- The data of an API goes to the model vendor of the chat, as MCP data does
  today (D-API-7).

### 2.8 IN-3 and the credential rule

**What IN-3 protects.** IN-3 (task 3176) builds the end of process-global
credential injection (`integrations_console.md` §3.3, D58.2, H-43). Today
`acb_skills.integrations.credential()` reads the process environment first.
So a key in the environment reaches the run of every org on that process. It
also wins over a key of one org (IN-D7).

The done-when of IN-3 is in its task text:

```text
"A run in org A gets org A's key and a run in org B gets org B's, on one process at one time."
```

**Why this path keeps that rule without IN-3.**

1. **No credential enters the orchestrator.** The chat tool sends a connection
   name, an op and arguments to the gateway. It sends no key and gets no key.
2. **The org comes from the run, not from the model.** The tool sends the
   run's org and member, from the run context. The gateway refuses a member
   who is not of that org. No argument of the model can name another org.
3. **One reader of the sealed column.** The gateway reads the row in
   `tenant_session(org)` under FORCE RLS, through `connections/store.py`. It
   passes the credential to `outbound_guard.request_https` as an argument. It
   never writes `os.environ`. This is rule 2 of §3.3: a credential reaches a
   call as an argument.
4. **The defect of IN-3 is not on the path.** No API module calls
   `acb_skills.integrations` or reads `os.environ`. No operator key in the
   environment is a fallback for an API connection, at any rung. Otherwise
   IN-D7 comes back by a new path.
5. **The model chooses within limits.** It picks a connection that its org
   holds and the member's grant allows. It picks an op from the approved list.
   It sends only the keys that the row names, in their shapes. It cannot send
   a context key, such as `organization_id`.

**The precedent is model-driven too.** `_inject_mcp_servers` reads the sealed
headers of each org server with `servers_for_org(org)`
(`connections/mcp.py:824`). It passes them to the MCP client as an argument
for the model's calls. IN-4 shipped that with no IN-3. This path is stricter,
because the credential stays in the gateway. GitHub's `token_for_org` and the
calendar sync read in the same way, but no model drives them.

**Two plans put this work after IN-3. The owner decides both reversals.**

- **TOF-5c (task 3425)** is blocked by task 3176 in `agent_sandbox.md` §0b. It
  planned a gateway-side request with the org's own credential. D-API-2 asks
  the owner to replace it with this spec, with no wait for IN-3.
- **`semantic_model.md` rung 4 and D-SEM-8** say "Zoho Books after IN-3".
  D-API-5 asks the owner to drop that wait for the gateway path. This pull
  request adds a note at both places in `semantic_model.md`.

**What still needs IN-3.** The resolvers of `acb_skills.integrations` for
scripts, and TOF-5b's integration actions that read the environment. A T1
template for one of those APIs uses the gateway path and no resolver.

**The fences.** An AST fence: no API module imports `acb_skills.integrations`
or reads `os.environ`. An R8 case: a run of an org A member names a connection
of org B and gets "not found". A run of org A and a run of org B, on one
process at one time, each reach only their own provider account. A sentinel
case: no answer, no error and no log line holds the credential. Review round
2 checked this section.

### 2.9 What happens to "Add API", plugins and the key connectors

- **"Add API".** It becomes "Connect an API": the T1 list first, then T2 by an
  OpenAPI address. The discovery call to the model goes (it is not metered,
  IN-D15). An old custom row reads "the chat cannot use this yet" (task 3766),
  with a link to the template that replaces it.
- **Plugins.** T2 takes the OpenAPI converter. The `plugins` table retires
  (D-API-6).
- **The seven key connectors.** Each becomes a T1 template with the org's own
  API key. An operator key in the environment is never a fallback (§2.8, point
  4). SMTP is not an HTTP API, so it stays a gap with that reason.

### 2.10 Zoho Books, the first template

Task 3749 owns the books connectors (D-SEM-8). This section only names what
the mechanism must carry for Zoho Books.

- **Sign-in.** The CRM's Zoho client, with the Books read scopes. One click,
  and every Zoho data centre. The client can ask for the Books scopes: each
  consent request names its scopes, and the console holds none
  (`docs/ZOHO_OAUTH_SETUP.md`, line 31). So the client needs no new
  registration (API-2, §2.11).
- **Hosts.** `ZOHO_DATA_CENTRES` gives them. The template lists no Zoho host
  of its own.
- **Context.** The connect step reads `GET /organizations` and keeps the
  `organization_id`. The admin picks one when the account has more than one.
  The gateway sets `organization_id` on each call. An argument that names it
  is refused.
- **Who may read (D-API-3).** The token carries the Zoho rights of the admin
  who connected it. So each member that the grant allows reads what that admin
  can see in Zoho, such as salaries and bank data. The Zoho CRM is
  member-scoped for this reason (`integrations_console.md` §3.4). The default
  grant of a books connection is the admin role only. An admin can grant a
  group later.
- **Header and limits.** The scheme is `Zoho-oauthtoken`. Third-party guides
  give 100 calls a minute for each organization, and a daily cap by plan.
  Check both against Zoho's own page before API-3. The daily budget (§2.3)
  takes half of the plan's cap.
- **Totals.** The adder is the gateway total (§2.3). The template marks the
  amount field of the invoice list op for it. API-1 also checks whether Zoho
  Books has a report operation, such as profit and loss. A report op is one
  more class A read, and its answer already holds the totals.

### 2.11 The connect flow (API-2)

An admin connects Zoho Books in four steps. They are the start, the consent
at Zoho, the callback, and a pick when Zoho names several organizations.
API-2 builds the gateway half, dark behind `API_CONNECTIONS_ORGS`. API-5 builds
the console rows and the pick sheet.

**The routes.** `routes/integrations_api_connect.py` holds four routes under
`/integrations`. The pure helpers are in `connections/api/connect.py`.

| Route | What it does |
|---|---|
| `POST /integrations/api/zoho_books/start` | Gives the consent URL, with the Books scopes and a marked, signed state |
| `GET /integrations/api/zoho/callback` | Finishes the consent. The CRM BFF callback sends it each marked state |
| `POST /integrations/api/zoho_books/pick` | Finishes a consent that found several organizations, with one of them |
| `GET /integrations/api/zoho_books` | Gives the status, and the choices of the admin's own pending pick. It gives no token |

Each route needs `integrations:manage`. For an org that the flag does not
hold, each route answers 404 before the feature gate and the permission.

**The order of the callback.** It follows `routes/crm/connection.py::zoho_callback`.

1. Check the state: the MAC of `api.zoho_books.connect`, then the org and the
   member of the session. Then take the one-use mark of the state from Redis
   with GETDEL. Each refusal is the same 400, and Zoho gets no call.
2. A provider `error`, or no code, ends in a redirect with that code.
3. Check the accounts server and the location against `ZOHO_DATA_CENTRES`. A
   bad value is 400, and Zoho gets no call.
4. Exchange the code for tokens with the CRM's Zoho client. An API domain that
   is not a host of the template is 400, and the gateway revokes the new token.
5. Read `GET /books/v3/organizations` through `outbound_guard.request_https`.
   With no organization, revoke and fail. With one, go on. With several, seal
   the credential with `store.seal_value`. Keep it in `TenantRedis` for 600
   seconds under the state nonce, and send the admin to the pick.
6. Run the test read: `list_invoices` with `per_page=1` and the picked
   `organization_id`. A failure writes no row, keeps an older row as it is,
   and revokes the new token. `store.save_credential` always writes
   `connected`, so the test read runs before the save.
7. Save under the advisory lock `api.connect:<org>:api:zoho_books`. Revoke the
   old refresh token after the commit.

**Three choices, made at API-2.** The owner may change each one.

1. **One redirect URI (option A).** Zoho holds one redirect URI for the
   client, the CRM's `/api/crm/oauth/zoho/callback`. A Books state starts with
   the public mark `zb.`. The CRM BFF callback sends a marked state to the
   Books route, and every other state to the CRM. The mark proves nothing. The
   purpose is only in the MAC, so a forged mark changes only where the state
   goes. Option B is a second URI in the Zoho console. That is an owner act
   (OWNER-GATE), and nothing here depends on it.
2. **The admin-only grant.** The admin, manager and member roles each hold
   `integrations:use:*` (§1, row 15). So the grant `integrations:use:api-zoho_books`
   alone keeps no member out. The connect writes `grant = admin` in
   `provider_meta`. While the grant is `admin`, API-3 and API-4 ask for
   `integrations:use:api-zoho_books` AND `integrations:manage`. Only the admin
   role holds `integrations:manage`, and the owner holds `*`. API-2 writes no
   role permission and no member override. Those writes are owner acts.
3. **The scopes.** `connect.ZOHO_BOOKS_SCOPES` holds `ZohoBooks.settings.READ`
   for the organizations and `ZohoBooks.invoices.READ` for the invoices. Zoho
   refuses a whole consent for one unknown scope. The first real consent
   confirms the two names, and that consent is an owner act.

**The console disconnect.** The row id of an API connection is
`api:zoho_books:<uuid>`. The console splits an `api:` id at its last colon
(`routes/integrations_connections.py::_split_id`). The disconnect revokes the
refresh token at Zoho, then drops the blob (`disconnect_books`).

**Done when.** Each line names its kind and its fence.

| # | Done when | Kind | Fence |
|---|---|---|---|
| AC1 | With the flag off for the org, each route answers 404, before the feature gate and the permission | AGENT-SAFE | `test_api_connect_zoho_books.py::test_ac1_flag_off_every_route_is_404_before_the_permission` |
| AC2 | A member with no `integrations:manage` gets 403 on each route | AGENT-SAFE | `test_ac2_no_manage_permission_is_403` |
| AC3 | A forged, expired, unmarked or other-purpose state, a state of another org or member, or a used state gets 400. Zoho gets no call | AGENT-SAFE | The `test_ac3_*` cases |
| AC4 | An accounts server, a location or an API domain off the list gets 400. An API domain off the template also revokes the new token | AGENT-SAFE | The `test_ac4_*` cases |
| AC5 | One organization gives one `connected` row: provider `api:zoho_books`, owning app `integrations`, scope `org`, public facts only, `grant = admin`. No code, token or secret reaches a response, a redirect, `provider_meta` or a log line | AGENT-SAFE | `test_ac5_one_organization_saves_a_connected_admin_row`, the sentinel case |
| AC6 | Several organizations write no row and one pending entry. A pick of an id outside the list gets 400. The state and the pick each work once | AGENT-SAFE | The `test_ac6_*` cases |
| AC7 | A failed test read writes no row, keeps an older row as it is, and revokes the new token | AGENT-SAFE | The `test_ac7_*` cases, `test_api_connect_r8.py` |
| AC8 | On a real database under FORCE RLS, org B cannot see or pick org A's row or pending entry. A reconnect updates the one row | AGENT-SAFE (R8) | `test_api_connect_r8.py` |

**The owner acts.** These are not part of the done-when list. The first real
consent against Zoho confirms the scopes and the paths. A value in
`API_CONNECTIONS_ORGS` on a box is an `enforcement-flip`. Option B is a change
in the Zoho console.

**Verification.**

```text
bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
uv run pytest -p no:xdist -rs tests/unit/test_api_connect_zoho_books.py tests/unit/test_api_connect_r8.py tests/unit/test_api_no_env_credentials.py tests/unit/test_api_templates.py
cd workbench/control_plane && npx vitest run src/app/api/crm/oauth
```

The R8 file must show no skip.

**Known gaps, found by the review of API-2 (2026-11-19). API-5 owns them.**

- **A stranded pending token.** When the account has several Books
  organizations, the sealed credential waits in Redis for 600 seconds. If the
  admin leaves, Redis drops it, and nothing revokes the refresh token at Zoho.
  Zoho keeps 20 tokens for each user and client, so a stranded token can push
  out the CRM mirror's token. No browser route reaches `start` or `pick` before
  API-5, so the case is latent. API-5 adds a "start again" path that revokes
  the old pending entry, or a revoke when the entry expires.
- **A dark org and `/pick`.** A malformed JSON body to `/pick` answers 422, not
  404, because FastAPI reads the body before the dependencies. The route shows
  that it exists. This is a low leak.
- **`pick` takes the nonce first.** `pick_zoho_books` reads and deletes the
  pending entry before it checks the member. Another admin of the same org who
  holds the nonce can use it up. This is a denial only.
- **The Studio fence.** `test_studio_connect_in_place.py` has an exemption,
  `_NOT_LISTED_YET`, for `api:zoho_books`. API-5 lists the connector and
  removes the exemption.
- **A large `/organizations` answer.** An account with about 40 or more Books
  organizations can pass the 64 KiB guard cap. The flow then fails closed with
  `context_unreadable`.

## 3. Options not taken

| Option | Why not |
|---|---|
| The gateway serves each connection as an MCP endpoint, and the run attaches it | The MCP client refuses a host-local address (§1, row 6). An exception widens the SSRF surface. The native tools keep the MCP behaviour without the hop |
| Three tools for each connection, with the name as a prefix | The schema grows with each connection, and the chat's tool budget is tight (task 3725). A `mcp:` row and an `api:` row with one name give two tools with one name. One shared set replaces it (§2.4) |
| One call tool for reads and writes | The gate must treat it as a write, so WhatsApp and a run with no member refuse even a read (§2.4) |
| One call route for reads and writes | The class check would rest on the orchestrator alone. A caller with the internal token could run a write with no card (§2.3) |
| A tool pack for each API | A pack is static code. It cannot hold tools for one org (§1, row 13) |
| An API as an app of the door (`App`) | An app needs a router, a pane and a feature. The manifests are static. This spec shares the door's row builder only (§2.1) |
| A free path on the provider host (TOF-5c as drafted) | The chat could then reach any endpoint, and the read or write class would mean nothing. An admin adds an operation instead (D-API-2) |
| The model adds the numbers of a list read | A model makes arithmetic errors, and `semantic_model.md` forbids it. The gateway total adds them (§2.3) |

## 4. The ladder

Each rung ends in a demo on production (D211).

| Rung | The demo | Slices |
|---|---|---|
| 0 | The chat reads Zoho Books through Zoho's own MCP server, with no code | Task 3767 |
| 1 | A Shaurrya admin asks for last month's revenue and the largest unpaid invoices. The chat answers from Zoho Books with the gateway total, and names the source | API-1 to API-6 |
| 2 | A member reads a Google Sheet in the chat | API-7: T1 templates for the key connectors, Google Sheets first, with the org's own keys only |
| 3 | An admin connects an API by its OpenAPI address and picks operations | API-8: T2, and the end of the plugin store and the discovery call. The 64 KiB guard cap stops most OpenAPI documents, so API-8 gives that one fetch a larger cap, with its own fence |
| 4 | The chat writes through an API, on a card | API-9: writes, after D-API-4 |
| 5 | A saved skill runs an API operation | With TOF-5a and TOF-5b |

**Rung 1 slices.** Each ships dark behind `API_CONNECTIONS_ORGS`. No slice builds
until the owner answers D-API-2, D-API-4 and D-API-9.

| Id | What | Tier |
|---|---|---|
| API-1 (task 3787) | The template grammar and its validator. The split of `build_call` into a builder over one `Route`, with query shapes and encoded path values. The Zoho Books template with read operations and the total mark | STANDARD |
| API-2 (task 3788) | The connect flow: the Zoho client with Books scopes, the context step, the test read, the status, the admin-only default grant. Built (§2.11) | HIGH (identity, a credential) |
| API-3 (task 3789) | The four gateway routes: the org check, the grant, the class at call time, the write switch, the limits, the refresh, the guard, the total, the strips and the log line | HIGH (egress, a credential) |
| API-4 (task 3790) | The shared tools, the attach step, the gate facts and the brief inputs. Needs CSY-1 (task 3400) and D-CSY-1 | HIGH (permission) |
| API-5 (task 3791) | The console: the connector list rows, the result card (IN-9) and the "used by" sheet (IN-10) | STANDARD |
| API-6 (task 3792) | Bug 3766: a custom row that the chat cannot use says so | LOW |

## 5. Fences (R7)

| Rule | Fence |
|---|---|
| Each template is valid: https hosts, a class on each op, no free path, a shape on each query key. A path shape refuses `/ ? # %` and `..` | `tests/unit/test_api_templates.py` (new) |
| An argument outside the row is refused. An argument that names a context or sign-in key is refused. The gateway sets those keys after the arguments | `tests/unit/test_api_call_egress.py` (new) |
| A call never reaches a host outside the template. Each call is https on 443. A Metorite client's token goes only to that client's hosts | `tests/unit/test_api_call_egress.py` |
| The `read` route refuses class B and C. The `write` route refuses every call while D-API-4 is off. The gateway checks the grant and takes the class from the template | `tests/unit/test_api_connections_r8.py` (new, R8) |
| A run in org A cannot call a connection of org B. A member of another org is refused. A name that the grant refuses answers "not found" | The R8 suite |
| No answer, no error text and no log line holds a credential, also when the API echoes it or the transport fails | Sentinel cases in the R8 suite |
| No API module imports `acb_skills.integrations` or reads `os.environ` | `tests/unit/test_api_no_env_credentials.py` (new, AST) |
| The connect flow: the flag, the permission, the state, the hosts, the test read before the save, the pick from the server list (§2.11, AC1 to AC7) | `tests/unit/test_api_connect_zoho_books.py` |
| Org B cannot see or pick the connection of org A. A reconnect updates the one row. A failed test read keeps the older row (AC8) | `tests/unit/test_api_connect_r8.py` (R8) |
| The BFF callback sends a marked state to the Books route, and passes only its three Location shapes | `workbench/control_plane/src/app/api/crm/oauth/zoho/callback/route.test.ts` |
| The limiter and the daily budget stop a loop. A cut answer is refused | `tests/unit/test_api_call_limits.py` (new) |
| The gate refuses a write op with no member and in WhatsApp | `tests/unit/test_maf_mcp_confirm.py` (extended) |
| Find and describe carry no MCP marker and ask no card. Their T2 and T3 text is framed | `tests/unit/test_api_chat_tools.py` (new) |
| Each `gap` row of G6 becomes a path or a written reason | `tests/unit/test_connector_chat_reach.py`, `test_integration_kinds_chat_reach.py` |

## 6. Seams that other specs own

| Topic | Owner | What this spec takes |
|---|---|---|
| The registry and the console | `integrations_console.md` | IN-6 and Q2 resolve here: the custom APIs get a reader, or retire |
| The brief, the mail precedence, the `mcp.call` line | `chat_connected_systems.md` (WS-CSY) | The brief inputs. The ten CSY rules hold |
| The profile of a connection | `semantic_model.md` (WS-SEM) | Shape reads, marked in the template. D-API-5 amends rung 4 and D-SEM-8 |
| Skills over connected systems | `agent_sandbox.md` §0b (TOF-5a, TOF-5b, TOF-5c) | TOF-5c becomes D-API-2 here |
| The one tool finder | D222, D225 (`find_tools`) | The ops join it if the TS-9 audit says so (§2.4) |
| The reach matrix and its fence | The reach matrix spec (task 3440, PR 2590, not merged yet) | Gap G6 |
| The writer of the session mark | WS-17 EM-G11 (`email_app_master_plan.md` §12.3.13.8) | Nothing in rung 1. D-API-9 decides |
| The books connectors | Task 3749 (D-SEM-8) | The Zoho Books template |
| The Zoho hosts and client | `crm_sources/zoho/auth.py` (`ZOHO_DATA_CENTRES`) | The one list of Zoho hosts |
| The call limiter | `connections/mcp_tools.py` `_spend` (Studio live read) | The limits, with counters of its own |

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

**Decided 2026-11-19, owner in chat (D228).** The owner accepted all nine
recommendations. The table keeps each question and its recommendation. The
list after it holds the answers.

| Id | Question | Recommendation |
|---|---|---|
| **D-API-1** | Which APIs: first-party templates only, or also an API by its OpenAPI address (T2), or also an API with no OpenAPI (T3)? | T1 and T2. T3 later, with admin approval of each operation |
| **D-API-2** | A closed operation list, or any path on the provider host (TOF-5c)? TOF-5c (task 3425) waits for IN-3 today. This spec replaces it, with no wait for IN-3 (§2.8) | Closed. An admin adds an operation. Replace TOF-5c with this spec, and do not wait for IN-3 |
| **D-API-3** | Who may use a connection? The token carries the Zoho rights of the admin who connected it, so a granted member reads what that admin can see | As an MCP server: the admin role, and each group the admin grants. For books, the default is the admin role only |
| **D-API-4** | Writes | Read-only first. Then each write asks a card, also in a member's own run. The gateway refuses writes until then |
| **D-API-5** | Zoho Books first, and with no wait for IN-3? D122.1 decided Zoho Books, then Tally, then QuickBooks. D-SEM-8 (open) puts Tally first and Zoho Books after IN-3 | Yes, as D122.1. A customer asked, the Zoho client exists, and the gateway path does not need IN-3 (§2.8). Drop "after IN-3" from `semantic_model.md` rung 4 and D-SEM-8 |
| **D-API-6** | Retire the discovery call of "Add API" and the plugin store (IN-6, Q2)? | Yes, when T2 lands. Until then the tile says that the chat cannot use it |
| **D-API-7** | The data notice: an API's data reaches the chat's model vendor | The same notice as MCP and LNK-Q2 |
| **D-API-8** | The flag | `API_CONNECTIONS_ORGS`, to all orgs after the rung 1 demo |
| **D-API-9** | Does an API answer mark the chat as untrusted? A security trade-off (§2.5) | No, the same as an MCP answer today. Decide both kinds together. **Cost of a mark:** each later MCP or API read in that chat asks a card (task 3714). **Open risk with no mark:** text in an answer, such as an invoice note, can try to steer another tool of the chat. Rung 1 builds dark either way, and the flag flip waits for this answer |

**The answers (2026-11-19, owner in chat, task 3562, answer `97b5d6ce`).**

1. **D-API-1.** Accepted. T1 and T2 first. T3 later, with admin approval of each operation.
2. **D-API-2.** Accepted. The operation list is closed. This spec replaces TOF-5c and does not wait for IN-3.
3. **D-API-3.** Accepted. The admin role and each group that the admin grants. For books, the admin role only.
4. **D-API-4.** Accepted. Read-only first. Then each write asks a card, also in a member's own run. The gateway refuses writes until then.
5. **D-API-5.** Accepted. Zoho Books is first, with no wait for IN-3. It is an owner priority.
6. **D-API-6.** Accepted. The discovery call of "Add API" and the plugin store retire when T2 lands.
7. **D-API-7.** Accepted. The data notice is the same as for MCP and LNK-Q2.
8. **D-API-8.** Accepted. The flag is `API_CONNECTIONS_ORGS`.
9. **D-API-9.** Accepted. An API answer does not mark the chat as untrusted, the same as an MCP answer today. The open risk of §2.5 stays: text in an answer can try to steer another tool of the chat.
