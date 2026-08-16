# Foundation Architecture Review & Audit — Metorite

**Date:** 2026-07-11
**Scope:** Foundational infrastructure only — the AI orchestration platform (Microsoft Agent Framework), the LLM routing layer (LiteLLM), the shared `acb_*` packages, the gateway application shell, the event/ingestion plumbing, self‑mutation, observability, configuration, CI/CD, and infra. Application/business features (email assistant, tasks/GTD, sales, ClickUp/Zoho logic) are explicitly **out of scope** except where they reveal a foundational contract being violated.
**Method:** Read‑only static review of the whole tree, cross‑checked by six parallel deep‑dive passes (orchestration, LLM/LiteLLM, core packages, skills/tools, gateway/infra, deps/CI/observability) and independent verification of the highest‑severity claims. A green unit‑test baseline (`848 passed`) was established before any change.

> **Companion document:** `FOUNDATION_BUILDOUT_CHECKLIST.md` lists every missing / partial / unwired capability with rationale, dependencies, approach, and priority.

> **Update 2026-08-01 (doc-truth pass):** the findings below are a **2026-07-11 snapshot** and are preserved as the audit record — do not read them as current state. File paths predate the `apps/` → `apps/services/` restructure (e.g. `apps/gateway/` is now `apps/services/gateway/`). Current execution state is tracked in `FOUNDATION_BUILDOUT_CHECKLIST.md` and `project-docs/work_plan.md`. Note: "BO-4" in §5 corresponds to what the checklist tracks as **BO-20** (event-bus consumer + durable job queue).

---

## 1. Executive summary

Metorite's foundation is **substantially real, not a scaffold** — the executor, event translator, stream relay, watchdog, dynamic agent loader, Copilot streaming wrapper, LiteLLM routing, prompt‑cache/compression pipeline, and mutation Docker sandbox are all genuine, working implementations with real test coverage. This is a capable platform.

However, the audit surfaced a consistent and serious theme: **the platform's documented guarantees are materially ahead of what the code enforces.** The gap is concentrated in four areas that must be closed before more features are layered on top:

1. **Security / trust boundaries are not enforced.** The OpenAI‑compatible LLM endpoint is a fully open proxy (no auth, honours caller‑supplied `api_base`). Identity auth "never rejects" — `Depends(get_current_user)` only *labels* a caller, so mutation‑approval (which `git push`es code), the memory API (IDOR), and the agent‑dispatch webhook are reachable anonymously. Dynamic agent code runs in‑process with full gateway privileges and installs its dependencies into the shared venv (no sandbox).
2. **Load‑bearing "non‑negotiables" are unmet.** The Action Broker — "the only component allowed to write back … no autonomous writes until it is live" — is a **46‑line stub that nothing imports**, while real ClickUp/email writes already ship elsewhere. Self‑mutation **auto‑pushes** unreviewed commits (weak/empty‑test gate), contradicting the "a human must merge" model. `max_mutation_attempts = 1` is not enforced by any counter.
3. **Observability is largely aspirational.** "MAF native OTel (OTLP‑ready)" is **actively disabled** in the executor because nothing is wired to receive it; the OTLP exporter isn't even in the lockfile; there is no collector in infra. The real telemetry is a good, bespoke Redis activity/cost feed — but cost tracking silently reports **$0** for exactly the models the tiers use.
4. **Documentation drift and repo hygiene undermine trust in the whole.** The README still describes LangGraph / Eclipse Theia / PostgresSaver / an `escalation_ui` service that were all removed, and its architecture block is duplicated/garbled. A **live Zoho OAuth token** and a **1.7 MB production Postgres dump** are committed to git.

None of these are reasons to distrust the *engineering* — they are reasons to **spend a hardening sprint closing the doc↔code gap and the trust boundaries before scaling**. The rest of this report is the itemized basis for that sprint.

### Severity roll‑up

| Severity | Count | Representative findings |
|---|---:|---|
| **Critical** | 6 | Open LLM proxy (no auth + SSRF); anonymous mutation‑approve `git push`; Action Broker stub + writes bypass it; committed live Zoho token; committed 1.7 MB DB dump; memory API IDOR |
| **High** | ~18 | Non‑enforcing auth; unsandboxed in‑process agent exec; mutation auto‑push/weak gate; `max_mutation_attempts` unenforced; event bus has no consumer; cost tracking reports $0; OTel dead/disabled; `acb_schemas` fully dead; 3 DB engines; migrations not auto‑applied; README materially false |
| **Medium** | ~22 | 5,094‑line executor / 1,690‑line function; MAF workflow engine unused; tier→model defined in 4 disagreeing places; `Settings` god‑object + weak in‑code secret defaults; blocking sync audit writes on the async loop; duplicate migration #50; permission gate is a structural no‑op; empty destructive registry; broken `github_search` |
| **Low** | ~25 | Stale docstrings, duplicated helpers, dead deps, vestigial proxy YAML, `_test_byok_final.py`, doc version lags, etc. |

