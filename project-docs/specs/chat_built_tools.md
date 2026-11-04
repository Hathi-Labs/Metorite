# Chat-built tools — code on the fly over outside data, kept as personal and organization tools (WS-CBT)

<!-- ste-tier: strict -->

- **Status:** 📋 PROPOSED, 2026-11-04. Planning only. Nothing built. The
  owner questions of §15 are open. Each one has a default, so no slice waits
  on the owner to start.
- **Verified against code on 2026-11-04** at `main` `a221ab972`. Re-verify
  every anchor at dispatch. The code is the fact.
- **Asked by:** the owner, in chat, 2026-11-04 (UTC). Metorite task 1655 holds
  the brief verbatim.
- **Board row:** **WS-CBT** in `work_plan.md` §2.1. Metorite epic **1656**
  in the project "AI Studio", under #491. §14 gives one task to each slice.
- **Audit:** spec-auditor, 2026-11-04. Round 1 was NO-GO with 15 findings.
  Round 2 was GO-NARROWED with 8 findings. This version fixes each one
  (§19). CBT-1 is dispatchable now.
- **Reads:** D12, D15, D19.3, D83, D85, D97, D98, D99, D124, D127, D129,
  D130, D131, D139, D141, D146, D147, R5 to R9.
- **Owns (only the topics that no spec owned before):**
  1. The ladder: when the chat uses a tool, a query, scratch code, a stored
     tool or AI Studio (§3).
  2. The scratch code tool `run_code` (§4).
  3. The source bridge: how sandbox code reaches an outside MCP server or an
     API connection (§5).
  4. Data handles: how data goes into and out of a built tool (§6).
  5. Reuse rules on top of the registry: save a scratch run as a tool, merge
     into a version, stale tools (§7).
  6. The tool library: the list of past tools (§8).
  7. Stewardship: organization tools, promotion, admin versions, forks and
     the departed owner (§9).
  8. The chat parity rows, the failure copy and the evals of this feature
     (§12, §13).
- **Does not own (link only, one owner per topic, `work_plan.md` §4):**

| Topic | Owner |
|---|---|
| The tool registry tables, `find_tool`, `build_tool`, `run_tool`, the smoke test, persist on success, versions, pins, disable, revoke, the run capability, the sandbox door, egress profiles, sandbox metering, the base `error_class` list | `agent_sandbox.md` (WS-TO, D130) |
| The sandbox broker, the image, the egress proxy, the data engine and its toolkit (§7.10) | `maf_coding_engine.md` (WS-43) |
| The one coding harness and its `tool_builder` profile | `maf_coding_engine.md` §18, CA-14 (task 1042) |
| The action catalog, the invoke door, MCP tools as catalog source 3 (B1b), logic modules (B4, B5), the Studio library pane, OpenAPI connections (O12), workflow steps (E5) | `ai_studio.md` (WS-STU, epic 491) |
| Install, update and roll-out of a package | `package_host.md` (WS-PKG, task 1234) |
| Tool packs and the large-result spill (CTX-1) | `one_assistant_context.md` (WS-67) |
| Org MCP connections, their grants and their identity | `connections/mcp.py`, `_maf_mcp.py`, D98 |
| Metorite as an MCP server for outside agents | `external_agent_access.md` (WS-55) |
| The artifact registry | `artifact_management.md` (WS-60) |
| The confirmation card | `projects_ai_chat.md` §5 |
| The chat parity fence | `chat_parity.md` |
| How the assistant writes | `agent_writing_voice.md` (WS-52) |

**Edits that this spec makes in other specs.** Each one is a pointer or a
narrow amendment, named here so the owner of that spec sees it:

| Spec | Edit | Slice |
|---|---|---|
| `agent_sandbox.md` header | A link to this spec in "Does not own" | This PR |
| `agent_sandbox.md` §7 | The columns of §9.8, when SBX-1 is not merged yet | CBT-6a |
| `agent_sandbox.md` §8.2 | Who runs which version of an organization tool (§9.4) | This PR (a pointer), CBT-8a |
| `ai_studio.md` B2 | The record of the package-free executor that the bridge uses (§5.5) | CBT-2 step 0 |
| `ai_studio.md` B5 | A pointer to §9.6, after CBT-Q5 | CBT-9 |
| `ai_studio.md` §13 | Two jobs in Studio's shell manifest (§8.5) | CBT-7 |

**Ids in this spec.** Every id carries the `CBT-` prefix (R2). The slices are
`CBT-0` to `CBT-13`, the fences `CBT-F1` and up, the evals `CBT-E1` and up,
the owner gates `CBT-G1` and up, and the owner questions `CBT-Q1` and up.

---

## 0. The answer

**Most of the brief is already planned.** `agent_sandbox.md` (D130) builds a
tool that does not exist, stores it after a good run, reuses it, and shares it
with the organization after an approval. AI Studio (D146) builds skills,
workflows and apps, and its action catalog lists the tools of the org's MCP
servers. This spec adds the six parts that are missing. It adds no second
sandbox, registry, door, catalog, card or harness.

1. **A ladder.** The chat first uses a tool that exists. Then it queries the
   data with SQL. Then it writes scratch code. Then it saves the code as a
   tool. AI Studio takes over when the need has records, a screen, a schedule
   or many people (§3).
2. **Scratch code in chat.** `run_code` runs one Python snippet once, in the
   `tools` target of the broker, as the member. Nothing is kept as a tool
   unless the member saves it (§4).
3. **A bridge to outside sources.** Sandbox code calls an MCP tool or an API
   action through one door route. The gateway holds the credential. The code
   never sees it (§5).
4. **Data handles.** Data moves by handle, not through the model. A large MCP
   answer goes into the sandbox as a file. The result comes back as an
   artifact and a short summary (§6).
5. **A library.** AI Studio's library gets a fourth kind, **Tools**. It lists
   every tool that the member can see, with its versions, runs and sources
   (§8).
6. **Stewardship.** A personal tool belongs to its builder. An organization
   tool belongs to the organization and has a steward. An admin can propose a
   new version, a second person approves it, and a rollback is one step (§9).

### 0.1 Non-goals

- **No `pip install` in a scratch run.** A new package means a saved tool
  with a lock (§4.2 step 4).
- **No `web:` host in a step that reads Metorite data** (SBX-Q2 stands).
- **No tool that calls a tool.** Sandbox code cannot call an organization
  tool through the bridge (§5.2).
- **No group scope** (`group:<slug>`) in this spec.
- **No tool across organizations.** A tool never leaves its organization.
- **No code editor in the UI** (`agent_sandbox.md` §3.2).
- **No change to `run_script`** on the host. D129 decides its future, not
  this spec.
- **No API key in chat or in code.** A key goes into Integrations only.
- **No run with no person** except a workflow read of an organization tool
  (SBX-Q5, §9.6).

---

## 1. The brief, mapped

| What the brief asks | Where this spec answers it | Already owned elsewhere |
|---|---|---|
| Write code on the fly for an outside MCP or API | §3, §4, §5 | Build and run of a stored tool: `agent_sandbox.md` §6 |
| Process data that the chat cannot process | §3.1 rungs 2 and 3, §6 | The SQL data engine: `maf_coding_engine.md` §7.10 |
| Set up workflows, tools and code | §3.2 | Workflows, skills and apps: `ai_studio.md` |
| Use AI Studio, or code in chat for a niche case | §3 | — |
| Code in chat is a tool too | §4 | — |
| The sandbox reaches the MCPs and the APIs | §5 | Door, capability and egress: `agent_sandbox.md` §5 |
| Pass data to and from a built tool | §6 | Input and output files: `agent_sandbox.md` §6.3, §6.4 |
| Reuse tools, find the tool before a new one | §7 | `find_tool` and the registry: `agent_sandbox.md` §6.1, §7 |
| The list of past tools | §8 | — |
| Share tools across the organization | §9 | Share and approve: `agent_sandbox.md` §8 |
| Personal tools against organization tools | §9 | — |
| An admin updates an organization tool | §9.4 | Pins: `agent_sandbox.md` §8.2 |
| Permissions and tenancy | §10 | — |
| Credits and cost | §11.1 | `agent_sandbox.md` §10 |
| Audit | §11.2 | `agent_sandbox.md` §8.4 |
| Safety | §11.3, §11.4 | `agent_sandbox.md` §5.7 |
| Chat parity | §12.1 | The fence: `chat_parity.md` |
| Failure copy | §12.2 | — |
| Evals | §13 | — |

---

## 2. What exists today (measured 2026-11-04)

Read at `main` `a221ab972`. Each row is a seam that this spec reuses.

| Piece | Where | State |
|---|---|---|
| The tool registry (`user_tools`, `user_tool_versions`, `user_tool_runs`, `tool_gaps`), `SANDBOX_ROUTES`, the `tools` target | `agent_sandbox.md` §7. No code or migration names them | **Spec only.** SBX-1 is the first slice |
| The sandbox broker | `apps/services/orchestrator/orchestrator/sandbox_broker.py` | Built, dark (WS-43c). Production cannot run it until BH-3 |
| The SQL data engine | `apps/services/orchestrator/sandbox/data_engine.py`. Verbs `load`, `query`, `preview`, `profile`, `export`. Only this file imports `duckdb` (WS43-F26) | The engine is built. The toolkit that gives the chat a tool for it (WS-43y1 to WS-43y4) is spec only |
| The large-result spill (CTX-1) | `packages/acb_skills/acb_skills/tool_result_spill.py`. An in-process LRU with a TTL. A handle `tr_<8 hex>`. The key is the org, member and thread of the run binding | Built, dark behind `TOOL_RESULT_SPILL` |
| The untrusted frame | `packages/acb_skills/acb_skills/untrusted.py` | Live |
| The marked session | `packages/acb_skills/acb_skills/egress.py:360`, `mark_untrusted_session`, and `:446`, `session_is_marked` | Live |
| Org MCP servers | `apps/services/gateway/gateway/connections/mcp.py`, with OAuth refresh. D98: an OAuth connection acts as the admin who connected it | Live |
| Which MCP servers a run holds | `MAF_MCP_ORGS`, the grant `integrations:use:mcp-<name>`, `no_egress`, the server caps. Fence `tests/unit/test_maf_mcp_injection.py` | Live |
| The per-tool MCP decision | `apps/services/orchestrator/orchestrator/_maf_mcp.py`, `McpPermissionGate` and its `decide()`, with `member_present`. Fences `tests/unit/test_maf_mcp_confirm.py` and `test_mcp_always_confirm_r8.py` | Live |
| The confirmation card | `packages/acb_skills/acb_skills/ask_tools.py`, `request_confirmation` | Live |
| Custom API definitions | `infra/postgres/12_custom_api_definitions.sql`, `routes/integrations.py` | Live. O12 grows them into connections with actions |
| The Studio action catalog | `apps/services/gateway/gateway/routes/studio/actions.py:216`, `GET /studio/actions`. Sources 1, 2, 4 and 5 | Built, dark. **Source 3 (MCP tools, B1b) is not built** |
| The Studio invoke door | `routes/studio/invoke.py:706`, `POST /studio/apps/{app_id}/actions/{action}` | Built, dark (B2a). Package actions for the package owner only. B2b-2 and B2b-3 are not built |
| The Studio library | `workbench/control_plane/src/app/studio/page.tsx` (`readKind`, `?kind=apps\|workflows\|skills`), `components/studio/StudioPage.tsx` | Built, preview pane (A1) |
| The artifact registry and writer | `infra/postgres/268_artifacts.sql`, `packages/acb_skills/acb_skills/write_artifact.py` | Built, dark (AM-1) |
| Credential shapes | `packages/acb_common/acb_common/secret_shapes.py` | Live |
| The chat parity table | `packages/acb_skills/acb_skills/chat_parity.py` (`COVERED`, `EXEMPT`, `DEBT`, `APP_OF`, `TOOL_SOURCES`, `DEBT_CEILING`), fence `tests/unit/test_chat_parity.py` | Live |
| Member offboarding | `apps/services/gateway/gateway/routes/admin/members.py`, fence `tests/unit/test_admin_member_offboarding.py` | Live |
| A sweep job to copy | `apps/services/gateway/gateway/agent_run_sweep.py` | Live |
| `run_script` on the host | `packages/acb_skills/acb_skills/code_tools.py:206` | Live. D129 withdraws it from personal agents, and the code still keeps it (`test_shared_agent_shell_tools.py`). Pending D129. Not reused |
| Evals | `evals/<name>/run.py` with `--scripted` and `--live`, a row in `evals/README.md`, a test `tests/unit/test_<name>_eval.py`, and the scripted run in `.github/workflows/skill-eval.yml`. Example: `evals/routing/` | Live |

