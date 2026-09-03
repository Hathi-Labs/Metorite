/**
 * Projects · where a status SITS — the position arithmetic for the editor.
 *
 * `pm_task_statuses.position` is a plain integer, root-scoped, and every read
 * orders by `position, name`. It decides board column order, list section order
 * and the order of the status picker, so it is not decoration.
 *
 * Two things made this a module with a test rather than inline arithmetic.
 *
 * **A new status defaults to position 0.** `admin.create_status` writes
 * `values.get("position") or 0`, and the seeded rows are spaced 10/20/30/40.
 * So a status created without an explicit position lands BEFORE Backlog — add
 * "In review" to a board and it appears as the first column. The editor has to
 * compute the position, and the rule for computing it is the interesting part.
 *
 * **Reordering has to stay inside a category.** Moving "In review" above "In
 * progress" is a reorder. Moving it above "To do" would put an `in_progress`
 * lane to the left of a `todo` lane, which makes the board read backwards
 * against the lifecycle it is grouped by. Changing which stage a status reports
 * as is the category control's job, not the arrow's.
 */

import { EDITABLE_CATEGORIES, STATUS_CATEGORIES } from "@/lib/statusCategory";

/** The minimum a row needs for this module to place it. */
export interface Placeable {
  id: string;
  name: string;
  category: string;
  position: number;
}

/** One row to PATCH, and its new position. */
export interface PositionPatch {
  id: string;
  position: number;
}

/** The gap left between neighbours, so an insert between two rows needs no
 *  renumber. Matches the 10/20/30/40 the seed writes. */
export const POSITION_STEP = 10;

/**
 * Lifecycle rank for ordering categories against each other.
 *
 * `STATUS_CATEGORIES` is the lifecycle order and holds every value the server
 * accepts. An unknown category sorts last rather than first: a value this
 * client has not heard of is more likely a later addition than a new front
 * door, and putting it at the head would reorder a board on deploy.
 */
function categoryRank(category: string): number {
  const index = (STATUS_CATEGORIES as readonly string[]).indexOf(category);
  return index === -1 ? STATUS_CATEGORIES.length : index;
}

/** Rows of one category, in the order every read draws them. */
export function siblings<T extends Placeable>(
  rows: readonly T[],
  category: string
): T[] {
  return rows
    .filter((row) => row.category === category)
    .sort((a, b) => a.position - b.position || a.name.localeCompare(b.name));
}

/** Every row in the order the BOARD draws its columns. */
function boardOrder<T extends Placeable>(rows: readonly T[]): T[] {
  return [...rows].sort(
    (a, b) =>
      a.position - b.position ||
      categoryRank(a.category) - categoryRank(b.category) ||
      a.name.localeCompare(b.name)
  );
}

/** Where a new status goes, and what has to move first to make room. */
export interface Placement {
  /** The position to create the new row at. */
    position: number;
  /**
   * Existing rows to reposition BEFORE the create, so `position` is free and
   * lands where it is meant to. Empty in the common case.
   */
  renumber: PositionPatch[];
}

/**
 * Where a NEW status in `category` should sit.
 *
 * It goes at the END of its own category, because that is where a lane someone
 * just invented belongs — "In review" added to `in_progress` sits after "In
 * progress", not before it. An empty category follows the last row of the
 * nearest EARLIER category, which is what keeps a first `cancelled` lane to the
 * right of Done rather than at position 0, where `create_status`'s own default
 * would have put it: the head of the board, left of Backlog.
 *
 * ⚠️ **The interesting half is the COLLISION, and it shipped broken for one
 * measured minute before the browser showed it.** The first version returned
 * `lastSibling.position + STEP`. On the seeded 10/20/30/40 board, adding a
 * second `in_progress` lane returned 40 — the position Done already held. The
 * editor still looked right, because it groups by category. The BOARD does not:
 * it orders columns by `position, name`, so it drew Backlog · To do · In
 * progress · **Done** · **In review**, with an in-progress lane to the right of
 * the done lane. Verified against the database: two rows at 40.
 *
 * So a placement has to respect what comes AFTER it, not only what comes
 * before. The gap between neighbours is used when there is one, and when there
 * is not, the board is renumbered rather than a tie being written. Never
 * writing a tie is the invariant, because a tie is resolved by NAME, and no
 * amount of later reordering can fix a board whose order depends on spelling.
 */
