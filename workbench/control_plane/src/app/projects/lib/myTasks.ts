/**
 * Projects · My tasks, the roll-up space (WS-64 OP-11, `one_projects_app.md`
 * §9 OP-11).
 *
 * My tasks is a virtual node. It is not a project and it is not stored. It
 * reads `GET /projects/tasks?scope=mine&with_overlay=1`, the calendar with the
 * same scope, `GET /projects/my/summary` and the Analytics reads with
 * `subject=me`. This file holds its pure rules, so the page and the component
 * stay thin:
 *
 * 1. **The chips.** All, New, Today, Next, Waiting, Someday and Done. Each chip is
 *    a filter on the row's overlay, with the rules of the server's
 *    `mine.fold_dispositions`. The counts on the chips come from
 *    `by_disposition`, never from a page of rows.
 * 2. **The old ids.** `my-today` and the four other list ids open My tasks
 *    with a chip (`LEGACY_CHIP`).
 * 3. **The board.** Statuses differ by root, so the board groups by STAGE. A
 *    drop writes the first status of that stage in the task's OWN project
 *    (`landingLane`, D79). There is no second resolver here.
 * 4. **The project chip.** Each row comes from some project, so each row says
 *    which one, with a path that truncates in the middle.
 *
 * Fence: `myTasks.test.ts`.
 */

import { inTodayChip } from "@/lib/shell/todayRule";
import { STATUS_CATEGORIES, categoryLabel, landingLane } from "@/lib/statusCategory";

import type { NodeSummary, StatusRow, TaskRow, ThroughputReport } from "./api";
import type { PositionWrite } from "./board";
import type { TaskGroup } from "./grouping";

// ── The tabs ────────────────────────────────────────────────────────────────

/**
 * The tabs of My tasks: a project's canvases (`commands.VIEW_MODES`), with the
 * same ids and icons. The Timeline is not here yet (slice 4). Declared here,
 * not read from `commands.ts`, so this leaf imports no nav module.
 * `myTasks.test.ts` holds the two lists equal.
 */
export const MY_TASKS_MODES = [
  { id: "overview", icon: "LayoutDashboard" },
  { id: "board", icon: "Kanban" },
  { id: "list", icon: "List" },
  { id: "table", icon: "Table" },
  { id: "calendar", icon: "Calendar" },
] as const;

export type MyTasksMode = (typeof MY_TASKS_MODES)[number]["id"];

/**
 * The My tasks tab a project canvas id names, or null when there is none (the
 * Timeline). A mode command of the palette lands here while My tasks is open
 * (MOB-1, phone audit 2026-11-03).
 */
export function myTasksModeFor(mode: string): MyTasksMode | null {
  return MY_TASKS_MODES.find((entry) => entry.id === mode)?.id ?? null;
}

/**
 * PJP-13 — the tab My tasks opens on. A member who picked a tab keeps it. With
 * no pick, a desktop opens the Board and a phone opens the List, the same rule
 * the project boards use. `chosen` is null until the member picks.
 */
export function myTasksMode(chosen: MyTasksMode | null, isMobile: boolean): MyTasksMode {
  return chosen ?? (isMobile ? "list" : "board");
}

/** PJP-13 — the session key that keeps a picked tab across a remount (the page remounts when the viewport crosses the phone line). */
export const MY_TASKS_PICK_KEY = "cc-my-tasks-picked-mode";

interface PickStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** The tab the member picked this session, or null. Never throws. */
export function readPickedMode(store: PickStore | null | undefined): MyTasksMode | null {
  try {
    const raw = store?.getItem(MY_TASKS_PICK_KEY);
    return raw ? myTasksModeFor(raw) : null;
  } catch {
    return null;
  }
}

/** Keeps the picked tab for the session. Never throws. */
export function writePickedMode(store: PickStore | null | undefined, mode: MyTasksMode): void {
  try {
    store?.setItem(MY_TASKS_PICK_KEY, mode);
  } catch {
    /* storage is off: the pick then lasts until the next remount */
  }
}

