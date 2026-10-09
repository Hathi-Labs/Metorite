/**
 * My Day's rules, all pure (`navigation_shell.md` §4.3 to §4.5, NS-3).
 *
 * My Day has ONE job: notice. It answers "What needs me today?" in a few
 * seconds and hands every longer act to the app that owns it. Planning goes
 * to Calendar, lists go to My Tasks (§4.3).
 *
 * Everything that decides what the page shows is here, so each rule is an
 * assertion and not a click: the greeting, the summary, the groups, the
 * seven-row cut, the Next actions dedupe and which cards a member gets.
 * Fence: `myDay.test.ts`.
 */
import { NAV_SECTIONS } from "@/lib/nav";
import { relativeTime } from "@/lib/taskCard";

import { KIND_ORDER, type NeedsApp, type NeedsItem, type NeedsKind, type SourceState } from "./needs";

// ── The header ──────────────────────────────────────────────────────────────

/** "Good morning" before noon, "Good afternoon" before six, then evening. */
export function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/**
 * The first name to greet. A display name gives its first word. An address
 * gives nothing, because "Good morning, vijay.r" reads worse than no name.
 */
export function firstName(name: string | null | undefined): string | null {
  const raw = (name ?? "").trim();
  if (!raw || raw.includes("@")) return null;
  return raw.split(/\s+/)[0] || null;
}

/** "Good morning, Vijay", or "Good morning" with no name. */
export function greetingLine(now: Date, name: string | null | undefined): string {
  const first = firstName(name);
  return first ? `${greeting(now.getHours())}, ${first}` : greeting(now.getHours());
}

const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "Thursday, 9 October". Built by hand so no locale moves the comma. */
export function dateLine(now: Date): string {
  return `${WEEKDAYS[now.getDay()]}, ${now.getDate()} ${MONTHS[now.getMonth()]}`;
}

const things = (n: number) => (n === 1 ? "1 thing" : `${n} things`);

/**
 * The one summary sentence. `today` is `null` when the member has no
 * calendar (no My Tasks), so the sentence does not claim an empty one.
 *
 * `partial` is true when a source did not answer. Then "nothing" is only
 * true of the apps that did, and the sentence says so.
 *
 * ⚠️ When nothing needs the member, the sentence does not repeat the card's
 * own line ("Nothing needs you right now."). Two identical lines 100px apart
 * read as a fault.
 */
export function summaryLine(needs: number, today: number | null, partial = false): string {
  const cal =
    today === null || today === 0
      ? null
      : today === 1
        ? "1 is on your calendar today"
        : `${today} are on your calendar today`;
  if (needs > 0) {
    const head = `${things(needs)} ${needs === 1 ? "needs" : "need"} you`;
    return cal ? `${head}, and ${cal}.` : `${head}.`;
  }
  const nothing = partial ? "Nothing needs you in the apps that answered" : "Nothing needs you";
  if (cal) return `${nothing}. ${things(today!)} ${today === 1 ? "is" : "are"} on your calendar today.`;
  if (today === 0) return `${nothing}, and your calendar is clear.`;
  return partial ? `${nothing}.` : `${nothing} right now.`;
}

/** The Needs you card's empty line. A failed source makes it say less. */
export function emptyNeedsLine(partial: boolean): string {
  return partial ? "Nothing needs you in the apps that answered." : "Nothing needs you right now.";
}

// ── Needs you ───────────────────────────────────────────────────────────────

/** The small plain label over each group, by kind. */
export const KIND_LABELS: Readonly<Record<NeedsKind, string>> = {
  overdue: "Overdue",
  due_today: "Due today",
  notification: "From your projects",
  needs_reply: "Waiting for your reply",
};

/** Rows shown before "Show all". Seven is what the eye takes in one look. */
export const NEEDS_SHOWN = 7;

export interface NeedsGroup {
  kind: NeedsKind;
  label: string;
  items: NeedsItem[];
}

/**
 * Group rows under their labels, in the server's kind order. The order
 * inside a group is the server's too: the shell sorts nothing again.
 */
export function groupNeeds(items: readonly NeedsItem[]): NeedsGroup[] {
  return KIND_ORDER.map((kind) => ({
    kind,
    label: KIND_LABELS[kind],
    items: items.filter((i) => i.kind === kind),
  })).filter((g) => g.items.length > 0);
}

