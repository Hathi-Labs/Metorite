// Lucide icons for the priority levels and the action-mode suggestions — the
// UI-layer counterpart to priority.ts (which stays pure data, no JSX imports).
// One mapping so the priority icon reads the same everywhere it appears: the
// card PriorityBadge, the list Priority/Suggestion columns, and the grouped
// section headers. Replaces the old emoji so the pills match the app's lucide
// icon language.

import { themedIcon } from "@/components/Icon";
import type { ThemedIcon } from "@/components/Icon";
import { CELL_ICON_NAME, type ActionMode, type PriorityCell } from "./priority";

/** Priority level → icon. Matches CELL_META's order/meaning:
 *  🔥 critical, 🚨 urgent, 📈 high-leverage, ❗ important,
 *  🚀 quick-leverage, 🧪 speculative-bet, ↓ low-priority. */
export const CELL_ICON = Object.fromEntries(
  Object.entries(CELL_ICON_NAME).map(([cell, name]) => [cell, themedIcon(name)]),
) as Record<PriorityCell, ThemedIcon>;

/** Action-mode / suggestion → icon: 🎯 do, 🙋 delegate (hand to a person),
 *  📅 schedule, 🚫 drop (eliminate/ignore). Shared by the Suggestion column,
 *  the card suggestion nudge, and the mode group headers. */
export const MODE_ICON: Record<ActionMode, ThemedIcon> = {
  do: themedIcon("Target"),
  delegate: themedIcon("UserPlus"),
  schedule: themedIcon("CalendarClock"),
  drop: themedIcon("Ban"),
};

/** The icon for a grouped section header, given the grouping axis and the
 *  group key. Only the priority and mode axes carry an icon (their keys are the
 *  PriorityCell / ActionMode); energy and context headers use their own marker. */
export function groupIcon(
  by: "priority" | "mode" | "energy" | "context" | "none" | string,
  key: string,
): ThemedIcon | null {
  if (by === "priority") return CELL_ICON[key as PriorityCell] ?? null;
  if (by === "mode") return MODE_ICON[key as ActionMode] ?? null;
  return null;
}
