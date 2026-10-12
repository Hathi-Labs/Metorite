/**
 * A message sent while the agent works ALWAYS steers the live run.
 *
 * Owner, 2026-10-12: "By default, we should always steer the messages, so
 * don't even give the option for stopping and sending. Whenever an agent is
 * still thinking and we send a reply, it should steer the output necessarily
 * so that the agent keeps continuing with the response and uses the new input
 * as additional context to get the output."
 *
 * So the composer has ONE send. While a run is live it POSTs the words the
 * normal way, and the gateway folds them into that run (§QM-1, the STEER
 * route of `orchestrator/steer.py::route_turn`). The live stream goes on: no
 * stop, no dropped partial reply, no second stream. The send-mode menu and
 * the local send-queue are gone.
 *
 * The server answers one of four ways, and `sendSteer` turns each into one
 * outcome:
 *
 * - `202 {steered: true}`: the words went into the run → `folded`. The
 *   bubble draws "Added to the reply", and the session owes a follow-up.
 * - `202 {aborted}` or `{dropped}`: a bare "stop" ended the run → `stopped`.
 *   The bubble goes, as the server saved no row.
 * - a stream: the run had ended, so the words started a new run → `started`.
 *   The hook follows that stream as a normal turn (`SendOptions.adopt`).
 * - anything else: a refusal → `refused`. The bubble goes, the words go back
 *   to the box, and a plain notice says why. Nothing is lost silently.
 *
 * **The follow-up.** A steer rides the next tool result. A steer that comes
 * while the model writes its final answer meets no tool result. The run gives
 * it back to the durable store at its end, and once the stream ends this tab
 * asks once for a follow-up turn (`steerFollowUp`). The gateway's ENGAGE seam
 * makes the stored steer that turn's message, so the member's words always get
 * an answer. A follow-up with nothing owed costs one 202 and starts nothing.
 *
 * Fences: `chatSteer.test.ts` (this module and the composer's wiring),
 * `tests/unit/test_steer_routing.py` and `tests/unit/test_steer_follow_up.py`
 * (the server), `e2e/chat-steer.spec.ts` (the browser).
 */
import { getSessionState, setSessionState, type ChatMessage } from "@/lib/chatStore";
import { nanoid } from "@/lib/chatStream";
import { getQueue, saveQueue } from "@/lib/sessions";

/**
 * The words an older build queued for this session, taken out of storage
 * ONCE. The send queue is gone: the composer puts these words in the box,
 * and nothing sends by itself after the upgrade. Empty string when none.
 */
export function takeLeftoverQueue(sessionId: string): string {
  const left = getQueue(sessionId);
  if (left.length === 0) return "";
  saveQueue(sessionId, []);
  return left.join("\n\n");
}

/** The custom event that marks a steered turn, on the bubble and on its row. */
export const STEERED = "steered";

/** True for a member's turn that steered a live run. */
export function isSteered(m: Pick<ChatMessage, "role" | "customEvents"> | undefined): boolean {
  return !!m && m.role === "user" && !!m.customEvents?.some((e) => e.name === STEERED);
}

/**
 * The newest message that is not a steer. A steer sits at the end of the
 * thread while the reply above it goes on, so a reader of "the current
 * turn" (the live step, the todos, the follow-up pills) looks past it.
 */
export function lastNonSteer<T extends Pick<ChatMessage, "role" | "customEvents">>(messages: T[]): T | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (!isSteered(messages[i])) return messages[i];
  }
  return undefined;
}

/** The composer's one primary control. */
export type PrimaryAction = "send" | "stop";

/**
 * Stop while a run is live and the box is empty. Send as soon as the box has
 * text, and Send steers. There is no third control.
 */
export function primaryAction(a: { runActive: boolean; hasText: boolean }): PrimaryAction {
  return a.runActive && !a.hasText ? "stop" : "send";
}

/** What one press of Send does. */
export type SendRoute = "send" | "steer" | "unsupported";

/**
 * `send` when nothing runs. `steer` when a run is live on the gateway path.
 * `unsupported` on the direct model path (`litellm`), which has no gateway
 * run to steer: the words stay in the box with a notice.
 */
export function sendRoute(a: { runActive: boolean; mode: "copilot" | "litellm" }): SendRoute {
  if (!a.runActive) return "send";
  return a.mode === "copilot" ? "steer" : "unsupported";
}

/** The notice of the direct model path, which cannot take a steer. */
export const STEER_UNSUPPORTED =
  "This model cannot take a message while it replies. Your message is still in the box.";

/** The caption under the box while a run is live and the box has text. */
export const STEER_CAPTION = "Adds to the current reply";

export type SteerOutcome =
  | { kind: "empty" }
  | { kind: "collapsed" }
  | { kind: "folded"; pendingReplay: boolean }
  | { kind: "stopped" }
  | { kind: "started"; response: Response; controller: AbortController; bubbleId: string }
  | { kind: "refused"; notice: string };

