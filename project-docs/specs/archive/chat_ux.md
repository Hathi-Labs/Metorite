# Chat UX Specification — Metorite Control Plane

> ⚠️ **SUPERSEDED / DEFERRED (2026-08-10 consolidation, D26).** No work dispatches from
> this document — `generative_ui_2.md` §2 owns the HITL model; kept as protocol reference (§12 VII–XI open, unscheduled). The active plan is `project-docs/work_plan.md` §2;
> the classification of record is `project-docs/INDEX.md`.


> **Type:** Implementation Spec  
> **Date:** 2026-06-05  
> **Status:** Active — Phase 1 complete, Phase 2 (CopilotKit patterns) in progress · **Header re-dated 2026-08-09:** §12.3 is superseded by `generative_ui_2.md` §2; the live remainder is §12 items **V, VII–XI** plus §11 **H** (dev console) and **J** (push-to-talk) — the 2026-08-01 doc-truth note below is authoritative; the body is retained as protocol reference per `work_plan.md` §5 item 3  
> **Target:** `workbench/control_plane/src/` — chat tab and related components  
> **Reference:** VS Code source — `chatThinkingContentPart.ts`, `chatProgressContentPart.ts`, `chatSubagentContentPart.ts`, `chatToolInputOutputContentPart.ts` (read in full); GitHub Copilot Chat extension; Claude Code  
> **Source studied:** [microsoft/vscode](https://github.com/microsoft/vscode) `src/vs/workbench/contrib/chat/browser/widget/chatContentParts/`  
> **Additional source studied:** [CopilotKit/CopilotKit](https://github.com/CopilotKit/CopilotKit) — `packages/react-ui/src/components/chat/` (Chat.tsx, Messages.tsx, Input.tsx, Suggestions.tsx, AttachmentQueue.tsx); `packages/react-core/src/hooks/` (use-copilot-chat.ts, use-human-in-the-loop.ts, use-coagent.ts, use-langgraph-interrupt.ts)

> **Update 2026-08-01 (doc-truth pass):** Phase 1 of this spec shipped (§0). The §12.3
> HITL/interrupt design (terminal run + `resume[]` array) was **NOT built** — the shipped
> HITL model is a `user_input_requested` SSE frame carrying a `request_id`, answered via
> `POST /agent/respond-input`, which parks the run on the `_pending_user_input` future
> registry (`apps/services/gateway/gateway/routes/agent.py`), rendered in the frontend as
> `ElicitationCard.tsx` / `ConfirmationCard.tsx` — see `generative_ui_2.md` §2, which
> **supersedes §12.3**. The live chat work queue is
> `specs/archive/chat_implementation_review_2026-07.md` (per `task_manager_app.md` §9.3).
>
> Shipped from §11/§12 since this spec was written (each verified in
> `workbench/control_plane/src`, 2026-08-01): §11 C ≈ `components/SuggestionPills.tsx` ·
> E partial = `hooks/useAgentState.ts` · F ≈ `components/ChatErrorCard.tsx` · I ≈
> `components/FileUploadButton.tsx`; from §12: II REASONING_* (`route.ts` handles
> `REASONING_MESSAGE_CONTENT` alongside legacy `THINKING_*`), III `TOOL_CALL_RESULT`
> (folded into the `TOOL_CALL_END` case), IV `STATE_SNAPSHOT`, VI generative UI via
> `CUSTOM` — all in `app/api/agent/chat/route.ts`.
>
> **Still open:** §12 V `STEP_STARTED`/`STEP_FINISHED` (no handler anywhere in
> `control_plane/src` as of 2026-08-01), VII capability discovery, VIII frontend-defined
> tools, IX activity events, X `REASONING_ENCRYPTED_VALUE`, XI `parentRunId`/time travel;
> §11 H dev console, J push-to-talk. §10's checklist was never maintained — §0 plus this
> banner are the status of record.

---

## 0. Phase 1 status (completed 2026-06-05)

| Item | Status | Notes |
|---|---|---|
| `ThinkingContainer` shimmer + auto-collapse | ✅ Done | |
| Tool icon mapping | ✅ Done | |
| Tool args shown (TOOL_CALL_ARGS accumulation) | ✅ Done | |
| Reasoning / chain-of-thought (AG-UI path) | ✅ Done | |
| `reasoning_content` from LiteLLM thinking models | ✅ Done | `route.ts` now parses `reasoning_content`/`thinking_content`/`thinking` |
| `showThinking` fix (visible whole active phase) | ✅ Done | Was hiding on first token; fixed to gate on `isThinkingActive` |
| `AgentStatusBar`, `MessageActionBar` | ✅ Done | |
| Gateway streaming for named agents | ✅ Done | New `POST /agent/run/stream` + `run_agent_stream()` executor |

---

## 1. What's wrong today

The current chat shows a blank screen while the agent is working. The user has no idea:
- Whether the request was received
- Which agent is handling it
- What tool calls are being made
- What data is being read
- Whether something went wrong silently
- How long it will take

The only feedback is the final answer appearing. There are no intermediate signals.

The tool-call accordion exists in `MarkdownMessage.tsx` but only renders **after** the tool completes — so during a long Zoho query the user sees nothing for 5–20 seconds. The `tool_start` / `tool_end` SSE events are received by `useAgentChat.ts` but only stored in `message.toolEvents`; they are never surfaced above the message in a live panel.

---

## 2. What the VS Code source actually does (read from production code)

I read the full source of `chatThinkingContentPart.ts` (2249 lines) and `chatProgressContentPart.ts` (359 lines) from the VS Code core repo. Here is exactly how the production system works. This is what we should copy.

### The "Thinking container" — `ChatThinkingContentPart`

VS Code has ONE collapsible container for the entire "working" phase. It groups ALL tool calls and raw thinking text into a single expandable block. Its lifecycle:

1. **Created** when first thinking content or tool call arrives.
2. **Title starts as** `"Thinking"` — changes to `"Working"` when the first tool call is registered.
3. **While active**: title shows `"Working: <current tool label>"` — e.g. `"Working: Searching for login"` — using a CSS shimmer animation (`chat-thinking-title-shimmer`) on the subtitle portion. The container header shows a pulsing filled-circle codicon (`circleFilled`) when collapsed.
4. **Expansion**: Three modes — `Collapsed` (click to expand), `CollapsedPreview` (starts expanded, auto-collapses on completion), `FixedScrolling` (scrolls within a fixed-height box). Lazy rendering: tool items inside the container are only rendered when the user expands it (factory pattern).
5. **Working messages are randomised** from a pool, refreshing per tool call:
   - Default pool: `"Searching…"`, `"Analyzing…"`, `"Processing…"` etc.
   - Easter eggs (1-in-100 chance): `"Bribing the hamster"`, `"Reticulating splines"`, `"Summoning Clippy"`, `"Mining diamonds"`.
   - Terminal tool sub-pool (different from generic tool pool).
   - User-configurable via `chat.agent.thinkingPhrases` setting.
6. **Tool icons** are assigned by tool ID keyword matching:
   - `search/grep/find/list/semantic/codebase` → `$(search)` icon
   - `read/get_file/problems` → `$(book)` icon
   - `edit/create/replace/patch/insertEdit` → `$(pencil)` icon
   - `terminal` → `$(terminal)` icon (or `$(terminal-secure)` if sandbox-wrapped)
   - default → `$(tools)` icon
7. **On completion**:
   - Icon switches to `$(check)` (green checkmark).
   - Shimmer animation stops.
   - An LLM call generates a 10-word past-tense summary title: `"Updated HomePage.tsx"`, `"Reviewed 2 files"`, `"Searched for login and authentication"`. Uses `copilot-utility-small` model. Falls back to last extracted title if LLM fails.
   - Container auto-collapses (unless it has content worth showing).
8. **Single tool optimisation**: if there is exactly ONE tool call and no thinking text, VS Code moves the tool block OUT of the thinking container and renders it inline in the message flow (no accordion needed).

### The progress row — `ChatProgressContentPart`

Separate from the thinking container, individual progress messages render as rows:
- A `$(loading~spin)` icon while active, `$(check)` when done.
- The text uses a **shimmer** CSS animation while the step is active (CSS class `shimmer-progress`). This is NOT a spinner next to the text — the text itself has a flowing gradient highlight. That's the key visual.
- When a subsequent non-progress content arrives (like a tool result), the progress row auto-hides.
- Shows elapsed time + token stats (currently intentionally hidden in "minimal shipping version", but the structure is there).

### Subagent rendering — `chatSubagentContentPart.ts`

When the orchestrator delegates to a sub-agent (Claude Code style), VS Code renders a nested section with:
- The sub-agent's name and icon as a header
- Its tool calls rendered recursively in the same thinking-container pattern
- The outer thinking container's title shows `"Working: <subagent name>"`

### Key CSS patterns to replicate

From reading the source:
```
.chat-thinking-box                // outer wrapper
.chat-thinking-active             // while streaming (adds shimmer class)
.chat-thinking-title-shimmer      // the animated subtitle span
.shimmer-progress                 // on individual progress rows
.chat-thinking-spinner-item       // the in-container spinner row
.chat-thinking-item               // each item inside the container
.chat-thinking-tool-wrapper       // wrapper around icon + content for each tool
.chat-thinking-icon               // icon element (themed codicon)
```

The shimmer is a CSS `background-position` animation on a linear gradient — creates the "sweeping highlight" effect used in loading states.

---

## 3. Proposed UI layout (matching VS Code patterns exactly)

**State 1: Agent is working (streaming)**
```
  You: Show deals in Awaiting PO stage

┌──────────────────────────────────────────────────────────────────────┐
│  ◉ Working: Searching for Deals · Stage:equals:Awaiting PO    [▼]  │  ← Thinking container (collapsed)
└──────────────────────────────────────────────────────────────────────┘  Title has shimmer animation on "Searching for Deals..."
  [blinking cursor ▌]
```

**State 2: Expanded (user clicks ▼)**
```
┌──────────────────────────────────────────────────────────────────────┐
│  ◉ Working: Searching for Deals...                            [▲]  │  ← title + shimmer + collapse button
├──────────────────────────────────────────────────────────────────────┤
│  🔍 zoho_crm  search · Deals · Stage:equals:Awaiting PO       [▼]  │  ← tool row with icon + label + expand
│  🔍 Running query...                                                │
│                                                                      │
│  [spin] Mining diamonds...                                           │  ← rotating random working message
└──────────────────────────────────────────────────────────────────────┘
```

**State 3: Tool completes, response arriving**
```
┌──────────────────────────────────────────────────────────────────────┐
│  ✓  Retrieved Zoho deals  [826ms]                             [▼]  │  ← LLM-generated 10-word summary, check icon
└──────────────────────────────────────────────────────────────────────┘

  Here are the 4 deals in the "Awaiting PO" stage:
  ...
                                          [👍] [👎]  [Copy]
```

**State 4: Multi-agent delegation (when orchestrator calls agent-sales-assistant)**
```
┌──────────────────────────────────────────────────────────────────────┐
│  ◉ Working: agent-sales-assistant › Searching Zoho...         [▼]  │  ← "agentName › toolName" in title
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. New components to build (VS Code-accurate)

### 4.1 `ThinkingContainer` (new — replaces `ToolCallBlock`)

**Replaces** the per-message `ToolCallBlock` accordion in `MarkdownMessage.tsx`.  
**Wraps** the entire working phase as a single collapsible group.

**Three phases, matching VS Code exactly:**

```tsx
type ThinkingMode = 'collapsed' | 'collapsed-preview' | 'fixed-scroll';

interface ThinkingContainerProps {
  toolEvents: ToolEvent[];           // fed from useAgentChat SSE stream
  progressLines: string[];           // generated from tool_start events
  isActive: boolean;                 // false after RUN_FINISHED
  mode?: ThinkingMode;               // default 'collapsed-preview'
  finalTitle?: string;               // LLM-generated summary (arrived after completion)
}
```

**Title lifecycle:**
1. No tool calls yet → `"Thinking…"` (with shimmer)
2. First tool call → `"Working: <tool label>"` (with shimmer)
3. Active → `"Working: <latest tool label>"` (title updates as tool labels change)
4. Completed → LLM-generated past-tense summary OR last tool label

**Visual states:**
- Active: `◉` pulsing filled circle + CSS shimmer on title subtitle
- Expanded: `▼` chevron down
- Completed: `✓` green check mark, shimmer off, auto-collapse

**Tool icon mapping** (copied from VS Code `getToolInvocationIcon`):
```ts
function getToolIcon(toolName: string): string {
  const n = toolName.toLowerCase();
  if (/search|grep|find|list|semantic|codebase/.test(n)) return '🔍';
  if (/read|get_file|problems/.test(n))                   return '📖';
  if (/edit|create|replace|patch|insert/.test(n))         return '✏️';
  if (/terminal|bash|shell/.test(n))                      return '>';
  return '⚙';
}
```

**Random working messages** (cycling, not static):
```ts
const WORKING_MESSAGES = [
  "Searching…", "Analyzing…", "Processing…", "Checking…", "Reviewing…",
  // Easter eggs (1-in-100):
  "Bribing the hamster", "Reticulating splines", "Summoning Clippy",
];
```

**CSS shimmer** (the key visual — NOT a spinner next to text):
```css
@keyframes shimmer {
  from { background-position: -200px 0; }
  to   { background-position: 200px 0; }
}
.chat-thinking-title-shimmer {
  background: linear-gradient(90deg, 
    var(--text-muted) 25%, var(--text-normal) 50%, var(--text-muted) 75%);
  background-size: 400px 100%;
  animation: shimmer 1.5s infinite linear;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
```

### 4.2 `LLMTitleGenerator` (utility function)

After `RUN_FINISHED`, call the gateway /v1 (litellm SDK) with a small model to generate a 10-word past-tense summary of all tool labels:

```ts
async function generateThinkingTitle(toolLabels: string[]): Promise<string> {
  const context = toolLabels.join(', ');
  const prompt = `Summarize in a SINGLE past-tense phrase under 10 words:
Output must start with a past-tense verb (Updated/Reviewed/Searched/Ran).
Do NOT include tool names. Just actions and subjects.
Content: ${context}`;
  // Call /api/completions with tier1-haiku or smallest available model
}
```

Fallback: if LLM call fails or takes >2s, use the last tool label directly.

### 4.3 `AgentStatusBar` (new, smaller scope than originally planned)

**Location:** sticky top of the chat response area, only shown when agent is selected  
**Shows:** agent name, required integrations (green/red dots), active/idle status  
**Implementation:** small, static; no LLM dependency

```tsx
interface AgentStatusBarProps {
  agentName: string;
  integrations: { name: string; configured: boolean }[];
  isActive: boolean;
}
```

### 4.4 `MessageActionBar` (new)

👍 👎 Copy — below each assistant message. Retry removed (out of scope for now).

### 4.5 Updates to `useAgentChat.ts`

Add to `ChatMessage`:
```ts
progressLines?: string[];     // ordered list of tool labels for ThinkingContainer
thinkingTitle?: string;       // LLM-generated title (set after completion)
isThinkingActive?: boolean;   // true while RUN is in progress
```

`progress` and `agent_meta` SSE events (same as before).

### 4.6 Updates to `api/agent/chat/route.ts`

Emit progress events on each `TOOL_CALL_START`:
```ts
// TOOL_CALL_START → emit progress line
yield `data: ${JSON.stringify({ type: 'progress', content: toolName })}\n\n`;
```

No changes to the AG-UI → SSE translation otherwise.

---

## 5. SSE event additions

Add to the existing event types in `useAgentChat.ts`:

```ts
// New SSE event from /api/agent/chat
{ type: "progress"; content: string }      // status line (shown in ThinkingPanel)
{ type: "agent_meta"; agent: string; model: string; integrations: string[] }  // header bar data
```

Emitted by `api/agent/chat/route.ts` at:
- Request start: `agent_meta` with agent name / model
- Each `TOOL_CALL_START` AG-UI event: `progress` with "Calling tool_name…"
- Each `TOOL_CALL_END` AG-UI event: `progress` with "✓ tool_name (Xms)"
- `RUN_ERROR`: `progress` with "⚠ Error: <message>"

---

## 6. Visual design spec (matching VS Code patterns)

### Colours (consistent with existing dark zinc palette)
| Element | Tailwind class | Notes |
|---|---|---|
| Thinking container | `bg-zinc-900/40 border border-zinc-700/40 rounded-lg` | Matches VS Code's subtle dark grouping |
| Title — active | `text-zinc-300` with CSS shimmer gradient | NOT a spinner next to text |
| Title — completed | `text-emerald-500` + ✓ icon | Same as current `text-emerald-500` |
| Tool row icon | `text-zinc-400` | Themed by tool type |
| Tool row label | `text-zinc-300 text-xs` | Truncated if long |
| Working message | `text-zinc-500 text-xs italic` | Rotating random message |
| Expand/collapse | `text-zinc-600 text-[10px]` | ▼ ▲ |
| Progress row | `text-zinc-400 text-xs` with shimmer | Same shimmer effect |
| Status bar | `bg-zinc-900 border-b border-zinc-800 px-4 py-1.5` | |
| Integration dot — OK | `bg-emerald-500 rounded-full w-1.5 h-1.5` | |
| Integration dot — missing | `bg-red-500 rounded-full w-1.5 h-1.5` | |

### Animation — the shimmer is the core visual language
```css
/* The flowing shimmer on title text while agent is working */
@keyframes chat-shimmer {
  from { background-position: -400px 0; }
  to   { background-position: 400px 0; }
}

.chat-thinking-title-shimmer {
  display: inline;
  background: linear-gradient(
    90deg,
    theme('colors.zinc.500') 0%,
    theme('colors.zinc.200') 40%,
    theme('colors.zinc.500') 80%
  );
  background-size: 400px 100%;
  animation: chat-shimmer 2s infinite linear;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* Shimmer on entire progress rows while active */
.shimmer-progress {
  /* same gradient but on the text element */
}
```

### When to show vs hide the ThinkingContainer
- Show: any message where `progressLines.length > 0` OR `isThinkingActive === true`  
- Hide: plain LLM text responses with no tool calls (pass-through to LiteLLM directly)  
- Auto-collapse: 300ms after `isThinkingActive` transitions to `false`  
- User can always re-expand to see the tool call history

---

## 7. Implementation order (revised — VS Code-accurate)

| Step | Component/File | What | Complexity | Impact |
|---|---|---|---|---|
| 1 | `api/agent/chat/route.ts` | Emit `progress` SSE on each `TOOL_CALL_START`; emit `agent_meta` at start | Low | Feeds all new UI |
| 2 | `useAgentChat.ts` | Add `progressLines`, `isThinkingActive`, `thinkingTitle` to `ChatMessage` | Low | State for new components |
| 3 | `ThinkingContainer.tsx` | New component: collapsible, shimmer title, tool icon rows, random working messages | Medium-High | Core UX win |
| 4 | `AgentStatusBar.tsx` | Name + integration dots | Low | Context/orientation |
| 5 | `MarkdownMessage.tsx` | Replace `ToolCallBlock` with `ThinkingContainer`; remove old accordion | Low | Cleanup |
| 6 | `AgentChat.tsx` | Mount `AgentStatusBar`; pass `progressLines` / `isThinkingActive` to messages | Low | Wiring |
| 7 | `LLMTitleGenerator` | Async title gen via small model after completion | Medium | Polish |
| 8 | `MessageActionBar.tsx` | 👍 👎 Copy | Low | Polish |

Total estimated effort: **2–3 engineer-days** (simpler than the original 4-day estimate because we're copying proven patterns rather than inventing).

> **Note:** Steps 3 and 7 are independent. Step 3 (ThinkingContainer) gives the biggest UX improvement and can ship without Step 7 (LLM title gen). LLM title gen is a polish step.

---

## 8. What NOT to change (anti-patterns to avoid)

- Do not add a raw "system prompt" viewer — users don't need to see 57k tokens of skill instructions
- Do not show raw JSON for tool args by default — only on expand
- Do not auto-scroll to bottom during thinking (jarring) — only auto-scroll on new tokens
- Do not add a separate "debug" tab — visibility should be inline, not hidden behind a tab
- Do not show thinking panel for litellm mode (direct LLM, no tool calls) — hide ThinkingPanel if `progressLines.length === 0`

---

## 9. Files to create / modify

| File | Action | Notes |
|---|---|---|
| `src/components/ThinkingContainer.tsx` | Create | Core component; replaces per-tool accordion |
| `src/components/AgentStatusBar.tsx` | Create | Static, small |
| `src/components/MessageActionBar.tsx` | Create | 👍 👎 Copy |
| `src/components/MarkdownMessage.tsx` | Modify | Remove `ToolCallBlock`; use `ThinkingContainer` |
| `src/components/AgentChat.tsx` | Modify | Add `AgentStatusBar`, wire `progressLines` |
| `src/hooks/useAgentChat.ts` | Modify | Add `progressLines`, `isThinkingActive`, `thinkingTitle` |
| `src/app/api/agent/chat/route.ts` | Modify | Emit `progress` + `agent_meta` SSE events |
| `src/styles/thinking.css` | Create | Shimmer CSS animation |

---

## 10. Acceptance criteria

> **Update 2026-08-01 (doc-truth pass):** this checklist was never maintained after
> Phase 1 shipped — the unchecked boxes below do NOT mean "not done". §0 and the banner
> at the top of this doc are the status of record.

- [ ] Sending a message immediately shows a `ThinkingContainer` with shimmer title — **no blank screen**
- [ ] Container title starts `"Thinking…"`, transitions to `"Working: <tool label>"` on first tool call
- [ ] Title shimmer animation plays while active; stops (no flicker) when done
- [ ] Each tool call appears in the container header title as it starts
- [ ] Container auto-collapses 300ms after `RUN_FINISHED`; icon switches to green ✓
- [ ] Expand shows tool icon rows with random working messages while active
- [ ] After completion, LLM-generated title OR last tool label shown (never "Working" permanently)
- [ ] Agent name and integration status visible in `AgentStatusBar`
- [ ] 👍 👎 Copy visible on every assistant message
- [ ] Direct LLM mode (no tool calls) — `ThinkingContainer` NOT shown
- [ ] No visible regression on markdown, code blocks, MCQ choices, streaming cursor

---

## 11. CopilotKit source study — features to adopt

> **Source:** [CopilotKit/CopilotKit](https://github.com/CopilotKit/CopilotKit) read 2026-06-05  
> **Files read:** `packages/react-ui/src/components/chat/` (Chat.tsx, Messages.tsx, Input.tsx, Suggestions.tsx, AttachmentQueue.tsx, Window.tsx, Sidebar.tsx, Popup.tsx); `packages/react-core/src/hooks/` (use-copilot-chat.ts, use-human-in-the-loop.ts, use-coagent.ts, use-langgraph-interrupt.ts, use-configure-chat-suggestions.tsx)

CopilotKit is the creators of the AG-UI protocol, so their frontend SDK has the highest-fidelity AG-UI rendering. We already stripped it as a dependency (it imposed its own opinionated SSE protocol that conflicted with our custom AG-UI → SSE bridge), but the **patterns and UX ideas** are still worth copying directly into our custom components.

---

### 11.1 Smart auto-scroll (`Messages.tsx` — `useScrollToBottom`)

**What it does:**
- Uses a `MutationObserver` on the messages container that watches for `childList`, `subtree`, and `characterData` changes — so it scrolls on every token arriving, not just on new messages.
- Tracks `isUserScrollUpRef`: if the user has scrolled up (reading old messages), the observer does NOT auto-scroll. Prevents the jarring snap-to-bottom while the user is reading.
- Resets `isUserScrollUpRef` to `false` and force-scrolls only when a new **user** message arrives (i.e. after you press Send).

**Current gap in our code:** We use `useEffect` with `bottomRef.current?.scrollIntoView()` on every message append. This scrolls even when the user has scrolled up. Jarring.

**How to implement in `AgentChat.tsx`:**

```tsx
// Replace the current scroll useEffect with this pattern
const messagesContainerRef = useRef<HTMLDivElement>(null);
const messagesEndRef = useRef<HTMLDivElement>(null);
const isUserScrollUpRef = useRef(false);
const isProgrammaticScrollRef = useRef(false);

// MutationObserver scrolls as tokens arrive (if user hasn't scrolled up)
useEffect(() => {
  const container = messagesContainerRef.current;
  if (!container) return;
  const observer = new MutationObserver(() => {
    if (!isUserScrollUpRef.current) {
      isProgrammaticScrollRef.current = true;
      container.scrollTop = container.scrollHeight;
    }
  });
  observer.observe(container, { childList: true, subtree: true, characterData: true });
  return () => observer.disconnect();
}, []);

// Detect when user manually scrolls up
useEffect(() => {
  const container = messagesContainerRef.current;
  if (!container) return;
  const onScroll = () => {
    if (isProgrammaticScrollRef.current) { isProgrammaticScrollRef.current = false; return; }
    const { scrollTop, scrollHeight, clientHeight } = container;
    isUserScrollUpRef.current = scrollTop + clientHeight < scrollHeight - 20;
  };
  container.addEventListener("scroll", onScroll);
  return () => container.removeEventListener("scroll", onScroll);
}, []);

// Force-scroll when user sends a new message (reset user-scroll-up state)
const userMessageCount = messages.filter(m => m.role === "user").length;
useEffect(() => {
  isUserScrollUpRef.current = false;
  messagesContainerRef.current!.scrollTop = messagesContainerRef.current!.scrollHeight;
}, [userMessageCount]);
```

**File to modify:** `src/components/AgentChat.tsx`  
**Priority:** High — this directly affects readability during long responses.

---

### 11.2 Contextual suggestions / starter prompts (`Suggestions.tsx`, `use-configure-chat-suggestions.tsx`)

**What it does:**  
When the chat is idle (no messages, or after a message), renders a row of clickable "suggestion" chips that the user can tap to send a canned message. Each suggestion has:
- `title`: short display label (e.g. "Show pipeline summary")
- `message`: the actual text sent (e.g. "Give me a summary of all open deals by stage")
- `partial?: boolean` — if true, shows a loading state while suggestions are being generated by the LLM
- `className?: string` — for custom styling

CopilotKit supports two modes:
1. **Static suggestions:** configured at the component level via props
2. **LLM-generated suggestions:** `useCopilotChatSuggestions()` hook calls the LLM with context to generate dynamic suggestions after each response

**How to implement in our app:**

Phase A (static — low effort):
```tsx
// In AgentChat.tsx, render suggestions above the input when messages.length === 0
const AGENT_SUGGESTIONS: Record<string, string[]> = {
  "orchestrator": [
    "What's the status of the sales pipeline?",
    "Show me tasks overdue this week",
    "Who are our top deals in Awaiting PO?",
    "Draft a follow-up for stale deals",
  ],
  "task-manager": ["What tasks are overdue?", "Show my team's workload", "Any blockers this sprint?"],
  "sales": ["Show deals closing this month", "Which deals need follow-up?"],
};

{messages.length === 0 && (
  <div className="flex flex-wrap gap-2 mt-4 px-2">
    {(AGENT_SUGGESTIONS[currentAgentName] ?? []).map((s) => (
      <button key={s} onClick={() => submitText(s)}
        className="text-xs border border-zinc-700 bg-zinc-800/60 rounded-full px-3 py-1.5 
                   text-zinc-300 hover:bg-zinc-700 hover:border-zinc-500 transition-colors">
        {s}
      </button>
    ))}
  </div>
)}
```

Phase B (dynamic LLM-generated — medium effort):
```tsx
// After each agent response, call LiteLLM to generate 3 follow-up suggestions
async function generateSuggestions(lastResponse: string, agentName: string): Promise<string[]> {
  // POST /api/agent/chat with litellm mode and a meta-prompt
  // e.g. "Given this response, suggest 3 short follow-up questions the user might ask next"
}
```

**File to create:** `src/components/ChatSuggestions.tsx`  
**Priority:** Medium — significantly improves onboarding and discoverability.

---

### 11.3 Human-in-the-loop interrupts (`use-human-in-the-loop.ts`, `use-langgraph-interrupt.ts`)

**What it does:**  
When an agent needs user confirmation/input mid-run, it pauses and renders a custom React component inline in the chat. The user sees a UI element (e.g. "Are you sure you want to send this email?"), interacts with it, and the agent resumes with the user's response.

CopilotKit's pattern:
1. Agent emits a special `interrupt` AG-UI event with `type: "human_input_required"` and a schema
2. Frontend detects this in the SSE stream
3. A custom React component renders inline (passed as `renderAndWaitForResponse` to `useCopilotAction`)
4. Component calls `respond({ approved: true, ... })` which resumes the run
5. Agent receives the response and continues

**Our AG-UI translation layer already receives custom event types** — we just need to:
1. Add `{ type: "interrupt", schema: {...}, question: "..." }` to the SSE event types
2. Store the `interrupt` object on the `ChatMessage`
3. Render a confirmation UI inline when `message.interrupt` is set
4. POST back to a resume endpoint with the user's answer

**This is the key feature for high-stakes operations** (sending emails, creating CRM records, approving invoices).

**Files to modify:**
- `src/hooks/useAgentChat.ts` — add `interrupt` field to `ChatMessage`, handle `interrupt` SSE event
- `src/components/AgentChat.tsx` — render `InterruptWidget` when `message.interrupt` is set  
- `src/components/InterruptWidget.tsx` — NEW: renders the interrupt UI (confirm/reject buttons + optional form)
- `apps/orchestrator/orchestrator/executor.py` — emit interrupt SSE events from agent hooks
- `apps/gateway/gateway/routes/agent.py` — add `POST /agent/run/resume` endpoint

**Priority:** High — essential for approval workflows (email drafts, CRM writes).

---

### 11.4 File/image attachment (`Input.tsx` → `onUpload`, `AttachmentQueue.tsx`, `ImageUploadQueue.tsx`)

**What it does:**
- Upload button in the input bar triggers a file picker
- Uploaded files/images appear as thumbnail chips in a queue above the textarea
- On send, files are attached to the message (image data or file content)
- `AttachmentQueue.tsx`: rows of upload progress bars + remove buttons
- `ImageUploadQueue.tsx`: image thumbnails in a horizontal scroll strip

**Implementation for our app:**

For MVP (image paste + file drag-drop):
```tsx
// In AgentChat.tsx textarea, add paste handler
const handlePaste = (e: React.ClipboardEvent) => {
  const items = Array.from(e.clipboardData.items);
  const imageItem = items.find(i => i.type.startsWith("image/"));
  if (imageItem) {
    e.preventDefault();
    const file = imageItem.getAsFile();
    if (file) addAttachment(file);
  }
};

interface Attachment {
  id: string;
  name: string;
  type: string;
  dataUrl: string;     // base64 for images
  content?: string;    // text content for .txt/.md files
}
```

Images would be passed to the LLM as multimodal messages (supported by GPT-4o, Claude 3, Gemini).  
Text files would be injected as system context.

**Files to create/modify:**
- `src/components/AttachmentBar.tsx` — NEW: thumbnail strip above textarea  
- `src/hooks/useAgentChat.ts` — add `attachments?: Attachment[]` to send payload  
- `src/app/api/agent/chat/route.ts` — pass image data URLs to LiteLLM as `image_url` content parts

**Priority:** Low for now (no immediate business need), but note for Phase 3.

---

### 11.5 Push-to-talk voice input (`Input.tsx` → `usePushToTalk`, `use-push-to-talk.ts`)

**What it does:**
- Microphone button in the input bar
- States: `idle` → `recording` (button glows red) → `transcribing` (spinner) → appends transcription to textarea
- Uses browser `MediaRecorder` API + `transcribeAudioUrl` endpoint (Whisper-compatible)
- `usePushToTalk` hook manages the full state machine

**Implementation:**
If we add a Whisper endpoint to LiteLLM config (e.g. OpenAI `/audio/transcriptions`), we can drop in a simplified `usePushToTalk` hook that:
1. Starts `MediaRecorder` on click
2. POSTs the WebM blob to `/api/audio/transcribe`
3. Appends transcription to the input field

**Files to create:**
- `src/hooks/usePushToTalk.ts` — NEW: MediaRecorder state machine  
- `src/app/api/audio/transcribe/route.ts` — NEW: proxy to LiteLLM Whisper endpoint

**Priority:** Low / nice-to-have. Note for voice-first workflows.

---

### 11.6 Shared agent state (`use-coagent.ts` — `useCoAgent`)

**What it does:**  
CopilotKit lets agents emit structured state updates (not just text tokens) that React components can subscribe to. The agent publishes state like `{ stage: "lead_research", found_leads: 12 }` and a React component renders it as a live dashboard panel.

This is **Generative UI** — agents generate and update UI components at runtime.

**Our equivalent:** The `tool_end` SSE event already carries `result` text. The next step is:
1. Define a convention: tool results with `result_type: "state_update"` carry JSON payloads
2. `useAgentChat.ts` stores the latest state update per agent
3. AgentChat renders a `LiveStateDashboard` panel when state is present (collapsed by default, expandable)

**Example use cases:**
- Sales prospecting: `{ stage: "scraping_maps", businesses_found: 47, leads_qualified: 12 }` → rendered as a live progress dashboard
- Reconciler: `{ tasks_checked: 143, mismatches: 3, escalations: 1 }` → rendered as a live summary card

**Files to create/modify:**
- `src/hooks/useAgentState.ts` — NEW: subscribes to `state_update` SSE events  
- `src/components/AgentStateDashboard.tsx` — NEW: renders live structured state as a card

**Priority:** Medium — very impactful for long-running agents (sales prospecting, reconciler).

---

### 11.7 Message regeneration + feedback (`Messages.tsx` → `onRegenerate`, `onThumbsUp`, `onThumbsDown`)

**What it does:**
- Each assistant message has a "Regenerate" button (↺) that re-runs the same prompt
- `onThumbsUp` / `onThumbsDown` log feedback to a `messageFeedback` record (keyed by `message.id`)
- Feedback persists across renders (stored in state/localStorage)
- CopilotKit has a `messageFeedback?: Record<string, FeedbackEntry>` prop on Messages that lets parent control what's shown

**Current state in our app:** `MessageActionBar` has 👍 👎 Copy but they only log to console. No regenerate.

**How to wire:**
```ts
// In useAgentChat.ts, add regenerate
const regenerateMessage = useCallback(async (messageId: string) => {
  const msgIdx = messages.findIndex(m => m.id === messageId);
  if (msgIdx < 0) return;
  // Find the user message before this assistant message
  const userMsg = messages.slice(0, msgIdx).reverse().find(m => m.role === "user");
  if (!userMsg) return;
  // Remove this assistant message and re-send
  setMessages(prev => prev.filter(m => m.id !== messageId));
  await sendMessage(userMsg.content);
}, [messages, sendMessage]);
```

**Files to modify:**
- `src/hooks/useAgentChat.ts` — add `regenerateMessage(messageId: string)`
- `src/components/MessageActionBar.tsx` — add ↺ Regenerate button, wire 👍👎 to audit API
- `src/app/api/audit/feedback/route.ts` — NEW: record feedback to Postgres `audit_event` table

**Priority:** Medium.

---

### 11.8 Error banner with retry (`Messages.tsx` → `ErrorMessage`, `chatError` prop)

**What CopilotKit does:**
- Renders a styled error banner below the messages when `chatError` is set
- Banner has an actionable "Try again" button that calls `clearError()` + re-enables input
- Uses `data-testid="copilot-error-banner"` for stable test targeting

**Current gap:** Our errors appear as a `role: "system"` message in the chat thread. They don't have a retry button. Hard to distinguish from system messages.

**How to implement:**

```tsx
// In AgentChat.tsx, replace the current error display with:
{error && !isLoading && (
  <div className="mx-4 mt-2 flex items-start gap-3 rounded-xl bg-red-950/40 border 
                  border-red-900/60 px-4 py-3 text-xs text-red-300">
    <span className="shrink-0 text-red-500 text-sm">⚠</span>
    <div className="flex-1 min-w-0">
      <span className="font-medium">Something went wrong</span>
      <p className="mt-1 text-red-400/80 break-words">{error}</p>
    </div>
    <button
      onClick={() => { setError(null); sendMessage(lastUserMessage); }}
      className="shrink-0 text-red-400 hover:text-red-200 text-xs underline"
    >
      Retry
    </button>
  </div>
)}
```

**Priority:** Low — quick win.

---

### 11.9 Dev console overlay (`dev-console/` directory)

**What CopilotKit does:**  
A floating debug panel (toggled with a keyboard shortcut) showing:
- All SSE events received in chronological order (raw JSON)
- Message state diffs
- Active tool calls
- Latency metrics

**For our app:**  
Already partially achievable by opening browser DevTools → Network → EventStream. But a dedicated in-UI panel would help during development. Recommended implementation: a collapsible `DevConsole` component hidden behind `process.env.NODE_ENV === "development"` that subscribes to all SSE events.

**Files to create:**
- `src/components/DevConsole.tsx` — NEW: dev-only floating panel showing SSE event log

**Priority:** Low / dev-only, but very useful during debugging.

---

### 11.10 Popup / Sidebar / Modal layouts (`Popup.tsx`, `Sidebar.tsx`, `Modal.tsx`)

**What CopilotKit provides:**
- `CopilotPopup`: floating chat bubble (bottom-right corner) that expands into a chat window
- `CopilotSidebar`: full-height sidebar panel, overlays the right side of the screen
- `CopilotModal`: modal dialog with backdrop blur
- All share the same `Chat.tsx` body

**For our app:**  
We removed CopilotKit's sidebar when we stripped the dependency. Our chat is full-page only. These patterns may be useful if we want to embed the agent chat inside other pages (e.g. a floating chat on the Observability page, or inside the Agents management page for a per-agent chat).

**Implementation:** Extract `AgentChat.tsx` into a headless hook (`useAgentChat`, already done) + a presentation layer so the same state can drive popup, sidebar, or full-page layouts.

**Priority:** Low — no immediate need. Note for Phase 3 when embedding chat in other pages.

---

### 11.11 Summary: priority order for Phase 2

| # | Feature | Source in CopilotKit | Our file(s) | Priority |
|---|---|---|---|---|
| A | Smart auto-scroll (MutationObserver) | `Messages.tsx` → `useScrollToBottom` | `AgentChat.tsx` | **High** |
| B | Human-in-the-loop interrupts | `use-human-in-the-loop.ts`, `use-langgraph-interrupt.ts` | `useAgentChat.ts`, new `InterruptWidget.tsx` | **High** |
| C | Static starter suggestions | `Suggestions.tsx`, `use-configure-chat-suggestions.tsx` | New `ChatSuggestions.tsx`, `AgentChat.tsx` | **Medium** |
| D | Message regeneration + feedback audit wire-up | `Messages.tsx` → `onRegenerate` | `MessageActionBar.tsx`, `useAgentChat.ts` | **Medium** |
| E | Live agent state dashboard | `use-coagent.ts` | New `useAgentState.ts`, `AgentStateDashboard.tsx` | **Medium** |
| F | Error banner with retry | `Messages.tsx` → `ErrorMessage` | `AgentChat.tsx` | **Low** |
| G | LLM-generated dynamic suggestions | `use-copilot-chat-suggestions.tsx` | New `ChatSuggestions.tsx` | **Low** |
| H | Dev console overlay | `dev-console/` | New `DevConsole.tsx` | **Low** |
| I | File/image attachment | `AttachmentQueue.tsx`, `Input.tsx` → `onUpload` | New `AttachmentBar.tsx` | **Low** |
| J | Push-to-talk voice input | `use-push-to-talk.ts` | New `usePushToTalk.ts` | **Low** |

---

## 12. AG-UI Protocol deep-dive — world-class agentic UX patterns

> **Source:** https://docs.ag-ui.com (read 2026-06-05)  
> **Pages studied:** introduction, agentic-protocols, concepts/events, concepts/capabilities, concepts/state, concepts/interrupts, concepts/tools, concepts/reasoning, concepts/generative-ui-specs

AG-UI is the open, event-based standard we are already using as our backend-to-frontend streaming protocol (via MAF's `agent_framework.ag_ui`). The docs reveal several advanced features of the protocol that we are **not yet using** in our frontend. This section captures every applicable pattern.

---

### 12.1 Protocol context: where AG-UI sits

AG-UI is one of three complementary agentic protocols:

| Protocol | Layer | Purpose |
|---|---|---|
| **MCP** (Anthropic) | Agent ↔ Tools & Data | Connects agents to external tools and data sources |
| **A2A** (Google) | Agent ↔ Agent | Agent-to-agent coordination |
| **AG-UI** (CopilotKit) | Agent ↔ User | Connects agents to user-facing frontends |

Our stack uses all three simultaneously: MAF agents use MCP-style tools, MAF can orchestrate sub-agents via A2A handshakes, and the gateway emits AG-UI events to our Next.js frontend.

**A2UI** (Google's generative UI spec) and **MCP-UI** (Microsoft + Shopify) are separate generative UI specs that **run on top of AG-UI** — AG-UI is the transport, A2UI/MCP-UI define the UI widget payload schema. Our `Custom` event type is the AG-UI hook for delivering these payloads.

---

### 12.2 Full AG-UI event taxonomy (what we must handle)

Our current `route.ts` translates a subset of AG-UI events. Here is the complete taxonomy against our current support:

#### Lifecycle events

| Event | Our support | Notes |
|---|---|---|
| `RUN_STARTED` | ✅ Emitted, shows ThinkingContainer | |
| `RUN_FINISHED` | ✅ Closes ThinkingContainer | Missing: `outcome.type === "interrupt"` handling |
| `RUN_ERROR` | ✅ Shows error | Missing: structured error code field |
| `STEP_STARTED` | ❌ Not handled | Should show sub-step label inside ThinkingContainer |
| `STEP_FINISHED` | ❌ Not handled | Should close sub-step label |

**Action:** Add `STEP_STARTED` → emit `{type: "progress", content: "Step: {stepName}"}`. This lets orchestrator graph nodes (e.g. LangGraph node names) appear as live progress items.

#### Text message events

| Event | Our support | Notes |
|---|---|---|
| `TEXT_MESSAGE_START` | ✅ | |
| `TEXT_MESSAGE_CONTENT` | ✅ | |
| `TEXT_MESSAGE_END` | ✅ | |
| `TEXT_MESSAGE_CHUNK` | ✅ | Convenience form — auto-expands to Start/Content/End |

#### Tool call events

| Event | Our support | Notes |
|---|---|---|
| `TOOL_CALL_START` | ✅ Emits `tool_start` | |
| `TOOL_CALL_ARGS` | ✅ Accumulates, attaches to `tool_end` | |
| `TOOL_CALL_END` | ✅ Emits `tool_end` | |
| `TOOL_CALL_RESULT` | ❌ Not forwarded to frontend | Should show tool result summary in ThinkingContainer |
| `TOOL_CALL_CHUNK` | ❌ Not handled | Convenience form; should expand same as full triad |

**Action:** Forward `TOOL_CALL_RESULT` as `{type: "tool_result", toolCallId, content}` — allows ThinkingContainer to show "✓ zoho_search → 4 records found".

#### State management events

| Event | Our support | Notes |
|---|---|---|
| `STATE_SNAPSHOT` | ❌ Not handled | Agent can push full state to frontend |
| `STATE_DELTA` | ❌ Not handled | Agent can push JSON Patch incremental state updates |
| `MESSAGES_SNAPSHOT` | ❌ Not handled | Full conversation history sync |

**These are the Shared State / Generative UI foundation.** See section 12.5.

#### Activity events

| Event | Our support | Notes |
|---|---|---|
| `ACTIVITY_SNAPSHOT` | ❌ Not handled | Structured in-progress activity (e.g. PLAN, SEARCH) |
| `ACTIVITY_DELTA` | ❌ Not handled | Incremental updates to an activity via JSON Patch |

**Activities** are structured objects emitted between chat messages — e.g. `{activityType: "SEARCH", results: [...]}`. They differ from state in that they're conversation-thread-scoped, not agent-scoped.

#### Reasoning events

| Event | Our support | Notes |
|---|---|---|
| `REASONING_START` | ✅ (via deprecated THINKING_START mapping) | **Must migrate to REASONING_**** |
| `REASONING_MESSAGE_START` | ✅ (via THINKING_TEXT_MESSAGE_START) | |
| `REASONING_MESSAGE_CONTENT` | ✅ (via THINKING_TEXT_MESSAGE_CONTENT) | |
| `REASONING_MESSAGE_END` | ✅ (via THINKING_TEXT_MESSAGE_END) | |
| `REASONING_MESSAGE_CHUNK` | ❌ | Convenience form — not handled |
| `REASONING_END` | ✅ | |
| `REASONING_ENCRYPTED_VALUE` | ❌ | Privacy-preserving encrypted chain-of-thought; store opaquely |
| `THINKING_*` (all) | ⚠️ DEPRECATED | Remove in favour of REASONING_* before v1.0.0 |

#### Special events

| Event | Our support | Notes |
|---|---|---|
| `RAW` | ❌ Not handled | Pass-through for external system events |
| `CUSTOM` | ❌ Not handled | **This is the hook for A2UI/MCP-UI generative widgets** |

---

### 12.3 Interrupt protocol — world-class approval UI

> **Update 2026-08-01 (doc-truth pass):** SUPERSEDED — this terminal-run + `resume[]`
> design was never built. The shipped HITL model is `request_id` +
> `POST /agent/respond-input`, parking the run on the `_pending_user_input` future
> registry (gateway `routes/agent.py`) with `ElicitationCard`/`ConfirmationCard` in the
> frontend; see `generative_ui_2.md` §2. This section is kept as protocol reference only.

The AG-UI interrupt spec is far more complete than what we studied from CopilotKit. It defines a **terminal run model**: the agent *ends the run* with an interrupt, and the client *starts a new run* with a `resume` array. This is different from pausing mid-stream.

**Interrupt lifecycle (protocol-correct):**

```
Run 1:  RUN_STARTED → ... → TOOL_CALL_START(sendEmail) → TOOL_CALL_ARGS → TOOL_CALL_END
        → STATE_SNAPSHOT (preserve state at interrupt boundary)
        → RUN_FINISHED { outcome: { type: "interrupt", interrupts: [ {...} ] } }

User:   sees approval UI, clicks "Approve" or "Edit & Approve"

Run 2:  RunAgentInput { threadId, resume: [{ interruptId, status: "resolved", payload: { approved: true } }] }
        → RUN_STARTED → TOOL_CALL_RESULT(tc-001) → TEXT_MESSAGE_CONTENT → RUN_FINISHED { outcome: { type: "success" } }
```

**The `Interrupt` object (exact protocol fields):**

```ts
interface Interrupt {
  id: string               // Correlation key for resume
  reason: string           // "tool_call" | "input_required" | "confirmation" | custom
  message?: string         // Human-readable prompt (fallback UI)
  toolCallId?: string      // Links to the prior ToolCallStart/Args/End sequence
  responseSchema?: JsonSchema  // JSON Schema for the expected resume payload
  expiresAt?: string       // ISO-8601 TTL — stale resumes produce RunError
  metadata?: Record<string, any>  // Framework-specific (e.g. LangGraph checkpointId)
}
```

**Resume payload shape (approve-with-edits):**

```ts
// Resume for a tool_call interrupt that supports edits
{
  interruptId: "int-abc123",
  status: "resolved",  // or "cancelled"
  payload: {
    approved: true,
    editedArgs: {      // Optional: full replacement of tool args
      to: "user@example.com",
      subject: "Updated subject"
    }
  }
}
```

**Reason taxonomy for routing UI:**

| `reason` | UI to show | Notes |
|---|---|---|
| `tool_call` | Approve/reject + optional edit form for `editedArgs` | `toolCallId` must be set; agent re-emits `ToolCallResult` not `ToolCallArgs` on resume |
| `confirmation` | Yes/No dialog | Free-standing, not tool-bound |
| `input_required` | Form rendered from `responseSchema` | Agent needs structured input |
| Custom (e.g. `langgraph:workflow_suspend`) | Render from `message` + `responseSchema` + `metadata` | Graceful fallback required |

**Parallel interrupts** (multiple tools awaiting approval simultaneously) must all be addressed in the same resume array — no partial resumes.

**How to implement in our app:**

```tsx
// In useAgentChat.ts — detect interrupt outcome
if (event.type === "RUN_FINISHED" && event.outcome?.type === "interrupt") {
  setMessages(prev => prev.map(m =>
    m.id === currentAssistantMsgId
      ? { ...m, streaming: false, interrupt: event.outcome.interrupts }
      : m
  ));
  setHasPendingInterrupt(true);
  return;
}

// In AgentChat.tsx — render interrupt widget inline above input
{hasPendingInterrupt && pendingInterrupts.map(interrupt => (
  <InterruptWidget
    key={interrupt.id}
    interrupt={interrupt}
    onResolve={(payload) => resumeRun(interrupt.id, "resolved", payload)}
    onCancel={() => resumeRun(interrupt.id, "cancelled")}
  />
))}
```

**`InterruptWidget` renders based on `reason`:**
- `tool_call`: Shows the tool name + args from the prior `ToolCallArgs`, Approve/Reject buttons, optional JSON editor for `editedArgs` if `responseSchema.properties.editedArgs` is present
- `confirmation`: Simple Yes/No
- `input_required`: Auto-generates a form from `responseSchema` using a JSON Schema form library (e.g. `react-jsonschema-form`)
- Unknown: Shows `interrupt.message` + generic OK/Cancel

**Files to create/modify:**
- `src/hooks/useAgentChat.ts` — handle `RUN_FINISHED` interrupt outcome, add `interrupt` to `ChatMessage`, add `resumeRun(interruptId, status, payload?)`
- `src/components/InterruptWidget.tsx` — NEW: renders per interrupt type
- `src/app/api/agent/chat/route.ts` — pass `resume` array from request body to gateway when present
- `apps/gateway/gateway/routes/agent.py` — accept `resume` in request body and forward to executor
- `apps/orchestrator/orchestrator/executor.py` — pass `resume` to agent.run()

**Priority: Critical** — needed for any CRM write, email send, invoice approval.

---

### 12.4 `StepStarted` / `StepFinished` — sub-step visibility

These lifecycle events let the agent label discrete phases of work:

```json
{ "type": "STEP_STARTED", "stepName": "research_phase" }
{ "type": "STEP_STARTED", "stepName": "draft_email" }
{ "type": "STEP_FINISHED", "stepName": "draft_email" }
```

`stepName` can be a LangGraph node name, a MAF agent method name, or any semantic label.

**How to surface in our UI:**
- Translate `STEP_STARTED` → `{type: "step_start", stepName}` SSE event
- Show it as a section header inside the ThinkingContainer: `"── research_phase ──"` (dimmed)
- `STEP_FINISHED` → replace with checkmark `"✓ research_phase"`

This gives users a multi-level view: outer step (e.g. "drafting email") → inner tool calls (e.g. "zoho_search", "gmail_send").

**File to modify:** `src/app/api/agent/chat/route.ts` — add `STEP_STARTED`/`STEP_FINISHED` translation cases.

---

### 12.5 State management — `STATE_SNAPSHOT` / `STATE_DELTA`

AG-UI's state system is the foundation for **Generative UI** and **Shared State**. The agent emits structured state updates, the frontend applies them, and React components re-render accordingly.

**Pattern:**

1. **`STATE_SNAPSHOT`** → replace entire frontend state object  
2. **`STATE_DELTA`** → apply JSON Patch (RFC 6902) operations to current state

The protocol uses `fast-json-patch` (or equivalent) to apply deltas:

```ts
// Pseudo-code for state delta handling in useAgentChat.ts
import { applyPatch } from "fast-json-patch";

case "state_snapshot":
  setAgentState(event.snapshot);
  break;

case "state_delta":
  setAgentState(prev => applyPatch(prev, event.delta, true, false).newDocument);
  break;
```

**Use cases for our agents:**
- `sales` agent: `{ stage: "prospecting", leads_found: 47, qualified: 12, current_action: "Analyzing LinkedIn profile" }` → rendered as a live dashboard card
- `reconciler` agent: `{ tasks_checked: 143, mismatches: [{ id: "task-1", issue: "missing due date" }] }` → rendered as a live mismatch list
- `orchestrator`: `{ active_subagent: "sales-assistant", subagent_stage: "zoho_search" }` → shown in AgentStatusBar

**How to forward in `route.ts`:**

```ts
case "STATE_SNAPSHOT":
  yield `data: ${JSON.stringify({ type: "state_snapshot", snapshot: event.snapshot })}\n\n`;
  break;
case "STATE_DELTA":
  yield `data: ${JSON.stringify({ type: "state_delta", delta: event.delta })}\n\n`;
  break;
```

**Files to create/modify:**
- `src/app/api/agent/chat/route.ts` — forward `STATE_SNAPSHOT` / `STATE_DELTA`
- `src/hooks/useAgentChat.ts` — apply patches with `fast-json-patch`, expose `agentState` from hook
- `src/components/AgentStateDashboard.tsx` — NEW: renders `agentState` as a card (auto-shows when state is non-empty)
- Install: `npm install fast-json-patch` in `workbench/control_plane/`

**Priority:** High for long-running agents (reconciler, prospecting).

---

### 12.6 `TOOL_CALL_RESULT` — show what tools returned

Currently we show `TOOL_CALL_START` (tool name) and accumulate `TOOL_CALL_ARGS`, but we never forward `TOOL_CALL_RESULT` (what the tool returned) to the frontend.

`TOOL_CALL_RESULT` carries:
```ts
{ messageId, toolCallId, content: string, role?: "tool" }
```

`content` is the raw tool output string (e.g. `'[{"id":"deal-1","name":"ACME Corp",...}]'`).

**UI treatment:**
- Inside ThinkingContainer, each tool row currently shows: `🔍 zoho_search → Searching...` → `✓ zoho_search`
- With `TOOL_CALL_RESULT`: `✓ zoho_search → 4 deals found` (parse `content`, infer summary)
- On expand: show truncated raw result (first 200 chars) for debugging

**Summary inference heuristic:**
```ts
function summarizeToolResult(content: string): string {
  try {
    const parsed = JSON.parse(content);
    if (Array.isArray(parsed)) return `${parsed.length} items`;
    if (typeof parsed === "object" && parsed !== null) {
      const keys = Object.keys(parsed);
      if (keys.length === 1) return `${keys[0]}: ${JSON.stringify(parsed[keys[0]]).slice(0, 50)}`;
      return `${keys.length} fields`;
    }
    return String(content).slice(0, 60);
  } catch {
    return String(content).slice(0, 60);
  }
}
```

**File to modify:** `src/app/api/agent/chat/route.ts` — add `TOOL_CALL_RESULT` case that emits `{type: "tool_result", toolCallId, summary}`.

---

### 12.7 Capability discovery — adaptive UI

AG-UI defines a `getCapabilities()` method agents can expose. Our gateway already knows which agents exist, but the frontend doesn't know *what each agent can do* until the first message.

The `AgentCapabilities` interface has typed categories:

```ts
interface AgentCapabilities {
  identity?: { name, description, version, provider }
  transport?: { streaming, websocket, resumable }
  tools?: { supported, items, parallelCalls, clientProvided }
  output?: { structuredOutput, supportedMimeTypes }
  state?: { snapshots, deltas, memory, persistentState }
  multiAgent?: { supported, delegation, subAgents: [{name, description}] }
  reasoning?: { supported, streaming, encrypted }
  multimodal?: { input: { image, audio, video, pdf, file }, output: { image, audio } }
  execution?: { codeExecution, sandboxed, maxIterations, maxExecutionTime }
  humanInTheLoop?: { supported, approvals, interventions, interrupts, approveWithEdits }
}
```

**Adaptive UI based on capabilities:**

```ts
// On agent selection, fetch capabilities and adapt UI
const caps = await fetchAgentCapabilities(agentName);

// Show/hide mic button
setShowVoiceInput(caps?.multimodal?.input?.audio ?? false);

// Show/hide file upload
setShowFileUpload(caps?.multimodal?.input?.image || caps?.multimodal?.input?.file ?? false);

// Show/hide reasoning toggle
setShowReasoningPanel(caps?.reasoning?.supported ?? false);

// Show approval warning badge in input bar
setRequiresApprovals(caps?.humanInTheLoop?.approvals ?? false);

// Show sub-agent selector
setSubAgents(caps?.multiAgent?.subAgents ?? []);
```

**How to expose from our gateway:**

```python
# In apps/gateway/gateway/routes/agent.py
@router.get("/{agent_name}/capabilities")
async def get_agent_capabilities(agent_name: str, ...):
    agent = executor.load_agent(agent_name)
    # Return static capabilities dict per agent (or dynamic if MAF supports it)
    return AGENT_CAPABILITIES.get(agent_name, {})
```

**Files to create/modify:**
- `apps/gateway/gateway/routes/agent.py` — `GET /agent/{name}/capabilities`
- `src/hooks/useAgentCapabilities.ts` — NEW: fetches capabilities on agent switch, caches them
- `src/components/AgentChat.tsx` — gate file upload / voice / approval indicators on capabilities

**Priority:** Medium — enables correct UI adaptation as we add more heterogeneous agents.

---

### 12.8 Frontend-defined tools (reverse direction)

AG-UI supports tools **defined in the frontend** and passed to the agent at runtime. This enables the agent to call frontend actions during a run (navigation, showing a dialog, updating local state) without a server round-trip.

**Example use cases for our app:**

```ts
// Tool: agent can navigate the user to a page
const navigateTool = {
  name: "navigateTo",
  description: "Navigate the user to a specific page in the application",
  parameters: {
    type: "object",
    properties: {
      page: { type: "string", enum: ["dashboard", "deals", "tasks", "settings"] },
      params: { type: "object" }
    },
    required: ["page"]
  }
};

// Tool: agent can open a modal with data it generated
const showRecordTool = {
  name: "showRecord",
  description: "Display a CRM record card to the user",
  parameters: {
    type: "object",
    properties: {
      recordType: { type: "string", enum: ["deal", "contact", "task"] },
      recordId: { type: "string" }
    },
    required: ["recordType", "recordId"]
  }
};
```

When the agent calls `navigateTo`, the frontend handles it locally — no gateway call needed. This is fundamentally different from the backend tools (zoho_search, etc.) which the MAF agent calls server-side.

**How to implement:**

1. Pass `tools` array in `RunAgentInput` when calling `/agent/run/stream`
2. When a `TOOL_CALL_START` arrives with a frontend tool name, execute it locally instead of showing it in ThinkingContainer
3. Inject `TOOL_CALL_RESULT` back into the SSE stream (or POST to a tool result endpoint)

**Files to modify:**
- `src/hooks/useAgentChat.ts` — maintain `frontendTools` registry, intercept `tool_start` events for known frontend tools
- `src/app/api/agent/chat/route.ts` — pass tools in gateway request body

**Priority:** Medium-High — enables agent-driven navigation and UI actions.

---

### 12.9 Activity events — structured in-progress information

`ACTIVITY_SNAPSHOT` / `ACTIVITY_DELTA` are distinct from state: they represent **discrete, in-flight work items** visible in the chat thread (between messages), not global agent state.

Example: a `PLAN` activity shows the agent's execution plan as it builds:

```json
{ "activityType": "PLAN",
  "content": {
    "steps": [
      { "id": 1, "label": "Search Zoho for deals", "status": "done" },
      { "id": 2, "label": "Filter by stage", "status": "active" },
      { "id": 3, "label": "Draft summary email", "status": "pending" }
    ]
  }
}
```

A `SEARCH` activity shows live results as they stream:

```json
{ "activityType": "SEARCH",
  "content": { "query": "deals in Awaiting PO", "results": [...] }
}
```

**UI pattern:** Render as a compact card **in the message thread** (not in the ThinkingContainer). Activity cards are collapsible and persist after the run completes.

**Files to create/modify:**
- `src/app/api/agent/chat/route.ts` — forward `ACTIVITY_SNAPSHOT` / `ACTIVITY_DELTA`
- `src/hooks/useAgentChat.ts` — maintain `activities` map keyed by `messageId`, apply patches with `fast-json-patch`
- `src/components/ActivityCard.tsx` — NEW: renders an activity (plan, search, etc.) inline in the chat

**Priority:** Low initially, but becomes important as agents emit structured plans.

---

### 12.10 Generative UI via `CUSTOM` events (A2UI / MCP-UI / Open-JSON-UI)

The AG-UI `Custom` event is the hook for delivering UI component payloads from the agent:

```json
{ "type": "CUSTOM", "name": "a2ui_widget", "value": { ... } }
{ "type": "CUSTOM", "name": "mcp_ui_frame", "value": { ... } }
```

**A2UI** (Google): JSONL-based, streaming, declarative widget trees. Agents emit JSON fragments that describe a UI component hierarchy (like React elements but in JSON). The client renders them using a registered widget library.

**MCP-UI** (Microsoft + Shopify): iframe-based, fully sandboxed. Agent returns an HTML/JS bundle inside an iframe. More powerful (full custom code), but higher trust requirements.

**For our app:** We should implement a `CUSTOM` event passthrough and a `CustomWidget` renderer:

```ts
// In route.ts:
case "CUSTOM":
  yield `data: ${JSON.stringify({ type: "custom", name: event.name, value: event.value })}\n\n`;
  break;

// In useAgentChat.ts:
// Append { type: "custom", name, value } to message.customWidgets[]

// In MarkdownMessage.tsx:
{message.customWidgets?.map(widget => (
  <CustomWidget key={widget.id} name={widget.name} value={widget.value} />
))}
```

The `CustomWidget` component can dispatch on `name`:
- `"deal_card"` → renders a compact CRM deal record
- `"email_draft"` → renders an editable email form
- `"a2ui_widget"` → renders A2UI declarative JSON tree
- Unknown → renders `<details><pre>{JSON.stringify(value)}</pre></details>`

**Priority:** Medium-High — this is the path to rich embedded UIs without a full generative-UI framework dependency.

---

### 12.11 `REASONING_ENCRYPTED_VALUE` — privacy-compliant reasoning

For enterprise deployments where chain-of-thought must not be stored in plaintext (GDPR right to erasure, SOC2, HIPAA minimum necessary):

1. Agent encrypts the full chain-of-thought server-side before sending
2. Emits `REASONING_ENCRYPTED_VALUE { subtype: "message"|"tool-call", entityId, encryptedValue }`
3. Frontend stores the opaque blob alongside the `ReasoningMessage`
4. On subsequent turns, client sends back the `ReasoningMessage.encryptedValue` in the messages array
5. Agent decrypts server-side to restore reasoning context

**Our current state:** We don't handle `REASONING_ENCRYPTED_VALUE`. We should store it in `ChatMessage.reasoning` alongside the visible text.

**Files to modify:**
- `src/app/api/agent/chat/route.ts` — forward `REASONING_ENCRYPTED_VALUE` as `{type: "reasoning_encrypted", entityId, encryptedValue}`
- `src/hooks/useAgentChat.ts` — store `encryptedReasoning` per message; send back in next run's message history

**Priority:** Low until we have enterprise customers with ZDR requirements, but note the architecture.

---

### 12.12 Migrate THINKING_* → REASONING_* events (breaking in v1.0.0)

The `THINKING_*` event family is deprecated in the current AG-UI spec and will be removed in v1.0.0:

| Deprecated | Replacement |
|---|---|
| `THINKING_START` | `REASONING_START` |
| `THINKING_END` | `REASONING_END` |
| `THINKING_TEXT_MESSAGE_START` | `REASONING_MESSAGE_START` |
| `THINKING_TEXT_MESSAGE_CONTENT` | `REASONING_MESSAGE_CONTENT` |
| `THINKING_TEXT_MESSAGE_END` | `REASONING_MESSAGE_END` |

Our `route.ts` currently maps `THINKING_*` to reasoning SSE events. We need to also handle `REASONING_*` natively. MAF may emit either form depending on which version of `agent_framework.ag_ui` is installed.

**File to modify:** `src/app/api/agent/chat/route.ts` — handle both `REASONING_*` and legacy `THINKING_*` until MAF upgrades.

**Priority:** High — prevents breakage when MAF upgrades to AG-UI v1.0.0.

---

### 12.13 `parentRunId` — branching and time travel

`RunStarted` gains `parentRunId` in the extended protocol. This creates a **git-like append-only log** of runs within a thread:

```
Thread: thread-abc
  ├── run-1 (initial)
  ├── run-2 (parentRunId: run-1) — interrupt resume
  ├── run-3 (parentRunId: run-1) — time travel: fork from run-1
  └── run-4 (parentRunId: run-2) — continue from run-2
```

**Use case:** "Regenerate from here" — the user clicks an earlier message, starts a new run with `parentRunId` pointing to the run that produced that message. The thread diverges but history is preserved.

This is also the mechanism for **undo** — rewind to a prior run state.

**For our app:** This should inform how we implement the `regenerate` button in `MessageActionBar`. Instead of deleting and re-queuing, we should start a new run with `parentRunId = message.runId` (once we track `runId` per message).

**Priority:** Low — architectural note for when we implement regeneration.

---

### 12.14 Summary: AG-UI-specific implementation backlog

| # | Feature | Protocol hook | Our files | Priority |
|---|---|---|---|---|
| **I** | Interrupt protocol (approval UI) | `RUN_FINISHED.outcome.interrupts` + `RunAgentInput.resume` | `useAgentChat.ts`, `InterruptWidget.tsx`, `route.ts`, gateway `agent.py` | **Critical** |
| **II** | `REASONING_*` migration (from THINKING_*) | `REASONING_START/MESSAGE_CONTENT/END` | `route.ts` | **High** |
| **III** | `TOOL_CALL_RESULT` forwarding | `TOOL_CALL_RESULT` | `route.ts`, `ThinkingContainer.tsx` | **High** |
| **IV** | State snapshot/delta (shared state) | `STATE_SNAPSHOT` / `STATE_DELTA` | `route.ts`, `useAgentChat.ts`, `AgentStateDashboard.tsx` | **High** |
| **V** | `STEP_STARTED` / `STEP_FINISHED` | lifecycle events | `route.ts`, `ThinkingContainer.tsx` | **Medium** |
| **VI** | `CUSTOM` event passthrough (generative UI) | `CUSTOM` | `route.ts`, `CustomWidget.tsx` | **Medium** |
| **VII** | Capability discovery | `GET /agent/{name}/capabilities` | `useAgentCapabilities.ts`, gateway | **Medium** |
| **VIII** | Frontend-defined tools | `RunAgentInput.tools` | `useAgentChat.ts`, `route.ts` | **Medium** |
| **IX** | Activity events | `ACTIVITY_SNAPSHOT` / `ACTIVITY_DELTA` | `route.ts`, `ActivityCard.tsx` | **Low** |
| **X** | `REASONING_ENCRYPTED_VALUE` | `REASONING_ENCRYPTED_VALUE` | `route.ts`, `useAgentChat.ts` | **Low (enterprise)** |
| **XI** | `parentRunId` / time travel | `RUN_STARTED.parentRunId` | `useAgentChat.ts`, `MessageActionBar.tsx` | **Low** |
