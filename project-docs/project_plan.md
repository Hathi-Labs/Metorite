# Project Plan — Metorite v2

> **Org:** Fracktal Works · **Updated:** 2026-08-01 · **Version:** 3.1
> Single source of truth for **what** we build (requirements), **when** (milestones), and **how much** (phased WBS). Absorbs the former `product_requirements.md` and `wbs.md`.
> **Read first:** [`AGENTS.md`](AGENTS.md) — current build status, file index, glossary.
> **Near-term sequencing and dispatch:** [`work_plan.md`](work_plan.md) (2026-07-31) — for ordering, that doc wins. *(re-affirmed 2026-08-09: §6 sequencing yields to work_plan.md §2, which now also carries WS-29 multi-tenancy — D15/D16/D18)*
> **Companions:** [`system_architecture.md`](system_architecture.md) (design + ADRs) · [`reference.md`](reference.md) (MAF / Copilot SDK / memory library notes) · [`agent_repo_compatibility.md`](agent_repo_compatibility.md) (how to build an agent) · [`specs/`](specs/) (per-feature specs).

> **⚠️ Two work surfaces — this doc is the FEATURE roadmap (M3→M6). It is NOT the whole "what's left":**
> A foundation architecture audit (2026-07) surfaced trust-boundary / plumbing / observability gaps that
> gate the feature work. Those are tracked separately and take priority:
> - **Foundation hardening (P0 first):** [`/FOUNDATION_BUILDOUT_CHECKLIST.md`](../FOUNDATION_BUILDOUT_CHECKLIST.md) — `BO-1..21`
>   (Action Broker wiring, auth enforcement, sandboxing, event-bus consumer, memory activation, …).
> - **Competitor-informed refs + 2 new items:** [`specs/competitive_hardening_2026-07.md`](specs/competitive_hardening_2026-07.md) — `CH-1..9`.
> - **Harness practices:** [`specs/harness_hardening_2026-07.md`](specs/harness_hardening_2026-07.md) — `HH-1..8`.
> Read those for the *foundation* backlog; read this doc for the *feature* backlog. The P0 foundation items
> (esp. **BO-1 Action Broker**) are prerequisites for L3/M5 write-authority milestones below.

---

## 1. What We Are Building

A **headless, self-mutating, multi-agent orchestration platform** for running a company.

When a company event fires (webhook from ClickUp/Zoho/Odoo, cron, or an ambient signal from email/WhatsApp/meetings), Metorite resolves the right specialist agent (or fans out to several), `git pull`s its persistent local clone, injects credentials from the Integration Registry, and executes it on the **Microsoft Agent Framework (MAF)** runtime. On failure, a researcher+editor **Copilot SDK mutation container** diagnoses and fixes the code, commits to the live clone, and opens a GitHub PR as the audit record — the next run picks up the fix automatically.

Operators work through a thin **Control Plane** (Next.js browser UI): one unified VS Code Copilot-style chat over all agents (multi-agent fan-out, streaming tool calls), agent add/remove, integration config, LLM tier settings, an HITL approval inbox, and observability. **No in-app agent/skill editing** — all authoring is VS Code + Git.

### Product levels (each independently deployable)

| Level | Name | Core value | Status |
|---|---|---|---|
| **L1** | Core Engine | Cited Q&A over live company data; any agent event runs end-to-end | ✅ Done |
| **L1.5** | Control Plane + Multi-Agent | Unified chat; dynamic specialist routing; agent/integration/LLM UIs | ✅ Done |
| **L2** | Self-Mutation + Agent Ecosystem | Agents auto-repair on failure; new capabilities from chat; memory; domain agents | 🔄 In progress |
| **L3** | Capture + Write Authority | All ingest channels; approval-gated writes to ClickUp/Zoho | 🔲 Planned |
| **L4** | Company Intelligence | Strategy, goals, Odoo ERP, BI; system self-improves | 🔲 Planned |

---

## 2. Scope

**In:** Core engine (FastAPI event router + Dynamic Agent Loader + MAF orchestration); self-mutation loop; distributed `agent-*` / `skill-*` repos; ingest (ClickUp, Zoho, Odoo, Gmail/Outlook, WhatsApp, meetings); pull/push/ambient interaction; approval-gated writes via Action Broker; nightly reconciliation; encrypted Integration Registry; Control Plane (chat, HITL, observability); memory (Mem0 + Graphiti); email client app.

