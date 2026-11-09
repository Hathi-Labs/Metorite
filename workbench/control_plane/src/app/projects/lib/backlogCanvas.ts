/**
 * Projects · the rules of the Backlog canvas (WS-39 SP-3a,
 * `project_management_app.md` §13.6 row SP-3a, D195).
 *
 * The canvas shows the planned cycles of a Sprints node at the top, each with
 * its task count, and the backlog below them: the open tasks of the node that
 * sit in no open cycle, in priority order. The member drags a task into a
 * cycle and back, or picks "Move to sprint" for one or many tasks. A move runs
 * at once and offers Undo (DESIGN_SYSTEM.md M5) through the one undo stack.
 *
 * Every rule lives here, because the vitest runner collects no `.tsx`:
 *
 * - which cycles show, in which order, and the words of their counts;
 * - the read of the backlog (`backlogQuery`), so the order is the server's;
 * - the writes of a move, and where the task is after a refusal;
 * - the Undo entry of a move;
 * - the phone rule: no drag, and "Move to sprint" opens a sheet;
 * - the probe that tells the page the flag is off (a 404).
 *
 * A move goes through the SP-1 routes only. A move from one cycle to another
 * is a remove, then an add. When the add fails, the task is in the backlog,
 * and the words say so.
 *
 * Pure: no React, no DOM and no fetch. The network is `MoveIo`.
 */

import { CLOSING_CATEGORIES, STATUS_CATEGORIES } from "@/lib/statusCategory";
import type { UndoEntry } from "@/lib/undo";

import type { CycleRow } from "./api";
import { registerUndoStep } from "./taskUndo";

// ── Places ──────────────────────────────────────────────────────────────────

/** Where a task sits on the canvas: the backlog, or one open cycle. */
export type Place = { kind: "backlog" } | { kind: "cycle"; id: string };

export const BACKLOG: Place = { kind: "backlog" };

/** A string identity, for state and for the drop highlight. */
export function placeKey(place: Place): string {
  return place.kind === "backlog" ? "backlog" : `cycle:${place.id}`;
}

export function samePlace(a: Place, b: Place): boolean {
  return placeKey(a) === placeKey(b);
}

export function cyclePlace(id: string): Place {
  return { kind: "cycle", id };
}

// ── The cycles at the top ───────────────────────────────────────────────────

/** The planned cycles, soonest start first, then by name. */
export function plannedCycles(rows: readonly CycleRow[]): CycleRow[] {
  return rows
    .filter((row) => row.state === "planned")
    .sort(
      (a, b) =>
        (a.starts_on ?? "").localeCompare(b.starts_on ?? "") || a.name.localeCompare(b.name),
    );
}

/** The cycle that runs now, if any. Its tasks are in no backlog. */
export function runningCycle(rows: readonly CycleRow[]): CycleRow | null {
  return rows.find((row) => row.state === "active") ?? null;
}

/**
 * The words of a count. A task that the reader cannot see is counted apart
 * (§13.7): "4 tasks, 2 hidden".
 */
export function countWords(visible: number, hidden = 0): string {
  const shown = visible === 0 ? "No tasks" : `${visible} task${visible === 1 ? "" : "s"}`;
  return hidden > 0 ? `${shown}, ${hidden} hidden` : shown;
}

/** The two dates of a cycle, as a short range: "10 Nov – 16 Nov". */
export function cycleDates(row: Pick<CycleRow, "starts_on" | "ends_on">): string {
  const day = (iso: string | null) => {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    if (!y || !m || !d) return iso;
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
    });
  };
  return [day(row.starts_on), day(row.ends_on)].filter(Boolean).join(" – ");
}

// ── The backlog read ────────────────────────────────────────────────────────

/** Every stage that does not close a task. Triage stays out on the server. */
export const OPEN_CATEGORIES: readonly string[] = STATUS_CATEGORIES.filter(
  (category) => !CLOSING_CATEGORIES.has(category),
);

/** One page of the backlog. The server caps a page at 100. */
export const BACKLOG_PAGE = 100;

/**
 * The query of the backlog, through the one task-list endpoint
 * (`GET /projects/tasks`). `cycle: "none"` leaves out each task that sits in
 * an open cycle. The order is the shared priority rank (`sort: importance`,
 * most pressing first), so the canvas draws the rows as they come. Only
 * top-level work: a story or a task joins a cycle, and a subtask goes with
 * its parent (§13.8).
 */
export function backlogQuery(nodeId: string, page = 1): Record<string, string | number | boolean> {
  return {
    project_id: nodeId,
    include_subtree: true,
    cycle: "none",
    status_category: OPEN_CATEGORIES.join(","),
    top_level: true,
    sort: "importance",
    direction: "desc",
    page,
    page_size: BACKLOG_PAGE,
  };
}