export function placeNew(
  rows: readonly Placeable[],
  category: string
): Placement {
  const ordered = boardOrder(rows);
  if (ordered.length === 0) return { position: POSITION_STEP, renumber: [] };

  const rank = categoryRank(category);
  const own = siblings(rows, category);

  // The row this one follows, and the row it must stay ahead of.
  let index: number;
  if (own.length > 0) {
    const last = own[own.length - 1];
    index = ordered.findIndex((r) => r.id === last.id) + 1;
  } else {
    // No sibling: sit after everything from an earlier stage.
    index = ordered.filter((r) => categoryRank(r.category) < rank).length;
  }

  const prev = index > 0 ? ordered[index - 1] : null;
  const next = index < ordered.length ? ordered[index] : null;

  if (!next) return { position: prev!.position + POSITION_STEP, renumber: [] };
  if (!prev) return { position: next.position - POSITION_STEP, renumber: [] };

  // Room between the two? Take the midpoint and touch nothing else.
  const gap = next.position - prev.position;
  if (gap >= 2) {
    return { position: prev.position + Math.floor(gap / 2), renumber: [] };
  }

  // No room. Re-space the whole board on the step, with a hole at `index`.
  const renumber: PositionPatch[] = [];
  let slot = POSITION_STEP;
  let position = POSITION_STEP;
  for (let i = 0; i <= ordered.length; i++) {
    if (i === index) {
      position = slot;
      slot += POSITION_STEP;
      continue;
    }
    const row = ordered[i > index ? i - 1 : i];
    if (row.position !== slot) renumber.push({ id: row.id, position: slot });
    slot += POSITION_STEP;
  }
  return { position, renumber };
}

/**
 * The position a new status should take, ignoring any renumber it needs.
 *
 * Kept because a caller that only wants to SHOW where something will land does
 * not want the patch list. A caller that WRITES must use {@link placeNew} and
 * apply `renumber` first, or it can write a tie.
 */
export function nextPosition(
  rows: readonly Placeable[],
  category: string
): number {
  return placeNew(rows, category).position;
}

/**
 * Move one status up or down WITHIN its category.
 *
 * Returns only the rows whose position actually changes, so the editor issues
 * the fewest PATCHes and a no-op move issues none. An empty array means the row
 * is already at the end it was asked to move toward.
 *
 * The normal case swaps two positions and costs two PATCHes. The degenerate
 * case — two siblings sharing a position, which the schema permits and a hand
 * SQL insert produces — cannot be fixed by swapping, because swapping equal
 * numbers changes nothing while the tie is broken by name. So the whole
 * category is renumbered in the intended order. Detecting that here rather than
 * assuming distinct positions is the difference between an arrow that works and
 * an arrow that silently does nothing on exactly the data a human hand-wrote.
 */
export function reorder(
  rows: readonly Placeable[],
  statusId: string,
  direction: "up" | "down"
): PositionPatch[] {
  const row = rows.find((r) => r.id === statusId);
  if (!row) return [];

  const group = siblings(rows, row.category);
  const index = group.findIndex((r) => r.id === statusId);
  if (index === -1) return [];

  const target = direction === "up" ? index - 1 : index + 1;
  if (target < 0 || target >= group.length) return [];

  const neighbour = group[target];

  if (neighbour.position !== row.position) {
    return [
      { id: row.id, position: neighbour.position },
      { id: neighbour.id, position: row.position },
    ];
  }

  // Tied positions: renumber the category in the order the move asked for.
  const moved = [...group];
  moved.splice(index, 1);
  moved.splice(target, 0, row);
  const base = Math.min(...group.map((r) => r.position));
  return moved
    .map((r, i) => ({ id: r.id, position: base + i * POSITION_STEP }))
    .filter((patch) => {
      const before = group.find((r) => r.id === patch.id);
      return before !== undefined && before.position !== patch.position;
    });
}

/**
 * True when this status is the only one in its category.
 *
 * The editor uses it to explain a refusal BEFORE the click rather than after:
 * deleting the last `done` status leaves a project that cannot complete a task
 * (`load_default_status(db, root, "done")` answers 422), and deleting the last
 * status of all leaves one that cannot create one. The server refuses either
 * way — this is so the button can say why instead of the toast having to.
 */
export function isLastInCategory(
  rows: readonly Placeable[],
  statusId: string
): boolean {
  const row = rows.find((r) => r.id === statusId);
  if (!row) return false;
  return siblings(rows, row.category).length === 1;
}

/**
 * Categories that hold no status, in lifecycle order.
 *
 * Shown as the editor's nudge: a project with no `done` lane cannot complete
 * anything, and a project with no `cancelled` lane has nowhere to drop work it
 * has abandoned. Naming the gap is cheaper than letting somebody discover it
 * from a 422 three weeks later.
 */
export function emptyCategories(rows: readonly Placeable[]): string[] {
  const used = new Set(rows.map((row) => row.category));
  return EDITABLE_CATEGORIES.filter((category) => !used.has(category));
}
