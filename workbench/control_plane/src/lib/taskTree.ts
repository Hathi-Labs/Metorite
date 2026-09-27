/**
 * The subtask tree — one helper for every view that nests (D-PM-38).
 *
 * Moved here from `app/projects/lib/table.ts` in Subtasks S2, because the
 * table was its only reader and the list, the board and My Tasks will be the
 * next ones. One tree model means one answer to three questions that each view
 * would otherwise answer on its own: how deep a row sits, which subtask has no
 * parent in the set, and what a loop in the data does.
 *
 * Hierarchy is `parent_task_id` and nothing else, the self-FK the gateway
 * walks.
 *
 * **An orphan is shown, never dropped (D-PM-38 decision 3).** An orphan is a
 * subtask whose parent is not in this set: filtered out, on another page, in
 * another group, or hidden from the reader. It renders at the top level with
 * its own "↳ Parent" line. The filter said "show this task", and a view that
 * hid it because its parent did not qualify would lose rows without a word.
 *
 * **Cycle-safe and stack-safe.** The gateway refuses a parent cycle
 * (`assert_no_task_cycle`), but a stale page must degrade to missing
 * indentation, never to a hung tab or a lost row. The walk uses an explicit
 * stack, so depth is limited by memory and not by the JavaScript call stack.
 *
 * Fence: `taskTree.test.ts`.
 */

export type TaskLike = { id: string; parent_task_id?: string | null };

export interface TreeRow<T extends TaskLike = TaskLike> {
  task: T;
  /** 0 for a top-level row; +1 per level under a parent in this set. */
  depth: number;
  /** How many DIRECT subtask rows sit under this one (0 = no caret). */
  childCount: number;
  /**
   * Every row under this one, at any depth, in this set. Counted from the
   * data, never from what is drawn, so a collapsed parent still says how much
   * it holds.
   */
  descendantCount: number;
  /**
   * A subtask whose parent is NOT in this set. It sits at depth 0, and the
   * view draws its "↳ Parent" crumb so the reader knows it is a subtask.
   */
  orphan: boolean;
}

/**
 * One set of tasks → the rows a nesting view draws, subtasks under their
 * parent at any depth.
 *
 * Within a level the incoming order is kept, because the caller already
 * ordered the list (server sort, or `sortForView`).
 *
 * `collapsed` hides a parent's whole subtree. The counts do not change when
 * something collapses: they describe the data, and the caret needs them most
 * when the children are out of sight.
 */
export function treeRows<T extends TaskLike>(
  tasks: readonly T[],
  collapsed: ReadonlySet<string> = new Set()
): TreeRow<T>[] {
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

  const out: TreeRow<T>[] = [];
  const visited = new Set<string>();
  // Every visited task in visit order, and the task it was reached from.
  // The counts are summed over this after the walk, bottom-up, in one pass.
  const order: string[] = [];
  const reachedFrom = new Map<string, string>();
  const walk = (start: T) => {
    // An explicit stack of [task, depth, hidden, from]. Children are pushed
    // in reverse so they pop in their incoming order.
    const stack: [T, number, boolean, string | null][] = [[start, 0, false, null]];
    while (stack.length) {
      const [task, depth, hidden, from] = stack.pop()!;
      // A task already met is a loop back in the data. It is skipped, so
      // each task is drawn once and counted once.
      if (visited.has(task.id)) continue;
      visited.add(task.id);
      order.push(task.id);
      if (from !== null) reachedFrom.set(task.id, from);
      const kids = children.get(task.id) ?? [];
      if (!hidden) {
        const parent = task.parent_task_id;
        out.push({
          task,
          depth,
          childCount: kids.length,
          descendantCount: 0,
          orphan: depth === 0 && Boolean(parent) && !present.has(parent!),
        });
      }
      // `hidden` still walks, so a collapsed subtree's members are marked
      // visited (the sweep below must not surface them flat) and counted.
      const hideKids = hidden || collapsed.has(task.id);
      for (let i = kids.length - 1; i >= 0; i -= 1) {
        stack.push([kids[i], depth + 1, hideKids, task.id]);
      }
    }
  };
  for (const root of roots) walk(root);
  // A parent cycle leaves its members with no root. Surface them flat rather
  // than lose them: the first one met becomes a root.
  for (const task of tasks) if (!visited.has(task.id)) walk(task);

  // Bottom-up: a task is visited after the task it was reached from, so the
  // reverse visit order meets every child before its parent.
  const descendants = new Map<string, number>();
  for (let i = order.length - 1; i >= 0; i -= 1) {
    const id = order[i];
    const from = reachedFrom.get(id);
    if (from !== undefined) {
      descendants.set(from, (descendants.get(from) ?? 0) + 1 + (descendants.get(id) ?? 0));
    }
  }
  for (const row of out) row.descendantCount = descendants.get(row.task.id) ?? 0;
  return out;
}
