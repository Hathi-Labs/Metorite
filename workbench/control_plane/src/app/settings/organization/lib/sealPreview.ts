/**
 * What off-boarding somebody would seal, as the sentence D63 dictates.
 *
 * Spec: `work_plan.md` §3 D63 · H-49 slice 2.
 *
 * D63's last requirement, quoted rather than paraphrased because the wording
 * carries the policy:
 *
 * > **The deactivation dialog must state the split in numbers before the
 * > click** — *"14 tasks — 3 handed over, 11 sealed, not deleted; later access
 * > is recorded"*. A policy nobody is told about at the moment it applies is
 * > one they discover by being surprised.
 *
 * Pure on purpose, and separate from the dialog, because the interesting part
 * is the WORDING and its edge cases — nothing here renders. The component
 * reads `GET /admin/members/{email}/seal-preview` and hands the result here.
 */

/** The server's answer. `handed_over + sealed === tasks` by construction. */
export interface SealPreview {
  projects: number;
  tasks: number;
  handed_over: number;
  sealed: number;
  /** Stated by the SERVER so the dialog cannot soften it. Always false. */
  deleted?: boolean;
  reversible?: boolean;
}

/** English plural for a count, without pulling in a library for two words. */
function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * The sentence, or `null` when there is nothing to say.
 *
 * ⚠️ **`null` for an empty tree, deliberately.** A member with no personal
 * projects and no tasks would otherwise get "0 tasks — 0 handed over, 0
 * sealed", which reads as a warning about nothing and trains people to skip
 * the line that matters. The dialog shows no seal paragraph at all in that
 * case.
 */
export function describeSeal(preview: SealPreview | null): string | null {
  if (!preview) return null;
  const { tasks, handed_over: handed, sealed } = preview;
  if (tasks <= 0 && preview.projects <= 0) return null;

  // ⚠️ The "not deleted" clause is NOT optional and never abbreviates. D63 is
  // emphatic that the tree is "retained, invisible, NEVER deleted", and the
  // moment of the click is the only place that promise does any work.
  const tail = "not deleted, and later access is recorded";

  if (tasks === 0) {
    // Projects but no tasks — an empty personal tree with Areas in it.
    return `${plural(preview.projects, "project", "projects")} sealed, ${tail}.`;
  }
  if (handed === 0) {
    // ⚠️ The COMMON case, and it must not read as an error. Two guards
    // (`assert_move_keeps_privacy`, `assert_assignable_here`) refuse to create
    // a task in a personal tree assigned to somebody else, so only rows
    // predating them can be handed over. Measured on production 2026-09-23:
    // 0 of 2. So the zero arm is the ordinary sentence, not the exception.
    return `${plural(tasks, "task", "tasks")} — all sealed, ${tail}.`;
  }
  return (
    `${plural(tasks, "task", "tasks")} — ${handed} handed over, ` +
    `${sealed} sealed, ${tail}.`
  );
}

/**
 * Who keeps what, in one clause — the half a person actually acts on.
 *
 * "Handed over" is D63's word and it is doing real work: those tasks stay
 * visible to the colleague they were assigned to, through the assignee arm of
 * the visibility clause, with no data moved. The admin needs to know nobody
 * has to chase them.
 */
export function describeHandover(preview: SealPreview | null): string | null {
  if (!preview || preview.handed_over <= 0) return null;
  return preview.handed_over === 1
    ? "1 task stays with the colleague it is assigned to."
    : `${preview.handed_over} tasks stay with the colleagues they are assigned to.`;
}
