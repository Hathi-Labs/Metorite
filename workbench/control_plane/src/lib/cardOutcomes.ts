/**
 * The one vocabulary of card outcomes (HRD-7, spec `chat_failure_modes.md` C1).
 *
 * The server writes the text of each card outcome from ONE table,
 * `ask_tools.CARD_OUTCOMES` in `packages/acb_skills/acb_skills/ask_tools.py`.
 * Each text starts with its lead and a colon: "Declined: the member said no
 * on the card, so nothing was done. ...". This map holds the same leads.
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

/** The lead of each outcome, in the order of the Python table. */
export const CARD_OUTCOME_LEADS: Readonly<Record<CardOutcome, string>> = {
  approved: "Approved",
  declined: "Declined",
  "timed out": "Timed out",
  "closed by your message": "Closed by your message",
  "no card": "Not shown",
  "another member answered": "Answered by another member",
  parked: "Parked",
  "changed since": "Changed since the card",
};

const OUTCOMES = Object.keys(CARD_OUTCOME_LEADS) as CardOutcome[];

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
