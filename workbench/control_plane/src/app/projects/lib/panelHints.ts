/**
 * What each report section's panel means, in one sentence: the ONE source.
 *
 * The Reports UX pass (2026-09-29, `projects_reports.md` §6.5 item 4).
 * The panel in `AnalyticsPanels.tsx` prints the sentence under its title,
 * and the builder puts the same sentence on the section's checkbox as its
 * tooltip. Before, the builder had no words for a section, and a copy
 * there would drift from the panel.
 *
 * Two panels change their sentence with their data, and they build it from
 * these words. `finished` names the period (`FINISHED_HINT_LEAD`). `stuck`
 * names the blocked list only when the Analytics route sends one, and a
 * report never does, so the report's sentence is the one here.
 *
 * Fence: `panelHints.test.ts`.
 */

/** The start of the `finished` panel's sentence. The panel adds the dates. */
export const FINISHED_HINT_LEAD = "Completed by project";

/**
 * The short sentence of each section (`projects_reports.md` §6.6 C). The panel
 * prints it under its title, and the builder's section tile prints it under
 * the label. It is short on purpose: one line in a tile.
 */
export const PANEL_HINTS: Readonly<Record<string, string>> = {
  finished: `${FINISHED_HINT_LEAD}, in the period.`,
  throughput: "Weekly finishes, and the time they took.",
  outlook: "When the open work should finish.",
  load: "Open tasks per person, by due date.",
  capacity: "Open work, and the spare hours.",
  pulse: "Each person today, and who needs help.",
  stuck: "Stalled work, and work past due.",
  hygiene: "Tasks with no owner, date or size.",
  conflicts: "Late blockers, and people spread thin.",
  rebalance: "Tasks at risk, and who could help.",
};

/**
 * The longer sentence of each section, for a tooltip only (§6.6 D item 7).
 * These were the panel hints until R5f repair round 1.
 */
export const PANEL_HINT_DETAILS: Readonly<Record<string, string>> = {
  finished: "Tasks completed in the period, by project. Cancelled work is not counted as finished.",
  throughput:
    "Tasks finished each week, and how long they took from first started to done.",
  outlook:
    "Forecast from what the team actually did, against what the plan would need. Estimated, because this product records no hours worked.",
  load: "Open tasks per person, split by when they are due. Unassigned is a bar, not a gap.",
  capacity:
    "Open work per person in this scope, with the spare hours they have across all the work you can see.",
  pulse:
    "One card for each person who holds open work in this scope: the load, the status, the top focus tasks and the reasons to help. This is today, not the period.",
  stuck:
    "Open tasks by how long they have sat without a change, and what is past due.",
  hygiene:
    "Open tasks with no assignee, no due date or no estimate, and work in progress that has not changed. This is the state now, not the period.",
  conflicts:
    "Work that starts before its blocker is due, late blockers, and one person on too many projects at once. Nothing is rescheduled.",
  rebalance:
    "Tasks at risk of missing their due date, the people whose skills fit them, and idle people with work they could take. Nothing is reassigned.",
};
