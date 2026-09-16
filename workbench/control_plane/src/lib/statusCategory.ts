/**
 * The status CATEGORY vocabulary — the mapped half of a status (WS-27 · owner
 * directive 2026-09-03).
 *
 * A status has two halves. `name` and `color` belong to whoever owns the space:
 * "IN PROCESS", "Building", "On the bench". `category` is the machine-readable
 * half, and it is the only part other code may key off — completion
 * (`CLOSING_CATEGORIES` stamps `completed_at`), the personal disposition
 * (`derive_disposition`), the roll-up (`by_category` on every summary) and
 * every cross-project number all read it and never the name.
 *
 * ## Why this module exists
 *
 * Two surfaces render categories and they must agree.
 *
 *  - **Projects** groups the status editor by category, because the whole point
 *    of a custom lane is which stage it reports as.
 *  - **Tasks** shows the category INSTEAD of the name. A personal list spans
 *    projects, and "IN PROCESS" in Marketing next to "Building" in Engineering
 *    are the same fact under two names. Grouping a personal list by the name
 *    invents a group per space; grouping by the category is the one reading
 *    that stays legible as projects multiply.
 *
 * `statusAccent.ts` already owns the category → HUE map. This owns the
 * category → ORDER, LABEL and MEANING. Two modules rather than one because
 * `statusAccent` is consumed as a paint function by cards, chips and lanes,
 * and the labels below are consumed as a vocabulary by two editors.
 *
 * ⚠️ **This is not a second status vocabulary** (AGENTS.md rule 4). It is the
 * presentation of the one vocabulary the gateway defines, and
 * `statusCategory.test.ts` fails if this file and `core.py` disagree — the
 * fence reads the Python rather than trusting this comment (R7).
 */

/**
 * Every category the server accepts, in LIFECYCLE order.
 *
 * Mirrors `STATUS_CATEGORIES` in
 * `apps/services/gateway/gateway/routes/projects/core.py`, which mirrors the
 * CHECK constraint migration 146 left in force and 164 widened. The order here
 * is ours and the server has none — a category is a stage, and stages have one
 * true order, which is what lets a board grouped by category read left to
 * right.
 *
 * It holds all six, including `triage`, so a stored row can never become
 * invisible. What the EDITOR offers is a shorter list — see below.
 */
export const STATUS_CATEGORIES = [
  "triage",
  "backlog",
  "todo",
  "in_progress",
  "done",
  "cancelled",
] as const;

export type StatusCategory = (typeof STATUS_CATEGORIES)[number];

/**
 * The categories the status editor offers when creating a lane.
 *
 * **Five, not six: `triage` is deliberately absent.** Owner directive
 * 2026-09-03 — "we don't need triage, backlog is kind of a substitute for
 * triage". The value stays in the vocabulary above because the server still
 * accepts it and `triage_exclusion_clause` in `core.py` still keys off it, so
 * an existing triage row keeps working and keeps rendering. Nothing new is
 * pointed at it.
 *
 * Measured on the dev database on 2026-09-03 before this shipped: five roots,
 * twenty statuses, and **zero** in `triage` or `cancelled`. So omitting it from
 * the create path took nothing away from anybody.
 *
 * ⚠️ Do not "tidy" this by deleting `triage` from `STATUS_CATEGORIES` as well.
 * The intake rail writes it, and a category the editor cannot create is very
 * different from a category the UI cannot display.
 */
export const EDITABLE_CATEGORIES = [
  "backlog",
  "todo",
  "in_progress",
  "done",
  "cancelled",
] as const;

/** What a member sees. Sentence case, because these are labels and not shouts. */
export const CATEGORY_LABEL: Record<string, string> = {
  triage: "Triage",
  backlog: "Backlog",
  todo: "To do",
  in_progress: "In progress",
  done: "Done",
  cancelled: "Cancelled",
};

/**
 * What each category MEANS, in the member's terms.
 *
 * These are the hint text beside each group in the editor, and they exist
 * because the consequence of a category is invisible at the moment you pick
 * one. Choosing `done` for a lane called "Handed off" silently makes every task
 * that reaches it complete, disappear from "what is still due", and count as
 * finished in the roll-up. That is a large behaviour to leave to a word.
 */
