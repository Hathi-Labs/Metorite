"use client";

/**
 * The hook of the Calendar's external events (CE-5a). With the client flag
 * off the key is `null`, so the hook asks for nothing and the list is empty.
 * The key differs from the one `useTodayItems` uses for the same route,
 * because the cached value has another shape.
 */
import { useMemo } from "react";

import { projectsKey } from "@/app/projects/lib/api";
import { lensFetchCalendar } from "@/app/tasks/lib/lens";
import { useCachedResource } from "@/lib/useCachedResource";
import { calendarEventsOn } from "@/lib/shell/calendarEventsFlag";

import { externalViews, type ExternalView } from "./externalEntries";

const NONE: ExternalView[] = [];

/** The visible days as ISO instants: the first midnight to the last one. */
export function visibleWindow(first: Date, afterLast: Date): { start: string; end: string } {
  const at = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString();
  return { start: at(first), end: at(afterLast) };
}

export function useExternalEntries(window: { start: string; end: string }): ExternalView[] {
  const on = calendarEventsOn();
  const res = useCachedResource<{ tasks: unknown; entries: unknown[] }>(
    on ? projectsKey("my/calendar/with-entries", window) : null,
    () => lensFetchCalendar(window.start, window.end),
  );
  const entries = res.data?.entries;
  return useMemo(() => (on && entries ? externalViews(entries) : NONE), [on, entries]);
}
