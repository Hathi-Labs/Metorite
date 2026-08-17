# Harness Hardening — Best-Practices Gap Analysis & Work Queue

> ⚠️ **HISTORICAL RECORD (2026-08-10 consolidation, D26).** No work dispatches from
> this document — HH-1..8 log; HH-6/7 deferred. The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Status:** In progress · **Created:** 2026-07-02
> Source: comprehensive comparison of the Metorite orchestrator against the practices catalogued in [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering) (agent loops, planning artifacts, context engineering, tool design, permissions, memory, evals, observability, HITL, sandboxing).
> Companions: [`archive/chat_implementation_review_2026-07.md`](archive/chat_implementation_review_2026-07.md) (streaming/HITL audit), [`llm_caching_memory.md`](llm_caching_memory.md) (caching plan), [`archive/email_tool_consolidation.md`](archive/email_tool_consolidation.md) (tool-surface plan).

## Verdict

Metorite already matches or exceeds the reference practices on **streaming/reconnect** (detached runs + Redis Streams relay + AG-UI), **HITL** (blocking ask tools, HITL-aware watchdog, mutation-approval inbox), **memory** (Mem0 episodic + Graphiti KG + active tools + NOTES.md working memory), and **planning artifacts** (AGENTS.md contract, ADRs, per-feature specs). The material gaps, ranked by impact:

| # | Gap | Best practice (list) | Our state |
|---|---|---|---|
| HH-1 | **Evals scaffold-only** | Eval gates block deployment; trajectory-level evals catch harness regressions unit tests miss | `skill-eval.yml` references an `evals/` dir that does not exist; both jobs `continue-on-error: true`; ADR-017 aspirational |
| HH-2 | **Fail-open confirmations** | Irreversible actions fail closed (OWASP LLM06 Excessive Agency); tool risk annotations (`readOnlyHint`/`destructiveHint`/…) inform permission decisions | `request_confirmation` auto-approves when no delivery channel exists; platform tools carry no risk metadata |
| HH-3 | **No observability backend** | Middleware hooks + trajectory logging + OTEL; LiteLLM ships OTEL callbacks for free | MAF telemetry disabled at startup (exported nowhere, crashed runs); Langfuse removed; no cache/token telemetry |
| HH-4 | **Context engineering unimplemented** | Prompt caching (explicit breakpoints), threshold-triggered compaction, token budgeting | Compaction is a manual route only; no token accounting; caching plan written but 0% implemented |
| HH-5 | **Tool-scope bypass** | Shrink tool space per agent (statewright: 2/10 → 10/10 from smaller tool sets) | email-assistant bakes ~63 tools into `Agent(tools=...)`, bypassing `tool_scope`; consolidation plan (63→~40) unimplemented |
| HH-6 | **No sandbox for normal runs** | Isolation as first-class primitive; intent-level authorization over allow-everything | Dynamic agents `importlib`-imported in-process in the gateway; Copilot agents run shell with `approve_all`; only self-mutation is containerized |
| HH-7 | **Untyped sub-agent handoffs** | Every handoff needs typed schemas + boundary validation | Handoffs pass a message string only; sub-agent injected-tool events un-namespaced in parent |
| HH-8 | **Stream-path duplication** | One event translator, one persistence owner | 4-way translation duplication — already diagnosed as P0-3 + strategic refactors in the chat review |

## Work queue (priority order)

### HH-1 · Make the eval harness real ✅→🔄
Create `evals/` with a golden trajectory set exercising the harness itself (not just skills): HITL round-trip, sub-agent spawn + tier propagation, reconnect replay, tool-failure recovery (ties to review P0-5), plus the existing Promptfoo/Inspect entry points CI already expects (`evals/promptfoo.yaml`, `evals/inspect/scenarios.py`). Un-scaffold `skill-eval.yml` (`continue-on-error: false`) once the runner is green. Golden cases that need a live LLM stay opt-in behind `LITELLM_BASE_URL`; structural/trajectory cases run offline against recorded fixtures.

### HH-2 · Fail-closed confirmations + tool risk annotations
- `request_confirmation` (`packages/acb_skills/acb_skills/ask_tools.py`): when no delivery channel exists, **deny** (return not-confirmed) instead of auto-approving, unless the call is explicitly marked reversible. Non-interactive automation that legitimately needs to proceed passes `non_interactive_default="approve"` only for reversible actions; destructive actions never auto-approve.
- Add MCP-style risk annotations (`read_only`, `destructive`, `idempotent`, `open_world`) as metadata on platform tools; surface them in the injected-tools addendum so agents and the permission layer can reason about risk.

