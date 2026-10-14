"use client";

/**
 * The reader of `/calendar?date=YYYY-MM-DD&at=HH:MM` (WS-58 MD-3a,
 * `my_day_feed.md` §5.8 item 9). It hands the page the day and the time
 * that the link names, and the page opens the day view there.
 *
 * It runs on a fresh visit and again when the query changes. A query that
 * names nothing good calls nothing, so a bare `/calendar` opens as it did
 * before. `CalendarView.tsx` mounts it inside `<Suspense>`, the boundary
 * that `useSearchParams` needs, so the rest of the page still renders on the
 * server. The email app's `EmailDeepLink.tsx` is the same shape.
 *
 * Fence: `../lib/calendarLink.test.ts`.
 */

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";

import { calendarLinkFromSearch, type CalendarLink } from "../lib/calendarLink";

export function CalendarDeepLink({ onLink }: { onLink: (link: CalendarLink) => void }) {
  const params = useSearchParams();
  const query = params?.toString() ?? "";
  const onLinkRef = useRef(onLink);
  useEffect(() => {
    onLinkRef.current = onLink;
  }, [onLink]);

  useEffect(() => {
    const link = calendarLinkFromSearch(new URLSearchParams(query));
    if (link) onLinkRef.current(link);
  }, [query]);

  return null;
}
