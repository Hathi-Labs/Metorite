/**
 * A chat recovers from an app update. The browser's half.
 *
 * Incident 2026-10-09, 16:18 UTC. The owner sent a message while a deploy
 * ran. The chat went silent, so he typed "Continue. I think you stopped
 * midway." four times. Four bubbles stood in the thread, and nothing
 * answered. The owner asked for three things: say WHY the chat is silent,
 * show the error or ask to try again, and pick up where it stopped once the
 * app is back.
 *
 * The rules this file holds, each one pure so a test can pin it:
 *
 * 1. **The app is updating.** A send that the gateway did not answer (502,
 *    503, 504, or no response at all) is held ONCE, and sent when
 *    `/api/health` says the gateway is back. `isUpdateOutage` decides,
 *    `queueOnce` holds, and `waitUntilBack` polls with backoff.
 * 2. **No message twice.** A send of the words that already wait is
 *    collapsed (`isRepeatWhilePending`).
 * 3. **A run a restart ended.** Its saved reply carries the
 *    `run_interrupted` marker (the gateway's sweep, or the chat route when
 *    the stream was cut). `interruptedTurn` finds it, and the chat offers
 *    Continue.
 * 4. **The composer is never dead.** A recovery that has heard nothing for
 *    `STALE_RUN_MS` falls back to idle, with the interrupted notice
 *    (`isStaleRecovery`, `markInterrupted`).
 *
 * Fence: `chatRecovery.test.ts`. The server's half is
 * `apps/services/orchestrator/orchestrator/run_liveness.py`, fenced by
 * `tests/unit/test_chat_deploy_recovery.py`.
 */
import { getSessionState, setSessionState } from "@/lib/chatStore";
import type { ChatMessage, SessionStreamState } from "@/lib/chatStore";
import type { EditOutcome } from "@/lib/chatEdit";

/** The marker a run a restart ended carries, on the stream and on the row. */
export const RUN_INTERRUPTED_EVENT = "run_interrupted";

/** What Continue shows in the thread. The gateway writes what the model reads. */
export const CONTINUE_TEXT = "Continue";

/** The statuses a restart produces. A 500 is an answer, so it is not here. */
const OUTAGE_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

/** How long a recovery may hear nothing before the chat falls back to idle. */
export const STALE_RUN_MS = 45_000;

/** The waits between two health probes. The last one repeats. */
export const HEALTH_BACKOFF_MS: readonly number[] = [1_000, 2_000, 4_000, 8_000, 10_000];

/**
 * Did the APP fail to take this send, rather than the run fail?
 *
 * `status` is the HTTP status of the chat request, or null when no response
 * arrived. With no response, only a network failure counts. The member
 * pressing Stop or leaving the page is an abort, and is not an outage.
 */
export function isUpdateOutage(f: { status: number | null; gotResponse: boolean; err?: unknown }): boolean {
  if (f.status !== null && OUTAGE_STATUSES.has(f.status)) return true;
  if (f.gotResponse) return false;
  const err = f.err;
  if (err instanceof DOMException && err.name === "AbortError") return false;
  return err instanceof TypeError;
}

/** The held sends, with `text` added once. Identical words are one send. */
export function queueOnce(pending: readonly string[], text: string): string[] {
  const t = text.trim();
  if (!t || pending.includes(t)) return [...pending];
  return [...pending, t];
}

/**
 * True when `text` repeats the member's last turn and that turn has not
 * reached a run yet. The send is then collapsed: no second bubble, and no
 * second copy on the server.
 */
export function isRepeatWhilePending(
  messages: ReadonlyArray<Pick<ChatMessage, "role" | "content" | "pendingDelivery">>,
  text: string,
): boolean {
  const t = text.trim();
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "system") continue;
    return m.role === "user" && m.pendingDelivery === true && m.content.trim() === t;
  }
  return false;
}

/** The wait before probe `attempt` (0-based). */
export function backoffMs(attempt: number): number {
  return HEALTH_BACKOFF_MS[Math.min(Math.max(attempt, 0), HEALTH_BACKOFF_MS.length - 1)];
}

/**
 * Poll until the gateway answers. Resolves true when it is back, false when
 * `signal` aborts. A probe that throws counts as "not yet".
 */
export async function waitUntilBack(opts: {
  probe: () => Promise<boolean>;
  sleep: (ms: number) => Promise<void>;
  signal?: AbortSignal;
}): Promise<boolean> {
  for (let attempt = 0; ; attempt++) {
    if (opts.signal?.aborted) return false;
    await opts.sleep(backoffMs(attempt));
    if (opts.signal?.aborted) return false;
    let up = false;
    try {
      up = await opts.probe();
    } catch {
      up = false;
    }
    if (up) return true;
  }
}

/** Is the gateway up? Reads the shell's own probe (`/api/health`). */
export async function probeGatewayUp(fetchFn: typeof fetch = fetch): Promise<boolean> {
  const res = await fetchFn("/api/health", { cache: "no-store" });
  if (!res.ok) return false;
  const body = (await res.json().catch(() => ({}))) as { gateway?: string };
  // "busy" is a database that refused a connection a moment ago. A send then
  // fails again, so only "up" ends the wait.
  return body.gateway === "up";
}