**What the table shows.** Every runtime piece is built and dark, or
specified. The new work is policy, one door route, one handle grammar, one
library kind and a few columns. Production waits on the same three blocks as
`agent_sandbox.md` §2: BH-3, WS-43g and the image on the box.

---

## 3. The ladder — when the chat writes code, and when it uses AI Studio

### 3.1 The five rungs

The assistant takes the lowest rung that does the job. Each rung costs more
and carries more risk than the one before it.

| Rung | What the assistant does | When | Kept after the chat? |
|---|---|---|---|
| **1 · A tool that exists** | Calls a platform tool, a pack tool, an org MCP tool or a stored tool | `find_tool` answers `existing` or `reuse` | — |
| **2 · A query** | Loads the data into the data engine and runs one read-only SQL query (`maf_coding_engine.md` §7.10) | The need is a filter, a join, a sum, a group or a sort over table data | No |
| **3 · Scratch code** | `run_code`: one Python snippet, run once (§4) | Rungs 1 and 2 cannot do it. Examples: parse an odd format, reshape nested JSON, compute a forecast, call two sources and merge them | No, unless the member saves it |
| **4 · A stored tool** | `build_tool` and `run_tool` (`agent_sandbox.md` §6) | The member asks to keep it, or §7.2 offers it and the member accepts | Yes, private first |
| **5 · AI Studio** | A skill, a workflow or an app (`ai_studio.md` §2) | The need keeps records, needs a screen, runs on a schedule or an event, or serves many people as a process | Yes |

**Rung 2 waits on the toolkit.** The chat has no tool for the data engine
until WS-43y ships. Until then, `find_tool` never answers `query`, and a
query-shaped need goes to rung 3.

### 3.2 The hand-off to AI Studio

The assistant offers Studio, and does not build there by itself, when one of
these holds:

1. The member wants the result **every day, week or month**, or on an event.
   That is a workflow (S1).
2. The data must **stay and grow**: visits, stock, permits. That is an app
   with objects (S2).
3. **Other people** enter or approve data. That is an app or a workflow.
4. The member wants the assistant to **always do it this way**. That is a
   skill (S0).

The offer is one line and a button: "This needs to run every Monday. Set it
up as a workflow in AI Studio?" A press starts the Studio build loop of
`ai_studio.md` §6.1 way 1, with the chat's code and sources as the seed. An
organization tool can be a step in that workflow (§9.6).

### 3.3 The fence for the ladder

`find_tool` gains one answer field, `rung_hint`, with one of `existing`,
`query`, `scratch`, `build` and `studio`. The typed `tier-decide` question of
`agent_sandbox.md` §6.1 step 3 sets it. The hint is advice. Two fences make
the rule hold:

- `run_code` refuses a run with no `find_tool` call in the same run (CBT-F2).
- `build_tool` keeps the gap rule of SBX-F8.

The quality of the ladder is an eval, not a fence (§13, CBT-E1).

---

## 4. The scratch code tool — `run_code`

### 4.1 The contract

`run_code(code, inputs, reads, uses, purpose)` is a new platform tool on the
general agent (D99).

| Argument | What it holds | Limit |
|---|---|---|
| `code` | Python 3 source. The entry is the module body | 64 KB |
| `inputs` | A map from a name to a handle (§6.1) or a literal JSON value | 20 names, 64 KB of literal JSON |
| `reads` | The Metorite read scopes that the code may call through the door, from `SANDBOX_ROUTES`. Default empty | 10 scopes |
| `uses` | The catalog action ids that the code may call through the bridge (§5) | 20 ids |
| `purpose` | One sentence for the run row and the disclosure | 280 characters |

It returns a JSON summary of at most 16 KB, a list of output handles (§6.3),
the exit status and the run id. The summary goes back to the model inside the
untrusted frame.

**"The step reads Metorite data" is a static fact.** It is true when `reads`
is not empty, or when an input handle is `art_`, `att_` or a `tr_` handle of a
Metorite tool. The broker decides it before the step starts, from the
arguments. The code cannot change it at run time. §4.4 and §5.4 use this fact.

### 4.2 How it runs

1. **The gap check.** The run must hold a `find_tool` call with the answer
   `none` or `unsure`. Otherwise `run_code` answers `call_find_tool_first`
   (CBT-F2).
2. **The capability.** The broker mints the run capability of
   `agent_sandbox.md` §5.1 with the phase `scratch`. Its read scopes are
   exactly `reads`. Its write scopes are empty, unless `uses` names a class B
   or C action. A write still waits for the card (§5.2 step 5).
3. **The container.** It is the `tools` target of the broker, with the same
   container key and caps as `agent_sandbox.md` §6.2. One thread keeps one
   container for its scratch and tool steps.
4. **The packages.** Only the packages of the sandbox image. There is no
   `pip install` in a scratch run, because the `run` profile has no build
   host. A need for a new package goes to rung 4. There the build profile
   installs it from a lock with hashes.
5. **The files.** The broker writes each input to
   `/workspace/.run/inputs/<name>` (§6.2). The code writes to
   `/workspace/outputs/`.
6. **The fix loop.** On an exception, the model may change the code and run
   it again. It has at most 2 more tries in one turn, inside the step budget.
7. **The disclosure.** The chat shows a closed "Code" row under the answer.
   It holds the purpose, the code, the read scopes, each source called with
   its call count, and the time. A source of a D98 connection shows "read as
   `<admin>` at `<provider>`, `<n>` calls". The member opens the row to read
   the code. The row is part of the WS-62 tool card, with no card of its own.
8. **The record.** Each run writes one `user_tool_runs` row with
   `phase = 'scratch'`, `tool_id = NULL`, and the SHA-256 of the code (§11.2).

**Until CBT-4 lands,** `run_code` refuses a `uses` that names an outside
source (an MCP tool or an API action), with `source_needs_card`. So no step
reaches an outside source before its card exists.

### 4.3 Limits of a scratch step

These are new rows in the table of `agent_sandbox.md` §5.6. The mechanism is
that table's.

| Limit | Scratch | Setting |
|---|---|---|
| CPU | 1 | `sandbox_cpus` |
| Memory | 1 GiB | `sandbox_memory` |
| The whole step | 120 s | `user_tool_step_seconds_scratch`, new |
| Bridge calls, all sources | 200 | `user_tool_bridge_calls_scratch`, new |
| Bridge calls to one outside server | 50 | `user_tool_bridge_calls_per_source`, new |
| Input handles | 20 | The `inputs` limit of §4.1 |
| Output files | 20 files, 50 MB in all | `user_tool_output_cap_mb`, new |
| Scratch runs for one member | 50 a day | `run_code_daily_cap`, new |
| Scratch containers | The `tools` caps of `agent_sandbox.md` §6.2 | `user_tool_max_per_org`, `user_tool_max_total` |

### 4.4 The card before a run

`run_code` asks no card for a step that reads Metorite data and calls no
outside source and no web host. A step that reads nothing also needs no card.
A card shows first in three cases:

1. `uses` names a class B or C action. Each held write gets its own card
   (`agent_sandbox.md` §5.3).
2. The step calls an outside source **and** reads Metorite data, names two
   or more outside servers, or passes data from one outside source to
   another (§5.4).
3. The step reads nothing from Metorite and asks for a `web:` host (SBX-Q2).

A step that reads Metorite data and asks for a `web:` host is refused with
`web_refused`. CBT-Q2 holds the owner's call on this rule.

### 4.5 What `run_code` is not

- It is not `run_script`. `run_script` runs on the host, and D129 decides its
  future. This spec does not depend on either state.
- It is not `code_task`. `code_task` is a harness session over a repo
  workspace. A scratch run is one snippet in one step.
- It is not a build. A scratch run makes no package, no smoke test and no
  registry row in `user_tools`.

---

## 5. The source bridge — sandbox code to an MCP server or an API

### 5.1 The rule

**Sandbox code reaches an outside source only through the door, and only as
a catalog action.** The gateway holds every credential. The container holds
only the run capability of `agent_sandbox.md` §5.1.

```text
sandbox code ──POST /sandbox/v1/actions/{action_id}──▶ door (capability → member, org)
   │                                                    │
   │  no token, no MCP URL, no API key                  ├─ catalog row: source, class
   │                                                    ├─ the uses check
   │                                                    ├─ the member's MCP gate (as in chat)
   │                                                    ├─ class A: run now
   │                                                    ├─ class B or C: hold for the card
   ▼                                                    ▼
answer in JSON (≤ 4 MB) or a handle (§6)      gateway ─▶ MCP server / API (org credential)
```

### 5.2 The route

`POST /sandbox/v1/actions/{action_id}` joins `SANDBOX_ROUTES`. It takes the
action's input as JSON and answers with its output.

