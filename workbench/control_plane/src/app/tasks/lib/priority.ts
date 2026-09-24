// The prioritization engine — the single source of truth for how a task's
// three inputs (important × urgent × leveraged) become a priority CELL, an
// action MODE, and a rank. Pure + unit-testable; imported everywhere the UI
// slices by priority so the matrix is computed identically on every surface.
//
// Design (agreed with the user):
//   • urgent is DERIVED from dueAt (overdue or due within a window), never
//     stored — so it can't go stale. Overridable only by editing the due date.
//   • important = downside (something stalls/breaks if skipped) — manual.
//   • leveraged = upside (asymmetric 100x outcome) — manual, the scarce flag.
//   • D78 (2026-09-24): important and leveraged are SHARED, one answer per
//     task, and Projects shows this same matrix. See `IMPORTANT_AT`.
//   • The 8 cells are a projection of the three booleans (the user's Notion
//     formula, verbatim). Never persisted.
//   • The cells collapse into ACTION MODES: do / delegate / schedule / drop.
//     Delegate/Schedule are SUGGESTIONS layered on My Next Actions, never a
//     forced move — dismissible via keptMine.

import { MyTask } from "./types";

/**
 * The stored `pm_tasks.importance` at which a task is Important. D78.
 *
 * Owner decision 2026-09-24: ONE priority system, the matrix, in Projects and
 * My Tasks, and one answer per task. The column keeps its 0-3 values so every
 * reader that compares it still works (filters, merge, recurrence). Important
 * is 2 or more. The server's twin is `IMPORTANT_AT` in
 * `routes/tasks/priority.py`, and `test_priority_shared.py` reads this line
 * to hold the two equal (R7).
 */
export const IMPORTANT_AT = 2;

/** `pm_tasks.importance` → the matrix's Important. Undefined stays undefined:
 *  nobody has judged the task yet, which the triage nudge looks for. */
export function importantFromImportance(
  importance: number | null | undefined,
): boolean | undefined {
  if (typeof importance !== "number") return undefined;
  return importance >= IMPORTANT_AT;
}

/** The matrix's Important → the value to store in `pm_tasks.importance`.
 *  0 means "judged, and not important", which is not the same as NULL. */
export function importanceFor(important: boolean): number {
  return important ? IMPORTANT_AT : 0;
}

/** Default urgency window (hours). A due task is urgent when overdue or due
 *  within this many hours. Overridable per-user (user_settings). */
export const DEFAULT_URGENT_WINDOW_HOURS = 48;

/** Header label for the "no @context" bucket when grouping by context. */
export const NO_CONTEXT_GROUP = "No context";

const HOUR_MS = 60 * 60 * 1000;

/** Is this task urgent right now? Overdue OR due within `windowHours`. A task
 *  with no due date is never urgent. `now` is injectable for tests/determinism. */
export function isUrgent(
  item: Pick<MyTask, "dueAt">,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): boolean {
  if (!item.dueAt) return false;
  const due = new Date(item.dueAt).getTime();
  if (Number.isNaN(due)) return false;
  return due <= now + windowHours * HOUR_MS; // overdue (due<=now) is included
}

/** True when the task became urgent only because a deadline is now close (vs
 *  already overdue) — used to *surface* a task that silently crossed into
 *  urgent so the auto-derivation doesn't work against the user. */
export function isNewlyUrgent(
  item: Pick<MyTask, "dueAt">,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): boolean {
  if (!item.dueAt) return false;
  const due = new Date(item.dueAt).getTime();
  if (Number.isNaN(due)) return false;
  return due > now && due <= now + windowHours * HOUR_MS; // close, not overdue
}

// ── The 7 priority levels ────────────────────────────────────────────────────
//
// LABELS describe the priority CHARACTER of a task (how important / leveraged /
// urgent it is). The action to take about it — delegate / schedule / eliminate —
// is NOT in the label; it surfaces separately as the competing card badge (see
// SUGGESTION_BADGE). Removing the action-words collapses the two "eliminate"
// cases (urgent-but-not-important AND neither) into a single "Low Priority"
// level, so there are 7 levels, not 8.

