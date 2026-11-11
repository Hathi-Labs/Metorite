/**
 * SP-2a — the Epic filter of the Board.
 *
 * Page state, not a saved-view key. It narrows the rows the board already
 * holds, as "Waiting on me" does. The rows carry `parent_task_id` and
 * `type_id`, so no new read is needed.
 *
 * A task stays when it is the epic itself or lies under it at any depth.
 * The epic stays so the member sees its own status beside its tasks.
 */
import type { TaskRow, TaskTypeRow } from "./api";
import { isEpicTask } from "./taskEpic";

type Node = Pick<TaskRow, "id" | "parent_task_id">;

/** The epics among the loaded rows, for the filter's options. */
export function epicOptions(
  tasks: readonly Pick<TaskRow, "id" | "type_id" | "title" | "task_number">[],
  types: readonly TaskTypeRow[] | undefined,
): { value: string; label: string }[] {
  return tasks
    .filter((t) => isEpicTask(t, types))
    .map((t) => ({
      value: t.id,
      label: t.task_number ? `#${t.task_number} ${t.title}` : t.title,
    }));
}

/** The id of the epic and of every task under it, at any depth. */
export function epicScope(tasks: readonly Node[], epicId: string): Set<string> {
  const kids = new Map<string, string[]>();
  for (const t of tasks) {
    if (!t.parent_task_id) continue;
    const list = kids.get(t.parent_task_id) ?? [];
    list.push(t.id);
    kids.set(t.parent_task_id, list);
  }
  const scope = new Set<string>([epicId]);
  const queue = [epicId];
  while (queue.length) {
    for (const id of kids.get(queue.pop() as string) ?? []) {
      if (scope.has(id)) continue; // a parent loop cannot hang the board
      scope.add(id);
      queue.push(id);
    }
  }
  return scope;
}

/** The rows under the epic. An empty `epicId` means no filter. */
export function filterByEpic<T extends Node>(tasks: readonly T[], epicId: string): T[] {
  if (!epicId) return tasks as T[];
  const scope = epicScope(tasks, epicId);
  return tasks.filter((t) => scope.has(t.id));
}