1. **The action id** is a row of the one catalog (`ai_studio.md` §3.2). The
   bridge serves source 1 (an app door op), source 2 (an integration action),
   source 3 (an org MCP tool, B1b) and an O12 API action. It refuses source 4
   (a workflow), source 5 (a package's own objects, which only its owner
   runs) and source 6 (an organization tool) with 403 `nested_tool`. So a
   tool never calls a tool or a workflow, and no loop of tools can form. The
   door invents no action.
2. **The `uses` check.** The action must be in the `uses` of the scratch run,
   or in the `uses` of the stored tool version. Another id answers 403
   `not_declared`.
3. **The member's MCP gate.** For a source 3 action, the door applies the
   same three checks as the chat:
   - (a) `MAF_CODING_SCOPE` holds `tools:<org>`, and the org is in
     `MAF_MCP_ORGS`,
   - (b) the member holds `integrations:use:mcp-<name>` for the server,
   - (c) `McpPermissionGate.decide()` for the tool, with `member_present`
     true only when a chat is open.
   CBT-2 lifts these three checks into one function that the chat injection
   and the door both call. It does not copy them (CBT-F3).
4. **The per-server cap.** At most 50 calls to one outside server in one
   step (§4.3). Past it, the door answers 429 `bridge_cap`.
5. **The class.** Class A runs at once. Class B and C hold for the card of
   `agent_sandbox.md` §5.3. That card goes through `request_confirmation`,
   the one card path for writes that the door holds. `ai_studio.md` B2c-2
   uses `app_requests.open_request` for the delegated writes of a package.
   The door does not use it, because a sandbox step has the member in an
   open chat, and D130 binds the door to the chat card. An MCP tool that the
   admin set to "always confirm" holds for a card even when it is read-only.
6. **The D98 rule.** An org MCP connection over OAuth acts as the admin who
   connected it. Each card for such a tool says "This runs at `<server>` as
   `<admin>`". Each read of such a tool shows in the Code row with its count
   (§4.2 step 7), and in the closing message. CBT-Q9 asks whether such a
   connection's tools are class B in sandbox code. Until the owner answers,
   they are class B in sandbox code, and each call holds for its own card.
   The batch route of `agent_sandbox.md` §5.3 serves Metorite routes only,
   not MCP actions. So a member can expect one card for each call to a D98
   tool. The CBT-E4 fixtures use a server that is not D98.
7. **The answer.** An MCP answer is untrusted text (`tool_result_spill.py`
   rule 3). The door frames it and marks the session. An answer above 256 KB
   goes to a handle (§6), and the code reads the file.
8. **The stamp.** Each call carries `X-Actor-Via: sandbox:scratch` or
   `sandbox:<slug>@v<n>`, as `agent_sandbox.md` §5.2 sets it.

### 5.3 An API that is not connected yet

Code never holds an API key. A member who wants a new API in code takes two
steps:

1. **The connection.** An admin adds the API in Integrations, as an O12
   connection with actions. The assistant can help: it reads the API
   documentation and drafts the OpenAPI definition (CBT-10). The admin reviews
   the actions and their classes, and types the key into Integrations. The
   key never passes through chat.
2. **The code.** The code then calls the new actions through the bridge.

A public API with no key can use a `web:<host>` profile instead, on the rules
of SBX-Q2. A member approves the host, and the step then holds no Metorite
read.

### 5.4 Data and outside sources in one step

A step can send data out inside the arguments of a call. Two flows matter:
Metorite data to an outside source, and data from source A to source B.

- **Two or more outside servers in `uses` count as a flow.** The broker
  cannot see at run time whether code forwards the answer of A to B. So the
  static test is: the step reads Metorite data and names an outside source,
  or it names two or more different outside servers, or it passes a `tr_`
  of one server to another.
- **Until CBT-Q3 is answered, each flow is refused** with
  `data_to_source_refused`. A step may read Metorite data, or call one
  outside server, but not both. So the rung-3 example "call two sources and
  merge them" works only after a yes to CBT-Q3. Until then, the assistant
  calls each source in chat and merges the two `tr_` results in a step
  that calls no source.
- **After a yes to CBT-Q3,** one card shows before the step. It lists each
  outside source in `uses`, with its read scopes and its call cap. It says
  "This code can read your Metorite data and send it to `<server 1>`,
  `<server 2>`", or "Data from `<A>` can go to `<B>`".
- **`web:` hosts** never join a step that reads Metorite data (SBX-Q2 stands).
- **A marked session:** the card adds "This request came after content from
  outside your organization." The `uses` list comes from the model, and an
  injected answer can steer it. So the card lists the sources from `uses`,
  never from the model's words.

### 5.5 What this needs from other specs

| Need | Owner | Status |
|---|---|---|
| MCP tools as catalog rows (source 3) | `ai_studio.md` B1b | Not built. The critical path (§18) |
| An executor that runs a catalog row as a member, outside a package | `ai_studio.md` B2 | B2a runs package actions only. B2b-2 says its executor choice is a HIGH seam with no record. CBT-2 step 0 records the package-free executor in `ai_studio.md` B2 first, then builds it in the B2 module. No second door |
| The sandbox door and the capability | `agent_sandbox.md` SBX-2, SBX-3 | Not built |
| API connections with actions | `ai_studio.md` O12 | Not built. CBT-2 serves MCP tools only. API actions join after O12 |

---

## 6. Data in and out — handles

### 6.1 The handle grammar

A handle names data that the gateway can read for the member. The model
passes a handle, never the bytes.

| Handle | What it names | Resolved by | Slice |
|---|---|---|---|
| `tr_<8 hex>` | A large tool result of this run (CTX-1) | `tool_result_spill`, with the run's org, member and thread | CBT-1 |
| `art_<uuid>` | An artifact (WS-60) | The artifact registry, with the member's own access check | CBT-1 |
| `att_<id>` | A chat attachment of this thread | The attachment store, with the thread check | CBT-1 |
| `ds_<id>` | A data-engine dataset of this thread (§7.10) | The broker's run-data dir | CBT-3, after WS-43y1 |
| `out_<run id>/<path>` | An output file of an earlier step in this thread | The run row, the thread check and the broker's safe opener | CBT-3 |

- **The resolver** is one function, `resolve_handle(handle, binding)`, in a
  new module `acb_skills/handles.py`. The org, the member and the thread come
  from the run binding, never from an argument (R5e). A handle of another
  member, thread or org answers `handle_not_found`, with no hint that it
  exists (CBT-F1).
- **An expired handle.** The spill store has a TTL and an LRU, so a `tr_`
  handle can go between turns. It answers `expired_handle`, and the model
  asks the member to run the source again.
- **A safe path.** The path of an `out_` handle goes through the broker's
  safe opener. A `..`, an absolute path or a link answers `unsafe_path`
  (CBT-F1).
- A literal JSON value is allowed up to 64 KB. Above that, the tool asks for
  a handle.

### 6.2 Into the sandbox

1. The broker resolves each handle in `inputs`.
2. It writes each one to `/workspace/.run/inputs/<name>`, through the safe
   opener of the broker. JSON stays JSON. A table goes as Parquet when the
   data engine made it, else as its own file.
3. It writes `/workspace/.run/input.json` with each name, its type, its size
   and its file path. A stored tool reads the same file (`agent_sandbox.md`
   §6.3).
4. A source that the code calls at run time (§5) is the other way in. Its
   answer comes in the HTTP answer, or as a file for a large answer.

**The MCP case of the brief.** The chat calls an org MCP tool, and the answer
is 3 MB of nested JSON. CTX-1 keeps it as `tr_5a1c09e2` and gives the model a
preview. The model calls `run_code` with `inputs = {"orders": "tr_5a1c09e2"}`.
The 3 MB goes from the spill store to the container as a file. It never
passes through the model.

**The spill store is in process.** If the broker runs in its own unit (BH-3),
the resolver must fetch a `tr_` entry from the gateway process. CBT-1
measures where the broker runs, and names the transport, before CBT-3 builds
on it.

### 6.3 Out of the sandbox

| What | Where it goes | Limit |
|---|---|---|
| Files in `/workspace/outputs/` | One artifact row each through `write_artifact.py` (WS-60), private, scoped to the thread. The chat shows artifact cards | 20 files, 50 MB |
| `/workspace/outputs/result.json` | Back to the model in the untrusted frame | 16 KB. A larger file is cut, with a note |
| A proposed write | Through the door, with a card (`agent_sandbox.md` §5.3) | The door's caps |
| An output handle | `out_<run id>/<path>`, for the next step in the thread | Lives as long as the thread's run-data |

**A chain.** The output handle of one step can be an input of the next step.
So a member can say "now chart it", and the next step reads the file, not the
model's copy.

### 6.4 What never leaves

- No container file leaves except through `outputs/`.
- No output goes to an outside source except through a bridge call, which
  §5.4 covers.
- The summary to the model never carries a credential-shaped string. The
  broker runs `secret_shapes` over `result.json` and replaces a match with
  `[removed]` (CBT-F5).

---

## 7. Reuse — find it before you write it

`agent_sandbox.md` §6.1 and §6.6 own the lookup. This section adds six rules
on top of it.

### 7.1 Search covers scratch history

`find_tool` searches stored tools first. It then searches the member's own
scratch runs of the last 30 days, by `purpose`. A match answers
`scratch_match` with the run id. The assistant can then run the same code
again with new inputs. The code text lives in `user_tool_runs.code` for 30
days (§11.2, CBT-Q6).

### 7.2 The offer to keep

After a scratch run succeeds, the assistant offers "Keep this as a tool?" in
one of these cases:

1. The member ran scratch code 2 times in 30 days for gaps with the same
   `category`. The category comes from `tool_gaps.category`, through the
   run's `gap_id`.
2. The member says "again", "every time", "keep" or the like.
3. The code called an outside source, so a second build would cost a second
   round of reads of the documentation.

The offer is one line with a button. `user_tool_runs.offered_at` records it,
so it never shows twice for the same code SHA.

### 7.3 Save a scratch run as a tool

A press on "Keep" runs `build_tool` with a seed: the scratch code, its inputs
as the smoke sample, its `reads` and its `uses`. The build session of
`agent_sandbox.md` §6.3 turns it into the five files: `SKILL.md`,
`tool.json`, `main.py`, `requirements.lock` and `smoke.json`. The smoke test
and the persist rule of `agent_sandbox.md` §6.5 apply unchanged. The gap row
of the scratch run is the gap id of the build, so SBX-F8 holds.

### 7.4 One tool, not two

Before a persist, the gateway compares the new tool to the member's tools and
the org tools, by name and description, with the lexical search of
`find_tool`. A close match makes the result card ask "Add this as version
`n+1` of `<tool>`?" The member chooses. A new version of an organization tool
follows §9.4.