// ── The flag ────────────────────────────────────────────────────────────────

export type SprintsProbe = { on: true; cycles: CycleRow[] } | { on: false };

/**
 * Read the cycles of a node. A 404 means `PROJECTS_SPRINTS_ORGS` does not hold
 * the org (each cycle route answers 404 while it is off), so the page hides
 * the Backlog tab. Any other failure is thrown: the caller does not know, and
 * an unknown answer also hides the tab.
 */
export async function probeSprints(
  read: () => Promise<{ cycles: CycleRow[] }>,
): Promise<SprintsProbe> {
  try {
    const answer = await read();
    return { on: true, cycles: answer.cycles ?? [] };
  } catch (err) {
    if ((err as { status?: number } | null)?.status === 404) return { on: false };
    throw err;
  }
}

// ── A move ──────────────────────────────────────────────────────────────────

/** One write of a move, through the SP-1 routes. */
export type Step = { op: "add" | "remove"; cycleId: string };

/**
 * The writes that move a task from one place to another. A move from one
 * cycle to another is a remove, then an add (the default of SP-3a). The same
 * place writes nothing.
 */
export function moveSteps(from: Place, to: Place): Step[] {
  if (samePlace(from, to)) return [];
  const steps: Step[] = [];
  if (from.kind === "cycle") steps.push({ op: "remove", cycleId: from.id });
  if (to.kind === "cycle") steps.push({ op: "add", cycleId: to.id });
  return steps;
}

/** The network of a move. `projectsApi.addCycleTask` and `removeCycleTask`. */
export interface MoveIo {
  add: (cycleId: string, taskId: string) => Promise<unknown>;
  remove: (cycleId: string, taskId: string) => Promise<unknown>;
}

/** Where one task is after its move, and why when it did not get there. */
export type MoveResult =
  | { taskId: string; ok: true; at: Place }
  | { taskId: string; ok: false; at: Place; reason: string };

/** The words the member reads when an add fails after the remove worked. */
export const NOW_IN_BACKLOG = "The task is in the backlog now.";

/** The server's own sentence for a refusal, or a plain line. */
export function refusalText(err: unknown): string {
  const message = (err as { message?: unknown } | null)?.message;
  const text = typeof message === "string" ? message.trim() : "";
  if (text && text !== "[object Object]" && text.length <= 300) return text;
  return "The move did not go through. Try again.";
}

/**
 * Move one task. It runs the writes in order and stops at the first refusal.
 * A refused first write leaves the task where it was. A refused add after a
 * remove leaves it in the backlog, and the reason says so.
 */
export async function moveOne(
  io: MoveIo,
  taskId: string,
  from: Place,
  to: Place,
): Promise<MoveResult> {
  let at = from;
  for (const step of moveSteps(from, to)) {
    try {
      if (step.op === "remove") {
        await io.remove(step.cycleId, taskId);
        at = BACKLOG;
      } else {
        await io.add(step.cycleId, taskId);
        at = cyclePlace(step.cycleId);
      }
    } catch (err) {
      const reason = refusalText(err);
      return {
        taskId,
        ok: false,
        at,
        reason: samePlace(at, from) || at.kind !== "backlog" ? reason : `${reason} ${NOW_IN_BACKLOG}`,
      };
    }
  }
  return { taskId, ok: true, at };
}

/** One task to move, and where it sits now. */
export interface MoveItem {
  taskId: string;
  from: Place;
}

/** Move many tasks to one place, one after another, in the given order. */
export async function moveMany(io: MoveIo, items: readonly MoveItem[], to: Place): Promise<MoveResult[]> {
  const out: MoveResult[] = [];
  for (const item of items) out.push(await moveOne(io, item.taskId, item.from, to));
  return out;
}

/**
 * Where each task stands now: `places` with the results laid over it. The
 * canvas draws from this, so a refused move leaves the task where the server
 * has it.
 */
export function applyResults(
  places: ReadonlyMap<string, Place>,
  results: readonly MoveResult[],
): Map<string, Place> {
  const next = new Map(places);
  for (const result of results) next.set(result.taskId, result.at);
  return next;
}

// ── The words of a move ─────────────────────────────────────────────────────

/** "Moved 1 task to Sprint 3", "Moved 3 tasks to the backlog". */
export function moveTitle(count: number, toName: string | null): string {
  const what = `${count} task${count === 1 ? "" : "s"}`;
  return `Moved ${what} to ${toName ?? "the backlog"}`;
}

/** The label of an Undo entry: past tense, lower case (`lib/undo.ts`). */
export function moveLabel(count: number, toName: string | null): string {
  const title = moveTitle(count, toName);
  return title.charAt(0).toLowerCase() + title.slice(1);
}