**Out / non-goals:** in-app editing of agent/skill/app *code*; any browser IDE (Theia, VS Code fork); n8n or any second workflow *runtime* (the Workflows app composes DB-persisted workflow configuration compiled to MAF Workflows — ADR-028, `specs/workflows_app.md`); autonomous repo merges (human PR review + `max_mutation_attempts=1` mandatory); autonomous writes before Action Broker is live; customer-facing access; RBAC beyond admin/operator/contributor.

*(Update 2026-08-01, doc-truth pass: the "no second workflow runtime" non-goal stands **as amended by ADR-028** — the Workflows app has since shipped (`specs/workflows_app.md`), composing DB-persisted workflow config compiled to MAF Workflows; it is workflow composition, not a second runtime.)*

### Success criteria (v2.0)
- Webhook → agent runs + telemetry logged in < 30 s (warm < 5 s).
- Skill failure → mutation PR with a plausible fix in < 5 min; `max_mutation_attempts=1` enforced.
- "Status of customer X / project Y" → cited answer in < 10 s.
- Zero silent drift over 30 consecutive days.
- ≥ 3 agent repos have merged self-authored improvement PRs in production by M6.

---

## 3. Requirements by Level

IDs are retained for traceability. Priority is **Must** unless noted.

### L1 — Core Engine ✅
- **L1-01/02** Event gateway routes to agent by name; Dynamic Agent Loader does `git pull --ff-only` (~0.5 s warm; full clone on first event), imports `agents.py`, calls `build_agents()`; per-repo lock prevents races.
- **L1-03/04** MAF workflow engine executes agents (in-process asyncio, Phase 0). Skills run as `GitHubCopilotAgent` tool calls / MCP servers. HITL via Action Broker (Postgres `approval_queue`).
- **L1-05/06/14** Integration Registry (encrypted Postgres) is the single credential store; loader resolves only `config.json`-declared integrations and injects via MAF `mcp_servers=` (MAF agents) or `_build_agent_env()` (Copilot Tier 1.5). OAuth authorize→callback→refresh framework; no creds in repos/logs/LLM context.
- **L1-07** First agent `agent-task-manager` + `skill-clickup-sync` (cited ClickUp Q&A).
- **L1-08/09** Reconciler v0 (nightly drift escalation); Guardrails v0 (schema-validated output, citation enforcement, unresolved-entity abort).
- **L1-10** All LLM calls via LiteLLM tiered routing; per-tier cost tracked; MAF-native OTel is OTLP-ready (self-hosted trace backend deferred).
- **L1-11** Control Plane shell: unified chat (MAF AG-UI + Copilot Tier 1.5), model picker, send/queue/steer, MCQ rendering, observability, Google SSO (org domain).
- **L1-12** Docker Compose infra: Postgres+pgvector, Redis, LiteLLM, single Linux VM.
- *Deferred to Phase 2:* L1-13 local Tier-1 vLLM/Qwen3-8B.

### L1.5 — Control Plane + Multi-Agent ✅
Dynamic `as_tool()` agent registry (LLM routes by description, zero hard-coded routing); `delegate_to_agent` + `spawn_copilot_agent` tools; agent auto-repair on `AgentLoadError`; proactive skill sync; `/agents`, `/integrations`, `/settings/models` UIs; AG-UI→SSE translation; universal tool injection (`web_search`, `fetch_page`, `call_agent`, `write_artifact`) for both MAF and Copilot agents.

### L2 — Self-Mutation + Agent Ecosystem 🔄
- **L2-01..06** `Self_Mutation_Node` spawns `acb-mutation-runner`; tests pass → commit live + open `auto-fix/{run_id}` PR; fail → reset; unmerged-close webhook → rollback; eval CI gate (Promptfoo + Inspect AI) on every `agent-*`/`skill-*` PR; mutation audit log in HITL inbox.
- **L2-07..10** Domain agents (`agent-reconciler`, `agent-sales`+Zoho, `agent-triage`+Gmail); cross-source entity resolution (deterministic keys → LLM fallback → review queue).
- **L2-11..13** Automatic memory extraction (Mem0 + Graphiti, ≤~2 facts/turn, non-blocking, dedup + audit); skill crystallisation from successful runs (confidence ≥ 0.6, as PR).
- **L2-14..17** Durable dispatch queue (idempotent, retry/backoff, dead-letter); long-run supervisor (heartbeats, runtime ceiling, loop detector); semantic cache + token compression *(Should; Phase 2)*; Control Plane HITL queue.

