"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useMemo, useState } from "react";
import { GtdItem } from "@/app/tasks/lib/types";
import { durationLabel } from "@/app/tasks/lib/utils";
import { priorityRank } from "@/app/tasks/lib/priority";
import {
  DEFAULT_BLOCK_MINS,
  blocksForDay,
} from "@/app/tasks/lib/scheduling";
import {
  fmtClock,
} from "./shared";


// ── Schedule sheet / Gap Filler (mobile + tap-to-schedule) ──────────────────
// Touch devices have no HTML5 drag-and-drop and the unscheduled rail is
// desktop-only, so on a phone you could view but barely schedule. This
// bottom-anchored sheet is the mobile path — and also what a tap on an empty
// grid slot opens on any device. With `at` set it becomes the GAP FILLER
// (calendar_focus_os.md §4.5): it measures the free minutes until the next
// block and answers "you have N minutes — here's what fits": the 2-minute
// pile first (done on the spot, tip 9 — never scheduled individually), then
// tasks whose estimate fits the gap, ranked by the priority matrix.
export function ScheduleSheet({
  day,
  at,
  tasks,
  items,
  dueSoon,
  urgentWindowHours,
  dayEndHour,
  onSchedule,
  onDoNow,
  onPlan,
  onClose,
}: {
  day: Date;
  at?: Date;
  tasks: GtdItem[];
  /** full store rows — for measuring the gap against the day's blocks. */
  items: GtdItem[];
  dueSoon: { item: GtdItem; days: number }[];
  urgentWindowHours: number;
  dayEndHour: number;
  onSchedule: (t: GtdItem, at?: Date) => void;
  /** the 2-minute rule: just do it — mark done without ever scheduling. */
  onDoNow: (t: GtdItem) => void;
  onPlan: () => void;
  onClose: () => void;
}) {
  const dueDays = new Map(dueSoon.map((d) => [d.item.id, d.days]));
  const dayLabel = day.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  // Track 2-minute tasks done from this sheet so they collapse in place — the
  // satisfying "clear the pile" loop instead of rows vanishing abruptly.
  const [clearedIds, setClearedIds] = useState<string[]>([]);

  // ── the gap: free minutes from `at` until the next block (or day end) ──────
  const gap = useMemo(() => {
    if (!at) return null;
    const atMs = at.getTime();
    const nextBlock = blocksForDay(items, day).find(
      (b) => b.item.disposition !== "DONE" && b.start.getTime() > atMs,
    );
    const dayEndAt = new Date(day);
    dayEndAt.setHours(dayEndHour, 0, 0, 0);
    const until = nextBlock ? nextBlock.start : dayEndAt;
    const mins = Math.max(0, Math.round((until.getTime() - atMs) / 60000));
    return {
      mins,
      untilLabel: nextBlock ? nextBlock.item.title : "end of day",
    };
  }, [at, items, day, dayEndHour]);

  // The 2-minute pile: flagged at clarify, or tiny by estimate.
  const twoMinute = useMemo(
    () =>
      tasks.filter(
        (t) =>
          !clearedIds.includes(t.id) &&
          (t.isTwoMinute || (t.timeEstimateMins != null && t.timeEstimateMins <= 5)),
      ),
    [tasks, clearedIds],
  );
  const twoMinIds = useMemo(
    () => new Set(twoMinute.map((t) => t.id)),
    [twoMinute],
  );
  // Everything else, ranked by the matrix; split around the gap when known.
  const ranked = useMemo(
    () =>
      tasks
        .filter((t) => !twoMinIds.has(t.id) && !clearedIds.includes(t.id))
        .sort(
          (a, b) =>
            priorityRank(a, urgentWindowHours) -
            priorityRank(b, urgentWindowHours),
        ),
    [tasks, twoMinIds, clearedIds, urgentWindowHours],
  );
  const fits = gap
    ? ranked.filter((t) => (t.timeEstimateMins ?? DEFAULT_BLOCK_MINS) <= gap.mins)
    : ranked;
  const tooLong = gap
    ? ranked.filter((t) => (t.timeEstimateMins ?? DEFAULT_BLOCK_MINS) > gap.mins)
    : [];
  return (
    <div
      className="fixed inset-0 z-[80] flex flex-col justify-end bg-black/40"
      onClick={onClose}
    >
      <div
        className="mx-auto flex max-h-[78vh] w-full max-w-md flex-col overflow-hidden rounded-t-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-center pt-2">
          <span className="h-1 w-10 rounded-full bg-border" />
        </div>
        <div className="flex items-center gap-2 px-4 py-2.5">
          <Icon name="CalendarPlus" className="h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-foreground">
              {gap
                ? `${gap.mins} min until ${gap.untilLabel}`
                : "Schedule a task"}
            </h2>
            <p className="truncate text-[11px] text-muted-foreground">
              {dayLabel}
              {at ? ` · from ${fmtClock(at)}` : " · first free slot"}
            </p>
          </div>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={onClose} aria-label="Close" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        <div className="px-3 pb-2">
          <Button size="none" radius="keep" layout="flex items-center justify-center" type="button" onClick={onPlan} className="w-full gap-1.5 rounded-md px-3 py-2.5 text-sm font-semibold">
            <Icon name="Wand2" className="h-4 w-4" />
            Rebuild my day with AI
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
          {tasks.length === 0 ? (
            <p className="px-1 py-8 text-center text-xs text-muted-foreground">
              Nothing to schedule — inbox zero on next actions. 🎉
            </p>
          ) : (
            <>
              {/* the 2-minute pile — do, don't schedule (tip 9) */}
              {twoMinute.length > 0 && (
                <>
                  <p className="mb-1 flex items-center gap-1 px-1 text-[10px] font-semibold uppercase tracking-wide text-primary">
                    <Icon name="Zap" className="h-3 w-3" /> 2-minute pile ({twoMinute.length})
                    — just do it
                  </p>
                  <div className="mb-2.5 flex flex-col gap-1">
                    {twoMinute.map((t) => (
                      <div
                        key={t.id}
                        className="flex items-center gap-2 rounded-lg border border-primary/25 bg-primary/5 p-2 pl-2.5"
                      >
                        <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
                          {t.title}
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            setClearedIds((c) => [...c, t.id]);
                            onDoNow(t);
                          }}
                          title="Done — no scheduling ceremony for small wins"
                          className="tech-transition inline-flex shrink-0 items-center gap-1 rounded-md bg-success/15 px-2 py-1 text-[11px] font-medium text-success hover:bg-success/25"
                        >
                          <Icon name="Check" className="h-3 w-3" strokeWidth={3} />
                          Done
                        </button>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {fits.length > 0 && (
                <p className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {gap ? `Fits in ${gap.mins} min` : "Unscheduled"} ({fits.length})
                </p>
              )}
              <div className="flex flex-col gap-1.5">
                {fits.map((t) => {
                  const d = dueDays.get(t.id);
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => onSchedule(t, at)}
                      className="tech-transition flex items-center gap-2 rounded-lg border border-border bg-background/60 p-2.5 text-left hover:border-primary/50 active:bg-primary/5"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-foreground">
                          {t.leveraged && (
                            <span className="text-amber-500">★ </span>
                          )}
                          {t.title}
                        </span>
                        <span className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
                          <span className="inline-flex items-center gap-0.5">
                            <Icon name="Clock" className="h-3 w-3" />
                            {durationLabel(t.timeEstimateMins ?? DEFAULT_BLOCK_MINS)}
                          </span>
                          {t.energy && (
                            <span className="inline-flex items-center gap-0.5 capitalize">
                              <Icon name="Zap" className="h-3 w-3" />
                              {t.energy}
                            </span>
                          )}
                          {d !== undefined && (
                            <span
                              className={`font-medium ${
                                d <= 1 ? "text-destructive" : "text-warning"
                              }`}
                            >
                              {d <= 0
                                ? "due today"
                                : d === 1
                                  ? "due tomorrow"
                                  : `due in ${d}d`}
                            </span>
                          )}
                        </span>
                      </span>
                      <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary">
                        {gap ? (
                          <>
                            <Icon name="Play" className="h-3 w-3" fill="currentColor" />
                            Start
                          </>
                        ) : (
                          <>
                            <Icon name="CalendarPlus" className="h-3.5 w-3.5" />
                            {at ? fmtClock(at) : "Timebox"}
                          </>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>

              {/* longer than the gap — visible (so nothing feels hidden) but
                  quiet; scheduling one anyway is allowed, eyes open. */}
              {tooLong.length > 0 && (
                <>
                  <p className="mb-1 mt-3 px-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70">
                    Longer than the gap ({tooLong.length})
                  </p>
                  <div className="flex flex-col gap-1">
                    {tooLong.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        onClick={() => onSchedule(t, at)}
                        className="tech-transition flex items-center gap-2 rounded-lg border border-border/60 bg-background/40 p-2 text-left opacity-70 hover:opacity-100"
                      >
                        <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                          {t.title}
                        </span>
                        <span className="shrink-0 text-[10px] tabular-nums text-warning">
                          {durationLabel(t.timeEstimateMins ?? DEFAULT_BLOCK_MINS)}
                        </span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