/**
 * The words of the refusals of a batch: the first reason, and how many more
 * did not move. Null when every task moved.
 */
export function refusalSummary(results: readonly MoveResult[]): string | null {
  const refused = results.filter((r): r is Extract<MoveResult, { ok: false }> => !r.ok);
  if (refused.length === 0) return null;
  const first = refused[0].reason;
  return refused.length === 1 ? first : `${first} (${refused.length} tasks did not move.)`;
}

// ── Undo ────────────────────────────────────────────────────────────────────

export interface MoveHooks {
  record: (entry: UndoEntry) => void;
  /** One short line to the member. */
  notify: (message: string) => void;
  /** Re-read the canvas after an undo or a redo. */
  after: () => Promise<void> | void;
  /** Each task's place after an undo or a redo, so the canvas draws it there. */
  placed?: (results: readonly MoveResult[]) => void;
}

/**
 * Record the moves that worked as one Undo entry. Undo sends each task back to
 * where it was before the move. Redo sends each one to `to` again. A refusal
 * says its reason once. An entry whose every write was refused is dead, so a
 * later Ctrl+Z on it does nothing.
 */
export function recordMove(
  io: MoveIo,
  hooks: MoveHooks,
  moved: readonly MoveItem[],
  to: Place,
  label: string,
): UndoEntry {
  const where = new Map<string, Place>(moved.map((m) => [m.taskId, to]));
  const send = async (target: (item: MoveItem) => Place): Promise<boolean> => {
    const results: MoveResult[] = [];
    for (const item of moved) {
      const now = where.get(item.taskId) ?? to;
      const result = await moveOne(io, item.taskId, now, target(item));
      where.set(item.taskId, result.at);
      results.push(result);
    }
    hooks.placed?.(results);
    const words = refusalSummary(results);
    if (words) hooks.notify(words);
    return results.some((r) => r.ok);
  };
  return registerUndoStep(
    { record: hooks.record, notify: hooks.notify, after: hooks.after },
    {
      label,
      revert: () => send((item) => item.from),
      reapply: () => send(() => to),
    },
  );
}

// ── The phone rule, and the targets of "Move to sprint" ─────────────────────

/**
 * On a phone a task does not drag: touch has no drag of the page, and a long
 * press is the board's menu. "Move to sprint" opens a sheet there. A desktop
 * drags, and the same control opens a menu under the button.
 */
export function moveSurface(phone: boolean): { drag: boolean; sheet: boolean } {
  return phone ? { drag: false, sheet: true } : { drag: true, sheet: false };
}

export interface MoveTarget {
  place: Place;
  label: string;
  /** True when every task to move is there already. */
  here: boolean;
}

/** The targets of "Move to sprint": each planned cycle, then the backlog. */
export function moveTargets(cycles: readonly CycleRow[], from: readonly Place[]): MoveTarget[] {
  const allAt = (place: Place) => from.length > 0 && from.every((p) => samePlace(p, place));
  return [
    ...plannedCycles(cycles).map((row) => ({
      place: cyclePlace(row.id),
      label: row.name,
      here: allAt(cyclePlace(row.id)),
    })),
    { place: BACKLOG, label: "Backlog", here: allAt(BACKLOG) },
  ];
}

// ── Plan a sprint ───────────────────────────────────────────────────────────

/** The longest cycle and the longest name, as the server holds them. */
export const PLAN_MAX_DAYS = 366;
export const PLAN_NAME_MAX = 120;

const isoDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/**
 * The form of a new sprint: the next number, today, and one week (§13.2: the
 * default length is one week).
 */
export function planDefaults(today: Date, existing: number): { name: string; starts_on: string; ends_on: string } {
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 6);
  return { name: `Sprint ${existing + 1}`, starts_on: isoDay(today), ends_on: isoDay(end) };
}

/** Why the form cannot be sent, or null. The server checks the same rules. */
export function planProblem(plan: { name: string; starts_on: string; ends_on: string }): string | null {
  const name = plan.name.trim();
  if (!name) return "Write a name for the sprint.";
  if (name.length > PLAN_NAME_MAX) return `A name has at most ${PLAN_NAME_MAX} characters.`;
  const start = Date.parse(`${plan.starts_on}T00:00:00Z`);
  const end = Date.parse(`${plan.ends_on}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return "Pick a start date and an end date.";
  if (end < start) return "The sprint ends before it starts. Pick a later end date.";
  if ((end - start) / 86_400_000 > PLAN_MAX_DAYS) return `A sprint lasts at most ${PLAN_MAX_DAYS} days.`;
  return null;
}