### L3 — Capture + Write Authority 🔲
- **L3-01..05** Meeting bot (Vexa + WhisperX/Pyannote) → transcript → action items; WhatsApp ingest + send; ambient trigger engine (Redis Streams → rule eval → dispatch); `agent-delivery` (stale-task pings, escalation).
- **L3-06..12** Action Broker full build (approval queue + UI, per-action authority tiers, audit + rollback, kill switch); Suggest+Apply for ClickUp + Zoho; authority-tier code gate (no autonomous writes in v1); out-of-band HITL (email/WhatsApp reply-to-approve); skills security scanner *(Should)*.

### L4 — Company Intelligence 🔲
- **L4-01..06** Odoo ERP ingestor (read-only); `agent-strategy` (weekly digest, LightRAG over SOPs); GOAL entity + roll-up; sales BI; personal prioritisation; smart delegation.
- **L4-07..12** RouteLLM training pass *(Should)*; Annealer loop (mine successes → skill PRs → shadow/canary/full) *(Should)*; per-skill success tracking; memory feedback loop; anti-hallucination guardrails (< 1%); nightly reconciler v2 (cross-source, < 30 min).

---

## 4. Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-01/02 | Event → exec latency (warm / cold) | < 5 s / < 30 s |
| NFR-03 | Pull query p95 | < 15 s |
| NFR-04 | Ambient trigger → push | < 5 min |
| NFR-05/06 | Mutation PR open time / max PRs per failure | < 5 min / 1 |
| NFR-07 | Hallucination rate | < 1% |
| NFR-08 | Availability (business hours IST) | ≥ 99% |
| NFR-09/10 | Monthly LLM cost / audit retention | within budget / ≥ 1 yr |
| NFR-11/12 | Memory extraction non-blocking / audit never deletes > 50% in one pass | — |
| NFR-14/15 | Idempotent dispatch (no double-spawn) / bounded concurrent runs | — |
| NFR-16 | Secret storage | encrypted at rest, access-audited |
| NFR-17/18 | Org-memory staleness / reconciler runtime | < 60 s / < 30 min |
| NFR-19 | Delegation/assignee accuracy | ≥ 90% |

---

## 5. Milestones

| ID | Name | Target | Status |
|---|---|---|---|
| **M1** | Core Engine live — ClickUp Q&A with citations | 2026-05-25 | ✅ PASSED |
| **M2** | Self-Mutation live — agents fix own code, open PRs | 2026-06-12 | ✅ PASSED |
| **M2.5** | Interactive runtime unified — Copilot SDK Tier 1.5 streaming; CopilotKit removed | 2026-06-20 | ✅ PASSED |
| **M2.6** | Foundation hardening — chat history, cloud sandbox, integration OAuth, AG-UI events | 2026-06-18 | ✅ PASSED |
| **M2.7** | Universal tool injection — web search + inter-agent wiring | 2026-06-06 | ✅ PASSED |
| **M2.8** | Memory live — Mem0 episodic + Graphiti bi-temporal KG | 2026-06-12 | ✅ PASSED *(Update 2026-08-01, doc-truth pass: this verified the plumbing, not activation — Mem0/Graphiti remain default-OFF in prod; activation is BO-21)* |
| **M2.9** | Email app — multi-account client (Gmail/Outlook) + AI assistant | 2026-06-20 | 🔄 In progress — full client + automation suite live; consolidated plan + prioritized completion roadmap: [`specs/email_app_master_plan.md`](specs/email_app_master_plan.md) |
| **M3** | Full Agent Ecosystem — Sales + Triage + Reconciler agents via UI | ~2026-08-26 | 🔲 Not started — platform work: Zoho + Gmail ingestion, entity resolution, Action Broker hardening |
| **M4** | Capture live — meetings + WhatsApp + ambient triggers | ~2026-10-14 | 🔲 Not started |
| **M5** | Suggest+Apply live — approval-gated writes to ClickUp/Zoho | ~2026-12-09 | 🔲 Not started |
| **M6** | v2.0 Release — Odoo + Strategy + Intelligence layer | ~2027-02-10 | 🔲 Not started |

