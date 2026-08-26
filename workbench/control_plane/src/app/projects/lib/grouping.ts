/**
 * Projects · grouping and the filter query (WS-27k).
 *
 * *"My open bugs in Ops, grouped by assignee"* — the grouping half. Filters are
 * SQL and live on the gateway (`routes/projects/filters.py`); grouping is
 * presentation and belongs here, over rows the list endpoint now returns with
 * their assignees attached.
 *
 * Everything is a pure function over plain data: what a board draws is easy to
 * get subtly wrong (a task with two assignees, a task with none, a lane with
 * nothing in it) and each of those is one assertion here.
 */

import type { StatusRow, TaskRow } from "./api";
import {
  DEFAULT_SHOWN,
  sameFieldSet,
  sanitizeShownFields,
} from "./shownFields";

export type GroupBy =
  | "status"
  | "assignee"
  | "project"
  | "importance"
  | "tag"
  | "none";

/** Mirrors the gateway's `filters.GROUP_BY`. */
export const GROUP_OPTIONS: GroupBy[] = [
  "status",
  "assignee",
  "project",
  "importance",
  "tag",
  "none",
];

/**
 * The board's grouping axis when nobody has chosen one.
 *
 * Named ONCE because two places default it — this module's `fromConfig`, for a
 * saved view whose config never stored an axis, and `page.tsx`'s initial state.
 * They were two string literals that had to agree. A default that disagrees
 * with itself re-groups a board on the second visit.
 */
export const DEFAULT_GROUP_BY: GroupBy = "status";

/**
 * The "Assigned to" control's value, and the two filter fields behind it.
 *
 * `Filters` carries `assignee` (an address) and `unassigned` (a flag) because
 * the SERVER takes two parameters. One control drives both, and the pair has
 * exactly four legal states, so the mapping is written here as two pure
 * functions rather than inline in the bar.
 *
 * ⚠️ `assignee` and `unassigned` are mutually exclusive. "Work assigned to
 * nobody, that is assigned to Priya" matches nothing, and a control that can
 * express it is a control that can ask an unanswerable question. Every write
 * goes through `assigneeFilter`, which always clears the other field.
 */
export const ANYONE = "";
export const UNASSIGNED = "__unassigned__";

/** What the control shows, given the filters. */
export function assigneeChoice(filters: Filters): string {
  if (filters.unassigned) return UNASSIGNED;
  return filters.assignee || ANYONE;
}

/** What the filters become, given a choice. Never sets both fields. */
export function assigneeFilter(choice: string): Pick<Filters, "assignee" | "unassigned"> {
  if (choice === UNASSIGNED) return { assignee: "", unassigned: true };
  return { assignee: choice === ANYONE ? "" : choice, unassigned: false };
}

/**
 * The addresses the "Assigned to" control offers, in the order it offers them.
 *
 * `me` first and always, so the commonest choice never moves. Then everyone
 * else on the board, sorted, case-folded to one entry each.
 *
 * `current` is unioned in on purpose. The list is derived from the tasks now on
 * screen, and those tasks are the FILTERED ones — so the moment you filter to
 * one person, everyone else leaves the list and the control cannot be changed
 * back to them. Keeping the current value present makes the control reversible.
 */
export function assigneeOptions(
  boardAssignees: readonly string[],
  me: string,
  current: string
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (raw: string) => {
    const value = raw.trim();
    if (!value) return;
    const key = value.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    out.push(value);
  };
  add(me);
  const rest = [...boardAssignees].sort((a, b) => a.localeCompare(b));
  for (const address of rest) add(address);
  if (current !== ANYONE && current !== UNASSIGNED) add(current);
  return out;
}

export interface TaskGroup {
  /** Stable identity — a status id, an address, a project id, or a sentinel. */
  key: string;
  label: string;
  tasks: TaskRow[];
}

/** The bucket for "this task has no value for the thing we grouped by". */
export const UNSET = "__unset__";

export interface Filters {
  q: string;
  statusCategory: string;
  assignee: string;
  unassigned: boolean;
  overdue: boolean;
  /** WS-27m — ANY of these tags. `tags_all` on the wire is not exposed here yet. */
  tags: string[];
}

/**
 * WS-27y — the board's second axis, and the lane state that travels with a view.
 *
 * One object rather than three loose values because they only mean anything
 * together: a collapsed-lane list without its axis is a list of keys from a
 * board that no longer exists.
 */
