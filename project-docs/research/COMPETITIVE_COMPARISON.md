# Metorite vs. Hermes Agent vs. OpenClaw

> Competitive analysis — where Metorite stands against the two most-visible
> self-hosted agent platforms of 2026, and what we should steal from each.
>
> Compiled 2026-07-12. Metorite facts are from an evidence-based code audit
> (cross-checked against `FOUNDATION_AUDIT_REPORT.md` / `FOUNDATION_BUILDOUT_CHECKLIST.md`).
> Competitor facts are from their GitHub repos, official docs, NVIDIA writeups, and
> security reporting. Fast-moving figures (stars, skill counts, channel counts) are
> date-stamped snapshots, not fixed facts.

---

## 0. TL;DR

**We are not really competing with Hermes or OpenClaw — we're in an adjacent category.**
Both of them are *self-hosted, single-user, personal autonomous agents* you talk to
from your chat apps. Metorite is an *enterprise, multi-agent, source-of-truth-mirroring
orchestration platform for running a company*, with human-in-the-loop approval as a
first-class governance layer.

That reframing matters because it tells us **what to copy and what to ignore**:

- **Copy their plumbing and their security engineering.** On the agent-loop / tool-execution /
  channel / sandboxing layer they are more complete and more battle-hardened than we are —
  because millions of people run them. This is exactly the layer our own audit flags as weak
  (unsandboxed loading, no-op permission gate, no messaging channels, dead observability).
- **Ignore their positioning.** Neither has multi-tenancy, org RBAC, a business system-of-record
  concept, HITL approval workflows for a *team*, or a real eval/regression harness. Those are
  our reason to exist, and they're genuine moats — but several are still *designed, not enforced*
  in our code.

**One-line verdict:** our architecture is more ambitious and more enterprise-correct than either
competitor's, but their *implemented* foundation (security, channels, ops robustness) is ahead of
ours. The highest-leverage work is to make our own guarantees real (Action Broker, sandboxing,
permission enforcement) using patterns they've already proven.

---

## 1. Category positioning

| | **Metorite** | **Hermes Agent** (Nous Research) | **OpenClaw** (formerly Clawdbot) |
|---|---|---|---|
| **What it is** | Headless multi-agent orchestration platform for running a company | Self-hosted, always-on personal autonomous agent | Self-hosted personal AI assistant living in your chat apps |
| **Primary user** | A company (team, multi-user, governed) | One power user / developer | One prosumer / developer |
| **Core value** | Coordinate specialist agents over company systems-of-record, with human approval on outward writes | "Grows with you" — self-improving skills, persistent memory | Omnichannel presence + acts on your OS (shell/files/web) |
| **Interface** | Web Control Plane (chat, HITL inbox, observability) | Chat apps + CLI + desktop app + web dashboard | Chat apps (26–29 channels) + device nodes |
| **Multi-agent** | Yes — designed as the whole point (7 live specialists) | Yes — orchestrator + isolated sub-agents (v0.6+) | **No** — single-agent loop; "multi-agent" = channel isolation |
| **Source-of-truth model** | **Read-mostly mirror of ClickUp/Zoho/Odoo; writes approval-gated** | None (personal files/memory) | None (personal files/memory) |
| **License / distribution** | Private, first-party (Fracktal Works) | MIT, open source, ~214K★ | MIT, open source, ~214K–355K★ (viral) |
| **Maturity** | Early, private, in active buildout | ~1yr, hype-heavy, ~27K open issues | <1yr, viral, security-troubled |
| **Deployment** | Docker Compose on a single VPS (Hostinger) | $5 VPS → GPU cluster → serverless; strong local/NVIDIA story | Local daemon (Node); optional NVIDIA NemoClaw |

**Takeaway:** the honest framing for stakeholders is *"we are building the governed, team-scale
orchestration layer these personal agents don't attempt — but we should adopt their proven
foundation code where our own is thin."*