/** What the page holds for My tasks. `chosen` is the explicit tab pick, null until the member picks one. */
export interface MyTasksRaw {
  attention: boolean;
  chip: MyTasksChip;
  chosen: MyTasksMode | null;
}

/**
 * PJP-13 — one update of the My tasks view. `pick` is true only for an
 * explicit tab choice (a tab click, the mode command, a link that opens List,
 * the phone tab). It records `chosen` even when it equals the default. A chip
 * change, or an echo of the shown view, leaves `chosen` as it was.
 */
export function applyMyTasksUpdate(
  raw: MyTasksRaw,
  isMobile: boolean,
  update: { mode: MyTasksMode; attention: boolean; chip: MyTasksChip } | ((v: { mode: MyTasksMode; attention: boolean; chip: MyTasksChip }) => { mode: MyTasksMode; attention: boolean; chip: MyTasksChip }),
  pick: boolean,
): MyTasksRaw {
  const current = { mode: myTasksMode(raw.chosen, isMobile), attention: raw.attention, chip: raw.chip };
  const next = typeof update === "function" ? update(current) : update;
  return { attention: next.attention, chip: next.chip, chosen: pick ? next.mode : raw.chosen };
}

/** PJP-12a — the text of an empty My tasks, chip All, nothing hidden by a filter. */
export const MY_TASKS_EMPTY_TEXT = "It's empty. Go to Projects and pick up tasks.";

/** PJP-12a — true when My tasks is truly empty: chip All, read done, no row, no filter. */
export function myTasksTrulyEmpty(opts: {
  chip: MyTasksChip;
  loaded: boolean;
  total: number;
  filtered: boolean;
}): boolean {
  return opts.chip === "all" && opts.loaded && opts.total === 0 && !opts.filtered;
}

// ── The chips ───────────────────────────────────────────────────────────────

export const MY_TASKS_CHIPS = [
  { id: "all", label: "All" },
  // D170 (INB-1, 2026-11-03): work assigned to me that I have not opened or
  // planned. The inbox holds captures only, so this is where it shows.
  { id: "new", label: "New" },
  { id: "today", label: "Today" },
  { id: "next", label: "Next" },
  { id: "waiting", label: "Waiting" },
  { id: "someday", label: "Someday" },
  { id: "done", label: "Done" },
] as const;

export type MyTasksChip = (typeof MY_TASKS_CHIPS)[number]["id"];

const CHIP_IDS: ReadonlySet<string> = new Set(MY_TASKS_CHIPS.map((c) => c.id));

/** A raw `?chip=` value as a chip. Anything else is All. */
export function asChip(raw: string | null | undefined): MyTasksChip {
  const value = String(raw ?? "").trim().toLowerCase();
  return CHIP_IDS.has(value) ? (value as MyTasksChip) : "all";
}

/**
 * The five old list ids and the chip each one opens. They were the rows of the
 * My work section until OP-11. A feed link, a redirect, the phone tab and a
 * chat card can still name them.
 */
export const LEGACY_CHIP: Readonly<Record<string, MyTasksChip>> = {
  "my-today": "today",
  "my-next": "next",
  "my-waiting": "waiting",
  "my-someday": "someday",
  "my-done": "done",
};

/** The chip of an app id, or null when the id names none. */
export function chipForApp(id: string | null | undefined): MyTasksChip | null {
  const raw = String(id ?? "").trim().toLowerCase();
  return LEGACY_CHIP[raw] ?? null;
}

/** The overlay facts a chip reads off a row (`with_overlay=1`). */
export type ChipRow = Partial<Pick<TaskRow, "id">> & Pick<
  TaskRow,
  | "disposition"
  | "is_mine"
  | "is_new"
  | "completed_at"
  | "archived_at"
  | "defer_until"
  | "start_date"
  | "due_at"
>;

