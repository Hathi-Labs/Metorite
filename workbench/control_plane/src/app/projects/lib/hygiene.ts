/**
 * WS-27bn R3c — the Hygiene panel's words, and the rows of each kind.
 *
 * Spec: `project-docs/specs/projects_reports.md` §8 R3c.
 *
 * ⚠️ **The server counts. This file does not.** `by_kind`, `open_total` and
 * the rows arrive from `analytics.py` `hygiene_body`. The one subtraction
 * here is {@link moreNote}: how many titles the panel did not print, from
 * the server's count and the rows on screen. It is a display count, as a
 * bar width is, and never a figure a report states.
 *
 * ⚠️ **A task counts in each kind that it breaks.** So the four counts do
 * not add up to `open_total`, and {@link OVERLAP_NOTE} says so.
 */
import { asList } from "./analyticsRead";
import type { HygieneKind, HygieneReport, HygieneRow } from "./api";

/** How many titles the panel prints under each bar. */
export const TITLES_SHOWN = 5;

/** The four kinds, in the server's order, with their words. */
export const HYGIENE_KINDS: readonly {
  kind: HygieneKind;
  label: string;
  /** What the count means. `stale_days` fills `{days}`. */
  title: string;
}[] = [
  {
    kind: "no_assignee",
    label: "No assignee",
    title: "Open tasks that nobody holds. An agent counts as an assignee.",
  },
  {
    kind: "no_due_date",
    label: "No due date",
    title: "Open tasks with no due date, so no plan or forecast can place them.",
  },
  {
    kind: "no_estimate",
    label: "No estimate",
    title:
      "Open tasks with no shared estimate. A private estimate in My Tasks does not count.",
  },
  {
    kind: "stale_in_progress",
    label: "Stale in progress",
    title: "Tasks in progress with no change for {days} days or more.",
  },
];

/** The line that says why the counts do not add up. */
export const OVERLAP_NOTE =
  "A task can miss more than one thing, so the counts do not add up to the open total.";

/** The server's count of one kind, or undefined when it did not send one. */
export function hygieneCount(
  data: HygieneReport | null | undefined,
  kind: HygieneKind
): number | undefined {
  const n = data?.by_kind?.[kind];
  return typeof n === "number" && Number.isFinite(n) ? n : undefined;
}

/** The rows of one kind, in the server's order. */
export function hygieneRows(
  data: HygieneReport | null | undefined,
  kind: HygieneKind
): HygieneRow[] {
  return asList<HygieneRow>(data?.rows).filter(
    (r): r is HygieneRow =>
      !!r && typeof r === "object" && r.kind === kind && typeof r.title === "string"
  );
}

/** A kind's tooltip, with the server's stale days in it. */
export function kindTitle(title: string, data: HygieneReport | null | undefined): string {
  const days = data?.stale_days;
  return title.replace("{days}", typeof days === "number" ? String(days) : "several");
}

/** "…and 18 more", or null when every task of the kind is on screen. */
export function moreNote(count: number | undefined, shown: number): string | null {
  if (typeof count !== "number" || count <= shown) return null;
  return `…and ${count - shown} more`;
}
