# 13 · AG-UI & Generative UI

An agent that works invisibly and dumps a wall of text at the end is a bad product. Users need to *watch*
the agent think, see which tools it's running, approve risky steps, and interact with rich outputs. That
requires a **streaming protocol** between the agent backend and the browser, plus a way for the agent to
**render interactive UI inside the chat**. That protocol is **AG-UI**, and the capability is **generative
UI**. This chapter covers both.

---

## 1. Why a protocol at all?

A normal HTTP response is one-shot: request → wait → full response. An agent run is the opposite — it's a
*sequence of events over time*: some reasoning, a tool call, a tool result, more reasoning, a question for
the user, a final answer. To render that live you need a **stream of typed events**, not a blob.

**AG-UI** (authored by the CopilotKit team) is exactly that: a standard vocabulary of streaming events for
agent↔UI communication. MAF speaks it natively (chapter 11's `add_agent_framework_fastapi_endpoint`), and
the browser consumes it. Transport is **Server-Sent Events** (SSE) — a simple `text/event-stream` of
`data: {json}\n\n` frames — which is why every agent-producing endpoint in the platform is an SSE endpoint
(chapter 02).

---

## 2. The AG-UI event vocabulary

The events fall into a few families (Metorite emits these from `run_agent_stream`):

**Run lifecycle**
```
RUN_STARTED   { runId, threadId }
RUN_FINISHED  { runId }
RUN_ERROR     { message, code }
```

**Streaming text & thinking**
```
TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT {delta} / TEXT_MESSAGE_END   ← the answer, token by token
THINKING_TEXT_MESSAGE_CONTENT {delta}                                  ← live reasoning
```

**Tool calls** (the transparency layer)
```
TOOL_CALL_START  { toolCallId, toolCallName }
TOOL_CALL_ARGS   { delta }          ← arguments stream in
TOOL_CALL_RESULT { content }
TOOL_CALL_PARTIAL { delta }         ← live output (e.g. terminal) while a tool runs
```

**Generative UI & state** (§4)
```
STATE_SNAPSHOT / STATE_DELTA        ← agent's structured state → custom UI
CUSTOM { name, value }              ← app-defined events (artifact_created, elicitation_requested, …)
```

**Sub-agent events** — the same shapes, namespaced (`SUB_AGENT_TEXT_DELTA`, `SUB_AGENT_TOOL_CALL_START`,
…) so a delegating agent's children render as nested activity.

The design principle: **every distinct thing an agent does gets its own event type**, so the UI can render
each appropriately (text as prose, thinking in a collapsible box, tool calls as activity cards) instead of
flattening everything into one text stream.

---

## 3. The frontend half — consuming the stream

The browser opens the SSE stream and dispatches events to renderers. Metorite's homegrown hooks
(chapter 02 §3) do this:

- **`useAgentChat`** parses each SSE frame and accumulates messages, tool events, reasoning, and artifacts.
- **`useAgentEvents`** is a subscriber bus so many UI pieces can react to the same stream, filtered by
  `threadId` for multi-session isolation.
- Rendering components map event families to UI: `MarkdownMessage` for text, a thinking container for
  reasoning, tool-activity cards for tool calls, `ArtifactCard` for outputs.

**Resilience** is built into this layer (chapter 06 §8): because every frame is also teed to a Redis Stream
server-side, a dropped connection doesn't lose the run — the frontend reconnects and replays from its
last-seen event id, or falls back to polling persisted messages. Streaming UIs *must* plan for disconnects;
a naive SSE consumer that loses state on refresh is a common beginner mistake.

---

## 4. Generative UI — agents that render interactive components

"Generative UI" means the agent doesn't just emit text — it emits **structured events that the frontend
turns into interactive widgets**. Three uses in Metorite, all built on the `CUSTOM` / `STATE_*`
events:

### (a) Human-in-the-loop cards

The governance gates from chapter 06 surface here as UI:

- **Confirmation** — agent emits `confirmation_requested`; the UI renders a `ConfirmationCard` (a yellow
  Approve/Reject card). The user's choice is sent back as the next message, and the paused workflow
  resumes.
- **Elicitation** — agent emits `elicitation_requested` with an array of questions; the UI renders an
  `ElicitationCard` with radio/checkbox/free-text inputs. Answers go back as a structured message.

This is the crucial pattern: **the agent controls the conversation flow by requesting UI, and the UI
returns structured answers.** The agent "pauses and asks" without any bespoke endpoint — just an event and
a card.

### (b) Artifacts

When an agent produces a file (a report, an image, a spreadsheet), it emits `artifact_created` with the
path + metadata. The UI shows an `ArtifactCard` inline and, on click, an `ArtifactViewerModal` that renders
the content by type — PDFs via PDF.js, code via Monaco, images inline, Word docs via Mammoth. The bytes are
fetched through a proxied workspace endpoint. (By platform convention, agents write user-visible outputs to
`inputs/`/`outputs/`/`agent-data/`, and only those are exposed to the file browser.)

### (c) Shared state / structured panels

`STATE_SNAPSHOT` (full) and `STATE_DELTA` (partial) events carry the agent's structured working state,
which a generic `GenerativeUIPanel` renders as tables/key-value views — e.g. a live todo list
(`manage_todo_list`) or an agent's progress object. The agent updates its state; the panel re-renders.

### (d) Frontend tools — UI the agent can drive

The inverse direction: `useFrontendTool` registers *browser-side* functions (e.g. `setTheme("dark")`,
navigate, open a panel) whose descriptions are injected into the agent's prompt. The agent can then call
them like any tool, and the handler runs **in the browser**. This lets an agent manipulate the UI directly,
not just produce content for it.

---

## 5. Why this matters for your build

- **Model the agent's output as a typed event stream from day one.** Retrofitting streaming and per-event
  rendering onto a request/response design is painful.
- **Use SSE** unless you specifically need bidirectional mid-stream input — it's simpler than WebSockets and
  fits the "server pushes events" shape perfectly.
- **Make HITL a UI event, not a special case.** "Emit a card, get a structured answer back" generalizes to
  every human-approval and clarification need.
- **Plan for reconnection.** Tee events to a durable buffer server-side so a refresh or a flaky connection
  replays instead of losing the run.
- **AG-UI is a good standard to adopt** — but even if you roll your own, copy its shape: lifecycle +
  text + thinking + tool lifecycle + custom/state events.

Next: **[14 · Build Your Own — A Minimal Blueprint](./14-build-your-own.md)**.