**Critical path (remaining):** Zoho + Gmail ingestion pipelines → entity resolution → domain agent repos registered via UI → **M3** → meeting bot + ambient triggers → **M4** → Action Broker + Suggest+Apply → **M5** → Odoo + strategy + goal model → **M6**.

**Estimate from today:** ~36 calendar weeks to M6 (2 engineers @ ~80%); ~10 months with a 20% buffer.

---

## 6. Work Breakdown by Phase

Effort in **engineer-weeks (ew)**, PERT = (O + 4M + P)/6. Status: ✅ done · 🔄 in progress · 🔲 next.

### Phase 0 — Core Engine Foundation ✅ (~17.6 ew · M1)
Infra baseline (Postgres+pgvector, redis:7-alpine, CI); graph schema v0; ClickUp ingestor; agent + skill repo templates; Dynamic Agent Loader + AG-UI endpoint; **MAF harness** (replaced LangGraph/deepagents/langchain — see ADR-026); gateway + Google SSO; first agent (`agent-task-manager`); guardrails v0; MAF-native OTel; LiteLLM tier config.

### Phase 1 — Self-Mutation Loop 🔄 (~5 ew remaining · M2 closed)
- ✅ **1.1/1.2** `Self_Mutation_Node` + Copilot SDK mutation sandbox (`Dockerfile.mutation`, host Docker socket, no DinD).
- ✅ **1.4** Inline eval gate in the sandbox (`_tests_passed()` parses `TEST_SUMMARY`; pass → auto-push, fail → HITL Push-Anyway/Re-mutate/Reject).
- ✅ **1.6** Webhook → MAF dispatch (Copilot `runtime` arm removed).
- 🔲 **1.3** GitHub PR automation (branch → commit → PR with telemetry; no self-merge). **NEXT.**
- 🔲 **1.5** Mutation audit logging surfaced in HITL queue.
- 🔲 **1.7** LiteLLM BYOK forced for all sessions (consistent cost metering).

### Phase 1.5 — Dynamic Multi-Agent Orchestration ✅ (2026-06-05)
`as_tool()` capability registry; `delegate_to_agent` + `spawn_copilot_agent`; agent auto-repair on `AgentLoadError` (researcher+editor); proactive skill sync; `/agents`, `/integrations`, `/settings/models` UIs; AG-UI→SSE fix; WorkflowBuilder wired (infra ready). *(Update 2026-08-01, doc-truth pass: that `WorkflowBuilder` import was never instantiated and was removed under BO-12; the real MAF WorkflowBuilder use shipped later via the ADR-028 workflows engine.)*

### Phase 1.6 — Universal Tool Injection + Inter-Agent Wiring ✅ (2026-06-06 · M2.7)
Zero-credential `web_search`/`fetch_page`; `normalize_tools()` wrapping across all three injection paths; Tier-2 list-tool shimming; live agent-registry system-message addendum; 14/14 integration checks pass.

### Phase 2 — Full Agent Ecosystem 🔲 (~10.3 ew · M3)
- 🔲 **2.1** Zoho CRM ingestion pipeline (webhooks + REST → entity graph).
- 🔲 **2.2** Gmail ingestion pipeline (Pub/Sub push → MESSAGE entity).
- 🔲 **2.3** Customer/person entity resolution (deterministic + LLM fallback, cross-source dedup).
- 🔲 **2.4** Action Broker hardening (approval queue + API, authority tiers, audit + rollback, kill switch). *(The authority-tier decision core now exists but is inert/unwired — see **BO-1**, the P0 foundation item.)*
- ◑ **2.5** Mem0 + Graphiti memory integration — code shipped (pgvector backend, Neo4j `--profile memory`, `/memory/*` API, injected into orchestrator + Copilot agents) **but default-OFF and inert** (`mem0_enabled=False`, zero-vector embeddings fallback). Activation = **BO-21**.
- 🔲 **2.6** Semantic cache + token compression (LiteLLM cache 1h TTL; LLMLingua-2 on >1k-token tool outputs). See [`specs/llm_caching_memory.md`](specs/llm_caching_memory.md).