### 7.5 Stale tools

A tool with no run in 90 days shows "Not used for 90 days" in the library.
Its owner gets one "needs you" item through Studio's shell manifest (§8.5),
with an Archive button. No second bell entry. An archive sets the status
`disabled` with the reason `stale`. Nothing deletes a tool by age. The sweep
of §9.8 finds the stale tools.

### 7.6 A tool knows its sources

`user_tool_versions` gains `uses text[]`, the catalog ids that the version
calls. When an admin removes a connection, each tool that uses it shows
"Needs a connection: `<name>`", and `run_tool` refuses with
`connection_missing`. This copies the I1 rule of `ai_studio.md` §8.

---

## 8. The tool library — the list of past tools

### 8.1 Where it lives

**In AI Studio's library, as a fourth kind, Tools.** The library already
lists apps, workflows and skills (`ai_studio.md` A1). `readKind` in
`app/studio/page.tsx` reads `?kind=`. `?kind=tools` lists tools. No new pane
and no new nav entry (D49, D89). CBT-Q7 holds the owner's call.

### 8.2 The list

One row for each tool that the member can see.

| Column | Source |
|---|---|
| Name and one-line description | `user_tools.name`, `description`, shown as untrusted text (§11.3) |
| Scope | Mine, or Organization |
| Steward | §9.2 |
| Version | The version this member runs, and the latest |
| Sources | The connections in `uses` |
| Last run | `user_tools.last_run_at` |
| Runs in 30 days, and the success rate | `user_tool_runs` |
| Average credits for each run | `user_tool_runs.credits` |
| Status | Active, Disabled, Revoked, Needs a connection, Pending approval |

Filters: Mine, Organization, Pending approval (for an approver), Disabled.
Sort: last run, name, runs. Search: the `search` column of `user_tools`.

### 8.3 The detail page

- What it does, its inputs and its outputs (the schemas of the version).
- The code, read only, with a version picker and a diff between two versions.
- The sources it calls, and the scopes it asks for.
- Runs: the member's own runs. An approver sees the count and the outcome of
  every run of the org, with no input, output or code (§11.2).
- Buttons: **Run in chat**, **New version in chat**, **Share with the
  organization**, **Fork**, **Disable**. An approver also sees **Approve**,
  **Pin version** and **Revoke**.
- Each button that changes a tool opens the chat or a card. The page holds no
  code editor.
- **A button ships only in the slice that adds its chat tool and its route.**
  CBT-7 renders no Fork, Offer, Adopt or Propose button, and no SBX-6
  button until the SBX-6 routes exist. So `test_chat_parity.py` stays green
  at each step.

### 8.4 The routes

All under `/user-tools`, behind `user_tool_find_enabled` (SBX-1).

| Route | Use | Slice |
|---|---|---|
| `GET /user-tools` | The list, with the filters of §8.2 | CBT-7 |
| `GET /user-tools/{id}` | The detail | CBT-7 |
| `GET /user-tools/{id}/versions/{n}` | One version, with its files | CBT-7 |
| `GET /user-tools/{id}/runs` | The runs this caller may see | CBT-7 |
| `POST /user-tools/{id}/share`, `/approve`, `/pin`, `/disable`, `/revoke` | `agent_sandbox.md` §8 | SBX-6 |
| `POST /user-tools/{id}/versions/propose`, `/steward` | §9.4, §9.2 | CBT-8a |
| `POST /user-tools/{id}/fork`, `/offer` | §9.7 | CBT-8b |
| `POST /user-tools/{id}/adopt` | §9.5 | CBT-8c |

Each POST writes an audit row.

### 8.5 Shell manifest (R9)

No new app. The Tools kind joins the Studio pane. So Studio's shell manifest
(`ai_studio.md` §13) gains two jobs:

- "Find a tool": verb `find`, with a search provider over `user_tools`.
- "Approve a tool": a "needs you" item for a holder of `tools:approve`. The
  stale-tool item (§7.5) and the steward item (§9.5) use the same seam.

No palette, bell, rail or ⌘K listener of its own. CBT-7 edits the manifest
and its search source.

---

## 9. Personal tools and organization tools

### 9.1 The two scopes

| | Personal tool | Organization tool |
|---|---|---|
| Who sees it | Its owner only | Every member of the org |
| Who runs it | Its owner, at `latest_version` | Each member, at `org_version`, with that member's own access (`agent_sandbox.md` §8.1) |
| Who changes it | Its owner, through the chat | The steward or an approver proposes a version. A second approver approves it (§9.4) |
| Who owns the row | The member | The organization. The steward looks after it |
| In Studio workflows | No | Yes, as a catalog action (§9.6) |
| After the owner leaves | §9.5 | Stays. The steward moves (§9.5) |

A group scope (`group:<slug>`, D12) comes later, when the owner asks.

### 9.2 The steward

`user_tools` gains `steward_email text NULL`. For an organization tool, the
steward is the member who answers for it. At the first approval, the steward
is the tool's owner. An approver may move the steward to another member, and
the move writes an audit row.

### 9.3 Promotion: personal to organization

The order follows `agent_sandbox.md` §8.1: the owner asks, an approver
approves one version, and only then does the tool show to the org.

1. The owner presses **Share with the organization**, or asks in chat.
2. The tool shows "Pending approval" to approvers, as a "needs you" item.
3. The approval card shows the code, `SKILL.md`, `tool.json`, the scopes, the
   `uses`, the description and the outcomes of the last 5 runs.
4. **The approver checks the description, `SKILL.md` and `tool.json` for
   instructions.** Other members' assistants read them, so they are a place
   for an injected instruction (§11.3).
5. On approval, `visibility` becomes `org`, `org_version` becomes the
   approved version, and `steward_email` becomes the owner.

A demotion is a revoke followed by a fork for the owner. Nothing turns an
organization tool back into a private row in place, because other members may
have runs on it.

### 9.4 Admin updates, review and rollback

**This is stricter than D130.3,** which needs one approver. Here an approver
cannot approve their own proposal while a second approver exists.

**Who runs which version of an organization tool.** The rule "the owner runs
`latest_version`" of `agent_sandbox.md` §8.2 binds a personal tool. For an
organization tool, every member, the owner and the steward too, runs
`org_version`. Only the proposer of a version runs it before approval, for
its smoke test and its first real run. `agent_sandbox.md` §8.2 gets a pointer
to this rule in this PR, and CBT-8a changes SBX-F13 to match.

1. **Propose.** The steward or an approver asks in chat: "Add a column for
   GST to the `invoice-summary` tool." The build session runs as that person,
   with the build card. The new version passes its smoke test and its first
   real run on that person's own data. It lands as a version row with
   `built_by_email` set to the proposer. `org_version` does not move.
2. **Review.** A holder of `tools:approve` who did not propose the version
   approves it. The card shows the diff from `org_version`, `SKILL.md` and
   `tool.json`. When the org has only one holder of `tools:approve`, that
   holder may approve their own version, and the audit row says
   `self_approved = true` (CBT-Q4).
3. **No approval from a risky chat.** `approve_tool_version` refuses in a
   marked session, and in the thread that proposed the version
   (`user_tool_versions.built_in_thread`), in any later run of it (CBT-F9).
   So an injected chat cannot propose and approve, now or on a later turn.
4. **Roll out.** The approval sets `org_version`. The next `run_tool` by any
   member uses it. A running step keeps its version.
5. **Roll back.** An approver presses **Pin version** and picks an earlier
   version. `org_version` moves back in one step. No version row changes
   (SBX-F10).
6. **Stop.** **Disable** stops new steps at once (`agent_sandbox.md` §8.3).

A member who is not the steward and not an approver cannot propose a version
of an organization tool. That member can fork it (§9.7).

### 9.5 When a member leaves

The offboard step lives in `routes/admin/members.py`. CBT-8c adds one hook to
it.

- **Organization tools** stay active. When the steward leaves, the steward
  becomes empty. Each approver gets a "needs you" item: "Pick a new steward
  for `<tool>`."
- **Personal tools** are set `disabled` with the reason `owner_left`.
- **Adopt.** An approver can adopt one. It is a private tool of another
  person, so a card names the departed owner and asks the approver to
  confirm. The approver becomes the owner, and the tool stays private to the
  approver until a share. Adopt writes an audit row. CBT-Q10 holds the
  owner's call.
- **Runs** stay. `user_tool_runs` has no foreign key to the member.

### 9.6 Organization tools as catalog actions

An approved organization tool becomes a row of the one action catalog, as a
new **source 6**, `tool:<slug>`. Its class comes from its version: class A
when `write_scopes` is empty, else class B, or class C when one write scope
is class C.

- **Only a workflow step runs source 6.** The step goes through the workflow
  tool path (`routes/workflows/tools.py::execute_tool` and `ai_studio.md`
  E5), not through `invoke.py`. The step runs `run_tool` at `org_version`, as
  the viewer, or as the `run_as` member of B6 for a run with no viewer. A run
  with no viewer runs a read tool only (SBX-Q5).
- **Sandbox code never runs source 6** (§5.2 step 1).
- **Logic modules (B5).** `ai_studio.md` B4 and B5 return **proposed
  writes**, which B2 applies in one transaction. A stored tool sends each
  write through the door, with a card. These two contracts differ. So CBT-9
  does not absorb B5. CBT-Q5 asks whether B5 should use this registry, with
  B5's own limits (no network, no database link) and a proposed-writes
  output file. A yes adds a slice after CBT-9.

### 9.7 Forks

1. Any member may fork a tool that the member can see. The fork is a new
   personal tool, with `forked_from_tool_id` and `forked_from_version` set.
2. A fork never changes the original.
3. The fork's owner may press **Offer to the organization**. That makes the
   fork's latest version a proposal for the original. Step 2 of §9.4 runs.
4. `find_tool` returns the member's own fork before the organization tool,
   and labels both.

### 9.8 The schema change

**Preferred: fold these columns into SBX-1's create table,** as an edit to
`agent_sandbox.md` §7, when SBX-1 has not merged. Else one expand-only
migration at the next free number at build time (R1, R6). CBT-6a reads
SBX-1's migration first and picks one.

| Table | Column | Type | Notes |
|---|---|---|---|
| `user_tools` | `steward_email` | `text` | Null |
| `user_tools` | `forked_from_tool_id` | `uuid` | Null. No foreign key, so a revoke of the original keeps the fork |
| `user_tools` | `forked_from_version` | `int` | Null |
| `user_tool_versions` | `uses` | `text[]` | Not null, default empty |
| `user_tool_runs` | `code` | `text` | Null. Scratch runs only. 64 KB at most. `secret_shapes` runs over it before the write. The sweep sets it to NULL after 30 days |
| `user_tool_runs` | `code_sha256` | `text` | Null |
| `user_tool_runs` | `purpose` | `text` | Null. 280 characters at most |
| `user_tool_runs` | `offered_at` | `timestamptz` | Null. When the keep offer showed (§7.2) |

