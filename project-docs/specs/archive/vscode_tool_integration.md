# VS Code Tool Integration — Implementation Plan

**Date**: 2026-06-17 · **Updated**: 2026-06-29
**Status**: ✅ Implemented incl. the localStorage / Postgres / reconnect persistence fixes (§4 — all done). **Open:** a live end-to-end pass to confirm todos + HITL cards + tool results render and survive refresh/reconnect in the running app (§7).

---

## 1. Overview

Integrate 6 VS Code Copilot Chat tools into Metorite to improve UX:

| Priority | Tool | VS Code Name | Metorite Name | Status |
|---|---|---|---|---|
| 🔴 High | HITL questions | `vscode_askQuestions` | `ask_questions` | ✅ Backend + Frontend |
| 🔴 High | Error checking | `get_errors` | `get_errors` | ✅ Backend |
| 🔴 High | Repo-scoped memory | `memory` (repo scope) | `save_note` / `recall_notes` | ✅ Backend |
| 🟡 Medium | Session history | `session_store_sql` | `query_history` | ✅ Backend |
| 🟡 Medium | GitHub search | `github_text_search` / `github_repo` | `github_search` / `github_repo_search` | ✅ Backend |
| 🟡 Medium | Inline images | `view_image` | Already covered | ✅ N/A |

---

## 2. Architecture

### 2.1 Event Flow (all tools follow this pattern)

```
Agent calls tool(args)
  │
  ├─ Tier 1 (Copilot SDK): CLI subprocess executes tool
  │     └─ function_call intercepted in executor.py → SSE event emitted
  ├─ Tier 2 (MAF): Python function executes in-process
  │     └─ Tool pushes event to _active_run_queue → drain loop → SSE
  │
  ▼
executor.py → AG-UI SSE event (TODO_LIST, CUSTOM, etc.)
  │
  ▼
route.ts → translateAndPersistStream() → frontend SSE event
  │
  ▼
useAgentChat.ts → update ChatMessage state + emitAgentEvent()
  │
  ▼
AgentChat.tsx → React state → renders component
```

### 2.2 Tool Injection

All tools injected via `_inject_agent_tools()` in `executor.py`:
- Wrapped with `normalize_tools()` for Copilot SDK compatibility
- Added to `agent._tools` (GitHubCopilotAgent), `agent.tools` (MAF Agent)
- Tool descriptions appended to system message via `_build_injected_tools_addendum()`

---

## 3. UI/UX Checklist

### 3.1 TodoPanel — "Todos (n/m)" above chat input

- [x] **Renders above input**: ✅ Already inside `<form>`, above the input pill
- [x] **Collapsible**: ✅ Toggle expand/collapse with ▼ arrow
- [x] **Status icons**: ✅ Green check (done), Blue pulse (in-progress), Empty circle (pending)
- [x] **Auto-collapse**: ✅ Auto-collapses 1.5s after all done (unless user toggled)
- [x] **Shows latest turn only**: ✅ Iterates messages in reverse, stops at user message
- [x] **Live updates during streaming**: ✅ `running` prop based on `m.streaming`
- [x] **Survives page refresh**: ✅ `todos` added to `PersistedMessage` type + all persistence points
- [x] **Survives reconnect**: ✅ `todos` passed to `persistAssistantMessage()` in route.ts

### 3.2 ElicitationCard — HITL question card

- [x] **Renders inline in chat thread**: After messages list, before suggestions
- [x] **Options as buttons**: With ⭐ for recommended, ✓ for selected
- [x] **Multi-select checkboxes**: When `multiSelect: true`
- [x] **Freeform text input**: Textarea when `allowFreeformInput: true`
- [x] **Submit formats answers**: Formatted as `[Header]\nSelected: ...\nAnswer: ...`
- [x] **Clears on submit**: `setElicitation(null)` after submit
- [x] **Handled in route.ts**: `CUSTOM` events forwarded
- [x] **Handled in useAgentChat.ts**: `emitAgentEvent("onCustomEvent", ...)`
- [x] **Handled in AgentChat.tsx**: `useAgentEvents` → `setElicitation`

### 3.3 Tool result rendering

- [x] **get_errors output**: Returns text — renders in MarkdownMessage
- [x] **query_history output**: Returns JSON — renders in MarkdownMessage
- [x] **github_search output**: Returns text — renders in MarkdownMessage
- [x] **save_note/recall_notes output**: Returns text — renders in MarkdownMessage

---

## 4. Persistence Gaps (TO FIX)

### 4.1 Todos not in localStorage persistence ✅ FIXED

**File**: `src/lib/sessions.ts`
```typescript
// PersistedMessage now includes:
todos?: { id: string; title: string; status: string }[];
```

**File**: `src/components/AgentChat.tsx`
```typescript
// Both useEffect saveMessages() and beforeunload handler now include todos
```

### 4.2 Todos not in Postgres persistence ✅ FIXED

