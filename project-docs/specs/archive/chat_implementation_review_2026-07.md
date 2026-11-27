# Chat Implementation Review — MAF + Copilot SDK (2026-07-02)

> **What this is.** A comprehensive audit of the chat stack — SSE streaming,
> HITL, chat resume, and multi-agent handoffs — across both agent runtimes
> (native MAF and GitHub Copilot SDK), combining three targeted code audits
> (backend stream lifecycle · frontend chat surface · handoffs + docs) with
> the defects already found and fixed this week (HITL stall watchdogs,
> answer-in-consciousness fold bugs, per-message ASSISTANT_MESSAGE dedup).
> Use the priority tables as the work queue; §5 has the three strategic
> refactors that eliminate whole bug classes rather than instances.
>
> **Scope of truth:** file:line refs are as of `main` @ 2026-07-02. Sibling
> docs: `chat_ux.md` (UX spec + AG-UI backlog, deleted 2026-10-27),
> [`../system_architecture.md`](../system_architecture.md) (ADRs — see §6
> drift corrections).
>
> **Status (2026-07-02, same day):** three hardening batches landed on `main`
> with regression coverage in `tests/unit/test_chat_hardening.py`:
> - **Batch 1** `3b9d3c8` — ✅ P0-1 (reconnect owner check) · ✅ P0-2 (active-key
>   refresh, `xx=True`) · ✅ P0-4 (per-stream persist id) · ✅ P0-5 (real tool
>   status) · ✅ P1-8 (session upsert attributes acting user).
> - **Batch 2** `d2de4d2` — ✅ P0-7 (delegation depth/cycle guard,
>   `SUB_AGENT_MAX_DEPTH`) · ✅ P0-8 (sub-agent `on_user_input_request`
>   binding) · ✅ P1-4 (`SUB_AGENT_TIMEOUT_SECONDS` wall-clock budget +
>   background-child cancel cascade via `register_background_child`).
> - **Batch 3** `20a7112` — ✅ P0-6 (sub-agent timeline persisted +
>   `applySubAgentEvent` shared by live/reconnect loops) · ✅ P1-1 (current
>   turn excluded from history + `withoutCurrentTurn` server-side) · ✅ P1-3
>   (HITL cards reset on session switch; respond-input failures restore the
>   card).
>
> - **Batch 4** (2026-07-02, core_loop_unification Phase 1) — ✅ P0-3 for the
>   `/agent/run/stream` path: gateway-side fold-and-persist at run end via
>   `run_detached(on_complete=)` + `gateway/chat_fold.py` (fold port of
>   route.ts/chatStream.ts, locked by trajectory evals). `/copilot/chat`
>   orchestrator path lands with unification Phase 2.
>
> - **Batch 5** (2026-07-02, unification Phases 2–3a) — ✅ P0-3 remainder
>   (`/copilot/chat` fold-and-persist via query-param message id) · ✅ §5
>   refactor 1 (one event translator, `orchestrator/event_translator.py`) ·
>   ✅ tier asymmetries (TODO_LIST/elicitation/PARTIAL/message-id framing) ·
>   ✅ P1-9 (memory extraction moved to the gateway run boundary for the
>   named-agent path) · real message-segment boundaries emitted end-to-end
>   (Phase 3a; segment-native rendering = 3b, pending).
>
> Still open: P1-2, P1-5, P1-6, P1-7, the P2 list, §5 refactor 3's client
> half (3b in `core_loop_unification.md`, deleted 2026-10-27),
> unified watchdog policy, §6 doc drift.

---

## 0. Verdict

The architecture is sound and the recent fixes hold: one Redis-relayed SSE
stream with reconnect, a shared client reducer (`chatStream.ts`), blocking
HITL futures, and tool-injected delegation all work end-to-end on both
runtimes. The systemic weakness is **N-way duplication**: the same
event-translation, persistence, and watchdog logic exists in 3–4 places
(native MAF Tier 1 · Copilot Tier 1.5 · Tier 2 batch shim · sub-agent
streaming; client reducer + server translator), and every chat bug fixed this
week — and most found by this audit — is a *drift* between those copies. The
highest-leverage move is consolidation, not more patches.

