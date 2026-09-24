/**
 * Projects · the spreadsheet layout's row and column model (WS-27x).
 *
 * Pure functions, because the three decisions a table gets subtly wrong are
 * each one assertion here rather than a screenshot: which columns a view's
 * `shown_fields` produces (and in what order), how sub-tasks nest under a
 * parent that is on the same page, and what a header click means for the sort
 * the server is asked for.
 *
 * **Sorting is the SERVER's** — a header click maps to the sort keys
 * `GET /projects/tasks` already accepts (`core.py TASK_SORTS`), never a
 * client-side re-sort. Pagination happens in SQL, so a client sort would only
 * ever reorder the page it can see; and `status` is a semantic sort
 * (category rank then lane position, WS-27w) that the browser has no business
 * re-deriving.
 */

import type { TaskRow } from "./api";
import type { FieldDef } from "./customFields";
import {
  CUSTOM_FIELD_PREFIX,
  FIELD_KEYS,
  FIELD_LABELS,
  type FieldKey,
  customFieldKey,
} from "./shownFields";

/** Mirrors the gateway's `TASK_SORTS` keys. An unknown key there is a 422. */
export const TASK_SORT_KEYS = [
  "created_at",
  "updated_at",
  "due_at",
  "importance",
  "title",
  "task_number",
  "completed_at",
  "status",
] as const;

export type SortKey = (typeof TASK_SORT_KEYS)[number];

export interface TableSort {
  key: SortKey;
  dir: "asc" | "desc";
}

/**
 * Which sort key a column's header click sends — only for columns whose field
 * IS a `TASK_SORTS` key. The rest (assignees, tags, sub-task progress, custom
 * fields…) have no server ordering and their headers are honest labels, not
 * disabled buttons pretending otherwise.
 */
const COLUMN_SORTS: Partial<Record<FieldKey, SortKey>> = {
  status: "status",
  due_at: "due_at",
  importance: "importance",
  created_at: "created_at",
};

/**
 * One header click: unsorted → ascending → descending → back to the view's
 * own order. The third state matters — a table that can never return to the
 * board's hand-arranged order (`sortForView`) has quietly replaced it.
 * Clicking a different column starts that column ascending.
 */
export function nextSort(
  current: TableSort | null,
  key: SortKey | null
): TableSort | null {
  if (!key) return current;
  if (!current || current.key !== key) return { key, dir: "asc" };
  if (current.dir === "asc") return { key, dir: "desc" };
  return null;
}

/** The query parameters a sort adds to the tasks fetch — nothing when none. */
export function sortQuery(sort: TableSort | null): Record<string, string> {
  return sort ? { sort: sort.key, direction: sort.dir } : {};
}

export interface TableColumn {
  /** A `shownFields` key — core, or `custom.<field_key>`. */
  key: string;
  label: string;
  /** What a header click sorts by, or `null` for an unsortable column. */
  sortKey: SortKey | null;
  /** Present only on custom-field columns. */
  def?: FieldDef;
}

/**
 * The columns a shown-field set draws, in canonical order: core fields in the
 * vocabulary's declaration order, then custom fields in the registry's own
 * (`position`) order. The stored list is a SET (`shownFields.ts` says why),
 * so its order contributes nothing here.
 *
 * A shown `custom.<key>` with no surviving definition produces NO column: the
 * field was deleted after the view was saved, and a header with no data and
 * no editor under it is a column of nothing.
 */
export function tableColumns(
  shown: readonly string[],
  defs: readonly FieldDef[]
): TableColumn[] {
  const wanted = new Set(shown);
  const out: TableColumn[] = [];
  for (const key of FIELD_KEYS) {
    if (!wanted.has(key)) continue;
    out.push({ key, label: FIELD_LABELS[key], sortKey: COLUMN_SORTS[key] ?? null });
  }
  const orderedDefs = [...defs].sort(
    (a, b) => a.position - b.position || a.name.localeCompare(b.name)
  );
  for (const def of orderedDefs) {
    const key = customFieldKey(def);
    if (!wanted.has(key)) continue;
    out.push({ key, label: def.name, sortKey: null, def });
  }
  return out;
}

