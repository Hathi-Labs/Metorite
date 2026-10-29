/**
 * The "Hide details on the lock screen" preference (WS-66 RE-9a).
 *
 * It is the key `hide_push_details` in `user_settings.my_day_prefs`. The
 * existing prefs route reads and writes it: `GET` and `PUT /api/shell/feed/prefs`
 * (`src/app/api/shell/feed/prefs/route.ts`). The body names no member and no
 * tenant, because the gateway takes both from the session.
 *
 * Spec: `project-docs/specs/calendar_events_reminders.md` §5.6 "Settled
 * 2026-10-30 (RE-9)", decision 4. RE-9b reads the key when it sends.
 */

const PREFS_URL = "/api/shell/feed/prefs";

/**
 * The stored value. A key the member never set reads as off. `null` means the
 * read failed, so the toggle does not draw a guess.
 */
export async function loadHideDetails(): Promise<boolean | null> {
  try {
    const res = await fetch(PREFS_URL, { cache: "no-store" });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    const v = (data as { hide_push_details?: unknown } | null)?.hide_push_details;
    return v === true;
  } catch {
    return null;
  }
}

/**
 * Save the value, and return what the server stored. `null` means the save
 * failed, and the caller keeps the old value.
 */
export async function saveHideDetails(value: boolean): Promise<boolean | null> {
  try {
    const res = await fetch(PREFS_URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: "hide_push_details", value }),
    });
    if (!res.ok) return null;
    const data: unknown = await res.json();
    return (data as { hide_push_details?: unknown } | null)?.hide_push_details === true;
  } catch {
    return null;
  }
}
