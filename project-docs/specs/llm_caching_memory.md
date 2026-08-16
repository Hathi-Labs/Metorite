# LLM Prompt Caching + Memory System — Development Plan

> **Status:** **Core implemented (2026-07-03)** — Phases 1–6 shipped; Phase 7 (email tool-surface reduction) is app-scoped and pending. See the "Implementation Status" section below.
> **Owner:** Metorite Core
> **Created:** 2026-06-17
> **ADR references:** ADR-008 (**implemented** 2026-07-03), ADR-012 (Phase 2 deferred)
> **WBS reference:** WBS 2.6 (semantic cache + token compression)
> **Related files:** `packages/acb_llm/acb_llm/prompt_cache.py` (NEW — the transform), `packages/acb_llm/acb_llm/client.py`, `apps/services/gateway/gateway/routes/v1_compat.py` (agent-traffic choke point), `apps/services/orchestrator/orchestrator/executor.py`, `apps/services/orchestrator/orchestrator/agents.py`, `packages/acb_memory/acb_memory/session_cache.py` (NEW), `apps/services/gateway/gateway/main.py` (prewarm), `apps/agents/agent-email-assistant/agents.py`
> **Companion plan:** [`specs/archive/email_tool_consolidation.md`](archive/email_tool_consolidation.md) — shrinking the tool surface (Phase 7 here depends on it)

---

## Implementation Status (2026-07-03)

> **Key architecture correction vs the original plan:** Metorite talks to
> providers through the **litellm SDK directly — there is NO LiteLLM proxy
> process** (`v1_compat.py` and `client.py` both call `litellm.acompletion`).
> So the plan's "LiteLLM proxy pre-call hook" (Phase 3.2) doesn't apply. The
> equivalent, and cleaner, design is a **single request-transform**
> (`acb_llm/prompt_cache.py::apply_prompt_caching`) called by BOTH completion
> paths right before `acompletion`. litellm 1.86.0 carries `cache_control` on
> OpenAI-format message content blocks + tool defs through to Anthropic
> (verified), so the transform stays provider-agnostic and lets litellm do the
> Anthropic translation. It is our own code → unit-testable, and covers 100% of
> paths (native-MAF `OpenAIChatCompletionClient`, Copilot SDK, orchestrator).

| Phase | Status | Where |
|---|---|---|
| 1 — Cache token observability | ✅ pre-existing (HH-3) | `client.py::_usage_stats`/`_emit_usage` already extract+log+audit `cache_read/creation_input_tokens` + OpenAI `cached_tokens` |
| 2 — OpenAI `prompt_cache_key` routing | ✅ done | `apply_prompt_caching` sets `prompt_cache_key` for OpenAI tiers; `complete*` accept `cache_key`; `v1_compat` derives it from `prompt_cache_key`/`user` |
| 3.1 — CACHE BREAK sentinel | ✅ done | `executor.py` memory-merge (MAF `instructions` + Copilot `system_message`); `agents.py::enrich_instructions_with_memory` |
| 3.2 — sentinel → cached system blocks | ✅ done (as the transform, not a proxy hook) | `apply_prompt_caching` splits the system message at the sentinel; stable block → `cache_control: ephemeral`, dynamic memory → uncached |
| 3.3 — tool-array `cache_control` | ✅ done | `apply_prompt_caching` marks the LAST tool for Anthropic tiers (caches the whole tools prefix — the first, highest-value block) |
| 4 — session-scoped memory | ✅ done | `acb_memory/session_cache.py` (injected-redis, layering-safe); wired into `agent.py` + `enrich_instructions_with_memory`; keyed on `thread_id`, 10-min TTL, `SESSION_MEMORY_CACHE=1` |
| 4.2 — Graphiti episode worthiness gate | ✅ done | `graphiti_client.py::_is_episode_worthy` (tier1, opt-in `GRAPHITI_EPISODE_FILTER=1`, conservative) |
| 5 — LiteLLM Redis exact-match cache | ✅ done (SDK-side) | `client.py::_init_litellm_cache` installs `litellm.cache` gated on `LITELLM_REDIS_CACHE=1`; per-call opt-in via `enable_litellm_cache=True` |
| 6 — cache warming at startup | ✅ done | `main.py::_prewarm_prompt_cache` (fire-and-forget, gated `PROMPT_CACHE_PREWARM=1`, warms only Anthropic-resolved tiers) |
| 7 — email tool-surface reduction | ⏳ pending | app-scoped (email-assistant), independent of the caching core — see `archive/email_tool_consolidation.md` |