export type PriorityCell =
  | "critical" // 1. ❗⏰⚖️  Important + Urgent + Leveraged
  | "urgent" // 2. ❗⏰   Important + Urgent, not Leveraged
  | "high-leverage" // 3. ❗⚖️   Important + Leveraged, not Urgent
  | "important" // 4. ❗    Important, not Urgent, not Leveraged
  | "quick-leverage" // 5. ⏰⚖️   Urgent + Leveraged, not Important
  | "speculative-bet" // 6. ⚖️    Leveraged only
  | "low-priority"; // 7. ⏰ only OR none — not important to you

export interface CellMeta {
  cell: PriorityCell;
  /** 1..7 — rank order (1 = act first). Interleaves leveraged and non-leveraged
   *  levels by TRUE priority (an important+urgent fire outranks leveraged-but-
   *  not-urgent high-leverage work). */
  order: number;
  emoji: string;
  label: string;
  /** which action SUGGESTION this level nudges toward — surfaced as a competing
   *  badge, NEVER a status the task lives in, and NEVER part of the label. */
  mode: ActionMode;
}

/** do = my leverage/important work (no nudge) · delegate = hand off · schedule =
 *  calendar it · drop = eliminate/ignore (or delegate if it must happen). All of
 *  delegate/schedule/drop are SUGGESTIONS surfaced as card badges, never a
 *  forced status change and never in the label. */
export type ActionMode = "do" | "delegate" | "schedule" | "drop";

// Labels are the priority CHARACTER only (no action-words). `order` is the rank
// 1→7; `mode` is the badge the level nudges toward.
//   1 Critical (do) · 2 Urgent (delegate) · 3 High-Leverage (do) ·
//   4 Important (schedule) · 5 Quick Leverage Win (do) ·
//   6 Speculative Bet (do) · 7 Low Priority (eliminate/delegate)
export const CELL_META: Record<PriorityCell, CellMeta> = {
  critical: { cell: "critical", order: 1, emoji: "🔥", label: "Critical", mode: "do" },
  urgent: { cell: "urgent", order: 2, emoji: "🚨", label: "Urgent", mode: "delegate" },
  "high-leverage": { cell: "high-leverage", order: 3, emoji: "📈", label: "High-Leverage", mode: "do" },
  important: { cell: "important", order: 4, emoji: "❗", label: "Important", mode: "schedule" },
  "quick-leverage": { cell: "quick-leverage", order: 5, emoji: "📤", label: "Quick Leverage Win", mode: "do" },
  "speculative-bet": { cell: "speculative-bet", order: 6, emoji: "🧪", label: "Speculative Bet", mode: "do" },
  "low-priority": { cell: "low-priority", order: 7, emoji: "🗑", label: "Low Priority", mode: "drop" },
};

/** Priority level → Lucide icon NAME. Data, so a `.ts` chip descriptor (the
 *  Projects card, `projects/lib/card.ts`) and the JSX map in
 *  `priorityIcons.ts` read ONE mapping. A level has its own glyph as well as
 *  its tone, so it reads without colour (D-PM-27). */
export const CELL_ICON_NAME: Record<PriorityCell, string> = {
  critical: "Flame",
  urgent: "Siren",
  "high-leverage": "TrendingUp",
  important: "CircleAlert",
  "quick-leverage": "Rocket",
  "speculative-bet": "FlaskConical",
  "low-priority": "ArrowDownWideNarrow",
};

/** The 7 levels in rank order (1 → 7). */
export const CELLS_IN_ORDER: PriorityCell[] = (
  Object.values(CELL_META) as CellMeta[]
)
  .slice()
  .sort((a, b) => a.order - b.order)
  .map((m) => m.cell);

/** The three resolved booleans for a task (urgent derived). */
export interface PriorityInputs {
  important: boolean;
  urgent: boolean;
  leveraged: boolean;
}

/** Resolve a task's three matrix inputs (urgent derived from dueAt). */
export function priorityInputs(
  item: Pick<MyTask, "dueAt" | "important" | "leveraged">,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): PriorityInputs {
  return {
    important: Boolean(item.important),
    leveraged: Boolean(item.leveraged),
    urgent: isUrgent(item, windowHours, now),
  };
}

/** The priority level as a pure function of the 3 booleans (7 levels: the two
 *  "not important to you" cases — urgent-only and neither — both fold into
 *  low-priority). */
export function cellForInputs({ important, urgent, leveraged }: PriorityInputs): PriorityCell {
  if (leveraged) {
    if (important && urgent) return "critical"; // 1
    if (important && !urgent) return "high-leverage"; // 3
    if (!important && urgent) return "quick-leverage"; // 5
    return "speculative-bet"; // 6
  }
  if (important && urgent) return "urgent"; // 2
  if (important && !urgent) return "important"; // 4
  // Not important to you — urgent-only OR neither → one Low Priority level.
  return "low-priority"; // 7
}