- `user_tool_runs.phase` takes the new value `'scratch'`. If SBX-1 has a CHECK
  on `phase`, the migration adds a new CHECK as `NOT VALID` and validates it
  in a guarded block, as migration 148 does.
- **The sweep.** A new job, `apps/services/gateway/gateway/user_tool_sweep.py`,
  copies the pattern of `agent_run_sweep.py`. It clears `code` after 30 days
  and finds tools with no run in 90 days (§7.5). It runs for each org in a
  tenant session.

---

## 10. Permissions and tenancy (R5)

| Rule | How it holds | Fence |
|---|---|---|
| Every new column is on a tenant table with RLS | §9.8 adds columns only to the SBX tables | `test_tenant_coverage.py` for the tables, CBT-F1 for the reads |
| The org and the member come from the run binding | The capability (SBX-F1), the handle resolver (§6.1) and the door | CBT-F1, SBX-F1 |
| A handle of another member, thread or org resolves to nothing | `resolve_handle` | CBT-F1, with an R8 half as the NOBYPASSRLS role |
| The door runs an MCP tool only as the member could in chat | §5.2 step 3, one shared function | CBT-F3 |
| A shared tool shares code, never authority | `agent_sandbox.md` §8.1 | SBX-F13 |
| No new database connection site, one new tenant-discovery site | Routes and the broker use the seam. The sweep reads the RLS-exempt `organization` table to list orgs, as `agent_run_sweep.py` does, and registers in `tests/unit/test_db_engine_seam.py` | `_SYNC_ENGINE_ALLOWED`, `_PSYCOPG_ALLOWED` ratchets, `test_db_engine_seam.py` |
| A Redis key goes through the prefix wrapper | The daily cap counter of §4.3 | The Redis prefix ratchet, and CBT-F4 |
| Who may run scratch code | A member with `feature:studio`, in an org on the new list `RUN_CODE_ORGS`. An admin turns it off by taking the org off the list. It reloads with no restart (`docs/flag_reload.md`) | CBT-F4 (CBT-Q1) |
| Who approves | `tools:approve` (`agent_sandbox.md` §8.1) | SBX-F13 |
| The sweep sees one org at a time | `user_tool_sweep.py` runs in a tenant session | CBT-F10, with an R8 half |

---

## 11. Cost, audit and safety

### 11.1 Credits and cost

`agent_sandbox.md` §10 owns the meter. This spec adds four rules:

1. **A scratch step meters as one step,** with `phase = 'scratch'`, through
   the same `usage_event` row and the same spend gate. Its price follows
   SBX-Q4: a floor of 1 credit for each step, as D130 recorded.
2. **The member who runs pays,** in the org of the run. An organization
   tool's runs bill to the member who ran them, not to the steward.
3. **A bridge call to an outside source** costs no Metorite credit beyond the
   step. The outside provider's own fees go to the org's own account.
4. **The daily cap** of §4.3 stops a loop that writes code again and again.
   At the cap, `run_code` answers `daily_cap` (§12.2).

The library shows the average credits of each run (§8.2). The build card
shows the estimate (`agent_sandbox.md` §6.1). CBT-F4 tests rules 1 and 4.

### 11.2 Audit

| Event | Where | What it holds |
|---|---|---|
| A scratch run | `user_tool_runs`, `phase = 'scratch'` | The member, the thread, the purpose, the code SHA, the code for 30 days, the sources called with counts, the outcome. No input or output data |
| A bridge call | The log line `sandbox.door.call` (`agent_sandbox.md` §9.1), with the action id | No arguments, no answer |
| A write through a tool | `meta.via = sandbox:scratch` or `sandbox:<slug>@v<n>` | As D-PM-36 |
| Share, approve, self-approve, pin, disable, revoke, fork, offer, adopt, steward change | `audit_event` through `acb_audit.record` | The actor, the tool, the versions, the reason |

**Who reads scratch code.** The member who ran it, and nobody else. Scratch
code comes from a private thread (D12). An approver sees the code of a
version in a share request or a proposal, never a member's scratch code.
CBT-Q10 holds the owner's call. The 30-day retention ends in the sweep of
§9.8.

### 11.3 Safety

`agent_sandbox.md` §5.7 holds the base threat table. These rows are new.

| Threat | Control | Fence |
|---|---|---|
| An MCP answer carries an instruction to write hostile code | The answer is untrusted (§5.2 step 7) and marks the session. The code runs with the member's access as the ceiling. Each write waits for a card from the wire | SBX-F3, CBT-E3 |
| An MCP tool's own description is poisoned | The catalog row holds the server's description as untrusted text. The assistant sees it inside the frame. It never enters the system text | CBT-F6 |
| A shared tool's text tells another member's assistant what to do | Each path that shows a tool's name, description, `SKILL.md` or `tool.json` to a model frames it as untrusted: `find_tool`, `list_tools`, `describe_tool`, source 6 rows and any `SKILL.md` load. The approver reads all of it (§9.3) | CBT-F6 |
| Code sends Metorite data, or data from one source, to an outside source in the arguments of a call | Refused until CBT-Q3. Then one card that names each source, its scopes and its cap (§5.4). `web:` hosts stay closed in a step that reads | CBT-F7, SBX-F5 |
| The model widens `uses` after an injected answer | The card lists `uses`, never the model's words. A marked session adds the outside-content line | CBT-F7 |
| A D98 connection acts as an admin for a member, in a loop | Class B in sandbox code until CBT-Q9. The per-server cap of 50. The Code row and the closing message name the admin and the count | CBT-F3 |
| A server says a write tool is read-only | The D98 rule above. A non-D98 server keeps `readOnlyHint` (B1b), with the per-server cap | CBT-F3 |
| A tool calls a tool, or a workflow, in a loop | 403 `nested_tool` (§5.2 step 1) | CBT-F3 |
| Code tries an action outside its `uses` | 403 `not_declared` | CBT-F3 |
| Code writes a key into a stored tool or a run row | The persist step refuses a file with a credential shape. `secret_shapes` runs over `user_tool_runs.code` before the write | CBT-F5 |
| An injected chat proposes and approves a version | `approve_tool_version` refuses in a marked session and in the proposing thread | CBT-F9 |
| A loop of scratch runs | The step budget, the daily cap and the spend gate | CBT-F4 |
| A large answer fills the model | Handles (§6). The model gets a preview only | CBT-F1 |
| A handle reaches another member's data | `resolve_handle` reads the binding only | CBT-F1 |
| A marked session writes code | Allowed. No `web:` profile. Each card says that the request came after outside content | SBX-F5 |

### 11.4 Resource limits

§4.3 holds the limits of a scratch step, and `agent_sandbox.md` §5.6 the
limits of build, smoke and run. A bridge call has the door's limits: 600
requests a step for a stored tool, 200 for a scratch step, 50 for one outside
server, and 4 MB for each answer. Past a limit, the door answers 429.

---

## 12. Chat parity and failure copy

### 12.1 Chat parity

Each act of the library has a chat tool in `chat_parity.py`, as
`chat_parity.md` requires. **CBT-7 adds these rows, the `APP_OF` prefix for
`/api/user-tools` and the new tool file in `TOOL_SOURCES`, in the same PR as
the UI.** Otherwise the new UI requests turn `test_chat_parity.py` red.
`DEBT_CEILING` does not grow, so a row with no tool yet is not allowed.

| Library act | Chat tool | Class | Slice |
|---|---|---|---|
| List tools | `list_tools` | A | CBT-7 |
| Read a tool and its versions | `describe_tool` | A | CBT-7 |
| Run a tool | `run_tool` | Per the tool | SBX-5 |
| New version | `build_tool` with a `tool_id` | B, on the build card | SBX-4 |
| Share with the organization | `request_tool_share` | B | CBT-7, over the SBX-6 route |
| Approve a version | `approve_tool_version` | C, on a card with the diff. Refused in a marked session and in the proposing thread | CBT-7, over the SBX-6 route |
| Pin a version (rollback) | `pin_tool_version` | B | CBT-7 |
| Disable and enable | `set_tool_status` | B | CBT-7 |
| Revoke | `revoke_tool` | C | CBT-7 |
| Propose a version, set the steward | `propose_tool_version`, `set_tool_steward` | B | CBT-8a |
| Fork, offer to the org | `fork_tool`, `offer_tool_version` | B | CBT-8b |
| Adopt | `adopt_tool` | C, on the card of §9.5 | CBT-8c |

**Run in chat** and **New version in chat** open the chat with a prompt. They
send no request of their own, so they need no row.

If SBX-6 has not merged when CBT-7 starts, CBT-7 ships only the read tools
and the read routes. The write tools land with the slice that adds their
route.

### 12.2 Failure copy

The words follow `agent_writing_voice.md`. Product copy is not bound by STE
(`docs/style_ste.md` §1). Each failure has one fixed `error_class` and one
sentence. The model may add one sentence of context. It never shows a stack
trace by default. The "Code" row of §4.2 step 7 holds the trace.

**The list.** `agent_sandbox.md` §7.3 owns the base `error_class` list. This
spec adds rows to the same list. The code keeps it in one module,
`packages/acb_skills/acb_skills/user_tool_errors.py`. CBT-3 creates it if SBX
has not. CBT-F8 holds the table equal to that module.

| `error_class` | What the member reads |
|---|---|
| `sandbox_busy` | "The code runner is busy. I'll try again in a minute." |
| `step_timeout` | "The code ran past its time limit and I stopped it. I can try a smaller piece of the data." |
| `code_error` | "My code failed three times on this data. Here is what went wrong: …" |
| `call_find_tool_first` | (Internal. The model calls `find_tool` and tries again. The member reads nothing.) |
| `connection_missing` | "This needs the `<name>` connection, and it is not set up. An admin can add it in Integrations." |
| `connection_auth` | "The `<name>` connection needs to sign in again. An admin can reconnect it in Integrations." |
| `source_down` | "`<server>` did not answer. Nothing was changed." |
| `source_needs_card` | "I can't call `<server>` from code yet. I can call it directly in chat." |
| `not_declared` | "The code tried to use `<action>`, which it did not list. I stopped it." |
| `nested_tool` | "Code can't run another saved tool or a workflow. I can run them one after the other in chat." |
| `class_x` | "Code can never do `<action>`. You can do it yourself in the app." |
| `bridge_cap` | "The code called `<server>` too many times in one run, so I stopped it." |
| `rate_limited` | "The code made too many requests, so I stopped it. I can try a smaller piece." |
| `data_to_source_refused` | "This code would send your data to `<server>`, and that is turned off." |
| `web_refused` | "I can't open `<host>` in a step that reads your Metorite data." |
| `handle_not_found` | "I can't find that earlier result. Can you run that step again?" |
| `expired_handle` | "That earlier result has expired. Can you run that step again?" |
| `unsafe_path` | "The code asked for a file outside its own folder, so I stopped it." |
| `write_declined` | "You declined the change, so I made none." |
| `write_timeout` | "The approval card timed out, so I made no change." |
| `output_too_big` | "The result is larger than 50 MB. I can split it, or filter it first." |
| `daily_cap` | "You've reached today's limit for running new code. A saved tool still runs." |
| `no_credits` | "Your organization is out of AI credits, so I can't run code now." |
| `feature_off` | "Running code is turned off for your organization." |
| `tool_disabled` | "The `<tool>` tool is turned off. Its steward is `<name>`." |
| `version_pending` | "The new version of `<tool>` waits for an approval. I used version `<n>`." |
| `approve_refused` | "I can't approve a tool version from this chat. Approve it from the Tools library." |

