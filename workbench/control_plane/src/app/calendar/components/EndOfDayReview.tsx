"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useState } from "react";
import {
  apiEstimateStats,
  apiSetDayState,
  type EstimateStats,
} from "@/app/tasks/lib/api";
import { GtdItem } from "@/app/tasks/lib/types";
import { priorityRank } from "@/app/tasks/lib/priority";
import {
  startOfDay,
  addDays,
  blocksForDay,
} from "@/app/tasks/lib/scheduling";
import {
  dayKey,
  saveFocusPrefs,
} from "@/app/tasks/lib/focusPrefs";
import {
  fmtClock,
  fmtLeft,
} from "./shared";


// ── End-of-day review / shutdown ─────────────────────────────────────────────
// A 2-minute reflection (calendar_ux_review §3 P3, extended per
// calendar_focus_os.md §4.2): what got DONE (celebrated, with planned-vs-
// actual), the leverage ratio (80/20 scoreboard), the One-Thing verdict, what
// carries FORWARD (framed kindly), estimate trends — and "seed tomorrow", the
// ≤3 picks that pre-load tomorrow's startup ritual. "Close the day" is the
// explicit permission-to-stop end state.
export function EndOfDayReview({
  now,
  items,
  oneThingId,
  urgentWindowHours,
  onOpen,
  onCloseDay,
  onClose,
}: {
  now: Date;
  items: GtdItem[];
  oneThingId: string | null;
  urgentWindowHours: number;
  onOpen: (id: string) => void;
  /** "Close the day" — persists tomorrow's seeds + the closed stamp. */
  onCloseDay: () => void;
  onClose: () => void;
}) {
  const [stats, setStats] = useState<EstimateStats | null>(null);
  const [seedIds, setSeedIds] = useState<string[]>([]);
  const toggleSeed = (id: string) =>
    setSeedIds((cur) =>
      cur.includes(id)
        ? cur.filter((x) => x !== id)
        : cur.length >= 3
          ? cur
          : [...cur, id],
    );
  useEffect(() => {
    let alive = true;
    apiEstimateStats()
      .then((s) => {
        if (alive) setStats(s);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const blocks = blocksForDay(items, startOfDay(now));
  const done = blocks.filter((b) => b.item.disposition === "DONE");
  const unfinished = blocks.filter((b) => b.item.disposition !== "DONE");
  const doneMins = done.reduce(
    (n, b) => n + (b.end.getTime() - b.start.getTime()) / 60000,
    0,
  );
  const doneHrs = Math.round((doneMins / 60) * 10) / 10;
  // Leverage ratio — done-hours can flatter a busy, pointless day; this can't.
  const leveragedDoneMins = done
    .filter(
      (b) => b.item.leveraged || b.item.important || b.item.id === oneThingId,
    )
    .reduce((n, b) => n + (b.end.getTime() - b.start.getTime()) / 60000, 0);
  const leveragePct =
    doneMins > 0 ? Math.round((leveragedDoneMins / doneMins) * 100) : null;
  // One-Thing verdict: if it got done, the day was a win regardless of the rest.
  const oneThing = oneThingId
    ? items.find((i) => i.id === oneThingId) ?? null
    : null;
  const oneThingDone = oneThing?.disposition === "DONE";
  // Seed-tomorrow candidates: today's unfinished blocks first, then the
  // highest-ranked unscheduled next actions. Up to 6 choices, ≤3 picks.
  const seedCandidates: GtdItem[] = (() => {
    const seen = new Set<string>();
    const out: GtdItem[] = [];
    for (const b of unfinished) {
      if (!seen.has(b.item.id)) {
        seen.add(b.item.id);
        out.push(b.item);
      }
    }
    const pool = items
      .filter(
        (i) =>
          i.disposition === "NEXT" &&
          i.isMine &&
          !i.archivedAt &&
          !i.scheduledStart &&
          !seen.has(i.id),
      )
      .sort(
        (a, b) =>
          priorityRank(a, urgentWindowHours) - priorityRank(b, urgentWindowHours),
      );
    return [...out, ...pool].slice(0, 6);
  })();
  const closeDay = () => {
    const tomorrow = dayKey(addDays(now, 1));
    saveFocusPrefs({
      seeds: { date: tomorrow, ids: seedIds },
      dayClosedOn: dayKey(now),
    });
    // Persist tomorrow's seeds so the morning digest / planner can pre-load them.
    void apiSetDayState(tomorrow, { seedIds }).catch(() => {});
    onCloseDay();
  };
  const dateLabel = now.toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Icon name="ClipboardCheck" className="h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-foreground">
              Day review
            </h2>
            <p className="truncate text-[11px] text-muted-foreground">
              {dateLabel}
            </p>
          </div>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={onClose} aria-label="Close" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {/* At-a-glance tally — done · leverage ratio · carry forward */}
          <div className="mb-3 flex gap-2">
            <div className="flex-1 rounded-lg border border-border bg-background/60 p-2.5 text-center">
              <div className="text-lg font-semibold tabular-nums text-success">
                {done.length}
              </div>
              <div className="text-[10px] text-muted-foreground">
                done · {doneHrs}h
              </div>
            </div>
            <div className="flex-1 rounded-lg border border-border bg-background/60 p-2.5 text-center">
              <div className="text-lg font-semibold tabular-nums text-amber-500">
                {leveragePct == null ? "—" : `${leveragePct}%`}
              </div>
              <div className="text-[10px] text-muted-foreground">leveraged</div>
            </div>
            <div className="flex-1 rounded-lg border border-border bg-background/60 p-2.5 text-center">
              <div className="text-lg font-semibold tabular-nums text-foreground">
                {unfinished.length}
              </div>
              <div className="text-[10px] text-muted-foreground">
                carry forward
              </div>
            </div>
          </div>

          {/* One-Thing verdict */}
          {oneThing && (
            <div
              className={[
                "mb-3 flex items-center gap-2 rounded-lg border p-2.5 text-xs",
                oneThingDone
                  ? "border-amber-500/40 bg-amber-500/10 text-foreground"
                  : "border-border bg-background/60 text-muted-foreground",
              ].join(" ")}
            >
              <Icon name="Star"
                className={[
                  "h-4 w-4 shrink-0",
                  oneThingDone
                    ? "fill-amber-400 text-amber-400"
                    : "text-amber-500/60",
                ].join(" ")}
              />
              {oneThingDone ? (
                <span>
                  <span className="font-semibold">One Thing done</span> —{" "}
                  {oneThing.title}. That was the day.
                </span>
              ) : (
                <span>
                  One Thing still open — {oneThing.title}. Seed it for tomorrow?
                </span>
              )}
            </div>
          )}

          {/* Estimate accuracy — the learned-estimate signal */}
          {stats && stats.samples >= 5 ? (
            <div className="mb-3 rounded-lg bg-primary/5 p-2.5 text-xs text-foreground">
              <span className="font-medium">Estimate accuracy.</span> Over{" "}
              {stats.samples} timed tasks you typically run{" "}
              <span
                className={
                  stats.overPct > 0
                    ? "font-medium text-warning"
                    : "font-medium text-success"
                }
              >
                {stats.overPct > 0
                  ? `${stats.overPct}% over`
                  : stats.overPct < 0
                    ? `${Math.abs(stats.overPct)}% under`
                    : "right on"}
              </span>{" "}
              your estimate
              {stats.overPct > 0 ? " — the planner now pads for it." : "."}
            </div>
          ) : (
            <div className="mb-3 rounded-lg bg-secondary/50 p-2.5 text-[11px] text-muted-foreground">
              Tip: hit{" "}
              <span className="font-medium text-foreground">▶ Start</span> on your
              current block to time it. After a few, we learn how your estimates
              compare and pad future plans.
            </div>
          )}

          {/* Done — celebrate, with how the estimate held up */}
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Done today
          </p>
          {done.length === 0 ? (
            <p className="mb-3 text-xs text-muted-foreground">
              Nothing marked done yet — no shame, tomorrow is a fresh plan.
            </p>
          ) : (
            <div className="mb-3 flex flex-col gap-1">
              {done.map((b) => {
                const plannedMins = Math.round(
                  (b.end.getTime() - b.start.getTime()) / 60000,
                );
                const actualMins =
                  b.item.actualStart && b.item.actualEnd
                    ? Math.max(
                        1,
                        Math.round(
                          (new Date(b.item.actualEnd).getTime() -
                            new Date(b.item.actualStart).getTime()) /
                            60000,
                        ),
                      )
                    : null;
                return (
                  <button
                    key={b.item.id}
                    type="button"
                    onClick={() => onOpen(b.item.id)}
                    className="tech-transition flex items-center gap-2 rounded-md border border-border bg-background/60 p-2 text-left hover:border-primary/40"
                  >
                    <Icon name="Check" className="h-3.5 w-3.5 shrink-0 text-success" />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground line-through decoration-muted-foreground/40">
                      {b.item.title}
                    </span>
                    {actualMins != null && (
                      <span
                        title={`Planned ${plannedMins}m · actually took ${actualMins}m`}
                        className={[
                          "shrink-0 text-[10px] font-medium tabular-nums",
                          actualMins > plannedMins
                            ? "text-warning"
                            : actualMins < plannedMins
                              ? "text-success"
                              : "text-muted-foreground",
                        ].join(" ")}
                      >
                        {actualMins === plannedMins
                          ? "on time"
                          : (actualMins > plannedMins ? "+" : "−") +
                            fmtLeft(Math.abs(actualMins - plannedMins))}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {/* Carry forward — kindly framed, not a wall of red */}
          {unfinished.length > 0 && (
            <>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Carry forward ({unfinished.length})
              </p>
              <p className="mb-1.5 text-[11px] text-muted-foreground">
                Not a failure — just tomorrow&apos;s plan. Roll them over or replan
                from the calendar.
              </p>
              <div className="flex flex-col gap-1">
                {unfinished.map((b) => (
                  <button
                    key={b.item.id}
                    type="button"
                    onClick={() => onOpen(b.item.id)}
                    className="tech-transition flex items-center gap-2 rounded-md border border-border bg-background/60 p-2 text-left hover:border-primary/40"
                  >
                    <span className="h-2 w-2 shrink-0 rounded-full border border-muted-foreground/50" />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
                      {b.item.title}
                    </span>
                    <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground">
                      {fmtClock(b.start)}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Seed tomorrow — ≤3 picks pre-load the morning startup ritual, so
            planning tomorrow takes 5 minutes, not 20. */}
        {seedCandidates.length > 0 && (
          <div className="border-t border-border px-4 py-3">
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Seed tomorrow · pick up to 3
            </p>
            <div className="flex flex-wrap gap-1.5">
              {seedCandidates.map((c) => {
                const on = seedIds.includes(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => toggleSeed(c.id)}
                    className={[
                      "tech-transition max-w-full truncate rounded-full border px-2.5 py-1 text-[11px] font-medium",
                      on
                        ? "border-primary/50 bg-primary/15 text-primary"
                        : "border-border bg-secondary/60 text-muted-foreground hover:text-foreground",
                    ].join(" ")}
                  >
                    {on ? "✓ " : ""}
                    {c.title}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={onClose} className="rounded-md px-3 py-2 text-xs">
            Not yet
          </Button>
          <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={closeDay} title="Save tomorrow's seeds and end the work day — permission to stop" className="gap-1.5 rounded-md px-3 py-2 text-xs">
            <Icon name="Moon" className="h-3.5 w-3.5" />
            Close the day
          </Button>
        </div>
      </div>
    </div>
  );
}
