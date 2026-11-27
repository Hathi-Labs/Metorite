/**
 * Projects · the work style of a node (WS-39 SP-3a, `project_management_app.md`
 * §13.1, D195).
 *
 * The picker "How this team works" offers the five styles of the catalogue, in
 * this order. Flow is the default. Only the `live` ones can be picked today:
 * the other three show disabled, with their reason. A style becomes live when
 * its blocks ship, and that is a change of one flag here and of
 * `LIVE_WORK_STYLES` in `gateway/routes/projects/core.py`.
 *
 * The keys mirror `core.WORK_STYLES` in the same order.
 * `tests/unit/test_project_work_style.py` reads this file and holds the two
 * equal, so the picker cannot offer a key that the server refuses.
 *
 * Pure: no React and no network, so the rules are tests.
 */

import { type NodeLevel, showsDashboard } from "./tree";

export type WorkStyleKey = "flow" | "sprints" | "shape_up" | "plan" | "personal";

export interface WorkStyle {
  key: WorkStyleKey;
  /** The name in the picker. */
  label: string;
  /** Who it is for, in one line (§13.1, the second column). */
  hint: string;
  /** True when a member can pick it today. */
  live: boolean;
  /** Why a style that is not live cannot be picked. */
  reason?: string;
}

export const COMING_LATER = "Coming later";

export const WORK_STYLES: readonly WorkStyle[] = [
  {
    key: "flow",
    label: "Flow",
    hint: "A steady stream of work with no fixed dates.",
    live: true,
  },
  {
    key: "sprints",
    label: "Sprints or Cycles",
    hint: "Plan, build and review in a fixed time box. Adds the Backlog canvas.",
    live: true,
  },
  {
    key: "shape_up",
    label: "Shape Up cycles",
    hint: "Bet on shaped work in six-week cycles, with a cooldown.",
    live: false,
    reason: COMING_LATER,
  },
  {
    key: "plan",
    label: "Plan-driven",
    hint: "A fixed end date, with phases and milestones.",
    live: false,
    reason: COMING_LATER,
  },
  {
    key: "personal",
    label: "Personal",
    hint: "One person who runs their own work.",
    live: false,
    reason: COMING_LATER,
  },
];

export const DEFAULT_WORK_STYLE: WorkStyleKey = "flow";

/**
 * The style of a node. A row from an older gateway has no key, and a key this
 * client does not know reads as the default, so the page never draws a style
 * it cannot name.
 */
export function workStyleOf(node: { work_style?: string | null } | null | undefined): WorkStyleKey {
  const raw = node?.work_style;
  return WORK_STYLES.some((s) => s.key === raw) ? (raw as WorkStyleKey) : DEFAULT_WORK_STYLE;
}

/** The style entry of a key. */
export function workStyle(key: WorkStyleKey): WorkStyle {
  return WORK_STYLES.find((s) => s.key === key) ?? WORK_STYLES[0];
}

/** True for a node whose team works in sprints: the Backlog canvas may show. */
export function usesSprints(node: { work_style?: string | null } | null | undefined): boolean {
  return workStyleOf(node) === "sprints";
}

// A space or a folder draws its overview, with no tab row (`showsDashboard`),
// and no node inherits a style. So the words of those two levels say so.

/** The line under a style in the picker, true for the level of the node. */
export function styleHint(style: WorkStyle, level: NodeLevel): string {
  if (style.key !== "sprints" || !showsDashboard(level)) return style.hint;
  return `A ${level} shows no Backlog canvas, and the projects in it do not take this style.`;
}

/** The line under the title of the picker, after the name of the node. */
export function pickerNote(level: NodeLevel): string {
  return showsDashboard(level)
    ? `A change applies to this ${level} only and deletes nothing.`
    : "A change hides or shows parts of the project and deletes nothing.";
}

/** The words after a save. */
export function savedStyleTitle(name: string, key: WorkStyleKey, level: NodeLevel): string {
  const title = `${name} now works as ${workStyle(key).label}`;
  if (key !== "sprints" || !showsDashboard(level)) return title;
  return `${title}. A ${level} shows no Backlog canvas. Its projects keep their own style.`;
}

/**
 * Can the member pick this key? Only a live style. The server refuses the rest
 * with 422, and this is the courtesy in front of it.
 */
export function canPick(key: string): boolean {
  return WORK_STYLES.some((s) => s.key === key && s.live);
}