/**
 * ── The LIST's columns (WS-27ab item 6) ──────────────────────────────────
 *
 * The list is not a thin table: it draws three fixed columns (`#`, Title, and
 * the shared chip strip) plus an optional selection gutter, and exactly two
 * gated ones. Those two — Status and Assignees — rendered **unconditionally**
 * from WS-27x until now, which made the field picker a liar on that surface:
 * un-ticking *Status* silenced its chip everywhere and left the column
 * standing.
 *
 * `shown_fields` is one contract, so the list obeys it like everything else.
 * **No default moves**: both keys have been in `DEFAULT_SHOWN` since WS-27x,
 * so a view nobody has edited draws exactly the columns it drew yesterday.
 *
 * Returned as an ordered key list rather than a count because the `colSpan` on
 * the group header and the quick-add row has to equal what the header renders
 * — computing that number twice by hand is how a table ends up with a heading
 * that spans four of its five columns.
 *
 * Fence: `table.test.ts` — "the list's columns".
 */
export const LIST_GATED_COLUMNS = ["status", "assignees"] as const;

export function listColumns(
  shown: readonly string[],
  selectable: boolean,
): string[] {
  const wanted = new Set(shown);
  return [
    ...(selectable ? ["select"] : []),
    "ref",
    "title",
    ...LIST_GATED_COLUMNS.filter((key) => wanted.has(key)),
    "details",
  ];
}

// D78 (owner decision 2026-09-24): the 0-3 priority vocabulary is retired.
// Projects shows the matrix level, the same as My Tasks. `lib/matrix.ts` owns
// the level, and the flags a person sets. The `importance` column key stays,
// so saved views and the gateway's sort keep working.

/** The `field_key` a `custom.<key>` column patches, for the cell editor. */
export function customKeyOf(columnKey: string): string | null {
  return columnKey.startsWith(CUSTOM_FIELD_PREFIX)
    ? columnKey.slice(CUSTOM_FIELD_PREFIX.length)
    : null;
}

export interface TableRow<T extends TaskLike = TaskRow> {
  task: T;
  /** 0 for a top-level row; +1 per nesting level under an on-page parent. */
  depth: number;
  /** How many DIRECT sub-task rows sit under this one (0 = no caret). */
  childCount: number;
}

type TaskLike = { id: string; parent_task_id?: string | null };

/**
 * One group's tasks → the rows the table draws, sub-tasks indented under
 * their parent.
 *
 * The same self-FK the server's `tree.py` walks for projects: hierarchy is
 * `parent_task_id`, nothing else. A task whose parent is NOT in this list —
 * filtered out, on another page, or in another group — renders at the top
 * level rather than disappearing: the filter said "show this task", and
 * hiding it because its parent did not qualify would make a filtered table
 * lose rows silently.
 *
 * Within a nesting level the incoming order is preserved, because the caller
 * has already ordered the list (server sort, or `sortForView`).
 *
 * `collapsed` hides a parent's whole subtree; collapse state is local to the
 * component (per the ticket), so this only needs the set.
 */
export function treeRows<T extends TaskLike>(
  tasks: readonly T[],
  collapsed: ReadonlySet<string>
): TableRow<T>[] {
  const present = new Set(tasks.map((t) => t.id));
  const children = new Map<string, T[]>();
  const roots: T[] = [];
  for (const task of tasks) {
    const parent = task.parent_task_id;
    if (parent && present.has(parent) && parent !== task.id) {
      const bucket = children.get(parent);
      if (bucket) bucket.push(task);
      else children.set(parent, [task]);
    } else {
      roots.push(task);
    }
  }

  const out: TableRow<T>[] = [];
  // Iterative with an explicit visited set: the server forbids parent cycles
  // (`assert_no_task_cycle`), but a stale page must degrade to missing
  // indentation, never to a hung tab.
  const visited = new Set<string>();
  // `hidden` still traverses (marking visited) so a collapsed subtree's
  // members are accounted for without being drawn — the rootless sweep below
  // must not resurface them flat.
  const walk = (task: T, depth: number, hidden: boolean) => {
    if (visited.has(task.id)) return;
    visited.add(task.id);
    const kids = children.get(task.id) ?? [];
    if (!hidden) out.push({ task, depth, childCount: kids.length });
    const hideKids = hidden || collapsed.has(task.id);
    for (const kid of kids) walk(kid, depth + 1, hideKids);
  };
  for (const root of roots) walk(root, 0, false);
  // A parent cycle (impossible server-side, but this must not depend on it)
  // leaves its members rootless; surface them flat rather than losing rows.
  for (const task of tasks) if (!visited.has(task.id)) walk(task, 0, false);
  return out;
}
