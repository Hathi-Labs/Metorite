"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { GtdItem } from "@/app/tasks/lib/types";
import {
  startOfDay,
  blocksForDay,
} from "@/app/tasks/lib/scheduling";
import {
  fmtClock,
  fmtLeft,
} from "./shared";


// ── Now / Next focus bar ─────────────────────────────────────────────────────
// "What should I be doing right now, and what's next?" — a persistent, live
// strip that always reflects the real current time (today), independent of the
// day you're browsing. Directly targets time-blindness: the current block gets
// a countdown + a one-tap done, the next block a "starts in" timer.
export function NowNextBar({
  now,
  items,
  onOpen,
  onComplete,
  onStart,
  onFillGap,
}: {
  now: Date;
  items: GtdItem[];
  onOpen: (id: string) => void;
  onComplete: (item: GtdItem) => void;
  /** enter the Focus room for the current block (stamps actualStart). */
  onStart: (item: GtdItem) => void;
  /** open right now with nothing scheduled → the Gap Filler (§4.5). */
  onFillGap: () => void;
}) {
  const nowMs = now.getTime();
  const todayBlocks = blocksForDay(items, startOfDay(now));
  const current = todayBlocks.find(
    (b) =>
      b.item.disposition !== "DONE" &&
      b.start.getTime() <= nowMs &&
      b.end.getTime() > nowMs,
  );
  const next = todayBlocks.find(
    (b) => b.item.disposition !== "DONE" && b.start.getTime() > nowMs,
  );

  // Nothing live to say → stay out of the way (empty day, or the day is over).
  if (!current && !next) return null;

  const minsLeft = current
    ? Math.max(0, Math.ceil((current.end.getTime() - nowMs) / 60000))
    : 0;
  const minsToNext = next
    ? Math.max(0, Math.ceil((next.start.getTime() - nowMs) / 60000))
    : 0;
  const nextIsNow = next && minsToNext <= 0;
  // How far through the current block we are (fills as the block elapses).
  const progress = current
    ? Math.min(
        100,
        Math.max(
          0,
          ((nowMs - current.start.getTime()) /
            (current.end.getTime() - current.start.getTime())) *
            100,
        ),
      )
    : 0;
  // Focus timer state: is the current block being actively timed, and for how
  // long? (actual work-time, distinct from the block's scheduled elapse.)
  const startedAt =
    current && current.item.actualStart
      ? new Date(current.item.actualStart)
      : null;
  const running = !!startedAt && !!current && !current.item.actualEnd;
  const focusMins =
    running && startedAt
      ? Math.max(0, Math.floor((nowMs - startedAt.getTime()) / 60000))
      : 0;

  return (
    <div className="flex items-stretch gap-3 border-b border-border bg-primary/[0.04] px-4 py-2">
      {/* NOW */}
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <span className="flex shrink-0 items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-primary">
          <span className="relative flex h-2 w-2">
            {current && (
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary/60" />
            )}
            <span
              className={[
                "relative inline-flex h-2 w-2 rounded-full",
                current ? "bg-primary" : "bg-muted-foreground/40",
              ].join(" ")}
            />
          </span>
          Now
        </span>
        {current ? (
          <>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onComplete(current.item);
              }}
              aria-label="Mark done"
              title="Mark done"
              className="tech-transition flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-muted-foreground/50 text-transparent hover:border-success hover:bg-success/10 hover:text-success"
            >
              <Icon name="Check" className="h-3 w-3" strokeWidth={3} />
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onStart(current.item);
              }}
              aria-label="Enter Focus Mode"
              title={
                running
                  ? "Re-enter the Focus room"
                  : "Focus — full-screen room + timer (tracks how long this actually takes)"
              }
              className="tech-transition flex h-5 shrink-0 items-center gap-1 rounded-full border border-primary/50 px-2 text-[10px] font-semibold text-primary hover:bg-primary/10"
            >
              <Icon name="Play" className="h-2.5 w-2.5" fill="currentColor" />
              Focus
            </button>
            <button
              type="button"
              onClick={() => onOpen(current.item.id)}
              className="flex min-w-0 flex-1 flex-col items-start text-left"
            >
              <span className="w-full truncate text-sm font-medium text-foreground">
                {current.item.title}
              </span>
              <span className="mt-1 flex w-full items-center gap-1.5">
                <span className="h-1 flex-1 overflow-hidden rounded-full bg-primary/15">
                  <span
                    className={[
                      "block h-full rounded-full",
                      running ? "bg-primary animate-pulse" : "bg-primary/70",
                    ].join(" ")}
                    style={{ width: `${progress}%` }}
                  />
                </span>
                <span className="shrink-0 text-[10px] font-medium tabular-nums text-primary">
                  {running ? `▶ ${fmtLeft(focusMins)}` : `${fmtLeft(minsLeft)} left`}
                </span>
              </span>
            </button>
          </>
        ) : (
          <Button variant="text" size="none" layout="" type="button" onClick={onFillGap} title="See what fits in this gap — 2-minute pile first" className="min-w-0 flex-1 truncate text-left text-xs">
            Open right now —{" "}
            <span className="font-medium text-primary">
              fill the gap with quick wins?
            </span>
          </Button>
        )}
      </div>

      {/* NEXT */}
      {next && (
        <button
          type="button"
          onClick={() => onOpen(next.item.id)}
          className="flex min-w-0 max-w-[42%] shrink items-center gap-2 border-l border-border pl-3 text-left"
        >
          <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Next
          </span>
          <span className="min-w-0">
            <span className="block truncate text-[12.5px] text-foreground">
              {next.item.title}
            </span>
            <span className="block text-[10px] tabular-nums text-muted-foreground">
              {fmtClock(next.start)}
              {" · "}
              {nextIsNow ? "starting now" : `in ${fmtLeft(minsToNext)}`}
            </span>
          </span>
        </button>
      )}
    </div>
  );
}