Health by area: **SSE streaming** B (works; drift + 1h-TTL landmine) ·
**HITL** B− (main path solid post-fixes; sub-agent + multi-worker gaps) ·
**Resume** C+ (reconnect works; persistence has data-loss holes) ·
**Handoffs** C (works for the happy path; no guards, big losses at the
boundary) · **Docs** C (three major promised-vs-built drifts).

---

## 1. P0 — fix first (security + data loss)

| # | Finding | Where | Fix |
|---|---|---|---|
| P0-1 | **Reconnect endpoint has no ownership check** — any authenticated user can replay any thread's full stream (text, tool args, reasoning) from Redis | `gateway/routes/agent.py` reconnect (~:1219) vs `cancel`'s `_thread_owner_ok` (:1355) | apply the same owner check before replay |
| P0-2 | **Long runs die at 1 hour** — `cc:active:{thread}` is set with `ex=3600` and never refreshed; when it lapses mid-run, subscribers terminate and reconnect emits a **synthetic RUN_FINISHED** for a still-running agent | `stream_relay.py:261` (set) vs `:105` (push refreshes stream key only) | refresh the active key on every `push_event`, or derive liveness from `_DETACHED_TASKS` |
| P0-3 | **Nobody persists assistant messages server-side.** Persistence lives only in the Next.js translator; browser gone + no reconnect ⇒ the detached run finishes into Redis and the tail is lost after TTL | `route.ts:98-143` is the only writer; executor never writes `chat_message` | persist the final message in the detached task's `finally` (gateway-side), translator remains the live-path writer |
| P0-4 | **Missing `assistantMessageId` collapses all assistant turns into one row** (`assistant-${threadId}` fallback + upsert) | `route.ts:114` + `chat.py:248` | require/mint a unique id server-side |
| P0-5 | **Failed tools are persisted as `status:"done"`** — after reload a failed tool renders as succeeded | `route.ts:295` (persist) vs `:307` (live honors `success`) | one-line: honor `ev.success` in the persisted copy |
| P0-6 | **Sub-agent activity is never persisted and dropped on reconnect** — nested timelines exist only in the live loop; reload/refresh mid-delegation loses them | persist shape omits sub-agent fields (`route.ts:291-299`, `:317-324`); reconnect loop lacks `sub_agent_*` cases (`useAgentChat.ts:693-768`) | persist sub-agent state on the parent delegate ToolEvent + share sub-agent handling between both loops |
| P0-7 | **No delegation depth limit or cycle guard** — sub-agents get the full `call_agent` family; A→B→A recursion with `call_agents_parallel` fans out 5^depth | `executor.py:572-576` (unconditional injection), plan's "loop detector" unbuilt | depth ContextVar (max 2–3) + visited-agent set |
| P0-8 | **Native `ask_user` inside a Copilot-SDK sub-agent is a black hole** — `on_user_input_request` is never bound for sub-agents, yet the sub-agent addendum advertises the tool | `executor.py:1046-1066` (sub-agent setup) vs `:2537-2542` (main path binding) | bind `_make_user_input_handler(relay thread)` in `_run_sub_agent_streaming` |

## 2. P1 — correctness / trust

