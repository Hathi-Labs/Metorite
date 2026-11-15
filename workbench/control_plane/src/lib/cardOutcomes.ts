/**
 * The one vocabulary of card outcomes (HRD-7, spec `chat_failure_modes.md` C1).
 *
 * The server writes the text of each card outcome from ONE table,
 * `ask_tools.CARD_OUTCOMES` in `packages/acb_skills/acb_skills/ask_tools.py`.
 * Each text is "<lead>: <happened> [act] <next>". The next step is for the
 * model, never for the member, so a receipt draws `memberLineOf` of it: the
 * head, the server's `card_outcome_head`.
 *
 * `CARD_OUTCOME_TEXTS` holds the same three parts of each row.
 * `cardOutcomes.test.ts` reads the Python table and holds the two equal, and
 * `tests/unit/test_card_outcome_vocabulary.py` reads this file.
 *
 * A card file reads the outcome of a result here, and keeps its own chip for
 * it. No card file writes a lead of its own.
 */

export type CardOutcome =
  | "approved"
  | "declined"
  | "timed out"
  | "closed by your message"
  | "no card"
  | "another member answered"
  | "parked"
  | "changed since";

/** The three parts of one row of the table. */
export interface CardOutcomeText {
  lead: string;
  happened: string;
  next: string;
}

/** Each row of the Python table, in its order. One row a line, so both
 *  fences can read it. */
export const CARD_OUTCOME_TEXTS: Readonly<Record<CardOutcome, CardOutcomeText>> = {
  approved: { lead: "Approved", happened: "the member approved the card.", next: "Do the act now, and say what it did." },
  declined: { lead: "Declined", happened: "the member said no on the card, so nothing was done. The card is closed, and no decision is open.", next: "Tell the member that it was not done. Show the card again only if the member asks for it." },
  "timed out": { lead: "Timed out", happened: "the card got no answer in time, so nothing was done. The card is closed.", next: "Tell the member that it was not done. Ask whether to show the card again." },
  "closed by your message": { lead: "Closed by your message", happened: "the member wrote in the chat while the card was open, and nothing was done.", next: "Read the member's words next, as their own chat message. If they change this act, keep each part that they do not change. Then call the tool again, so that a new card shows. If they ask about something else, answer that first. Then ask whether to show the card again." },
  "no card": { lead: "Not shown", happened: "a card cannot show here, so nothing was done.", next: "Tell the member that this needs their approval on a card. Ask them to ask for it again in the Metorite web app, where a card can show." },
  "another member answered": { lead: "Answered by another member", happened: "someone other than the member who asked answered the card, so nothing was done.", next: "Tell the member that only they can approve this card. Ask whether to show it again." },
  parked: { lead: "Parked", happened: "the card waits in the chat for the member's answer, and nothing was done yet.", next: "Tell the member that the card waits for them. Do not say that it is done." },
  "changed since": { lead: "Changed since the card", happened: "the item changed after the card showed, so nothing was done.", next: "Read the item again. Then show a new card with what is true now." },
};

const OUTCOMES = Object.keys(CARD_OUTCOME_TEXTS) as CardOutcome[];

/** The lead of each outcome, in the order of the Python table. */
export const CARD_OUTCOME_LEADS: Readonly<Record<CardOutcome, string>> = Object.fromEntries(
  OUTCOMES.map((o) => [o, CARD_OUTCOME_TEXTS[o].lead]),
) as Record<CardOutcome, string>;

/**
 * The outcome of a tool result that starts with a lead of the table and its
 * colon, or null. A result of before HRD-7 ("Cancelled — ...") gives null,
 * so each card file keeps its own match for those.
 */
export function cardOutcomeOf(text: string | null | undefined): CardOutcome | null {
  const t = (text || "").trimStart();
  return OUTCOMES.find((o) => t.startsWith(`${CARD_OUTCOME_LEADS[o]}:`)) ?? null;
}

/**
 * The words of a result that a member reads: the head of a card outcome,
 * with no next step. That is the lead, what happened and the act sentence,
 * as the server's `card_outcome_head` builds it. It cuts at the next step of
 * the outcome's row.
 *
 * A text that does not start with a head of the table is not a card outcome:
 * it comes back as it was, a stored result of before HRD-7 too. A head with
 * a next step of its own (the email redraft texts) gives the head alone, so
 * no step for the model ever reaches the member.
 */
export function memberLineOf(text: string | null | undefined): string {
  const raw = text ?? "";
  const t = raw.trim();
  const outcome = cardOutcomeOf(t);
  if (outcome === null) return raw;
  const row = CARD_OUTCOME_TEXTS[outcome];
  const head = `${row.lead}: ${row.happened}`;
  if (!t.startsWith(head)) return raw;
  const at = t.indexOf(row.next, head.length);
  return (at >= 0 ? t.slice(0, at) : head).trim();
}

/**
 * The outcomes of a card that did nothing and was no message of the member:
 * a no, a timeout, no card, another member's answer, a park, a change since.
 * The receipt draws each one as "Cancelled", as it drew the one text of
 * before (D200: the look does not change). A card that the member's own chat
 * message closed is not in it: it is no decline, and each card file keeps its
 * own receipt for it.
 */
export const NOT_DONE_AT_CARD: ReadonlySet<CardOutcome> = new Set<CardOutcome>([
  "declined",
  "timed out",
  "no card",
  "another member answered",
  "parked",
  "changed since",
]);
