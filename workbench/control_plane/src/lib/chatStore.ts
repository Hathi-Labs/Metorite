/**
 * chatStore — module-level singleton that holds streaming chat state for every session.
 *
 * Why: React component state is destroyed on unmount (navigation away / session switch).
 * By storing messages + stream state here, active SSE loops continue writing even when
 * the component is unmounted, and remounting immediately reflects the current state.
 *
 * Usage (via useSyncExternalStore in useAgentChat.ts):
 *   const state = useSyncExternalStore(
 *     (l) => subscribeSession(id, l),
 *     () => getSessionState(id),
 *   );
 */

import { IDLE_RUN, reduceRun, type RunEvent, type RunStatus } from "@/lib/runStatus";
import { CARD_ANSWERED, type CardLogEntry } from "@/lib/cardReplay";

// ── ChatMessage type (co-located here to avoid circular imports) ────────────

export type ToolEventStatus = "running" | "done" | "error";

export interface SubAgentTool {
  id: string;
  name: string;
  status: ToolEventStatus;
  result?: string;
}

export interface ToolEvent {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result?: string;
  status: ToolEventStatus;
  startedAt?: number;
  endedAt?: number;
  /** Number of reasoning blocks that existed when this tool started.
   *  Lets the UI interleave reasoning text and tool calls chronologically
   *  (VS Code-style timeline) without a separate timeline structure —
   *  this field persists through the existing tool_events JSONB column. */
  reasoningCutoff?: number;
  /** Number of message SEGMENTS captured when this tool started (Phase 3b,
   *  segment-native rendering). Mirrors reasoningCutoff so the renderer can
   *  interleave real assistant segments with tools chronologically. Only set
   *  when the runtime supplied real message ids; absent for id-less streams
   *  (litellm/langgraph), which fall back to the reasoningBlocks fold. */
  segmentCutoff?: number;
  /** True while a delegated sub-agent is still running. */
  subAgentActive?: boolean;
  /** Name of the sub-agent being delegated to. */
  subAgentName?: string;
  /** Accumulated streaming text from the sub-agent. */
  subAgentText?: string;
  /** Tool calls made inside the sub-agent. */
  subAgentTools?: SubAgentTool[];
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  /** True while the assistant is still streaming tokens. */
  streaming?: boolean;
  toolEvents?: ToolEvent[];
  progressLines?: string[];
  isThinkingActive?: boolean;
  /** Sequential reasoning blocks — each displayed as a separate timeline entry. */
  reasoningBlocks?: string[];
  /** Real assistant-message segments (Phase 3a, message-id-native): one entry
   *  per TEXT_MESSAGE_START/END pair the runtime emitted, in order. Captured
   *  additively — the renderer still uses content/reasoningBlocks until 3b
   *  switches to segment-native rendering and the fold heuristic is deleted. */
  segments?: { id: string; text: string }[];
  /** Agent's structured todo list (VS Code Todos panel parity). */
  todos?: { id: string; title: string; status: string }[];
  agentState?: Record<string, unknown>;
  /** `segmentCutoff`: the count of text segments when the event arrived, so a
   *  card draws before the text that streamed after it (`genUiFlow` in
   *  `lib/chatPlacement.ts`). Absent on a run with no segment ids. */
  customEvents?: { name: string; value: unknown; segmentCutoff?: number }[];
  /** Who authored this turn: a person's email, or an agent's registered name. */
  authorEmail?: string;
  /** Whose FACE to render — deliberately separate from `role`.
   *  `role` is the model's vocabulary: which side of the conversation a turn
   *  sits on. In a room that no longer answers the UI's question, because
   *  `role: "user"` covers every human in the thread, not just the reader.
   *  Absent on pre-authorship rows, which render exactly as they always did. */
  authorKind?: "human" | "agent" | "system";
  /** The reader is not cleared to see this turn; `content` is a notice, not the
   *  original text. Render it as a boundary holding, never as an error. */
  redacted?: boolean;
  /** Capabilities the producing run held that the reader does not. */
  redactedCaps?: string[];
  /** A member's turn that has not reached a run yet: held while the app
   *  updates, or steered into a run that has not taken it. A repeat of the
   *  same words collapses into it (`lib/chatRecovery.ts`). Never saved. */
  pendingDelivery?: boolean;
  /** A new turn whose send the server has not answered yet, on the path where
   *  the gateway saves the turn itself at run start (WS-51 S4). The chat does
   *  not save it until the answer comes, so a send that the run cap refuses
   *  (WS-51 D-3) leaves no row. Cleared on every other answer. */
  awaitingServer?: boolean;
  /** A message sent while a run was live, which steers it (owner,
   *  2026-10-12, `lib/chatSteer.ts`). "sending" until the server answers,
   *  then "added" once the server folded it into the run. Never saved: the
   *  saved row carries the `steered` custom event instead. */
  steerAck?: "sending" | "added";
  /** The follow-up for this steer ran out of tries (`lib/chatSteer.ts`).
   *  The bubble says the words wait for the next reply. Never saved. */
  steerWaiting?: boolean;
}