---

## 2. Capability matrix (implemented reality, not docs)

Legend: ✅ real / mature · ◑ partial or default-off · ⚠️ designed-but-not-enforced · ✖ absent

| Capability | Metorite | Hermes | OpenClaw |
|---|---|---|---|
| Agent execution runtime | ✅ MAF executor (real, but monolithic god-file) | ✅ unified `AIAgent` core | ✅ Gateway agent loop |
| **BYO-LLM, many providers** | ✅ LiteLLM SDK (7+ providers + vLLM) | ✅ 300+ models, per-tool routing, MoA | ✅ provider/model + failover |
| **Local / on-prem models** | ◑ vLLM supported, weak story | ✅ Ollama/LM Studio/llama.cpp + NVIDIA RTX/DGX | ✅ local models; NemoClaw/Nemotron |
| Tiered / cost-aware routing | ✅ tier-fast/-balanced/-powerful (config fragmented) | ◑ per-tool model choice | ◑ failover only |
| **Messaging channels** | ✖ **email only** (Gmail/IMAP/Outlook) | ✅ 16+ (Telegram/Slack/WhatsApp/iMessage…) | ✅ **26–29** channels + device nodes |
| Robust job queue (backoff/retry/rate-limit) | ✖ Redis stream has **no consumer** | ◑ | ✅ cited as its hardest-to-copy strength |
| Business-system integrations | ✅ ClickUp/Zoho/Gmail + ~13 API providers | ◑ generic tools + MCP | ◑ generic tools + skills |
| **Skills system** | ✅ SKILL.md registry w/ authority+rollout metadata | ✅ SKILL.md (agentskills.io std) + Hub | ✅ SKILL.md + ClawHub (700+/2857) |
| **Self-improving skills** | ✖ (we self-*repair* broken repos, not self-improve) | ✅ **Curator** writes/prunes skills (headline feature) | ✖ |
| Public skill registry / hub | ✖ first-party only | ✅ Skills Hub | ✅ ClawHub (but ~12% malicious) |
| Persistent cross-session memory | ◑ mem0 + graphiti built, **default-OFF/inert** | ✅ SQLite + FTS5 + `MEMORY.md` + Honcho user model | ◑ plaintext + summarization (compaction loss) |
| **Multi-agent orchestration** | ✅ designed (but MAF Workflow engine **unused**) | ✅ orchestrator + typed-message sub-agents + Kanban | ✖ single-agent |
| **HITL / approvals** | ⚠️ Control Plane inbox exists; **Action Broker not in write path** | ✅ command-approval (smart/manual/off), fail-closed | ◑ DM pairing; host tools run un-gated |
| **Sandboxed code/tool execution** | ✖ agents load **in-process, unsandboxed**, deps into shared venv | ✅ Docker/Singularity/Modal/Daytona, `--cap-drop ALL` | ◑ main session on host; sub-sessions sandboxable |
| Permission / risk policy enforced | ⚠️ risk annotations exist but gate is a **no-op**; 0 tools flagged destructive | ✅ 7-layer, hardline blocklist, deny rules, SSRF block | ◑ policy cascade; NemoClaw adds out-of-process rails |
| Secrets / BYOK at rest | ✅ Fernet + PBKDF2 (480K) encrypted key store | ◑ local SQLite; PII redaction, secret masking | ◑ local config |
| **Eval / regression harness** | ✅ **17 golden trajectories + Inspect AI + Promptfoo** | ✖ none first-party (DSPy research repo only) | ✖ none |
| Distributed tracing / OTel | ⚠️ advertised but **disabled/no collector** | ◑ third-party (SigNoz/Langfuse) | ◑ third-party |
| Cost tracking | ◑ was silently $0 (now reports unknown) | ✅ per-turn cost + `/usage`/`/insights` | ◑ |
| Self-mutation / self-heal | ◑ Copilot Docker sandbox patches broken repos (partial reach) | ◑ skills self-heal during use | ✖ |
| Audit log | ✅ append-only (but sync on async loop) | ◑ structured logs | ◑ |
| Multi-tenancy / org RBAC | ⚠️ in build: default-deny auth SHIPPED (BO-2 closed); row-level multi-tenancy in flight as WS-29 (D15 — organization_id + RLS; H1 scratch-verified 2026-08-09) | ✖ single-user by design | ✖ single-user by design |