/** The words of a refusal, for the member. Never the raw body. */
export function refusalNotice(status: number, body: string): string {
  let detail: unknown = null;
  // The chat proxy wraps a gateway refusal as one SSE frame whose `content`
  // is the gateway's own body.
  const raw = body.trim().startsWith("data:") ? body.trim().slice(5).trim() : body;
  try {
    const frame = JSON.parse(raw) as { content?: unknown };
    const inner = typeof frame.content === "string" ? frame.content : raw;
    try { detail = JSON.parse(inner); } catch { detail = frame; }
  } catch { /* not JSON */ }
  const d = (detail as { detail?: unknown })?.detail ?? detail;
  const error = typeof (d as { error?: unknown })?.error === "string" ? (d as { error: string }).error : "";
  const message = typeof (d as { message?: unknown })?.message === "string" ? (d as { message: string }).message : "";
  if (status === 429 || error === "too_many_runs") {
    return "Too many of your replies are running. Wait for one to end, then send this again.";
  }
  if (message && message.length <= 400) return message;
  return "The agent could not take your message now. It is back in the box.";
}

/** Remove one bubble. */
function dropBubble(threadId: string, id: string): void {
  setSessionState(threadId, (prev) => ({ ...prev, messages: prev.messages.filter((m) => m.id !== id) }));
}

/** Change one bubble. */
function patchBubble(threadId: string, id: string, fn: (m: ChatMessage) => ChatMessage): void {
  setSessionState(threadId, (prev) => ({
    ...prev,
    messages: prev.messages.map((m) => (m.id === id ? fn(m) : m)),
  }));
}

/**
 * Send words into the live run of `threadId`.
 *
 * It draws the bubble at once, in the "added to the reply" style, and POSTs
 * to `/api/agent/chat` with the body from `buildBody`. It never stops the
 * run, never touches the streaming reply, and never sets the loading state:
 * the live stream keeps all three.
 */
export async function sendSteer(args: {
  threadId: string;
  text: string;
  /** The request body. `history` is the thread without this bubble. */
  buildBody: (turn: {
    message: string;
    userMessageId: string;
    userMessageTs: number;
    history: ChatMessage[];
  }) => Record<string, unknown>;
  fetchImpl?: typeof fetch;
}): Promise<SteerOutcome> {
  const { threadId } = args;
  const text = args.text.trim();
  if (!text) return { kind: "empty" };
  const before = getSessionState(threadId);
  // A double press of the same words while the first is on its way is one
  // steer, not two.
  if (before.messages.some((m) => m.steerAck === "sending" && m.content === text)) {
    return { kind: "collapsed" };
  }
  const bubble: ChatMessage = {
    id: nanoid(),
    role: "user",
    content: text,
    timestamp: Date.now(),
    customEvents: [{ name: STEERED, value: {} }],
    steerAck: "sending",
    // Not saved until the server answers: a refused steer leaves no row.
    awaitingServer: true,
  };
  setSessionState(threadId, (prev) => ({ ...prev, messages: [...prev.messages, bubble] }));
  const history = getSessionState(threadId).messages.filter(
    (m) => m.id !== bubble.id && !(m.streaming && !m.content.trim()),
  );
  const controller = new AbortController();
  let res: Response;
  try {
    res = await (args.fetchImpl ?? fetch)("/api/agent/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify(args.buildBody({
        message: text, userMessageId: bubble.id, userMessageTs: bubble.timestamp, history,
      })),
    });
  } catch {
    dropBubble(threadId, bubble.id);
    return { kind: "refused", notice: "The message did not reach Metorite. It is back in the box." };
  }

  if (res.status === 202) {
    const out = (await res.json().catch(() => ({}))) as {
      steered?: boolean; pendingReplay?: boolean; aborted?: boolean; dropped?: boolean;
    };
    if (out.steered) {
      patchBubble(threadId, bubble.id, (m) => {
        const next: ChatMessage = { ...m, steerAck: "added" };
        delete next.awaitingServer;
        return next;
      });
      setSessionState(threadId, (prev) => ({ ...prev, steerOwed: true }));
      return { kind: "folded", pendingReplay: !!out.pendingReplay };
    }
    if (out.aborted || out.dropped) {
      dropBubble(threadId, bubble.id);
      return { kind: "stopped" };
    }
    dropBubble(threadId, bubble.id);
    return { kind: "refused", notice: refusalNotice(202, "") };
  }

  if (res.ok && res.body) {
    // The run had ended, so these words started a new one. They are a
    // normal turn now: no steer mark, and the hook follows the stream.
    patchBubble(threadId, bubble.id, (m) => {
      const next: ChatMessage = { ...m, customEvents: (m.customEvents ?? []).filter((e) => e.name !== STEERED) };
      delete next.steerAck;
      delete next.awaitingServer;
      return next;
    });
    return { kind: "started", response: res, controller, bubbleId: bubble.id };
  }

  const body = await res.text().catch(() => "");
  dropBubble(threadId, bubble.id);
  return { kind: "refused", notice: refusalNotice(res.status, body) };
}

/**
 * Should the hook ask for the follow-up now? Once, after the run it steered
 * has ended: no stream, no recovery, no update outage, and the gateway path.
 */
export function followUpDue(st: {
  steerOwed?: boolean;
  isLoading: boolean;
  recovering: boolean;
  outage: boolean;
}, mode: "copilot" | "litellm"): boolean {
  return !!st.steerOwed && !st.isLoading && !st.recovering && !st.outage && mode === "copilot";
}