/**
 * Is the row in the chip? The rules of `mine.fold_dispositions`, on one row:
 *
 * - Next: the effective disposition is NEXT, and the task is assigned to me.
 * - Waiting, Someday and Done: the effective disposition.
 * - Today: `isInToday`, the old Today rule (`tasks/lib/today.ts`), which the
 *   server's `in_today` mirrors. With `TODAY_RULE_ORGS` on, the summary sends
 *   `today_ids`, the set of the one Today predicate (WS-MDC CC-2), and the
 *   chip lists that set (`todayRule.ts` `inTodayChip`).
 * - New: the server's `is_new` (`mine.new_work`), no stated disposition of mine.
 * - All: every row but TRASH. TRASH is in no chip.
 *
 * `disposition` arrives EFFECTIVE (`effective_disposition`, D77), so a closed
 * lane already reads DONE here.
 */
export function inChip(
  row: ChipRow,
  chip: MyTasksChip,
  today: string,
  timeZone?: string,
  todayIds?: ReadonlySet<string> | null,
): boolean {
  const effective = String(row.disposition ?? "");
  switch (chip) {
    case "all":
      return effective !== "TRASH";
    case "new":
      return Boolean(row.is_new);
    case "next":
      return effective === "NEXT" && Boolean(row.is_mine);
    case "waiting":
      return effective === "WAITING";
    case "someday":
      return effective === "SOMEDAY";
    case "done":
      return effective === "DONE";
    case "today":
      return inTodayChip(
        {
          id: row.id ?? "",
          disposition: effective as never,
          isMine: Boolean(row.is_mine),
          archivedAt: row.archived_at ?? undefined,
          completedAt: row.completed_at ?? undefined,
          dueAt: row.due_at ?? undefined,
          startDate: row.start_date ?? undefined,
          deferUntil: row.defer_until ?? undefined,
        },
        { today, timeZone },
        todayIds,
      );
  }
}

/** The rows of one chip, in the order they came. */
export function chipRows<T extends ChipRow>(
  rows: readonly T[],
  chip: MyTasksChip,
  today: string,
  timeZone?: string,
  todayIds?: ReadonlySet<string> | null,
): T[] {
  return rows.filter((row) => inChip(row, chip, today, timeZone, todayIds));
}

/**
 * The rows of a chip, with the Today set (WS-MDC CC-2, fix round 1 item 1).
 *
 * The Today set is not a subset of mine: a mark, a block or the One Thing on
 * a visible task of another member is part of my day. So with the flag on
 * (`todayIds`), the Today chip lists the rows of the mine list that the set
 * holds, and adds each task of the set that the list does not hold, from
 * `GET /projects/my/today?with_tasks=true` (`setRows`). The count, the rows
 * and the set are then one set. Any other chip, and the flag off, is
 * `chipRows` as before.
 */
export function chipRowsWithSet<T extends ChipRow & { id: string }>(
  rows: readonly T[],
  chip: MyTasksChip,
  today: string,
  timeZone: string | undefined,
  todayIds: ReadonlySet<string> | null,
  setRows: readonly T[] | null | undefined,
): T[] {
  const listed = chipRows(rows, chip, today, timeZone, todayIds);
  if (chip !== "today" || !todayIds || !setRows) return listed;
  const held = new Set(rows.map((row) => row.id));
  const added = setRows.filter(
    (row) => !held.has(row.id) && todayIds.has(row.id) && inChip(row, chip, today, timeZone, todayIds),
  );
  return [...listed, ...added];
}

/**
 * The ids of the Today set, from the summary, or null for the old rule.
 * `today_ids` comes only with `TODAY_RULE_ORGS` on (WS-MDC CC-2).
 */
export function todayIdsOf(
  summary: Pick<NodeSummary, "today_rule" | "today_ids"> | null | undefined,
): ReadonlySet<string> | null {
  if (!summary?.today_rule || !Array.isArray(summary.today_ids)) return null;
  return new Set(summary.today_ids.map(String));
}