/**
 * The rows to draw: the first seven, or all of them once the member asks.
 * The cut runs over the whole feed, then groups, so seven means seven rows
 * and not seven per group.
 */
export function shownNeeds(
  items: readonly NeedsItem[],
  expanded: boolean,
  limit = NEEDS_SHOWN,
): { groups: NeedsGroup[]; hidden: number } {
  const shown = expanded ? items : items.slice(0, limit);
  return { groups: groupNeeds(shown), hidden: items.length - shown.length };
}

/**
 * A row's time, in muted words. A due row says when it was or is due, so
 * "Due 2d ago" and "Due in 3h". Any other row says how long ago it came.
 */
export function rowTime(item: Pick<NeedsItem, "kind" | "at">, nowMs = Date.now()): string {
  const rel = relativeTime(item.at, nowMs);
  if (!rel) return "";
  return item.kind === "overdue" || item.kind === "due_today" ? `Due ${rel}` : rel;
}

/** The short error a row shows when its act did not go through. */
export function actError(act: NeedsItem["act"]): string {
  return act === "read" ? "Could not mark it read. Try again." : "Could not mark it done. Try again.";
}

/** The member-facing name of each source app. */
export const APP_NAMES: Readonly<Record<NeedsApp, string>> = {
  tasks: "My Tasks",
  projects: "Projects",
  email: "Email",
};

const APP_HREFS: Readonly<Record<NeedsApp, string>> = {
  tasks: "/tasks",
  projects: "/projects",
  email: "/email",
};

/** A row's icon: its app's own icon from the manifest, never a second list. */
export function appIcon(app: NeedsApp): string {
  const pane = NAV_SECTIONS.flatMap((s) => s.items).find((p) => p.href === APP_HREFS[app]);
  return pane?.icon ?? "Circle";
}

/** One muted line per source that failed: "Email did not answer. …". */
export function failedLines(sources: Partial<Record<NeedsApp, SourceState>>): string[] {
  return (Object.keys(APP_NAMES) as NeedsApp[])
    .filter((app) => sources[app] === "failed")
    .map((app) => `${APP_NAMES[app]} did not answer. Showing the rest.`);
}

// ── Next actions ────────────────────────────────────────────────────────────

/** Next actions shown. Five is a short list, which is the point. */
export const NEXT_SHOWN = 5;

/**
 * The task ids the feed already shows. A task row names it in `act_ref`, and
 * a notification names its task in the `task` value of its link.
 */
export function needsTaskIds(items: readonly NeedsItem[]): Set<string> {
  const ids = new Set<string>();
  for (const item of items) {
    if (item.app === "tasks" && item.act_ref) ids.add(item.act_ref);
    const linked = taskInHref(item.href);
    if (linked) ids.add(linked);
  }
  return ids;
}

function taskInHref(href: string): string | null {
  const at = href.indexOf("?");
  if (at === -1) return null;
  return new URLSearchParams(href.slice(at + 1)).get("task");
}

/**
 * The first five next actions, minus any task Needs you already shows.
 * One task in two cards teaches the eye to skip both.
 */
export function nextActions<T extends { id: string }>(
  tasks: readonly T[],
  needs: readonly NeedsItem[],
  limit = NEXT_SHOWN,
): T[] {
  const shown = needsTaskIds(needs);
  return tasks.filter((t) => !shown.has(t.id)).slice(0, limit);
}

// ── Today ───────────────────────────────────────────────────────────────────

/** The member's local day, `[midnight, next midnight)`, as ISO instants. */
export function todayWindow(now: Date): { start: string; end: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return { start: start.toISOString(), end: end.toISOString() };
}

// ── Which cards show ────────────────────────────────────────────────────────

export interface MyDayCards {
  needs: boolean;
  today: boolean;
  next: boolean;
}

/**
 * Which cards a member gets. A card shows only for an app the member holds:
 * a placeholder for an app they lack is a defect (§4.5). Today and Next
 * actions belong to Calendar and My Tasks, which both ride `feature:tasks`.
 * Needs you shows when any app that feeds it is held.
 */
export function cardsFor(features: readonly string[]): MyDayCards {
  const has = new Set(features);
  return {
    needs: has.has("tasks") || has.has("projects") || has.has("email"),
    today: has.has("tasks"),
    next: has.has("tasks"),
  };
}