---

## 3. Layer-by-layer read

### 3.1 Agent runtime / foundation
- **Us:** A genuine, working MAF executor with 3-tier dispatch (native ChatAgent → gateway `/v1`,
  Copilot SDK interactive, batch shim). It's real code, not scaffold — but `run_agent_stream` is a
  ~1,600-line function and the whole executor is a ~4–5K-LOC god-file. We import MAF's
  `WorkflowBuilder`/`.as_tool()` but **never instantiate them** — so our "orchestration" is really
  tool-calling + history, not a graph engine.
- **Hermes:** A single unified `AIAgent` core shared across every surface (CLI, gateway, cron, ACP),
  concurrent tool exec via `ThreadPoolExecutor` (≤8 workers), context compression + SQLite persistence.
  Cleaner, more consistent, more reused.
- **OpenClaw:** Hub-and-spoke Gateway (TS/Node) multiplexing WS+HTTP on one port; standard ReAct loop;
  device "nodes" over WebSocket. Well-factored control-plane/agent split.
- **Verdict:** Ours is functionally comparable but structurally messier. Both competitors have a
  *single reused agent core*; we have a monolith. Their runtime discipline is worth emulating
  (our own audit files this as M1/M2).

### 3.2 Plumbing — channels, event bus, ops robustness *(our weakest layer)*
- **Us:** Email is our **only** real inbound messaging surface. The advertised "Redis Streams event
  bus" has **no consumer** — provider webhooks normalize inline but **no agent is actually triggered**
  by a real provider webhook. No WhatsApp/Slack/Telegram/Discord ingress exists despite doc hints.
- **Hermes:** One gateway process fronting 16+ chat platforms, one memory/state across all of them.
- **OpenClaw:** 26–29 channels **plus a job queue with automatic backoff, retry, rate-limit handling,
  and concurrent-job management** — repeatedly cited as its single hardest-to-replicate strength.
- **Verdict:** This is our biggest *implemented* gap. We don't need 29 channels, but we need
  **(a)** the event-bus consumer actually wired end-to-end, and **(b)** a real durable job queue with
  backoff/retry. OpenClaw's queue is the reference design.

### 3.3 LLM routing / BYO-LLM
- **Us:** Strong — in-process LiteLLM SDK, tiered routing, and a genuinely solid **Fernet+PBKDF2
  BYOK key store** (better secrets-at-rest than either competitor). Weak spots: tier→model defined in
  4 disagreeing places; cost was silently $0 for our exact tier models; local-model story is thin.
- **Hermes:** Best-in-class BYO-LLM: 300+ models, per-tool model assignment, Mixture-of-Agents, and a
  **strong local/on-prem narrative** (Ollama/LM Studio/llama.cpp + NVIDIA RTX/DGX Spark).
- **OpenClaw:** provider/model strings + failover; local models; NemoClaw bundles Nemotron.
- **Verdict:** We're competitive here and *ahead on secrets management*. Steal Hermes's **per-tool /
  per-agent model routing** and its **local-model ergonomics**; fix our tier-config fragmentation.

### 3.4 Skills
- **Us:** A real, structured **SKILL.md registry** with unusually good governance metadata —
  `authority`, `cost_tier`, `rollout_stage: shadow`, `success_rate_30d`, per-skill evals. This is
  *more disciplined* than either competitor's skill format.