**Env flags (all default OFF except session-memory, which is ON):**
`SESSION_MEMORY_CACHE` (default 1), `SESSION_MEMORY_CACHE_TTL` (600),
`LITELLM_REDIS_CACHE` (0), `LITELLM_REDIS_CACHE_TTL` (300),
`GRAPHITI_EPISODE_FILTER` (0), `PROMPT_CACHE_PREWARM` (0).

**Provider reality:** the default tier is **DeepSeek**, where explicit
`cache_control` is a no-op (DeepSeek does automatic context caching) — the
sentinel is simply stripped. The Anthropic explicit-cache path activates the
moment any tier is pointed at a `claude-…` model (e.g. via the Settings tier
picker or a per-agent model). The transform is correct for all three provider
classes today; the win materialises when Anthropic/OpenAI is in the tier.

**Tests:** `tests/unit/test_prompt_cache.py` (11), `tests/unit/test_session_memory_cache.py` (7), `evals/trajectories/test_prompt_cache_trajectory.py` (5, CI-blocking). Full suite: 566 unit + all trajectory evals green, no regressions.

---

## Why This Matters

Every agent request processes the full system prompt from scratch. For a typical Metorite agent call:

- **System prefix** (agent instructions + tool addendum): ~3,000–4,000 tokens — static, identical across all turns
- **Tool schemas** (function-calling agents): a **separate** static `tools` array sent every request, on top of the system prefix. Small for most agents, but the **email-assistant sends ~63 tool schemas ≈ 6,500 tokens** here. The original plan overlooked this (it assumed all tools are described inside the system prompt) — see the revised Phase 3.3 and the new Phase 7.
- **Memory context** (Mem0 + Graphiti): ~200–600 tokens — dynamic per user/query
- **Conversation history**: grows per turn

At current usage with Claude Sonnet 4.6 ($3/MTok input):
- Each 3,500-token system prefix costs **$0.0105** per request
- With Anthropic prompt caching, cache reads cost **$0.0003** (10% of base)
- **Saving: ~$0.0102 per cached request (~97% reduction on the stable prefix)**

Beyond cost, cache hits also reduce time-to-first-token by 30–80% for long system prompts.

---

## Current State Audit

### What Exists

| Component | Current Status | Cache-Friendly? |
|---|---|---|
| `_build_injected_tools_addendum()` in `executor.py` | LRU-cached (module-level), byte-stable | ✅ Content is stable |
| `_build_registry_block()` in `executor.py` | LRU-cached, only changes on gateway restart | ✅ Content is stable |
| Agent instructions from repo | Loaded once per clone, never changes mid-run | ✅ Content is stable |
| Memory context from Mem0 | Fetched fresh every request via semantic search | ❌ Changes per user + query |
| Memory context from Graphiti | Fetched fresh every request, time-stamped | ❌ Changes as new facts arrive |
| System prompt final assembly | Flat string concat — not structured blocks | ❌ Providers can't identify stable boundary |
| `acb_llm/client.py` `acompletion()` calls | No `cache_control` params, no `prompt_cache_key` | ❌ No explicit caching |
| Native-MAF function-tool schemas (email-assistant, 63 tools ≈ 6.5k tok) | Sent as a separate top-level `tools` array every request; static but uncached | ❌ Not cached (and not scoped) |
| Per-agent tool scoping (`executor._inject_agent_tools` / `config.json: tool_scope`) | Filters tools for Copilot/orchestrator agents; **email-assistant bypasses it** (bakes tools into `Agent(tools=list(_TOOLS))`) | ⚠️ Partial — email agent injects all 63 |
| LiteLLM proxy (infra) | No Redis cache configured | ❌ No proxy-level caching |
| Token tracking (gateway + workbench) | No `cache_read_input_tokens` / `cache_creation_input_tokens` | ❌ No cache visibility |

### What Already Works Accidentally

OpenAI and Anthropic do implicit server-side caching when the same byte-identical prefix is sent repeatedly. Because the stable prefix (instructions + tool addendum) is always assembled first and the LRU cache makes it byte-stable, **implicit KV-cache hits may already occur** for same-agent same-user consecutive turns within ~5 minutes. However:

- We have no telemetry to confirm this
- Any change in memory context (different query → different Mem0 results) changes the concatenated system string, making the prefix hash differ even if the stable part is identical
- We're not explicitly marking breakpoints, so Anthropic's 5-min TTL cache lifecycle is not guaranteed

