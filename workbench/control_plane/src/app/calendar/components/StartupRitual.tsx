"use client";

// Startup ritual — 5 guided minutes at day start (calendar_focus_os.md §4.2):
// (1) breathe — an optional 60-second settle, always skippable;
// (2) review — carry-forwards framed kindly + what's due soon;
// (3) commit — pick the ★ One Thing, then hand off to the AI planner.
// Completion (or dismissal) stamps today in focusPrefs so it offers once/day;
// the streak counts completions only.

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useMemo, useState } from "react";
import { GtdItem } from "@/app/tasks/lib/types";
import { priorityRank } from "@/app/tasks/lib/priority";
import { durationLabel } from "@/app/tasks/lib/utils";
import { DEFAULT_BLOCK_MINS } from "@/app/tasks/lib/scheduling";
import {
  completeStartup,
  loadFocusPrefs,
  saveFocusPrefs,
  seedsFor,
  dayKey,
  toggleOneThing,
  oneThingIdFor,
} from "@/app/tasks/lib/focusPrefs";

const BREATHE_SECS = 60;

export function StartupRitual({
  items,
  urgentWindowHours,
  carryForward,
  onRollover,
  onPlan,
  onClose,
}: {
  items: GtdItem[];
  urgentWindowHours: number;
  /** yesterday's (or older) scheduled-but-unfinished blocks. */
  carryForward: GtdItem[];
  /** move the carry-forwards back to the unscheduled list (instant). */
  onRollover: () => void;
  /** hand off to the AI planner (PlanDayPanel), One Thing already committed. */
  onPlan: () => void;
  onClose: () => void;
}) {
  const [step, setStep] = useState<0 | 1 | 2>(0);
  const [breatheLeft, setBreatheLeft] = useState(BREATHE_SECS);
  const [breathing, setBreathing] = useState(false);
  const [oneThingId, setOneThingId] = useState<string | null>(() =>
    oneThingIdFor(new Date()),
  );
  const streak = loadFocusPrefs().startupStreak ?? 0;

  // The breathe countdown (only while running); finishing advances the step.
  useEffect(() => {
    if (!breathing) return;
    const id = setInterval(
      () =>
        setBreatheLeft((s) => {
          if (s <= 1) {
            clearInterval(id);
            setBreathing(false);
            setStep(1);
            return 0;
          }
          return s - 1;
        }),
      1000,
    );
    return () => clearInterval(id);
  }, [breathing]);

  // ── candidates for the One Thing: last night's seeds first, then the
  // highest-ranked unscheduled next actions. ──────────────────────────────────
  const seeds = useMemo(() => seedsFor(new Date()), []);
  const candidates = useMemo(() => {
    const pool = items.filter(
      (i) =>
        i.disposition === "NEXT" &&
        i.isMine &&
        !i.archivedAt &&
        !i.scheduledStart,
    );
    const seedSet = new Set(seeds);
    return [...pool]
      .sort((a, b) => {
        const sa = seedSet.has(a.id) ? 0 : 1;
        const sb = seedSet.has(b.id) ? 0 : 1;
        if (sa !== sb) return sa - sb;
        return priorityRank(a, urgentWindowHours) - priorityRank(b, urgentWindowHours);
      })
      .slice(0, 8);
  }, [items, seeds, urgentWindowHours]);

  const dueSoonCount = useMemo(() => {
    // eslint-disable-next-line react-hooks/purity
    const horizon = Date.now() + 14 * 86400000;
    return items.filter(
      (i) =>
        i.isMine &&
        i.disposition === "NEXT" &&
        !i.scheduledStart &&
        !i.archivedAt &&
        i.dueAt &&
        new Date(i.dueAt).getTime() <= horizon,
    ).length;
  }, [items]);

  const pickOneThing = (id: string) => {
    toggleOneThing(new Date(), id);
    setOneThingId((cur) => (cur === id ? null : id));
  };

  const finish = (plan: boolean) => {
    completeStartup();
    // consumed seeds don't linger into tomorrow
    if (seeds.length) saveFocusPrefs({ seeds: { date: dayKey(), ids: [] } });
    onClose();
    if (plan) onPlan();
  };

  const dismiss = () => {
    // dismissal still stamps the day (don't nag), but doesn't count the streak
    saveFocusPrefs({ startupDoneOn: dayKey() });
    onClose();
  };

  const stepPill = (n: 0 | 1 | 2, label: string) => (
    <button
      type="button"
      onClick={() => setStep(n)}
      className={[
        "rounded-full px-2.5 py-0.5 text-[10.5px] font-semibold",
        step === n
          ? "bg-primary text-primary-foreground"
          : "bg-secondary text-muted-foreground hover:text-foreground",
      ].join(" ")}
    >
      {n + 1} · {label}
    </button>
  );

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/50 p-4"
      onClick={dismiss}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-md flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <Icon name="Sun" className="h-4 w-4 shrink-0 text-warning" />
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
            Start your day
          </h2>
          {streak > 1 && (
            <span
              title="Consecutive startup-ritual days"
              className="shrink-0 text-[11px] font-medium text-success"
            >
              streak {streak} 🔥
            </span>
          )}
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={dismiss} aria-label="Skip today" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex items-center gap-1.5 border-b border-border px-4 py-2">
          {stepPill(0, "Breathe")}
          {stepPill(1, "Review")}
          {stepPill(2, "Commit")}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {step === 0 && (
            <div className="flex flex-col items-center py-2 text-center">
              <div
                className="flex h-28 w-28 items-center justify-center rounded-full border-2 border-primary/40"
                style={{
                  boxShadow: "inset 0 0 30px hsl(198 89% 50% / .12)",
                }}
              >
                <div>
                  <div className="text-xl font-bold tabular-nums text-foreground">
                    {Math.floor(breatheLeft / 60)}:
                    {String(breatheLeft % 60).padStart(2, "0")}
                  </div>
                  <div className="text-[10px] text-muted-foreground">
                    {breathing ? "breathe…" : "one minute"}
                  </div>
                </div>
              </div>
              <p className="mt-3 max-w-[260px] text-xs text-muted-foreground">
                Settle before you plan. One slow minute — in through the nose,
                out longer than in.
              </p>
              <div className="mt-4 flex items-center gap-2">
                {!breathing ? (
                  <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => {
                      setBreatheLeft(BREATHE_SECS);
                      setBreathing(true);
                    }} className="gap-1.5 rounded-md px-3 py-2 text-xs font-semibold">
                    <Icon name="Wind" className="h-3.5 w-3.5" />
                    Begin
                  </Button>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      setBreathing(false);
                      setStep(1);
                    }}
                    className="tech-transition rounded-md bg-secondary px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground"
                  >
                    End early
                  </button>
                )}
                {!breathing && (
                  <Button variant="text" size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => setStep(1)} className="gap-1 rounded-md px-3 py-2 text-xs">
                    Skip
                    <Icon name="ArrowRight" className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="flex flex-col gap-3">
              {carryForward.length > 0 ? (
                <div className="rounded-lg border border-border bg-background/60 p-3">
                  <p className="text-xs text-foreground">
                    <span className="font-semibold">
                      {carryForward.length} carry-forward
                      {carryForward.length === 1 ? "" : "s"}
                    </span>{" "}
                    from earlier — not a failure. Move them back to your list,
                    then Rebuild my day to re-plan them.
                  </p>
                  <ul className="mt-1.5 flex flex-col gap-0.5">
                    {carryForward.slice(0, 4).map((i) => (
                      <li
                        key={i.id}
                        className="truncate text-[11.5px] text-muted-foreground"
                      >
                        • {i.title}
                      </li>
                    ))}
                    {carryForward.length > 4 && (
                      <li className="text-[11px] text-muted-foreground/70">
                        +{carryForward.length - 4} more
                      </li>
                    )}
                  </ul>
                  <button
                    type="button"
                    onClick={onRollover}
                    className="tech-transition mt-2 inline-flex items-center gap-1.5 rounded-md bg-warning/20 px-2.5 py-1.5 text-[11.5px] font-medium text-warning hover:bg-warning/30"
                  >
                    <Icon name="RotateCcw" className="h-3.5 w-3.5" />
                    Move to my list
                  </button>
                </div>
              ) : (
                <div className="rounded-lg bg-success/5 p-3 text-xs text-success">
                  <Icon name="Check" className="mr-1 inline h-3.5 w-3.5" />
                  Nothing carried over — clean slate.
                </div>
              )}
              {dueSoonCount > 0 && (
                <div className="flex items-center gap-2 rounded-lg bg-warning/10 p-3 text-xs text-foreground">
                  <Icon name="AlertTriangle" className="h-4 w-4 shrink-0 text-warning" />
                  {dueSoonCount} unscheduled task{dueSoonCount === 1 ? "" : "s"}{" "}
                  due within 2 weeks — the planner will weigh them.
                </div>
              )}
              <Button size="none" radius="keep" type="button" onClick={() => setStep(2)} className="gap-1.5 rounded-md px-3 py-2 text-xs font-semibold">
                Pick the One Thing
                <Icon name="ArrowRight" className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}

          {step === 2 && (
            <div className="flex flex-col gap-2">
              <p className="text-xs text-muted-foreground">
                If only <span className="font-semibold text-foreground">one thing</span>{" "}
                gets done today, it should be… (the planner puts it in your first
                peak-energy window)
              </p>
              {candidates.length === 0 ? (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  No unscheduled next actions — plan or capture first.
                </p>
              ) : (
                candidates.map((c) => {
                  const picked = oneThingId === c.id;
                  const seeded = seeds.includes(c.id);
                  return (
                    <button
                      key={c.id}
                      type="button"
                      onClick={() => pickOneThing(c.id)}
                      className={[
                        "tech-transition flex items-center gap-2 rounded-lg border p-2.5 text-left",
                        picked
                          ? "border-amber-500/60 bg-amber-500/10"
                          : "border-border bg-background/60 hover:border-amber-500/40",
                      ].join(" ")}
                    >
                      <Icon name="Star"
                        className={[
                          "h-4 w-4 shrink-0",
                          picked
                            ? "fill-amber-400 text-amber-400"
                            : "text-muted-foreground/50",
                        ].join(" ")}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[12.5px] font-medium text-foreground">
                          {c.title}
                        </span>
                        <span className="mt-0.5 flex items-center gap-2 text-[10px] text-muted-foreground">
                          {durationLabel(c.timeEstimateMins ?? DEFAULT_BLOCK_MINS)}
                          {c.leveraged && (
                            <span className="font-medium text-amber-500">
                              leveraged
                            </span>
                          )}
                          {seeded && (
                            <span className="font-medium text-primary">
                              seeded last night
                            </span>
                          )}
                        </span>
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>

        {step === 2 && (
          <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
            <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={() => finish(false)} className="rounded-md px-3 py-2 text-xs">
              Just start
            </Button>
            <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => finish(true)} className="gap-1.5 rounded-md px-3 py-2 text-xs font-semibold">
              <Icon name="Wand2" className="h-3.5 w-3.5" />
              Rebuild my day
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
