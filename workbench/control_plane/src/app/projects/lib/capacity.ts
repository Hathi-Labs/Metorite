/**
 * WS-27bm S7a — the Capacity panel's words, and nothing else.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §13.3 and §10.3 item 8.
 *
 * ⚠️ **No arithmetic lives here, and none may.** Every figure arrives
 * finished from `GET /projects/analytics/capacity`, which computes it with
 * `gateway/capacity.py` — the module the People dashboard reads too. A sum
 * or a difference taken here would be a second answer to "does this person
 * have the hours", and it would be the one on screen. This file chooses
 * words and reads the response defensively, as `analyticsRead.ts` does.
 * `capacity.test.ts` scans this file for the arithmetic it must not hold.
 *
 * ⚠️ **An absent HR key is not zero.** Without `admin:members:read` the
 * server omits the hours entirely. With the grant it still omits committed
 * and spare hours when nothing is estimated. Both cases must read as "not
 * known", never as "free", so every reader here tests for the key.
 */
import { hours } from "@/app/people/lib/dashboard";

import type { CapacityReport, CapacityRow } from "./api";
import { asList } from "./analyticsRead";

/** The rows, read at the boundary. A malformed entry is dropped, not drawn. */
export function capacityRows(data: CapacityReport | null | undefined): CapacityRow[] {
  return asList<CapacityRow>(data?.rows).filter(
    (row): row is CapacityRow => !!row && typeof row === "object"
  );
}

/** Who a row is, as a person reads it. */
export function rowLabel(row: CapacityRow): string {
  if (row.kind === "unassigned" || !row.assignee) return "Unassigned";
  // An agent keeps its address, `agent:<name>`, as the Load panel beside
  // this one prints it. A bare name would read as a colleague.
  if (row.kind === "agent") return row.assignee;
  return row.name || row.assignee;
}

/** Whether the server sent this row's HR half at all. */
export function hasHrHalf(row: CapacityRow): boolean {
  return !!row.all_work && typeof row.all_work === "object";
}

/**
 * The hours line under a row, or `null` when the row carries no HR half.
 *
 * The spare figure and the working time are the server's, verbatim. When
 * the server says the hours mean nothing (`hours_basis` false), the line
 * says so in its own words and prints no number.
 */
export function hoursLine(
  row: CapacityRow,
  horizonDays: number
): { text: string; title: string } | null {
  if (!hasHrHalf(row)) return null;
  const spare = row.spare_hours_horizon;
  if (row.hours_basis && typeof spare === "number") {
    return {
      text: `${hours(spare)} spare`,
      title:
        `${hours(spare)} spare in the next ${horizonDays} days: ` +
        `${hours(row.working_hours_horizon)} of working time, ` +
        `${hours(row.committed_hours_horizon)} committed. ` +
        "Measured over all the work you can see, and estimated, never logged.",
    };
  }
  return {
    text: "No hours",
    title:
      row.hours_note ||
      "No open task carries an estimate, so there are no committed or spare hours.",
  };
}

/**
 * The extra facts a row earns: away, leaving, over its ceiling.
 *
 * `today` is the horizon's first day as the SERVER sent it, never the
 * browser's clock. An end date before it has already passed, so the row
 * says "Left", not "Leaves". ISO dates compare as strings.
 */
export function rowWarnings(row: CapacityRow, today?: string): string[] {
  const out: string[] = [];
  const away = asList<{ kind: string; starts_on: string; ends_on: string }>(
    row.absences
  );
  if (away.length > 0) {
    const first = away[0];
    out.push(`Away ${first.starts_on} to ${first.ends_on}`);
  }
  if (row.leaving_in_window && row.end_date) {
    const past = !!today && row.end_date < today;
    out.push(`${past ? "Left" : "Leaves"} ${row.end_date}`);
  }
  if (row.over_concurrency && typeof row.max_concurrent_tasks === "number") {
    out.push(`Over a ceiling of ${row.max_concurrent_tasks} in progress`);
  }
  return out;
}

/** The skill names a row carries, strongest first as the server sent them. */
export function skillLine(row: CapacityRow, limit = 3): string | null {
  const skills = asList<{ skill: string; level: string | null }>(row.skills)
    .filter((s) => s && typeof s.skill === "string" && s.skill.trim())
    .slice(0, limit);
  if (skills.length === 0) return null;
  return skills
    .map((s) => (s.level ? `${s.skill} (${s.level})` : s.skill))
    .join(", ");
}

/**
 * Both windows, said once each. The pill reads the Monday-to-Sunday week,
 * and the spare hours read the horizon. A reader who sees only one date
 * range assumes both figures share it.
 */
export function windowsLine(data: CapacityReport): string {
  const week = data.windows?.week;
  const horizon = data.windows?.horizon;
  const parts: string[] = [];
  if (horizon) {
    parts.push(
      `Spare hours: ${horizon.starts_on} to ${horizon.ends_on} (${horizon.days} days)`
    );
  }
  if (week) parts.push(`Pill: week of ${week.starts_on} to ${week.ends_on}`);
  return parts.join(" · ");
}

/**
 * A report's capacity rows, as `RenderedBody` draws them: a name, the open
 * count in scope, and the spare hours or the reason there are none. Capped
 * at eight named rows like every other report section, and the unassigned
 * row survives the cap, as the server's own cap keeps it.
 */
export function capacityReportRows(input: unknown): {
  key: string;
  name: string;
  open: number;
  aside?: string;
  title: string;
}[] {
  const rows = asList<CapacityRow>(input).filter(
    (row): row is CapacityRow => !!row && typeof row === "object"
  );
  const named = rows.filter((row) => row.kind !== "unassigned").slice(0, 8);
  const nobody = rows.filter((row) => row.kind === "unassigned");
  return [...named, ...nobody].map((row) => {
    const spare = row.spare_hours_horizon;
    const aside =
      !hasHrHalf(row)
        ? undefined
        : row.hours_basis && typeof spare === "number"
          ? `${hours(spare)} spare`
          : "no hours";
    return {
      key: row.assignee ?? "__unassigned",
      name: rowLabel(row),
      open: row.open_tasks,
      aside,
      title:
        `${row.open_tasks} open for ${rowLabel(row)}` +
        (aside === "no hours" && row.hours_note ? `. ${row.hours_note}` : ""),
    };
  });
}
