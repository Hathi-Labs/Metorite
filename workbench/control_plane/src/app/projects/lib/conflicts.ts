/**
 * WS-27bm S7c — the Conflicts panel's words, and nothing else.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §13.5 and §10.5 item 13.
 *
 * ⚠️ **No arithmetic lives here, and none may.** Every row, count and date
 * arrives finished from `GET /projects/analytics/conflicts`, which applies
 * the rules in `gateway/conflicts.py`. A count taken here would be a second
 * answer to "what is in conflict", and it would be the one on screen. This
 * file chooses words and hues, and reads the response defensively, as
 * `capacity.ts` does. `conflicts.test.ts` scans this file and the panel for
 * the arithmetic they must not hold.
 *
 * ⚠️ **The timeline keeps its own copy of the dependency rule**
 * (`timeline.ts` `conflicts()`), for live feedback while a bar is dragged.
 * This file does not hold a third one. It never decides whether a pair is
 * in conflict. It only says what the server decided.
 *
 * ⚠️ **The four HR kinds are ABSENT for a reader without
 * `admin:members:read`, not empty.** `hr_visible` says which answer arrived,
 * and the panel says so in one line instead of implying there are none.
 */
import type { AccentHue } from "@/lib/statusAccent";

import { asList } from "./analyticsRead";
import type { ConflictKind, ConflictRow, ConflictsReport } from "./api";

/** The seven kinds, in the order the server sorts them (§13.5 table). */
export const CONFLICT_KINDS: readonly ConflictKind[] = [
  "dependency_order",
  "blocker_late",
  "parallel_person",
  "overcommitted",
  "absent_on_due",
  "over_concurrency",
  "leaving",
];

/** What a person reads for each kind. */
export const KIND_LABEL: Record<ConflictKind, string> = {
  dependency_order: "Out of order",
  blocker_late: "Late blocker",
  parallel_person: "Parallel work",
  overcommitted: "Overcommitted",
  absent_on_due: "Away on the due date",
  over_concurrency: "Over the ceiling",
  leaving: "Leaves before the due date",
};

/**
 * Severity as a hue from the ONE status vocabulary (`statusAccent.ts`).
 *
 * `high` is `red`, the destructive token: a due date is at stake. `medium` is
 * `amber`, the warning token. Never a raw palette class (AGENTS.md rule 4).
 */
export const SEVERITY_HUE: Record<ConflictRow["severity"], AccentHue> = {
  high: "red",
  medium: "amber",
};

/** The rows, read at the boundary. A malformed or unknown row is dropped. */
export function conflictRows(
  data: ConflictsReport | null | undefined,
): ConflictRow[] {
  return asList<ConflictRow>(data?.rows).filter(
    (row): row is ConflictRow =>
      !!row &&
      typeof row === "object" &&
      CONFLICT_KINDS.includes(row.kind) &&
      typeof row.sentence === "string",
  );
}

/** The hue a row wears. An unknown severity reads as the milder one. */
export function severityHue(row: ConflictRow): AccentHue {
  return SEVERITY_HUE[row.severity] ?? SEVERITY_HUE.medium;
}

/** The people a row names, as a person reads them. */
export function rowPeople(row: ConflictRow): string | null {
  const names = asList<{ email: string; name: string | null }>(row.people)
    .filter((p) => p && typeof p === "object" && (p.name || p.email))
    .map((p) => p.name || p.email);
  return names.length > 0 ? names.join(", ") : null;
}

/**
 * The server's counts by kind, as words, in the server's order. A kind the
 * server did not send is not printed, so a hidden kind never reads as zero.
 */
export function countsLine(data: ConflictsReport): string {
  const counts = data?.by_kind ?? {};
  return CONFLICT_KINDS.filter((kind) => typeof counts[kind] === "number")
    .filter((kind) => (counts[kind] ?? 0) > 0)
    .map((kind) => `${counts[kind]} ${KIND_LABEL[kind].toLowerCase()}`)
    .join(" · ");
}

/**
 * The window, said once. The dated kinds read it and the dependency kinds do
 * not, and a reader who sees one date range assumes every row shares it.
 */
export function windowLine(data: ConflictsReport): string | null {
  const w = data?.window;
  if (!w?.starts_on || !w?.ends_on) return null;
  return (
    `Dated kinds: ${w.starts_on} to ${w.ends_on} (${w.days} days). ` +
    "Dependencies are checked whenever they fall."
  );
}

/** How many rows the panel draws before it says "of N". */
export const PANEL_ROWS = 12;

/** The note under a list the server capped, or under a list the panel cut. */
export function capNote(data: ConflictsReport, shown: number): string | null {
  if (typeof data?.total !== "number") return null;
  if (data.truncated || shown < data.total) {
    return `Showing ${shown} of ${data.total}.`;
  }
  return null;
}

/**
 * A report's conflict rows, as `RenderedBody` draws them: the kind, the
 * server's sentence and the severity hue. Capped at eight like every other
 * report section.
 */
export function conflictsReportRows(input: unknown): {
  key: string;
  label: string;
  sentence: string;
  hue: AccentHue;
}[] {
  return asList<ConflictRow>(input)
    .filter(
      (row): row is ConflictRow =>
        !!row &&
        typeof row === "object" &&
        CONFLICT_KINDS.includes(row.kind) &&
        typeof row.sentence === "string",
    )
    .slice(0, 8)
    .map((row, i) => ({
      key: `${row.kind}:${asList<string>(row.task_ids).join(",")}:${i}`,
      label: KIND_LABEL[row.kind],
      sentence: row.sentence,
      hue: severityHue(row),
    }));
}