function hasMarker(m: Pick<ChatMessage, "customEvents">): boolean {
  return (m.customEvents ?? []).some((e) => e.name === RUN_INTERRUPTED_EVENT);
}

/**
 * The id of the turn to offer Continue on, or null.
 *
 * Only the LAST turn of the thread, only when it is a settled answer, and
 * only when it carries the marker. A later turn means the member has moved
 * on, so the notice goes.
 */
export function interruptedTurn(
  messages: ReadonlyArray<Pick<ChatMessage, "id" | "role" | "streaming" | "customEvents">>,
): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "system") continue;
    if (m.role !== "assistant" || m.streaming) return null;
    return hasMarker(m) ? m.id : null;
  }
  return null;
}

/** The answer with the marker added once. Used when the run state is lost. */
export function markInterrupted(m: ChatMessage, reason = "unknown"): ChatMessage {
  if (hasMarker(m)) return { ...m, streaming: false, isThinkingActive: false };
  return {
    ...m,
    streaming: false,
    isThinkingActive: false,
    customEvents: [
      ...(m.customEvents ?? []),
      { name: RUN_INTERRUPTED_EVENT, value: { reason }, segmentCutoff: m.segments?.length ?? 0 },
    ],
  };
}

/**
 * Has a recovery heard nothing for too long? Then the chat falls back to idle.
 *
 * `since` is when the recovery began. A live stream (`hasLiveStream`) is
 * never stale here: it ends on its own, or the member stops it.
 */
export function isStaleRecovery(s: {
  recovering: boolean;
  runStatus: string;
  hasLiveStream: boolean;
  since: number;
  now: number;
}): boolean {
  if (s.hasLiveStream) return false;
  if (!s.recovering && (s.runStatus === "idle" || s.runStatus === "running")) return false;
  return s.since > 0 && s.now - s.since > STALE_RUN_MS;
}

/**
 * A card answer that reached a dead run comes back from
 * `POST /api/agent/respond-input` as 409 `run_restarted`, with the question
 * and the answer as one message. Returns that message, or null.
 */
export function restartedCardMessage(body: string): string | null {
  try {
    const parsed = JSON.parse(body) as { detail?: { error?: unknown; resumeMessage?: unknown } };
    const d = parsed?.detail;
    if (d && d.error === "run_restarted" && typeof d.resumeMessage === "string" && d.resumeMessage.trim()) {
      return d.resumeMessage;
    }
  } catch {
    /* not the gateway's JSON */
  }
  return null;
}

// ── The held sends: one per text, sent when the app is back ─────────────────
//
// The store is module-level (`chatStore`), so a hold outlives a remount and a
// session switch. `useAgentChat` registers its sender per thread, and the
// loop below calls the latest one.

/** Options of one chat send. */
export interface SendOptions {
  /** The Continue button: the gateway writes the note the model reads. */
  resume?: boolean;
  /** Send the held bubble with this id, in place, rather than a new one. */
  heldId?: string;
  /** An EDIT of the last user message, with this id (`lib/chatEdit.ts`). It
   *  replaces that turn only once the server accepts it. */
  supersedes?: string;
  /** Hears the server's answer to an edit, once, also after a hold. */
  onEditOutcome?: (o: EditOutcome) => void;
  /** Set by `flushHeld`: a held send that meets a running turn waits again. */
  fromHold?: boolean;
}

/** What a held edit keeps: its target and its answer. It keeps no bubble,
 *  because the old turn stays visible until the server accepts the edit. */
export interface HeldEdit {
  supersedes: string;
  onEditOutcome?: (o: EditOutcome) => void;
}

/** The held edits, by thread and by text. Module-level, like the senders,
 *  because a callback cannot live in the session state. */
const heldEdits = new Map<string, Map<string, HeldEdit>>();

/** Tests only: the held edit for this text, if any. */
export function heldEditFor(threadId: string, text: string): HeldEdit | undefined {
  return heldEdits.get(threadId)?.get(text.trim());
}

export type Sender = (text: string, opts?: SendOptions) => Promise<void>;

const senders = new Map<string, Sender>();
const loops = new Map<string, AbortController>();