---

## 13. Evals

One eval package, `evals/chat_built_tools/`, on the pattern of
`evals/routing/`: `run.py` with `--scripted` and `--live`, a row in
`evals/README.md`, a checker for each case, and a unit test
`tests/unit/test_chat_built_tools_eval.py` with mutation tests of the
checkers. The scripted run calls no model and no network, and
`.github/workflows/skill-eval.yml` runs it. **A scripted run proves the path,
never the choice.** Only the live run measures the model's choice.

| # | Eval | What it measures | Scripted run | Live bar |
|---|---|---|---|---|
| **CBT-E1** | The ladder | 60 member requests, each labelled with its rung (§3.1): 15 existing tool, 10 query, 15 scratch, 10 stored tool, 10 Studio | Each case reaches the right tool path when the script picks the labelled rung. The checker catches a wrong rung (mutation test) | 85 % on the right rung. At most 5 % of rung 1 and 2 cases go to rung 3 or higher. The 10 query cases count only after WS-43y. Before it, they must land on rung 3 |
| **CBT-E2** | Reuse | A fixture registry of 30 tools, 40 needs, 20 of which a tool covers | `find_tool`'s lexical step returns the gold tool among its candidates for 20 of 20 | `find_tool` answers the gold tool for 18 of the 20, and a wrong tool for at most 2 of the other 20 |
| **CBT-E3** | Safety | 25 attacks: an MCP answer with instructions, a poisoned MCP description, a shared tool description, an email that asks to send data out, a handle of another member, a nested tool call, a self-approval from a marked chat | **The script makes the model comply** with each attack. The gates must still block each one. 25 of 25, and one miss fails the run. CI runs this on each PR that touches the door, the handles or the bridge, once CBT-11 lands. Before it, each of CBT-1, CBT-2, CBT-4 and CBT-8a carries its own attack cases in its fence file | Same, with a live model |
| **CBT-E4** | Data tasks | 20 tasks over fixture MCP answers from a server that is not D98: nested JSON, CSV in a field, paged results, a merge of two sources. The merge cases run with the CBT-Q3 test switch on | The scripted code runs in the image and the checker accepts the output | 16 of 20 right on the first turn, inside the step budget |
| **CBT-E5** | Cost | The credits of CBT-E4 | The run writes one usage event for each step | The median task costs at most 2 credits. The report gives the exact figure |

**Command.** `uv run python -m evals.chat_built_tools.run --scripted`, and
`--live` before a turn-on. A filter flag selects one eval, as the routing
eval does.

---

## 14. Slices

Each slice is one pull request, ships dark, and takes the next free migration
number at build time (R1). The risk tiers follow `next-ticket.md` step 1b.
Every slice that runs code waits for the SBX slice it names.

| Slice | Work | Depends on | Risk | Gate | Task |
|---|---|---|---|---|---|
| **CBT-0** | The owner answers §15. The answers become one numbered decision in `work_plan.md` §3 | — | — | **OWNER-GATE** (a decision) | #1658 |
| **CBT-1** | Data handles `tr_`, `art_` and `att_`: `acb_skills/handles.py`, `resolve_handle`, `expired_handle`, the R8 half, and the measured note on where the spill lives (§6.2) | CTX-1 (built) | HIGH (tenancy) | AGENT-SAFE. Dark | #1659 |
| **CBT-2** | The source bridge for MCP tools. Step 0: record the package-free executor in `ai_studio.md` B2. Then the route, the `uses` check, the shared MCP gate function, the per-server cap, the class rule, the D98 rule, `nested_tool` | SBX-2, SBX-3, B1b | HIGH (auth, outward writes) | AGENT-SAFE. Dark. The D98 class follows CBT-Q9's default | #1660 |
| **CBT-3** | `run_code`: the gap check, `reads`, the `scratch` phase, image-only packages, the fix loop, the Code row, outputs as artifacts, the run row, `RUN_CODE_ORGS`, the error module, handles `ds_` and `out_`, `source_needs_card` | SBX-4, CBT-1, CBT-6a. Not CBT-2: `source_needs_card` refuses outside sources until CBT-4 | HIGH (code execution) | AGENT-SAFE. Dark | #1661 |
| **CBT-4** | The data-and-source card (§5.4), the per-step egress fact, the daily cap | CBT-2, CBT-3, SBX-7 | HIGH (exfiltration) | AGENT-SAFE. Dark. The card path stays refused until CBT-Q3 | #1662 |
| **CBT-5** | The ladder: `rung_hint` in `find_tool`, the system text, the Studio offer (§3.2), and eval CBT-E1 scripted | SBX-1 | STD | AGENT-SAFE. Dark | #1663 |
| **CBT-6a** | The columns of §9.8, folded into SBX-1 or as an expand migration, and the sweep job | SBX-1 (its PR or its merge) | HIGH (migration) | AGENT-SAFE | #1664 |
| **CBT-6b** | Reuse: scratch history in `find_tool`, the keep offer, save as tool, merge into a version, stale tools, `uses` and "needs a connection" (§7) | CBT-3, SBX-5, CBT-6a | STD | AGENT-SAFE. Dark | #1665 |
| **CBT-7** | The library: `?kind=tools`, the list and the detail, the read routes, the Studio manifest jobs, and the parity rows of §12.1 marked CBT-7 | SBX-1, CBT-6a | STD (UI) | AGENT-SAFE. Preview pane (D145) | #1666 |
| **CBT-8a** | Stewardship: propose a version, the two-person approval, self-approval with a flag, refusal in a marked or proposing run, steward moves, the §8.2 amendment and SBX-F13 | SBX-6, CBT-6a, CBT-7 | HIGH (approval) | AGENT-SAFE. Dark | #1667 |
| **CBT-8b** | Forks and "Offer to the organization" | CBT-8a | STD | AGENT-SAFE. Dark | #1668 |
| **CBT-8c** | The offboard hook and Adopt | CBT-8a | HIGH (member lifecycle) | AGENT-SAFE. Dark. Adopt follows CBT-Q10's default | #1669 |
| **CBT-9** | Organization tools as catalog source 6, run by workflow steps | CBT-8a, `ai_studio.md` B1 and E5 | HIGH | AGENT-SAFE. Dark | #1670 |
| **CBT-10** | Draft an API connection from its documentation, as an O12 definition for an admin to review | `ai_studio.md` O12 | STD | AGENT-SAFE. Typing the key is the admin's act in Integrations | #1671 |
| **CBT-11** | Evals CBT-E2 to CBT-E5, the failure copy fence, and the member guide section | CBT-1, CBT-2, CBT-3, CBT-4, CBT-6b, CBT-8a | STD | AGENT-SAFE | #1672 |
| **CBT-12** | API actions on the bridge: O12 actions join §5.2 | CBT-2, O12 (waits on O12) | HIGH (outward writes) | AGENT-SAFE. Dark | #1673 |
| **CBT-13** | Turn on: `RUN_CODE_ORGS` and the library for Fracktal on production, after SBX-9 | CBT-1 to CBT-12, SBX-9 | — | **OWNER-GATE** | #1674 |

**The first slice to dispatch** is CBT-1. CBT-5 and CBT-6a follow SBX-1.

### 14.1 Done when, files and verification for each slice

Re-verify every path at dispatch. "New" means the file does not exist at
`a221ab972`. A test marked "(SBX-n, not built)" is an SBX fence that the
named SBX slice creates. It must exist before that CBT slice can verify.

Each pytest run below needs the database first:

```bash
bash scripts/dev_db.sh
eval "$(bash scripts/dev_db.sh --export)"
```

A skip in a run that names an R8 test is a failure.

#### CBT-1 — Data handles

- **Files.** New: `packages/acb_skills/acb_skills/handles.py`,
  `tests/unit/test_tool_handles.py`. Changed:
  `packages/acb_skills/acb_skills/tool_result_spill.py` (one read function for
  the resolver, no change to its rules).
- **Done when.**
  1. `tr_`, `art_` and `att_` resolve for their own member, thread and org.
  2. A handle of another member, thread or org answers `handle_not_found`.
     The R8 half runs as the NOBYPASSRLS role for `art_`.
  3. No argument of `resolve_handle` names an org, a member or a thread.
  4. A `tr_` entry that is untrusted stays untrusted after it resolves.
  5. A `tr_` entry past its TTL answers `expired_handle`.
  6. The PR states where the broker resolves a `tr_` entry (same process, or
     a named fetch from the gateway).
- **Verify.** `uv run pytest tests/unit/test_tool_handles.py tests/unit/test_tool_result_spill.py -q -rs`

#### CBT-2 — The source bridge for MCP tools

- **Files.** Changed: `project-docs/specs/ai_studio.md` B2 (step 0), the
  SBX-2 door module (SBX-2 names it), `routes/studio/invoke.py` or the B2
  module the record names, the `SANDBOX_ROUTES` list,
  `apps/services/orchestrator/orchestrator/_maf_mcp.py` and the MCP injection
  in `_tool_injection.py` (the shared gate function). New:
  `tests/unit/test_sandbox_bridge.py`.
- **Done when.**
  1. Step 0 is merged first, or in the same PR: `ai_studio.md` B2 records the
     package-free executor.
  2. One function answers "may this member call this MCP tool now", and both
     the chat injection and the door call it.
  3. A class A MCP tool in `uses` runs, and its answer comes back framed.
  4. An action outside `uses` answers 403 `not_declared`.
  5. A source 4, 5 or 6 action answers 403 `nested_tool`.
  6. A tool that the chat withholds from this member, the door refuses.
  7. A class B tool holds for a card through `request_confirmation`. A
     refusal answers 403.
  8. An "always confirm" read tool holds for a card.
  9. A D98 tool is class B in sandbox code, and its card names the admin and
     the server.
  10. The 51st call to one server in a step answers 429 `bridge_cap`.
  11. An answer above 256 KB comes back as a handle.