---

## Architecture: How Provider Caching Works

### Anthropic

- Requires **explicit `cache_control: {"type": "ephemeral"}`** on content blocks in the `system` array
- System prompt must be a **list of content blocks**, not a flat string
- Minimum cacheable prefix: **1,024 tokens** (Sonnet 4.6, Haiku 4.5); 4,096 for some Opus models
- Cache prefix order: `tools` → `system` → `messages`
- Default TTL: 5 minutes (refreshed on each hit); 1-hour TTL available at 2× write cost
- Write cost: 1.25× base input token price; Read cost: 0.10× base input token price
- Up to 4 explicit breakpoints per request; automatic caching also available (top-level `cache_control` field)
- Cache is workspace-isolated (as of Feb 2026)

### OpenAI

- Caching is **automatic** for prompts ≥ 1,024 tokens — no code changes required
- Cache hit reflected in `usage.prompt_tokens_details.cached_tokens`
- Add `prompt_cache_key` to route requests for the same prefix to the same server → improves hit rate
- Default in-memory TTL: 5–10 min (up to 1 hour off-peak); extended 24h available for newer models
- Static content must be at the **beginning** of the prompt

### Key Rule (Both Providers)

> Place static content first. Place dynamic content (memory, history, user message) last. Mark the boundary explicitly where possible.

---

## Implementation Plan

### Phase 1 — Observability (Week 1, ~3 hours)

**Goal:** Measure current cache state before changing anything.

**1.1 — Cache token logging in `acb_llm/client.py`**

After every `acompletion()` call in both `complete()` and `complete_with_tools()`, extract and log cache usage from the provider response:

```python
usage = response.get("usage") or {}
cache_read = (
    usage.get("cache_read_input_tokens")                              # Anthropic
    or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")  # OpenAI
    or 0
)
cache_write = usage.get("cache_creation_input_tokens", 0)  # Anthropic only

_log.info("llm.completion",
          model=model,
          input_tokens=usage.get("input_tokens") or usage.get("prompt_tokens", 0),
          output_tokens=usage.get("output_tokens") or usage.get("completion_tokens", 0),
          cache_read_tokens=cache_read,
          cache_write_tokens=cache_write)
```

**1.2 — Pass cache stats through audit layer**

Extend `AuditEvent` (or the existing audit log payload in `acb_audit`) to include `cache_read_tokens` and `cache_write_tokens` as optional integer fields. The gateway's audit recording in the agent run path should pass these through.

**1.3 — Workbench cache badge (optional, defer to Phase 4)**

A small "Cache" pill in the chat response header showing `X tokens from cache`. Requires a new SSE event type or extension of the existing `STEP_FINISHED` event payload from the gateway → Next.js route → chat UI.

---

### Phase 2 — OpenAI Automatic Cache + Routing Key (Week 1, ~1 hour)

**Goal:** Maximise OpenAI cache hit rate with zero structural change.

OpenAI caching is already automatic. The only enhancement is adding `prompt_cache_key` so that all requests for the same agent are routed to the same server pool.

**2.1 — Add `cache_key` parameter to `acb_llm/client.py`**

```python
async def complete(*, tier, messages, ..., cache_key: str | None = None, **extra) -> str:
    if cache_key:
        extra["prompt_cache_key"] = cache_key
    response = await acompletion(model=model, messages=messages, ..., **extra)
```

Same change for `complete_with_tools()`.

**2.2 — Pass agent name as `cache_key` from call sites**

In `executor.py`, when the orchestrator calls `complete()` or `complete_with_tools()` for triage / extraction tasks, pass `cache_key=agent_name`.

For the mutation layer in `mutation.py`, pass `cache_key="mutation"`.

---

### Phase 3 — Anthropic Explicit Cache Breakpoints (Week 2, ~4 hours)

**Goal:** Explicit 10× cost reduction on the stable system prefix for all Anthropic model calls.

This is the highest-value change. The problem is that MAF's `GitHubCopilotAgent` and the LiteLLM proxy both receive the system prompt as a flat string — not the structured block list Anthropic requires. The correct fix is a **LiteLLM pre-call hook** that transforms the prompt transparently, so that all execution paths (MAF agent, direct `acb_llm` calls, orchestrator) benefit without touching MAF internals.

**3.1 — Sentinel marker in `executor.py`**

At the stable/dynamic boundary during system prompt assembly (~line 1750), change:
```python
_merged = f"{_existing}\n\n{_memory_context}"
```
to:
```python
_merged = f"{_existing}\n<!-- CACHE BREAK -->\n{_memory_context}"
```