**File**: `src/app/api/agent/chat/route.ts`
```typescript
// persistAssistantMessage() now accepts todos parameter
// translateAndPersistStream() now accumulates and passes todos
```

### 4.3 Todos not in reconnect recovery ✅ FIXED

**File**: `src/lib/sessions.ts`
```typescript
// fetchMessagesFromDb() now includes todos in the remote type + mapping
```

---

## 6. Frontend Rendering Verification

### 6.1 How each tool output renders

| Tool | Output Type | Frontend Component | How It Renders |
|---|---|---|---|
| `manage_todo_list` | `TODO_LIST` SSE event | `TodoPanel.tsx` | Collapsible panel ABOVE chat input with check icons, progress count, auto-collapse |
| `ask_questions` | `CUSTOM` SSE event → `elicitation_requested` | `ElicitationCard.tsx` | Inline card in chat thread with option buttons, ⭐ recommended, multi-select, freeform textarea, Submit button |
| `get_errors` | Text (tool result) | `ThinkingContainer.tsx` + `MarkdownMessage.tsx` | Tool row in thinking timeline (expandable to see result) + final text in message body |
| `save_note` / `recall_notes` | Text (tool result) | `ThinkingContainer.tsx` + `MarkdownMessage.tsx` | Tool row + result text |
| `query_history` | JSON text (tool result) | `ThinkingContainer.tsx` + `MarkdownMessage.tsx` | Tool row + JSON formatted as code block |
| `github_search` / `github_repo_search` | Text with URLs (tool result) | `ThinkingContainer.tsx` + `MarkdownMessage.tsx` | Tool row + text with clickable links |

### 6.2 ThinkingContainer tool row rendering

The `ThinkingContainer` renders EVERY tool call as a row in a chronological timeline:
- **Color-coded**: Run=purple, Read=blue, Search=amber, Edit=green, Delegate=rose
- **Expandable**: Click to see tool args + result
- **Terminal card**: Shell commands render in a macOS-style terminal window with syntax highlighting
- **Timing**: Shows duration for long-running tools
- **Auto-expand**: Running tools auto-expand to show live output
- **Summary title**: "Ran 2 commands, read 1 file" after completion

### 6.3 Dual runtime support

Both MAF and Copilot SDK agents can use ALL tools:

| Runtime | Tool Execution | Event Emission |
|---|---|---|
| **GitHub Copilot SDK** (Tier 1) | CLI subprocess executes tool | `function_call`/`function_result` intercepted in `executor.py` → SSE events |
| **MAF Agent** (Tier 2) | `_make_tool_shim` wraps in-process calls | Shim pushes `TOOL_CALL_START`/`TOOL_CALL_RESULT` to drain queue → SSE events |
| **Special tools** (`ask_questions`, `manage_todo_list`) | Same as above + extra interception | Tier 1: intercepted at `function_call` for immediate CUSTOM/TODO_LIST events. Tier 2: tool pushes to `_active_run_queue` alongside shim events |

| File | Change |
|---|---|
| `packages/acb_skills/acb_skills/ask_tools.py` | New — `ask_questions` tool |
| `packages/acb_skills/acb_skills/error_tools.py` | New — `get_errors` tool |
| `packages/acb_skills/acb_skills/note_tools.py` | New — `save_note`, `recall_notes` |
| `packages/acb_skills/acb_skills/history_tools.py` | New — `query_history` tool |
| `packages/acb_skills/acb_skills/github_tools.py` | New — `github_search`, `github_repo_search` |
| `packages/acb_skills/acb_skills/__init__.py` | Modified — exports all new tools |
| `apps/orchestrator/orchestrator/executor.py` | Modified — injects all tools, Tier 1 interception for ask_questions, updated addendum |
| `workbench/control_plane/src/components/ElicitationCard.tsx` | New — HITL question card |
| `workbench/control_plane/src/components/AgentChat.tsx` | Modified — elicitation state + rendering |
| `packages/AGENTS.md` | Modified — DOX pass |
| `apps/orchestrator/AGENTS.md` | Modified — DOX pass |
| `workbench/AGENTS.md` | Modified — DOX pass |

*(The persistence work formerly listed here — `sessions.ts`, `AgentChat.tsx`, `route.ts` — is
complete; see §4, all marked ✅ FIXED.)*

---

## 7. Open: live end-to-end verification

Implementation and persistence are done. What remains is a manual pass in the running app
(not yet signed off):

- [ ] Todos render above the input, update live during streaming, and **survive page refresh + reconnect** (localStorage + Postgres rehydrate).
- [ ] `ask_questions` HITL card renders inline, submits, and the answer round-trips to the agent on both runtimes (Copilot SDK + MAF).
- [ ] `get_errors` / `query_history` / `github_search` / `save_note` / `recall_notes` results render in the thinking timeline + message body.
- [ ] Repo-scoped memory (`save_note` / `recall_notes`) persists across sessions.

Once verified, flip the status line to fully ✅ and date it.
