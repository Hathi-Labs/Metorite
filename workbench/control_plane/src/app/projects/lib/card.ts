/**
 * Projects · a task row, in the shared card's terms (WS-27s).
 *
 * The seam between `TaskRow` — snake_case, straight off the list endpoint — and
 * `@/lib/taskCard`'s `TaskFacts`, which is deliberately neither app's row type.
 * Keeping the translation here rather than inline in the board means the two
 * surfaces that draw a card (board and list) cannot start disagreeing about
 * which facts a task has, which is exactly how they drifted before.
 *
 * **Only fields the LIST endpoint actually returns.** `attachmentCount` is
 * honestly absent — attachments are counted on the single task read (WS-27i) —
 * and filling it with a plausible zero would make the card assert something it
 * does not know. `estimateMins` is NOT in that category and used to be treated
 * as though it were: `pm_tasks.estimate_mins` has always been on `TaskModel`
 * and the table's Estimate column has always read it, so the card was the one
 * surface that dropped the fact (S6).
 */

import {
  type MetaChip,
  type TagFact,
  type TaskFacts,
  type TypeFact,
  chipKind,
  priorityChip,
  taskMeta,
} from "@/lib/taskCard";

import type { TagRow, TaskRow, TaskTypeRow } from "./api";
import { taskCell } from "./matrix";

/**
 * `lower(tag name)` → the registry's stored colour.
 *
 * Built once per surface, not once per card: a board draws hundreds of rows
 * and the registry is one array. Case-folded because a task's `tags` array
 * carries the spelling that was saved and the registry owns the canonical one
 * (`tags.registryOf` folds the same way) — a chip that lost its colour because
 * somebody typed `Bug` is the drift this avoids.
 */
export function tagColours(
  tags: readonly Pick<TagRow, "name" | "color">[]
): Map<string, string> {
  return new Map(tags.map((tag) => [tag.name.toLowerCase(), tag.color]));
}

/**
 * WS-27bh — the type registry as the map `taskFacts` looks an id up in.
 *
 * Keyed by **id**, not by name. A tag is carried on the row by name, so
 * `tagColours` keys by a lowercased name; a type is carried as `type_id`, and
 * keying this by name would need a second lookup that does not exist.
 */
export function typeFacts(
  types: readonly Pick<TaskTypeRow, "id" | "name" | "icon" | "color">[]
): Map<string, TypeFact> {
  return new Map(
    types.map((type) => [
      type.id,
      { name: type.name, icon: type.icon, color: type.color },
    ])
  );
}

export function taskFacts(
  task: TaskRow,
  colours?: ReadonlyMap<string, string>,
  types?: ReadonlyMap<string, TypeFact>
): TaskFacts {
  return {
    // WS-27bh. `type_id` is a uuid and the registry that names it is
    // per-root, so the page resolves it and passes the map. An id with no
    // entry yields NO chip rather than a chip reading the uuid: a type
    // deleted after the task was written is exactly the case the tag colour
    // lookup already survives.
    type: task.type_id ? (types?.get(task.type_id) ?? null) : null,
    source: task.source ?? null,
    dueAt: task.due_at,
    completedAt: task.completed_at,
    subtasks: task.subtasks ?? null,
    blockedByCount: task.blocked_by_count ?? 0,
    tags: (task.tags ?? []).map(
      (name): TagFact => ({ name, color: colours?.get(name.toLowerCase()) })
    ),
    estimateMins: task.estimate_mins,
  };
}

/**
 * The task's priority LEVEL as a chip — the matrix level (D78, 2026-09-24).
 *
 * Projects and My Tasks show one priority system: shared Important × urgent
 * from the due date × shared Leveraged (`lib/matrix.ts`). The chip key stays
 * `importance`, so a saved view's "show Priority" still governs it.
 *
 * **Low Priority earns no chip.** It is the level of every task nobody has
 * flagged, and a pill on every ordinary card is noise. My Tasks' card makes
 * the same call (`hideLowPriority`).
 *
 * The chip itself is `priorityChip` (`@/lib/taskCard`), the ONE priority
 * chip My Tasks' `PriorityBadge` draws too, so a level looks the same in both
 * apps. This file only reads the level off a `TaskRow`.
 */
