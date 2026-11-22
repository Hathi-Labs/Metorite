/**
 * The flag for events on the Calendar (`calendar_events_reminders.md` §9,
 * CE-5). Off, the Calendar does not ask for `entries` and draws no Google or
 * Outlook event. The gateway flag `CALENDAR_EVENTS` is the other half. With it
 * off, the read has no `entries` key.
 *
 * Turning it on in production is the `enforcement-flip` act.
 */

/** Whether the Calendar reads and draws the entries of the read. */
export function calendarEventsOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_CALENDAR_EVENTS === "1") return true;
  // ⚠️ Development and test builds ONLY: `localStorage["cc-calendar-events"] = "1"`.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-calendar-events") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** The flag as the SERVER render sees it: the build-time value only. */
export function calendarEventsOnServer(): boolean {
  return process.env.NEXT_PUBLIC_CALENDAR_EVENTS === "1";
}