This single-line change marks where the stable prefix ends. The sentinel is invisible to the LLM — it is consumed by the LiteLLM hook before the request leaves the gateway.

**3.2 — LiteLLM pre-call hook `acb_litellm_hooks.py`**

> **Update 2026-08-01 (doc-truth pass):** does not apply — no LiteLLM proxy process exists (see
> header); superseded by the SDK-level transform `acb_llm/prompt_cache.py::apply_prompt_caching`
> at the gateway choke points. Do not create `infra/litellm/acb_litellm_hooks.py`.

Create `infra/litellm/acb_litellm_hooks.py` (mounted into the LiteLLM container):

```python
def pre_call_hook(data: dict, ...) -> dict:
    """
    For Anthropic requests: if system contains '<!-- CACHE BREAK -->',
    split into two content blocks and add cache_control on the stable block.
    """
    model = data.get("model", "")
    if "anthropic" not in model and "claude" not in model:
        return data

    system = data.get("system")
    if not isinstance(system, str) or "<!-- CACHE BREAK -->" not in system:
        return data

    parts = system.split("<!-- CACHE BREAK -->", 1)
    stable_part = parts[0].rstrip()
    dynamic_part = parts[1].lstrip() if len(parts) > 1 else ""

    blocks = [{"type": "text", "text": stable_part,
               "cache_control": {"type": "ephemeral"}}]
    if dynamic_part:
        blocks.append({"type": "text", "text": dynamic_part})

    data["system"] = blocks
    return data
```

Register in `infra/litellm/config.yaml`:
```yaml
litellm_settings:
  callbacks: ["acb_litellm_hooks.pre_call_hook"]
```

**Important constraints:**
- The stable prefix must be ≥ 1,024 tokens. At ~3,500 tokens, this is well above the minimum for all current models.
- The sentinel must not appear in user-facing output. The hook consumes it before it reaches the model.
- Thinking blocks cannot be directly marked with `cache_control` — this hook only touches the `system` field, so thinking is unaffected.

**3.3 — Tool-definition caching (function-calling agents) — REVISED**

> **Update 2026-08-01 (doc-truth pass):** the "extend the Phase 3.2 LiteLLM hook" mechanics below
> do not apply — no proxy process exists (see header); shipped as part of the SDK-level
> `apply_prompt_caching` transform (marks the last tool for Anthropic tiers).

> The original plan assumed all Metorite tools are *described in the system-prompt addendum* — true for the orchestrator / Copilot-SDK agents. It is **not** true for native-MAF agents that pass real function-tools: the **email-assistant sends ~63 tool schemas (~6,500 tokens) as a separate top-level `tools` array on every request**, entirely outside the system prompt. None of it is cached today, and it's the single largest static block for that agent.

Anthropic caches in the order `tools` → `system` → `messages`, so the `tools` array is the FIRST cacheable block — caching it is the highest-value change for function-tool agents. Extend the Phase 3.2 LiteLLM hook to also mark the tool array:

```python
def pre_call_hook(data: dict, ...) -> dict:
    ...  # existing system-block split above
    tools = data.get("tools")
    if _is_anthropic(model) and isinstance(tools, list) and tools:
        # cache_control on the LAST tool caches the whole tools prefix
        last = tools[-1]
        (last.get("function") or last)["cache_control"] = {"type": "ephemeral"}
    return data
```

For the tool cache to actually hit:
- **Byte-stable, deterministically-ordered tool list.** The email agent's `tools=list(_TOOLS)` is static (good). If Phase 7's `tool_scope` is adopted, each scoped subset must be stable *per context* (same context → same ordered subset) or every context switch busts the cache.
- **No per-request mutation** of tool descriptions (no timestamps/ids injected into docstrings).
- **OpenAI** needs no code change — tools count toward its automatic prefix cache as long as they lead the request and are byte-stable.

**Provider dependency:** this only helps when the agent's tier resolves to **Anthropic** (explicit `cache_control`) or **OpenAI** (automatic). For a DeepSeek-backed tier, rely on the provider's own context caching + the LiteLLM Redis response cache (Phase 5). **Confirm the tier→provider mapping for the email-assistant** (`EMAIL_AGENT_MODEL` / per-account `chat_model`) before assuming a win — it determines whether 3.3 does anything for that agent.

Mind Anthropic's 4-breakpoint limit: tools + the split system prefix is already 2–3 breakpoints; leave headroom.