export function importanceChip(
  task: Pick<TaskRow, "importance" | "leveraged" | "due_at">,
  nowMs?: number
): MetaChip | null {
  const cell = taskCell(task, nowMs);
  if (cell === "low-priority") return null;
  return priorityChip(cell);
}

/**
 * The chips one row has earned.
 *
 * `taskMeta` stays the rulebook for the facts both apps share; priority is
 * spliced in immediately after `blocked` — the same move `/tasks`' adapter
 * makes for its count-only subtasks. The slot is deliberate: blocked says *do
 * not start this*, priority says *how much this matters*, due says *when*, and
 * a reader scanning a column wants them in that order.
 */
export function cardChips(
  task: TaskRow,
  nowMs?: number,
  colours?: ReadonlyMap<string, string>,
  types?: ReadonlyMap<string, TypeFact>
): MetaChip[] {
  const chips = taskMeta(taskFacts(task, colours, types), nowMs);
  const priority = importanceChip(task, nowMs);
  if (priority) {
    const at = chips.findIndex((chip) => chip.key === "blocked") + 1;
    chips.splice(at, 0, priority);
  }
  return chips;
}

/**
 * WS-27x — which shown-field key each chip kind renders under.
 *
 * The VISIBILITY layer over `taskMeta`'s fact layer: `taskCard.ts` stays the
 * one place that decides which chips a task has *earned*, and this mapping is
 * the one place that decides which of them the view's `shown_fields` lets
 * through. Keys are `lib/shownFields.ts`'s vocabulary — the same source the
 * table's columns read, so hiding a field silences its chip on every surface
 * at once.
 *
 * Exported so a test can assert every chip `cardChips` can emit is mapped —
 * an unmapped chip kind would silently bypass the gate.
 *
 * **Keyed by chip KIND, not by chip key** (S6): a task with three tags emits
 * `tags:ops`, `tags:api`, `tags:more`, and a whole-key lookup would find none
 * of them and silence every tag on every view.
 */
export const CHIP_FIELD: Record<string, string> = {
  type: "type",
  source: "source",
  blocked: "blocked",
  importance: "importance",
  due: "due_at",
  subtasks: "subtasks",
  tags: "tags",
  attachments: "attachments",
  estimate: "estimate",
};

/**
 * The chips one row has earned AND the view chose to show.
 *
 * A chip whose field is not shown produces nothing — not a dimmed chip, not a
 * placeholder — because "this view does not surface due dates" and "this task
 * has no due date" must read identically, exactly as `taskMeta`'s
 * a-zero-earns-no-chip rule already treats absence.
 */
export function visibleChips(
  task: TaskRow,
  shownFields: readonly string[],
  nowMs?: number,
  colours?: ReadonlyMap<string, string>,
  types?: ReadonlyMap<string, TypeFact>
): MetaChip[] {
  return cardChips(task, nowMs, colours, types).filter((chip) => {
    const kind = chipKind(chip.key);
    return shownFields.includes(CHIP_FIELD[kind] ?? kind);
  });
}

/**
 * WS-27w item 6 — the human task id, formatted in ONE place.
 *
 * `#42` everywhere a number exists; `null` (not `"#undefined"`, not `"—"`)
 * when it does not, so each surface keeps its own honest fallback. The board,
 * the list and the panel all read this — three inline `#${…}` templates is
 * how one of them ends up rendering `#null` after an import.
 */
export function taskRef(task: Pick<TaskRow, "task_number">): string | null {
  return task.task_number == null ? null : `#${task.task_number}`;
}

/**
 * The URL the copy-link affordance puts on the clipboard.
 *
 * `/projects?task=<id>` is the deep-link shape the board already reads
 * (WS-28b) and the notification bell already emits — a third spelling would
 * be a link that opens nothing. `origin` is passed in rather than read from
 * `window` here, so the formatting stays pure and testable.
 */
export function taskDeepLink(task: Pick<TaskRow, "id">, origin = ""): string {
  return `${origin}/projects?task=${encodeURIComponent(task.id)}`;
}
