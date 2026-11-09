/**
 * WS-39 LP-1a — the one line of the newest project update, on the Overview of
 * a project (`project_management_app.md` §14.2a).
 *
 * Pure and framework-free. `components/ProjectUpdateLine.tsx` draws what this
 * returns and decides nothing. The health's words, glyph and hue come from
 * `src/lib/statusAccent.ts` (`PROJECT_HEALTHS`), the one colour vocabulary.
 *
 * The line shows the health, the first line of the text, the author and the
 * day. The text opens the whole update in place. The update comes with the
 * node summary (`summary.latest_update`), so the line draws with the roll-up
 * and nothing moves when it arrives. No key (the flag is off) and null (no
 * update) draw nothing. The author picks the health by hand in LP-1a, so this
 * module computes no health.
 */

import { projectHealth } from "@/lib/statusAccent";

import type { ProjectUpdate } from "./api";

/** The longest text the line shows. A longer first line ends in "…". */
export const UPDATE_LINE_MAX = 140;

/** The month names of the day, as `outlook.shortDate` writes them. */
const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** A Markdown heading mark at the start of a line, and an optional closing run. */
const HEADING = /^\s{0,3}#{1,6}\s+/;
const HEADING_CLOSE = /\s+#+\s*$/;
/** A list or a quote mark at the start of a line. */
const LINE_MARK = /^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)/;

export type UpdateLine = {
  /** The stored value: `on_track`, `at_risk` or `off_track`. */
  health: string;
  /** What a person reads, such as "At risk". */
  label: string;
  /** The Lucide name of the health's glyph. */
  icon: string;
  /** The first line of real text, cut at {@link UPDATE_LINE_MAX}. */
  text: string;
  /** The whole text, which the line opens in place. */
  full: string;
  /** The author's address, as the session stamped it. */
  author: string;
  /** The day it was posted, in the reader's own time zone, as "8 Nov 2026". */
  date: string;
};

function cut(line: string, max: number): string {
  if (line.length <= max) return line;
  const part = line.slice(0, max);
  const space = part.lastIndexOf(" ");
  return `${(space > max / 2 ? part.slice(0, space) : part).trimEnd()}…`;
}

/**
 * The first line of *body* that holds real text, cut at a word near *max*.
 *
 * A heading line ("## Week 45") is a title, not the news, so the line skips
 * it and shows the first line of text below it. A body of headings only
 * shows the first heading, with no marks. A list or a quote mark at the
 * start of a line goes too, because the line is plain text.
 */
export function firstLine(body: string | null | undefined, max = UPDATE_LINE_MAX): string {
  let heading = "";
  for (const raw of String(body ?? "").split(/\r?\n/)) {
    if (!raw.trim()) continue;
    if (HEADING.test(raw)) {
      if (!heading) heading = raw.replace(HEADING, "").replace(HEADING_CLOSE, "").trim();
      continue;
    }
    const text = raw.replace(LINE_MARK, "").trim();
    if (text) return cut(text, max);
  }
  return cut(heading, max);
}

/**
 * The day of *iso* in *timeZone*, or in the reader's own zone when it is
 * absent: "8 Nov 2026". Never the UTC day, which is the wrong day for a
 * reader in India from 18:30 UTC on.
 */
export function updateDay(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "—";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "—";
  const parts = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "numeric",
    year: "numeric",
    ...(timeZone ? { timeZone } : {}),
  }).formatToParts(at);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  const month = MONTHS[Number(part("month")) - 1];
  return month ? `${Number(part("day"))} ${month} ${part("year")}` : "—";
}

/** The line of one update, or `null` when there is nothing to draw. */
export function projectUpdateLine(
  update: ProjectUpdate | null | undefined,
  timeZone?: string
): UpdateLine | null {
  if (!update) return null;
  const visual = projectHealth(update.health);
  // An unknown value comes from a newer server. Draw nothing rather than a
  // grey chip that names no health.
  if (!visual) return null;
  return {
    health: update.health,
    label: visual.label,
    icon: visual.icon,
    text: firstLine(update.body),
    full: String(update.body ?? "").trim(),
    author: String(update.author ?? ""),
    date: updateDay(update.posted_at, timeZone),
  };
}
