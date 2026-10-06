/**
 * The queue of confirmation cards one chat is waiting on.
 *
 * A model can call card-gated tools in parallel. On 2026-10-06 one turn
 * made ten calls, and the server parked ten tools, each on its own card. The
 * chat had ONE card slot, so nine cards were never shown and the run never
 * ended. A reconnect replayed all ten events, the slot landed on a card the
 * member had already approved, and every Approve got a 409.
 *
 * The rules:
 *
 * 1. Every pending card is kept, keyed by its `request_id`, with no
 *    duplicates. Answering one card removes only that card.
 * 2. `confirmation_resolved` (published by `request_confirmation` in
 *    `acb_skills/ask_tools.py`) closes a card. The id is remembered, so a
 *    replay that sends the request again does not bring the card back.
 * 3. A card that is being answered is not shown again by a replay.
 * 4. A failed answer restores its card ONLY when the failure can pass on a
 *    retry: a network fault or a 5xx. A 409 says the id is not waiting, so
 *    the card goes, and a restored card would 409 for ever. The POST and
 *    that rule are `lib/respondInput.ts`; `settleAnswer` turns its outcome
 *    into the action for the card.
 *
 * Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/confirmationQueue.test.ts`.
 */

/** The event a closed card arrives as. The server owns the name
 *  (`CONFIRMATION_RESOLVED` in `ask_tools.py`), and
 *  `tests/unit/test_chat_hardening.py` checks the two agree. */
export const CONFIRMATION_RESOLVED = "confirmation_resolved";

export interface PendingConfirmation {
  /** The dedupe key: the `request_id` of a blocking card, else the event id. */
  key: string;
  title: string;
  detail?: string;
  context?: string;
  /** Set when the tool is parked on a server Future (a blocking card). */
  requestId?: string;
}

export interface ConfirmationQueueState {
  cards: PendingConfirmation[];
  /** Ids the server closed, or said are not waiting. A replay cannot reopen them. */
  resolved: ReadonlySet<string>;
  /** Ids with an answer in flight. A replay cannot reopen them either. */
  answering: ReadonlySet<string>;
}

export const EMPTY_CONFIRMATIONS: ConfirmationQueueState = {
  cards: [],
  resolved: new Set(),
  answering: new Set(),
};

export type ConfirmationAction =
  | { type: "requested"; value: unknown }
  | { type: "resolved"; requestId: string }
  | { type: "answering"; key: string }
  | { type: "answered"; key: string }
  | { type: "restore"; card: PendingConfirmation }
  /** A non-blocking card, answered by a chat message. Its key can come again. */
  | { type: "dismiss"; key: string }
  | { type: "runFinalized" }
  | { type: "reset" };

/** A `confirmation_requested` value as a card, or null when it is not one. */
export function cardFromEvent(value: unknown): PendingConfirmation | null {
  if (!value || typeof value !== "object") return null;
  const v = value as Record<string, unknown>;
  const requestId = v.request_id ? String(v.request_id) : undefined;
  return {
    // A non-blocking card has no request id. Its own id, or one fixed key,
    // keeps a re-send of it from drawing twice.
    key: requestId ?? String(v.id ?? "confirmation"),
    title: String(v.title ?? "Confirm action"),
    detail: v.detail ? String(v.detail) : undefined,
    context: v.context ? String(v.context) : undefined,
    requestId,
  };
}

function withAdded(set: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(set);
  next.add(key);
  return next;
}

function withRemoved(set: ReadonlySet<string>, key: string): Set<string> {
  const next = new Set(set);
  next.delete(key);
  return next;
}

export function confirmationReducer(
  state: ConfirmationQueueState,
  action: ConfirmationAction,
): ConfirmationQueueState {
  switch (action.type) {
    case "requested": {
      const card = cardFromEvent(action.value);
      if (!card) return state;
      if (state.resolved.has(card.key) || state.answering.has(card.key)) return state;
      const at = state.cards.findIndex((c) => c.key === card.key);
      if (at >= 0) {
        // The same card again (a replay). Keep its place in the queue.
        const cards = state.cards.slice();
        cards[at] = card;
        return { ...state, cards };
      }
      return { ...state, cards: [...state.cards, card] };
    }
    case "resolved": {
      const key = action.requestId;
      if (!key) return state;
      return {
        cards: state.cards.filter((c) => c.key !== key),
        resolved: withAdded(state.resolved, key),
        answering: withRemoved(state.answering, key),
      };
    }
    case "answering":
      return {
        ...state,
        cards: state.cards.filter((c) => c.key !== action.key),
        answering: withAdded(state.answering, action.key),
      };
    case "answered":
      // The server took the answer. Its `confirmation_resolved` follows, and
      // this keeps a replay that arrives first from reopening the card.
      return {
        ...state,
        resolved: withAdded(state.resolved, action.key),
        answering: withRemoved(state.answering, action.key),
      };
    case "restore": {
      const key = action.card.key;
      const answering = withRemoved(state.answering, key);
      if (state.resolved.has(key) || state.cards.some((c) => c.key === key)) {
        return { ...state, answering };
      }
      // First, so the member sees the card they tried to answer.
      return { ...state, cards: [action.card, ...state.cards], answering };
    }
    case "dismiss":
      return { ...state, cards: state.cards.filter((c) => c.key !== action.key) };
    case "runFinalized":
      // A finished run waits on nothing. A non-blocking card stays: its
      // answer is a new chat message.
      return { ...state, cards: state.cards.filter((c) => !c.requestId) };
    case "reset":
      return EMPTY_CONFIRMATIONS;
  }
}

/** The action for a card once its POST settled (`lib/respondInput.ts`). */
export function settleAnswer(
  card: PendingConfirmation,
  outcome: "ok" | "drop" | "retry",
): ConfirmationAction {
  return outcome === "retry"
    ? { type: "restore", card }
    : { type: "answered", key: card.key };
}