---

### Phase 4 — Session-Scoped Memory (Week 3, ~3 hours)

**Goal:** Make memory byte-stable within a session so the stable prefix + memory block together become cache-eligible across turns.

**The problem:** Mem0 uses semantic search — different queries against the same user's memory return different result sets. This means even if the stable prefix is cached, the combined `[stable prefix + memory]` block changes every turn → no cross-turn cache reuse on the memory portion.

**The solution:** Cache memory per session in Redis, fetched once at session start and reused for all turns in that session.

**4.1 — Session memory cache in gateway agent route**

In `apps/services/gateway/gateway/routes/agent.py`, before calling `get_memory_context()`:

```python
_mem_key = f"session_mem:{thread_id}"
_cached = await redis.get(_mem_key)
if _cached:
    memory_context = _cached.decode()
else:
    memory_context = await get_memory_context(user_id, message)
    if memory_context:
        await redis.setex(_mem_key, 600, memory_context)  # 10-minute session TTL
```

Apply the same pattern in `apps/services/gateway/gateway/main.py` for the orchestrator `/copilot/chat` path (`enrich_instructions_with_memory()`).

> **Update 2026-08-01 (doc-truth pass):** the `/copilot/chat` path is slated for retirement under
> `agent_architecture.md` §12 A1 (`work_plan.md` WS-8 — single runtime). Coordinate with that
> workstream before investing further in this path (decision D4 context).

**Tradeoff:** Memory does not update mid-session. This is acceptable because:
- Agents can call `recall_timeline(entity, query)` explicitly for fresh data when needed
- `add_memories_background()` runs async anyway; new facts are available in the next session
- A 10-minute TTL matches the LLM provider cache TTL, so the two systems expire together

**4.2 — Graphiti episode write filter (Week 3, ~2 hours)**

Currently every conversation turn triggers `add_episode()` in Graphiti, which bloats the knowledge graph with low-signal entries (greetings, status checks, confirmations) and degrades retrieval quality. This indirectly hurts memory stability: more noise → different semantic search results → different memory blocks → cache misses.

Add a quick LLM-based filter (tier1, ~50 tokens) before writing a Graphiti episode:

```python
async def _is_episode_worthy(messages: list[dict]) -> bool:
    """Return True if the exchange contains a named entity, date, commitment,
    or decision worth recording in the knowledge graph."""
    ...
```

Only call `add_episode()` when `_is_episode_worthy()` returns True. This is a semantic quality gate, not a performance gate.

---

### Phase 5 — LiteLLM Proxy Redis Cache (Week 1, ~1 hour)

> **Update 2026-08-01 (doc-truth pass):** the `infra/litellm/config.yaml` instructions below do
> not apply — no proxy process exists (see header). Shipped SDK-side instead:
> `client.py::_init_litellm_cache` installs `litellm.cache` (gated `LITELLM_REDIS_CACHE=1`,
> per-call opt-in `enable_litellm_cache=True`).

**Goal:** Exact-match prompt cache at the proxy layer — free wins for repeated identical calls (classification, triage, structured extraction).

Enable LiteLLM's built-in Redis cache in `infra/litellm/config.yaml`:

```yaml
litellm_settings:
  cache: True
  cache_params:
    type: redis
    host: redis          # existing Redis service in docker-compose.yml
    port: 6379
    ttl: 300             # 5 minutes — matches provider cache TTL
    mode: default_off    # opt-in per request only
```

Use `mode: default_off` so only explicitly opted-in calls are cached. Opt-in from `acb_llm/client.py`:

```python
async def complete(*, tier, messages, ..., enable_litellm_cache: bool = False, **extra):
    if enable_litellm_cache:
        extra["cache"] = {"no-cache": False, "no-store": False}
```

Good candidates for opt-in caching:
- Mutation layer prompt analysis (same error → same fix analysis)
- Triage / classification calls with identical or near-identical inputs
- Structured extraction on repeated webhook payloads

---

### Phase 6 — Cache Warming at Startup (Week 4, ~2 hours)

**Goal:** Eliminate the first-request cache-miss latency penalty for high-frequency agents.

Anthropic's `max_tokens: 0` API feature (available since Feb 2026) lets you write to the cache without generating output or paying for completions.

In `apps/services/gateway/gateway/main.py` startup event:

