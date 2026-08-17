# 09 · LLM Routing with LiteLLM

A serious agent platform must not be married to one model provider. Prices change, a cheaper model
appears, a provider has an outage, or different tasks want different capability/cost trade-offs.
Metorite solves this with **LiteLLM** plus a thin routing layer (`packages/acb_llm`) that gives the
rest of the system *one* way to call *any* model. This chapter explains that layer: tiered routing,
context-window fitting, model fallback, BYOK, and cost control.

---

## 1. LiteLLM in one sentence

**LiteLLM is one OpenAI-compatible API for ~100 model providers.** You call `litellm.acompletion(model=
"anthropic/claude-…", messages=[…])` or `model="deepseek/deepseek-chat"` and it handles the
provider-specific HTTP, auth, and response-shape differences. Crucially, Metorite uses the **Python
SDK directly, in-process** — there is *no separate proxy container* to run and monitor. The gateway
imports LiteLLM and calls providers itself.

---

## 2. Tiered routing — the central abstraction

Application code never names a concrete model. It names a **tier**:

```
tier-fast      → cheap, fast     → classify, triage, cheap extraction
tier-balanced  → mid capability  → structured extraction, drafting        (the default)
tier-powerful  → top capability  → multi-hop reasoning, strategy, fallback
```

These aliases resolve through two hops at runtime (`acb_llm/client.py`):

```
tier-fast / tier-balanced / tier-powerful     (what app code says)
        │  _TIER_ALIAS_MAP
        ▼
tier1 / tier2 / tier3                          (internal tier ids)
        │  _TIER_MODEL   (live, mutable dict)
        ▼
deepseek/deepseek-chat, anthropic/claude-…     (concrete provider models)
```

The concrete mapping is **runtime-configurable**, loaded at startup from (in priority order): a Postgres
`model_config` table → a `tier_overrides.yaml` file → hardcoded defaults. A settings UI can call
`set_tier_model(tier_id, model)` to remap a tier **live, with no restart** — the next request reads the
updated dict.

Why this matters: **you can swap the model behind an entire class of work by editing one row.** Decide
"drafting is too expensive on Sonnet, move tier-balanced to DeepSeek" and every agent that drafts follows,
instantly, with zero code changes. This is ADR-005/008 — tiered routing from day one, for cost.

New models work without a release, too: `ensure_model_registered()` recognizes a known provider prefix
(`deepseek/`, `anthropic/`, `openai/`, …) and registers the model with LiteLLM on first use.

---

## 3. Fitting the context window

Cheap, fast models have *small* context windows (tier-fast ≈ 32K tokens vs. tier-powerful ≈ 200K). A long
email thread + rules + knowledge base can overflow the cheap model's input, and the provider rejects the
call. `acb_llm/context.py` handles this **before** the call:

```python
fitted, was_truncated = fit_messages_to_context(messages, model, max_output_tokens=1024, safety_margin=512)
```

- **Budget** = `context_window_for(model) − max_output_tokens − safety_margin`. The window comes from the
  tier table, else LiteLLM's model metadata, else a safe default.
- **Token counting** uses LiteLLM's `token_counter` for the model, falling back to a `chars/4 + 8/msg`
  heuristic.
- **Truncation strategy**: repeatedly find the *longest* message and trim its middle — keep the head (75%)
  and tail (25%) with a `[… content truncated …]` marker — until it fits. Short system instructions are
  preserved intact; the original list is never mutated (a copy is returned only when needed).

The point: **make the prompt fit the model you chose, rather than crashing or silently getting a provider
error.** Small windows become a *sizing* problem, not a *failure* mode.

---

## 4. Model fallback — resilience and confidence escalation

Fitting isn't always enough — the cheap model may still fail, or produce a low-confidence answer. The
helper `acompletion_with_fallback` wraps a primary call with an escalation to a stronger model:

```python
resp, used_model = await acompletion_with_fallback(
    model="tier-balanced",          # primary (cheap-ish)
    fallback_model="tier-powerful", # escalate here on failure
    messages=messages, max_tokens=700, temperature=0.3,
)
```

It fits the input to the primary's window, calls it, and **on any failure retries once on the fallback**
(re-fitting to the fallback's larger window). It skips the fallback if it resolves to the *same*
underlying model (no pointless second call), and returns which model actually answered.

Metorite layers a second trigger on top for the email assistant: **low-confidence escalation.** The
drafting model is prompted to emit a `NO_DRAFT` sentinel when it isn't confident; the caller detects that
(or an empty body) and re-runs on the more powerful `fallback_model`. So a draft escalates when the cheap
model (a) overflows even after compression, (b) errors, *or* (c) declines. The interactive **chat**
deliberately does *not* escalate — it stays on the assistant model for predictable cost/latency; fallback
is reserved for the deterministic, quality-sensitive paths (rule classification, drafting).

**General pattern:** *cheap-by-default, escalate-on-signal.* The signal can be an exception (overflow) or a
semantic one (the model says "I'm not sure"). Both are worth wiring.

---

## 5. BYOK and the `/v1` endpoint

The gateway exposes an **OpenAI-compatible** `POST /v1/chat/completions` (and `/chat/completions`). This
means *any* OpenAI SDK — the orchestrator's MAF client, the memory library, the mutation sandbox, even an
external script — can talk to the platform's router by pointing `base_url` at it. Internally it resolves
the tier alias, sanitizes messages for provider quirks (e.g. DeepSeek rejects an assistant message with
both null content and null tool_calls), and streams or returns the result.

**BYOK (Bring Your Own Key)** rides on this: a Copilot-SDK agent can be given a `provider` block
(`{type: "openai", base_url: <gateway>/v1, api_key: …}`) so its LLM calls route through the platform's
router and *your* keys, instead of the Copilot cloud backend. One endpoint, every model, your keys.

---

## 6. Keys and cost

- **Provider keys** live encrypted in Postgres (chapter 05 §4), decrypted into LiteLLM's config at startup
  (`_ensure_keys_loaded`). No keys in code, no keys in agent repos.
- **Cost control** is structural: tiered routing keeps cheap models on cheap work; context-fitting caps
  input size; prompt caching (Anthropic `cache_control` / OpenAI automatic caching on stable prefixes) cuts
  repeat-prefix cost. The architecture treats spend as a first-class design concern, not an afterthought.

---

## 7. What to steal

1. **Never let application code name a concrete model.** Route through tiers (or some semantic alias).
2. **Make the tier→model map runtime-editable.** The ability to remap a whole workload with one config
   change is worth a lot the first time a provider disappoints you.
3. **Fit before you call; fall back on failure.** Small windows and flaky cheap models become handled
   cases, not incidents.
4. **Expose an OpenAI-compatible endpoint.** It makes everything — SDKs, tools, BYOK — interoperate for
   free.

Next: **[10 · MCP & Connecting to External Apps](./10-mcp-and-integrations.md)**.