// ── Session state ────────────────────────────────────────────────────────────

export interface SessionStreamState {
  messages: ChatMessage[];
  isLoading: boolean;
  error: string | null;
  /** Kept here so stopGeneration() can abort even when component is unmounted. */
  abortController: AbortController | null;
  /** True when the stream was interrupted (refresh/tab-close) and polling is
   *  actively recovering content from Postgres.  The UI shows a "Reconnecting…"
   *  indicator while this is true. */
  recovering: boolean;
  /** Last SSE event ID received from the server.  Used for stream reconnection:
   *  on reconnect the client sends this ID so the server can replay only events
   *  that arrived after the disconnect. */
  lastEventId: string | null;
  /** Tracks the agent's run state for the UI status indicator:
   *  - "idle": no agent running
   *  - "running": agent is actively executing (confirmed by server)
   *  - "recovering": reconnecting or polling for content after disconnect
   *  - "unknown": can't determine status (e.g. Redis unavailable) */
  runStatus: "idle" | "running" | "recovering" | "unknown";
  /** True while the app updates: the gateway did not take a send, and the
   *  chat shows ONE "Metorite is updating" notice (`lib/chatRecovery.ts`). */
  outage: boolean;
  /** The member's sends held during an update, each once, in order. They go
   *  out when `/api/health` says the gateway is back. */
  pendingSends: string[];
  /** The held sends that are a Continue: they go out with `resume`. */
  pendingResume: string[];
  /** This tab steered the live run. Once the run ends, the chat asks the
   *  server once for a follow-up turn, which answers any steer the run never
   *  read (`lib/chatSteer.ts`). A Stop clears it. */
  steerOwed?: boolean;
  /** Counts the Stops of this thread. A steer answer that comes back after a
   *  Stop owes no follow-up (round 2 of the review of #920). */
  stopEpoch?: number;
  /** The ONE run state of this chat (`lib/runStatus.ts`). Every indicator
   *  of an open chat reads it: the status row, the composer's Stop, the
   *  header dot and the placeholder. Written only through `dispatchRun`. */
  run: RunStatus;
}

function _defaultState(): SessionStreamState {
  return {
    messages: [], isLoading: false, error: null, abortController: null, recovering: false,
    lastEventId: null, runStatus: "idle", outage: false, pendingSends: [], pendingResume: [],
    run: IDLE_RUN,
  };
}

// ── Module-level store ───────────────────────────────────────────────────────

const _store = new Map<string, SessionStreamState>();
const _listeners = new Map<string, Set<() => void>>();
const _globalListeners = new Set<() => void>();

export function getSessionState(id: string): SessionStreamState {
  // Always return the stored entry — never create a transient object.
  // useSyncExternalStore requires getSnapshot to return the SAME reference
  // when the state hasn't changed; a fresh _defaultState() on every call
  // causes React to see a "new" state on every render → infinite loop.
  if (!_store.has(id)) {
    _store.set(id, _defaultState());
  }
  return _store.get(id)!;
}

export function setSessionState(
  id: string,
  updater: (prev: SessionStreamState) => SessionStreamState,
): void {
  const prev = getSessionState(id);
  const next = updater(prev);
  _store.set(id, next);
  // Notify per-session subscribers.
  _listeners.get(id)?.forEach((l) => l());
  // Notify global subscribers when isLoading toggles.
  if (prev.isLoading !== next.isLoading) {
    _invalidActiveIdsCache();
    _globalListeners.forEach((l) => l());
  }
}

/**
 * Feed one event to the thread's run state (`lib/runStatus.ts`). An event
 * that changes nothing notifies nobody. Returns the state after it.
 */
export function dispatchRun(id: string, event: RunEvent): RunStatus {
  const prev = getSessionState(id);
  const next = reduceRun(prev.run ?? IDLE_RUN, event);
  if (next !== prev.run) setSessionState(id, (p) => ({ ...p, run: next }));
  return next;
}