```python
@app.on_event("startup")
async def _prewarm_anthropic_cache():
    """Pre-warm the Anthropic KV cache for high-frequency agents at startup."""
    from orchestrator.executor import _build_injected_tools_addendum, _PULL_INSTRUCTIONS
    stable_prefix = _PULL_INSTRUCTIONS + _build_injected_tools_addendum()

    for model in ["claude-sonnet-4-6", "claude-haiku-4-5"]:
        try:
            await _anthropic_prewarm(stable_prefix, model=model)
            _log.info("cache.prewarm_complete", model=model)
        except Exception as exc:
            _log.warning("cache.prewarm_failed", model=model, error=str(exc))
```

The pre-warm fires `max_tokens=0` with the system prompt marked `cache_control: {"type": "ephemeral"}`. No response is generated; only the cache write is charged (1.25× for the first write, then reads are 0.10×).

For the 5-minute default TTL: schedule a background task to re-warm every 4 minutes.  
For agents that run less frequently than every 5 minutes: use `ttl: "1h"` (2× write cost) to avoid the warmup loop overhead.

---

### Phase 7 — Tool-surface efficiency: reduce before you cache (parallel track)

**Goal:** shrink and stabilise the tool payload itself, so there is less to cache AND tool-selection accuracy improves. Caching makes a big prefix *cheap*; this makes it *small*. Do both — they compound.

The email-assistant is the acute case: **63 tools, all injected every request, bypassing `tool_scope`** — a ~30× outlier vs every other agent (task-manager 2, apis-config 1, coding orchestrator ~16 + Copilot built-ins). Companion plan: [`specs/archive/email_tool_consolidation.md`](archive/email_tool_consolidation.md).

**7.1 — Consolidate similar tools (email-assistant): 63 → ~40.** 13 merges behind `action=`/`preset=` params — no capability loss, endpoints unchanged (‑~23 tools ≈ ‑2–3k tokens off the tool array). Ship per that plan's 3-phase rollout; the AG-UI card router (`EmailToolCards.tsx`) must change in lockstep (it keys on tool names).

**7.2 — Route the email agent through `tool_scope`.** The platform already filters tools per agent (`executor._inject_agent_tools` + `config.json: tool_scope`), added explicitly to fight the Berkeley Function-Calling "too many tools" degradation — but the email agent bakes tools into the native MAF `Agent(tools=list(_TOOLS))` and bypasses it. Define a small **core set** (read/label/archive/draft/send) and load specialist tools (rules, digest, knowledge, patterns) only when the conversation is about them. Keep each scoped subset **deterministically ordered** so it stays cache-eligible (see 3.3).

**7.3 — Trim the mega-schemas.** `update_assistant_settings` (21 params, 1,687-char docstring) and `create_rule` (17 params) dominate the tool array. Move exhaustive per-param docs into the docstring body; keep the one-line description. Same capability, fewer tokens on every request.

**7.4 — Move UI-triggerable actions out of the tool set.** `sync_account`, `get_unread_count`, `install_default_rules`, `reset_rules`, `process_past_emails` are usually button actions, not things a user asks the agent to do. If they already exist in the UI, they don't need to be LLM-callable — dropping them shrinks the surface with near-zero chat capability loss.

**Ordering vs. caching:** run 7.1–7.4 to shrink/stabilise the payload FIRST, then 3.3 to cache what remains. Consolidation also *helps* the cache — fewer, stable tools = fewer prefix invalidations. These items are agent-side and independent of the LiteLLM hook, so they can land in parallel with Phases 1–5.

---

## Risk Assessment

> **Update 2026-08-01 (doc-truth pass):** rows referencing "the LiteLLM hook" do not apply — no
> proxy process exists (see header); the equivalent risks are carried by the SDK-level
> `apply_prompt_caching` transform (unit-tested in `tests/unit/test_prompt_cache.py`).

