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
}

function _defaultState(): SessionStreamState {
  return {
    messages: [], isLoading: false, error: null, abortController: null, recovering: false,
    lastEventId: null, runStatus: "idle", outage: false, pendingSends: [], pendingResume: [],
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
