"use client";

import AppIcon, { themedIcon, type ThemedIcon } from "@/components/Icon";
import { GtdItem } from "../lib/types";
import { useTaskStore } from "../lib/taskStore";
import {
  CELL_META,
  SUGGESTION_BADGE,
  isImportant,
  isUrgent,
  modeSuggestion,
  priorityCell,
  type ActionMode,
  type PriorityCell,
} from "../lib/priority";
import { CELL_ICON, MODE_ICON } from "../lib/priorityIcons";

// Shared prioritization UI: the 3-flag Weight toggle (Important / Urgent-auto /
// Leveraged) and the priority badges. Kept together so the visual language
// (which colour, which emoji) is defined once.
//
// Two DISTINCT signals, and they may sit side by side on a card:
//   • PriorityBadge — the matrix-cell PILL (🔥 Critical, 📈 High-Leverage, …):
//     WHAT the task is. Shown on every Next-Actions card so the priority reads
//     at a glance (label hidden on the Priority view, which is grouped by level).
//   • SuggestionBadge — the competing action NUDGE (delegate / schedule /
//     eliminate): what to DO about it. A dismissible suggestion, never a status.
// They answer different questions, so they're allowed to co-exist (the nudge is
// hidden on the Priority view, where the pill already carries the level).

/** The Weight toggles. Important + Leveraged are manual (click to flip); Urgent
 *  is DERIVED from the due date and shown read-only (a lit, non-clickable chip
 *  when the task is urgent) so the user sees it without being able to lie about
 *  it. `onChange` fires with the new value of the flipped flag. */
export function WeightToggles({
  item,
  urgentWindowHours,
  onChange,
  size = "md",
}: {
  item: Pick<GtdItem, "important" | "leveraged" | "deepWork" | "dueAt"> &
    Pick<Partial<GtdItem>, "importance">;
  urgentWindowHours?: number;
  onChange: (patch: {
    important?: boolean;
    leveraged?: boolean;
    deepWork?: boolean;
  }) => void;
  size?: "sm" | "md";
}) {
  const urgent = isUrgent(item, urgentWindowHours);
  const pad = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {/* D76 — Important is a view of the task's SHARED Priority (High or
          Urgent), so the switch writes Priority for everybody. The store
          resolves the level (`importanceForImportant`). */}
      <FlagToggle
        active={isImportant(item)}
        onClick={() => onChange({ important: !isImportant(item) })}
        icon={themedIcon("AlertTriangle")}
        label="Important"
        title="Something stalls or breaks if this is skipped. Sets the task's shared Priority: on is High, off is Normal."
        tone="important"
        pad={pad}
      />
      <FlagToggle
        active={!!item.leveraged}
        onClick={() => onChange({ leveraged: !item.leveraged })}
        icon={themedIcon("Gem")}
        label="Leveraged"
        title="Rare, asymmetric 100x upside — an investor, a grant, a key hire."
        tone="leveraged"
        pad={pad}
      />
      <FlagToggle
        active={!!item.deepWork}
        onClick={() => onChange({ deepWork: !item.deepWork })}
        icon={themedIcon("Waves")}
        label="Deep work"
        title="Needs an unbroken flow state — creative, building, writing, strategy. The planner protects a long peak-energy block for it."
        tone="deep"
        pad={pad}
      />
      {/* Urgent is derived — read-only. Only shown when actually urgent. */}
      <span
        title={
          urgent
            ? "Urgent — overdue or due soon (set automatically from the due date)."
            : "Not urgent — set a due date to make it urgent automatically."
        }
        className={[
          "inline-flex items-center gap-1 rounded-full border font-medium",
          pad,
          urgent
            ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
            : "border-dashed border-border text-muted-foreground/50",
        ].join(" ")}
      >
        <AppIcon name="Zap" className="h-3 w-3" />
        Urgent
        <span className="text-[9px] opacity-60">(auto)</span>
      </span>
    </div>
  );
}

function FlagToggle({
  active,
  onClick,
  icon: Icon,
  label,
  title,
  tone,
  pad,
}: {
  active: boolean;
  onClick: () => void;
  icon: ThemedIcon;
  label: string;
  title: string;
  tone: "important" | "leveraged" | "deep";
  pad: string;
}) {
  const on =
    tone === "important"
      ? "border-amber-500/50 bg-amber-500/10 text-amber-600 dark:text-amber-400"
      : tone === "leveraged"
        ? "border-violet-500/50 bg-violet-500/10 text-violet-600 dark:text-violet-400"
        : "border-sky-500/50 bg-sky-500/10 text-sky-600 dark:text-sky-400";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={title}
      className={[
        "tech-transition inline-flex items-center gap-1 rounded-full border font-medium",
        pad,
        active
          ? on
          : "border-border text-muted-foreground hover:text-foreground hover:border-primary/40",
      ].join(" ")}
    >
      <Icon className="h-3 w-3" />
      {label}
    </button>
  );
}

