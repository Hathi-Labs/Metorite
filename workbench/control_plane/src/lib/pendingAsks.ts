/**
 * The questions a chat must draw again, from the server (WS-51 S2,
 * `project-docs/specs/chat_run_continuity.md` §4 S2).
 *
 * A card lives in its run's stream. When the run parks (it waited too long,
 * saved its reply and ended), or its process dies, the open chat loses the
 * card: the run's end clears every blocking card. The question itself is a
 * row on the server (`chat_pending_ask`), so the chat asks
 * `GET /api/chat/pending-asks` and draws each card again, after a reload or a
 * restart too.
 *
 * The answer goes through the one POST (`lib/respondInput.ts`). For a card
 * whose run ended, the gateway answers 409 `run_restarted` with the question
 * and the answer as one message, and the chat sends that message: a new run.
 *
 * Only the three cards the chat itself draws come back here. A blocking
 * generative UI is drawn from the saved reply, which keeps its event.
 *
 * Fence (R7): `pendingAsks.test.ts`.
 */

export interface PendingAsk {
  requestId: string;
  kind: string;
  /** The card's event name, e.g. `confirmation_requested`. */
  event: string;
  /** The card's own event value, as the run emitted it. Data, never code. */
  payload: Record<string, unknown>;
  askedAt: string | null;
  /** `run`: the run that asked still waits on it, live. `new_run`: it ended. */
  answerBy: "run" | "new_run";
}

/** The card events the chat draws from its own state (`AgentChat`). */
export const RESTORED_EVENTS: ReadonlySet<string> = new Set([
  "user_input_requested",
  "elicitation_requested",
  "confirmation_requested",
]);

function asAsk(raw: unknown): PendingAsk | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.requestId !== "string" || !r.requestId) return null;
  if (typeof r.event !== "string" || !r.payload || typeof r.payload !== "object") return null;
  return {
    requestId: r.requestId,
    kind: typeof r.kind === "string" ? r.kind : "",
    event: r.event,
    payload: r.payload as Record<string, unknown>,
    askedAt: typeof r.askedAt === "string" ? r.askedAt : null,
    answerBy: r.answerBy === "run" ? "run" : "new_run",
  };
}

/**
 * The thread's questions that still wait, or null when the server did not
 * answer. `[]` means "no card waits" and null means "unknown": a 4xx, a 5xx,
 * a timeout, a thrown fetch and a body that is no list are all null. Only a
 * real list settles a card (RESUME-0a). There is no read that folds a
 * failure into `[]`, so no caller can make that error.
 */
export async function fetchPendingAsksOrNull(
  threadId: string,
  fetchFn: typeof fetch = fetch,
): Promise<PendingAsk[] | null> {
  try {
    const res = await fetchFn(`/api/chat/pending-asks?threadId=${encodeURIComponent(threadId)}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = (await res.json()) as unknown;
    if (!Array.isArray(data)) return null;
    return data.map(asAsk).filter((a): a is PendingAsk => a !== null);
  } catch {
    return null;
  }
}

/**
 * The redrawn cards to settle: the ones that still draw as waiting and that
 * the server's real list does not hold as pending. With no list (null),
 * nothing settles.
 */
export function cardsToSettle(
  asks: readonly PendingAsk[] | null,
  waitingRedrawn: ReadonlySet<string>,
): string[] {
  if (!asks) return [];
  const listed = new Set(asks.map((a) => a.requestId));
  return [...waitingRedrawn].filter((id) => !listed.has(id));
}

/**
 * The card events to replay into the chat, oldest first.
 *
 * Only a card whose run ended (`new_run`): a live run's own stream still
 * carries its card. Only a card the chat draws itself. Only a payload whose
 * `request_id` is the row's own, so a row can never draw another card. A card
 * in `shown` (on screen now) is left as it is.
 */
export function cardsToRestore(
  asks: readonly PendingAsk[],
  shown: ReadonlySet<string> = new Set(),
): { name: string; value: Record<string, unknown> }[] {
  const out: { name: string; value: Record<string, unknown> }[] = [];
  for (const ask of asks) {
    if (ask.answerBy !== "new_run") continue;
    if (!RESTORED_EVENTS.has(ask.event)) continue;
    if (String(ask.payload.request_id ?? "") !== ask.requestId) continue;
    if (shown.has(ask.requestId)) continue;
    out.push({ name: ask.event, value: ask.payload });
  }
  return out;
}
