// The shared-vocabulary chips a GTD task card earns — the /tasks adapter over
// `@/lib/taskCard` (WS-27s), so both task apps describe the same facts with
// the same chips: one icon for "due", one tone for "overdue", one duration
// format, drawn by the one `TaskMeta` renderer.
//
// This is an ADAPTER, not a fork: `taskMeta` stays the single rulebook for
// which chips a task earns and in what order. The only thing added here is
// the one fact `GtdItem` states differently — it knows HOW MANY subtasks a
// task has but not how many are done (`subtaskCount`, no `done`), so the
// shared `subtasks: {done, total}` descriptor would have to lie ("0/5"). A
// count-only chip in the same grammar (same key, icon, tone, slot in the
// reading order) is the honest version of the same sentence.
//
// GTD-only signals — @context, energy, deep-work, source, stage, priority —
// deliberately do NOT move into this row: they are this app's identity, and
// `taskCard.ts`'s own header says what is shared is the vocabulary for the
// facts both apps have, not a flattening of the two apps into one.

import { importanceChip } from "@/app/projects/lib/card";
import { type MetaChip, taskMeta } from "@/lib/taskCard";

import type { GtdItem } from "./types";

/**
 * The shared-fact chips for a GTD item, in the shared reading order:
 * Priority, due (with the overdue escalation), subtask count, tags,
 * attachments, estimate.
 *
 * D77 — Priority and tags are the TASK's shared facts, so the card draws
 * them with the Projects card's own chips (`importanceChip`, `taskMeta`'s
 * tag pills). One vocabulary (`IMPORTANCE_OPTIONS`, D76): a task Projects
 * shows as "Highest" shows "Highest" here too, in the same place.
 */
export function gtdMetaChips(item: GtdItem, nowMs = Date.now()): MetaChip[] {
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

  // The slot the Projects card gives it: right after `blocked`, which a
  // GTD item never earns, so first.
  const priority = importanceChip({ importance: item.orgPriority ?? null });
  if (priority) {
    const at = chips.findIndex((c) => c.key === "blocked") + 1;
    chips.splice(at, 0, priority);
  }

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
