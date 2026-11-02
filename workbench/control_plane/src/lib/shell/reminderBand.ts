/**
 * The rules of My Day's Reminders band (RX-3, owner directive 2026-11-01:
 * "Reminders show up differently than a due task showing up in My Day").
 *
 * A reminder is a moment, not a chore. So it leaves the feed's groups and
 * draws in a band of its own above them: the reminders that fired and still
 * wait on the member come first, in strong rows, and then the reminders of
 * the rest of today as quiet rows. Everything that decides what the band
 * shows is here and pure, so each rule is an assertion. `RemindersBand.tsx`
 * only draws it.
 *
 * **Where the rows come from.** The fired ones are the feed's own cards of the
 * three reminder kinds, so the bell, the count and the band agree. The upcoming
 * ones are `reminders_ahead` of the same answer. They are never cards, so they
 * raise no count. One feed, no second read.
 *
 * **The acts write the reminder row.** Done is `/resolve`, and Snooze is
 * `/snooze`, through `reminderOpen.ts`, the one client. Neither writes card
 * state. Undo of a done is `/reopen`. Undo of a snooze is `undoMove`.
 *
 * Fence: `reminderBand.test.ts`.
 */
import { fmtClock } from "@/app/tasks/lib/utils";
import { relativeTime } from "@/lib/taskCard";

import { type AheadReminder, type FeedAnswer, type FeedCard, cardHidden } from "./feed";
import { REMINDER_KINDS, reminderIdOf } from "./reminderKinds";

// ── The rows ────────────────────────────────────────────────────────────────

/**
 * What a row is. `fired` waits on the member, `check` is the check-back
 * ("Did you do it?"), `unconfirmed` is one that Metorite stopped checking, and
 * `upcoming` is a scheduled one of today.
 */
export type BandPhase = "fired" | "check" | "unconfirmed" | "upcoming";

export interface BandRow {
  /** The reminder's own id. */
  id: string;
  /** The key of the needs store and the feed: `reminders:<id>`. */
  key: string;
  phase: BandPhase;
  title: string;
  /** ISO time of the fire, or null when the card carried none. */
  at: string | null;
  atMs: number | null;
  /** The linked object in one line, such as "Task · Send the invoice", or null. */
  subject: string | null;
  /** Where a tap on the row goes: the chat card of the reminder. */
  href: string;
}

/** The chat link of a reminder, as the feed card and the push open it. */
export function reminderHref(id: string): string {
  return `/chat?do=new-chat&reminder=${encodeURIComponent(id)}`;
}

const SUBJECT_WORDS: Readonly<Record<string, string>> = {
  task: "Task",
  project: "Project",
  email_thread: "Email",
  crm_contact: "Contact",
  wa_chat: "WhatsApp",
};

/**
 * The linked object in one line. A task the member may see reads "Task ·
 * Send the invoice". One they may not see, or a kind the server does not
 * read, reads the kind alone. No link reads nothing.
 */
export function subjectLine(subject: { kind: string; label: string | null } | undefined): string | null {
  if (!subject) return null;
  const word = SUBJECT_WORDS[subject.kind] ?? "Linked item";
  return subject.label ? `${word} · ${subject.label}` : word;
}

const PHASE_OF_KIND: Readonly<Record<string, BandPhase>> = {
  reminder_due: "fired",
  reminder_check: "check",
  reminder_unconfirmed: "unconfirmed",
};

const PHASE_RANK: Readonly<Record<BandPhase, number>> = { fired: 0, check: 1, unconfirmed: 2, upcoming: 3 };

const ms = (iso: string | null): number | null => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : t;
};

function firedRow(card: FeedCard, subjects: FeedAnswer["reminder_subjects"]): BandRow | null {
  const id = reminderIdOf(card.signal_id);
  const phase = PHASE_OF_KIND[card.kind];
  if (!id || !phase) return null;
  return {
    id,
    key: card.signal_id,
    phase,
    title: card.title,
    at: card.at,
    atMs: ms(card.at),
    subject: subjectLine(subjects[id]),
    href: reminderHref(id),
  };
}

function upcomingRow(r: AheadReminder, subjects: FeedAnswer["reminder_subjects"]): BandRow {
  return {
    id: r.id,
    key: `reminders:${r.id}`,
    phase: "upcoming",
    title: r.title,
    at: r.fire_at,
    atMs: ms(r.fire_at),
    subject: subjectLine(subjects[r.id]),
    href: reminderHref(r.id),
  };
}

/**
 * The rows of the band, in the order they draw. Fired rows come first, the
 * ones that wait on the member before the check-backs and the unconfirmed,
 * each in time order. Then today's upcoming rows in time order. A row the
 * member acted on (`marked`, the needs store) or hid through the old menu
 * (`hidden`) is gone. An upcoming row whose time has passed is gone too: the
 * loop fires it within a minute, and it then comes back as a fired row.
 */
export function bandRows(
  answer: Pick<FeedAnswer, "cards" | "reminders_ahead" | "reminder_subjects"> | undefined,
  marked: ReadonlySet<string>,
  hidden: ReadonlySet<string>,
  nowMs: number,
): BandRow[] {
  if (!answer) return [];
  const byTime = (a: BandRow, b: BandRow) => (a.atMs ?? Infinity) - (b.atMs ?? Infinity);
  const fired = answer.cards
    .filter((c) => REMINDER_KINDS.has(c.kind) && !hidden.has(c.signal_id) && !cardHidden(c, marked))
    .map((c) => firedRow(c, answer.reminder_subjects))
    .filter((r): r is BandRow => r !== null)
    .sort((a, b) => PHASE_RANK[a.phase] - PHASE_RANK[b.phase] || byTime(a, b));
  const seen = new Set(fired.map((r) => r.id));
  const upcoming = answer.reminders_ahead
    .filter((r) => !seen.has(r.id) && !marked.has(`reminders:${r.id}`))
    .map((r) => upcomingRow(r, answer.reminder_subjects))
    .filter((r) => r.atMs !== null && r.atMs > nowMs)
    .sort(byTime);
  return [...fired, ...upcoming];
}

