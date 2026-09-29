/**
 * The icon of each report section: the ONE map (`projects_reports.md` §6.6 C).
 *
 * The builder's section tiles, the section headers of `RenderedBody` and the
 * all-clear rows all read it. A second map is a defect, because two maps
 * drift and one section then wears two icons.
 *
 * The names are Lucide names, and `<Icon name>` draws them (AGENTS.md rule 2).
 * Fence: `reportsRedesign.test.ts` holds this map equal to `REPORT_SECTIONS`.
 */
export const SECTION_ICONS: Readonly<Record<string, string>> = {
  finished: "CircleCheckBig",
  throughput: "Timer",
  outlook: "Telescope",
  load: "Layers",
  capacity: "Gauge",
  pulse: "HeartPulse",
  stuck: "Hourglass",
  hygiene: "Sparkles",
  conflicts: "GitMerge",
  rebalance: "HandHelping",
};

/** The icon of a section, or a neutral one for a key this client does not know. */
export function sectionIcon(key: string): string {
  return SECTION_ICONS[key] ?? "FileText";
}