- **Hermes:** SKILL.md on the **agentskills.io open standard**, ~166 skills, a Skills Hub, and the
  **Autonomous Curator** that grades/consolidates/prunes the library on a cycle.
- **OpenClaw:** SKILL.md + **ClawHub** (700+ marketed, 2,857 counted) — but a security audit found
  **~12% malicious skills**. Big ecosystem, weak curation.
- **Verdict:** Our *format* is the best of the three; our *ecosystem* is nonexistent (first-party only).
  Two things to learn: **(1)** adopt the **agentskills.io** convention so our skills interop with the
  wider ecosystem; **(2)** the biggest idea to steal is Hermes's **Curator** — auto-authoring and
  pruning skills — which is a natural extension of our self-mutation machinery (see §4).
  OpenClaw's malicious-skill problem is a *warning*, not a model: it validates our
  authority/rollout-gated approach.

### 3.5 Memory
- **Us:** Three real layers (mem0 episodic, graphiti bi-temporal KG, session cache) — but **all
  default-OFF and inert out of the box**, and `/v1/embeddings` returns a zero-vector when no
  OpenAI key is set (mem0 would store facts with no usable semantic search).
- **Hermes:** SQLite + FTS5 full-text search over past sessions + LLM summarization + a human-readable
  `MEMORY.md` the agent curates + **Honcho** cross-session user modeling. Works day one, one memory
  across all channels.
- **OpenClaw:** plaintext + summarization; suffers **context-compaction loss** on long sessions.
- **Verdict:** Our memory design is *more sophisticated* but *less real*. Hermes's lesson: a
  **simple, always-on, human-readable memory that works out of the box** beats a sophisticated one
  that's disabled by default. Turn ours on with a sane local-embeddings fallback; adopt a
  `MEMORY.md`-style human-auditable layer.

### 3.6 Multi-agent orchestration *(our category, our moat — but under-built)*
- **Us:** The whole platform is multi-agent by design (7 live specialists, dynamic per-event loading
  from independent repos). **But** we don't use MAF's Workflow/graph engine — orchestration is
  currently routing + tool-calling, not a coordinated planner/worker topology.
- **Hermes:** orchestrator spawns isolated sub-agents with scoped context, workers exchange **typed
  result objects** through a validated message layer, resource-aware concurrency limits, background
  sub-agents, and a **Kanban board** dispatcher. This is a genuinely good, lightweight orchestration
  design.
- **OpenClaw:** single-agent; no real orchestration.
- **Verdict:** This is *our* differentiator and OpenClaw simply isn't in the race. But **Hermes's
  orchestration is more built-out than ours** on the coordination mechanics. Steal its
  **typed-message passing + resource-aware concurrency + Kanban dispatch** model as we finally wire
  the MAF Workflow engine.

### 3.7 Security / permissions / HITL *(their strength, our biggest liability)*
- **Us — designed well, enforced poorly:**
  - Agents load **in-process, unsandboxed**, with dependencies `uv pip install`-ed into the shared
    gateway venv. This is the single biggest posture gap (audit BO-7, not started).
  - The permission gate `decide()` is called with only the tool *name* and **every branch returns
    approved** — a structural **no-op**. Fail-closed holds only where an author manually opts in.
  - We have a clean risk-annotation vocabulary (`read_only/destructive/idempotent/open_world`) but
    **zero tools are flagged destructive**, so the destructive path never fires.
  - The **Action Broker** (our approval-gated write executor) is real decision logic but **nothing
    imports it, and real ClickUp/email writes bypass it entirely** — violating our own
    non-negotiable #4.
- **Hermes — deep and honestly documented:** 7-layer defense-in-depth. Command approval with
  smart/manual/off modes, **fail-closed timeout→deny**, an always-on **hardline blocklist**
  (`rm -rf /`, fork bombs, `mkfs`, disk-zeroing `dd`), user deny-globs, SSRF blocking, PII redaction,
  secret masking, prompt-injection scanning, and container sandboxing with `--cap-drop ALL` +
  `no-new-privileges`.