| # | Finding | Where | Fix |
|---|---|---|---|
| P1-1 | **Duplicate user message sent to the model** on copilot/executor paths (history still contains the just-appended turn AND `message` is sent) | `useAgentChat.ts:235-247` + `route.ts:630-694` (only litellm dedups :846) | exclude current turn from history or dedup server-side everywhere |
| P1-2 | **Multi-worker breaks Stop + HITL silently** — `_DETACHED_TASKS`, `_pending_user_input`, `_copilot_session_store` are per-process; cancel/respond-input landing on another worker no-op (cancel still emits RUN_FINISHED → UI "stops", backend keeps burning) | `stream_relay.py:326`, `executor.py:205, 4405` | move control state to Redis, or pin/enforce single worker and assert it at boot |
| P1-3 | **HITL cards leak across session switches** (component state never reset on `sessionId` change) and **blocking answers fire-and-forget** (`.catch(()=>{})` — failed respond-input = parked agent, no card, no error) | `AgentChat.tsx:599-623`, `:1302/:1368/:1421` | clear on session switch; surface respond-input failures + restore card |
| P1-4 | **No timeout around sub-agent runs** (a slow-but-not-idle sub-agent holds the parent open) and **`call_agent_background` children escape cancel** (keep burning after Stop) | `executor.py:1153-1200`, `agent_tools.py:285` | `wait_for` + `SUB_AGENT_ERROR`; register background children under the parent thread for cancel cascade |
| P1-5 | **Reconnect placeholder gets a new message id and persists under it** → duplicate assistant rows for one turn; polling merge is content-prefix-based and fights the un-fold | `useAgentChat.ts:594/:633`, `:979-994` | stable per-turn id; id-based reconciliation |
| P1-6 | **Copilot session continuity gaps**: fire-and-forget Postgres writes on a deprecated loop API (`executor.py:4464`), model-store not durable (restart ⇒ model-switch undetected ⇒ resume on wrong model, `:4411/:2554`), Copilot-SDK-agents-registered-as-maf skip session storage entirely (`:3278-3287`), over-broad stale-session retry can double-stream output (`:3344`) | executor session store | await the writes; persist last-model; key storage off `_is_copilot_sdk` not runtime string; gate retry to before-first-emit |
| P1-7 | **chatStore never evicts** — unbounded growth across sessions in long-lived tabs | `chatStore.ts:97-108` | LRU/evict listener-less inactive sessions |
| P1-8 | **`user_id="system"` session upsert can 403 the owner's cancel** during the race before frontend upsert | `executor.py:4459` + `agent.py:1323` | pass acting user into `_store_session_id` |
| P1-9 | **Memory extraction never runs on the reconnect path** (and litellm) — conversations completed after a refresh contribute nothing to Mem0 | `route.ts:594-601` vs `:674/:729` | extract after reconnect persistence too |

## 3. P2 — UX robustness (selected)

- **Punctuation heuristic for "interrupted"** (`content` not ending `.?!`) misfires on code-block/list answers → spurious "Reconnecting…" up to 45s (`useAgentChat.ts:515, :860, :1025`). Use the tracked `streaming`/`done` signal.
- **Polling effect re-subscribes on every token** (`deps: [messages]`, `:1051`) — churn; use refs + coarse deps.
- **`lastEventId` is effectively always 0-0** (initial frames carry no `_stream_id`) → every reconnect is a full O(n) replay + re-translate (`useAgentChat.ts:649`, `route.ts:347`). Emit stream ids from the first frame.
- **Steer resurrection**: locally-dropped streaming message keeps persisting server-side and returns via polling (`AgentChat.tsx:988` vs `route.ts:369`).
- **Tier feature asymmetry**: TODO_LIST parsing, the elicitation Future-bridge, TOOL_CALL_PARTIAL, PROGRESS_UPDATE, and INTENT exist only on Tier 1.5 (Copilot); native MAF + Tier 2 silently degrade (`executor.py:3053-3272` vs elsewhere). Tier 2's "streaming" is cosmetic word-splitting after completion (`:3783`).
- **Sub-agent attribution**: injected-tool events (todos, artifacts, elicitations) fired inside a sub-agent surface un-wrapped in the parent stream — a sub-agent's todo panel overwrites the parent's; parallel sub-agents' HITL cards are indistinguishable; concurrent sub-agent deltas merge into the last `subAgentActive` tool row (`useAgentChat.ts:308`).
- **Handoff losses by design worth revisiting**: sub-agent gets message-string only (no compacted history/memory option); parent gets text truncated at 8000 chars with no artifact manifest; `think_mode` not propagated (model tier is); fresh Copilot session per delegation (no `(parent_thread, sub_agent)` reuse — direct cost/latency waste).
- **`request_confirmation` fails OPEN** (auto-approve `True`) when no delivery channel exists — fail closed for irreversible actions (`ask_tools.py:402`).
- **`_hitl_pending()` is global** — any parked question suppresses the Copilot stall detector for *all* concurrent runs (`copilot_agent.py:44`). Scope by thread when control state moves to Redis (P1-2).