export interface BoardLanes {
  /** Sub-grouping axis drawn as swimlane rows. `"none"` = a flat board. */
  subGroupBy: GroupBy;
  /** Lane keys the viewer folded shut. Persisted with the view, like filters. */
  collapsedLanes: string[];
  /** Draw lanes with nothing in them. Off by default — an empty lane is noise. */
  showEmptyLanes: boolean;
}

export const NO_LANES: BoardLanes = {
  subGroupBy: "none",
  collapsedLanes: [],
  showEmptyLanes: false,
};

export const EMPTY_FILTERS: Filters = {
  q: "",
  statusCategory: "",
  assignee: "",
  unassigned: false,
  overdue: false,
  tags: [],
};

/**
 * Filters → query parameters for `GET /projects/tasks`.
 *
 * Only non-empty values are emitted. A `?overdue=false` would be indistinguishable
 * from "the client forgot to send it" on the server side, and sending every key
 * every time makes a saved view's stored config noisier than the choice it
 * records.
 */
export function toQuery(filters: Filters): Record<string, string> {
  const out: Record<string, string> = {};
  if (filters.q.trim()) out.q = filters.q.trim();
  if (filters.statusCategory) out.status_category = filters.statusCategory;
  if (filters.assignee.trim()) out.assignee = filters.assignee.trim();
  if (filters.unassigned) out.unassigned = "true";
  if (filters.overdue) out.overdue = "true";
  // CSV, matching `split_csv` on the gateway. A tag containing a comma would
  // break this — which is why the picker treats a comma as a separator, so
  // one can never be stored.
  if (filters.tags.length) out.tags = filters.tags.join(",");
  return out;
}

/** A stored view's `config.filters` → the form state, defaults filled in. */
export function fromConfig(config: unknown): {
  filters: Filters;
  groupBy: GroupBy;
  lanes: BoardLanes;
  /** WS-27x — the fields this view shows. Defaulted when nothing was stored. */
  shownFields: string[];
} {
  const raw = (config ?? {}) as Record<string, unknown>;
  const stored = (raw.filters ?? {}) as Record<string, unknown>;
  const groupBy = GROUP_OPTIONS.includes(raw.group_by as GroupBy)
    ? (raw.group_by as GroupBy)
    : DEFAULT_GROUP_BY;
  // A sub-axis equal to the main axis is a board that lanes by its own
  // columns — nonsense a hand-edited config could still say. Normalised to
  // "none" HERE so every consumer sees one truth rather than each re-deciding.
  const storedSub = raw.sub_group_by;
  const subGroupBy =
    GROUP_OPTIONS.includes(storedSub as GroupBy) && storedSub !== groupBy
      ? (storedSub as GroupBy)
      : "none";
  return {
    filters: {
      ...EMPTY_FILTERS,
      q: typeof stored.q === "string" ? stored.q : "",
      statusCategory:
        typeof stored.status_category === "string" ? stored.status_category : "",
      assignee: typeof stored.assignee === "string" ? stored.assignee : "",
      unassigned: stored.unassigned === true,
      overdue: stored.overdue === true,
      tags:
        typeof stored.tags === "string" && stored.tags
          ? stored.tags.split(",").map((s) => s.trim()).filter(Boolean)
          : [],
    },
    groupBy,
    lanes: {
      subGroupBy,
      // An array in the config (JSON keeps a list a list; lane keys never
      // travel as a query string, so there is no CSV shape to mirror). A lane
      // key that is not a string is a hand-edit, and is dropped.
      collapsedLanes: Array.isArray(raw.collapsed_lanes)
        ? raw.collapsed_lanes.filter((k): k is string => typeof k === "string")
        : [],
      showEmptyLanes: raw.show_empty_lanes === true,
    },
    // WS-27x — a set of known field keys (`shownFields.sanitizeShownFields`
    // owns the discipline: unknown/non-string dropped, duplicates collapsed).
    // ABSENT means the default set; an explicitly stored `[]` means every
    // column hidden — collapsing the two would un-hide a deliberate choice.
    shownFields: sanitizeShownFields(raw.shown_fields) ?? [...DEFAULT_SHOWN],
  };
}

/**
 * The form state → what gets stored on a view. Mirrors `fromConfig`.
 *
 * Deliberately *not* `toQuery`: a query string has only text, so `toQuery`
 * writes `"true"`, whereas a config is JSON and keeps a boolean a boolean.
 * `fromConfig` refuses a string where a toggle belongs — a hand-edited
 * `"false"` must not read as on — so a view built from query shape would come
 * back with its toggles silently cleared.
 */
