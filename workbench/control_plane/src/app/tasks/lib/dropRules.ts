// /tasks · why a board drop is refused — said out loud (the WS-27y pattern,
// backported from `app/projects/lib/board.dropRefusal`).
//
// The old board refused silently: under a field sort a card dropped back into
// its own column just snapped home (no rank to write, no stage to change),
// and in select mode dragging was switched off. A card that snaps back
// wordlessly teaches people the board is broken, not that Priority sort owns
// the order. So the refusal becomes a sentence, rendered as an overlay on the
// hovered target while the card is in the air — same grammar as Projects.
//
// Note what is NOT refused: a cross-stage drop under a field sort is a real
// re-file (the stage changes even though the sort owns the order within it),
// and every drop in Manual sort is a real reorder. The refusal set is exactly
// the drops the handlers already ignore.

import { SORT_LABEL, type SortField } from "./ordering";

export interface DropContext {
  /** Multi-select is active (drag is suppressed, a drop can do nothing). */
  selectMode: boolean;
  /** The active sort — only "manual" lets a drop set the order. */
  sortField: SortField;
  /** The hovered column is the one the dragged card already lives in. */
  sameColumn: boolean;
}

/** The reason a drop into the hovered column is refused, or `null` when the
 *  drop is real (re-file, reorder, or both). */
export function dropRefusal(ctx: DropContext): string | null {
  if (ctx.selectMode) {
    return "Selecting — leave select mode to move tasks.";
  }
  if (ctx.sameColumn && ctx.sortField !== "manual") {
    return `Sorted by ${SORT_LABEL[ctx.sortField]} — switch to Manual sort to reorder within a stage.`;
  }
  return null;
}
