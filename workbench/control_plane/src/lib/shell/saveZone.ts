"use client";

/**
 * The member's zone, saved once from the shell (RX-5,
 * `calendar_events_reminders.md` §5.11).
 *
 * The gateway reads a member's day from `user_settings.timezone`: the hour of
 * the morning summary, and the day of "tomorrow at 5" in chat. Two clients wrote
 * that zone, `taskStore.hydrate` and `CalendarView`, and they run only on
 * `/tasks` and `/calendar`. A member who stayed on My Day kept the UTC default,
 * so the summary came at 12:30 in India.
 *
 * So the shell saves the browser's zone, once, for a member whose saved zone is
 * the default. "UTC" is the column default, and it is also the answer of
 * `GET /tasks/settings` for a member with no row, so the two read the same. Any
 * other saved value stays. This code never overwrites a zone that is not the
 * default. A browser that reports UTC has nothing better to save.
 *
 * The read and the write are `/tasks/settings`, the one store of the zone, and
 * that router needs `feature:tasks`. So a member who does not hold it stays on
 * the default, and the caller does not run this for them.
 *
 * A failed read or write leaves no mark, so the next page load tries again. It
 * never reaches the member: this is a background save with nothing to show.
 *
 * Fence: `saveZone.test.ts`.
 */

import { useEffect } from "react";

import { fetchTaskSettings, updateTaskSettings } from "@/app/tasks/lib/api";
import { browserTimeZone } from "@/app/tasks/lib/utils";

/** The column default of `user_settings.timezone`. It may never be a choice. */
const DEFAULT_ZONE = "UTC";

const CHECKED_KEY = "metorite.zoneChecked";

/** The zone to save, or null when the saved zone stays as it is. */
export function zoneToSave(
  saved: string | null | undefined,
  browser: string | undefined,
): string | null {
  if (!browser || browser === DEFAULT_ZONE || browser === "Etc/UTC") return null;
  const stored = (saved ?? "").trim();
  return !stored || stored === DEFAULT_ZONE ? browser : null;
}

function checked(mark: string): boolean {
  try {
    return sessionStorage.getItem(CHECKED_KEY) === mark;
  } catch {
    return false;
  }
}

function setChecked(mark: string): void {
  try {
    sessionStorage.setItem(CHECKED_KEY, mark);
  } catch {
    // A browser with no session storage reads the settings on each page load.
  }
}

/**
 * One check for *mark*, the member and the org of this tab. A saved row belongs
 * to one org, so another org is another check.
 */
export async function saveBrowserZone(
  mark: string,
  browser: string | undefined = browserTimeZone(),
): Promise<void> {
  if (checked(mark)) return;
  try {
    const zone = zoneToSave((await fetchTaskSettings()).timezone, browser);
    if (zone) await updateTaskSettings({ timezone: zone });
    setChecked(mark);
  } catch {
    // No mark: the next page load tries again.
  }
}

/** Save the zone once for a signed-in member who holds `feature:tasks`. */
export function useSaveZone(
  member: string | null,
  org: string | null,
  allowed: boolean,
): void {
  useEffect(() => {
    if (member && org && allowed) void saveBrowserZone(`${member}|${org}`);
  }, [member, org, allowed]);
}
