/**
 * Task type rules for the type control and for "Make it an epic" (SP-6).
 *
 * Pure on purpose: the panel and the board menu both read these, so the two
 * doors give one answer. The gateway holds the real rule
 * (`assert_epic_has_no_parent`, a 422). This file refuses the same case
 * before the request, so the member reads the reason at once.
 */

import type { SelectOption } from "@/components/ui/SelectButton";

import type { TaskRow, TaskTypeRow } from "./api";

/** What the member reads when a subtask is asked to become an epic. */
export const SUBTASK_EPIC_REASON =
  "A subtask cannot be an epic. Move it to the top level first.";

/** The value the type select uses for "No type". */
export const NO_TYPE = "";

/** The project's epic type, or undefined when the project has none. */
export function epicTypeOf(
  types: readonly TaskTypeRow[] | undefined,
): TaskTypeRow | undefined {
  return (types ?? []).find((t) => Boolean(t.is_epic));
}

/** True when the task already wears an epic type. */
export function isEpicTask(
  task: Pick<TaskRow, "type_id">,
  types: readonly TaskTypeRow[] | undefined,
): boolean {
  if (!task.type_id) return false;
  return (types ?? []).some((t) => t.id === task.type_id && Boolean(t.is_epic));
}

/** A subtask is a task with a parent. */
export function isSubtask(task: Pick<TaskRow, "parent_task_id">): boolean {
  return Boolean(task.parent_task_id);
}

/**
 * Whether "Make it an epic" is offered, and the epic type it would set.
 * `undefined` means the command is not offered at all.
 */
export function makeEpicTarget(
  task: Pick<TaskRow, "type_id">,
  types: readonly TaskTypeRow[] | undefined,
): TaskTypeRow | undefined {
  const epic = epicTypeOf(types);
  if (!epic) return undefined;
  if (isEpicTask(task, types)) return undefined;
  return epic;
}

/**
 * The reason this task may not take this type, or null when it may.
 * Only a subtask and an epic type conflict.
 */
export function typeRefusal(
  task: Pick<TaskRow, "parent_task_id">,
  type: Pick<TaskTypeRow, "is_epic"> | undefined,
): string | null {
  return type?.is_epic && isSubtask(task) ? SUBTASK_EPIC_REASON : null;
}

/**
 * The options of the panel's type select: "No type", then each type.
 * On a subtask the epic type stays in the list but cannot be chosen, and
 * its description says why.
 */
export function typeSelectOptions(
  types: readonly TaskTypeRow[] | undefined,
  task?: Pick<TaskRow, "parent_task_id">,
): SelectOption[] {
  return [
    { value: NO_TYPE, label: "No type" },
    ...(types ?? []).map((t) => {
      const refusal = task ? typeRefusal(task, t) : null;
      return {
        value: t.id,
        label: t.name,
        hint: t.is_epic ? "Top level only" : undefined,
        disabled: refusal !== null,
        description: refusal ?? undefined,
      };
    }),
  ];
}