/** Rows shown before "Show all". Five is what the eye takes in one look. */
export const BAND_SHOWN = 5;

/**
 * The rows to draw. The cut runs over the whole band, so a day with many
 * fired reminders shows the fired ones first and no upcoming row. `hidden` is
 * what the cut held back.
 */
export function cutBand(rows: readonly BandRow[], expanded: boolean, limit = BAND_SHOWN): { shown: BandRow[]; hidden: number } {
  const shown = expanded ? [...rows] : rows.slice(0, limit);
  return { shown, hidden: rows.length - shown.length };
}

/** True for a row that waits on the member now: the strong rows. */
export function isWaiting(row: Pick<BandRow, "phase">): boolean {
  return row.phase !== "upcoming";
}

// ── The words of a row ──────────────────────────────────────────────────────

/** The clock of a time, in the member's zone, the clock Today draws. */
export function clockOf(atMs: number | null): string {
  return atMs === null ? "" : fmtClock(new Date(atMs));
}

/**
 * The time line of a row, before the linked object. A fired row names the
 * exact time and how long it has waited ("9:00 AM · 25m ago"). A check-back
 * asks the question. An unconfirmed row says Metorite stopped checking. An
 * upcoming row says "Later today · 5:00 PM".
 */
export function timeWords(row: Pick<BandRow, "phase" | "at" | "atMs">, nowMs: number): string {
  const clock = clockOf(row.atMs);
  switch (row.phase) {
    case "upcoming":
      return clock ? `Later today · ${clock}` : "Later today";
    case "check":
      return clock ? `Did you do it? · set for ${clock}` : "Did you do it?";
    case "unconfirmed":
      return clock ? `Not confirmed · set for ${clock}` : "Not confirmed";
    default: {
      const ago = relativeTime(row.at, nowMs);
      return clock ? (ago ? `${clock} · ${ago}` : clock) : ago || "Due now";
    }
  }
}

/** The whole second line of a row: the time words, then the linked object. */
export function rowMeta(row: BandRow, nowMs: number): string {
  const time = timeWords(row, nowMs);
  return row.subject ? `${time} · ${row.subject}` : time;
}

/** The label over the band: "Reminders", with the number that wait on the member. */
export function bandCount(rows: readonly BandRow[]): number {
  return rows.filter(isWaiting).length;
}

// ── Snooze ──────────────────────────────────────────────────────────────────

export type SnoozeChoiceId = "hour" | "evening" | "tomorrow";

export interface SnoozeChoice {
  id: SnoozeChoiceId;
  label: string;
  /** The wall time the choice names, in the browser's zone. */
  at: Date;
}

/** The hour of "This evening" and the hour of "Tomorrow morning". */
export const EVENING_HOUR = 18;
export const MORNING_HOUR = 9;
/** "This evening" is offered while it is more than 90 minutes away, else it only repeats "In 1 hour". */
const EVENING_MIN_GAP_MS = 90 * 60_000;

/**
 * The snooze choices of a moment (owner, RX-3): In 1 hour, This evening
 * (18:00 today, offered only while it is more than 90 minutes away), and
 * Tomorrow morning (09:00 the next day). The wall clock is the browser's, as
 * the Calendar's is.
 */
export function snoozeChoices(now: Date): SnoozeChoice[] {
  const hour = new Date(now.getTime() + 60 * 60_000);
  const evening = new Date(now.getFullYear(), now.getMonth(), now.getDate(), EVENING_HOUR, 0, 0, 0);
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, MORNING_HOUR, 0, 0, 0);
  const out: SnoozeChoice[] = [{ id: "hour", label: "In 1 hour", at: hour }];
  if (evening.getTime() - now.getTime() > EVENING_MIN_GAP_MS) out.push({ id: "evening", label: "This evening", at: evening });
  out.push({ id: "tomorrow", label: "Tomorrow morning", at: tomorrow });
  return out;
}

const pad = (n: number) => String(Math.abs(n)).padStart(2, "0");

/** A date as ISO WITH the browser's offset, as the gateway wants it ("2026-11-02T18:00:00+05:30"). */
export function localIso(d: Date): string {
  const off = -d.getTimezoneOffset();
  const sign = off < 0 ? "-" : "+";
  const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return `${date}T${time}${sign}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`;
}

/** "Snoozed to 6:00 PM", or "Snoozed to tomorrow 9:00 AM" for another day. */
export function snoozedWords(at: Date, now: Date): string {
  const sameDay = at.toDateString() === now.toDateString();
  return sameDay ? `Snoozed to ${fmtClock(at)}` : `Snoozed to tomorrow ${fmtClock(at)}`;
}

/** How soon after Undo a reminder that was waiting comes back: the loop fires a past time within a minute. */
export const COMEBACK_MS = 60_000;

/**
 * The time that the Undo of a snooze moves the reminder to. An upcoming row
 * goes back to the time it had. A row that was waiting on the member would
 * come back as a scheduled one at a past time, so it goes to one minute from
 * now, and the loop fires it again at once. The gateway's `/reschedule`
 * takes any named time.
 */
export function undoMove(row: Pick<BandRow, "phase" | "at" | "atMs">, nowMs: number): string {
  if (row.phase === "upcoming" && row.at && row.atMs !== null && row.atMs > nowMs) return row.at;
  return localIso(new Date(nowMs + COMEBACK_MS));
}
