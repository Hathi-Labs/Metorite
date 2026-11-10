# Competitive Hardening — Learnings from Hermes Agent & OpenClaw

> ⚠️ **HISTORICAL RECORD (2026-08-10 consolidation, D26).** No work dispatches from
> this document — build log for the 2026-07-27 passes. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Status:** Planned (annealed into the backlog **(2026-07-13; 'no code yet' is stale — BO-20a/20f built 2026-08-02, BO-20b slice 1 2026-08-03; per-item state lives in FOUNDATION_BUILDOUT_CHECKLIST.md)**) · **Created:** 2026-07-13
> **Source:** [`/COMPETITIVE_COMPARISON.md`](../../COMPETITIVE_COMPARISON.md) — an evidence-based three-way
> comparison of Metorite against the two most-visible self-hosted agent platforms of 2026:
> **Hermes Agent** (Nous Research — self-improving personal autonomous agent) and **OpenClaw**
> (formerly Clawdbot — omnichannel personal assistant).
> **Companions:** [`harness_hardening_2026-07.md`](harness_hardening_2026-07.md) (the awesome-harness-engineering
> gap analysis — this doc is its competitor-sourced sibling), [`/FOUNDATION_BUILDOUT_CHECKLIST.md`](../../FOUNDATION_BUILDOUT_CHECKLIST.md)
> (the BO-item work tracker), [`multi_user_organization_research.md`](multi_user_organization_research.md).

This spec exists so the competitive findings are **actionable in the future**, not lost in a comparison doc.
It does not add much genuinely new work — most findings map onto backlog items we already have (`BO-*`, `HH-*`,
Phase-5 Annealer, WBS 3.3). Its job is to (1) attach a *proven external reference implementation* to each
of those items so whoever picks them up knows what "good" looks like, and (2) surface the handful of gaps the
comparison exposed that were **not** yet tracked.

---

## Verdict (category framing — read this first)

**Hermes and OpenClaw are not competitors in our category.** Both are *single-user personal autonomous agents*
you run for yourself and talk to from chat apps. Metorite is a *governed, multi-agent, source-of-truth-mirroring
orchestration platform for a company*. That reframing decides what we copy and what we ignore:

- **Copy their plumbing and their security engineering** — on the agent-loop / tool-execution / channel /
  sandbox layer they are more *implemented* than we are, because millions run them. That layer is exactly where
  our own audit is thin (unsandboxed loading, no-op permission gate, no messaging channels, dead observability).
- **Ignore their positioning** — neither has multi-tenancy, org RBAC, a system-of-record concept, team HITL
  approvals, or a real eval harness. Those are our reason to exist and genuine moats (several still
  *designed-not-enforced* in our code — see the "Defend" section).

We out-design both on paper; the gap is **execution on the foundation**, and the good news is we don't have to
invent the solutions — they've proven them.

---

## Gap → work-item map

Legend for **Our state**: ✅ real · ◑ partial/default-off · ⚠️ designed-but-not-enforced · ✖ absent

