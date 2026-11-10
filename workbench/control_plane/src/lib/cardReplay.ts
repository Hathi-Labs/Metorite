/**
 * cardReplay — how a chat that mounts again draws the cards of the run in
 * flight (`chat_run_continuity.md` §4.10, review of #1171).
 *
 * The stream loops keep the card events of the run in flight in a log
 * (`noteCardEvent` in `lib/chatStore.ts`). The log also holds what the
 * member answered (`noteCardAnswered`), and marks a question as moved when
 * the run sent any event after it (`noteRunMoved`). A run waits on its
 * question and sends nothing until it has an answer, so an event after the
 * question proves that the question is answered, in this tab or in another.
 *
 * The rule: a card redraws as WAITING only when nothing says it is settled.
 * A confirmation settles on its `confirmation_resolved` or on an answer here.
 * A question settles on an answer here, or when the run moved on. The
 * server's pending asks (`lib/pendingAsks.ts`) are the last word where they
 * are on: `AgentChat` settles a waiting card that they do not list.
 *
 * Pure. Fence (R7): `cardReplay.test.ts`.
 */

/** One entry of the log. `moved`: the run sent an event after it. */
export interface CardLogEntry {
  name: string;
  value: unknown;
  moved?: boolean;
}

/** The log entry of an answer the member sent. */
export const CARD_ANSWERED = "card_answered";

export type CardKind = "confirmation" | "elicitation" | "user_input";

/** One card to draw again. */
export interface CardRedraw {
  kind: CardKind;
  requestId: string;
  /** The card's own event value, as the stream sent it. */
  value: unknown;
  /** WAITING: the card waits on the member. Else it draws settled. */
  waiting: boolean;
  /** The member's answer, when this tab sent one: the card shows it. */
  answer?: string;
  /** The member declined the card. */
  declined?: boolean;
}

const KIND_OF: Record<string, CardKind> = {
  confirmation_requested: "confirmation",
  elicitation_requested: "elicitation",
  user_input_requested: "user_input",
};

function rid(value: unknown): string {
  if (!value || typeof value !== "object") return "";
  const v = (value as Record<string, unknown>).request_id;
  return typeof v === "string" ? v : v == null ? "" : String(v);
}

/** The cards of the log, in the order they were asked, each once. */
export function planCardRedraw(log: readonly CardLogEntry[]): CardRedraw[] {
  const out = new Map<string, CardRedraw>();
  for (const e of log) {
    const id = rid(e.value);
    if (!id) continue;
    const kind = KIND_OF[e.name];
    if (kind) {
      out.set(id, {
        kind, requestId: id, value: e.value,
        // A question the run moved past is answered, here or elsewhere.
        waiting: !(kind !== "confirmation" && e.moved),
      });
      continue;
    }
    const card = out.get(id);
    if (!card) continue;
    if (e.name === "confirmation_resolved") card.waiting = false;
    if (e.name === CARD_ANSWERED) {
      const v = e.value as { answer?: unknown; declined?: unknown };
      card.waiting = false;
      if (typeof v.answer === "string") card.answer = v.answer;
      if (v.declined === true) card.declined = true;
    }
  }
  return [...out.values()];
}
