/**
 * Projects · board and ordering maths.
 *
 * Spec: `project-docs/specs/project_management_app.md` §3.10, §5 · D-PM-5.
 *
 * Pure functions only, so the arithmetic that decides where a dragged card
 * lands is testable without a DOM. Everything here is the client half of the
 * per-view ordering decision: positions are floats in `pm_view_task_positions`,
 * there is no rank column on the task, and one drop writes exactly one row.
 */

import { UNSET } from "./grouping";
import { type Placeable, landingLane } from "./statusOrder";
import { taskCell } from "./matrix";

export interface PositionedTask {
  id: string;
  /** The task's position in THIS view, or null when it has never been dragged. */
  view_position?: number | null;
  view_group_key?: string | null;
  created_at?: string | null;
}

/**
 * The ceiling for a fractional index.
 *
 * `Number.MAX_SAFE_INTEGER` (Paca's choice, and its reason): appending is
 * `(prev + MAX) / 2` and prepending is `next / 2`, so a position can never
 * reach zero and never overflow — negative positions and overflow are
 * impossible by construction rather than by validation.
 */
export const POSITION_MAX = Number.MAX_SAFE_INTEGER;

/**
 * The position for a card dropped between `prev` and `next`.
 *
 * Halving the gap means one write per drag and no renumbering of neighbours.
 * ~52 halvings exhaust a float's precision, which is far past any real board,
 * so renormalisation is deliberately not implemented — the same call Paca
 * documented and left alone.
 */
export function positionBetween(
  prev: number | null | undefined,
  next: number | null | undefined
): number {
  const before = typeof prev === "number" ? prev : null;
  const after = typeof next === "number" ? next : null;
  if (before === null && after === null) return POSITION_MAX / 2;
  if (before === null) return (after as number) / 2;
  if (after === null) return (before + POSITION_MAX) / 2;
  return (before + after) / 2;
}

/**
 * Order tasks for a view: positioned first by position, then the rest by age.
 *
 * Unpositioned tasks sort to the BOTTOM rather than the top. A newly created
 * task appearing at the top of a hand-arranged column would look like the board
 * had reordered itself.
 */
export function sortForView<T extends PositionedTask>(tasks: readonly T[]): T[] {
  const positioned = tasks.filter((t) => typeof t.view_position === "number");
  const rest = tasks.filter((t) => typeof t.view_position !== "number");
  positioned.sort((a, b) => (a.view_position as number) - (b.view_position as number));
  rest.sort((a, b) => String(a.created_at ?? "").localeCompare(String(b.created_at ?? "")));
  return [...positioned, ...rest];
}

export interface PositionWrite {
  task_id: string;
  position: number;
  group_key?: string | null;
}

/**
 * The rows one drop must write.
 *
 * Normally exactly one. The exception is the first drag into a group nobody has
 * ordered yet: the neighbours have no positions to interpolate between, so the
 * whole group is **materialised** in the same request. Doing it lazily — or as
 * N requests — is how a hand-arranged order half-applies and then looks random.
 */
export function planDrop<T extends PositionedTask>(
  ordered: readonly T[],
  taskId: string,
  toIndex: number,
  groupKey?: string | null
): PositionWrite[] {
  const others = ordered.filter((t) => t.id !== taskId);
  const index = Math.max(0, Math.min(toIndex, others.length));

  const needsMaterialising = others.some(
    (t) => typeof t.view_position !== "number"
  );
  if (needsMaterialising) {
    const finalOrder = [
      ...others.slice(0, index).map((t) => t.id),
      taskId,
      ...others.slice(index).map((t) => t.id),
    ];
    const step = POSITION_MAX / (finalOrder.length + 1);
    return finalOrder.map((id, i) => ({
      task_id: id,
      position: step * (i + 1),
      group_key: groupKey ?? null,
    }));
  }

  return [
    {
      task_id: taskId,
      position: positionBetween(
        others[index - 1]?.view_position ?? null,
        others[index]?.view_position ?? null
      ),
      group_key: groupKey ?? null,
    },
  ];
}

/**
 * Where a saved view is positioned (WS-27k).
 *
 * Above the two views `tree.py` seeds on every project — "All tasks" at 100 and
 * "Board" at 200 — because the server returns views ordered by position, and
 * `orderBearingView` takes the first board it sees. A view saved below 200
 * would quietly become the one every drag writes its order into.
 */
export const SAVED_VIEW_POSITION = 300;

/**
 * The view whose `pm_view_task_positions` rows the board's drags write.
 *
 * There is no `is_default` column, so "the board" is the first `board` view the
 * server returned — and the server orders by position, which is why saved views
 * sit above the seeded one. Naming this once matters because two places need
 * the same answer: the drag handler writes to it, and the delete button must
 * refuse it. Deleting it CASCADEs every hand-arranged position on the project,
 * which is exactly the silent loss `delete_view` warns about.
 */
export function orderBearingView<T extends { view_type: string }>(
  views: readonly T[]
): T | null {
  return views.find((view) => view.view_type === "board") ?? null;
}

/**
 * The field patch a cross-column drag implies.
 *
 * Board columns come from the view's `column_by`, so dropping a card into a
 * column sets **whatever field the view groups by** — not `status` specifically.
 * Hard-coding status is what makes a board that can only ever group by status.
 */
