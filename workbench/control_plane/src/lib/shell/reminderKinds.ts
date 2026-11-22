/**
 * The three card kinds of a reminder (`calendar_events_reminders.md` §5.6),
 * in a file with no imports so `feed.ts`, `myDay.ts` and the band all read the
 * same set (RX-3). The server's set is `REMINDER_KINDS` in `routes/shell/needs.py`.
 */

/** A reminder that fired and still waits: the band's strong rows and the bell's reminder rows. */
export const FIRED_REMINDER_KINDS: ReadonlySet<string> = new Set(["reminder_due", "reminder_check"]);

/** Every reminder card kind. */
export const REMINDER_KINDS: ReadonlySet<string> = new Set([
  "reminder_due",
  "reminder_check",
  "reminder_unconfirmed",
]);

/** Whether a card or row kind is one of the reminder kinds. */
export function isReminderKind(kind: string): boolean {
  return REMINDER_KINDS.has(kind);
}

/** The reminder id of a feed or needs id (`reminders:<id>`), or null. */
export function reminderIdOf(signalId: string): string | null {
  return signalId.startsWith("reminders:") && signalId.length > 10 ? signalId.slice(10) : null;
}