### Phase 3 — Capture Expansion 🔲 (~9 ew · M4)
Meeting bot infra (`skill-meeting-transcribe`); transcript → graph + action items; WhatsApp ingest + send; ambient trigger engine.

### Phase 4 — Write Authority + Approval UX 🔲 (~7.7 ew · M5)
Action Broker full build; Suggest+Apply for ClickUp (from triage) and Zoho (from sales).

### Phase 5 — Intelligence Layer + Hardening 🔲 (~10.5 ew · M6)
`agent-strategy` (digest + LightRAG); Odoo RPC ingestor; RouteLLM training pass; self-mutation quality review (seed golden evals); **hardening pass** (cost, latency, retry/idempotency, security audit — includes the infra lockdown below); v2.0 release review.

### Phase 2.5 — Harness Hardening 🔄 (~3 ew · started 2026-07-02)
Gap-closure workstream from the best-practices audit against [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering). Full analysis + work queue: [`specs/harness_hardening_2026-07.md`](specs/harness_hardening_2026-07.md).
- ✅ **HH-1** Real eval harness: create `evals/` (Promptfoo golden cases + Inspect scenarios + offline trajectory tests for HITL/sub-agent/reconnect/tool-failure), un-scaffold `skill-eval.yml`. Supersedes the scaffold half of L2-01..06's "eval CI gate". *(Shipped 2026-07-02 — see `specs/harness_hardening_2026-07.md` status log.)*
- ✅ **HH-2** Fail-closed `request_confirmation` for destructive actions + risk annotations (`read_only`/`destructive`/`idempotent`/`open_world`) on platform tools. *(Shipped 2026-07-02 — see `specs/harness_hardening_2026-07.md` status log.)*
- ✅ **HH-3** Telemetry export path: LiteLLM OTEL callbacks (gated on `OTEL_EXPORTER_OTLP_ENDPOINT`), per-call cache/token usage into `audit_event`; backend selection stays in Phase 5. *(Shipped 2026-07-02 — see `specs/harness_hardening_2026-07.md` status log.)*
- ✅ **HH-4** Automatic compaction — verified already shipped: `AgentChat.tsx` auto-compacts at 80% of the model's real context window (75/80 hysteresis, checkpoint model). Full token budgeting stays with [`specs/llm_caching_memory.md`](specs/llm_caching_memory.md).
- ✅ **HH-5** `own_tool_scope` config key filters agent-baked tools (executor `_apply_own_tool_scope`, all three build sites); the email-assistant subset itself lands with [`specs/archive/email_tool_consolidation.md`](specs/archive/email_tool_consolidation.md).
- 🔲 **HH-6** Sandbox normal agent runs (allowlist permission handler now; container isolation with Phase 5 hardening).
- 🔲 **HH-7** Typed sub-agent handoffs + namespaced sub-agent events (after chat-review strategic refactors).

### Cross-Phase Continuous Work
VS Code + Git authoring (ongoing); mutation PR review (~2 h/wk when active); prompt/instruction tuning (~15%/phase); docs (~10%/phase); **security review + secrets hygiene** (quarterly); cost monitoring + LiteLLM tier tuning (~2 h/wk).

- 🔲 **SEC-1 · Lock down production Postgres/Redis exposure.** On the Hostinger VPS, `acb-postgres` (5432) and `acb-redis` (6379) are published to `0.0.0.0` (public internet; Postgres logs show external auth probes). Bind to `127.0.0.1` in `infra/docker-compose.yml` (or firewall the ports) — the app reaches them locally. Schedule under the Phase 5 hardening pass or sooner. *(Added 2026-06-20.)*

### Phase totals

| Phase | Capability | PERT ew | Cumulative (2 eng) |
|---|---|---|---|
| 0 | Core Engine + first agent | 17.6 | 9 cw |
| 1 | Self-Mutation + PR automation | 7 | 12.5 cw |
| 2 | Agent ecosystem: sales, triage, reconciler | 12.3 | 18.5 cw |
| 3 | Capture: WhatsApp + meetings + ambient | 11 | 24 cw |
| 4 | Write authority + Action Broker | 7.7 | 28 cw |
| 5 | Strategy + Odoo + hardening + v2.0 | 10.5 | 33.5 cw |

**Total ~66 ew ≈ 33 calendar weeks ≈ ~8 months** (2 engineers @ ~80%); ~10 months with +20% buffer.

---

