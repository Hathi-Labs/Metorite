// ── The next activity on a card (WS-53 CRM-X3, crm_platform.md §19.7) ──────
//
// The gateway puts `next_activity` on each deal and lead it lists: the open
// task with the soonest due date, or the next call or meeting. This file turns
// it into the chip on a card, says when a card is overdue, and keeps the
// member's choice of lane order.
//
// The chip is a DESCRIPTOR (`MetaChip`), never JSX and never a class. Its tone
// is a name, and `TaskMeta` is the one place that draws it. So the CRM adds no
// tone map of its own (`sharedTaskUi.test.ts`). Pure, so `nextActivity.test.ts`
// checks every state without a browser.

import type { MetaChip } from "@/lib/taskCard";
import { localStore } from "./emptyStages";
import { dueDay } from "./format";
import type { NextActivity, StatusType } from "./types";

export type { NextActivity } from "./types";

/** A card in a lane of these types is closed, so it shows no chip. */
const CLOSED: ReadonlySet<string> = new Set<StatusType>(["won", "lost"]);

/** The longest subject a chip prints. The title keeps the whole subject. */
const SUBJECT_MAX = 22;

function shortSubject(subject: string | null | undefined): string {
  const text = (subject ?? "").trim();
  return text.length > SUBJECT_MAX ? `${text.slice(0, SUBJECT_MAX - 1)}…` : text;
}

/** "15 Oct". A task's date is a UTC date, so it prints in UTC. */
function dayLabel(next: NextActivity, now: Date): string {
  const at = new Date(next.at);
  const year = next.all_day ? at.getUTCFullYear() : at.getFullYear();
  return at.toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: year === now.getFullYear() ? undefined : "numeric",
    timeZone: next.all_day ? "UTC" : undefined,
  });
}

function joined(head: string, subject: string): string {
  return subject ? `${head} · ${subject}` : head;
}

/**
 * The chip of one card, or null for no chip.
 *
 * | State   | When                                     | Tone      |
 * |---------|------------------------------------------|-----------|
 * | Overdue | a task whose due day is before today     | `danger`  |
 * | Today   | a task due today, or a call or meeting   | `accent`  |
 * | Later   | after today                              | `muted`   |
 * | None    | `next_activity` is null                  | `warning` |
 *
 * A card in a won or lost lane shows no chip. An absent field (an older
 * gateway) shows no chip either, because "No next step" would be a claim the
 * payload did not make.
 */
export function nextActivityChip(
  next: NextActivity | null | undefined,
  laneType: StatusType | string | null | undefined,
  now: Date = new Date()
): MetaChip | null {
  if (laneType && CLOSED.has(laneType)) return null;
  if (next === undefined) return null;
  if (next === null) {
    return {
      key: "next",
      icon: "CalendarX",
      label: "No next step",
      tone: "warning",
      title: "No open task, call or meeting is planned",
    };
  }
  const day = dueDay(next.at, next.all_day, now);
  if (day === null) return null;
  const subject = shortSubject(next.subject);
  const what = next.subject?.trim() || next.type;
  if (day < 0) {
    const late = -day;
    return {
      key: "next",
      icon: "AlertCircle",
      label: `Overdue ${late}d`,
      tone: "danger",
      title: `Overdue by ${late} ${late === 1 ? "day" : "days"}: ${what}`,
    };
  }
  if (day === 0) {
    return {
      key: "next",
      icon: "CalendarClock",
      label: joined("Today", subject),
      tone: "accent",
      title: `Today: ${what}`,
    };
  }
  const date = dayLabel(next, now);
  return {
    key: "next",
    icon: "Calendar",
    label: joined(date, subject),
    tone: "muted",
    title: `${date}: ${what}`,
  };
}

/** True when the card's next activity is overdue. The card then wears a red edge. */
export function isOverdue(
  next: NextActivity | null | undefined,
  laneType: StatusType | string | null | undefined,
  now: Date = new Date()
): boolean {
  return nextActivityChip(next, laneType, now)?.tone === "danger";
}

// ── The lane order ────────────────────────────────────────────────────────
//
// A deal lane orders its cards by stage age (the default) or by next activity.
// The gateway sorts, because a lane returns one page of its cards. This
// browser keeps the choice for each member and organization, with the key
// shape of `emptyStages.ts`.

/** `GET /crm/pipeline?card_sort=`. */
export type CardSort = "stage_age" | "next_activity";

/** The key prefix. The scope `<email>|<organizationId>` follows it. */
export const CARD_SORT_PREFIX = "metorite:crm:card-sort:";

type Store = Pick<Storage, "getItem" | "setItem">;

/** The key of one member's choice, or null with no member to name. */
export function cardSortKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${CARD_SORT_PREFIX}${scope}`;
}

/** The member's stored order, or stage age. Never throws. */
export function readCardSort(
  scope: string | null | undefined,
  store: Store | null = localStore()
): CardSort {
  const key = cardSortKey(scope);
  if (!key || !store) return "stage_age";
  try {
    return store.getItem(key) === "next_activity" ? "next_activity" : "stage_age";
  } catch {
    return "stage_age";
  }
}

/** Keep the member's order. Never throws. */
export function writeCardSort(
  scope: string | null | undefined,
  sort: CardSort,
  store: Store | null = localStore()
): void {
  const key = cardSortKey(scope);
  if (!key || !store) return;
  try {
    store.setItem(key, sort);
  } catch {
    // Private window or blocked site data: the choice lasts for this visit.
  }
}

/** The query of the board read. The default sends nothing, as before. */
export function cardSortQuery(sort: CardSort | undefined): { card_sort?: CardSort } {
  return sort === "next_activity" ? { card_sort: sort } : {};
}
