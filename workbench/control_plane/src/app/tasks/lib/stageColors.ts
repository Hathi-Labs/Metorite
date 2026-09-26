// /tasks · stage accents — now a thin adapter over the SHARED vocabulary.
//
// The palette, the keyword regexes and the positional fallback moved to
// `src/lib/statusAccent.ts` when /projects adopted them (WS-27ad): /projects'
// board columns were all one grey while this app's were colour-coded, which is
// two products, not one. The class strings are unchanged — the shared module's
// tests pin them byte-for-byte — so nothing here renders differently.
//
// What stays local is the one rule that is /tasks' and not /projects': the LAST
// configured stage is this app's Done stage (dropping a card there completes
// the task), so it reads green when nothing else has said otherwise. /projects
// must not inherit that — a lane's meaning there comes from its category, never
// from where it sits.

import { statusAccent, type StatusAccent } from "@/lib/statusAccent";

export type StageAccent = StatusAccent;

/** The accent for a stage given its name, index, and how many stages exist. */
export function stageAccent(
  name: string,
  index: number,
  total: number,
): StageAccent {
  return statusAccent({ name, index, total, lastIsDone: true });
}

/**
 * The accent for a Next Actions group or a card's pill: its status CATEGORY
 * (D73.9), through the shared `CATEGORY_HUES`, so a lane reads the same colour
 * here as on its Projects board.
 */
export function categoryAccent(category: string | null | undefined): StageAccent {
  return statusAccent({ category: category ?? "todo" });
}

/**
 * The accent for ONE task's own lane: the pill on a card and the Status
 * column. The lane's stored colour first, then its category, the order
 * Projects' `accentForStatus` uses. So a lane an owner coloured violet is
 * violet in both apps (AGENTS.md rule 5). Before this, the pill read the
 * category only, and a custom-coloured lane drew two colours.
 *
 * ⚠️ Only for a single lane. A Next Actions GROUP spans lanes from many
 * projects, so it keeps `categoryAccent`: no one stored colour speaks for it.
 */
export function laneAccent(
  lane: { statusColor?: string | null; statusCategory?: string | null },
  fallbackCategory?: string | null,
): StageAccent {
  return statusAccent({
    color: lane.statusColor,
    category: lane.statusCategory ?? fallbackCategory ?? "todo",
  });
}