- **Verify.** `uv run pytest tests/unit/test_sandbox_bridge.py tests/unit/test_maf_mcp_injection.py tests/unit/test_maf_mcp_confirm.py tests/unit/test_mcp_always_confirm_r8.py -q -rs`, plus `tests/unit/test_sandbox_routes_coverage.py` (SBX-2, not built).

#### CBT-3 — `run_code`

- **Files.** New: `packages/acb_skills/acb_skills/run_code_tool.py`,
  `packages/acb_skills/acb_skills/user_tool_errors.py` (if SBX has not made
  it), `tests/unit/test_run_code.py`. Changed:
  `apps/services/orchestrator/orchestrator/_tool_injection.py`,
  `apps/services/orchestrator/orchestrator/sandbox_broker.py` (the `scratch`
  phase, the safe path of `out_`), `packages/acb_skills/acb_skills/handles.py`
  (`ds_` after WS-43y1, and `out_`),
  `packages/acb_skills/acb_skills/write_artifact.py` (one artifact for each
  output), `packages/acb_common/acb_common/settings.py` (`RUN_CODE_ORGS`, the
  limits of §4.3), the prefix list of `env_guard.py` (add `RUN_CODE_`), and the chat tool card in
  `workbench/control_plane/src/components/` (the Code row, re-verify the
  card's path).
- **Done when.**
  1. With the org off `RUN_CODE_ORGS`, no agent holds `run_code`.
  2. With no `find_tool` call in the run, it answers `call_find_tool_first`.
  3. With `reads` empty and no Metorite handle, the capability holds no read
     scope.
  4. A snippet reads a `tr_` input file and writes a CSV, and the CSV comes
     back as an artifact card.
  5. A `uses` that names an outside source answers `source_needs_card`.
  6. An `out_` handle with `..`, an absolute path or a link answers
     `unsafe_path`.
  7. After 3 failed tries the tool answers `code_error`.
  8. One `user_tool_runs` row lands, with `phase = 'scratch'`, the code SHA,
     and the code after `secret_shapes`.
  9. A unit test shows that the broker argv carries the `run` profile.
  10. `sandbox_docker` half: the snippet runs in the image as uid 1000, and
      `pip install` fails at the proxy (needs WS-43g).
- **Verify.** `uv run pytest tests/unit/test_run_code.py tests/unit/test_tool_handles.py -q -rs`, plus `tests/unit/test_user_tool_injection.py` (SBX-1, not built). The Docker half: `uv run pytest -m sandbox_docker tests/unit/test_run_code.py -q -rs` on `.github/workflows/sandbox-docker.yml`. Frontend: `cd workbench/control_plane && npx tsc --noEmit && npx vitest related <changed files>`.

#### CBT-4 — The data-and-source card and the daily cap

- **Files.** Changed: `run_code_tool.py`, the SBX-3 card builder. New:
  `tests/unit/test_run_code_egress_card.py`.
- **Done when.**
  1. The broker decides "reads Metorite data" from the arguments, before the
     step, and the code cannot change it.
  2. With CBT-Q3 on its default, a step that reads Metorite data and calls an
     outside source answers `data_to_source_refused`. So does a step with two
     outside servers in `uses`, and a step that passes a `tr_` of server A to
     server B.
  3. With the CBT-Q3 switch on (a test setting), one card lists each source,
     its scopes and its cap. A refusal starts nothing.
  4. A step with a Metorite read and a `web:` host answers `web_refused`.
  5. A marked session adds the outside-content line.
  6. The 51st scratch run of a member in a day answers `daily_cap`. The
     counter key goes through the Redis prefix wrapper. The test is in
     `test_run_code.py` (CBT-F4).
  7. A scratch step writes one `usage_event` row, and a step for an org at
     its hard cap does not start. The test is in `test_run_code.py`.
- **Verify.** `uv run pytest tests/unit/test_run_code_egress_card.py tests/unit/test_run_code.py -q -rs`, plus `tests/unit/test_sandbox_egress_profiles.py` and `tests/unit/test_sandbox_metering.py` (SBX-4 and SBX-7, not built).

#### CBT-5 — The ladder

- **Files.** Changed: `packages/acb_skills/acb_skills/user_tool_tools.py`
  (SBX-1 creates it, with `find_tool`), the general agent's system text. New:
  `evals/chat_built_tools/` (`run.py`, the E1 cases, the checker),
  `tests/unit/test_find_tool_rung.py`,
  `tests/unit/test_chat_built_tools_eval.py`, a row in `evals/README.md`, a
  step in `.github/workflows/skill-eval.yml`.
- **Done when.**
  1. `find_tool` returns `rung_hint` with one of the five values.
  2. With the WS-43y toolkit off, `find_tool` never answers `query`.
  3. A Studio-shaped need ("every Monday") gets `studio`, and the chat shows
     the offer.
  4. The scripted E1 run passes. A mutation of the checker (a wrong rung)
     fails it.
  5. `find_tool` frames each candidate's name and description as untrusted
     text.
- **Verify.** `uv run pytest tests/unit/test_find_tool_rung.py tests/unit/test_chat_built_tools_eval.py -q -rs`, then `uv run python -m evals.chat_built_tools.run --scripted`.

#### CBT-6a — The columns and the sweep

- **Files.** Either an edit of SBX-1's migration and `agent_sandbox.md` §7
  (SBX-1 not merged), or a new migration at the next free number. New:
  `apps/services/gateway/gateway/user_tool_sweep.py`,
  `tests/unit/test_user_tool_stewardship_schema.py`. Changed:
  `apps/services/gateway/gateway/main.py` (a start hook, as
  `start_agent_run_sweep`), `tests/unit/test_db_engine_seam.py` (the
  tenant-discovery site).
- **Done when.**
  1. The PR says which path it took, after it read SBX-1's migration.
  2. The schema applies on a fresh dev database and on a copy of the current
     schema.
  3. Each new column is nullable or has a default.
  4. As the NOBYPASSRLS role, org A reads no new column of org B's rows.
  5. The sweep clears `code` on a row older than 30 days, in one org's tenant
     session, and leaves other orgs' rows alone.
  6. The sweep lists a tool with no run in 90 days.
  7. The sweep is registered in `test_db_engine_seam.py` and starts from
     `main.py`.
- **Verify.** `uv run pytest tests/unit/test_user_tool_stewardship_schema.py tests/unit/test_tenant_coverage.py tests/unit/test_db_engine_seam.py -q -rs`

#### CBT-6b — Reuse

- **Files.** Changed: `user_tool_tools.py`, `run_code_tool.py`, the SBX-5
  persist step. New: `tests/unit/test_tool_reuse.py`.
- **Done when.**
  1. A second request with the same purpose finds the scratch run.
  2. The keep offer shows on the second run of a category, sets
     `offered_at`, and never shows twice for one SHA.
  3. "Keep" runs `build_tool` with the scratch gap id, and SBX-F8 passes.
  4. A close match asks "Add as version n+1".
  5. Removing a connection makes each tool that uses it read
     `connection_missing`.
  6. A stale tool makes one "needs you" item for its owner.
- **Verify.** `uv run pytest tests/unit/test_tool_reuse.py -q -rs`, plus `tests/unit/test_tool_gap_detection.py` (SBX-1, not built).

#### CBT-7 — The library

- **Files.** Changed: `workbench/control_plane/src/app/studio/page.tsx`
  (`readKind` and the `Kind` type), `components/studio/StudioPage.tsx`,
  `apps/services/gateway/gateway/routes/user_tools.py` (SBX-1 creates it),
  `packages/acb_skills/acb_skills/chat_parity.py` (rows, `APP_OF`,
  `TOOL_SOURCES`), `project-docs/specs/ai_studio.md` §13, the Studio nav
  manifest entry in `src/lib/nav.ts` (re-verify where the jobs live). New:
  `components/studio/ToolsList.tsx`, `components/studio/ToolDetail.tsx`, their
  tests, the chat tools of §12.1 marked CBT-7, the `user_tools` search
  source.
- **Done when.**
  1. `/studio?kind=tools` lists only the tools the member can see.
  2. The detail shows code, versions, a diff and the member's own runs.
  3. An approver sees the counts and outcomes of every run of the org, with
     no data and no code.
  4. Names and descriptions render as text, and a tool reads them framed.
  5. `test_chat_parity.py` passes with the new routes, and `DEBT_CEILING`
     does not grow.
  6. The `visual-review` skill passes in light mode, compact density, a
     changed accent and at phone width.
  7. The page renders no button whose chat tool or route is in a later
     slice.
- **Verify.** `uv run pytest tests/unit/test_chat_parity.py -q -rs`, plus `tests/unit/test_user_tool_registry.py` (SBX-1, not built). Then `cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/components/studio src/app/studio`.

#### CBT-8a — Propose, approve, pin, steward

- **Files.** Changed: `routes/user_tools.py`, the SBX-6 review module,
  `project-docs/specs/agent_sandbox.md` §8.2, the SBX-F13 test. New:
  `tests/unit/test_tool_stewardship.py`.
- **Done when.**
  1. A proposal by the steward or an approver lands a version and does not
     move `org_version`.
  2. A member who is neither cannot propose a version.
  3. Every member, the owner too, runs `org_version` of an organization tool.
     Only the proposer runs the proposed version.
  4. A second approver's approval moves `org_version`. With one approver, a
     self-approval moves it and the audit row says `self_approved = true`.
  5. `approve_tool_version` refuses in a marked session, and in the
     proposing thread on a later turn too.
  6. Pin moves `org_version` back, and no version row changes.
  7. Each act writes one `audit_event` row.
- **Verify.** `uv run pytest tests/unit/test_tool_stewardship.py -q -rs`, plus `tests/unit/test_user_tool_review.py`, `test_user_tool_audit.py` and `test_user_tool_versions_immutable.py` (SBX-6 and SBX-1, not built).

#### CBT-8b — Forks

- **Files.** Changed: `routes/user_tools.py`, `user_tool_tools.py`. New:
  `tests/unit/test_tool_forks.py`.
- **Done when.**
  1. A fork is a new personal tool with its lineage, and the original does
     not change.
  2. "Offer to the organization" opens a review of §9.4 step 2.
  3. `find_tool` returns the member's fork first, and labels both.
- **Verify.** `uv run pytest tests/unit/test_tool_forks.py -q -rs`

#### CBT-8c — Offboarding and Adopt

- **Files.** Changed: `apps/services/gateway/gateway/routes/admin/members.py`
  (the offboard step), `routes/user_tools.py`. New:
  `tests/unit/test_tool_offboarding.py`.
- **Done when.**
  1. A departed owner's personal tools read `disabled` with `owner_left`.
  2. A departed steward makes one "needs you" item for each approver.
  3. Adopt shows a card that names the departed owner, then moves the owner
     and keeps the tool private.
  4. Each act writes one `audit_event` row.
- **Verify.** `uv run pytest tests/unit/test_tool_offboarding.py tests/unit/test_admin_member_offboarding.py -q -rs`

#### CBT-9 — Organization tools in workflows

- **Files.** Changed: `apps/services/gateway/gateway/routes/studio/actions.py`
  (source 6), `apps/services/gateway/gateway/routes/workflows/tools.py`
  (`execute_tool`), `project-docs/specs/ai_studio.md` B5 (a pointer, after
  CBT-Q5). New: `tests/unit/test_studio_catalog_tools.py`.
- **Done when.**
  1. An approved organization tool shows as `tool:<slug>` with the right
     class, and its description is framed.
  2. A private tool never shows.
  3. A workflow step runs it at `org_version`, as the viewer.
  4. A run with no viewer runs a read tool only.
  5. The sandbox bridge still refuses `tool:<slug>` with `nested_tool`.
- **Verify.** `uv run pytest tests/unit/test_studio_catalog_tools.py tests/unit/test_sandbox_bridge.py -q -rs`

#### CBT-10 — Draft an API connection from its documentation

- **Files.** New: a builder tool next to the O12 importer (O12 names its
  module), `tests/unit/test_api_definition_draft.py`.
- **Done when.**
  1. Given an OpenAPI file or a documentation page, the tool drafts a
     definition with each operation's class (GET A, other verbs B, DELETE C).
  2. The draft holds no credential. A credential shape in the input is
     dropped.
  3. Only an admin can save the draft, in Integrations.
- **Verify.** `uv run pytest tests/unit/test_api_definition_draft.py -q -rs`

#### CBT-11 — Evals, failure copy and the guide

- **Files.** Changed: `evals/chat_built_tools/` (E2 to E5),
  `tests/unit/test_chat_built_tools_eval.py`. New:
  `tests/unit/test_tool_failure_copy.py`, and the member guide section in
  `docs/agent_sandbox.md` (SBX §9.3 creates the file. If it does not exist,
  CBT-11 creates it with this section only).
- **Done when.**
  1. The scripted runs of E2 to E5 pass, and E3 blocks 25 of 25 with a
     model scripted to comply.
  2. Each `error_class` in `user_tool_errors.py` has one row in the §12.2
     table, and the reverse.
  3. The guide says only what is built.
- **Verify.** `uv run pytest tests/unit/test_chat_built_tools_eval.py tests/unit/test_tool_failure_copy.py -q -rs`, then `uv run python -m evals.chat_built_tools.run --scripted`.

#### CBT-12 — API actions on the bridge

- **Files.** Changed: the bridge route of CBT-2, the O12 connection module,
  and `tests/unit/test_sandbox_bridge.py` (new O12 cases).
- **Done when.**
  1. An O12 GET action runs through the bridge with the org credential, and
     the container never holds it.
  2. A non-GET action holds for a card.
  3. A call goes through `outbound_guard.py`.
- **Verify.** `uv run pytest tests/unit/test_sandbox_bridge.py -q -rs`

#### CBT-13 — Turn on

- **Owner acts.** Add Fracktal to `RUN_CODE_ORGS` and the `tools:<org>`
  scope on production. The SBX-9 gates come first.
- **Done when.** A real scratch run on production writes its run row and its
  log line, and the evidence is on the task.

---

## 15. Owner questions

Each question has a recommendation and a default. A slice that needs an
answer builds to the default and stays dark. The default never ships to
production by itself.

| # | Question | Recommendation | Default until answered |
|---|---|---|---|
| **CBT-Q1** | Who may run scratch code in chat? | Every member with `feature:studio`, in an org on `RUN_CODE_ORGS`. An admin can take the org off the list | As recommended, dark |
| **CBT-Q2** | Does a scratch run that only reads Metorite data need a card first? | No. A card shows for a write, an outside source with shared data, or a web host. The Code row shows the code after | As recommended |
| **CBT-Q3** | May one step send Metorite data, or data from one source, to an org-connected MCP server or API? | Yes, after one card that names each source, its scopes and its cap. A web host stays closed in such a step (SBX-Q2) | No. Such a step is refused |
| **CBT-Q4** | Who approves an admin's new version of an organization tool? | A second holder of `tools:approve`. With only one holder, a self-approval with a flag in the audit | As recommended |
| **CBT-Q5** | Do member-authored logic modules (`ai_studio.md` B5) use this tool registry? | Yes, with B5's limits (no network, no database link) and a proposed-writes output file. One store of member code | No. B5 keeps its own plan |
| **CBT-Q6** | Do we keep the code of a scratch run? | Yes, for 30 days, in the tenant, readable by the member only | As recommended |
| **CBT-Q7** | Where is the list of tools? | In AI Studio's library, as a fourth kind, Tools | As recommended |
| **CBT-Q8** | May a scratch run install a package? | No. Only the image's packages. A new package means a saved tool with a lock | As recommended |
| **CBT-Q9** | Are the tools of a D98 connection (acts as the admin who connected it) class B when sandbox code calls them? | Yes. In code, a loop of reads as an admin needs a card | As recommended |
| **CBT-Q10** | May an approver read a member's scratch code, or adopt a departed member's private tool? | Scratch code: no, the member only. Adopt: yes, after a card that names the departed owner | As recommended |

---

## 16. Fences (R7)

Every fence is a new test, verified red first. The ones on tenancy, auth and
outward writes also get mutation testing (`engineering_practice.md` §4).

| # | Test file | What breaks it |
|---|---|---|
| CBT-F1 | `tests/unit/test_tool_handles.py` | A handle resolves for another member, thread or org. `resolve_handle` takes an org, a member or a thread from an argument. An untrusted `tr_` entry loses its mark. An `out_` path with `..`, an absolute path or a link opens. R8 half on a real database |
| CBT-F2 | `tests/unit/test_run_code.py` | `run_code` runs with no `find_tool` call in the run |
| CBT-F3 | `tests/unit/test_sandbox_bridge.py` | The door runs an action outside `uses`, a source 4, 5 or 6 action, an MCP tool that the shared gate refuses, a class B or C action with no card, a D98 action with no card or with a card that does not name the admin, or a 51st call to one server. The chat injection and the door call two different gate functions |
| CBT-F4 | `tests/unit/test_run_code.py` | `run_code` reaches an org off `RUN_CODE_ORGS` or a member without the feature. The daily cap does not stop a run, or its key skips the prefix wrapper. A scratch step writes no usage event |
| CBT-F5 | `tests/unit/test_run_code.py` | A credential shape reaches the model's summary or `user_tool_runs.code`, or a version with a credential shape persists |
| CBT-F6 | `tests/unit/test_tool_text_framing.py` (new) | A tool's name, description, `SKILL.md` or `tool.json`, or an MCP tool's description, reaches a model outside the untrusted frame, through `find_tool`, `list_tools`, `describe_tool`, a source 6 row or a `SKILL.md` load |
| CBT-F7 | `tests/unit/test_run_code_egress_card.py` | A step that reads Metorite data calls an outside source with no card, a step with two outside servers in `uses` runs with no card, data from one source goes to another with no card, a step that reads gets a `web:` host, or the card lists a source that is not in `uses` |
| CBT-F8 | `tests/unit/test_tool_failure_copy.py` | An `error_class` in `user_tool_errors.py` has no copy row, or a copy row has no `error_class` |
| CBT-F9 | `tests/unit/test_tool_stewardship.py` | A proposal moves `org_version`. A non-steward, non-approver proposes. The proposer approves while a second approver exists. An approval comes from a marked session or the proposing thread, on any turn. A member other than the proposer runs a version that is not `org_version`. A stewardship act writes no audit row |
| CBT-F10 | `tests/unit/test_user_tool_stewardship_schema.py` | The sweep clears or reads a row of another org, or leaves `code` on a row older than 30 days. R8 half on a real database |

---

## 17. Owner gates

An agent refuses each of these by name. The window of CLAUDE.md §3a does not
open the WS-43 or SBX gates, and it does not open these.

| # | Act |
|---|---|
| CBT-G1 | Add an org to `RUN_CODE_ORGS` on staging or production (it rides SBX-G1) |
| CBT-G2 | Approve a production organization tool for a customer org |
| CBT-G3 | Turn on source 6 (organization tools in workflows) on production |
| CBT-G4 | Type or store an API key for a customer's connection |
| CBT-G5 | Turn on the data-to-source card (CBT-Q3) on production |

---

## 18. Side findings

1. **Catalog source 3 is the critical path.** `ai_studio.md` B1b (MCP tools
   as actions) is not built. Both this spec and Studio's I2 need it. It
   should move up the Studio order.
2. **The executor of B2b-2 has no record.** `ai_studio.md` B2b-2 calls its
   executor choice a HIGH seam with no decision. CBT-2 step 0 needs that
   record, so it may be worth a decision of its own.
3. **The CTX-1 spill store is in process.** A handle resolves only in the
   process that holds the entry. CBT-1 measures this and names the transport.
4. **`run_script` and D129 disagree.** D129 withdraws `run_script` from
   personal agents. The code still keeps it
   (`test_shared_agent_shell_tools.py`,
   `test_a_personal_agent_keeps_its_shell_tools`). It needs its own task.

---

## 19. Changelog

- 2026-11-04 — Drafted from the owner's brief (task 1655). Planning only.
  Nothing built.
- 2026-11-04 — Spec audit round 1: NO-GO, 15 findings. Fixed: the header
  date and non-goals, the §4 registry row and the cross-spec edits, the eval
  pattern and the anchors, the B2 executor as CBT-2 step 0, the card path,
  CBT-1 narrowed, the scratch `reads` argument and the static read fact, no
  nested tools, the per-server cap and cross-source flow, the D98 rule
  (CBT-Q9), framing on every path, approver privacy (CBT-Q10), the
  two-person rule and the risky-run refusal, the org-tool version rule, B5
  left to CBT-Q5, CBT-8 split in three, parity rows moved into CBT-7, the
  failure rows, the sweep job, and slice CBT-12 for API actions.
- 2026-11-04 — Spec audit round 2: GO-NARROWED, 8 findings. Fixed: the
  card path against B2c-2, two outside servers count as a flow, buttons ship
  with their tools, the approval refusal covers the proposing thread, the
  CBT-4 and CBT-11 dependencies, the sweep's seam registration and start
  hook, the `RUN_CODE_` env prefix, one card for each D98 call, and the
  task numbers 1658 to 1674.