| # | Finding / gap | Proven reference (theirs) | Our state | Maps to |
|---|---|---|---|---|
| **CH-1** | **Security is designed, not enforced.** Agents load in-process unsandboxed; permission `decide()` is a no-op; 0 tools flagged destructive. | **Hermes** 7-layer defense: command-approval (smart/manual/off), **fail-closed timeout→deny**, always-on hardline blocklist, container sandbox `--cap-drop ALL`+`no-new-privileges`. **NemoClaw** out-of-process policy enforcement (rails live *outside* the agent process). | ⚠️/✖ | **BO-7**, **BO-14**, HH-2, HH-6 |
| **CH-2** | **Approval-gated writes bypassed.** Action Broker decision core is orphaned; real ClickUp/email writes skip it → non-negotiable #4 is false today. | **Hermes** routes every risky command through one fail-closed approval gate; single choke point. | ⚠️ | **BO-1** |
| **CH-3** | **Plumbing gap: event bus has no consumer; no durable job queue.** Webhooks normalize inline but trigger no agent; no backoff/retry/rate-limit. | **OpenClaw** job queue with automatic backoff, retry, rate-limit + concurrent-job handling — its single most-praised, hardest-to-copy subsystem. | ✖ | **BO-20** (new), H7 |
| **CH-4** | **Only one inbound channel (email).** No WhatsApp/Slack/Telegram ingress despite doc hints. | **OpenClaw** hub-and-spoke Gateway, 26–29 channels, one state across all. **Hermes** one gateway → 16+ platforms. | ✖ | WBS 3.3 (extend), **BO-20** (queue substrate) |
| **CH-5** | **Multi-agent orchestration under-built.** MAF `WorkflowBuilder`/`.as_tool()` advertised but the Workflow/graph engine is unused; sub-agent handoffs pass a bare string. | **Hermes** orchestrator + isolated sub-agents exchanging **typed result objects**, resource-aware concurrency limits, background workers, Kanban dispatch. | ◑ | **BO-12**, HH-7 |
| **CH-6** | **Memory is sophisticated but inert.** mem0 + graphiti default-OFF; `/v1/embeddings` zero-vector when no OpenAI key → facts with no semantic search. | **Hermes** SQLite + FTS5 + human-readable `MEMORY.md` the agent curates + Honcho user-modeling — works day one, one memory across channels. | ◑ | **BO-21** (new), llm_caching_memory.md |
| **CH-7** | **We self-*repair* broken repos but don't self-*improve*.** No skill auto-authoring/pruning loop. | **Hermes** Curator writes reusable skills after complex tasks + prunes on a cycle; DSPy/GEPA self-evolution research repo. | ✖ | **Phase-5 Annealer** (glossary) — informs its design |
| **CH-8** | **Observability advertised but disabled.** OTel off, no collector, not in lockfile; cost was silently $0. | **Hermes** per-turn cost tracking + `/usage`/`/insights`; both use third-party OTel (SigNoz/Langfuse). | ⚠️ | **BO-5**, HH-3 |
| **CH-9** | **Skill format is best-in-class but ecosystem-isolated.** SKILL.md with `authority`/`rollout_stage`/`success_rate_30d` — richer than either — but first-party only. | Both use SKILL.md; **Hermes** on the **agentskills.io** open standard + Skills Hub; **OpenClaw** ClawHub. | ✅/✖ | CH-7, skills/AGENTS.md |

---

## Work queue (priority order — mirrors COMPARISON §4)

Most of these **are not new items** — they are existing items with a competitive reference attached. Do not
re-scope the owning BO/HH item here; use this as the "what good looks like" note when you pick it up.

### P0 — Make our security real (OpenClaw's CVEs are the warning)
- **CH-1 → BO-7 (sandbox) + BO-14 (permission gate).** Extend the existing `acb-mutation-runner` container
  pattern (already used for mutation) to the *normal* agent load path in
  `packages/acb_skills/acb_skills/loader.py`; run with `--cap-drop ALL`, `no-new-privileges`, resource limits.
  Pass **full call context** (not just the tool name) to `permission_policy.decide()`, flag genuinely
  destructive platform tools via the existing `tool_annotations` vocabulary, and — the NemoClaw lesson —
  evaluate policy **out-of-process** so an agent can't route around its own gate. Adopt Hermes's
  **hardline blocklist + fail-closed timeout→deny** as the default posture.
- **CH-2 → BO-1 (Action Broker).** Route every outward write through `broker.propose()` → registered handler;
  add the `pending_actions` table; bind the Control Plane approval inbox. This is the top P0 already.

### P1 — Close the plumbing gap (OpenClaw is the reference design)
- **CH-3 → BO-20 (new).** Build the missing `ingestion.worker` consumer so provider webhooks actually trigger
  agents, and add a **durable job queue with backoff/retry/rate-limit + concurrency control** modeled on
  OpenClaw's. This is the substrate CH-4 needs too.
- **CH-4 → WBS 3.3 (bring forward the design, not necessarily the build).** A hub-and-spoke channel-adapter
  layer fronting Slack + WhatsApp (env keys already stubbed) — match how our users actually work; we need a
  handful of channels, not all 29. Depends on CH-3's queue.

### P2 — Level up agent, skill, memory quality
- **CH-5 → BO-12 + HH-7.** Actually instantiate the MAF Workflow engine for the multi-step pipelines it's
  advertised for; adopt Hermes's **typed-message sub-agent passing + resource-aware concurrency**.
- **CH-6 → BO-21 (new).** Turn mem0/graphiti on by default with a **local-embeddings fallback** (kills the
  zero-vector landmine), and add a human-readable `MEMORY.md`-style auditable layer per Hermes.
