// The shared-vocabulary chips a My Tasks card earns — the /tasks adapter over
// `@/lib/taskCard` (WS-27s), so both task apps describe the same facts with
// the same chips: one icon for "due", one tone for "overdue", one duration
// format, drawn by the one `TaskMeta` renderer.
//
// This is an ADAPTER, not a fork: `taskMeta` stays the single rulebook for
// which chips a task earns and in what order. The only thing added here is
// the one fact `MyTask` states differently — it knows HOW MANY subtasks a
// task has but not how many are done (`subtaskCount`, no `done`), so the
// shared `subtasks: {done, total}` descriptor would have to lie ("0/5"). A
// count-only chip in the same grammar (same key, icon, tone, slot in the
// reading order) is the honest version of the same sentence.
//
// My-Tasks-only signals — @context, energy, deep-work, source, stage, priority —
// deliberately do NOT move into this row: they are this app's identity, and
// `taskCard.ts`'s own header says what is shared is the vocabulary for the
// facts both apps have, not a flattening of the two apps into one.

import { type MetaChip, taskMeta } from "@/lib/taskCard";

import type { MyTask } from "./types";

/**
 * The shared-fact chips for a task, in the shared reading order:
 * Priority, due (with the overdue escalation), subtask count, tags,
 * attachments, estimate.
 *
 * D77 — tags are the TASK's shared facts, so the card draws them with the
 * Projects card's own tag pills. Priority is not a chip here: since D78 the
 * card's `PriorityBadge` is the one drawing of it, in both apps.
 */
export function taskMetaChips(item: MyTask, nowMs = Date.now()): MetaChip[] {
  const chips = taskMeta(
    {
      dueAt: item.dueAt,
      completedAt: item.completedAt,
      attachmentCount: item.attachments?.length ?? 0,
      estimateMins: item.timeEstimateMins,
      tags: (item.tags ?? []).map((name) => ({ name })),
    },
    nowMs,
  );

  const count = item.subtaskCount ?? 0;
  if (count > 0) {
    // Same slot the shared order gives subtasks: right after "due", before
    // the quieter counts — so the row scans identically in both apps.
    const at = chips.findIndex((c) => c.key === "due") + 1;
    chips.splice(at, 0, {
      key: "subtasks",
      icon: "ListTree",
      label: String(count),
      tone: "muted",
      title: `${count} subtask${count === 1 ? "" : "s"}`,
    });
  }

  return chips;
}