/** The server count of a chip, or null when the summary has not said. */
export function chipCount(
  summary: Pick<NodeSummary, "by_disposition"> | null | undefined,
  chip: MyTasksChip,
): number | null {
  if (chip === "all") return null;
  const value = summary?.by_disposition?.[chip];
  return typeof value === "number" ? value : null;
}

/**
 * "Done this week" on the Overview: the completions of the CURRENT week of
 * Throughput (`subject=me`). The server's last bucket is the current week
 * when `current_week_partial` says so. Otherwise, or with no read, null.
 */
export function doneThisWeek(
  report: Pick<ThroughputReport, "series" | "current_week_partial"> | null | undefined,
): number | null {
  if (!report || !report.current_week_partial) return null;
  const last = report.series?.[report.series.length - 1];
  return typeof last?.completed === "number" ? last.completed : null;
}

/** The open tasks of a summary: every task that is not done or cancelled. */
export function openCount(summary: Pick<NodeSummary, "tasks" | "by_category">): number {
  const by = summary.by_category ?? {};
  return Math.max(0, (summary.tasks ?? 0) - (by.done ?? 0) - (by.cancelled ?? 0));
}

// ── The address ─────────────────────────────────────────────────────────────

/**
 * The address of My tasks with a chip: `/projects?app=my-tasks&chip=next`.
 * Every other key of `search` stays, in its order. All writes no `chip`.
 */
export function myTasksHref(search: string, chip: MyTasksChip): string {
  const params = new URLSearchParams(search);
  params.delete("app");
  params.delete("chip");
  const head = chip === "all" ? "app=my-tasks" : `app=my-tasks&chip=${chip}`;
  const tail = params.toString();
  return `/projects?${tail ? `${head}&${tail}` : head}`;
}

/** The address with My tasks taken out, for a member who left it. */
export function withoutMyTasks(search: string): string {
  const params = new URLSearchParams(search);
  params.delete("chip");
  if (params.get("app") === "my-tasks") params.delete("app");
  const qs = params.toString();
  return qs ? `/projects?${qs}` : "/projects";
}

/**
 * What the phone's "My tasks" tab does (`phoneTabs.ts`). Away from My tasks
 * it opens My tasks. On My tasks it opens the Projects sheet, so the tree
 * stays one tap away.
 */
export function phoneMyTasksTap(app: string | null | undefined): "open" | "sheet" {
  return app === "my-tasks" ? "sheet" : "open";
}

// ── The board ───────────────────────────────────────────────────────────────

/** The columns the board always draws, as `one_projects_app.md` names them. */
export const BOARD_STAGES: readonly string[] = ["backlog", "todo", "in_progress", "done"];

/** The stage of a row: the server's `status_category`, else the status set's. */
export function stageOf(
  row: Pick<TaskRow, "status_id" | "status_category">,
  known?: ReadonlyMap<string, Pick<StatusRow, "category">>,
): string | null {
  return row.status_category ?? known?.get(row.status_id)?.category ?? null;
}

/**
 * The status rows the canvases read for rows of many projects.
 *
 * Each status of a loaded row takes one entry. A status the client holds
 * keeps its name and colour. Any other one takes the stage's label, so a row
 * from a project the member cannot open still lands in its stage. The board
 * reads `category` from these to know a drop inside one stage is a reorder.
 */
export function mineStatuses(
  rows: readonly Pick<TaskRow, "status_id" | "status_category" | "project_id">[],
  known: readonly StatusRow[],
): StatusRow[] {
  const byId = new Map(known.map((s) => [s.id, s]));
  const out = new Map<string, StatusRow>();
  for (const row of rows) {
    if (!row.status_id || out.has(row.status_id)) continue;
    const hit = byId.get(row.status_id);
    if (hit) {
      out.set(hit.id, hit);
      continue;
    }
    const category = row.status_category ?? "todo";
    out.set(row.status_id, {
      id: row.status_id,
      project_id: row.project_id,
      name: categoryLabel(category),
      color: "",
      position: STATUS_CATEGORIES.indexOf(category as never),
      category,
      is_default: false,
    });
  }
  return [...out.values()];
}