/** The priority cell for a task (inputs resolved + formula applied). */
export function priorityCell(
  item: Pick<MyTask, "dueAt" | "important" | "leveraged">,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): PriorityCell {
  return cellForInputs(priorityInputs(item, windowHours, now));
}

/** The action mode for a task (do / delegate / schedule / drop). */
export function actionMode(
  item: Pick<MyTask, "dueAt" | "important" | "leveraged">,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): ActionMode {
  return CELL_META[priorityCell(item, windowHours, now)].mode;
}

/** The ONE presentation of an action mode — the suggestion of what to do with a
 *  task: "Do It" (it's yours), or the nudges "Delegate?" / "Schedule?" /
 *  "Eliminate?". Shared by the Action Mode column AND the group-by lens so the
 *  two never drift. The "?" reads as a suggestion, never a forced status. */
export const ACTION_MODE_META: Record<
  ActionMode,
  { label: string; emoji: string }
> = {
  do: { label: "Do It", emoji: "🎯" },
  delegate: { label: "Delegate?", emoji: "🙋" },
  schedule: { label: "Schedule?", emoji: "📅" },
  drop: { label: "Eliminate?", emoji: "🗑" },
};

/** The matrix rank (1 = highest). Lower sorts first. */
export function priorityRank(
  item: Pick<MyTask, "dueAt" | "important" | "leveraged">,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): number {
  return CELL_META[priorityCell(item, windowHours, now)].order;
}

/** A task has NO explicit judgment yet (neither flag set) — the matrix is only
 *  *guessing* (via urgency) about it. Drives the "needs triage" affordance so
 *  the user can tell judged tasks from defaulted ones. */
export function isUntagged(
  item: Pick<MyTask, "important" | "leveraged">,
): boolean {
  return !item.important && !item.leveraged;
}

// ── Action-mode suggestion (the competing badge on a task card) ──────────────

export interface ModeSuggestion {
  mode: ActionMode;
  cell: PriorityCell;
}

/** Presentation for the competing suggestion BADGE. A task's status is
 *  untouched; this badge just prompts the user to reconsider — delegate it,
 *  schedule it, or eliminate/ignore it (drop). "Delegate" is never a status. */
export interface SuggestionBadge {
  emoji: string;
  /** short chip label */
  label: string;
  /** the fuller prompt (tooltip / expanded) */
  prompt: string;
}

export const SUGGESTION_BADGE: Record<
  Exclude<ActionMode, "do">,
  SuggestionBadge
> = {
  delegate: {
    emoji: "🚨",
    label: "Delegate?",
    prompt: "Important + urgent — attend to it now or hand it off.",
  },
  schedule: {
    emoji: "🔁",
    label: "Schedule?",
    prompt: "Important but not urgent — put it on the calendar or delegate it.",
  },
  // Low Priority (urgent-only OR neither): not important to you — kill it, or
  // hand it off if it genuinely has to happen.
  drop: {
    emoji: "🗑",
    label: "Eliminate?",
    prompt: "Not important to you — eliminate it, or delegate it if it must happen.",
  },
};

/** Should this NEXT task carry a delegate/schedule/eliminate *suggestion badge*?
 *
 * Only for tasks that are still mine to act on (NEXT + mine), not already
 * delegated (WAITING) or calendared, and not dismissed (keptMine). Returns null
 * for "do" work (genuinely yours) and anything that shouldn't be nudged. This is
 * a SUGGESTION — a competing badge on the card — never a status change. */
export function modeSuggestion(
  item: Pick<
    MyTask,
    | "dueAt"
    | "important"
    | "leveraged"
    | "disposition"
    | "isMine"
    | "keptMine"
  >,
  windowHours = DEFAULT_URGENT_WINDOW_HOURS,
  now: number = Date.now(),
): ModeSuggestion | null {
  if (item.keptMine) return null; // user said "this one's mine"
  if (item.disposition !== "NEXT" || !item.isMine) return null;
  const cell = priorityCell(item, windowHours, now);
  const mode = CELL_META[cell].mode;
  if (mode === "do") return null; // nothing to suggest — it's yours to do
  return { mode, cell };
}
