/**
 * The held send card (`email_app_master_plan.md` §21).
 *
 * A held chat read mail from outside senders, so a model in it may follow
 * an instruction that a stranger wrote. Its sends go through ONE card that
 * the gateway builds and records (`gateway/routes/email/automation/held_card.py`).
 * The card is the confirmation card with rows (`confirmationQueue.ts`), and
 * each row says what it is (`kind`) and how the SERVER classed the person on
 * it (`cls`). The client never classes a person and never ticks a row on its
 * own. It draws the server's default ticks, the server's marks and the
 * server's warnings, and the Approve answer names ticked ids only
 * (`approveRows`), as every rows card does.
 *
 * The rules this file holds:
 *
 * 1. A `new` row carries the mark {@link NEW_MARK}. The server starts it
 *    unticked, and the card draws it as the server sent it.
 * 2. A card with recipient rows may send only with a ticked To row
 *    ({@link sendCanGo}). With none, Send is off, and the server refuses it
 *    too ("Not sent: no recipient was ticked").
 * 3. A row with none of the new keys is exactly as before.
 *
 * The server owns the words of the marks (`acb_skills/held_card.py`), and
 * `tests/unit/test_held_send_card_record.py` reads this file to hold the two
 * in step. Pure, and in `lib/`, because `vitest.config.ts` runs in the node
 * environment. Fence: `src/lib/sendCard.test.ts`.
 */

/** What one row of a held card is. */
export type SendRowKind = "to" | "cc" | "bcc" | "file" | "unsubscribe";

/** How the server classed the person on a row (`CLASSES`). */
export type PersonClass = "org" | "sent" | "thread" | "new";

export const SEND_ROW_KINDS: readonly SendRowKind[] = ["to", "cc", "bcc", "file", "unsubscribe"];
export const PERSON_CLASSES: readonly PersonClass[] = ["org", "sent", "thread", "new"];

/** The mark of a new address (`NEW_MARK` in `held_card.py`). */
export const NEW_MARK = "new address — not in your contacts";
/** The mark of a person of the thread (`THREAD_MARK`). */
export const THREAD_MARK = "from this thread";

/** The recipient kinds. A card with one of them is a send card. */
const RECIPIENT_KINDS: ReadonlySet<string> = new Set(["to", "cc", "bcc"]);

/** The kind of a row of an event, or undefined for none or an unknown one. */
export function kindOf(value: unknown): SendRowKind | undefined {
  return typeof value === "string" && (SEND_ROW_KINDS as readonly string[]).includes(value)
    ? (value as SendRowKind)
    : undefined;
}

/**
 * The class of a row of an event, or undefined for none. An unknown class
 * reads as `new`, the class that marks a person, so a drift fails closed.
 */
export function classOf(value: unknown): PersonClass | undefined {
  if (typeof value !== "string" || !value) return undefined;
  return (PERSON_CLASSES as readonly string[]).includes(value) ? (value as PersonClass) : "new";
}

/** The fields of a row that this file reads. */
export interface SendRowFields {
  checked: boolean;
  kind?: SendRowKind;
  cls?: PersonClass;
}

/** True for a card with a recipient row: a held send card. */
export function isSendCard(rows: readonly SendRowFields[] | undefined): boolean {
  return !!rows && rows.some((r) => r.kind !== undefined && RECIPIENT_KINDS.has(r.kind));
}

/**
 * Send may go: a held send card needs a ticked To row. Any other card with
 * rows needs one ticked row, as before.
 */
export function sendCanGo(rows: readonly SendRowFields[] | undefined): boolean {
  if (rows === undefined) return true;
  if (isSendCard(rows)) return rows.some((r) => r.checked && r.kind === "to");
  return rows.some((r) => r.checked);
}

/** The mark that the card draws under a row, or "" for none. */
export function rowMark(cls: PersonClass | undefined): string {
  if (cls === "new") return NEW_MARK;
  if (cls === "thread") return THREAD_MARK;
  return "";
}

/** The line under Send when no To row is ticked, or "" when Send may go. */
export function noRecipientHint(rows: readonly SendRowFields[] | undefined): string {
  return isSendCard(rows) && !sendCanGo(rows) ? "Tick one To row at least. Nothing goes to an unticked row." : "";
}