## 7. Constraints & Principles (Hard)

| # | Constraint |
|---|---|
| C-01 | `max_mutation_attempts = 1` per failure event — no exceptions. |
| C-02 | No credentials in agent/skill repos — Integration Registry only. `config.json` declares integration *names*. |
| C-03 | Action Broker is the only write path to source systems. |
| C-04 | No autonomous writes until Action Broker + authority tiers are live (Phase 4). |
| C-05 | All agent/skill artefacts promoted via PR with eval CI gate. Git is the single source of truth. |
| C-06 | Mutation container needs the host Docker socket mapped into the orchestrator. |
| C-07 | Indian DPDP Act 2023 — written employee consent before ingesting email/WhatsApp. |
| C-08 | All interactive + autonomous execution runs on **MAF** paths. No net-new raw Copilot SDK runtime entrypoints for business-agent execution (Copilot SDK = mutation container only). *(Update 2026-08-01, doc-truth pass: the "mutation container only" clause is superseded by the BO-12 resolution — the Copilot SDK is the supported **second runtime for interactive coworker chat** (Tier 1.5), per the AGENTS.md non-negotiables; unification tracked in `specs/agent_architecture.md` §11.)* |
| C-09 | No in-app agent/skill *code* editing; no browser IDE; no second workflow runtime (n8n) — workflow *composition* is DB-persisted config compiled to MAF Workflows (ADR-028). |

---

## 8. Top Risks

| ID | Risk | Score | Mitigation |
|---|---|---|---|
| R-04 | Unauthorised write to ClickUp/Zoho/Odoo | 12 | Action Broker; authority tiers; kill switch |
| R-06 | **Public exposure of prod Postgres/Redis (5432/6379 on 0.0.0.0)** | 12 | Bind to `127.0.0.1` / firewall (SEC-1); rotate creds |
| R-01 | Entity resolution failures (duplicate nodes) | 9 | Deterministic rules first; LLM fallback; review queue |
| R-02 | Agent hallucinations on company data | 9 | Citation enforcement; schema validation; second-pass verify |
| R-05 | WhatsApp Business API verification delay | 9 | Start early; OpenBSP/Whapi fallback |

---

## 9. Quality Gates

- **Per-PR (agents/skills):** Promptfoo golden cases + Inspect AI; merge blocked on regression; no `agents.py`/`SKILL.md` merge without a passing golden case.
- **Per-PR (Control Plane chat):** Playwright regression for the unified chat (default + named-agent render, model switching, send/queue/steer, tool blocks, markdown, MCQ).
- **Per-phase exit:** demo vs milestone acceptance; reconciler stable ≥ 7 days; cost within budget.
- **Continuous:** citation coverage + per-tier cost + per-skill success rate tracked.
- **Quarterly:** security review, secrets rotation, access audit, DPDP check.

---

## 10. Open Questions

1. Monthly LLM cost ceiling (drives tier thresholds + caching aggressiveness).
2. Retention windows for raw transcripts / message bodies / derived facts (needs legal sign-off).
3. Success-rate threshold that justifies suggest+apply → autonomous promotion.
4. WhatsApp community read posture (Meta TOS for reading group messages as a participant).
5. Meeting policy — which meetings the bot joins; default-in/out; consent UI.
6. DurableTask hosting (Phase 2) — DTS emulator vs Azure Durable Task Scheduler when workflows must wait hours/days. Postgres is not the DTS backend.
7. OAuth provider registration — one shared org-level app per service vs per-operator tokens (affects credential scope + audit granularity).
8. Cloud sandbox GitHub token model — org-level App installation token (auto-rotated) vs per-operator PAT.

---

## 11. Nice to Have / Future

**AI-powered integration code generation** — upgrade the `apis-config` agent to generate the full 6-file integration stack (client, normaliser, sync script, webhook receiver, agent tools, settings) from API docs via web search + LLM, committed as a `skill-<name>` repo through the eval gate. Credentials stay in the Integration Registry. Gated on code-gen fidelity + automated eval; deferred until L3+.

**Artifact Viewer** — ✅ **SHIPPED** (file-tree sidebar + inline document viewer + DOCX; `ArtifactViewerModal` + `write_artifact` backend). Spec archived: [`specs/archive/artifact_viewer.md`](specs/archive/artifact_viewer.md).