// ── The cards of the run in flight (CI regression of #1171) ─────────────────
//
// A card the run waits on arrives once, as a stream event, and lives in the
// component state of `AgentChat`. A chat that unmounts and mounts again (the
// Email pane closed and opened, a rail, the dock) lost it while the run
// still waited. The old reconnect replayed the whole stream to get it back,
// beside a live stream that still read the run. Now the stream loops keep
// the card events of the run in flight here, and a chat that mounts draws
// them again (`AgentChat`). Only the card events: no side effect replays.

/** The card events a chat draws again on mount. */
export const CARD_EVENTS: ReadonlySet<string> = new Set([
  "confirmation_requested",
  "confirmation_resolved",
  "elicitation_requested",
  "user_input_requested",
]);
const CARD_LOG_MAX = 50;
const _cardLog = new Map<string, CardLogEntry[]>();

/**
 * The stream events that prove a run moved past its question. THE RULE:
 * when in doubt, redraw as waiting. A stale waiting card costs one harmless
 * 4xx, because a refused answer of a redrawn card never steers. A hidden
 * waiting card blocks the run until its question times out.
 *
 * The tools of one model turn run CONCURRENTLY (MAF's
 * `allow_concurrent_invocation`, one `asyncio.gather`). While the asking
 * tool waits, its siblings go on. So none of these counts:
 *
 * - `sub_agent_*`: a parallel delegation streams them (review of #1171).
 * - `tool_start`, `tool_end`: the executor's tool shim emits a start and a
 *   result for each tool as it runs, inside the gather
 *   (`orchestrator/executor.py`, the Tier 2 `_shim`). A sibling's result can
 *   come while the question waits.
 * - `progress`, `tool_args`, `tool_partial`, a heartbeat, a card event.
 *
 * What counts is model output, which comes only after the gather: a text
 * delta, a text message start, reasoning, and the run's `done`.
 */
const MOVE_EVENTS: ReadonlySet<string> = new Set([
  "delta", "reasoning", "message_start", "done",
]);

/** Keep a card event of the run in flight. Other events are ignored. */
export function noteCardEvent(threadId: string, name: string, value: unknown): void {
  if (!CARD_EVENTS.has(name)) return;
  const log = _cardLog.get(threadId) ?? [];
  log.push({ name, value });
  if (log.length > CARD_LOG_MAX) log.splice(0, log.length - CARD_LOG_MAX);
  _cardLog.set(threadId, log);
}

/** The member answered a card here: the redraw shows the answer at once,
 *  and never draws the card as waiting (review of #1171, P1). */
export function noteCardAnswered(
  threadId: string, requestId: string, answer: string, declined = false,
): void {
  if (!requestId) return;
  const log = _cardLog.get(threadId) ?? [];
  log.push({ name: CARD_ANSWERED, value: { request_id: requestId, answer, ...(declined ? { declined } : {}) } });
  if (log.length > CARD_LOG_MAX) log.splice(0, log.length - CARD_LOG_MAX);
  _cardLog.set(threadId, log);
}

/** A stream event of type `type` arrived. If it proves the run moved on, a
 *  question asked before it is answered: it never redraws as waiting. */
export function noteRunMoved(threadId: string, type: string): void {
  if (!MOVE_EVENTS.has(type)) return;
  const log = _cardLog.get(threadId);
  if (!log) return;
  for (const e of log) {
    if (!e.moved && (e.name === "elicitation_requested" || e.name === "user_input_requested")) e.moved = true;
  }
}

/** A new run, or the end of one: no card of it waits any more. */
export function clearCardLog(threadId: string): void {
  _cardLog.delete(threadId);
}

/** The card events of the run in flight, oldest first. */
export function getCardLog(threadId: string): readonly CardLogEntry[] {
  return _cardLog.get(threadId) ?? [];
}

export function subscribeSession(id: string, listener: () => void): () => void {
  if (!_listeners.has(id)) _listeners.set(id, new Set());
  _listeners.get(id)!.add(listener);
  return () => {
    _listeners.get(id)?.delete(listener);
  };
}

// ── Global subscribers (for active-sessions tracking) ────────────────────

/** Subscribe to ANY session state change (for active-sessions dashboard). */
export function subscribeAllSessions(listener: () => void): () => void {
  _globalListeners.add(listener);
  return () => { _globalListeners.delete(listener); };
}