export function toConfig(
  filters: Filters,
  groupBy: GroupBy,
  lanes: BoardLanes = NO_LANES,
  shownFields: readonly string[] = DEFAULT_SHOWN
): Record<string, unknown> {
  const stored: Record<string, unknown> = {};
  if (filters.q.trim()) stored.q = filters.q.trim();
  if (filters.statusCategory) stored.status_category = filters.statusCategory;
  if (filters.assignee.trim()) stored.assignee = filters.assignee.trim();
  if (filters.unassigned) stored.unassigned = true;
  if (filters.overdue) stored.overdue = true;
  // A CSV string here too, not an array: `build_task_filters` parses it with
  // `split_csv`, so a saved view and a typed query string must be the same
  // shape or the view would be the one that breaks.
  if (filters.tags.length) stored.tags = filters.tags.join(",");
  const config: Record<string, unknown> = { filters: stored, group_by: groupBy };
  // WS-27y — lane state, only when it says something. A flat board stores no
  // lane keys at all, so older views and lane-less views are byte-identical.
  if (lanes.subGroupBy !== "none" && lanes.subGroupBy !== groupBy) {
    config.sub_group_by = lanes.subGroupBy;
    if (lanes.collapsedLanes.length) config.collapsed_lanes = lanes.collapsedLanes;
    if (lanes.showEmptyLanes) config.show_empty_lanes = true;
  }
  // WS-27x — stored only when it differs from the default SET, so a view that
  // never touched its columns stays byte-identical to one saved before
  // shown-fields existed. Set comparison, not sequence: order is presentation
  // the vocabulary owns (`table.tableColumns`), and a toggle-off-toggle-on
  // must not dirty a config it did not change. An empty list IS stored —
  // "every column hidden" is a choice, not the default.
  if (!sameFieldSet(shownFields, DEFAULT_SHOWN)) {
    config.shown_fields = sanitizeShownFields([...shownFields]) ?? [];
  }
  return config;
}

/** Whether anything is actually filtering, for the "clear" affordance. */
export function isFiltered(filters: Filters): boolean {
  return Object.keys(toQuery(filters)).length > 0;
}

/**
 * ── The saved view's live state, and whether it still matches (WS-27ab item 2)
 *
 * Everything a view stores, held together. The four values only mean anything
 * as a set — `toConfig` already takes exactly these, and the divergence check
 * below is the same four read back.
 */
export interface ViewState {
  filters: Filters;
  groupBy: GroupBy;
  lanes: BoardLanes;
  shownFields: readonly string[];
}

/** The parts of a view a person can diverge from, and what to call them. */
export const DIVERGENCE_LABELS = {
  filters: "filters",
  grouping: "grouping",
  lanes: "lanes",
  fields: "shown fields",
} as const;

export type DivergenceKey = keyof typeof DIVERGENCE_LABELS;

export interface ViewDivergence {
  /** True when applying the saved view again would change the board. */
  dirty: boolean;
  /** Which parts differ, in `DIVERGENCE_LABELS` order. Empty when clean. */
  changed: DivergenceKey[];
}

/** A CSV filter value is a SET on the wire (`split_csv`), so order is noise. */
const sortedCsv = (value: unknown): string =>
  typeof value === "string"
    ? value.split(",").map((s) => s.trim()).filter(Boolean).sort().join(",")
    : "";

/**
 * A view state → four comparable strings, one per part.
 *
 * **The config round trip is the single fact.** Both sides are pushed through
 * `toConfig`, and the saved side through `fromConfig` first — so a view stored
 * by an older client, or hand-edited into a shape `fromConfig` normalises
 * (a sub-axis equal to the main axis, an unknown field key, `"false"` where a
 * boolean belongs), compares by what it MEANS rather than by its bytes. A
 * byte comparison would light the dirty affordance on a board nobody had
 * touched, which is the failure that makes such an affordance ignored.
 */
function comparable(state: ViewState): Record<DivergenceKey, string> {
  const config = toConfig(
    state.filters,
    state.groupBy,
    state.lanes,
    state.shownFields,
  );
  const stored = (config.filters ?? {}) as Record<string, unknown>;
  return {
    filters: JSON.stringify([
      typeof stored.q === "string" ? stored.q : "",
      typeof stored.status_category === "string" ? stored.status_category : "",
      typeof stored.assignee === "string" ? stored.assignee.toLowerCase() : "",
      stored.unassigned === true,
      stored.overdue === true,
      sortedCsv(stored.tags),
    ]),
    grouping: JSON.stringify([config.group_by, config.sub_group_by ?? "none"]),
    lanes: JSON.stringify([
      [...((config.collapsed_lanes as string[] | undefined) ?? [])].sort(),
      config.show_empty_lanes === true,
    ]),
    // Absent means the default SET (`fromConfig` says why), and the set is
    // compared sorted because `shown_fields` is a set, not a sequence.
    fields: JSON.stringify(
      [...((config.shown_fields as string[] | undefined) ?? DEFAULT_SHOWN)].sort(),
    ),
  };
}

