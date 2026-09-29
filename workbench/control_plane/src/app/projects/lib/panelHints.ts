/**
 * What each report section's panel means, in one sentence: the ONE source.
 *
 * The Reports UX pass (2026-09-29, `projects_reports.md` §6.5, item 10).
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

export const PANEL_HINTS: Readonly<Record<string, string>> = {
  finished: `${FINISHED_HINT_LEAD}, in the period.`,
  throughput:
    "Tasks finished each week, and how long they took from first started to done.",
  outlook:
    "Forecast from what the team actually did, against what the plan would need. Estimated — this product records no hours worked.",
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