## 4. What is healthy (keep)

- The **relay-with-reconnect** shape (detached run + Redis stream + replay) is the right architecture for resumable chat; ownership + TTL fixes make it solid.
- **Blocking-Future HITL** (ask_user/ask_questions/respond-input) post-fixes is reliable on the main path and simpler than the documented interrupt protocol.
- **`chatStream.ts` extraction** proved its worth — extend it rather than invent new reducers.
- Tool injection + system-message addendum, BYOK provider forwarding, per-message ASSISTANT_MESSAGE dedup, HITL-aware watchdogs (this week's fixes).

## 5. Strategic refactors (eliminate bug classes)

1. **One event translator.** Extract the content→SSE mapping (function_call /
   function_result / text / text_reasoning / todo / elicitation / partial /
   intent) into a single module used by all four streaming paths. Every
   consciousness/HITL bug this week was four-way drift; §2/§3's tier
   asymmetries are the same disease. Include one watchdog policy
   (idle/tool/HITL budgets) parameterized per tier instead of three
   implementations.
2. **One persistence owner.** Move authoritative assistant-message persistence
   server-side (gateway writes on stream events + final in the detached task's
   `finally`); the Next translator stops persisting and only translates. Kills
   P0-3/4/5/6-persist, F22-timestamp, steer-resurrection, and the dual-reducer
   drift (`route.ts` accumulation vs `chatStream.ts`) in one move.
3. **Message-id-native protocol.** The narration-vs-answer split is currently
   *inferred* by the fold heuristic client- and server-side, but the runtimes
   know real message boundaries (message_id — already used for dedup). Emit
   `TEXT_MESSAGE_START/END` with ids consistently on all tiers and render each
   assistant message as its own timeline entry; the fold/un-fold heuristic
   (and its whole bug family) becomes unnecessary.

## 6. Doc drift to correct

- `system_architecture.md` ADR-026/§frontend still describes **CopilotKit
  AG-UI-native frontend**; reality is the custom `route.ts` translator +
  `/agent/run/stream` (CopilotKit stripped — `chat_ux.md:415`).
- Docs promise **MAF `HandoffBuilder`/`delegate_to_agent`**; shipped mechanism
  is injected `call_agent` + `_run_sub_agent_streaming`.
- `chat_ux.md` marks the **AG-UI interrupt/resume protocol** Critical with a
  `POST /agent/run/resume` endpoint; the blocking-Future HITL shipped instead
  and no resume endpoint exists. Decide: mark superseded, or build it.
- `agent_framework.ag_ui.stream_agent_response` not being exported (the reason
  Tier 1's bespoke translation exists — `executor.py:2564`) is a load-bearing
  known issue recorded only in a code comment; log it in project_plan.
- AG-UI event backlog (STATE_SNAPSHOT/DELTA, STEP_*, REASONING_* migration) is
  honestly tracked in `chat_ux.md` §11–12 — unchanged, still open.

## 7. Suggested sequencing (PC development)

1. **Security/data-loss day**: P0-1 authz · P0-2 TTL · P0-5 one-liner ·
   P0-4 id · P1-8 owner. Small, independent, high value.
2. **Persistence consolidation** (strategic refactor 2) — subsumes P0-3/6 and
   several P1/P2 items; do before adding chat features.
3. **HITL hardening**: P0-8 sub-agent binding · P1-3 card lifecycle · fail-closed
   confirmation · (with P1-2 Redis control state if multi-worker is planned).
4. **Handoff guards**: P0-7 depth/cycles · P1-4 timeouts+cancel-cascade · then
   the cost wins (session reuse per sub-agent, artifact manifest).
5. **Translator unification** (strategic refactor 1) once the above stabilizes;
   message-id-native rendering (refactor 3) rides on it.
6. Docs pass (§6) alongside.
