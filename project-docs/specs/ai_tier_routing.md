# AI tier routing — the platform picks the tier for each step

**Status:** DECIDED and ACTIVE. The owner accepted D90 on 2026-10-06, as
written. Specified 2026-10-05. Board row **WS-45**, and decision **D90** is in
`work_plan.md` §3.

**S1 is built and dark** (2026-10-06, PR #667).
**S2 is built and dark** (2026-10-06, PR #675).
**S3 is built and dark** (2026-10-06, branch `ws45-s3-picker`).
`AI_TIER_ROUTING` ships empty, and `NEXT_PUBLIC_AI_TIER_ROUTING` ships OFF.
Neither flag covers an agent on any box. S4 is next.

**S3 build notes (2026-10-06).** Read these before S4.

- `src/lib/tierRouting.ts` holds every rule of S3 as a pure function. The
  composer, the hook and the answer only call it.
- **The client learns coverage from the agent list.** `GET /agent` sets
  `tier_routed` on each entry with `_stamp_tier_routing`, which asks
  `tier_policy.tier_routing_on`. No file in the Control Plane names a covered
  agent. An entry with no field reads as not covered.
- A covered agent needs BOTH flags. With the UI flag on and no agent covered,
  every picker draws as today. So the wrong order of the two flips is safe.
- For a covered agent, the composer draws no picker and no divider. It
  fetches no `/api/models/all` and reads no `cc-model-` key. It sends no
  `model` field, and it takes the executor path for the orchestrator too. The
  effort selector keeps Auto, Thinking and Max (Q3).
- The composer deletes `cc-model-<agent>` for a covered agent. It deletes
  `cc-model-usage` only when the flag covers every agent in the list. The
  counts still sort the picker of an agent that the flag does not cover.
- With the UI flag on, the composer draws no picker until the agent list
  lands. A turn sent before then sends `model` with the forced model or
  `auto`, never the stored choice. The composer reads no stored choice until
  it knows that the agent keeps its picker. Review P3 of PR #678 found that
  this note claimed otherwise.
- The context ring of a covered agent names no model, and it estimates on
  the `auto` window.
- **The tier label** is in a new details menu on each answer,
  `components/AnswerDetails.tsx`, for every member (Q4). It names each tier
  once, in first-use order, for example "Balanced, then Powerful". An
  off-ladder slug draws nothing, so no model can show (D32.7).
- ⚠️ **The words are a fenced mirror.** The Control Plane does not read
  `GET /my/tiers` yet (`ai_metering_and_analytics.md` §8.4 clause 6). So
  `TIER_WORDS` copies the seed rows of `015_tier_pricing.sql`, and
  `tierRouting.test.ts` reads that file and fails on a drift. An operator
  edit of a label does not reach the chat until that read ships.
- `ai.route` never draws in the raw "Interactive view" fold, with the flag on
  or off. This is the one change that does not wait for the UI flag. Only a
  covered run emits the event, and no box covers an agent.
- The spec named the fence `AgentChat.picker.test.tsx`. Vitest here collects
  `*.test.ts` only, so the file is `AgentChat.picker.test.ts`.
- With both flags off, the composer and the answer draw the same markup as
  `origin/main`. A one-time check compared 7 composer cases and 3 answer
  cases byte for byte. The committed fence holds the flag-off markup equal
  across coverage values.
- **Not built in S3:** the chat of the Email app still passes `model` and
  `lockModel`, and the two `chat_model` controls stay (S4). The `route.ts` field
  `model` stays (§8, contract).

**S2 build notes (2026-10-06).** Read these before S3 or S4.

- `acb_skills/tier_policy.py` holds the table (`KIND_TIERS`), `TOOL_HINTS`,
  the effort mapping (`choose`), the turn-kind question (`turn_kind`) and
  `TierPolicyMiddleware`. The executor holds only the glue
  (`_tier_policy_for_run`, `_with_tier_policy`).
- `TOOL_HINTS` holds real names: `run_command`, `code_task` and `run_script`
  (code), `propose_plan` and `rebalance` (plan), and `task_dataset`,
  `analytics_outlook` and `find_conflicts` (analysis). The §4.4 names
  `plan_with_capacity`, `analyse` and `forecast` are not tools.
- A function middleware records each tool call. The chat middleware reads the
  calls once, at the next request, so a hint raises that request only.
- **Max sends no turn-kind question.** Max puts every main request on
  `tier-powerful`, so the answer could change nothing.
- **Thinking adds nothing to the tier today.** Every hint kind maps to
  `tier-powerful`, so "one rung up" gives the same tier as the hint. The rule
  is built and a test holds it. Thinking still sets `reasoning_effort` to
  `medium` and the System-1 threshold to 0.80.
- The turn-kind question imports its modules before its timer starts. The
  client timeout is 1.5 seconds too. A cold import made the first question
  time out.
- A covered agent's default is its build-time client model, for example
  `PROJECTS_AGENT_MODEL`. `copilot_chat_model` does not count (§8).
- `ai_route.turn_kind` logs the kind, the source and `latency_ms`, so the
  median that Q8 asks for can be read on a box. S2 did not measure it live.
- With the flag unset, a dump of three projects-assistant runs (six requests
  and every event) was byte-identical on main and on the branch.
- **Not built in S2:** a sub-agent runs no policy of its own (§4.5, last
  bullet). The batch path (`run_agent`) and the sub-agent path attach no
  policy. S4 owns both.
- **Until S4, a covered run publishes the agent's DEFAULT tier** through
  `_active_run_model`, never the turn's tier. So a `call_agent` fan-out from a
  code turn does not put every request of every sub-agent on
  `tier-powerful`. Review P2 of PR #675 found this.
- **An `ai.route` event is not output.** The Tier 1 path holds the event
  until the step gives output. So a Tier 1 fault before any output still
  falls back to Tier 2. On the fallback the held events go, and the policy
  counts its requests from 1 again. Review P1 of PR #675 found this.
- **An off-ladder default stays.** A `tier-code` or `provider/model` default
  cannot be compared with a rung, so no kind, hint or effort moves it
  (§4.2 rule 2).
- **Q6 is not answered.** The box reads `ROUTER_SERVING_ENABLED=true` and
  `DECIDE_ENABLED=true`, and `AI_TIER_ROUTING` is not set. The live
  `tier_binding` rows were not read. `GET /catalog/models` needs an operator
  credential. The only one on the box is the shared token, and every
  break-glass use is an owner gate (WS-31 row).

**S1 build notes (2026-10-06).** Read these before S2.

- The System-1 callable sets `__tool_risk__` itself and does NOT call
  `annotate()`. `annotate` writes `TOOL_ANNOTATIONS["decide"]` by name, and
  that entry is the Jev engine's. §6.6 now says so.
- §2.7 is stale. The core floor measured **8997 of 9000** on 2026-10-06, not
  8925. So the System-1 description was cut until its schema cost 181 tokens,
  the same as the Jev schema.
- The 3 s limit is the client's own request timeout, with no retry. A
  `wait_for` around the whole call also counted the first imports of a cold
  process.
- `kind` keeps today's default, `yes_no`, as §6.3 says "does not break it".
- The tool makes no request when `acb_llm.routed.routing_is_on()` is false.
  So no System-1 call reaches a vendor directly (owner answer Q1).
- The addendum line of §6.8 is NOT built. The injection chain caches the
  addendum per scope, not per agent, and the tool description carries the
  batch rule. With
  `DECIDE_ENABLED` off, a covered agent's prompt does not name `decide`.
- `test_no_direct_ai_vendor_calls.py` (§10, §12) is NOT built in S1.
- ⚠️ **For S2 or S4: the skills catalog shows the Jev schema.**
  `gateway/routes/integrations_skills.py:49` calls
  `_collect_injectable_platform_tools()` with no agent name. So for an agent
  that the flag covers, the Skills tab shows the Jev `decide` schema, and it
  measures the Jev cost. The agent's run holds the System-1 schema. Review
  found this on 2026-10-06. S1 does not change the catalog.
- The reason filter drops a reason with `//`, `www.`, a backtick, a known
  scheme such as `mailto:` or `javascript:`, any other scheme with no space
  after the colon, or a bare domain with a path. Review P3 widened it.

**Verified against code on 2026-10-05** at `origin/main` `28cfcf437`.
**Owner:** vjvarada.

**Single owner.** This spec owns one question: which Router tier serves each
model request that an agent makes. It owns the System-1 tool and the tier
policy of the executor. It also owns the removal of the chat's model picker,
and how the effort selector maps to tiers.

Other specs keep what they own. `customer_console.md` keeps the Router, the
tier catalog, the bindings and the `decide` task (§6A.14).
`ai_metering_and_analytics.md` keeps the tier vocabulary a person sees and
every usage surface. `maf_coding_engine.md` keeps the sandbox and the egress
rule (§16.3). `projects_ai_chat.md` keeps what the Projects chat does.

---

## 0. The answer in one screen

1. **The chat's model picker goes.** The composer stops offering
   "LiteLLM — Tiers". A member never picks a tier again.
2. **The effort selector stays.** It reads Auto, Thinking or Max. It now nudges
   the tier policy, and it still sets the reasoning effort.
3. **The platform picks the tier for each step, by the KIND of work.** A
   decision goes to `tier-fast`. Ordinary chat and tool use go to
   `tier-balanced`. Code, a plan of many steps and hard analysis go to
   `tier-powerful`.
4. **Every AI call goes through our Router.** No agent and no tool calls an
   outside AI vendor directly. A fence holds a baseline of the direct calls
   that exist today, and the baseline only goes down.
5. **The System-1 tool is an MAF agent wrapped as a function tool.** It runs
   on `tier-fast`, holds no tools, and returns one fixed shape: a choice, a
   confidence and a short reason. Low confidence hands the decision back to
   the main model. It takes a batch of questions in one call.
6. **The System-1 tool is not an egress tool.** It says `open_world=False`,
   because its only destination is our Router, which the run's own model
   requests already use. So a covered (sandboxed) run may hold it.
7. **It applies to every agent**, Projects first and Email second (D86). One
   flag, default OFF, names the agents it covers.

---

## 1. Scope and non-goals

### 1.1 In scope

- The tier of every model request that a chat agent makes on the platform:
  projects-assistant, email-assistant, crm-assistant, whatsapp-assistant,
  task-manager, apis-config, app-builder and the orchestrator.
- The System-1 tool, its engine, its schema, its egress class and its meter.
- The tier policy in the executor, per run and per model request.
- The chat composer: the picker leaves, the effort selector stays, and a tier
  label may show on each answer.
- The two per-account "chat model" settings that act as a second picker: the
  email assistant's `chat_model` and the Tasks `chat_model`.
- An inventory and a ratchet fence for the AI calls that skip the Router.
- The plan for modality tiers (image, speech, transcription), as slice S5.

### 1.2 Non-goals — named so nobody builds them from this file

- **No Router change.** The Router does not infer a tier from the payload.
  D61.3 stands: the caller declares the tier. The caller here is the executor.
- **No new tier and no new binding.** The operator keeps owning which model
  serves each tier (D58.4, D-AI-5).
- **No price change.** D67 keys the price on the tier. The meter still rates
  the tier that served the call.
- **The email triage features stay on `tier-decide`.** D-EM-7 and D-EM-8 run
  `email.rule_match` and three more features on Jev. This spec does not move
  them. The owner answered §13 Q1 on 2026-10-06: they stay.
- **Background jobs outside a chat keep their literal tiers.** The notes
  summary, the email digest and similar jobs choose a tier at the call site.
  H-44 owns that sweep (D59.4).
- **No customer-facing tier choice.** D90 withdraws it for the chat.
- **No new database table.** The tier policy needs no migration. §8 shows why.

---

## 2. What exists today — measured 2026-10-05

### 2.1 The model picker

| Fact | Anchor |
|---|---|
| The picker is a block inside the composer, not its own file | `workbench/control_plane/src/components/AgentChat.tsx:2077-2106`, inside `{!lockModel && (` |
| A static list shows while the fetch loads. It names `tier-fast`, `tier-balanced` and `tier-powerful` in the group "LiteLLM — Tiers" | `AgentChat.tsx:50-57` |
| The options come from `GET /api/models/all` | `AgentChat.tsx:292-302` |
| That route serves the wire ids `tier1-local-qwen3`, `tier2-sonnet` and `tier3-opus` | `src/app/api/models/all/route.ts:77-119`, the tier rows at `:79-81` |
| PR #483 drops user-enabled raw models while the Router serves | `route.ts:339`, `apps/services/gateway/gateway/routes/settings.py:618`, `:680-695` |
| The button shows the raw stored id when the served list does not hold it. That is how "tier-powerful" appears | `AgentChat.tsx:1330-1331` |
| The choice lives in React state and in `localStorage` key `cc-model-<agent>`, per agent and not per session | `AgentChat.tsx:120-131`, `:267-269`, `:308-317` |
| A "Frequently Used" sort reads `localStorage` key `cc-model-usage` | `AgentChat.tsx:121`, `:320-338` |
| The email chat locks the picker and forces its own `chat_model` | `AgentChat.tsx:215-227`, `src/app/chat/page.tsx:1093-1098` |

### 2.2 How the choice travels, and where it stops working

1. The hook sends `model` and `thinkMode` in the body of
   `POST /api/agent/chat` (`src/hooks/useAgentChat.ts:252-271`, `:602-604`).
2. For a named agent the proxy forwards `model` at the top level and
   `think_mode` inside `payload` to `POST /agent/run/stream`
   (`src/app/api/agent/chat/route.ts:667-697`).
3. The gateway takes `AgentRunRequest.model` (`routes/agent.py:58-80`) and
   passes it to `run_agent_stream` (`:2354-2364`).
4. The executor picks the model in this order: the request, then
   `copilot_chat_model`, then the agent file, then `model_tier`
   (`apps/services/orchestrator/orchestrator/executor.py:3617-3637`).
5. `_byok_default_model` coerces every id that is not a gateway alias to
   `tier-balanced` (`orchestrator/_model_resolution.py:35-66`).
   `tests/unit/test_byok_default.py:28-38` pins that.
6. `_apply_model_for_maf_agent` writes the result into the agent's
   `default_options["model"]` for the whole run (`_model_resolution.py:186-209`,
   called at `executor.py:3684-3687`).

🔴 **So the picker is already inert for a named agent.** The served wire ids
`tier1-local-qwen3` and `tier3-opus` are not gateway aliases. Step 5 coerces
both to `tier-balanced`. Only the `/v1` Router hop maps a wire id to its slate
name (`routed.py:185-217`, `v1_compat.py:496-515`). A named agent does not take
that hop with the wire id. A member who picks "Tier 3 (powerful)" gets
Balanced and is not told.

No table stores a model per chat session. `chat_session` has no model column
(`infra/postgres/02_chat_history.sql:6-16`).

### 2.3 The effort selector

| Fact | Anchor |
|---|---|
| Three modes: Auto, Thinking, Max. React state only, not saved | `AgentChat.tsx:285-286`, `:1372-1376`, `:2110-2132` |
| The field is `thinkMode`, then `payload.think_mode` | `useAgentChat.ts:264`, `route.ts:682` |
| The gateway normalises it to `auto`, `thinking` or `max` | `routes/agent.py:84-107` |
| A native MAF agent gets `reasoning_effort` only: nothing, `medium`, `high` | `_model_resolution.py:145-159`, fixed by PR #585 |
| A Copilot SDK agent gets `reasoning_effort` and a thinking budget of 4000 or 16000 tokens | `_model_resolution.py:109-138` |
| The Copilot stream path sends `low` for Auto, not nothing | `executor.py:4251-4268` |
| The selector never changes the model | `executor.py:3703-3714` |

### 2.4 The agents and their default tier

Every agent defaults to `tier-balanced`. That default is D-AI-4's "an app
declares a default tier".

| Agent | Runtime | Default | Anchor |
|---|---|---|---|
| projects-assistant | native MAF | `PROJECTS_AGENT_MODEL`, else `tier-balanced` | `apps/agents/agent-projects/agents.py:93` |
| email-assistant | native MAF | `EMAIL_AGENT_MODEL`, else `tier-balanced` | `apps/agents/agent-email-assistant/agents.py:2387` |
| crm-assistant | native MAF | `CRM_AGENT_MODEL`, else `tier-balanced` | `apps/agents/agent-crm/agents.py:1013` |
| whatsapp-assistant | native MAF | `WHATSAPP_AGENT_MODEL`, else `tier-balanced` | `apps/agents/agent-whatsapp-assistant/agents.py:516` |
| apis-config | native MAF | `MODEL = "tier-balanced"` | `apps/agents/agent-apis-config/agents.py:54` |
| orchestrator | native MAF | `tier-balanced` | `apps/services/orchestrator/orchestrator/agents.py:468` |
| task-manager | Copilot SDK | `tier-balanced` | `apps/agents/agent-task-manager/agents.py:135` |
| app-builder | Copilot SDK | `tier-balanced` | `apps/agents/agent-app-builder/agents.py:54` |

The registry of record is `_AGENT_REGISTRY` (`routes/agent.py:419-611`). A
sub-agent inherits its parent's tier through the `_active_run_model`
ContextVar (`orchestrator/agents.py:287-289`).

### 2.5 The Router tiers

- `tier_catalog` holds 11 tiers (`infra/customer_console/015_tier_pricing.sql:46-58`).
  Migration `033_decide_task.sql:38-47` adds the hidden `tier-decide`.
- The chat bands are `tier-fast`, `tier-balanced`, `tier-powerful` and
  `tier-code`.
- The seed binds `tier-fast` to `deepseek/deepseek-chat`, and both
  `tier-balanced` and `tier-powerful` to `deepseek/deepseek-v4-pro`
  (`002_seed_catalog.sql:88-92`).
- ⚠️ **If production keeps that seed, Balanced and Powerful are the same
  model.** Then escalation to Powerful costs the Powerful rate and changes
  nothing. The live binding is not in the repo. S2 reads it (§13 Q6).
- `infra/litellm/tier_overrides.yaml:1-13` disagrees with the seed. D58.4
  already retires it.
- The Router resolves a tier to an ordered chain (`customer_console/router.py:120`,
  `:173`). It serves chat, transcription, image, speech and `decide` doors
  (`customer_console/main.py:7595`, `:8030`, `:8488`, `:8606`, `:8761`).

### 2.6 Metering

- The Router writes one `usage_event` row for each call
  (`customer_console/store.py:939-986`). The row holds `agent`, `module_slug`,
  `model`, `tier`, `task`, `run_id` and `client_ref`.
- The member, agent, module and run come from the `X-CC-*` headers
  (`acb_llm/routed.py:138-178`). `attributed_openai` adds them to every agent
  request from the run context (`apps/agents/agent-projects/agents.py:95-101`).
- So a System-1 call is metered with no new code on the Router side. It is one
  more routed request at the `tier-fast` rate.

### 2.7 The `decide` tool and `acb_llm.decide`

- `acb_skills/decide_tools.py:142` is a core-floor tool for every MAF agent.
  The injection chain adds it only when `DECIDE_ENABLED` is true
  (`orchestrator/_tool_injection.py:1106-1116`).
- It calls `acb_llm.decide` (`packages/acb_llm/acb_llm/decide.py:337`). That
  facade POSTs the Router's `/v1/decide` on the hidden tier `tier-decide`
  (`decide.py:57`, `acb_auth/console_resolve.py:2506`).
- The Router serves `tier-decide` with Jev, through the reseller AI/ML API
  (`customer_console/handlers.py:100-102`, D75 clause 8).
- `TOOL_ANNOTATIONS["decide"]` says `open_world=True`, because a third-party
  model receives the content (`acb_skills/tool_annotations.py:86-88`).
- `DECIDE_ENABLED` is ON in production since 2026-10-02, by owner report
  (`HANDOFF.md` H-166).
- The tool sends NO member, on purpose (the R11 finding,
  `customer_console.md` §6A.14 CP-13d).
- The core floor schema measured 8925 of 9000 tokens on 2026-09-24
  (`customer_console.md` §6A.14). A wider `decide` schema has about 75 tokens
  of room.

### 2.8 Per-step tier switching that exists today

- **None in a chat run.** The executor sets one model for the whole run.
- `orchestrator/router.py:11-18` holds `pick_tier`, a keyword heuristic. No
  production code calls it.
- Many features pick a tier per call outside the chat. Examples are
  `routes/notes/summaries.py:36-40`, `routes/email/automation/assistant.py:150-154`
  and `routes/tasks/settings.py:43-48`. H-44 owns those.

### 2.9 MAF

- `agent-framework-core` is 1.19.0 (`uv.lock:353-354`).
- `Agent.as_tool(name, description, arg_name="task", ...)` exists and returns
  a `FunctionTool`. Its input schema is ONE string. It returns the agent's
  text.
- No code calls `as_tool` today. The orchestrator wraps specialist agents in
  its own closures (`orchestrator/agents.py:334-437`).
- `ChatMiddleware` exists, and `acb_skills/tool_guard.py:45` already uses it
  per run. A chat middleware sees each model request's `options`.

### 2.10 AI calls that skip the Router

`ROUTER_SERVING_ENABLED` turns on the gateway's Router hop
(`v1_compat.py:482`) and the in-process seam `acb_llm/routed.py` (H-171). PR
#483's message says production runs with the hop on. `HANDOFF.md` H-69 still
lists the flip as pending, so S2 reads the box at dispatch.

These paths reach a vendor without the Router, whatever the flag says:

| Path | Anchor | Kind |
|---|---|---|
| The `/v1/embeddings` door calls OpenAI with `OPENAI_API_KEY` | `apps/services/gateway/gateway/main.py:1716-1719` | embed |
| Three embedding writers call `litellm.aembedding` | `email_ingestion/email_embeddings.py:85-100`, `whatsapp_ingestion/wa_embeddings.py:65-75`, `gateway/routes/tasks/capability.py:87-100` | embed |
| Transcription through `acb_stt` | `packages/acb_stt/acb_stt/litellm_provider.py:196`, `assemblyai_provider.py:57` | transcribe |
| Live notes mint Deepgram keys | `gateway/routes/notes/live.py:38` | transcribe |
| An integration check calls four vendor models | `gateway/routes/integrations.py:1384-1398` | chat |
| The prompt-cache warm-up | `gateway/main.py:500` | chat |
| The streamed text helper | `acb_llm/context.py`, `acompletion_stream_text` (H-171) | chat |

The key tests and model lists in `gateway/routes/settings.py` reach vendor
hosts too. They move no tenant content, so they are not AI work, and this spec
leaves them.

---

## 3. Decision D90 (decided: owner, 2026-10-06)

**D90 — The platform picks the tier for each step, by the kind of work. The
chat's model picker goes, and the effort selector stays.** Owner direction,
2026-10-05. Board **WS-45**. Owning spec: this file.

### 3.1 The clauses

1. **The model picker leaves the chat.** No member picks a tier in any chat
   composer. The effort selector stays.
2. **The platform picks the tier for each model request, by the kind of
   work.** A decision goes to `tier-fast`. Ordinary chat and tool use go to
   `tier-balanced`. Code, a plan of many steps and hard analysis go to
   `tier-powerful`. §4 is the policy of record.
3. **Every AI call goes through our Router tiers.** No agent and no tool calls
   an outside AI vendor directly. The System-1 tool uses OUR `tier-fast`, not
   the `decide` task's vendor.
4. **An agent calls System 1 as a tool.** It is an MAF agent wrapped as a
   function tool. It runs on `tier-fast`, holds no tools, and answers in one
   fixed shape. Low confidence hands the decision back to the main model. It
   takes a batch. It says `open_world=False`, so a covered run may hold it.
5. **It binds every agent on the platform**, through one flag, default OFF.
6. **Modality tiers come later the same way.** A tool declares the task, the
   Router serves the tier, and nothing calls a vendor directly.

### 3.2 How D90 meets each earlier decision

| Decision | What it says | What D90 does to it |
|---|---|---|
| **D75.6** | The main chat's `decide` tool calls the Router's `decide` task, served by Jev | **Amended.** The tool keeps its name and its place in the core floor. Its engine becomes the System-1 agent on `tier-fast`. The `decide` task and `tier-decide` stay for the email features |
| **D75.1 to D75.5, D75.7, D75.8** | `decide` is a task, its hidden tier, its wire shape, its vendor, its adoption rules | **Unchanged.** They govern the email triage features, which keep Jev (§13 Q1) |
| **D59.5** | A customer MAY see tiers | **Narrowed, not reversed.** A tier label may still show (§7.2). A customer may not PICK a chat tier |
| **D59.6 (4)** | A customer-visible tier choice in chat comes last | **Withdrawn.** It will not come |
| **D67.1** | "The meter rates the tier the customer picked" | **Wording amended.** The meter rates the tier that served the call, which the platform picked. The key `(tier, task)` does not change |
| **D-AI-3** (`ai_metering_and_analytics.md` §3.3) | `customer_visible` TRUE means "the customer picks this chat band" | **Meaning amended.** TRUE now means the label may show to a customer. Nobody picks it |
| **D-AI-4** (same spec, §3.4) | An app declares a default tier. An admin may change it. A member may not | **Kept.** The default is the Balanced rung of the policy. An admin change moves that rung |
| **D32.7, D66** | A customer never sees a model | **Unchanged, and reinforced.** The label is a tier label, never a model |
| **D61.3** | The caller declares the task. The Router never reads the payload | **Unchanged.** The executor is the caller. It declares the tier. The Router does not decide |
| **D57.7** | A routed call that fails, fails | **Unchanged.** A System-1 failure returns fixed text, and the main model decides. No call falls back to a direct vendor |
| **D85, H-236** | A covered run holds only tools that say `open_world=False` | **Applied.** §6.6 gives the exact class |
| **D84** | The Copilot SDK leaves the platform | **Composes.** A Copilot agent cannot change tier per request. It gets the run-level tier until its port |
| **D86** | Projects first, then Email | **Followed.** S1 is Projects, S4 is the rest |
| **D88** | The command bar's AI tier runs at `tier-fast` | **Consistent.** An intent read is a decision |

### 3.3 What D90 does NOT decide

- Which model serves each tier. The operator binds it.
- The owner answered §13.2 Q1 to Q8 with their defaults on 2026-10-06. The
  email triage features stay on Jev, and the tier label shows in the
  message details.
- The price of a tier (H-42).

---

## 4. The routing policy

### 4.1 The table of record

`acb_skills/tier_policy.py` (new in S2) holds this table as data. Nothing else
holds a second copy.

| Kind of work | Examples | Tier | Who decides |
|---|---|---|---|
| **Decide**: classify, sort, select, route, yes or no, score | Which project fits. Is this urgent. Which agent takes this. Which rule matches | `tier-fast` | **The System-1 tool.** Its tier is fixed. No setting moves it |
| **Turn kind** | What kind of work is this turn | `tier-fast` | **The executor**, through the System-1 agent, once at the start of a turn (§4.3) |
| **Ordinary chat and tool use** | Answer a question, call a read tool, summarise what a tool returned, fill a confirmation card | `tier-balanced` (the agent's default, D-AI-4) | **The executor.** This is the default when no other row applies |
| **Code** | Write or fix code, read the output of `run_command` | `tier-powerful` | **The executor**, from the turn kind or from a tool hint (§4.4) |
| **Plan of many steps** | Plan with capacity, rebalance a team, draft a project plan | `tier-powerful` | **The executor**, from the turn kind or from a tool hint |
| **Hard analysis** | On-the-fly analysis, a forecast, a conflict read with many parts | `tier-powerful` | **The executor**, from the turn kind or from a tool hint |
| **System 1 is unsure** | A confidence below the threshold | The main model's own tier | **The calling agent.** The tool says "unsure", and the main model decides |
| **The member asks for more effort** | Thinking or Max | §5 | **The effort selector** |
| **Read an image** | A screenshot in the chat | `tier-vision`, or the chat model when it reads images | **The Router**, from the declared task (D-AI-2). Unchanged |
| **Make an image, speak, transcribe, embed** | Later, S5 | `tier-image`, `tier-tts`, `tier-stt`, `tier-embed` | **The tool**, which declares the task (D61.3) |

### 4.2 Three rules bound the policy

1. **The executor is the only place that picks a chat tier.** An agent file
   declares its default. A tool declares a hint. Neither picks a tier itself.
2. **The policy never moves a request DOWN below the agent's default,** except
   for a System-1 call. A turn classified as "chat" stays on the default. This
   keeps an admin's D-AI-4 choice in force.
3. **A request names a tier slug, never a model** (D32.7). The policy only
   chooses among `tier-fast`, `tier-balanced` and `tier-powerful`. Code goes to
   `tier-powerful`, not to `tier-code` (§13 Q2).

### 4.3 The turn kind

At the start of each turn, the executor asks the System-1 agent one question.
The question is the kind of the member's message: `chat`, `code`, `plan` or
`analysis`.

- It is one `tier-fast` request, with the last member message and the names
  of the tools the agent holds. It sends no earlier history.
- A confidence below the threshold reads as `chat`, so the turn stays on the
  default.
- The executor waits at most 1.5 s. A timeout reads as `chat`.
- The answer sets the tier of every main model request in that turn, unless a
  tool hint raises it.
- A turn with one short message under 12 words skips the question and reads as
  `chat`. This saves the call on "thanks" and "yes".

### 4.4 Tool hints

`tier_policy.TOOL_HINTS` maps a tool name to a kind. Examples:

| Tool | Kind |
|---|---|
| `run_command`, `code_task` | `code` |
| `plan_with_capacity`, `rebalance` | `plan` |
| `analyse`, `forecast` | `analysis` |

S2 takes the real names from the tool registries at build time. A fence
fails on a name that no registry holds.

**How a hint acts.** After the model calls a hinted tool, the NEXT model
request in the same turn goes to the hint's tier. That request reads the tool's
output and writes the answer, so it is the step that needs the stronger model.

### 4.5 How the executor switches the tier per request

- A new chat middleware, `TierPolicyMiddleware`, lives in
  `acb_skills/tier_policy.py`. It sets `options["model"]` on each model
  request of ONE run.
- The executor attaches it per run, the same way it attaches
  `EgressGuardProvider` (`acb_skills/egress.py:301-314`). It never touches a
  shared agent object.
- It reads the turn kind and the last tool call from run state.
- It logs one line for each request: `ai_route.chosen`, with the agent, the
  run, the tier, the kind and the reason (`default`, `turn_kind`, `tool_hint`
  or `effort`). The line holds no tenant text.
- **A Copilot SDK agent cannot switch per request.** task-manager and
  app-builder get the turn-kind tier for the whole run, set once at
  `_apply_model_for_maf_agent`. D84 ports them later.
- **A sub-agent runs its own policy.** It no longer inherits the parent's tier
  through `_active_run_model` while the flag covers it.

---

## 5. The effort selector

The selector keeps its three labels. Each label now does two things: it
nudges the policy, and it sets the reasoning effort as today.

| Selector | Main requests | `reasoning_effort` (native MAF) | System-1 threshold | System-1 tier |
|---|---|---|---|---|
| **Auto** | The policy, §4 | not sent (as today) | 0.70 | `tier-fast` |
| **Thinking** | The policy, and a `chat` turn with a tool hint of any kind goes one rung up | `medium` (as today) | 0.80 | `tier-fast` |
| **Max** | Every main request goes to `tier-powerful` | `high` (as today) | 0.90 | `tier-fast` |

- **A decision stays on `tier-fast` in every mode.** The owner put System 1 on
  the fast tier. A higher threshold makes the tool hand more decisions back to
  the main model, and that is how effort reaches a decision.
- **Max costs more credits.** Every main request bills at the Powerful rate.
  The selector shows no price (D88's rule for the bar applies here too).
- **The Copilot stream path sends `low` for Auto** (`executor.py:4251-4268`).
  S2 keeps it, because a Copilot agent needs it to stream its reasoning.

**Answered (§13 Q3, owner 2026-10-06).** The selector gets no fourth label,
"Fast". It keeps three labels.

---

## 6. The System-1 tool

### 6.1 Name and place

- **The tool keeps the name `decide`.** It is already in the core floor, every
  MAF agent holds it, and the prompt addendum already teaches it ("Fast
  decisions"). A second tool would be a second seam (CLAUDE.md §4). It would
  also break the schema ceiling, which has about 75 tokens of room (§2.7).
- **The engine changes.** For an agent the flag covers, the injection chain
  gives the agent the System-1 `decide`. For every other agent it gives the
  Jev `decide` when `DECIDE_ENABLED` is on, as today.
- **The agent inside is `system-one`.** It is an MAF `Agent` with no tools,
  fixed instructions, and a client on `tier-fast`. It is built in
  `acb_skills/system_one.py`. It is not in `_AGENT_REGISTRY`, so no member
  can chat with it and the orchestrator cannot delegate to it.

### 6.2 Why a typed wrapper, and not `Agent.as_tool()` as it stands

`Agent.as_tool()` gives the tool ONE string input (§2.9). The System-1 tool
needs typed options and a batch, and its answer must be checked against the
options. So S1 wraps the agent in an `acb_skills` function that calls
`system_one.run(...)` with a `response_format`. It is still an agent wrapped
as a function tool. It also gives the platform its own callable, which the
egress rule needs (§6.6).

### 6.3 The schema

The calling model sees this signature. It widens today's signature, and it
does not break it.

```text
decide(question, context, kind="choice", options="", items="")
```

- `question`, `context`, `kind` and `options` keep their meaning
  (`decide_tools.py:142-153`). `kind` is `yes_no`, `choice` or `score`.
- `items` is new. It is a JSON list of up to 20 objects, each with an `id`, a
  `question`, a `kind` and `options`. When `items` is set, the tool ignores
  `question`, `kind` and `options`, and it asks every item in ONE request.

The System-1 agent answers in this fixed shape, through `response_format`
with a JSON schema:

```text
{"answers": [{"id": str, "choice": str, "confidence": number 0..1, "reason": str}]}
```

- `choice` must be one of the item's options. For `yes_no` the options are
  `yes` and `no`.
- `reason` holds at most 120 characters.

### 6.4 What the calling model reads

One short line per item. The format keeps today's shape and adds the reason.

```text
beta (confidence 0.81) — the task names the beta launch date
unsure (confidence 0.42) — decide this yourself
```

**Escalation.** A confidence below the threshold (§5) gives `unsure`. The main
model then decides on its own tier. The tool makes no second call. This is
what "low confidence escalates to the main model" means.

**Failure.** A Router refusal, a timeout of 3 s or an answer that fails
validation gives the fixed text `UNAVAILABLE` (`decide_tools.py:40`). The
agent loop never sees an exception. D57.7 holds: nothing falls back to a
direct call.

### 6.5 Metering

- Each System-1 request is one routed request on `tier-fast`. The Router
  writes one `usage_event` row at the `tier-fast` rate (D67).
- The request carries `X-CC-Agent` of the CALLING agent and
  `X-CC-Source: system_one`. `attributed_openai` adds the member, app and run
  from the run context, as it does for the run's own requests.
- A batch of 20 items is one row, not 20.
- The turn-kind question (§4.3) is metered the same way, with
  `X-CC-Source: system_one`.
- **The R11 finding.** Today's Jev tool sends no member, because a run's member
  can come from request input. The System-1 request carries exactly the
  attribution that the run's own model requests already carry. So it adds no
  new trust in request input. It inherits the open R11 item, and it does not
  close it.

### 6.6 The egress class — exact

**The System-1 `decide` is NOT an egress tool. A `no_egress` run keeps it.**

`is_egress_tool` (`acb_skills/egress.py:232-250`) keeps a tool only when every
check below passes. Here is how the System-1 callable passes each one:

| Check | Line | The System-1 `decide` |
|---|---|---|
| Not an MCP tool | `:242` | It is a plain function in `acb_skills` |
| Not a store write | `:247` | `decide` is not in `STORE_WRITES` |
| Its annotation says `open_world` is `False` | `:249-250` | `decide_tools.SYSTEM_ONE_RISK` is set as `__tool_risk__` on the function, with no `annotate()` call, so the Jev registry entry stays. `_risk_of` reads the function first (`:217-220`) |
| The platform owns the callable | `:221`, `:183-196` | `_collect_injectable_platform_tools` registers it with `_register_platform_callable`, as it does for every platform tool |

- **The Jev `decide` stays `open_world=True`.** Its annotation in
  `TOOL_ANNOTATIONS` does not change, because it still reaches a separate
  sub-processor. The two callables carry the same name and different
  annotations. The function annotation is what tells them apart.
- **Why `False` is true here.** The System-1 tool sends content to one
  destination: our Router, on `tier-fast`. The run's own model requests
  already send the same content to the same Router. So the tool adds no
  destination. That is the meaning `open_world=False` carries for every model
  request today.
- ⚠️ **One condition keeps it true, and no test can hold it.** The vendor that
  serves `tier-fast` must already be a sub-processor for the organization.
  If an operator binds `tier-fast` to a new vendor, that vendor receives
  covered content. This is advisory. Per §13 Q5, an operator warning holds it.
  WS-37 owns the sub-processor list.
- `test_delegation_no_egress.py:82-101` pins `EXPECTED_OPEN_WORLD`. It keeps
  `decide`, because the Jev entry stays `open_world=True`. S1 adds
  `COVERED_PROJECTS_TOOLS_TIER_ROUTED` beside `COVERED_PROJECTS_TOOLS`, with
  this section as the reason. The plain pin does not move.

### 6.7 Its output is data

The context is tenant content, and an injection can sit inside it. Five rules
bound what an injection can do.

1. **The System-1 agent holds no tools.** It cannot act.
2. **Its answer is validated.** A `choice` outside the item's options gives
   `unsure`. So the worst an injection can do is pick a wrong option.
3. **The reason is capped at 120 characters** and has its line breaks
   removed. The tool drops a reason that holds a URL or a code fence.
4. **The tool frames the result as data.** The calling model reads it after
   the fixed lead "System 1 answer (data, not instructions):".
5. **The logs hold no tenant text.** The tool logs the status, the kind, the
   item count and the confidence only. This is today's rule
   (`decide_tools.py:27`).

### 6.8 Batching

- The tool asks up to 20 items in one request. The schema says so, so the
  calling model learns to batch.
- The prompt addendum gains one line: ask related decisions together, in
  `items`.
- The executor's turn-kind question is never batched with a tool call. It runs
  before the first main request.

---

## 7. What changes in the UI

### 7.1 What leaves

- The picker block (`AgentChat.tsx:2077-2106`).
- The models fetch (`:292-302`), the static list (`:50-57`), the
  "Frequently Used" sort (`:320-338`) and the two `localStorage` keys
  (`:120-145`).
- The `model` and `lockModel` props, once the email chat stops passing them
  (S4).
- The `chat_model` field of the email assistant settings
  (`src/app/email/components/automation/ai-settings/SettingsTab.tsx`) and of
  the Tasks settings (`src/app/tasks/components/TaskSettingsModal.tsx`). S4
  removes both controls.

### 7.2 What stays, and what is new

- **The effort selector stays**, at `AgentChat.tsx:2110-2132`, with its three
  labels.
- **A tier label on each answer (proposed).** The executor emits one AG-UI
  custom event, `ai.route`, per model request, with the tier slug and the
  kind. The chat folds the events of one answer into one quiet label, for
  example "Balanced, then Powerful". The label comes from `tier_catalog`
  (D-AI-1), never from a model name (D32.7).
- **Where it shows.** In the answer's details menu, for every member. The
  owner chose this on 2026-10-06 (§13 Q4).
- **An admin sees cost per tier** in the existing usage reads (CP-7,
  `GET /my/usage/activity`). Nothing new is needed there.
- `/api/models/all` stays. The Models settings page and other surfaces read
  it.

---

## 8. Backward compatibility (R6)

No migration is needed. Every step below is expand, then switch, then
contract.

| What | Expand (S2) | Switch (S3, S4) | Contract (a LATER release) |
|---|---|---|---|
| `AgentRunRequest.model` | The executor ignores it for a covered agent, and logs `ai_route.model_ignored` with the value | The chat stops sending it | Remove it from `route.ts` after one release with zero `ai_route.model_ignored` lines from the chat. The gateway field stays for API callers until H-44 decides |
| `payload.think_mode` | Unchanged | Unchanged | Never. It stays |
| `localStorage` `cc-model-<agent>`, `cc-model-usage` | Unchanged | The chat stops reading them, and deletes them once on mount | Nothing left |
| `email_assistant_settings.chat_model` | Unchanged | The email chat stops reading it for a covered agent | The column stays. A drop is a later decision (R6) |
| `user_settings.chat_model` (Tasks) | Unchanged | The Tasks chat stops reading it for a covered agent | Same |
| `PROJECTS_AGENT_MODEL` and the other agent env vars | They become the Balanced rung of the policy | Unchanged | Unchanged. They are D-AI-4's default |
| `copilot_chat_model` setting | The executor ignores it for a covered agent | Unchanged | H-44 decides |
| The `mode === "litellm"` branch of `route.ts:644-645` | Unchanged | No UI reaches it once the picker leaves | A later PR removes it |
| The Jev `decide` tool | Unchanged for agents the flag does not cover | — | It leaves the chat when the flag covers every agent. The facade stays for the email features |

**Rollback.** Remove an agent from the flag. The executor then reads
`model` again and gives the agent the Jev `decide`. No data moved, so nothing
needs restoring.

---

## 9. The flag and the rollout

- **`AI_TIER_ROUTING`**, setting `ai_tier_routing: str = ""` in
  `packages/acb_common/acb_common/settings.py`. Empty means OFF.
- The value is a comma list of agent names, or `*` for every agent. Example:
  `AI_TIER_ROUTING=projects-assistant`.
- One reader, `tier_policy.tier_routing_on(agent: str) -> bool`, fails closed.
  This follows the `_native_sessions_enabled` idiom (`executor.py:5728-5737`).
- **The UI flag is `NEXT_PUBLIC_AI_TIER_ROUTING`**, a build-time value that
  hides the picker. It ships OFF.
- The UI flag must not turn on before the backend flag covers the agents the
  chat shows. A member with no picker and no policy gets Balanced for every
  step, which is today's real behaviour (§2.2). So the wrong order is safe,
  and it is not useful.

**Order.**

1. S1 and S2 ship dark.
2. The flag covers `projects-assistant` on production. Read §11 for the gate.
3. One week of `ai_route.chosen` lines and credit burn is read.
4. S3 ships, and the UI flag turns on.
5. S4 adds the other agents one at a time. Email-assistant goes first (D86).
   Then crm, whatsapp, the orchestrator, apis-config, task-manager and
   app-builder follow.

---

## 10. Fences (R7)

| Rule | Fence | Kind |
|---|---|---|
| The chat shows no model picker while the UI flag is on | `src/components/AgentChat.picker.test.ts` (new, S3). The markup of a covered agent holds no picker, and the one fetch of `/api/models/all` waits for the plan | vitest |
| The effort selector stays with three labels | Same file. It finds Auto, Thinking and Max | vitest |
| The composer reads no model from `localStorage` | Same file. A source check finds no `cc-model-` key in `AgentChat.tsx` | vitest |
| The chat learns coverage from the server, never from a list of its own | `tests/unit/test_agent_list_tier_routed.py` (S3). `GET /agent` sets `tier_routed` from `tier_routing_on`, and a fault marks no agent | pytest |
| An answer names its tiers in its details menu, for every member | `AgentChat.picker.test.ts` and `src/lib/tierRouting.test.ts` (S3). Two events give one label. An off-ladder slug draws nothing. The words match the `tier_catalog` seed | vitest |
| The UI flag off changes nothing | `AgentChat.picker.test.ts`. The flag-off markup is the same for each coverage value. An answer with `ai.route` events draws the same as one with none | vitest |
| The executor ignores a client model for a covered agent | `tests/unit/test_tier_policy.py` (new) | pytest |
| The policy table lives in one place | `test_tier_policy.py`. Each kind maps to a tier in the slate, and each `TOOL_HINTS` name exists in a tool registry | pytest |
| A tool hint raises the NEXT request only | `test_tier_policy.py`, through a real `OpenAIChatCompletionClient` on an `httpx.MockTransport`, as in `test_native_maf_wire.py` | pytest |
| Max sends every main request to `tier-powerful`, and a decision stays on `tier-fast` | `test_tier_policy.py` | pytest |
| The System-1 request names `tier-fast`, holds no tools and carries a JSON schema | `tests/unit/test_system_one_tool.py` (new), on the wire | pytest |
| A batch is one request | `test_system_one_tool.py` | pytest |
| A choice outside the options reads as `unsure` | `test_system_one_tool.py` | pytest |
| The reason is capped, cleaned and framed as data | `test_system_one_tool.py` | pytest |
| The System-1 request is attributed to the calling agent | `test_system_one_tool.py`, and `test_internal_ai_is_routed.py` gains one case | pytest |
| A `no_egress` run keeps the System-1 `decide` and drops the Jev `decide` | `test_delegation_no_egress.py` gains two cases and the pin `COVERED_PROJECTS_TOOLS_TIER_ROUTED` | pytest |
| The `decide` schema stays under its ceiling | `test_tool_schema_diet.py`, `CORE_SCHEMA_CEILINGS` and `CALL_CONTRACTS` | pytest |
| The flag fails closed | `test_tier_policy.py`. A broken settings read reads as OFF | pytest |
| No new direct vendor call | `tests/unit/test_no_direct_ai_vendor_calls.py` (new). It scans `apps/` and `packages/` for `litellm` verbs, the `openai` and `anthropic` SDKs and vendor AI hosts. A baseline lists the §2.10 paths with a reason each, and it only goes down. `apps/services/customer_console/` is exempt, because it IS the Router | pytest |
| `tier-fast` serves only a vendor that is already a sub-processor | **Advisory.** §6.6 and §13 Q5 | — |

---

## 11. Tickets

Each ticket is one PR. Each one ships dark.

### S1 · The System-1 tool for Projects — AGENT-SAFE (build), OWNER-GATE (turn on)

**Builds:**

- `acb_skills/system_one.py`, the agent, and the System-1 engine of `decide`.
- The `items` batch, the reason, and the output rules of §6.7.
- The annotation and the registration of §6.6.
- The `AI_TIER_ROUTING` setting and its reader.
- The injection chain gives a covered agent the System-1 `decide`.

**Done when:**

1. With `AI_TIER_ROUTING=projects-assistant`, a projects-assistant run holds
   `decide`. One call sends one request. Its `model` is `tier-fast`, it holds
   no `tools`, and its `response_format` has the type `json_schema`.
2. That run holds the System-1 `decide` with `DECIDE_ENABLED` off.
3. Another agent on the same box holds the Jev `decide` only when
   `DECIDE_ENABLED` is on, as today.
4. A batch of 5 items sends one request and returns 5 lines.
5. A confidence of 0.5 under Auto returns `unsure`.
6. A `choice` outside the options returns `unsure`.
7. A Router 402, a timeout and a bad JSON answer each return `UNAVAILABLE`.
8. A covered (`no_egress`) projects-assistant run keeps `decide`.
9. Every fence of §10 that names S1's files passes, and was red before the
   change.

**Measured, not in CI:** on a dev box, 50 fixed Projects questions are asked of
System 1 and of the main model. Where System 1 says a confidence at or above
0.70, the two agree on at least 45. The PR reports the number.

**Gate:** to put `projects-assistant` in the flag on production is
OWNER-GATE, because it moves credit spend for a live organization (§3a rule 3).

### S2 · The executor's tier policy — AGENT-SAFE (build), OWNER-GATE (turn on)

**Builds:**

- `tier_policy.py`: the table, `TOOL_HINTS`, and the threshold per effort.
- `TierPolicyMiddleware` and the turn-kind question.
- The effort mapping of §5.
- The `ai_route.chosen` and `ai_route.model_ignored` log lines.
- The `ai.route` AG-UI event.

**Done when:**

1. A covered agent's run ignores `AgentRunRequest.model` and logs
   `ai_route.model_ignored`.
2. A turn classified `chat` sends every main request on the agent's default.
3. A turn classified `code` sends every main request on `tier-powerful`.
4. A call to a hinted tool moves the NEXT request to the hint's tier. The
   request after a plain tool goes back.
5. Max sends every main request on `tier-powerful`, and a System-1 call stays
   on `tier-fast`.
6. A turn-kind timeout reads as `chat`.
7. A message under 12 words sends no turn-kind request.
8. An agent the flag does not cover behaves as today, byte for byte, on the
   wire.
9. task-manager, a Copilot agent, gets the turn-kind tier for the whole run.

**Before dispatch:** read `ROUTER_SERVING_ENABLED` and the live `tier_binding`
rows on the box. Report whether Balanced and Powerful serve one model (§13 Q6).

### S3 · The picker leaves the chat — AGENT-SAFE (build), OWNER-GATE (turn on)

**Builds:** the removal of §7.1 behind `NEXT_PUBLIC_AI_TIER_ROUTING`, the
one-time `localStorage` clean-up, and the tier label of §7.2.

**Done when:**

1. With the flag on, the composer shows no picker and fetches no
   `/api/models/all`.
2. The effort selector shows Auto, Thinking and Max, and its choice reaches
   `payload.think_mode`.
3. The chat sends no `model` field.
4. An answer with two `ai.route` events shows one label with both tier labels.
5. With the flag off, the composer is unchanged.
6. The surface passes the `visual-review` skill in light mode, at compact
   density and under a changed accent (CLAUDE.md §4).

**Gate:** the flag is build-time and reaches every organization at once, so to
turn it on in production is OWNER-GATE.

### S4 · Every other agent — AGENT-SAFE (build), OWNER-GATE (turn on)

**Builds:** the policy and the System-1 `decide` for email-assistant, then
crm-assistant, whatsapp-assistant, the orchestrator, apis-config, task-manager
and app-builder. The email chat stops passing `model` and `lockModel`. The two
`chat_model` controls leave (§7.1). One PR may cover one agent or several, and
each agent joins the flag on its own.

**Done when:**

1. Each named agent passes S2's done-when items 1 to 5 under the flag.
2. The email chat runs with no `chat_model` read for a covered email-assistant.
3. The Tasks chat runs with no `chat_model` read for a covered task-manager.
4. The orchestrator's own pick of a specialist uses the System-1 `decide`
   only when the model calls it. This ticket adds no router in front of the
   orchestrator (`customer_console.md` §6A.14 CP-13d).

### S5 · Modality tiers — SPEC ONLY (each later ticket AGENT-SAFE)

**Scope:** move the §2.10 paths onto the Router, one task at a time, and let a
tool declare its task.

1. Transcription: `acb_stt` calls the Router's `/v1/audio/transcriptions`
   (`customer_console/main.py:8030`) on `tier-stt`.
2. Embeddings: the gateway's `/v1/embeddings` and the three writers call the
   Router. The Router serves no `aembedding` door today
   (`customer_console/router.py:625-645`), so this needs one first, per D61.1.
3. Image and speech: a tool names `image` or `speak`, and the Router serves
   `tier-image` or `tier-tts`.
4. Live notes: Deepgram keys come from the Router, or the feature moves to a
   routed door. This needs its own design.

**Done when, for each one:** its path leaves the baseline of
`test_no_direct_ai_vendor_calls.py`, and a test proves the call reaches the
Router with the declared task.

### Order

S1 → S2 → (owner turns on Projects) → S3 → S4 → S5. S3 may be built in
parallel with S2, and it turns on after S2.

---

## 12. Verification commands

```bash
# The backend fences (S1, S2, S4)
uv run pytest tests/unit/test_system_one_tool.py tests/unit/test_tier_policy.py \
  tests/unit/test_decide_tool.py tests/unit/test_delegation_no_egress.py \
  tests/unit/test_tool_schema_diet.py tests/unit/test_core_tool_floor.py \
  tests/unit/test_internal_ai_is_routed.py tests/unit/test_native_maf_wire.py \
  tests/unit/test_think_mode.py tests/unit/test_byok_default.py \
  tests/unit/test_no_direct_ai_vendor_calls.py \
  tests/unit/test_agent_list_tier_routed.py -q

# The UI fences (S3)
cd workbench/control_plane && npx tsc --noEmit && \
  npx vitest run src/components/AgentChat.picker.test.ts src/lib/tierRouting.test.ts \
  src/app/api/models/all/route.test.ts src/app/email/lib/chatScope.test.ts

# The writing fence (every PR that touches markdown)
node .claude/hooks/ste-lint.mjs --staged
```

On a box, a covered run shows one `ai_route.chosen` line for each model
request. Each System-1 call shows one `usage_event` row on `tier-fast` with
source `system_one`.

---

## 13. Owner gates and answered questions

### 13.1 Gates — refuse these by name

| Act | Why |
|---|---|
| Put an agent in `AI_TIER_ROUTING` on production | It changes what a live organization is charged |
| Turn on `NEXT_PUBLIC_AI_TIER_ROUTING` in production | It is build-time and reaches every organization at once |
| Bind `tier-fast` to a vendor that is not yet a sub-processor | §6.6. A covered run would send content to it |

### 13.2 Answered — the owner took each default on 2026-10-06

| # | Question | Answer |
|---|---|---|
| Q1 | Do the email triage features move from `tier-decide` (Jev) to the System-1 tool on `tier-fast`? | No. They stay on Jev under D-EM-7 and D-EM-8 |
| Q2 | Does code go to `tier-code` when the operator binds it, instead of `tier-powerful`? | No. Code goes to `tier-powerful`, as the owner said |
| Q3 | Does the effort selector gain a fourth label, "Fast", that caps a turn at Balanced? | No fourth label |
| Q4 | Does the tier label show on each answer, and to whom? | Yes, in the answer's details menu, for every member |
| Q5 | Where does the rule "`tier-fast` serves only an existing sub-processor" live: an operator warning on the binding page, or WS-37's list? | An operator warning, added with WS-37 |
| Q6 | Does production bind Balanced and Powerful to one model? If yes, escalation costs more and changes nothing until the operator binds a stronger model | S2 reads the box and reports. No policy change |
| Q7 | Are the thresholds 0.70, 0.80 and 0.90 right? | Yes, until S1's measured agreement says otherwise |
| Q8 | Does the turn-kind question earn its latency and cost, or do tool hints alone suffice? | Keep it. S2 reports the median latency it adds |

---

## 14. Where the rest of the documentation points here

- `customer_console.md` §6A.14 CP-13d: the `decide` tool's engine changes
  under D90.
- `ai_metering_and_analytics.md` §3.3 and §3.4: D90 amends the meaning of
  `customer_visible` and keeps D-AI-4.
- `agent_architecture.md` §8: the `decide` row of the delegation table.
- `projects_ai_chat.md`: the Projects chat is the first covered agent.