- **CH-7 → Phase-5 Annealer.** Our glossary already defines the Annealer ("mines successful run patterns,
  proposes new reusable skills as PRs, shadow→canary→full rollout"). Hermes's Curator + DSPy/GEPA is the
  reference: extend our self-mutation sandbox from "fix broken repos" to "propose skill/prompt improvements
  from execution traces" — **behind our human approval gate**. Self-improvement *plus* enterprise HITL is
  something neither competitor offers; this is the highest-differentiation item here.
- **CH-9 → skills interop.** Adopt the **agentskills.io** SKILL.md convention so our (already richer) format
  interops with the wider ecosystem. **Warning, not a model:** a ClawHub security audit found ~12% malicious
  skills — this *validates* our authority/rollout-gated approach; keep the gate on any community-skill intake.
- **CH-8 → BO-5 + HH-3.** Either stand up an OTel collector (Langfuse env keys already present) or drop the
  claim and formalize the bespoke Redis activity/cost feed. Add Hermes-style per-turn cost visibility.

---

## Defend — differentiators NOT to lose (why we're not just a Hermes clone)

These are things **neither competitor has**, and they are the platform's reason to exist. Protect them; several
are still *designed-not-enforced*, so "defend" partly means "finish."

- **The eval / regression harness** — 17 golden trajectories + Inspect AI + Promptfoo. *Neither competitor has
  a first-party eval harness.* This is a genuine moat — keep it green and blocking (see BO-17).
- **Source-of-truth mirror + approval-gated writes** — the governed-writes model (finish via BO-1).
- **Encrypted BYOK key store** (Fernet + PBKDF2) — better secrets-at-rest than either competitor.
- **Distributed agent-repo architecture** — each agent independently versioned/tested/CI'd, more disciplined
  than fork-the-monorepo.
- **Human-gated self-mutation sandbox** — nobody else stages code fixes for approval.
- **Multi-tenancy / org RBAC intent** (multi_user_organization_research.md) — the whole category they don't attempt.

---

## Status log

- 2026-07-13 — Spec created from `/COMPETITIVE_COMPARISON.md`. Findings annealed into the backlog: BO-1/BO-5/BO-7/BO-12/BO-14
  annotated with competitive references; **BO-20** (event-bus consumer + durable job queue) and **BO-21**
  (memory activation + local-embeddings fallback) added as new foundational items; Phase-5 Annealer flagged
  as the home for the self-improving-skills (Hermes Curator) learning. No code changes.
- 2026-07-27 — BO-7 progress, in two passes. **Cheap wins**: full call context (not just tool name) now
  reaches `permission_policy.decide()`; `install_dependency` flagged destructive via `tool_annotations`;
  the mutation-runner container got `--cap-drop ALL`/`no-new-privileges`/resource limits it was missing.
  **Dep-install RCE fix**: `loader.py`'s agent dependency install now defaults to `--only-binary=:all:`
  (wheels only) — closes the arbitrary-code-at-install-time gap that ran ahead of any tool-call gate.
  **Copilot-SDK session containerization (code_task)**: `code_task` sessions now route through
  `orchestrator/copilot_sandbox.py` when `copilot_sandbox_scope` includes `code_task` — containerizes just
  the `copilot` CLI binary as a TCP JSON-RPC server (`Dockerfile.copilot-sandbox`), reusing the mutation
  container's hardening flags; host-side orchestration/permission-handling is unchanged. Off by default,
  hard fallback to in-process on any spawn failure.
  **Copilot-SDK session containerization (App Workshop app-builder)**: same day, follow-up pass — the
  interactive-chat call site now wired too, via a new `executor._maybe_sandbox_session_workspace()` gated on
  the SAME `allow_session_workspace` hook `_session_workspace_override` already used (never a hardcoded
  agent name) plus `"app_builder"` in `copilot_sandbox_scope`. Uses `copilot_sandbox.get_or_spawn_sticky`'s
  per-`thread_id` container reuse — a fresh container every chat turn would have regressed the latency this
  path doesn't have today. The T2 build step (`node build_t2.mjs`, a host-absolute path baked into the
  agent's system prompt at import time) needed two extra read-only mounts: the app-builder's own repo
  mounted at the IDENTICAL container path (so the baked-in path resolves with zero prompt changes) and the
  shared T2 vendor cache mounted at a fresh path, pointed to via `CUSTOM_APPS_T2_VENDOR_DIR` (build_t2.mjs
  already reads this env var on the host) — `Dockerfile.copilot-sandbox` also picked up `nodejs` for this.
  **Still open**: the bulk of CH-1's own wording — `loader.py`'s in-process `importlib` agent load path
  itself, which neither pass touched. Row status stays ⚠️ until that lands.
