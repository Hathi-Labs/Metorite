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