/**
 * The board's columns: one per stage, in the stage order. The four stages of
 * `BOARD_STAGES` always show, as a project keeps its empty lanes. Triage and
 * Cancelled show only while a row sits there.
 */
export function stageGroups<T extends Pick<TaskRow, "status_id" | "status_category">>(
  rows: readonly T[],
  known?: ReadonlyMap<string, Pick<StatusRow, "category">>,
): (Omit<TaskGroup, "tasks"> & { tasks: T[] })[] {
  const present = new Set(rows.map((r) => stageOf(r, known)).filter(Boolean) as string[]);
  const stages = STATUS_CATEGORIES.filter((c) => BOARD_STAGES.includes(c) || present.has(c));
  const strays = [...present].filter((c) => !(STATUS_CATEGORIES as readonly string[]).includes(c)).sort();
  return [...stages, ...strays].map((stage) => ({
    key: stage,
    label: categoryLabel(stage),
    tasks: rows.filter((r) => stageOf(r, known) === stage),
  }));
}

/** What a drop on the board of My tasks writes, or why it writes nothing. */
export type StageDrop =
  | { kind: "write"; patch: { status_id: string }; stage: string }
  | { kind: "none" }
  | { kind: "refuse"; reason: string };

/**
 * The stage a board drop names: the `group_key` of the dragged task's
 * position write, which is the column key (`planDrop`).
 */
export function droppedStage(taskId: string, writes: readonly PositionWrite[]): string | null {
  const own = writes.find((w) => w.task_id === taskId) ?? writes[0];
  return own?.group_key ?? null;
}

/**
 * The status a drop into a stage writes. It is the FIRST status of that
 * stage in the task's OWN project (`landingLane`, D79 rule of
 * `lib/statusCategory.ts`), never the first of the merged set. A drop inside
 * the task's own stage writes nothing. A project with no status in that stage
 * refuses, and says so.
 */
export function stageDrop(
  task: Pick<TaskRow, "status_id" | "status_category">,
  stage: string | null,
  ownStatuses: readonly StatusRow[],
  /** True when the read of the project's statuses failed (no answer, not "none"). */
  readFailed = false,
): StageDrop {
  if (!stage) return { kind: "none" };
  const current = stageOf(task, new Map(ownStatuses.map((s) => [s.id, s])));
  if (current === stage) return { kind: "none" };
  if (readFailed) return { kind: "refuse", reason: STATUS_READ_FAILED };
  const lane = landingLane(ownStatuses, stage);
  if (!lane) {
    return {
      kind: "refuse",
      reason: `This task's project has no ${categoryLabel(stage)} status. Change it in the task.`,
    };
  }
  return { kind: "write", patch: { status_id: lane.id }, stage };
}

/** What a drop says when the statuses of the task's project could not be read. */
export const STATUS_READ_FAILED =
  "The statuses of this task's project could not be read just now. Try again.";

/** The statuses of one project, and whether the read of them failed. */
export interface OwnStatuses {
  rows: readonly StatusRow[];
  failed: boolean;
}

/** What one write of the My tasks board sends, or why it sends nothing. */
export type MineDrop =
  | { kind: "write"; patch: Record<string, string | number | null>; stage: string | null }
  | { kind: "none" }
  | { kind: "refuse"; reason: string };

/**
 * The ONE patch a write of the My tasks board sends. A drag, the Mark-done
 * tick and the Status menu all arrive here (`TaskBoard.onDrop`).
 *
 * - A drag (`writes` not empty) on the stage axis. The stage half is the
 *   landing lane of the TASK'S OWN project (`stageDrop`). The board's own
 *   `status_id` came from the merged set, so this never sends it. Every other
 *   key of the board's patch is the lane half, and it goes in the same PATCH.
 * - A tick or a menu pick (`writes` empty). The patch names a status of the
 *   card's own project (`statusesFor`), and it goes as it is.
 * - Any other axis. The board's patch, as a project board sends it.
 */
