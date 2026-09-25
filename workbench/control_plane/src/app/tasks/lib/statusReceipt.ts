/**
 * My Tasks · what a status write SAYS it did (D79).
 *
 * "Stages group, statuses write." A write always lands in one exact status,
 * and the receipt names it. A member who drags a card to In progress must be
 * able to read WHICH In progress status the task is now in, because two
 * statuses of one stage can mean different things ("Doing" and "In review").
 *
 * The receipt is the undo toast's title (`undoToast.ts`), so every receipt
 * here comes with Undo.
 *
 * | The write                          | The receipt                          |
 * |------------------------------------|--------------------------------------|
 * | a board task moves                 | Moved to In review · Website relaunch |
 * | a personal task moves              | Moved to Doing                       |
 * | Mark done, 2+ Done statuses        | Done · Shipped in Website relaunch   |
 * | Mark done, 1 Done status           | Marked done                          |
 *
 * A personal task names no project: "My Tasks" says nothing the member does
 * not already know. Fence: `statusReceipt.test.ts`.
 */

/** Where the task lives, as far as a receipt needs to know. */
export interface ReceiptPlace {
  projectName?: string;
  /** In my own tree (my root or one of my Areas). */
  personal: boolean;
}

const where = (place: ReceiptPlace): string | null =>
  !place.personal && place.projectName ? place.projectName : null;

/** A move to one exact status. */
export function moveReceipt(status: { name: string }, place: ReceiptPlace): string {
  const project = where(place);
  return project ? `Moved to ${status.name} · ${project}` : `Moved to ${status.name}`;
}

/**
 * Mark done. It never asks (D79 rule 2), so it lands in the FIRST Done status
 * by position. When the set holds more than one Done status, the receipt
 * names the one it chose. `doneLanes` is the Done stage in board order
 * (`stageLanes(lanes, "done")`).
 */
export function doneReceipt(
  doneLanes: readonly { name: string }[],
  place: ReceiptPlace,
): string {
  if (doneLanes.length < 2) return "Marked done";
  const project = where(place);
  const first = doneLanes[0].name;
  return project ? `Done · ${first} in ${project}` : `Done · ${first}`;
}
