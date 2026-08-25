"use client";

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useState } from "react";
import {
  apiPlanDay,
  apiReplan,
  type EnergyWindow,
  type DayPlanResult,
} from "@/app/tasks/lib/api";
import { energyWindowsPayload, fmtClock } from "./shared";
import { useTaskStore } from "@/app/tasks/lib/taskStore";


// ── AI "Plan my day" review panel ────────────────────────────────────────────
export function PlanDayPanel({
  mode = "plan",
  target,
  dayStart,
  dayEnd,
  capacityMins,
  bufferMins,
  energyWindows,
  extraNote,
  onOpenSettings,
  onClose,
}: {
  mode?: "plan" | "replan";
  target: Date;
  dayStart: number;
  dayEnd: number;
  capacityMins: number;
  bufferMins: number;
  energyWindows: EnergyWindow[];
  /** standing guidance appended to every run — e.g. the ★ One Thing directive
   *  (protect it in the first peak window). Rides the existing energy_note
   *  seam so no backend change is needed. */
  extraNote?: string;
  /** open Calendar settings (to edit the STANDING planning prompt). */
  onOpenSettings?: () => void;
  onClose: () => void;
}) {
  const isReplan = mode === "replan";
  const applySchedule = useTaskStore((s) => s.applySchedule);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [plan, setPlan] = useState<DayPlanResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Plan-through horizon — where the day STOPS. "work" = your standing working
  // hours (the office default); "1".."6" = that many hours from NOW, which
  // crosses midnight so a late-night burst plans 24/7. When you open the panel
  // OUTSIDE working hours (early morning / late night), we default to a few
  // hours-from-now so the plan isn't an empty window. Only "today" gets the
  // from-now options; a future day always plans its working hours.
  const planningToday = new Date().toDateString() === target.toDateString();
  const [horizon, setHorizon] = useState<string>(() => {
    if (planningToday) {
      const h = new Date().getHours();
      if (h >= dayEnd || h < dayStart) return "3";
    }
    return "work";
  });
  // "Custom time…" — an exact clock time to work through (HH:MM, 24h). Defaults
  // to the working-hours end; a time already past today rolls to the next day so
  // a late-night pick (e.g. 1:00 at 11pm) plans into the small hours.
  const [customTime, setCustomTime] = useState<string>(
    `${String(dayEnd % 24).padStart(2, "0")}:00`,
  );
  // A single "now" captured when the panel opens — used for the horizon LABELS
  // (kept out of render so it stays pure; the labels are a preview, and the
  // panel is short-lived). `run` recomputes from a fresh Date.now() at tap time.
  const [openedAtMs] = useState(() => Date.now());
  const workEndAt = () => {
    const d = new Date(target);
    d.setHours(dayEnd, 0, 0, 0);
    return d;
  };
  const customEndAt = (baseMs: number) => {
    const [hh, mm] = customTime.split(":").map(Number);
    const d = new Date(target);
    d.setHours(hh || 0, mm || 0, 0, 0);
    // Wrap a past clock time to tomorrow (only makes sense for "today").
    if (planningToday && d.getTime() <= baseMs) d.setDate(d.getDate() + 1);
    return d;
  };
  const endForHorizon = (hz: string, baseMs: number) =>
    hz === "work"
      ? workEndAt()
      : hz === "custom"
        ? customEndAt(baseMs)
        : new Date(baseMs + Number(hz) * 3600_000);

  const run = async (n: string, hz: string = horizon) => {
    setLoading(true);
    setError(null);
    const dayStartAt = new Date(target);
    dayStartAt.setHours(dayStart, 0, 0, 0);
    // Fresh "now" at tap time — a "N more hours" / past-time-wrap horizon is
    // measured from when you actually plan, not when the panel opened.
    const dayEndAt = endForHorizon(hz, Date.now());
    try {
      setPlan(
        await (isReplan ? apiReplan : apiPlanDay)({
          day_start: dayStartAt.toISOString(),
          day_end: dayEndAt.toISOString(),
          energy_windows: energyWindowsPayload(energyWindows, target),
          capacity_mins: capacityMins,
          buffer_mins: bufferMins,
          energy_note:
            [n.trim(), extraNote].filter(Boolean).join(" ") || undefined,
        }),
      );
    } catch (err) {
      setError((err as Error)?.message || "Couldn't plan your day right now.");
    } finally {
      setLoading(false);
    }
  };
  // NB: we deliberately do NOT plan on open — the user first adds an optional
  // note / picks a horizon, then taps the button. Planning is an explicit act so
  // a per-run prompt is never skipped by an auto-run.

  const apply = () => {
    if (!plan) return;
    // One undoable op: place/move the blocks AND clear the evicted ones (back to
    // the unscheduled list). Undo restores every block's prior schedule.
    const changes = [
      ...plan.blocks.map((b) => ({
        id: b.itemId,
        patch: { scheduledStart: b.start, scheduledEnd: b.end },
      })),
      ...plan.evicted.map((e) => ({
        id: e.itemId,
        patch: { scheduledStart: "", scheduledEnd: "" },
      })),
    ];
    const label = plan.evicted.length
      ? `Replanned ${plan.blocks.length}, freed ${plan.evicted.length}`
      : `Planned ${plan.blocks.length} block${plan.blocks.length === 1 ? "" : "s"}`;
    applySchedule(label, changes);
    onClose();
  };
  // Carried over from a prior day (unfinished leftovers Rebuild swept in);
  // Reshuffled = today's own blocks moved; Added = new from the unscheduled list.
  const carried = plan?.blocks.filter((b) => b.carriedOver) ?? [];
  const moved =
    plan?.blocks.filter((b) => b.previouslyScheduled && !b.carriedOver) ?? [];
  const added = plan?.blocks.filter((b) => !b.previouslyScheduled) ?? [];
  const groupHdr =
    "mb-1 mt-3 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground first:mt-0";
  const renderBlock = (b: DayPlanResult["blocks"][number]) => {
    const dot =
      b.energy === "high"
        ? "bg-destructive"
        : b.energy === "low"
          ? "bg-success"
          : "bg-warning";
    return (
      <div
        key={b.itemId}
        className="rounded-md border border-border bg-background/60 p-2"
      >
        <div className="flex items-baseline gap-2">
          <span className="shrink-0 text-[11px] font-medium tabular-nums text-primary">
            {fmt(b.start)}–{fmt(b.end)}
          </span>
          <span className="min-w-0 flex-1 truncate text-[12.5px] text-foreground">
            {b.title}
          </span>
          {b.energy && (
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
          )}
        </div>
        {b.rationale && (
          <p className="mt-0.5 text-[10.5px] text-muted-foreground">
            {b.rationale}
          </p>
        )}
      </div>
    );
  };
  const dropRow = (u: DayPlanResult["unplaced"][number]) => (
    <div key={u.itemId} className="flex items-baseline gap-2 text-[11.5px]">
      <span className="min-w-0 flex-1 truncate text-muted-foreground">
        {u.title}
      </span>
      <span className="shrink-0 text-[10px] text-muted-foreground/70">
        {u.reason}
      </span>
    </div>
  );

  const fmt = (iso: string) => fmtClock(new Date(iso));
  const dateLabel = target.toLocaleDateString(undefined, {
    weekday: "long",
    month: "short",
    day: "numeric",
  });
  // The planner fell back to deterministic priority order — the LLM was
  // unavailable, so the standing prompt AND today's note were NOT applied.
  const fellBack = plan?.rankedBy === "priority" && plan.blocks.length > 0;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-2xl border border-border bg-card shadow-2xl sm:max-h-[85vh] sm:max-w-lg sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* grab handle (mobile) */}
        <div className="flex shrink-0 justify-center pt-2 sm:hidden">
          <span className="h-1 w-10 rounded-full bg-border" />
        </div>
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
          {isReplan ? (
            <Icon name="CalendarClock" className="h-4 w-4 shrink-0 text-primary" />
          ) : (
            <Icon name="Wand2" className="h-4 w-4 shrink-0 text-primary" />
          )}
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-foreground">
              {isReplan ? "Fit what's left" : "Rebuild my day"}
            </h2>
            <p className="truncate text-[11px] text-muted-foreground">
              {isReplan
                ? `Reshuffle today into the time left · ${dateLabel}`
                : dateLabel}
            </p>
          </div>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" onClick={onClose} aria-label="Close" className="rounded-md">
            <Icon name="X" className="h-4 w-4" />
          </Button>
        </div>

        {/* Plan-through horizon — how far into the day (or night) to schedule.
            Defaults to your working hours; a preset works "N hours from now", or
            pick "Custom time…" for an exact end time (any time up to — and past —
            midnight, so late-night sessions plan 24/7). Re-plans on change. */}
        <div className="flex shrink-0 flex-col gap-2 border-b border-border px-4 py-2">
          <div className="flex items-center gap-2">
            <Icon name="CalendarClock" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <label
              htmlFor="plan-horizon"
              className="shrink-0 text-[11px] font-medium text-foreground"
            >
              Plan through
            </label>
            <select
              id="plan-horizon"
              value={horizon}
              onChange={(e) => {
                const v = e.target.value;
                setHorizon(v);
                // Only LIVE re-plan once a plan is on screen — before the first
                // run, changing the horizon just updates the pending setting so
                // planning stays an explicit tap (with your note).
                if (plan) void run(note, v);
              }}
              disabled={loading}
              className="min-w-0 flex-1 rounded-md border border-border bg-background/60 px-2 py-1.5 text-xs text-foreground focus:border-primary/50 focus:outline-none disabled:opacity-50"
            >
              <option value="work">
                Working hours · till {fmt(workEndAt().toISOString())}
              </option>
              {planningToday &&
                [1, 2, 3, 4, 6].map((h) => (
                  <option key={h} value={String(h)}>
                    {h} more hour{h === 1 ? "" : "s"} · till{" "}
                    {fmt(endForHorizon(String(h), openedAtMs).toISOString())}
                  </option>
                ))}
              <option value="custom">Custom time…</option>
            </select>
          </div>
          {horizon === "custom" && (
            <div className="flex items-center gap-2 pl-[22px]">
              <label
                htmlFor="plan-horizon-time"
                className="shrink-0 text-[11px] text-muted-foreground"
              >
                Work until
              </label>
              <input
                id="plan-horizon-time"
                type="time"
                value={customTime}
                onChange={(e) => {
                  setCustomTime(e.target.value);
                  if (plan) void run(note, "custom");
                }}
                disabled={loading}
                className="rounded-md border border-border bg-background/60 px-2 py-1.5 text-xs text-foreground focus:border-primary/50 focus:outline-none disabled:opacity-50"
              />
              <span className="truncate text-[11px] text-muted-foreground">
                {planningToday &&
                customEndAt(openedAtMs).getDate() !== target.getDate()
                  ? "tomorrow"
                  : "today"}
              </span>
            </div>
          )}
        </div>

        {/* Today's note — an optional per-run prompt that steers the AI's
            selection/order (Plan) or ordering (Fit) for THIS run, ON TOP of your
            standing planning prompt (Settings) and OVERRIDING it where the two
            conflict. Shown in both modes; the server threads it to the ranker and
            also reads a horizon from it ("until 2am", "work for 2 more hours"). */}
        <div className="shrink-0 border-b border-border px-4 py-2.5">
          <div className="mb-1 flex items-center justify-between gap-2">
            <label
              htmlFor="plan-note"
              className="text-[11px] font-medium text-foreground"
            >
              Anything special about today?{" "}
              <span className="font-normal text-muted-foreground">
                (optional)
              </span>
            </label>
            {onOpenSettings && (
              <Button variant="text" size="none" layout="inline-flex items-center" type="button" onClick={() => {
                  onClose();
                  onOpenSettings();
                }} className="gap-1 text-[10px]">
                <Icon name="Settings2" className="h-3 w-3" />
                Standing prompt
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <input
              id="plan-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void run(note);
              }}
              placeholder="e.g. “low energy”, “calls only”, “deep work”, “work for 2 more hours”, “until 2am”"
              className="min-w-0 flex-1 rounded-md border border-border bg-background/60 px-3 py-2 text-base text-foreground focus:border-primary/50 focus:outline-none sm:text-sm"
            />
            <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={() => void run(note)} disabled={loading} className="shrink-0 gap-1.5 rounded-md px-3 py-2 text-xs">
              {loading ? (
                <Icon name="Loader2" className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Icon name="Wand2" className="h-3.5 w-3.5" />
              )}
              {plan ? "Redo" : isReplan ? "Fit" : "Plan"}
            </Button>
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">
            {isReplan
              ? "Reshuffles today's blocks into the time left. Your note orders them and can override the standing prompt for this run."
              : "Applied on top of your standing plan, overriding it where they conflict."}
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {error ? (
            <p className="flex items-center gap-1.5 text-xs text-destructive">
              <Icon name="AlertTriangle" className="h-4 w-4 shrink-0" /> {error}
            </p>
          ) : loading && !plan ? (
            <p className="flex items-center gap-1.5 py-6 text-xs text-muted-foreground">
              <Icon name="Loader2" className="h-4 w-4 animate-spin" /> Planning your day…
            </p>
          ) : plan ? (
            <>
              {/* How the day was ordered — reassure when AI ran, warn (not fail)
                  when it fell back so the user knows their prompt wasn't used.
                  Both modes rank via the LLM when candidates exist. */}
              {plan.blocks.length > 0 &&
                (fellBack ? (
                  <p className="mb-2 flex items-start gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2.5 py-2 text-[11px] text-warning">
                    <Icon name="AlertTriangle" className="mt-px h-3.5 w-3.5 shrink-0" />
                    <span>
                      AI ranking was unavailable, so this used priority order —
                      your note and standing prompt weren&apos;t applied. Times
                      are still packed around your calendar. Try Re-plan.
                      {plan.rankNote && (
                        <span className="mt-1 block text-warning/80">
                          Reason: {plan.rankNote}
                        </span>
                      )}
                    </span>
                  </p>
                ) : (
                  <p className="mb-2 inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                    <Icon name="Sparkles" className="h-3 w-3" /> AI-ranked for your prompt
                  </p>
                ))}
              {plan.notes && (
                <p className="mb-2 rounded-md bg-primary/5 px-2.5 py-2 text-xs text-muted-foreground">
                  {plan.notes}
                </p>
              )}
              {/* Headline: what this plan does, in one line. */}
              <p className="mb-2 text-[11px] text-muted-foreground">
                {(() => {
                  const parts: string[] = [];
                  if (carried.length) parts.push(`${carried.length} carried over`);
                  if (moved.length) parts.push(`${moved.length} moved`);
                  if (added.length) parts.push(`${added.length} added`);
                  if (plan.evicted.length)
                    parts.push(`${plan.evicted.length} back to list`);
                  return parts.length ? parts.join(" · ") + " · " : "";
                })()}
                {Math.round((plan.usedMins / 60) * 10) / 10}h of{" "}
                {Math.round((plan.capacityMins / 60) * 10) / 10}h capacity
              </p>

              {carried.length > 0 && (
                <>
                  <p className={`${groupHdr} text-primary`}>
                    Carried over from earlier ({carried.length})
                  </p>
                  <div className="flex flex-col gap-1.5">
                    {carried.map(renderBlock)}
                  </div>
                </>
              )}
              {moved.length > 0 && (
                <>
                  <p className={groupHdr}>Reshuffled ({moved.length})</p>
                  <div className="flex flex-col gap-1.5">
                    {moved.map(renderBlock)}
                  </div>
                </>
              )}
              {added.length > 0 && (
                <>
                  <p className={groupHdr}>Added from your list ({added.length})</p>
                  <div className="flex flex-col gap-1.5">
                    {added.map(renderBlock)}
                  </div>
                </>
              )}
              {plan.evicted.length > 0 && (
                <>
                  <p className={`${groupHdr} text-warning`}>
                    Back to your list ({plan.evicted.length})
                  </p>
                  <div className="flex flex-col gap-1">
                    {plan.evicted.map(dropRow)}
                  </div>
                </>
              )}
              {plan.unplaced.length > 0 && (
                <>
                  <p className={groupHdr}>
                    Left for another day ({plan.unplaced.length})
                  </p>
                  <div className="flex flex-col gap-1">
                    {plan.unplaced.map(dropRow)}
                  </div>
                </>
              )}
              {plan.blocks.length === 0 &&
                plan.unplaced.length === 0 &&
                plan.evicted.length === 0 && (
                  <p className="py-4 text-center text-xs text-muted-foreground">
                    {isReplan
                      ? "Nothing to reshuffle — the rest of your day is clear."
                      : "Nothing to schedule — no unscheduled next actions."}
                  </p>
                )}
            </>
          ) : (
            // Pre-run state — nothing plans until you tap the button, so a
            // per-run note is never skipped by an auto-run.
            <div className="flex flex-col items-center gap-2 py-8 text-center">
              {isReplan ? (
                <Icon name="CalendarClock" className="h-6 w-6 text-muted-foreground/60" />
              ) : (
                <Icon name="Wand2" className="h-6 w-6 text-muted-foreground/60" />
              )}
              <p className="text-[12.5px] font-medium text-foreground">
                {isReplan
                  ? "Fit today's tasks into the time left"
                  : "Rebuild your day"}
              </p>
              <p className="max-w-[16rem] text-[11px] text-muted-foreground">
                Add an optional note above and pick how far to plan through, then
                tap{" "}
                <span className="font-medium text-foreground">
                  {isReplan ? "Fit" : "Plan"}
                </span>{" "}
                to see a proposal before anything changes.
              </p>
            </div>
          )}
        </div>

        <div
          className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3"
          style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
        >
          <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={onClose} className="rounded-md px-3 py-2 text-xs">
            Cancel
          </Button>
          <Button size="none" radius="keep" layout="inline-flex items-center" type="button" onClick={apply} disabled={!plan || (plan.blocks.length === 0 && plan.evicted.length === 0)} className="gap-1.5 rounded-md px-3 py-2 text-xs">
            <Icon name="Check" className="h-3.5 w-3.5" />
            Apply
            {plan?.blocks.length
              ? ` ${plan.blocks.length} block${plan.blocks.length === 1 ? "" : "s"}`
              : " plan"}
          </Button>
        </div>
      </div>
    </div>
  );
}
