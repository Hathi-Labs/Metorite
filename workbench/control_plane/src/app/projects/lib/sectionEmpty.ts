/**
 * When a report section is all clear, and the one friendly line it says.
 *
 * `projects_reports.md` §6.6 D. The owner found an all-clear report that
 * looked like three error boxes. So an empty section is one compact row, and
 * a report whose sections are all empty is one calm card.
 *
 * ⚠️ **"All clear" is a claim about the scope, so it is strict.** A section
 * whose rows the server removed for this reader (`hidden_people` above zero)
 * is NOT clear: the work exists, and the reader may not see it. A rebalance
 * section without the HR grant is not clear either, because it holds no
 * lists at all. Those sections draw their panel, which says why.
 *
 * The app, the email and the download read these lines. A second copy of a
 * line is a defect. Fence: `reportsRedesign.test.ts`.
 */
import type { RenderedReportBody } from "./api";

type Sections = RenderedReportBody["sections"];

/** The friendly line of each section when it is clear. Short and active. */
export const SECTION_CLEAR_LINES: Readonly<Record<string, string>> = {
  finished: "Nothing finished in this period yet.",
  throughput: "No finished work to time yet.",
  outlook: "No open work to forecast.",
  load: "Nobody holds open work here right now.",
  capacity: "Nobody holds open work here right now.",
  pulse: "No open work for anyone here right now.",
  stuck: "Nothing is stuck.",
  hygiene: "The data looks tidy.",
  conflicts: "No conflicts. The plan lines up.",
  rebalance: "Nothing is at risk, and nobody is idle.",
};

function hides(section: unknown): boolean {
  const n = (section as { hidden_people?: unknown } | null)?.hidden_people;
  return typeof n === "number" && n > 0;
}

function count(list: unknown): number {
  return Array.isArray(list) ? list.length : 0;
}

/**
 * True when a section has nothing to show and hides nothing from the reader.
 * An unknown key or an absent section is never clear, so the panel draws.
 */
export function sectionIsClear(key: string, section: unknown): boolean {
  if (section === undefined || section === null || hides(section)) return false;
  const s = section as Record<string, unknown>;
  switch (key) {
    case "finished":
      return s.total_completed === 0 && (s.total_cancelled ?? 0) === 0;
    case "throughput":
      return (
        s.measured === 0 &&
        (Array.isArray(s.series) ? s.series : []).every(
          (w: { completed?: number }) => (w?.completed ?? 0) === 0
        )
      );
    case "outlook": {
      const v = (s.velocity ?? {}) as { remaining_tasks?: unknown };
      return v.remaining_tasks === 0;
    }
    case "load":
    case "capacity":
      return s.total_tasks === 0 && count(s.people) === 0;
    case "pulse":
      return count(s.rows) === 0 && (s.people_total ?? 0) === 0;
    case "stuck": {
      // A body with no `stale` key did not send the bands (before R3a). That
      // is "not said", never zero, so the section is not clear.
      if (!Array.isArray(s.stale)) return false;
      const bands = s.stale as { n?: number }[];
      return (
        s.overdue_total === 0 &&
        (s.blocked_total ?? 0) === 0 &&
        bands.every((b) => (b?.n ?? 0) === 0)
      );
    }
    case "hygiene": {
      const kinds = Object.values((s.by_kind ?? {}) as Record<string, number>);
      return kinds.every((n) => n === 0) && count(s.rows) === 0;
    }
    case "conflicts":
      // R5f round 2 (item 2). Without the HR grant the server sends only the
      // task kinds, so "No conflicts" would be a claim about kinds it held
      // back. The section draws its panel, which says why.
      return s.hr_visible !== false && s.total === 0 && count(s.rows) === 0;
    case "rebalance":
      return (
        s.hr_visible !== false &&
        count(s.at_risk) === 0 &&
        count(s.pickups) === 0 &&
        (s.idle_total ?? 0) === 0
      );
    default:
      return false;
  }
}

/** The friendly line of a section, for a clear row or an email note. */
export function clearLine(key: string): string {
  return SECTION_CLEAR_LINES[key] ?? "Nothing to show here.";
}

/** The note an email or a download adds under a clear section: one line, or none. */
export function clearNote(key: string, section: unknown): string[] {
  return sectionIsClear(key, section) ? [clearLine(key)] : [];
}

/**
 * The keys of the sections a body holds, in its order, and whether every one
 * of them is clear. An empty body is not "all clear": it says nothing.
 */
export function allClear(sections: Sections): { clear: boolean; keys: string[] } {
  const keys = Object.entries(sections)
    .filter(([, body]) => body !== undefined && body !== null)
    .map(([key]) => key);
  return {
    keys,
    clear: keys.length > 0 && keys.every((k) => sectionIsClear(k, (sections as Record<string, unknown>)[k])),
  };
}
