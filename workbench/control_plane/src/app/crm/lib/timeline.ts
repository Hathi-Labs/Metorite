// ── Timeline entry dispatch — pure, so the branch decision is testable ────
//
// `Timeline.tsx` renders three kinds of entry and cannot be render-tested here
// (vitest runs `environment: "node"` over `src/**/*.test.ts`; there is no jsdom
// and no `.tsx` in the include glob). The decision worth testing is therefore
// pulled out into this module, where it is plain data in and a string out.
//
// It exists because of a real trap. The dispatch used to be BINARY —
//
//     entry.kind === "status_change" ? <StatusEntry/> : <ActivityEntry/>
//
// — and `ActivityEntry` opens with `const activity = entry.activity!`. The `!`
// suppresses the null check, so tsc could not see that a third kind reaching
// that branch reads `.type` off null and throws at runtime. Adding a kind to
// the union in `types.ts` is therefore only half the change; the other half is
// a branch here, and `timeline.test.ts` is what says so out loud.

import type { MetaChip } from "@/lib/taskCard";
import { dueDay } from "./format";
import type { Activity, TimelineEntry } from "./types";

/** Which renderer an entry belongs to, or `"unknown"` if nothing can render it. */
export type TimelineBranch =
  | "activity"
  | "status_change"
  | "email_thread"
  | "unknown";

/**
 * Route one entry to its renderer.
 *
 * Every branch requires its OWN payload to be present, not merely the matching
 * `kind`. A `kind` with no payload is a server that changed shape under us, and
 * the honest answer to that is "I cannot render this" — never "render it as an
 * activity", which is the crash, and never an empty row, which looks like data
 * loss to whoever is reading the record.
 */
export function timelineBranch(entry: TimelineEntry): TimelineBranch {
  if (entry.kind === "status_change" && entry.status_change) return "status_change";
  if (entry.kind === "email_thread" && entry.email_thread) return "email_thread";
  if (entry.kind === "activity" && entry.activity) return "activity";
  return "unknown";
}

/**
 * A stable React key for one entry.
 *
 * The list index alone is not enough: entries arrive merged from three sources
 * and re-sorted, so a key that is only positional makes React reuse the wrong
 * DOM node when a new email lands. The index stays as the tail because two
 * different sources can legitimately produce entries with no id at all.
 */
export function timelineKey(entry: TimelineEntry, index: number): string {
  const id =
    entry.activity?.id ??
    entry.status_change?.id ??
    entry.email_thread?.thread_id ??
    index;
  return `${entry.kind}-${id}`;
}

/** The email app's per-thread statuses, in the words a human uses for them. */
const THREAD_STATUS_LABELS: Record<string, string> = {
  NEEDS_REPLY: "Needs reply",
  AWAITING: "Awaiting reply",
  FYI: "FYI",
  DONE: "Done",
};

/**
 * The status badge's text, or null when there is nothing to say.
 *
 * A thread nobody classified has no status, and an unrecognised one is shown
 * verbatim rather than dropped: the email app owns that vocabulary and may
 * grow it, and silently hiding a value we do not recognise is how a UI starts
 * disagreeing with its own data.
 */
export function threadStatusLabel(
  status: string | null | undefined
): string | null {
  const value = (status || "").trim();
  if (!value) return null;
  return THREAD_STATUS_LABELS[value.toUpperCase()] ?? value;
}

// ── The record page v2 (WS-53 CRM-X4, crm_platform.md section 19.8) ───────

/** The kinds a member can filter the stream to. "All" comes first. */
export const TIMELINE_FILTERS = [
  { id: "all", label: "All" },
  { id: "note", label: "Notes" },
  { id: "call", label: "Calls" },
  { id: "meeting", label: "Meetings" },
  { id: "task", label: "Tasks" },
  { id: "email", label: "Emails" },
  { id: "stage", label: "Stage changes" },
] as const;

export type TimelineFilter = (typeof TIMELINE_FILTERS)[number]["id"];

/**
 * The filter kind of one entry, or null when no filter but "All" keeps it.
 *
 * It reads the entry through `timelineBranch`, so an entry that no renderer
 * can draw is no kind at all. A status change counts twice over: the funnel
 * log's own entry and an activity of type `status_change` are both stage
 * changes.
 */
