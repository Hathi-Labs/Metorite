/**
 * My Tasks · which acts a card's right-click menu offers, and in what order
 * (continuity P3, item 6).
 *
 * The shared acts take their order from `@/lib/taskMenuVocabulary`, the list
 * the Projects registry follows. The acts only My Tasks offers ("Schedule on
 * calendar", "Eliminate…") come after them. A separator falls between two
 * blocks, never at either end and never twice, the rule Projects'
 * `taskMenuItems` keeps.
 *
 * ⚠️ "Change status" still runs `setCategory`, as it did before. This file
 * moves where the block sits and not what it does.
 */

import { inTaskMenuOrder, type SharedTaskAct } from "@/lib/taskMenuVocabulary";

/** The acts only My Tasks offers. They sit after every shared act. */
export const MY_TASK_ONLY_ACTS = ["schedule", "eliminate"] as const;

export type CardMenuAct =
  | Extract<SharedTaskAct, "markDone" | "moveToProject" | "status">
  | (typeof MY_TASK_ONLY_ACTS)[number];

/**
 * The menu block each act draws in. The shared blocks match the groups of the
 * Projects registry (done 0, place 2, state 3). My Tasks' own acts are one
 * block after them.
 */
const BLOCK: Record<CardMenuAct, number> = {
  markDone: 0,
  moveToProject: 2,
  status: 3,
  schedule: 10,
  eliminate: 10,
};

export type CardMenuEntry = CardMenuAct | "sep";

/** The card menu, top to bottom, with its separators. */
export function cardMenuActs(ctx: { canPromote: boolean }): CardMenuEntry[] {
  const shared = inTaskMenuOrder<CardMenuAct>([
    "status",
    "markDone",
    ...(ctx.canPromote ? (["moveToProject"] as const) : []),
  ]);
  const acts: CardMenuAct[] = [...shared, ...MY_TASK_ONLY_ACTS];
  const out: CardMenuEntry[] = [];
  acts.forEach((act, at) => {
    if (at > 0 && BLOCK[acts[at - 1]] !== BLOCK[act]) out.push("sep");
    out.push(act);
  });
  return out;
}