export function planMineDrop(
  task: Pick<TaskRow, "id" | "status_id" | "status_category">,
  writes: readonly PositionWrite[],
  patch: Record<string, string | number | null> | null,
  axis: string,
  own: OwnStatuses,
): MineDrop {
  if (axis !== "category") {
    return patch && Object.keys(patch).length
      ? { kind: "write", patch: { ...patch }, stage: null }
      : { kind: "none" };
  }
  const out: Record<string, string | number | null> = {};
  let stage: string | null = null;
  if (writes.length > 0) {
    const plan = stageDrop(task, droppedStage(task.id, writes), own.rows, own.failed);
    if (plan.kind === "refuse") return plan;
    if (plan.kind === "write") {
      out.status_id = plan.patch.status_id;
      stage = plan.stage;
    }
    for (const [key, value] of Object.entries(patch ?? {})) {
      if (key !== "status_id") out[key] = value;
    }
  } else if (patch) {
    Object.assign(out, patch);
    const picked = typeof patch.status_id === "string" ? patch.status_id : null;
    if (picked === task.status_id) delete out.status_id;
    else if (picked) stage = own.rows.find((s) => s.id === picked)?.category ?? null;
  }
  return Object.keys(out).length ? { kind: "write", patch: out, stage } : { kind: "none" };
}

// ── The project chip ────────────────────────────────────────────────────────

/**
 * The words of a project chip. Two levels or fewer show whole, joined by
 * " › ". A deeper path keeps its first and last names around an ellipsis, so
 * the space and the project both stay in sight. `title` is the whole path.
 */
export function projectChipText(path: readonly string[]): { label: string; title: string } | null {
  const names = path.map((n) => n.replace(/\s+/g, " ").trim()).filter(Boolean);
  if (names.length === 0) return null;
  const title = names.join(" › ");
  const label = names.length <= 2 ? title : `${names[0]} › … › ${names[names.length - 1]}`;
  return { label, title };
}

// ── The reads ───────────────────────────────────────────────────────────────

/** The rows of one page of My tasks. The largest page the server allows. */
export const MY_TASKS_PAGE = 100;

/** A chip reads every page, up to this many, because it filters on the client. */
export const MY_TASKS_MAX_PAGES = 20;

/** The most rows a chip other than All holds. The one cap, in one place. */
export const MY_TASKS_MAX_ROWS = MY_TASKS_PAGE * MY_TASKS_MAX_PAGES;

/**
 * The notice of a chip read that stopped at the cap, or null when it read
 * every row. A chip other than All filters on the client, so past the cap it
 * cannot say how many of the rest are in it. It says so in place.
 */
export function truncationNotice(loaded: number, total: number | null): string | null {
  if (total === null || total <= loaded) return null;
  const n = (v: number) => v.toLocaleString("en-US");
  return `Showing the first ${n(loaded)} of ${n(total)}. Narrow the filter or use All.`;
}

/**
 * Where a quick add of My tasks files: the member's personal root, or null
 * when the member has none yet. Never "", "mine" or "my-tasks", which no
 * server row has (My tasks is a virtual node).
 */
export function quickAddHome(personalRootId: string | null | undefined): string | null {
  const id = String(personalRootId ?? "").trim();
  if (!id || id === "mine" || id === "my-tasks") return null;
  return id;
}

/** The query of one page of the list over mine. */
export function myTasksQuery(
  filterQuery: Record<string, string>,
  page: number,
): Record<string, string | number> {
  return {
    ...filterQuery,
    scope: "mine",
    with_overlay: 1,
    page,
    page_size: MY_TASKS_PAGE,
  };
}

/** True when a chip filters, so the list must hold every page. */
export function chipNeedsEveryPage(chip: MyTasksChip): boolean {
  return chip !== "all";
}