export function entryKind(entry: TimelineEntry): Exclude<TimelineFilter, "all"> | null {
  switch (timelineBranch(entry)) {
    case "status_change":
      return "stage";
    case "email_thread":
      return "email";
    case "activity": {
      const type = entry.activity?.type;
      if (type === "status_change") return "stage";
      if (type === "note" || type === "call" || type === "meeting" || type === "task") return type;
      return null;
    }
    default:
      return null;
  }
}

/** The entries of one kind. "All" keeps every entry, an entry of an unknown kind too. */
export function filterTimeline(entries: TimelineEntry[], kind: TimelineFilter): TimelineEntry[] {
  if (kind === "all") return entries;
  return entries.filter((entry) => entryKind(entry) === kind);
}

/** An instant as milliseconds, or NaN for no date and for a bad one. */
function ms(value: string | null | undefined): number {
  return value ? Date.parse(value) : Number.NaN;
}

/**
 * The sort key of an activity that is a next step, or null when it is not one.
 *
 * ⚠️ The same rule as `NEXT_ACTIVITY_SQL` in `routes/crm/core.py` (CRM-X3,
 * `crm_platform.md` section 19.7), clause by clause, so the Overview tab and
 * the card chip never disagree:
 *
 * - `completed_at IS NULL`;
 * - `type = 'task' AND due_at IS NOT NULL`, or
 *   `type IN ('call', 'meeting') AND occurred_at > now()`;
 * - the key is `COALESCE(due_at, occurred_at)`.
 *
 * No branch reads `ext_source`: a Zoho task and a native task count alike.
 * The cases of section 19.7 in `timeline.test.ts` hold it.
 */
function nextAt(activity: Activity, now: Date): number | null {
  if (activity.completed_at) return null;
  const isTask = activity.type === "task" && !Number.isNaN(ms(activity.due_at));
  const isComing =
    (activity.type === "call" || activity.type === "meeting") &&
    ms(activity.occurred_at) > now.getTime();
  if (!isTask && !isComing) return null;
  const key = Number.isNaN(ms(activity.due_at)) ? ms(activity.occurred_at) : ms(activity.due_at);
  return Number.isNaN(key) ? null : key;
}

/**
 * The next open activities of this record, soonest first, for the Overview
 * tab, by the rule of `nextAt`. A tie goes to the smaller id, compared as the
 * SQL compares it (ordinal, not by locale). An inherited entry of the lead is
 * history, not a next step: the SQL reads `deal_id` only.
 */
export function nextOpenActivities(
  entries: TimelineEntry[],
  now: Date = new Date(),
  limit = 3,
): TimelineEntry[] {
  return entries
    .filter((entry) => entry.origin !== "lead" && timelineBranch(entry) === "activity")
    .map((entry) => ({ entry, id: entry.activity!.id, at: nextAt(entry.activity!, now) }))
    .filter((row): row is { entry: TimelineEntry; id: string; at: number } => row.at !== null)
    .sort((a, b) => a.at - b.at || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    .slice(0, limit)
    .map((row) => row.entry);
}

/** "12 Oct" for a due day. The year shows only when it is not this year. */
function dueText(at: string, now: Date): string {
  // A due date sits at 00:00 UTC, so it reads in UTC, or a member west of
  // UTC sees the day before.
  const when = new Date(at);
  return when.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: when.getUTCFullYear() === now.getFullYear() ? undefined : "numeric",
    timeZone: "UTC",
  });
}

/**
 * The chips of a task's due date: the date, and "Overdue" in the `danger`
 * tone when the task is open and its due day is before today (`dueDay`).
 * `TaskMeta` draws them, so the CRM adds no class map of its own.
 */
export function taskDueChips(activity: Activity, now: Date = new Date()): MetaChip[] {
  if (activity.type !== "task" || !activity.due_at) return [];
  const day = dueDay(activity.due_at, true, now);
  if (day === null) return [];
  const chips: MetaChip[] = [
    {
      key: "due",
      icon: "Calendar",
      label: `Due ${dueText(activity.due_at, now)}`,
      tone: "muted",
      title: "The due date of this task",
    },
  ];
  if (!activity.completed_at && day < 0) {
    chips.push({
      key: "overdue",
      icon: "AlertCircle",
      label: "Overdue",
      tone: "danger",
      title: "This task was due before today",
    });
  }
  return chips;
}
