"use client";

/**
 * Today — the Calendar's Home card (`navigation_shell.md` §4.5, NS-3).
 *
 * The member's scheduled work for today, with times, in a short list. It
 * reads the Calendar's own window read (`GET /projects/my/calendar`, through
 * the lens) for one local day.
 *
 * ⚠️ It does NOT mount `StartupRitual`. The ritual's props belong to
 * `CalendarView`, and §4.3 says the start of the day is the Calendar's. So
 * the card links to `/calendar` ("Plan my day"), and the ritual stays there.
 */
import Link from "next/link";
import { useMemo } from "react";

import { projectsKey } from "@/app/projects/lib/api";
import { lensFetchScheduled } from "@/app/tasks/lib/lens";
import type { MyTask } from "@/app/tasks/lib/types";
import { fmtClock } from "@/app/tasks/lib/utils";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { CardError, HomeCard } from "@/lib/shell/HomeCard";
import { todayWindow } from "@/lib/shell/myDay";
import { type CachedResource, useCachedResource } from "@/lib/useCachedResource";

/** Rows shown before "more in Calendar". Today is a glance, not the grid. */
const TODAY_SHOWN = 6;

/**
 * Today's scheduled tasks. My Day reads it for the summary line, and the
 * card reads it again: one key, so the cache makes it one request.
 */
export function useTodayItems(enabled: boolean, now: Date | null): CachedResource<MyTask[]> {
  const day = now ? now.toDateString() : null;
  const window = useMemo(() => (day ? todayWindow(new Date(day)) : null), [day]);
  return useCachedResource<MyTask[]>(
    enabled && window ? projectsKey("my/calendar", window) : null,
    () => lensFetchScheduled(window!.start, window!.end),
  );
}

function timeRange(task: MyTask): string {
  if (!task.scheduledStart) return "";
  const start = fmtClock(new Date(task.scheduledStart));
  return task.scheduledEnd ? `${start} – ${fmtClock(new Date(task.scheduledEnd))}` : start;
}

export default function TodayCard({
  today,
  now,
  className = "",
}: {
  today: CachedResource<MyTask[]>;
  /** The page's clock, by the minute. A render reads no clock itself. */
  now: Date | null;
  className?: string;
}) {
  const plan = { href: "/calendar", label: "Plan my day" };
  const nowMs = now?.getTime() ?? 0;
  let body: React.ReactNode;
  if (today.data === undefined) {
    body = today.loading ? (
      <SkeletonRows count={3} className="px-2 py-1" />
    ) : (
      <CardError message="Your calendar could not load." onRetry={today.refresh} />
    );
  } else if (today.data.length === 0) {
    body = (
      <p className="px-2 py-4 text-sm text-muted-foreground">Nothing on your calendar today.</p>
    );
  } else {
    const shown = today.data.slice(0, TODAY_SHOWN);
    const more = today.data.length - shown.length;
    body = (
      <ul className="flex flex-col">
        {shown.map((task) => {
          const over = task.scheduledEnd ? new Date(task.scheduledEnd).getTime() < nowMs : false;
          return (
            <li key={task.id}>
              {/* The time sits over the title, not beside it. Today is a
                  narrow rail from `xl`, and a column of times there left the
                  titles three words long. */}
              <Link
                href="/calendar"
                className="flex min-h-11 flex-col justify-center rounded-lg px-2 py-1.5 hover:bg-secondary/60 tech-transition"
              >
                <span className="text-xs tabular-nums text-muted-foreground">{timeRange(task)}</span>
                <span className={`truncate text-sm ${over ? "text-muted-foreground" : "text-foreground"}`}>
                  {task.title}
                </span>
              </Link>
            </li>
          );
        })}
        {more > 0 ? (
          <li>
            <Link href="/calendar" className="flex min-h-9 items-center px-2 text-xs text-muted-foreground hover:text-foreground">
              {more} more in Calendar
            </Link>
          </li>
        ) : null}
      </ul>
    );
  }
  return (
    <HomeCard title="Today" icon="CalendarDays" source="Calendar" link={plan} testId="today" className={className}>
      {body}
    </HomeCard>
  );
}
