// Small presentation helpers for the GTD task UI.

import { isOverdue as overdue } from "@/lib/taskCard";

import { Disposition, Energy, GtdItem, ProviderKind, Source } from "./types";

// `relativeTime`, `durationLabel` and `initials` now live in `@/lib/taskCard`,
// which /projects draws its cards from too (WS-27s) — one definition of "2d
// ago" and one of what an avatar's letters are, so the two surfaces a member
// moves between hour to hour cannot describe the same task differently.
// Re-exported rather than moved at every call site: seventeen files import
// them from here, and a rename that touches seventeen files to change nothing
// is a diff nobody can review.
export { durationLabel, initials, relativeTime } from "@/lib/taskCard";

/** True if a hard-date item is overdue. */
export function isOverdue(item: GtdItem, nowMs = Date.now()): boolean {
  return overdue(item.dueAt, item.completedAt, nowMs);
}

/** Milliseconds elapsed since an ISO timestamp (wall-clock now). */
export function msSince(iso: string, nowMs = Date.now()): number {
  return nowMs - new Date(iso).getTime();
}

function startOfDay(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export type DateBucketKey = "today" | "yesterday" | "week" | "older";

/** Which capture-date bucket an item falls into (for date filtering/grouping). */
export function dateBucket(
  iso: string,
  nowMs = Date.now(),
): { key: DateBucketKey; label: string } {
  const days = Math.round(
    (startOfDay(nowMs) - startOfDay(new Date(iso).getTime())) / 86_400_000,
  );
  if (days <= 0) return { key: "today", label: "Today" };
  if (days === 1) return { key: "yesterday", label: "Yesterday" };
  if (days < 7) return { key: "week", label: "Earlier this week" };
  return { key: "older", label: "Older" };
}

/** True if an item belongs in the Calendar view (date-specific actions). */
export function isCalendarItem(item: GtdItem): boolean {
  return !!item.isHardDate && !!item.dueAt;
}

export const DISPOSITION_LABEL: Record<Disposition, string> = {
  INBOX: "Inbox",
  NEXT: "Next action",
  WAITING: "Waiting for",
  SOMEDAY: "Someday / Maybe",
  PROJECT: "Project",
  REFERENCE: "Reference",
  DONE: "Done",
  TRASH: "Trash",
};

export const ENERGY_LABEL: Record<Energy, string> = {
  low: "Low energy",
  medium: "Medium energy",
  high: "High energy",
};

/** A short, capability-free label + dot-tone for the source/provider badge. */
export function sourceBadge(source: Source, provider?: ProviderKind): {
  label: string;
  tone: "local" | "synced";
} {
  if (source === "LOCAL") return { label: "Local", tone: "local" };
  const label =
    provider && provider !== "local"
      ? provider.charAt(0).toUpperCase() + provider.slice(1)
      : "Synced";
  return { label, tone: "synced" };
}

/** ClickUp's API returns status names lower-cased ("to do", "in progress");
 *  its UI shows them title-cased. Present them the way the tool does — capitalize
 *  each word's first letter, leaving the rest untouched (so an already-cased or
 *  ALL-CAPS acronym is preserved). Display only; the raw value is kept for
 *  matching and back-sync. */
export function formatStatus(status?: string): string {
  if (!status) return "";
  return status.replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Quick snooze/defer targets for the tickler, relative to now. */
export function snoozeOptions(nowMs = Date.now()): { label: string; iso: string }[] {
  const at = (d: Date, h = 9) => {
    d.setHours(h, 0, 0, 0);
    return d.toISOString();
  };
  const tomorrow = new Date(nowMs);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const weekend = new Date(nowMs);
  weekend.setDate(weekend.getDate() + ((6 - weekend.getDay() + 7) % 7 || 7)); // next Saturday
  const nextWeek = new Date(nowMs);
  nextWeek.setDate(nextWeek.getDate() + 7);
  return [
    { label: "Tomorrow", iso: at(tomorrow) },
    { label: "This weekend", iso: at(weekend) },
    { label: "Next week", iso: at(nextWeek) },
  ];
}

/** True if a deferred item is still tickled (resurface date in the future). */
export function isTickled(item: { deferUntil?: string }, nowMs = Date.now()): boolean {
  return !!item.deferUntil && new Date(item.deferUntil).getTime() > nowMs;
}

/** A short "where it already lives" label for a duplicate/similar match — the
 *  feedback the user needs to decide keep/create/skip. Combines WHAT the match
 *  is (its GTD disposition) with WHERE it lives (local vs a PM tool / ClickUp).
 *  e.g. "a Next action on ClickUp", "in your inbox". */
export function matchWhere(disposition?: string, source?: string): string {
  const onClickUp = !!source && source !== "LOCAL";
  const kind =
    disposition === "INBOX" ? "in your inbox"
      : disposition === "NEXT" ? "a Next action"
      : disposition === "WAITING" ? "a Waiting-for item"
      : disposition === "SOMEDAY" ? "in Someday / Maybe"
      : disposition === "PROJECT" ? "a Project"
      : disposition === "REFERENCE" ? "in Reference"
      : "in your list";
  // "in your inbox" already reads oddly with "on ClickUp"; only append the
  // location for the disposition phrases that take it cleanly.
  if (!onClickUp) return kind;
  return kind.startsWith("in ") ? `${kind} (on ClickUp)` : `${kind} on ClickUp`;
}

/** A lightweight, local date-phrase detector — the seam where an AI capture
 *  parser will later suggest a defer/due date. Suggestion only; never acts. */
export function detectDateHint(title: string): string | null {
  const t = title.toLowerCase();
  const words = [
    "today",
    "tomorrow",
    "tonight",
    "this weekend",
    "next week",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
  ];
  const hit = words.find((w) => t.includes(w));
  return hit ?? null;
}

/** Deep link to the source email of an email-origin item (the email app
 *  reads ?account= at hydrate and ?email= on mount). */
export function originEmailHref(origin?: {
  kind: string;
  accountId?: string;
  emailId?: string;
}): string | null {
  if (!origin || origin.kind !== "email" || !origin.emailId) return null;
  const params = new URLSearchParams();
  if (origin.accountId) params.set("account", origin.accountId);
  params.set("email", origin.emailId);
  return `/email?${params.toString()}`;
}

/**
 * "2:05 PM" — the product's one clock format.
 *
 * ⚠️ Promoted here 2026-08-24 (D54, board WS-39 S2). It lived in the calendar's
 * `shared.ts`, and `FocusMode` — which `AppShell` mounts GLOBALLY, so it is not
 * a Tasks-only component — imported it across the app boundary. When Calendar
 * became its own app that import pointed the wrong way: Tasks may not depend on
 * Calendar (`calendarBoundary.test.ts` is the fence).
 *
 * It lands in `tasks/lib` rather than a new `src/lib` module because this is
 * already the shared formatter module both surfaces consume (`durationLabel` is
 * right above). When S3a re-points the store, this whole directory moves with
 * it — one move, not two.
 */
export const fmtClock = (d: Date) =>
  d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
