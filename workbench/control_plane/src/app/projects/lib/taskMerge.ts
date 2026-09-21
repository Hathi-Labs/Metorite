/**
 * Projects · choosing what a merge will keep, before agreeing to it.
 *
 * Owner request, 2026-09-21. The gateway owns the merge; this owns the
 * question the member is answering, which is **which task survives**.
 *
 * ⚠️ That question is the whole reason a merge opens a card instead of
 * happening on click. `moveToProject` opens one because a move crosses two
 * vocabularies; this opens one for a stronger reason — a merge moves comments,
 * history and attachments ONE WAY, and running it again does not undo it.
 *
 * `vitest.config.ts` is `environment: "node"`, so the rules live here and the
 * dialog only draws them.
 */
import type { TaskRow } from "./api";

/** A task the selection could be merged into, and why not where it cannot. */
export interface MergeCandidate {
  task: TaskRow;
  /** Non-null means "offer it greyed, and say this" rather than hide it. */
  refusal: string | null;
}

/**
 * Why this task cannot receive the merge, or `null`.
 *
 * ⚠️ **Refusals are SHOWN, not filtered out**, which is `MoveDialog`'s rule
 * and matters more here. A member looking for the task they meant to merge
 * into needs to find it and read why it is unavailable; silently omitting it
 * reads as the list being wrong, and the commonest case — the task is one of
 * the ones being merged — is the one they most need explained.
 */
export function mergeRefusal(
  target: TaskRow,
  sources: readonly string[],
): string | null {
  if (sources.includes(target.id)) {
    return "This is one of the tasks being merged.";
  }
  if (target.merged_into_task_id) {
    // The gateway refuses this too. Saying so here means the member never
    // spends a click to be told.
    return "Already merged into another task.";
  }
  if (target.archived_at) {
    return "Archived — restore it first.";
  }
  return null;
}

/**
 * Every task in hand, as a candidate, newest work first.
 *
 * ⚠️ Ordered by task NUMBER descending rather than by title. The task you
 * are merging into is nearly always one you touched recently, and a
 * title sort buries it among a hundred alphabetical neighbours.
 */
export function mergeCandidates(
  tasks: readonly TaskRow[],
  sources: readonly string[],
): MergeCandidate[] {
  return [...tasks]
    .sort((a, b) => (b.task_number ?? 0) - (a.task_number ?? 0))
    .map((task) => ({ task, refusal: mergeRefusal(task, sources) }));
}

/** Narrow the list by a typed query — the number or any word of the title. */
export function matchCandidates(
  candidates: readonly MergeCandidate[],
  query: string,
): MergeCandidate[] {
  const needle = query.trim().toLowerCase().replace(/^#/, "");
  if (!needle) return [...candidates];
  return candidates.filter(
    (c) =>
      String(c.task.task_number ?? "").includes(needle) ||
      (c.task.title ?? "").toLowerCase().includes(needle),
  );
}

/**
 * What the member is about to do, in one sentence.
 *
 * ⚠️ Names the SURVIVOR explicitly and says the sources are archived rather
 * than deleted. Both halves were asked for: the owner's objection to the
 * first design was that a merged task must not become unreachable, and a
 * confirmation that said "merge 3 tasks" without naming which one is kept
 * is the sentence somebody misreads at four in the afternoon.
 */
export function mergeSummary(
  target: TaskRow | null,
  sources: readonly string[],
): string {
  const n = sources.length;
  const what = n === 1 ? "1 task" : `${n} tasks`;
  if (!target) return `Choose the task to keep. ${what} will be folded into it.`;
  // ⚠️ Agrees with the count. "Their comments … they are archived" for one
  // task reads as a translation, and this is the sentence somebody reads
  // immediately before an irreversible click — the one place in the product
  // where sounding unsure is expensive.
  const whose = n === 1 ? "Its" : "Their";
  const it = n === 1 ? "it is" : "they are";
  return (
    `${what} will be folded into #${target.task_number} ${target.title}. ` +
    `${whose} comments, history, attachments, assignees, tags and subtasks ` +
    `move across, and ${it} archived with a link to it.`
  );
}
