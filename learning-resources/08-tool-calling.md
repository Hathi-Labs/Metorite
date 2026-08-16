# 08 · Tool Calling

Tool calling is how an LLM stops being a text generator and starts *doing things* — reading a database,
sending an email, calling another agent. It's the single most important mechanism in the whole platform,
and it's simpler than it looks. This chapter explains the universal pattern, then how Metorite
implements it.

---

## 1. The core idea

An LLM can only emit text. "Tool calling" is a convention layered on top: you tell the model, *"here are
some functions you may use, with these names and parameter schemas,"* and the model — instead of
answering in prose — emits a **structured request** to call one, like:

```json
{ "tool_call": { "name": "create_task", "arguments": { "title": "Follow up with Acme", "list_id": "42" } } }
```

Your runtime sees that, **actually runs `create_task(...)` in your code**, captures the return value, and
feeds it back to the model as a new message. The model then continues — maybe calling another tool, maybe
answering. That's the whole trick. The model never runs code; it *requests* calls, and your runtime is
the thing with hands.

```
        ┌──────────────────────────────────────────────────────┐
        │                                                      ▼
   [ LLM ] ──emits tool_call──▶ [ your runtime executes it ] ──result──▶ [ messages grow ]
        ▲                                                                      │
        └──────────────────── model reads result, decides next ◀──────────────┘
```

Every agent framework — MAF, LangChain, the OpenAI SDK, the Copilot SDK — is a different skin over this
exact loop.

---

## 2. Anatomy of a tool

A tool is three things bundled:

1. **A name** — `create_task`.
2. **A description + parameter schema** (JSON Schema) — this is *prompt engineering*, not just plumbing.
   The model decides *whether* and *how* to call the tool entirely from these words. A vague description
   yields a misused tool.
3. **An implementation** — the actual function that runs.

In MAF (and most Python frameworks) you often just hand it a typed function; the framework derives the
schema from the signature and docstring:

```python
async def create_task(title: str, list_id: str) -> dict:
    """Create a ClickUp task. `title` is the task name; `list_id` is the target list."""
    return await clickup.post(f"/list/{list_id}/task", {"name": title})
```

The type hints become the parameter schema; the docstring becomes the description the model reads. **Write
that docstring for the model, not for a human maintainer** — it is literally part of the prompt.

---

## 3. How Metorite provides tools

Tools reach an agent from two sources, combined at load time:

- **Agent-declared tools** — functions the agent repo defines in its `agents.py` and passes to `Agent(...,
  tools=[...])`. These are the agent's specialty (e.g. the task agent's ClickUp functions).
- **Platform-injected tools** — a shared toolbox the runner adds to every agent (`_inject_agent_tools`
  in the orchestrator, sourced from `acb_skills`). These give *every* agent baseline superpowers:

| Category | Tools | Purpose |
|---|---|---|
| Agent delegation | `call_agent`, `call_agents_parallel`, `call_agent_background` | Multi-agent orchestration (chapter 06/07). |
| Web | `web_search`, `fetch_page` | Read the open web (DuckDuckGo + a reader). |
| Memory | `remember`, `recall_timeline`, `save_memory`, `save_episode` | Read/write the memory layers (chapter 07). |
| Files/artifacts | `write_artifact`, `share_artifact` | Produce user-visible outputs (chapter 13). |
| Human-in-loop | `ask_questions`, `ask_user` | Pause and ask (rendered as UI cards). |
| Code | `get_errors`, `install_dependency` | Self-check / self-repair support. |
| Data | `query_history`, `github_search`, `manage_todo_list` | Structured reads + a live task panel. |

The injection is model-shape-aware: MAF-native agents get functions appended to `agent.tools`; Copilot-SDK
agents get them normalized to the SDK's tool format plus a system-prompt addendum describing them.

---

## 4. Tool *scoping* — less is more

A counterintuitive but load-bearing lesson: **injecting every tool into every agent makes agents worse.**
When a model has 30 tools, it more often picks the wrong one or hallucinates arguments. So an agent's
`config.json` can declare a `tool_scope` whitelist, and the runner injects *only* those:

```json
{ "name": "task-manager", "tool_scope": ["call_agent", "web_search", "manage_todo_list"] }
```

Treat the tool set as a **curated menu per agent**, not a global buffet. This is a real accuracy lever,
not just tidiness.

---

## 5. Executing tools safely

Between "the model asked" and "the result comes back" sit three guardrails the platform enforces — and
that you'll want in any serious system:

1. **Timeouts.** Every tool runs under `asyncio.wait_for(..., timeout=300s)`. A tool that hangs (network
   stall, waiting on input) can otherwise freeze the entire agent run. A hung tool becomes a clean error,
   not a dead stream.
2. **Result truncation.** Tool (and sub-agent) outputs are capped (~8000 chars) before re-entering the
   model's context. One verbose API response shouldn't blow the context window or the cost.
3. **Approval gating for writes.** Tools that mutate the outside world don't just do it — they route
   through the HITL approval queue (chapter 06 §6). The tool-call *loop* is uniform; the *governance* of
   individual tools is where "safe" lives.

Every tool call is also streamed to the UI (`TOOL_CALL_START` → `TOOL_CALL_ARGS` → `TOOL_CALL_RESULT`) so
the user sees exactly what the agent invoked and what came back — transparency and audit in one.

---

## 6. Tools you don't have to write: MCP

You don't have to hand-code every integration as Python tool functions. The **Model Context Protocol**
(chapter 10) lets you point an agent at a *server* that already exposes an app's operations as tools. A
ClickUp MCP server turns ClickUp's whole API into agent-callable tools with no bespoke code on your side.
Mechanically, MCP tools enter the same loop as native tools — the model calls them identically; only the
*transport* differs. MCP is "tool calling with a standardized backend."

---

## 7. The pattern to remember

> **Advertise functions (name + schema + description) → the model requests calls → your runtime executes
> and returns results → the model reads them and continues.** Everything else — frameworks, MCP,
> multi-agent delegation, generative UI — is a specialization of this one loop.

If you internalize this, no agent framework will surprise you: they all reduce to *how they let you
declare tools* and *how they run the loop*. MAF is one such framework; the next chapters cover how it, the
model router, and MCP fit together.

Next: **[09 · LLM Routing with LiteLLM](./09-litellm-routing.md)**.