/**
 * How the board differs from the view it was applied from.
 *
 * ONE function, so no surface re-derives "is this dirty" from a comparison of
 * its own — three near-identical comparisons in three components is how the
 * Update button comes to disagree with the badge beside it.
 *
 * `saved` is a raw `pm_views.config` (or `null`/anything else, which reads as
 * an empty config, i.e. the default board).
 *
 * Fence: `grouping.test.ts` — "divergence".
 */
export function viewDivergence(live: ViewState, saved: unknown): ViewDivergence {
  const restored = fromConfig(saved);
  const before = comparable({
    filters: restored.filters,
    groupBy: restored.groupBy,
    lanes: restored.lanes,
    shownFields: restored.shownFields,
  });
  const after = comparable(live);
  const changed = (Object.keys(DIVERGENCE_LABELS) as DivergenceKey[]).filter(
    (key) => before[key] !== after[key],
  );
  return { dirty: changed.length > 0, changed };
}

/** "filters and shown fields" — what the affordance says has moved. */
export function describeDivergence(changed: readonly DivergenceKey[]): string {
  const words = changed.map((key) => DIVERGENCE_LABELS[key]);
  if (words.length === 0) return "";
  if (words.length === 1) return words[0];
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

const localPart = (address: string): string => address.split("@")[0] || address;

/** How an assignee reads on a card: `priya`, or `builder` for an agent. */
export function personLabel(who: string): string {
  if (who.startsWith("agent:")) return who.slice("agent:".length) || who;
  return localPart(who);
}

const IMPORTANCE_LABELS: Record<string, string> = {
  "3": "Urgent",
  "2": "High",
  "1": "Normal",
  "0": "Low",
};

/**
 * Split tasks into groups.
 *
 * **A task with two assignees appears in both their columns.** That is the
 * honest rendering: it IS both people's work, and picking one arbitrarily would
 * hide it from the other. The total across groups can therefore exceed the task
 * count, which is why the header counts tasks and not group sizes.
 *
 * **Empty status lanes are kept**, because a board with a missing "In progress"
 * column reads as "this project has no in-progress state" rather than "nothing
 * is in progress". Every other grouping drops empties — there is no meaningful
 * "assignees with nothing assigned" column.
 */
export function groupTasks(
  tasks: TaskRow[],
  by: GroupBy,
  ctx: { statuses: StatusRow[]; projectName?: (id: string) => string },
): TaskGroup[] {
  if (by === "none") {
    return [{ key: "all", label: `${tasks.length} tasks`, tasks }];
  }

  if (by === "status") {
    const ordered = [...ctx.statuses].sort((a, b) => a.position - b.position);
    return ordered.map((status) => ({
      key: status.id,
      label: status.name,
      tasks: tasks.filter((t) => t.status_id === status.id),
    }));
  }

  const buckets = new Map<string, TaskGroup>();
  const put = (key: string, label: string, task: TaskRow) => {
    const existing = buckets.get(key);
    if (existing) existing.tasks.push(task);
    else buckets.set(key, { key, label, tasks: [task] });
  };

  for (const task of tasks) {
    if (by === "assignee") {
      const people = task.assignees ?? [];
      if (people.length === 0) put(UNSET, "Unassigned", task);
      else for (const who of people) put(who, personLabel(who), task);
    } else if (by === "tag") {
      // A task with three tags appears in three columns, for the same reason a
      // task with two assignees appears in both theirs: it genuinely belongs to
      // each, and picking one hides it from the others.
      const names = task.tags ?? [];
      if (names.length === 0) put(UNSET, "Untagged", task);
      else for (const name of names) put(name, name, task);
    } else if (by === "project") {
      const id = task.project_id;
      put(id, ctx.projectName?.(id) ?? "Project", task);
    } else {
      const value = task.importance;
      const key = value === null || value === undefined ? UNSET : String(value);
      put(key, key === UNSET ? "No priority" : IMPORTANCE_LABELS[key] ?? key, task);
    }
  }

  // Unassigned / no-priority last: it is a residue, not a peer of the named
  // buckets, and putting it first pushes the real work down the page.
  return [...buckets.values()].sort((a, b) => {
    if (a.key === UNSET) return 1;
    if (b.key === UNSET) return -1;
    return a.label.localeCompare(b.label);
  });
}
