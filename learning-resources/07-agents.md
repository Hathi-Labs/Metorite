# 07 · How an Agent Works

Strip away the orchestration and an "agent" is a small, precise idea: **an LLM in a loop that can call
tools.** This chapter dissects that idea — the anatomy of an agent, the run loop, and how Metorite
expects agents to be structured — so you understand what's actually happening inside the "RUN" box of the
core loop.

---

## 1. Anatomy of an agent

An agent is four things bound together:

| Part | What it is | In Metorite |
|---|---|---|
| **Instructions** | The system prompt — identity, goals, rules, tone. | An `instructions.md` / built system prompt per agent. |
| **Tools** | Functions the model may call to read/write the world. | Python functions declared by the agent + platform tools injected at load (chapter 08). |
| **A model** | The LLM that does the reasoning. | A tier alias (`tier-balanced`) resolved via LiteLLM (chapter 09). |
| **Context/memory** | What the agent knows beyond the prompt. | Context providers: conversation history (Redis), episodic memory (Mem0), the entity graph. |

In MAF terms, that's literally the constructor:

```python
from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

def build_agents() -> list[Agent]:
    client = OpenAIChatCompletionClient(
        base_url="http://127.0.0.1:8080/v1",   # the platform's own gateway (LiteLLM)
        api_key="sk-local",
        model="tier-balanced",                  # a tier alias, not a raw model id
    )
    return [Agent(
        client=client,
        name="task-manager",
        instructions="You manage ClickUp tasks. …",
        tools=[create_task, list_tasks],        # plain async functions
        context_providers=[...],                # optional memory
    )]
```

**The contract every agent repo must satisfy:** export a `build_agents() -> list[Agent]` function. That's
the entire interface between the platform and an agent. The loader (chapter 06) calls it; everything else
is up to the agent author. This tiny, stable contract is what lets agents live in separate repos and
evolve independently.

---

## 2. The run loop — what "an agent runs" means

When the runner calls the agent, this loop executes (MAF drives it; the model provider does the thinking):

```
1. Build the message list:  [system: instructions] + [context/memory] + [user/event message]
2. Call the LLM, advertising the available tools (their names + JSON-schema parameters).
3. The LLM responds with EITHER:
      (a) a final text answer            → done, return it, OR
      (b) one or more tool calls         → "call create_task({title: 'X'})"
4. If (b): the runtime executes each tool, captures the result, appends it to the messages.
5. Go to 2 — the LLM now sees the tool results and decides the next step.
   Repeat until the LLM returns a final answer (or a step/time/budget limit is hit).
```

That loop — **think → call tool → observe result → think again** — *is* an agent. Everything else
(streaming, memory, multi-agent) is elaboration. Chapter 08 zooms into step 2–4 (the tool-call
mechanics); this chapter stays at the loop level.

Each pass streams out as it happens: reasoning tokens, the tool-call name/args, the tool result, and the
final answer all become AG-UI events (chapter 13), so the user watches the agent think in real time.

---

## 3. Context & memory — what the agent knows

The prompt alone is stateless. Agents get richer knowledge through **context providers**, each a
different scope (this is the memory architecture from `reference.md`):

| Layer | Store | Scope | When to use |
|---|---|---|---|
| In-process session | Python dict | One run | Scratchpad for a single event. |
| Conversation history | Redis (`RedisHistoryProvider`) | One chat thread, survives restart | "What did we say earlier in this chat?" |
| Entity graph | Postgres + pgvector | Durable company facts | "What is true about Customer X?" |
| Episodic memory | Mem0 | Cross-run learned facts per agent/user | "What has this agent learned over time?" |
| Bi-temporal KG | Graphiti + Neo4j (optional) | Time-aware entity timeline | "How did this entity change over time?" |

The important design idea: **memory captured in one surface benefits all others.** A preference the user
mentions in chat is extracted into Mem0 after the session, then injected into *background* agent runs
later — so a delivery agent scheduling a notification already knows the user prefers WhatsApp over email.
Memory is best-effort and fail-open: if the memory service is down, the agent still runs, just with less
context.

---

## 4. Single agent vs. multi-agent

Most work is one agent looping. When a task spans specialties, agents compose (chapter 06 §5):

- **Delegation-as-tool** (the common path): the agent calls `call_agent("sales", "summarize the Acme
  pipeline")`; the sub-agent runs its *own* loop and streams progress back, tagged by agent name.
- **Declarative topologies** (MAF builders): `HandoffBuilder` (triage → specialist), `ConcurrentBuilder`
  (fan-out/fan-in), `GroupChatBuilder` (agents debate).

The mental model: a multi-agent system is just agents whose *tools* happen to be *other agents*. There's
no new primitive — it's the same run loop, nested.

---

## 5. What makes an agent *good* (hard-won practicalities)

The Metorite codebase encodes several lessons that transfer to any agent you build:

1. **Scope the tools.** More tools ≠ better. Past ~a dozen, model accuracy at *choosing* the right tool
   degrades. Agents declare a `tool_scope` whitelist so each gets only what it needs.
2. **Cap tool runtime.** A hung tool (infinite loop, waiting on stdin) blocks the whole run. Tools run
   under a timeout (`COPILOT_TOOL_TIMEOUT_SECONDS`, default 300 s).
3. **Truncate what flows back.** Sub-agent and tool results are capped (~8000 chars) before re-entering
   the parent's context — otherwise one chatty tool blows the context window.
4. **Fit the context, then fall back.** Before a call, messages are trimmed to fit the model's window;
   if the cheap model still can't cope (or isn't confident), escalate to a more powerful one. This is the
   fallback-model machinery in `acb_llm.context` (chapter 09).
5. **Give the agent an escape hatch to ask.** The `ask_questions`/`ask_user` tools let an agent stop and
   request clarification instead of guessing — surfaced as an interactive card (chapter 13).

---

## 6. The agent repo layout (the authoring contract)

Because agents live in their own repos, there's a compatibility contract. A well-formed `agent-<name>`
repo contains:

```
agent-<name>/
  config.json        # model tier, required integrations (by NAME), skill repos, tool_scope, triggers
  agents.py          # exports build_agents() -> list[Agent]   ← the one required interface
  instructions.md    # the persona / system prompt
  tests/             # pytest — must pass in CI before merge (and after self-mutation)
  evals/             # golden-case + scenario evals — also CI-gated
```

`config.json` never contains a secret — only integration *names*, which the loader resolves against the
platform's encrypted registry (chapters 05/06). This is what keeps credentials out of Git.

**The takeaway for your own platform:** define the *smallest possible contract* an agent must satisfy
(here: one function + a config file), and make everything else — tools, memory, model routing, streaming
— a service the platform provides. Thin contract + rich platform = agents that are easy to write and safe
to run.

Next: **[08 · Tool Calling](./08-tool-calling.md)** — the mechanics under step 2–4 of the run loop.