export const CATEGORY_HINT: Record<string, string> = {
  triage:
    "Parked at the front door. Tasks here are hidden from lists and boards " +
    "until somebody sorts them.",
  backlog: "Captured, not committed to. Kept out of anyone's next actions.",
  todo: "Committed and not started.",
  in_progress: "Being worked on now.",
  done: "Finished. Reaching a lane here completes the task everywhere.",
  cancelled:
    "Dropped, not finished. Counts as closed, so it leaves the outstanding " +
    "work — but it is not a win.",
};

/**
 * Categories that close a task.
 *
 * Mirrors `CLOSING_CATEGORIES` in `core.py`. Crossing INTO one stamps
 * `completed_at`; crossing out clears it. `cancelled` counts as closed on
 * purpose — a cancelled task is not outstanding work, and leaving it open would
 * keep it in every "what is still due" read forever.
 */
export const CLOSING_CATEGORIES: ReadonlySet<string> = new Set([
  "done",
  "cancelled",
]);

/** True when a lane in this category finishes the task that reaches it. */
export function closesTask(category: string): boolean {
  return CLOSING_CATEGORIES.has(category);
}

/** The label, falling back to the raw value rather than to nothing.
 *
 *  A category we do not know is a server that moved ahead of this file, and the
 *  useful failure is showing the unfamiliar word — not an empty chip that makes
 *  the status look broken. */
export function categoryLabel(category: string | null | undefined): string {
  if (!category) return "No status";
  return CATEGORY_LABEL[category] ?? category;
}

/** One category and the statuses filed under it, for the editor's groups. */
export interface CategoryGroup<T> {
  category: string;
  label: string;
  hint: string;
  /** False for a group the editor shows but does not offer on the create path. */
  editable: boolean;
  rows: T[];
}

/**
 * Group statuses into the editor's sections.
 *
 * Returns the five editable categories ALWAYS — an empty group is how a member
 * discovers they have no Cancelled lane and can add one, so an empty section is
 * information rather than clutter. Then it appends any other category that
 * actually holds rows, marked `editable: false`.
 *
 * That tail is the part worth keeping: a root carrying a `triage` status, or a
 * category a later migration adds, must not have its lanes silently vanish from
 * the one screen that can fix them. A status you cannot see is a status you
 * cannot rename, and it still draws a column on the board.
 */
export function groupByCategory<T extends { category: string; position: number; name: string }>(
  rows: readonly T[]
): CategoryGroup<T>[] {
  const sorted = [...rows].sort(
    (a, b) => a.position - b.position || a.name.localeCompare(b.name)
  );
  const groups: CategoryGroup<T>[] = EDITABLE_CATEGORIES.map((category) => ({
    category,
    label: CATEGORY_LABEL[category] ?? category,
    hint: CATEGORY_HINT[category] ?? "",
    editable: true,
    rows: sorted.filter((row) => row.category === category),
  }));

  const known = new Set<string>(EDITABLE_CATEGORIES);
  const strays = sorted.filter((row) => !known.has(row.category));
  for (const category of STATUS_CATEGORIES) {
    if (known.has(category)) continue;
    const rowsHere = strays.filter((row) => row.category === category);
    if (rowsHere.length === 0) continue;
    groups.push({
      category,
      label: CATEGORY_LABEL[category] ?? category,
      hint: CATEGORY_HINT[category] ?? "",
      editable: false,
      rows: rowsHere,
    });
  }
  // A value neither list knows — a server ahead of this file. Show it last,
  // under its raw name, rather than dropping the rows.
  const listed = new Set(groups.map((g) => g.category));
  for (const row of sorted) {
    if (listed.has(row.category)) continue;
    listed.add(row.category);
    groups.push({
      category: row.category,
      label: row.category,
      hint: "This project uses a status category this screen does not know.",
      editable: false,
      rows: sorted.filter((r) => r.category === row.category),
    });
  }
  return groups;
}