| Risk | Severity | Mitigation |
|---|---|---|
| Sentinel marker leaking into LLM context | Medium | LiteLLM hook consumes it before the request leaves; add an assertion in hook tests |
| Anthropic cache miss due to < 1,024 token prefix | Low | Current prefix is ~3,500 tokens; add a token count assertion in tests |
| Email agent tier resolves to DeepSeek (not Anthropic/OpenAI) → Phase 3.3 is a no-op | Medium | Confirm `EMAIL_AGENT_MODEL` / per-account `chat_model` tier→provider first; if DeepSeek, rely on the provider's auto-cache + LiteLLM Redis (Phase 5) + the provider-independent Phase 7 payload reduction |
| `tool_scope` subset change busts the tool cache | Low | Keep each scoped subset deterministically ordered; cache is per-context, not per-turn — a context switch legitimately re-warms |
| Tool consolidation renames break the AG-UI card router / quick-action callers | Medium | Phase 7.1 must land card-router + `_register_agent_tools` changes in the same PR (see `archive/email_tool_consolidation.md` compat section) |
| Session memory stale mid-session | Low | Agents can call `recall_timeline()` explicitly; 10-min TTL is short enough for most sessions |
| LiteLLM hook breaks non-Anthropic calls | Low | Hook is gated on `"anthropic" in model or "claude" in model` |
| MAF internal format conflicts with structured system blocks | Medium | Option A (LiteLLM hook) avoids touching MAF at all; MAF sees a pre-transformed request |
| Graphiti episode filter false negatives | Low | Filter is conservative (passes anything uncertain); worst case is current behaviour |
| Pre-warm loop adds startup latency | Low | Pre-warm is fire-and-forget (`asyncio.create_task`); startup does not block |
| Cache workspace isolation (Anthropic Feb 2026 change) | None | Metorite uses a single API workspace — all agents share a pool, which is the desired behaviour |

---

## Expected Impact

| Change | Tokens Saved Per Request | Cost Change |
|---|---|---|
| Anthropic explicit cache (stable prefix ~3,500 tokens) | 3,500 tokens @ 10% on hit | **~90% reduction on system prefix** |
| Anthropic tool-array cache (email-assistant ~6,500 tokens) — Phase 3.3 | 6,500 tokens @ 10% on hit | **~90% reduction on the tool schemas** (only when tier→Anthropic/OpenAI) |
| Tool consolidation 63→~40 + docstring trim — Phase 7 | ~2–3k tokens off the tool array *before* caching | Smaller payload + better tool-selection accuracy; provider-independent |
| OpenAI `prompt_cache_key` routing | Higher hit rate on implicit cache | ~15–20% more cache hits |
| Session memory (10-turn session) | 9/10 turns reuse same memory block | Memory portion also cache-eligible cross-turn |
| Graphiti episode filter | Fewer writes → cleaner retrieval → stable memory | Indirect quality + retrieval cost reduction |
| LiteLLM proxy cache | 100% saving on exact-match repeated calls | Variable; useful for classification/triage paths |
| Cache warming | Eliminates cold-start penalty on first user request | Latency: −30–80% on first request TTFT |

**Rough monthly estimate** for 10,000 agent requests/month, 3,500-token stable prefix, Claude Sonnet 4.6:
- Without caching: 10,000 × 3,500 tokens × $3/MTok = **$105/month** on stable prefix alone
- With caching (80% hit rate): $105 × 0.2 (misses at 1.25× write) + $105 × 0.8 × 0.1 (hits at 0.1×) = **$29.40/month**
- **Saving: ~$75/month (~72%)** — purely on the stable system prefix

---

## Execution Order

> **Update 2026-08-01 (doc-truth pass):** the `infra/litellm` rows below do not apply — no proxy
> process exists (see header); superseded by the SDK-level cache at the gateway choke points.

```
Week 1, Day 1   Phase 1.1   Cache token logging in acb_llm/client.py         ~1h
Week 1, Day 1   Phase 5     LiteLLM Redis cache config (infra/litellm)         ~1h
Week 1, Day 2   Phase 1.2   Audit layer cache stats propagation                ~1h
Week 1, Day 3   Phase 2     OpenAI prompt_cache_key in acb_llm + executor      ~1h
Week 2, Day 1   Phase 3.1   Sentinel marker in executor.py memory injection    ~30m
Week 2, Day 1   Phase 3.2   LiteLLM pre-call hook + config registration        ~3h
Week 2, Day 2   Phase 3.3   Tool-array cache_control in the hook (native-MAF)  ~1h
Week 2, Day 2   Phase 3.3   Verify token count >= 1,024 + tools cached in tests ~30m
(parallel)      Phase 7.1   Email tool consolidation 63→~40 (own 3-phase plan) — see archive/email_tool_consolidation.md
(parallel)      Phase 7.2   Route email agent through tool_scope (core + specialist sets)  ~3h
(parallel)      Phase 7.3   Trim update_assistant_settings / create_rule docstrings ~1h
(parallel)      Phase 7.4   Drop UI-only actions from the email tool set        ~1h
Week 3, Day 1   Phase 4.1   Session-scoped memory in Redis (agent route)       ~2h
Week 3, Day 2   Phase 4.1   Same change for orchestrator /copilot/chat path    ~1h
Week 3, Day 3   Phase 4.2   Graphiti episode worthiness filter                 ~2h
Week 4, Day 1   Phase 6     Cache warming at startup (gateway main.py)         ~2h
Week 4, Day 2   Phase 1.3   Workbench cache stats badge (optional)             ~3h
```

