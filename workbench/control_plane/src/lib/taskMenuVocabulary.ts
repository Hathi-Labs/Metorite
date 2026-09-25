/**
 * The right-click menu on a task: one set of words, in one order, for both
 * task apps (continuity P3, item 6).
 *
 * Projects declares its menu in `app/projects/lib/taskMenu.ts`. My Tasks
 * builds its card menu in `app/tasks/lib/cardMenu.ts`. The two menus offer
 * different acts, because the two apps can do different things. But an act
 * both apps offer must have ONE label, and it must sit in ONE place in the
 * menu. Before this file, My Tasks said "Mark as Done" where Projects said
 * "Mark done", and it put "Change status" above the done toggle.
 *
 * This module is the list of the shared acts, their labels and their order.
 * An act only one app offers ("Schedule on calendar", "Eliminate…") is not
 * here. It comes AFTER every shared act, in the app that offers it.
 *
 * Fences: `taskMenuVocabulary.test.ts` holds the Projects registry and the
 * My Tasks menu to this order and these labels.
 */

/** Every act a task menu in either app may share, in menu order. */
export const SHARED_TASK_ACTS = [
  "open",
  "copyLink",
  "markDone",
  "rename",
  "addSubtask",
  "select",
  "moveToProject",
  "mergeInto",
  "status",
  "archive",
  "delete",
] as const;

export type SharedTaskAct = (typeof SHARED_TASK_ACTS)[number];

/**
 * The fixed label of each shared act. `markDone` and `select` are toggles,
 * so their label depends on the task. `doneLabel` owns the first. Projects'
 * registry owns the second, because only Projects offers it.
 */
export const TASK_ACT_LABEL = {
  open: "Open",
  copyLink: "Copy link",
  rename: "Rename",
  addSubtask: "Add subtask",
  select: "Select",
  moveToProject: "Move to project…",
  mergeInto: "Merge into…",
  status: "Change status",
  archive: "Archive",
  delete: "Delete",
} as const satisfies Partial<Record<SharedTaskAct, string>>;

/** The done toggle says which way it goes. One wording in both apps. */
export function doneLabel(isDone: boolean): "Reopen" | "Mark done" {
  return isDone ? "Reopen" : "Mark done";
}

/** Where an act sits in the shared order. An app-only act sorts last. */
export function taskActRank(act: string): number {
  const at = (SHARED_TASK_ACTS as readonly string[]).indexOf(act);
  return at === -1 ? SHARED_TASK_ACTS.length : at;
}

/**
 * Put acts into the shared order. Shared acts come first, in
 * `SHARED_TASK_ACTS` order. App-only acts follow, in the order given.
 */
export function inTaskMenuOrder<T extends string>(acts: readonly T[]): T[] {
  return acts
    .map((act, at) => ({ act, at }))
    .sort((a, b) => taskActRank(a.act) - taskActRank(b.act) || a.at - b.at)
    .map((entry) => entry.act);
}
