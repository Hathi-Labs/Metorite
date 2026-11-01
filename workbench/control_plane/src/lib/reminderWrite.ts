/**
 * The writes of a reminder that two apps share (WS-66 RM-5, RX-6): create and
 * move. The Calendar form, the Tasks quick capture and the task detail all
 * set a reminder through `createReminder`. The routes are the RM-1 routes
 * through the BFF proxy `/api/projects/my/reminders`. A refusal reads
 * `refusalWords` from `lib/shell/reminderOpen.ts`, the one set of words.
 */
import { refusalWords, UNREACHABLE_WORDS } from "@/lib/shell/reminderOpen";

export type ReminderKind = "action" | "notice";

/** What a reminder is about. The routes take a task as `subject_kind: "task"` (RX-6). */
export interface ReminderSubject {
  kind: "task";
  id: string;
}

/** The longest title of a reminder (the route's own limit). */
export const REMINDER_TITLE_MAX = 200;

/** The words when the reminder routes answer 404 or 403 to a create: the flag is off, or the member lacks Projects. */
export const REMINDERS_OFF_WORDS = "Reminders are not available for you.";

export const REMINDERS_BASE = "/api/projects/my/reminders";
const BASE = REMINDERS_BASE;

export type WriteResult = { ok: true } | { ok: false; words: string };

/** The words of a create or a move that the server refused for no named reason. */
const SAVE_FAILED_WORDS = "Metorite could not save the reminder. Try again.";

async function write(
  url: string,
  body: Record<string, unknown>,
  fetchImpl: typeof fetch,
  off?: string,
): Promise<WriteResult> {
  try {
    const res = await fetchImpl(url, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) return { ok: true };
    const data: unknown = await res.json().catch(() => null);
    if (off && (res.status === 404 || res.status === 403)) return { ok: false, words: off };
    return { ok: false, words: refusalWords(res.status, data, SAVE_FAILED_WORDS) };
  } catch {
    return { ok: false, words: UNREACHABLE_WORDS };
  }
}

/**
 * Create a reminder. With a `subject` it is linked to that task (RX-6, "Remind
 * me" on a task). The link picker for other kinds waits for CE-3.
 */
export function createReminder(
  a: { title: string; kind: ReminderKind; fireAt: string; tz: string; subject?: ReminderSubject },
  fetchImpl: typeof fetch = fetch,
): Promise<WriteResult> {
  const body: Record<string, unknown> = { title: a.title, kind: a.kind, fire_at: a.fireAt, tz: a.tz };
  if (a.subject) {
    body.subject_kind = a.subject.kind;
    body.subject_id = a.subject.id;
  }
  return write(BASE, body, fetchImpl, REMINDERS_OFF_WORDS);
}

/** Move a reminder to a named time through `/reschedule` (§5.4). */
export function moveReminder(
  id: string,
  a: { fireAt: string; tz: string },
  fetchImpl: typeof fetch = fetch,
): Promise<WriteResult> {
  return write(`${BASE}/${encodeURIComponent(id)}/reschedule`, { fire_at: a.fireAt, tz: a.tz }, fetchImpl);
}
