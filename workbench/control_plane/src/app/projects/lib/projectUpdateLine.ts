/**
 * WS-39 LP-1a — the one line of the newest project update, on the Overview of
 * a project (`project_management_app.md` §14.2a).
 *
 * Pure and framework-free. `components/ProjectUpdateLine.tsx` draws what this
 * returns and decides nothing. The health's words, glyph and hue come from
 * `src/lib/statusAccent.ts` (`PROJECT_HEALTHS`), the one colour vocabulary.
 *
 * The line shows the health, the first line of the text, the author and the
 * date. It shows nothing while the read fails, while the flag is off (the
 * route answers 404), and while the project has no update. The author picks
 * the health by hand in LP-1a, so this module computes no health.
 */

import { projectHealth } from "@/lib/statusAccent";

import type { ProjectUpdatesPage } from "./api";
import { shortDate } from "./outlook";

/** The longest text the line shows. A longer first line ends in "…". */
export const UPDATE_LINE_MAX = 140;

export type UpdateLine = {
  /** The stored value: `on_track`, `at_risk` or `off_track`. */
  health: string;
  /** What a person reads, such as "At risk". */
  label: string;
  /** The Lucide name of the health's glyph. */
  icon: string;
  /** The first line of the text, cut at {@link UPDATE_LINE_MAX}. */
  text: string;
  /** The whole text, for the tooltip. */
  full: string;
  /** The author's address, as the session stamped it. */
  author: string;
  /** The day it was posted, as "8 Nov 2026". */
  date: string;
};

/**
 * The first line of *body* that holds text, cut at a word near *max*.
 *
 * Markdown heading and list marks at the start of the line go, because the
 * line is plain text and "## Summary" reads as noise there.
 */
export function firstLine(body: string | null | undefined, max = UPDATE_LINE_MAX): string {
  const line =
    String(body ?? "")
      .split(/\r?\n/)
      .map((l) => l.replace(/^\s*(?:#{1,6}\s+|[-*+]\s+|>\s*)/, "").trim())
      .find((l) => l.length > 0) ?? "";
  if (line.length <= max) return line;
  const cut = line.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max / 2 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** The line of the newest update, or `null` when there is nothing to draw. */
export function projectUpdateLine(
  page: ProjectUpdatesPage | null | undefined
): UpdateLine | null {
  const newest = page?.rows?.[0];
  if (!newest) return null;
  const visual = projectHealth(newest.health);
  // An unknown value comes from a newer server. Draw nothing rather than a
  // grey chip that names no health.
  if (!visual) return null;
  const text = firstLine(newest.body);
  return {
    health: newest.health,
    label: visual.label,
    icon: visual.icon,
    text,
    full: String(newest.body ?? "").trim(),
    author: String(newest.author ?? ""),
    date: shortDate(newest.posted_at),
  };
}