---

## 2. Architecture overview (as‑built)

**One‑sentence reality:** A FastAPI **gateway** receives chat / webhook / cron events, dynamically **git‑clones** the target `agent-<name>` repo (and its `skill-<name>` deps) and imports its `agents.py` in‑process via `importlib`, then runs it on one of **two** agent runtimes — native **MAF `ChatAgent`** (via an in‑process `OpenAIChatCompletionClient` pointed at the gateway's own `/v1`) or the **GitHub Copilot SDK** (`agent_framework_github_copilot`) — with the **LiteLLM SDK in‑process** (no proxy) doing tiered BYOK model routing; state/telemetry live in **Redis** (history, stream‑relay, activity/cost) and **Postgres** (chat sessions, audit, run traces, agent registry); on structural load failure a **Docker mutation sandbox** drives the Copilot SDK to patch the failing repo and open a review item.

```
Webhook / Cron / Chat
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ Gateway (FastAPI, apps/gateway)                             │
│  • 40 route modules (foundation ~10 + email 18 + tasks 12)  │
│  • lifespan: schedulers + fire-and-forget warmups           │
│  • /v1/chat/completions  ← in-process LiteLLM SDK           │
│  • /copilot/chat (AG-UI)  → orchestrator MAF agent          │
└───────────────┬─────────────────────────────────────────────┘
                │ importlib load_agent() (git clone + exec_module, IN-PROCESS)
                ▼
┌─────────────────────────────────────────────────────────────┐
│ Orchestrator (apps/orchestrator)                            │
│  executor.run_agent_stream — 3-tier dispatch:               │
│   Tier 1  native MAF ChatAgent  (OpenAIChatCompletionClient)│
│   Tier 1.5 GitHub Copilot SDK   (interactive; NOT sandbox)  │
│   Tier 2  batch shim fallback                               │
│  event_translator · watchdog · stream_relay · resolution    │
└───────────────┬─────────────────────────────────────────────┘
                │ on structural failure only
                ▼
┌─────────────────────────────────────────────────────────────┐
│ Mutation sandbox (docker run --rm, mutation_runner.py)      │
│  Copilot SDK patches the repo → commit → (AUTO-PUSH today)  │
└─────────────────────────────────────────────────────────────┘

Shared packages (packages/*):  acb_common (settings, structlog, Redis
activity/cost)  ·  acb_llm (LiteLLM client, tiers, BYOK key store, prompt
cache, compression, guardrails)  ·  acb_graph (sync SQLAlchemy + pgvector)  ·
acb_skills (loader, tool injection, permission policy, integrations)  ·
acb_memory (mem0 + graphiti + session cache)  ·  acb_audit  ·  acb_auth
(header-trust SSO)  ·  acb_schemas (DEAD)

Data plane:  Postgres 16 + pgvector  ·  Redis 7  ·  Neo4j (optional, graphiti)
Infra:  docker-compose (pg/redis/neo4j/langfuse/mutation); gateway + workbench
run as host systemd units behind Caddy (NOT in compose).
```

### Components reviewed

| Component | Path | Verdict |
|---|---|---|
| MAF orchestration / executor | `apps/orchestrator/orchestrator/executor.py` (5,094 LOC) | Real; monolithic; two runtimes; governance gaps |
| Agent factory / MAF wiring | `apps/orchestrator/orchestrator/agents.py` | Real; `WorkflowBuilder`/`as_tool()` advertised but unused |
| Event translation / streaming | `event_translator.py`, `stream_relay.py`, `watchdog.py`, `copilot_agent.py` | **Solid, well‑factored** |
| Self‑mutation | `mutation.py`, `mutation_runner.py` | Real sandbox; under‑gated; auto‑push |
| Dynamic loader | `packages/acb_skills/acb_skills/loader.py` (1,609 LOC) | Real; unsandboxed in‑process exec |
| LLM routing / LiteLLM | `packages/acb_llm/*` + `apps/gateway/gateway/routes/v1_compat.py` | Real SDK‑in‑process; open endpoint; cost=$0 |
| BYOK key store | `acb_llm/key_store.py` | Real Fernet encryption; broadcast to env |
| Config | `acb_common/settings.py` | One source of truth; god‑object; weak defaults |
| Logging / observability | `acb_common/_log.py`, `activity.py` | Structlog good; OTel dead; cost feed bespoke |
| DB / graph | `acb_graph/*`, `infra/postgres/*.sql` | Sync engine; 3 pools; 60+ ungoverned migrations |
| Memory | `acb_memory/*` | 3 layers real; overlap; all inert by default |
| Auth | `acb_auth/*` | Header‑trust; never rejects |
| Schemas | `acb_schemas/*` | **Dead — 0 production importers** |
| Gateway shell / infra | `apps/gateway/gateway/main.py`, `infra/*` | Boots; monolith; contracts unenforced |
| Action Broker | `apps/action_broker/action_broker/broker.py` (46 LOC) | **Stub; unimported** |
| Event bus / ingestion | `apps/ingestion/*` | Producer only; **no consumer** |
| CI/CD / evals | `.github/workflows/*`, `evals/*` | Eval harness real; gates mostly report‑only |

---

## 3. Findings by severity

Each finding cites `file:line`. Type ∈ {security, correctness, wiring‑gap, stub, dead‑code, tech‑debt, doc‑drift}.

### 3.1 CRITICAL

**C1 — `/v1/chat/completions` is an open, unauthenticated LLM proxy with an SSRF passthrough.** *(security)*
`apps/gateway/gateway/routes/v1_compat.py:146` `_handle_chat_completions(request: Request)` has **no auth dependency**; mounted plainly at `main.py:727‑730`; only CORS middleware is global. It bills the server's stored provider keys for any caller. Worse, it reads caller‑supplied `api_base = body.get("api_base")` and `api_key = body.get("api_key")` (`v1_compat.py:166‑167`, applied `:210‑213`) — an unauthenticated caller can aim the server at an arbitrary URL (SSRF/relay). Raw exception strings are returned to the client (`v1_compat.py:233,274`), leaking provider payloads. *Verified: every legitimate internal caller (MAF agents `agents.py:413`, Copilot BYOK `executor.py:1876`, mem0 `mem0_client.py:72`, graphiti `graphiti_client.py:65`, Next.js `suggestions/route.ts:39` & `compact/route.ts:92`) already sends `Authorization: Bearer <LITELLM_MASTER_KEY>` — so enforcing the internal token breaks nothing.* **→ fixed in this pass (F1).**

**C2 — Anonymous callers can approve self‑mutations and force‑push code.** *(security/governance)*
`apps/gateway/gateway/routes/agent.py` has **zero** `require_role` across its 22 endpoints. `POST /agent/mutations/pending/{commit_id}/approve` (`agent.py:1852`) runs `git push --force-with-lease` to `origin/HEAD`, guarded only by non‑enforcing `Depends(get_current_user)` → an anonymous `EMPLOYEE` can push code and defeat the "destructive actions FAIL CLOSED without a human" rule.

**C3 — The Action Broker (non‑negotiable #4) is a stub, and writes already bypass it.** *(stub/security)*
`apps/action_broker/action_broker/broker.py:1` "Phase‑0 placeholder"; `propose()` (`:34`) only records an audit row — no queue, no approval gate, no write executor. **Nothing imports it** (`grep` across gateway/orchestrator is empty). Meanwhile live writes already ship: ClickUp `POST` in `routes/tasks/providers.py:365`; outbound email in `email_ingestion/providers/{gmail,outlook,imap}.py`. Root `AGENTS.md:67` ("No autonomous writes to source systems until Action Broker is live") is therefore violated today.

**C4 — A live Zoho OAuth token is committed to git.** *(security)*
`.zoho_token_cache.json` is git‑tracked and contains `access_token: "1000.9659402d…"` (`expires_at 2026‑06‑17`). It is a real production credential in history; `.gitignore` has no `*token_cache*` rule. Must be **revoked** and purged from history, not merely deleted. **→ removed from tree + gitignored in this pass (F2); rotation + history purge flagged as owner action.**

**C5 — A 1.7 MB production Postgres dump is committed to git.** *(security/data‑leak)*
`acb_dump.bak` is a `PGDMP` custom‑format dump of DB `acb` (verified magic bytes) — almost certainly containing real CRM/entity/email data. Not in `.gitignore`. The `check-added-large-files --maxkb=1024` pre‑commit hook (`.pre-commit-config.yaml:53`) only inspects new additions, so it gave false assurance. **→ removed from tree + gitignored in this pass (F2); history purge flagged.**

**C6 — Memory API is an IDOR.** *(security)*
`apps/gateway/gateway/routes/memory.py:52‑108` (`/memory/{user_id}`, `/search`, `DELETE /{memory_id}`, `/add`) take `user_id` from the path and are guarded only by non‑enforcing `get_current_user`. An anonymous caller can read or delete **any** user's memories.

> C2/C6 share the same root cause as the auth story below (H1). **→ both are now gated in this pass (F7):** the state‑changing mutation routes and the whole `/memory` router require the internal bearer token (401 anonymous), after verifying every caller is the Next.js proxy that forwards it. The *systemic* fix (make `get_current_user` able to reject globally, cover the remaining routes) remains BO‑2.

### 3.2 HIGH

**H1 — Identity authentication never rejects; `Depends(get_current_user)` authenticates nothing.** *(security)*
`packages/acb_auth/acb_auth/deps.py:76` docstring: "Never raises — missing/wrong headers resolve to the lowest‑privilege role"; anonymous → `UserContext(email=None, role=EMPLOYEE)` (`deps.py:120`). Real enforcement requires `require_role(...)`, used on only a handful of routes. The internal bearer also falls back to `litellm_master_key` (`deps.py:56`), so an LLM key doubles as the service‑identity secret. This is the systemic root of C1/C2/C6.

**H2 — Dynamic agent code executes in‑process with full gateway privileges; deps install into the shared venv; no sandbox.** *(security)*
`loader.py:1247` `spec.loader.exec_module(module)` runs cloned‑repo code inside the gateway process (access to `os.environ` secrets, the PG session, the network). `_install_agent_deps` (`loader.py:1095`, called `:971`) `uv pip install`s the repo's declared deps into `sys.executable`'s venv — a malicious `requirements.txt`/`setup.py` runs arbitrary code at install time. `install_dependency` (`dep_tools.py:49`) exposes the same path as a live tool. Cross‑org clones are supported (`loader.py:1504`), widening the trust boundary. HH‑6 ("run dynamic code in the mutation‑style container") is deferred. This is the single largest security‑posture gap.

**H3 — Self‑mutation auto‑pushes unreviewed commits with a weak/empty test gate.** *(governance)*
`mutation.py:210` calls `_auto_push_commit` → `git push origin HEAD` with `reviewed_by="system:auto"` (`:225`) whenever `_tests_passed(test_summary)` is true — and `_tests_passed("")` returns **True** on empty output (`mutation.py:79`). "Sandbox success" is defined as "a commit was produced," not "tests passed" (`mutation_runner.py:151`). So a repo with no tests auto‑pushes any commit, unreviewed — contradicting README:13/77 and `AGENTS.md:66`.

**H4 — `max_mutation_attempts = 1` is not enforced by any counter.** *(wiring‑gap)*
`mutation.py:164` guards `if mutation_attempts >= MAX_MUTATION_ATTEMPTS`, but both call sites (`executor.py:2523,2569`) pass nothing, so the value is always `0` and `0 >= 1` is always False. There is no persistent per‑failure counter. The "one attempt" property is an emergent artifact of control flow, not an enforced limit.

**H5 — Self‑mutation on runtime errors is effectively unreachable; the streaming path never mutates.** *(wiring‑gap/dead‑code)*
In batch `run_agent`, `_self_anneal` (`executor.py:2554`) runs LLM‑recovery first and returns non‑None in all cases except when the recovery LLM call itself throws, so `attempt_self_mutation` at `:2569` is dead in practice. `run_agent_stream` (the primary chat path) has **no** `attempt_self_mutation` call at all. Only the structural‑incompatibility path (`:2523`) reliably mutates. The "self‑heals on failure" narrative is half‑wired.

**H6 — Copilot SDK is a primary interactive runtime, violating "MAF is the sole runtime / Copilot is mutation‑sandbox only."** *(governance/doc‑drift)*
`AGENTS.md:70,73` (#6/#9). In reality `GitHubCopilotAgent` drives full interactive runs (executor "Tier 1.5", `executor.py:2894‑3017,3307`); `agent-task-manager/agents.py:33` and `agent-apis-config/agents.py:16` build Copilot agents. Two runtimes coexist. (It is *branded* MAF via `agent_framework_github_copilot`, but the engine is the Copilot CLI.) This is a real architectural fork the docs deny.

**H7 — The "Redis Streams event bus" has no consumer; the webhook→agent flow is not wired.** *(wiring‑gap)*
`ingestion/queue.py:52` `xadd`s to `ingestion:*` streams, and `queue.py:16` promises a consumer via `python -m ingestion.worker` — **no such module exists** (`apps/ingestion/ingestion/` has only `queue.py`, `scheduler.py`, `sources/`). The real provider webhooks (`sources/clickup/webhook.py:72`) enqueue‑for‑audit then normalise inline via `BackgroundTasks` — **no agent is triggered**. The agent‑dispatch endpoint `/agent/webhook/{source}` (`agent.py:2522`) is a *separate*, unauthenticated, signature‑less path providers aren't configured to call. The two webhook systems are disconnected.

**H8 — Cost tracking silently reports $0 for the actual tier models.** *(correctness/observability)*
`acb_llm/client.py:315` `ensure_model_registered` registers unknown models into litellm's `model_cost` with `input/output_cost_per_token: 0`. The tiers route to models not in litellm's price map (`deepseek-v4-pro`, `‑flash`, `deepseek-reasoner`), so `_compute_cost` (`client.py:449`) finds the 0‑cost entry and returns `0.0` instead of `None` — dashboards show a confident **$0.00** for essentially all tier traffic. Spend tracking is non‑functional for the models in use. **→ fixed in this pass (F4).**

**H9 — OTel is dead: advertised, disabled in code, exporter not installed, no collector.** *(reality‑gap)*
`AGENTS.md:85` "MAF native OTel for observability (OTLP‑ready)". Reality: `executor.py:310‑347` calls `disable_instrumentation()` on every entry point because it "exports NOWHERE"; `acb_llm/client.py:346‑369` gates telemetry on unset `OTEL_EXPORTER_OTLP_ENDPOINT`; `opentelemetry-exporter-otlp` is **not in `uv.lock`**; `acb_common` declares `opentelemetry-api/sdk` but imports neither (dead deps); no collector/jaeger/tempo/prometheus in `infra/docker-compose.yml`. No traces are exported by any path today.

**H10 — `acb_schemas` is a dead package (0 production importers).** *(dead‑code)*
Referenced only by a comment (`acb_graph/models.py:4`) and one smoke test (`tests/unit/test_smoke.py:17`). Its models (`entities.py:20‑89`) duplicate the ORM field‑for‑field, are never used as a wire/API surface, and have already drifted (`Meeting.start/end` vs ORM/SQL `start_at/end_at`, `entities.py:75`). Either wire it in as the real API surface or delete it.

**H11 — Three independent DB engines; the foundational one is unconfigured.** *(tech‑debt/correctness)*
(1) `acb_graph/db.py:14` sync `create_engine` with only `pool_pre_ping` (no pool sizing → default 5+10). (2) `routes/tasks/core.py:114` async engine (10+20). (3) `routes/email/core.py:267` another async engine. Two bypass the "funnel all DB access through `acb_graph`" contract (`repo.py:1`). Additionally, `acb_audit.record()` is **synchronous** and is called directly from async orchestrator paths (`executor.py:2236,2301,…`), blocking the event loop per audit INSERT.

**H12 — 60+ raw numbered migrations, no framework, not auto‑applied, with a live number collision.** *(tech‑debt/correctness)*
`docker-compose.yml:31` mounts only `00_`/`01_` into init; everything `02+` reaches the DB only if `scripts/apply_migrations.sh` runs (deploy‑only) — a bare `docker compose up` yields a DB missing chat/email/tasks tables. The runner re‑runs *every* file every deploy, relying on hand‑written idempotency; there is no ledger, no down‑migrations, no checksum. Duplicate number **50** exists (`50_agent_run_trace.sql` + `50_gtd_item_origin.sql`), ordered by filename suffix. **→ collision fixed in this pass (F5).**

**H13 — README materially misrepresents the runtime, IDE, and topology.** *(doc‑drift)*
README still documents LangGraph (`README.md:13,38,95` incl. `PostgresSaver`/`StateGraph`), Eclipse Theia (`:113,138,231` — removed by ADR‑014), a non‑existent `apps/escalation_ui/` service (`:99,123`), and its layout block is **duplicated/garbled** (`:82‑143`). `AGENTS.md` is largely correct; the README is the wrong document. **→ rewritten in this pass (F3).**

### 3.3 MEDIUM

**M1 — The executor is a 5,094‑line god‑file; `run_agent_stream` is a single ~1,690‑line function** (`executor.py:2666‑4355`) containing the entire 3‑tier dispatch, watchdog, HITL, session store, and cleanup. This is the "complexity 223" function grandfathered in CI (`pyproject.toml:94`). *(tech‑debt)*

**M2 — MAF's Workflow engine is unused.** `WorkflowBuilder` is imported (`agents.py:25`) and advertised ("used for explicit multi‑step pipelines", `agents.py:12`) but **never instantiated**; `.as_tool()` is advertised (`:425`) but tools are built manually (`FunctionTool(...)`, `:368`). MAF is used as ChatAgent + tool‑calling + history only. *(doc‑drift/tech‑debt)*

**M3 — Tier→model is defined in four disagreeing places** (`client.py:36` defaults; `config.yaml:10`; `tier_overrides.yaml:1`; `settings.py:1495` comment). A fresh boot with an empty DB resolves via `tier_overrides.yaml` to flash/chat/reasoner — matching none of the other three. `_TIER_CONTEXT_WINDOWS` (`settings.py:1502`) is a second, already‑stale copy of what `context.py:71` computes dynamically. *(correctness/tech‑debt)*

**M4 — `Settings` is a ~200‑line god‑object** mixing runtime config, 8 legacy LLM keys (self‑labelled DEPRECATED, `settings.py:45`), and a dozen business‑integration credentials, with **weak in‑code defaults** that fail *open*: `gateway_session_secret="change-me-dev-only"` (`:59`), `litellm_master_key="sk-local"` (`:37`), a DB URL with an embedded password (`:28`). A deployment that forgets an env var runs with a known session‑signing secret rather than failing closed. *(tech‑debt/security)*

**M5 — The injected‑tool permission gate is a structural no‑op; the destructive registry is empty.** `_gate_injected_tool._gate()` calls `decide({"tool_name": name})` with only the name (`executor.py:118`); every branch of `decide` (`permission_policy.py:117‑181`) returns *approved* for a name‑only call. `TOOL_ANNOTATIONS` (`tool_annotations.py:21`) has **zero** `destructive: True` platform tools, so `risk_summary_block()`'s destructive line never renders and `is_destructive()` is always False. Fail‑closed holds *only* where an agent author manually calls `request_confirmation(non_interactive_default="deny")` — a convention, not a harness invariant. *(correctness/security)*

**M6 — Config drift in infra:** vestigial LiteLLM proxy files remain (`infra/litellm/config.yaml`, `tier_overrides.yaml`) despite `infra/AGENTS.md:4` "no proxy files remain", and they disagree with each other; a Langfuse container exists (`docker-compose.yml:90`) despite `infra/AGENTS.md:18` "No Langfuse container", with **no healthcheck**; weak default secrets baked into compose; Postgres `5432` published to host. *(config‑drift/security)*

**M7 — `agent_run.started_at` is never written.** `50_agent_run_trace.sql:24` defaults it to `now()`, but `run_trace._persist_row` upserts at run **end** inserting only `ended_at` (`run_trace.py:158`), so `started_at ≈ run end`; `observability.py:208` orders/filters by it. *(correctness)*

**M8 — Decrypted BYOK keys are broadcast into `os.environ`** (`key_store.py:363,414`) for the process lifetime — readable via any subprocess, crash dump, or `/proc/<pid>/environ`; plus an in‑memory plaintext cache (`:57`). Deliberate (litellm reads env) but the at‑rest guarantee ends there. The SQL comment claims "AES‑256‑GCM" (`08_provider_keys.sql:3`) but the implementation is Fernet (AES‑128‑CBC+HMAC). *(security/doc)*

**M9 — `github_search`/`github_repo_search` are broken.** They hit `api.github.com/search/code` with **no Authorization header** (`github_tools.py:15,85`) though `GITHUB_TOKEN` exists; the code‑search API requires auth (401/422), and they parse `text_matches` without the `text-match+json` Accept header so it is always empty. *(correctness)*

**M10 — Weak/misleading CI gates.** Blocking steps are only a correctness‑subset ruff (`--select F821,…`) and a grandfathered xenon (abs=F, i.e. cc≤223) (`pr-check.yml:47,63`); full ruff, mypy, and codebase‑health are `continue-on-error`. Evals are **path‑gated** (`skill-eval.yml:11`) so a PR to gateway/ingestion/reconciler runs **zero** evals; `deploy.yml:34` exposes `skip_tests`. README's "CI runs pytest + evals; merge when green" (`:252`) overstates coverage. *(tech‑debt)*

**M11 — `07_mem0_schema.sql` is a comment‑only non‑migration** (`:1‑8`) yet is executed every deploy and cited to operators as a real migration (`settings.py:210`). *(stub)*

**M12 — Duplicated gateway‑routing workaround** copy‑pasted between `mem0_client.py:112` and `graphiti_client.py:108` (embedding selection + gateway‑URL assembly); the shared `_gateway_env.py` helper only covers the env‑pop. *(duplicate‑logic)*

**M13 — `/v1/embeddings` silently returns a zero‑vector** when `OPENAI_API_KEY` is unset (`main.py:853`), so Mem0 stores facts with no usable semantic search — a silent correctness landmine. *(stub/correctness)*

**M14 — Role model incoherence:** `UserRole` has `executive|employee|agent` (`roles.py:20`) but the `app_user` CHECK allows only `('executive','employee')` (`09_app_user.sql:18`). *(correctness)*

### 3.4 LOW (selected)

- **L1** Stale "placeholder" docstrings on production code: `guardrails.py:1` ("Phase‑0 placeholder … WBS 0.8") though citation logic is live; `orchestrator/__init__.py:1` still titled "LangGraph + Deep Agents orchestrator"; `acb_graph/repo.py:4`, `acb_llm/client.py:644,689`, `acb_skills/integrations.py:25`, `acb_auth/deps.py:104` reference LangGraph. *(doc‑drift)* **→ swept in this pass (F6).**
- **L2** `_test_byok_final.py` (root, ad‑hoc, not under `tests/`) and `gateway.pid` are committed junk. **→ removed (F2).**
- **L3** Duplicate dep constraint `ddgs>=9.0` **and** `ddgs>=9.14.4` (`acb_skills/pyproject.toml`); `requires-python` floors disagree (2 members `>=3.11` depend on `acb-common` `>=3.12`); `AGENTS.md:78` says 3.11+, root is 3.12+. **→ ddgs + docstrings swept (F6).**
- **L4** Vestigial `provider_models_cache.json` (4,333‑line committed cache, `fetched_at 2026‑06‑17`), `enabled_models.json`, `tier_overrides.yaml` still git‑tracked and read as fallbacks after the DB migration (`35_model_config.sql`) that was meant to retire them.
- **L5** Duplicated helpers: `_find_uv` (`loader.py:998` + `dep_tools.py:31`); three workspace‑root resolvers (`note_tools.py:33`, `error_tools.py:24`, `permission_policy.py:91`); two `_split_frontmatter` (`registry.py:56` + `agent_md.py:56`); two tier‑alias maps (`client.py:44` + `v1_compat.py:30`); `AuditEvent` defined twice (dataclass `acb_audit/log.py:19` + ORM `acb_graph/models.py:192`).
- **L6** No engine/Neo4j `dispose()`/`close()` on gateway shutdown (`main.py:225`); `GraphitiClient.close()` (`graphiti_client.py:238`) never called. No pgvector ANN index on `message.embedding` (sequential scan).
- **L7** `resolver.resolve_with_llm` (`resolver.py:478`) is a deliberate no‑op; entity resolution is deterministic‑only in practice.
- **L8** Separation‑of‑concerns inversion: `acb_skills` tools import *up* from `orchestrator.executor` at call time (`ask_tools.py:150`, `agent_tools.py:79`), reversing the intended dependency direction.
- **L9** `query_history` "SELECT‑only" is naive substring filtering on a full‑privilege session (`history_tools.py:73`) — a crafted `pg_read_file()` passes; a column named `deleted_at` is falsely rejected.

---

## 4. What is genuinely solid (to keep a balanced picture)

- `event_translator.py`, `watchdog.py`, `stream_relay.py`, `copilot_agent.py` — careful, well‑factored, single‑source‑of‑truth code with trajectory‑eval backing.
- The LiteLLM **prompt‑cache / message‑compression / tool‑output** pipeline is real and correctly wired at the choke point; the cache‑byte‑stability rule (stable prefix + `CACHE_BREAK` sentinel, memory as suffix) is honored on the agent path (`executor.py:3434`, `prompt_cache.py:102`).
- **BYOK key store** uses authenticated Fernet with PBKDF2 (480k) — sound encryption at rest.
- **structlog** run‑correlation (`bind_run_context`/`get_run_context`) is clean and actually used, and the **Redis activity/cost feed** (`acb_common/activity.py`) is a genuine, cross‑app live‑telemetry substrate.
- The dynamic **loader** and the **mutation Docker sandbox** are real, non‑trivial implementations.
- The **eval harness** is real: 17 golden trajectory evals + Inspect scenarios + Promptfoo, and the harness rule "behaviour changes ship a trajectory eval" is honored.
- **86 unit test files**; foundation components (acb_llm, executor, acb_skills, prompt cache, HITL) are genuinely covered. Green baseline `848 passed`.

---

## 5. Recommendations (priority order)

1. **Close the trust boundaries (Critical).** Authenticate `/v1` (done, F1); make identity auth able to reject and gate mutation‑approve / memory / agent‑webhook (BO‑2); move dynamic agent execution into the mutation‑style container or a restricted subprocess (BO‑7).
2. **Reconcile the write‑path non‑negotiable (Critical).** Either land the Action Broker with a real queue + approval + executor, or route the existing ClickUp/email writes through an approval gate — and update the docs to whichever is true (BO‑1).
3. **Fix self‑mutation governance (High).** Remove auto‑push (require human approval), make "success" mean tests actually ran and passed, and enforce `max_mutation_attempts` with a persistent counter (BO‑3).
4. **Make observability real or stop advertising it (High).** Add the OTLP exporter + a collector to compose and re‑enable MAF/LiteLLM tracing, or delete the OTel deps and the "OTLP‑ready" claim; fix cost=$0 (done, F4) (BO‑5).
5. **Wire or delete the event bus (High).** Ship `ingestion.worker` and connect provider webhooks → agent dispatch, or remove the Redis‑Streams‑bus claim (BO‑4).
6. **Adopt a migration framework (High).** Alembic (or, minimum, an `applied_migrations` ledger + a CI unique‑prefix check) and auto‑apply on boot; fix the #50 collision (done, F5) and `started_at` (BO‑6).
7. **Pay down the executor monolith (Medium).** Extract the 3 tiers behind a `Runtime` strategy interface; ratchet the xenon ceiling down from F.
8. **Consolidate the LLM config (Medium).** One source of truth (DB) for tier→model + context windows; delete the vestigial proxy YAML/JSON.
9. **Delete dead code (Medium/Low).** `acb_schemas`, `build_graph`/`graph_module` LangGraph scaffolding, unused OTel deps, `ddgs` dup, `provider_models_cache.json`.
10. **Split `Settings` and fail closed on secrets (Medium).** Nested config groups; no usable in‑code defaults for signing/DB/master keys — raise if unset in non‑dev.

### Code to remove / refactor / reorganize (quick index)

| Action | Target |
|---|---|
| **Remove from repo** (done F2) | `.zoho_token_cache.json`, `acb_dump.bak`, `gateway.pid`, `_test_byok_final.py` |
| **Remove (dead code)** | `acb_schemas` package (H10); `loader.build_graph`/`_import_graph_module`/`graph_module` (F6.1); unused `opentelemetry-*` deps (H9); duplicate `ddgs` pin (done F6) |
| **Remove (vestigial)** | `infra/litellm/config.yaml` proxy directives, `tier_overrides.yaml`, `provider_models_cache.json`, `enabled_models.json` once DB is the source |
| **Refactor** | `executor.run_agent_stream` (3‑tier strategy split); `Settings` god‑object; collapse 3 DB engines → `acb_graph`; de‑dup `_find_uv` / workspace‑root / tier‑alias maps |
| **Reorganize** | Move email/tasks route trees out of the gateway monolith into their own app modules; move `activity.py` cost ledger out of "common" |

---

## 6. Fixes applied in this review pass

These were executed after the audit, each verified against the `848‑passing` baseline and committed separately. See `git log` on `claude/foundation-architecture-audit-ftur3x`.

| ID | Fix | Severity addressed | Risk |
|---|---|---|---|
| **F1** | Require the internal bearer token on `/v1/chat/completions` + `/chat/completions`; gate caller `api_base`/`api_key` behind an env flag (default off); stop leaking raw exception strings | C1 | Low — all internal callers already send the token (verified) |
| **F2** | Remove committed secrets/junk from the tree and add `.gitignore` rules (`*.pid`, `*.bak`, `*token_cache*.json`, `*_dump.bak`) | C4, C5, L2 | None (runtime‑inert files) |
| **F3** | Rewrite `README.md` to match reality (MAF‑only; no LangGraph/Theia/PostgresSaver/escalation_ui; fix duplicated layout) | H13 | None (docs) |
| **F4** | Fix cost tracking so unpriced models report *unknown* (`None`), not a false `$0.00` | H8 | Low — contained to `acb_llm/client.py`, covered by tests |
| **F5** | Resolve the duplicate migration number 50 | H12 (collision) | Low — idempotent SQL, renumbered only |
| **F6** | Sweep stale docstrings/comments (LangGraph/placeholder), remove duplicate `ddgs` pin | L1, L3 | None (docs/metadata) |
| **F7** | Gate the mutation approve/reject/remutate/delete routes and the whole `/memory` router on `require_internal_auth` (401 anonymous) | C2, C6 | Low — every caller is the Next.js proxy forwarding the internal token (verified); read‑only + public‑webhook routes untouched |
| **F8** | Self‑mutation stages for human approval by default: `_tests_passed("")`→False, auto‑push opt‑in via `MUTATION_AUTO_PUSH` (default off) | H3 | Low — strictly *more* conservative (matches the documented "human must merge" model); unit‑tested helpers |
| **M7** | Write `agent_run.started_at` at the true run start (was defaulting to ≈ run end, breaking `/observability` ordering) | M7 | Low — contained to `run_trace.py`, `now()` fallback preserves `NOT NULL`; covered by tests |
| **R1–R4** | Refactor: extract 4 cohesive concerns from the 5,094‑line `executor.py` (`_todo_tracker`, `_copilot_session`, `_tool_injection`, `_model_resolution`) → **4,069 lines**, each re‑exported (no importer changed) | M1 (partial) | Low — behaviour‑preserving whole‑function moves; full suite + trajectory evals green after each |
| **E1** | Reconcile the email‑assistant `own_tool_scope` with its post‑consolidation tools (pre‑existing failing eval) | (app‑scope drift) | Low — config + stale eval name; trajectory evals now fully green |

All fixes above are on `claude/foundation-architecture-audit-ftur3x`, each verified against the unit suite (grown to **859 passed** with the added regression + auth tests).

> **Owner actions that this pass deliberately did NOT perform** (they require rotation, history rewrite, or an architectural decision, and are unsafe to do unattended): revoke the leaked Zoho token and `git filter-repo` C4/C5 out of history; the **systemic** auth fix — make `get_current_user` able to reject / add a global gate — for the remaining unauthenticated routes (H1/BO‑2; note the two most dangerous specific endpoints, C2/C6, ARE now gated by F7); the Action Broker decision (BO‑1); moving agent execution to a sandbox (BO‑7); the persistent `max_mutation_attempts` counter (H4/BO‑3). All are itemized in the checklist.