Total implementation effort: **~18 hours** across 4 weeks.

---

## File Inventory

> **Update 2026-08-01 (doc-truth pass):** the `infra/litellm/*` rows below do not apply — no proxy
> process exists (see header). The file actually created was
> `packages/acb_llm/acb_llm/prompt_cache.py` (the SDK-level transform).

Files to create:

| File | Purpose |
|---|---|
| `infra/litellm/acb_litellm_hooks.py` | LiteLLM pre-call hook — sentinel → structured blocks |

Files to modify:

| File | Change |
|---|---|
| `packages/acb_llm/acb_llm/client.py` | Log cache tokens; add `cache_key` and `enable_litellm_cache` params |
| `apps/services/orchestrator/orchestrator/executor.py` | Insert `<!-- CACHE BREAK -->` sentinel at memory injection boundary; (Phase 7.2) `tool_scope` for the email agent path |
| `infra/litellm/acb_litellm_hooks.py` | (Phase 3.3) also add `cache_control` to the last entry of the `tools` array for Anthropic requests |
| `apps/agents/agent-email-assistant/agents.py` | (Phase 7) consolidate 63→~40 tools, trim mega-docstrings, drop UI-only tools, keep `_TOOLS` deterministically ordered — see `archive/email_tool_consolidation.md` |
| `infra/litellm/config.yaml` | Enable Redis cache; register hook callback |
| `apps/services/gateway/gateway/routes/agent.py` | Session-scoped memory cache in Redis |
| `apps/services/gateway/gateway/main.py` | Session-scoped memory cache for `/copilot/chat` path; startup cache warming |
| `packages/acb_memory/acb_memory/graphiti_client.py` | Episode worthiness filter |
| `packages/acb_audit/` | Add `cache_read_tokens` / `cache_write_tokens` fields to AuditEvent |

No new infrastructure required — Redis already exists in `infra/docker-compose.yml`.

---

## Testing Plan

For each phase:

1. **Phase 1 (Observability):** After deploying, run 5 consecutive requests against the same agent. Confirm `llm.completion` log lines appear with `cache_read_tokens > 0` on requests 2–5.

2. **Phase 2 (OpenAI routing key):** Use the workbench model picker to select an OpenAI model. Send 3 identical messages. Confirm `usage.prompt_tokens_details.cached_tokens` grows across turns in the gateway logs.

3. **Phase 3 (Anthropic explicit cache):** With `ANTHROPIC_API_KEY` configured, run 2 requests to the same agent. First request should show `cache_creation_input_tokens ≈ 3,500`. Second request should show `cache_read_input_tokens ≈ 3,500`. Verify sentinel string does not appear in any LLM response.

3b. **Phase 3.3 (tool-array cache):** Point a function-tool agent (email-assistant) at an Anthropic tier. First request → `cache_creation_input_tokens` includes the ~6,500-token tool array; second request → `cache_read_input_tokens` reflects it. Assert the `tools` array is byte-identical across the two requests (no per-request mutation).

4. **Phase 4 (Session memory):** Run a 5-turn conversation. Confirm Redis key `session_mem:{thread_id}` is set after turn 1 and not re-fetched on turns 2–5 (check Mem0/Graphiti call count in logs).

5. **Phase 5 (LiteLLM cache):** Trigger the same classification/triage flow twice. Second call should return from Redis cache (visible in LiteLLM logs as `cache_hit: true`).

6. **Phase 6 (Warm-up):** Restart the gateway. Confirm the startup log includes `cache.prewarm_complete` for each model before the first user request arrives. Verify first request shows `cache_read_input_tokens > 0`.

---

## DOX Update Required After Implementation

When each phase is implemented:
- Update `project-docs/system_architecture.md` ADR-008 status from "approved / unimplemented" to "implemented"
- Update `project-docs/project_plan.md` WBS 2.6 status (§6 Phase 2)
- Update `apps/services/orchestrator/AGENTS.md` to note sentinel marker convention
- Update `packages/acb_llm/AGENTS.md` (or create if absent) to document cache parameters
- Update `packages/acb_memory/` AGENTS.md to document session-scoped memory TTL contract