const defaultDeps = {
  probe: () => probeGatewayUp(),
  sleep: (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
};
let deps = defaultDeps;

/** Tests only: a fake probe and a fake clock. `null` restores the real ones. */
export function setRecoveryDeps(d: typeof defaultDeps | null): void {
  deps = d ?? defaultDeps;
}

/** The chat's sender for `threadId`. The latest registration wins. */
export function registerSender(threadId: string, send: Sender): void {
  senders.set(threadId, send);
}

/**
 * Hold `text` until the gateway is back, and start the wait.
 *
 * `userMsgId` marks the member's bubble as held. `front` puts a send that
 * failed AGAIN back at the head, so held sends keep their order.
 */
export function holdForUpdate(
  threadId: string,
  text: string,
  opts: { userMsgId?: string | null; front?: boolean; resume?: boolean; edit?: HeldEdit } = {},
): void {
  const t = text.trim();
  if (opts.edit) {
    const byText = heldEdits.get(threadId) ?? new Map<string, HeldEdit>();
    byText.set(t, opts.edit);
    heldEdits.set(threadId, byText);
  }
  setSessionState(threadId, (prev) => {
    const pending = prev.pendingSends.includes(t)
      ? prev.pendingSends
      : opts.front ? [t, ...prev.pendingSends] : queueOnce(prev.pendingSends, t);
    return {
      ...prev,
      outage: true,
      error: null,
      pendingSends: pending,
      // A held Continue stays a Continue: it goes out with `resume`.
      pendingResume: opts.resume ? queueOnce(prev.pendingResume, t) : prev.pendingResume,
      messages: opts.userMsgId
        ? prev.messages.map((m) => (m.id === opts.userMsgId ? { ...m, pendingDelivery: true } : m))
        : prev.messages,
    };
  });
  startRecoveryLoop(threadId);
}

/** The held bubble for `text`: the last held turn with those words. */
function heldBubbleId(messages: readonly ChatMessage[], text: string): string | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user" && m.pendingDelivery && m.content.trim() === text) return m.id;
  }
  return undefined;
}

/**
 * Send every held text, in order, each in its own bubble. Stops at the first
 * send that the app does not take: that send holds itself again, and a new
 * wait starts.
 */
export async function flushHeld(threadId: string): Promise<void> {
  const send = senders.get(threadId);
  for (;;) {
    const st = getSessionState(threadId);
    const [next, ...rest] = st.pendingSends;
    if (next === undefined || !send) {
      setSessionState(threadId, (prev) => ({ ...prev, outage: false }));
      return;
    }
    const heldId = heldBubbleId(st.messages, next);
    const resume = st.pendingResume.includes(next);
    // A held edit goes out as an edit: same target, same answer.
    const edit = heldEdits.get(threadId)?.get(next);
    heldEdits.get(threadId)?.delete(next);
    setSessionState(threadId, (prev) => ({
      ...prev, outage: false, pendingSends: rest,
      pendingResume: prev.pendingResume.filter((x) => x !== next),
    }));
    const opts: SendOptions = {
      ...(heldId ? { heldId } : {}),
      ...(resume ? { resume: true } : {}),
      ...(edit ? { supersedes: edit.supersedes, onEditOutcome: edit.onEditOutcome, fromHold: true } : {}),
    };
    await send(next, Object.keys(opts).length ? opts : undefined);
    if (getSessionState(threadId).outage) return;
  }
}

/** Wait for the gateway, then flush. One wait per thread. */
export function startRecoveryLoop(threadId: string): void {
  if (loops.has(threadId)) return;
  const ctrl = new AbortController();
  loops.set(threadId, ctrl);
  void (async () => {
    let back = false;
    try {
      back = await waitUntilBack({ probe: deps.probe, sleep: deps.sleep, signal: ctrl.signal });
    } finally {
      if (loops.get(threadId) === ctrl) loops.delete(threadId);
    }
    if (back) await flushHeld(threadId);
  })();
}

/** The notice's Retry: stop waiting and try the held sends now. */
export function retryHeldNow(threadId: string): Promise<void> {
  loops.get(threadId)?.abort();
  loops.delete(threadId);
  return flushHeld(threadId);
}

/** Tests only: is a wait running for this thread? */
export function isWaiting(threadId: string): boolean {
  return loops.has(threadId);
}

// ── The two decisions `useAgentChat` makes, as pure functions ───────────────

/**
 * What a send does, before any request.
 *
 * - `collapse`: the same words already wait. Nothing new is sent or drawn.
 * - `hold`: the app is updating. The send is held, with its bubble.
 * - `requeue`: a held send met a running turn. It waits again, at the head.
 * - `busy`: a turn is running. The composer's own queue takes the send.
 * - `send`: send now.
 */
export type SendPlan = "collapse" | "hold" | "requeue" | "busy" | "send";

export function planSend(
  st: Pick<SessionStreamState, "messages" | "isLoading" | "outage" | "pendingSends">,
  text: string,
  heldId?: string,
): SendPlan {
  const t = text.trim();
  const held = heldId ? st.messages.some((m) => m.id === heldId && m.role === "user") : false;
  if (!held && (st.pendingSends.includes(t) || isRepeatWhilePending(st.messages, t))) return "collapse";
  if (!held && st.outage) return "hold";
  if (st.isLoading) return held ? "requeue" : "busy";
  return "send";
}

/**
 * The composer falls back to idle: no spinner, no Stop, and the last answer
 * gets the interrupted notice so Continue is one press away.
 */
export function idleAfterStaleRecovery(st: SessionStreamState): SessionStreamState {
  const i = st.messages.length - 1;
  const last = st.messages[i];
  const messages = last?.role === "assistant"
    ? st.messages.map((m, j) => (j === i ? markInterrupted(m) : m))
    : st.messages;
  return { ...st, messages, recovering: false, runStatus: "idle", isLoading: false };
}