/** Get the set of session IDs that currently have isLoading === true. */
export function getActiveSessionIds(): Set<string> {
  const ids = new Set<string>();
  for (const [id, state] of _store) {
    if (state.isLoading) ids.add(id);
  }
  return ids;
}

// Cached active-IDs snapshot — must return the SAME reference
// when the set hasn't changed, otherwise useSyncExternalStore re-renders infinitely.
let _cachedActiveIds: Set<string> | null = null;
let _cachedActiveIdsStr: string | null = null;

function _invalidActiveIdsCache() {
  _cachedActiveIds = null;
  _cachedActiveIdsStr = null;
}

export function getActiveSessionIdsStable(): Set<string> {
  const fresh = getActiveSessionIds();
  const str = JSON.stringify([...fresh].sort());
  if (_cachedActiveIds && _cachedActiveIdsStr === str) return _cachedActiveIds;
  _cachedActiveIds = fresh;
  _cachedActiveIdsStr = str;
  return fresh;
}

// ── Which agent each session runs (WS-51 S1) ─────────────────────────────────
//
// The nav's run badge counts a live run on its app (`lib/runActivity.ts`).
// The server names the agent of a run with a session row. A run this tab just
// started may have no row yet, and the server then says "unknown". The hook
// that streams the session knows its agent, and records it here.

const _sessionAgents = new Map<string, string>();

export function setSessionAgent(id: string, agentName: string): void {
  if (agentName) _sessionAgents.set(id, agentName);
}

export function getSessionAgent(id: string): string | undefined {
  return _sessionAgents.get(id);
}

/**
 * Clear `isLoading` for a loop that gives up its stream, but only when that
 * loop still owns the session's loading state (`controller` is the current
 * one). A newer send or reattach keeps its own state.
 *
 * Why: the reattach loop in `useAgentChat` is aborted on unmount. Its catch
 * and finally skip every write once cancelled, so `isLoading` stayed true with
 * a dead controller. The store outlives the component, so the run badge then
 * counted a run that this tab no longer watched, until a reload. A run that
 * still runs stays counted: the server's list reports it.
 *
 * Returns true when it cleared the state. Fence: `chatStore.test.ts`.
 */
export function releaseLoading(id: string, controller: AbortController): boolean {
  const cur = _store.get(id);
  if (!cur || !cur.isLoading || cur.abortController !== controller) return false;
  setSessionState(id, (prev) => ({ ...prev, isLoading: false, abortController: null }));
  return true;
}

// ── Stream ownership (single-writer-per-message) ─────────────────────────────
//
// One assistant message can be targeted by more than one SSE loop:
//   • the LIVE send loop, and
//   • the RECONNECT/replay loop, which resets the message and replays the Redis
//     stream from the beginning.
// React StrictMode also double-invokes the reconnect effect in dev.  When two
// loops append to the same message concurrently, every chunk lands twice — the
// "duplicated tokens" bug ("email email-specific-specific card card …"): the
// first token survives single (cleared by the reset) and everything after
// doubles.
//
// Ownership makes the LAST loop to start the only writer: each loop claims a
// unique token for (threadId, messageId) before streaming; every mutation
// checks it still owns the message and silently no-ops if a newer loop took
// over.  The superseded loop stops contributing and the new owner rebuilds the
// message from its reset baseline, so the text is never doubled.

const _streamOwners = new Map<string, string>();
const _streamOwnerKey = (threadId: string, messageId: string): string =>
  `${threadId}::${messageId}`;

/** Claim exclusive write ownership of (threadId, messageId) for `token`.  Any
 *  loop that previously claimed the same message immediately loses ownership. */
export function claimStreamOwnership(
  threadId: string,
  messageId: string,
  token: string,
): void {
  _streamOwners.set(_streamOwnerKey(threadId, messageId), token);
}

/** True while `token` is still the current owner of (threadId, messageId). */
export function ownsStream(
  threadId: string,
  messageId: string,
  token: string,
): boolean {
  return _streamOwners.get(_streamOwnerKey(threadId, messageId)) === token;
}

/** Release ownership iff `token` still holds it — never clobber a newer owner's
 *  claim during this (superseded) loop's cleanup. */
export function releaseStreamOwnership(
  threadId: string,
  messageId: string,
  token: string,
): void {
  const key = _streamOwnerKey(threadId, messageId);
  if (_streamOwners.get(key) === token) _streamOwners.delete(key);
}
