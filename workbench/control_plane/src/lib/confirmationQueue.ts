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
 *    retry: a network fault or a 5xx. A 4xx (a 409) removes the card, and a
 *    restored card would get a 409 on every click. The POST and that rule
 *    are `lib/respondInput.ts`. `settleAnswer` turns its outcome into the
 *    action for the card.
 * 5. A 4xx does NOT remember the id. The gateway also answers 409 when the
 *    control bus is slow or has no listener (`dispatch_control`), and then
 *    the tool still waits. Only the server's `confirmation_resolved` closes
 *    a card for good, so a later replay can show a card that still waits.
 *
 * Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/confirmationQueue.test.ts`.
 */

/** The event a closed card arrives as. The server owns the name
 *  (`CONFIRMATION_RESOLVED` in `ask_tools.py`), and
 *  `tests/unit/test_chat_hardening.py` checks the two agree. */
export const CONFIRMATION_RESOLVED = "confirmation_resolved";

/**
 * One checkbox of a card with rows (WS-46 P13 one-card). The tool offers the
 * rows, and the member's Approve names the ticked ids.
 */
export interface ConfirmationRow {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
}

export interface PendingConfirmation {
  /** The dedupe key: the `request_id` of a blocking card, else the event id. */
  key: string;
  title: string;
  detail?: string;
  context?: string;
  /** Set when the tool is parked on a server Future (a blocking card). */
  requestId?: string;
  /** Set only for a card with rows. A card without rows is as before. */
  rows?: ConfirmationRow[];
}

/**
 * The answer of a card with rows: `APPROVE {"rows": [...]}`. The server owns
 * the shape (`ROWS_ANSWER_PREFIX` and `ticked_rows` in `ask_tools.py`), and
 * `tests/unit/test_confirmation_rows.py` reads this file to hold the two in
 * step. A card without rows answers a plain `APPROVE`.
 */
export const ROWS_ANSWER_PREFIX = "APPROVE ";

export type RowsApproval = `APPROVE {${string}`;

export function approveRows(ids: string[]): RowsApproval {
  return `${ROWS_ANSWER_PREFIX}${JSON.stringify({ rows: ids })}` as RowsApproval;
}

/** The most rows the card draws. The server holds the same cap (`MAX_CARD_ROWS`). */
export const MAX_CARD_ROWS = 100;

/** The rows of an event, or undefined for a list the card cannot read. */
export function rowsFromEvent(value: unknown): ConfirmationRow[] | undefined {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_CARD_ROWS) return undefined;
  const rows: ConfirmationRow[] = [];
  const seen = new Set<string>();
  for (const raw of value) {
    if (!raw || typeof raw !== "object") return undefined;
    const r = raw as Record<string, unknown>;
    const id = typeof r.id === "string" ? r.id : "";
    if (!id || seen.has(id)) return undefined;
    seen.add(id);
    rows.push({
      id,
      label: String(r.label ?? id),
      hint: r.hint ? String(r.hint) : undefined,
      checked: r.checked !== false,
    });
  }
  return rows;
}

/**
 * The summary of a card with rows, as the member ticks them. The tool's
 * title says the whole count ("Create 4 tasks in «Ops»?"), and the card says
 * how many of them the Approve covers ("Create 3 of 4 tasks in «Ops»?"). A
 * title with no count keeps its words, and the count follows them.
 */
export function rowSummary(title: string, ticked: number, total: number): string {
  if (ticked === total) return title;
  const count = new RegExp(`\\b${total}\\b`);
  if (count.test(title)) return title.replace(count, `${ticked} of ${total}`);
  return `${title} (${ticked} of ${total})`;
}

/**
 * The card as the member left it: each row ticked as the member ticked it.
 * The queue hands THIS card to the answer path, so a restore after a failed
 * POST (a 5xx, a network fault) shows the member's ticks, never the tool's
 * defaults again (review round 1). A card with no rows is the same card.
 */
export function withTicked(
  card: PendingConfirmation,
  ticked?: readonly string[],
): PendingConfirmation {
  if (card.rows === undefined || ticked === undefined) return card;
  const on = new Set(ticked);
  return { ...card, rows: card.rows.map((r) => ({ ...r, checked: on.has(r.id) })) };
}

/**
 * What the queue sends for an Approve: the answer, and the card with the
 * member's ticks on it, which a failed POST restores. Null approves nothing.
 */
export function approvedCard(
  card: PendingConfirmation,
  ticked?: readonly string[],
): { card: PendingConfirmation; answer: "APPROVE" | RowsApproval } | null {
  const answer = approvalFor(card, ticked);
  return answer ? { card: withTicked(card, ticked), answer } : null;
}

/**
 * The Approve answer of a card: a plain `APPROVE` for a card with no rows,
 * and the ticked ids for a card with rows. It keeps only ids the card
 * offered, in the card's order, and gives null when none is left, because
 * an approval of no row is no approval (the server refuses it too).
 */
export function approvalFor(
  card: Pick<PendingConfirmation, "rows">,
  ticked?: readonly string[],
): "APPROVE" | RowsApproval | null {
  if (card.rows === undefined) return "APPROVE";
  const wanted = new Set(ticked ?? []);
  const ids = card.rows.filter((r) => wanted.has(r.id)).map((r) => r.id);
  return ids.length > 0 ? approveRows(ids) : null;
}

export interface ConfirmationQueueState {
  cards: PendingConfirmation[];
  /** Ids the server closed, or that took an answer. A replay cannot reopen them. */
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
  /** The POST got a 4xx. The card stays gone until a replay shows it again. */
  | { type: "dropped"; key: string }
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
    // A rows list the card cannot read gives NO rows, so nothing can be
    // ticked and Approve stays off. A plain APPROVE would be refused anyway.
    ...(v.rows !== undefined ? { rows: rowsFromEvent(v.rows) ?? [] } : {}),
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
    case "dropped":
      return { ...state, answering: withRemoved(state.answering, action.key) };
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
  if (outcome === "retry") return { type: "restore", card };
  if (outcome === "drop") return { type: "dropped", key: card.key };
  return { type: "answered", key: card.key };
}