### HH-3 · Telemetry with somewhere to export
- Enable LiteLLM OTEL callbacks (config-level) gated on `OTEL_EXPORTER_OTLP_ENDPOINT` being set.
- Record cache/token usage per call (`cache_read_input_tokens` etc.) in the existing `audit_event` path — groundwork for caching-plan Phase 1.
- Re-enable MAF instrumentation (`ENABLE_INSTRUMENTATION=1`) only after an OTLP endpoint exists; keep the kill-switch.
- Backend choice (self-hosted Langfuse vs OTLP→ClickHouse) deferred to Phase 5 hardening; the export path must be ready now.

### HH-4 · Automatic compaction trigger — ✅ already implemented (verified 2026-07-02)
Verification found this shipped, contrary to the initial survey: `AgentChat.tsx` auto-compacts at 80% of the model's **real** context window (75/80 hysteresis, between turns only, re-arms per fill-up and on session/model switch), using the compact route as the engine and a Claude-Code-style checkpoint model (`activeContextSlice` in `lib/tokenCount.ts` — only [summary + recent turns] are sent/counted; full transcript stays for scrollback). Copilot-SDK-runtime agents are deliberately excluded (the SDK compacts server-side natively). No work needed here; token budgeting proper (central accounting in the executor) stays with the caching plan.

### HH-5 · Enforce tool_scope on email-assistant — mechanism shipped 2026-07-02
The executor now supports `config.json: own_tool_scope` — the counterpart of `tool_scope` (platform-tool injection filter) for the tools an agent repo bakes itself. Applied via `_apply_own_tool_scope` before injection at all three build sites (main, streaming, sub-agent); no-match fails open with a warning, mirroring `tool_scope`. Email-assistant's actual narrowed subset is deliberately NOT set here — choosing which of the ~60 tools to drop/merge is [`archive/email_tool_consolidation.md`](archive/email_tool_consolidation.md)'s job; when that lands, declare the per-surface subsets via `own_tool_scope`.

### HH-6 · Sandbox normal agent runs (deferred — Phase 5)
Replace `PermissionHandler.approve_all` with an allowlist-based handler (near-term); longer-term run dynamic-agent code in the mutation-style container instead of in-process. Scheduled with the Phase 5 hardening pass alongside SEC-1.

### HH-7 · Typed sub-agent handoffs (deferred)
Namespace sub-agent injected-tool events in the parent stream; define a typed handoff payload (message + optional history slice + memory scope). Sequence after the chat review's strategic refactors to avoid churn.

### HH-8 · Stream-path unification (tracked elsewhere)
Owned by [`archive/chat_implementation_review_2026-07.md`](archive/chat_implementation_review_2026-07.md) (one event translator, one persistence owner, message-id-native protocol). Listed here only for completeness — do not duplicate work.

## Status log

- 2026-07-02 — Spec created; HH-1..HH-5 queued for immediate implementation, HH-6/7 deferred as noted, HH-8 tracked in the chat review.
- 2026-07-02 — **HH-1 shipped**: `evals/` created — shared promptfoo provider (`_runner.py`) + fixtures + top-level `promptfoo.yaml`, Inspect scenarios, and 20 offline trajectory tests (HITL round-trips, stream replay/reconnect invariants, delegation guards, LLM retry recovery); per-skill `cases.yaml` provider paths fixed (they pointed at a nonexistent depth); `skill-eval.yml` un-scaffolded — trajectories + inspect smoke now blocking, promptfoo gated on CI secrets.
- 2026-07-02 — **HH-2 shipped**: `request_confirmation` fails closed (`non_interactive_default="deny"`); `acb_skills.tool_annotations` registry (+`annotate` decorator) with MCP-style hints for all platform tools; email send tools annotated destructive; risk block rendered in both injected-tools addenda.
- 2026-07-02 — **HH-3 shipped**: LiteLLM OTEL callback auto-registers when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; per-call token+cache usage logged (`acb_llm.usage`), optional `audit_event` rows via `LLM_USAGE_AUDIT=1`; env knobs documented in `.env.example`. Backend selection still Phase 5.
- 2026-07-02 — **HH-4 verified already shipped** (see section above; initial survey was wrong).
- 2026-07-02 — **HH-5 mechanism shipped** (`own_tool_scope`; see section above).
