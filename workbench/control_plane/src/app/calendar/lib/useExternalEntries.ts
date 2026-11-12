"use client";

/**
 * The hook of the Calendar's external events (CE-5a). With the client flag
 * off the key is `null`, so the hook asks for nothing and the list is empty.
 * The key differs from the one `useTodayItems` uses for the same route,
 * because the cached value has another shape.
 */
import { useMemo, useRef } from "react";

import { projectsKey } from "@/app/projects/lib/api";
import { lensFetchCalendar } from "@/app/tasks/lib/lens";
import { useCachedResource } from "@/lib/useCachedResource";
import { calendarEventsOn } from "@/lib/shell/calendarEventsFlag";

import { externalReadNote, externalViews, type ExternalView } from "./externalEntries";

const NONE: ExternalView[] = [];

/** The visible days as ISO instants: the first midnight to the last one. */
export function visibleWindow(first: Date, afterLast: Date): { start: string; end: string } {
  const at = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).toISOString();
  return { start: at(first), end: at(afterLast) };
}

/** The drawable events, and the line to show when the last read failed. */
export function useExternalEntries(window: { start: string; end: string }): {
  views: ExternalView[];
  note: string | null;
} {
  const on = calendarEventsOn();
  const res = useCachedResource<{ tasks: unknown; entries: unknown[] }>(
    on ? projectsKey("my/calendar/with-entries", window) : null,
    () => lensFetchCalendar(window.start, window.end),
  );
  const entries = res.data?.entries;
  const fresh = useMemo(() => (on && entries ? externalViews(entries) : null), [on, entries]);
  // A failed read keeps the last good list, and says so.
  const last = useRef<ExternalView[]>(NONE);
  if (!on) last.current = NONE;
  else if (fresh) last.current = fresh;
  return { views: on ? last.current : NONE, note: on ? externalReadNote(res.error) : null };
}