export function buildColumnDropUpdate(
  columnBy: string | null | undefined,
  groupKey: string | null,
  /**
   * The project's lanes — required ONLY for the `category` axis, which names a
   * stage and must resolve it to a concrete `status_id`.
   *
   * Optional so every existing caller keeps its two-argument shape. A
   * `category` drop with no lanes to resolve against patches nothing, which is
   * the same thing an unknown axis does.
   */
  statuses?: readonly Placeable[]
): Record<string, string | number | null> | null {
  switch (columnBy) {
    case "status":
      return { status_id: groupKey };
    case "category": {
      // A stage is not a field. `pm_tasks.status_id` is NOT NULL, so the drop
      // has to choose a lane, and `landingLane` owns that rule — see its
      // docstring for why it is NOT `is_default`.
      if (!groupKey || !statuses) return null;
      const lane = landingLane(statuses, groupKey);
      return lane ? { status_id: lane.id } : null;
    }
    case "type":
      return { type_id: groupKey };
    case "project":
      return { project_id: groupKey };
    default:
      // An unknown grouping is not an error — the drop still reorders within
      // the column; it simply patches no field.
      return null;
  }
}

/**
 * WS-27y — why a drop into this target is refused, or `null` when it is not.
 *
 * The axes a drop can WRITE are the single-valued plain-PATCH fields: status
 * and priority (and type, which `buildColumnDropUpdate` already speaks). The
 * refusals are the reasons the old board silently disabled dragging for —
 * said out loud, on the target, while the card hovers over it:
 *
 * - assignee/tag are many-valued. A task with two owners is drawn in both
 *   their columns; dropping it into a third cannot know which of the two it
 *   should replace, so the drop cannot honestly mean anything.
 * - project crosses a grant boundary (R5) — the move is real but deliberate,
 *   and belongs in the task panel, not at the end of a flick.
 * - "none" refuses nothing: one column, so a drop is a pure reorder.
 *
 * A dual-axis target (a lane cell) is refused if EITHER axis is — writing
 * half of what the cell means would file the card somewhere it is not.
 */
export function dropRefusal(
  columnBy: string | null | undefined,
  laneBy?: string | null,
  task?: Pick<PositionedTask, "id"> & { archived_at?: string | null }
): string | null {
  // A task the server has archived is read-only history; the board should
  // never be dragging one, but a stale row is not impossible.
  if (task?.archived_at) return "This task is archived — unarchive it first.";

  const REASONS: Record<string, string> = {
    assignee:
      "Assignees are many-valued — a drop can't know which one to replace. Edit them on the task.",
    tag: "Tags are many-valued — edit them on the task instead.",
    project:
      "Moving between projects crosses a grant boundary — use the task panel.",
    // D78 — a level is computed from Important, Leveraged AND the due date,
    // so a drop cannot promise the card lands in the level it was dropped on.
    importance:
      "A priority level is worked out from Important, Leveraged and the due date — set them on the task.",
  };
  const WRITABLE = new Set(["status", "category", "type", "none"]);

  for (const axis of [columnBy, laneBy]) {
    if (!axis || axis === "none" || WRITABLE.has(axis)) continue;
    return REASONS[axis] ?? `Grouping by ${axis} isn't a field a drop can set.`;
  }
  return null;
}

/** The bucket key a task currently occupies on an axis — `UNSET` sentinel and
 *  all, so it compares directly against a column or lane key. `null` means the
 *  axis has no single current value (many-valued, or unknown). */
export function currentAxisKey(
  task: {
    status_id?: string | null;
    type_id?: string | null;
    project_id?: string | null;
    importance?: number | null;
    leveraged?: boolean | null;
    due_at?: string | null;
  },
  axis: string | null | undefined,
  /** Lanes, for the `category` axis alone — a stage is read THROUGH the lane. */
  statuses?: readonly Placeable[]
): string | null {
  switch (axis) {
    case "status":
      return task.status_id ?? null;
    case "category": {
      // ⚠️ Without this a same-stage drop is not recognised as a no-op, and the
      // card is PATCHED onto the stage's first lane — so dragging a "Blocked"
      // card one inch inside its own column would silently demote it to
      // "In progress". The skip in `buildCellDropPatch` is the only thing
      // standing between a reorder and a status write.
      if (!task.status_id || !statuses) return null;
      return statuses.find((s) => s.id === task.status_id)?.category ?? null;
    }
    case "type":
      return task.type_id ?? null;
    case "project":
      return task.project_id ?? null;
    case "importance":
      return taskCell(task);
    default:
      return null;
  }
}

/**
 * The one PATCH a drop into a (column, lane) cell implies — BOTH axes at once.
 *
 * Axes the task already satisfies are left out, so dropping a card elsewhere
 * in its own lane changes only the axis that actually moved, and a drop into
 * the very cell it came from patches nothing (`null`) — no activity row
 * saying a task moved to where it already was.
 *
 * Callers must have consulted `dropRefusal` first; an unwritable axis here
 * simply contributes nothing, which is the safe wrong answer.
 */
export function buildCellDropPatch(
  task: Parameters<typeof currentAxisKey>[0],
  columnBy: string | null | undefined,
  columnKey: string | null,
  laneBy?: string | null,
  laneKey?: string | null,
  /** Lanes, for the `category` axis alone — see `buildColumnDropUpdate`. */
  statuses?: readonly Placeable[]
): Record<string, string | number | null> | null {
  const patch: Record<string, string | number | null> = {};
  for (const [axis, key] of [
    [columnBy, columnKey],
    [laneBy, laneKey],
  ] as const) {
    if (!axis || key === undefined || key === null) continue;
    if (currentAxisKey(task, axis, statuses) === key) continue;
    Object.assign(patch, buildColumnDropUpdate(axis, key, statuses) ?? {});
  }
  return Object.keys(patch).length ? patch : null;
}