const CELL_TONE: Record<PriorityCell, string> = {
  critical: "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400",
  urgent: "border-orange-500/40 bg-orange-500/10 text-orange-600 dark:text-orange-400",
  "high-leverage": "border-violet-500/40 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  important: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  "quick-leverage": "border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  "speculative-bet": "border-teal-500/40 bg-teal-500/10 text-teal-600 dark:text-teal-400",
  "low-priority": "border-border bg-secondary/40 text-muted-foreground",
};

/** The matrix-cell badge (emoji + optional label) — the task's priority level.
 *  Rides on every Next-Actions card (list + board) so the priority is visible at
 *  a glance; `showLabel=false` on the Priority view (grouped by level already). */
export function PriorityBadge({
  item,
  urgentWindowHours,
  showLabel = true,
  hideLowPriority = false,
}: {
  item: Pick<GtdItem, "important" | "leveraged" | "dueAt">;
  urgentWindowHours?: number;
  showLabel?: boolean;
  /** On the card face, don't badge the default "low-priority" cell — a colored
   *  pill on every ordinary task is noise. Elevated cells still show. The detail
   *  Priority section and the level-grouped Priority view leave this off. */
  hideLowPriority?: boolean;
}) {
  const cell = priorityCell(item, urgentWindowHours);
  if (hideLowPriority && cell === "low-priority") return null;
  const meta = CELL_META[cell];
  const Icon = CELL_ICON[cell];
  return (
    <span
      title={meta.label}
      className={[
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        CELL_TONE[cell],
      ].join(" ")}
    >
      <Icon className="h-3 w-3 shrink-0" aria-hidden />
      {showLabel && meta.label}
    </span>
  );
}

const SUGGESTION_TONE: Record<Exclude<ActionMode, "do">, string> = {
  delegate: "border-orange-500/40 bg-orange-500/10 text-orange-600 dark:text-orange-400",
  schedule: "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  drop: "border-border bg-secondary/60 text-muted-foreground",
};

/** The competing SUGGESTION badge on a task card. The matrix reads a task as
 *  better delegated / scheduled / eliminated than done by you — so it nudges,
 *  as a small badge that sits alongside the card, NEVER changing the task's
 *  status. Clicking opens the task (where the real delegate/schedule/trash
 *  controls live); the × dismisses the nudge ("keep mine" → keptMine). Renders
 *  nothing for "do" work or a dismissed task. Delegate is not a status. */
export function SuggestionBadge({
  item,
  urgentWindowHours,
  compact = false,
}: {
  item: GtdItem;
  urgentWindowHours?: number;
  compact?: boolean;
}) {
  const updateItem = useTaskStore((s) => s.updateItem);
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const openEliminate = useTaskStore((s) => s.openEliminate);
  const openDelegate = useTaskStore((s) => s.openDelegate);
  const sug = modeSuggestion(item, urgentWindowHours);
  if (!sug || sug.mode === "do") return null;
  const mode = sug.mode as Exclude<ActionMode, "do">;
  const badge = SUGGESTION_BADGE[mode];
  const Icon = MODE_ICON[mode];
  // Every nudge ACTS in place: "Schedule?" opens the calendar popup,
  // "Eliminate?" the Someday-or-delete popup, and "Delegate?" the eligible-
  // people picker (which promotes a LOCAL task to ClickUp when needed).
  const act = () => {
    if (mode === "schedule") openSchedule(item.id);
    else if (mode === "drop") openEliminate(item.id);
    else openDelegate(item.id);
  };
  // Plain nudge ("Delegate?"), no name. A delegate nudge fires on tasks that are
  // MINE (NEXT + isMine), so item.assignee is me — naming it would read
  // "Delegate to <me>?", which is nonsense. Suggesting a real delegate from the
  // org/HR structure is a separate follow-up; until then, no name.
  const label = badge.label;
  // On the card face (compact) the nudge is a QUIET neutral chip so it doesn't
  // compete with the colored priority pill — a card should carry one colored
  // priority signal, not two. In the detail Priority section it keeps its
  // expressive per-mode colour.
  const tone = compact
    ? "border-border bg-secondary/50 text-muted-foreground"
    : SUGGESTION_TONE[mode];
  return (
    <span
      className={[
        "inline-flex items-center gap-1 rounded-full border font-medium",
        compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]",
        tone,
      ].join(" ")}
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          act();
        }}
        title={badge.prompt}
        className="tech-transition inline-flex items-center gap-1 hover:opacity-80"
      >
        <Icon className="h-3 w-3 shrink-0" aria-hidden />
        {label}
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          updateItem(item.id, { keptMine: true });
        }}
        title="Keep this one mine — stop suggesting"
        aria-label="Dismiss suggestion — keep mine"
        className="tech-transition -mr-0.5 shrink-0 rounded-full p-0.5 hover:bg-black/10 dark:hover:bg-white/15"
      >
        <AppIcon name="X" className="h-2.5 w-2.5" />
      </button>
    </span>
  );
}