- **OpenClaw — cautionary tale:** main session runs tools **on the host with full access** →
  "privacy nightmare," **CVE-2026-25253 (42K+ exposed panels)**, malicious skills. **NVIDIA NemoClaw**
  is the interesting response: **out-of-process policy enforcement** (rails live *outside* the agent
  process so the agent can't disable its own controls) + NeMo Guardrails + OpenShell sandbox.
- **Verdict:** **This is the highest-priority thing to learn.** Our security *design* is arguably
  better than OpenClaw's, but our *enforcement* is largely absent, and OpenClaw is a live case study
  in what that costs. Adopt Hermes's approval model wholesale and NemoClaw's out-of-process
  enforcement principle. Details in §4.

### 3.8 Observability & evals
- **Us:** **Evals are a genuine strength and a genuine differentiator** — 17 golden-trajectory tests +
  Inspect AI scenarios + Promptfoo golden cases, wired to CI (path-gated). **Neither competitor has a
  first-party eval/regression harness.** But our OTel is *advertised and disabled* (no collector, not
  even in the lockfile); our real telemetry is a bespoke Redis activity/cost feed.
- **Hermes:** per-turn cost, `/usage`/`/insights`, structured logs; OTel via third-party (SigNoz/Langfuse).
- **OpenClaw:** third-party OTel.
- **Verdict:** **Keep and market the eval harness — it's a moat.** Fix observability: either turn OTel
  on with a collector, or stop advertising it and lean on the (good) Redis feed + Langfuse (we already
  have Langfuse env keys wired).

### 3.9 Self-improvement / self-mutation
- **Us:** Copilot Docker sandbox patches a **structurally broken** agent repo and stages a commit for
  human approval. This is self-*repair*, and only reliably reached on the structural-failure path.
- **Hermes:** self-*improvement* — the Curator writes new reusable skills after complex tasks and
  prunes stale ones; skills self-heal during use.
- **Verdict:** We have unique, valuable machinery (a real mutation sandbox with a human gate — nobody
  else stages code fixes for approval). Hermes points at the next step: **extend the same sandbox from
  "fix broken repos" to "propose skill/prompt improvements from execution traces"** — still behind our
  human approval gate. That combination (self-improvement *plus* enterprise HITL) would be genuinely
  differentiated; neither competitor has both.

---

## 4. What to steal — prioritized, mapped to our own backlog

Ordered by leverage. Each ties to an existing audit item where one exists.

**P0 — Make our security real (this is what OpenClaw's CVEs are warning us about):**
1. **Sandbox dynamic agent execution** (audit **BO-7**). Today agents run in-process with full gateway
   privileges. Adopt Hermes's model directly: run loaded agent code in a **Docker sandbox with
   `--cap-drop ALL`, `no-new-privileges`, and resource limits** — we already run the *mutation* runner
   this way (`mutation_runner.py`), so extend that pattern to the normal load path in
   `packages/acb_skills/acb_skills/loader.py`.
2. **Wire the Action Broker into the write path** (audit **BO-1**, our top P0). The decision core in
   `apps/action_broker/broker.py` is sound but orphaned; real writes in `routes/tasks/providers.py`
   and `email_ingestion/providers/*` bypass it. Route every outward write through
   `broker.propose()`/registered handlers, add the `pending_actions` table, and bind it to the
   Control Plane approval inbox that already exists.
3. **Make the permission gate non-optional** (audit **M5 / BO-14**). Pass full tool *context* (not just
   name) into `permission_policy.decide()`, flag real tools `destructive: True` via the existing
   `tool_annotations` vocabulary, and — borrowing **NemoClaw's out-of-process enforcement** —
   evaluate the policy *outside* the agent's own tool-call surface so an agent can't route around it.
   Adopt Hermes's **hardline blocklist + fail-closed timeout→deny** as the default.

**P1 — Close the plumbing gap (OpenClaw is the reference):**
4. **Wire the event bus end-to-end** (audit **H7**). Build the missing `ingestion.worker` consumer so
   provider webhooks actually trigger agents. Add a **durable job queue with backoff/retry/rate-limit
   handling** modeled on OpenClaw's — this is its most-praised subsystem and directly fixes our
   dead-letter gap.
5. **Add real messaging channels** beyond email. We already have WhatsApp env keys stubbed; a
   channel-adapter layer (hub-and-spoke like OpenClaw's Gateway) fronting Slack + WhatsApp would match
   how our own users actually work, without chasing all 29.

**P2 — Level up agent & skill quality:**
6. **Actually use the MAF Workflow engine** (audit **M2**) and adopt Hermes's **typed-message
   sub-agent passing + resource-aware concurrency + Kanban dispatch** for coordinated multi-agent work.
7. **Turn memory on by default** with a **local-embeddings fallback** (fixes the zero-vector landmine)
   and add a human-readable `MEMORY.md`-style auditable layer per Hermes.
8. **Adopt the agentskills.io SKILL.md convention** so our (already best-in-class) skill format
   interops with the wider ecosystem, and prototype a **Curator** that proposes new skills from
   execution traces — behind our human gate, reusing the mutation sandbox.
9. **Fix observability honesty** (audit **BO-5**): either stand up an OTel collector or drop the claim
   and formalize the Redis feed + Langfuse we already have.

**Keep / defend (don't lose these — they're why we're not just a Hermes clone):**
- The **eval/regression harness** (neither competitor has one).
- The **source-of-truth mirror + approval-gated writes** governance model.
- The **encrypted BYOK key store** (better than either competitor's secrets handling).
- The **distributed agent-repo architecture** (independently versioned/tested/CI'd — more disciplined
  than fork-the-monorepo).
- The **human-gated self-mutation sandbox** (unique — nobody else stages code fixes for approval).

---

## 5. Honest scorecard

| Dimension | Winner | Why |
|---|---|---|
| Enterprise governance (RBAC, HITL, audit, source-of-truth) | **Metorite** (by design) | Neither competitor attempts it — but ours is partly designed-not-enforced |
| Eval / regression testing | **Metorite** | Only one of the three with a real harness |
| Secrets at rest | **Metorite** | Fernet+PBKDF2 BYOK store |
| Multi-agent orchestration (concept) | **Metorite**; (mechanics) **Hermes** | Our category, but Hermes's coordination is more built-out |
| Security *as implemented* | **Hermes** | 7-layer, fail-closed, sandboxed — we're designed-not-enforced |
| Local / on-prem model story | **Hermes** | NVIDIA RTX/DGX + Ollama/LM Studio/llama.cpp |
| Self-improving skills | **Hermes** | The Curator loop is a real differentiator |
| Persistent memory that works day-one | **Hermes** | Ours is more sophisticated but default-off |
| Channel breadth + ops robustness | **OpenClaw** | 26–29 channels + the best job queue |
| Skill ecosystem size | **OpenClaw** | ClawHub — though ~12% malicious |
| Out-of-the-box "it just works" | **OpenClaw / Hermes** | Ours needs infra + config before it does anything |
| Not being a security liability | **Metorite** (design) / **Hermes** (impl) | OpenClaw is the cautionary tale |

**Bottom line:** we're building the right thing for a harder problem, and on paper we out-design both.
The gap is execution on the foundation — security enforcement, channels/queue, and turning our own
inert-but-real subsystems on. The good news is we don't have to invent those solutions: Hermes and
OpenClaw have already proven them, and most map cleanly onto backlog items we've already identified
(BO-1, BO-7, M5, H7). The differentiators we *do* have — evals, governed writes, self-mutation with a
human gate — are exactly the things these viral personal agents structurally cannot offer a company.
