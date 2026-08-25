"use client";

// CalendarView — the orchestrator for the timeboxing surface (Focus OS).
// Day/Week/Month grid + the Now/Next bar, the unscheduled rail, the AI planner
// panels and the daily rituals. This file owns state + the schedule mutations
// (all routed through the store's undoable applySchedule); the pieces live in
// ./calendar/* (TimeGrid, MonthGrid, UnscheduledRail, NowNextBar, PlanDayPanel,
// EndOfDayReview, ScheduleSheet, CalendarSettings) with shared geometry and
// formatters in ./calendar/shared. Focus Mode renders globally (FocusSession in
// AppShell). Specs: calendar_timeboxing.md, calendar_focus_os.md,
// calendar_ai_review.md.

import Button from "@/components/ui/Button";
import Icon from "@/components/Icon";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  apiGetDayState,
  apiSetDayState,
} from "@/app/tasks/lib/api";
import { useTaskStore } from "@/app/tasks/lib/taskStore";
import { GtdItem } from "@/app/tasks/lib/types";
import {
  DEFAULT_BLOCK_MINS,
  startOfDay,
  addDays,
  sameDay,
  blocksForDay,
  firstFreeSlot,
  type Block,
} from "@/app/tasks/lib/scheduling";
import {
  dayKey,
  loadFocusPrefs,
  oneThingIdFor,
  saveFocusPrefs,
  toggleOneThing,
} from "@/app/tasks/lib/focusPrefs";
import {
  DAY_START_HOUR,
  DAY_END_HOUR,
  SOFT_CAPACITY_MINS,
  SNAP_MINS,
  HOUR_PX,
  addMonths,
  startOfWeek,
  useNow,
  type Mode,
  type OutcomeById,
} from "./components/shared";
import { StartupRitual } from "./components/StartupRitual";
import { NowNextBar } from "./components/NowNextBar";
import { EndOfDayReview } from "./components/EndOfDayReview";
import { ScheduleSheet } from "./components/ScheduleSheet";
import { PlanDayPanel } from "./components/PlanDayPanel";
import { CalendarSettings } from "./components/CalendarSettings";
import { TimeGrid } from "./components/TimeGrid";
import { MonthGrid } from "./components/MonthGrid";
import { UnscheduledRail } from "./components/UnscheduledRail";

export function CalendarView() {
  const items = useTaskStore((s) => s.items);
  const projects = useTaskStore((s) => s.projects);
  const updateItem = useTaskStore((s) => s.updateItem);
  const applySchedule = useTaskStore((s) => s.applySchedule);
  const openSchedule = useTaskStore((s) => s.openSchedule);
  const requestDelete = useTaskStore((s) => s.requestDelete);
  const openFocus = useTaskStore((s) => s.openFocus);
  const quickDispose = useTaskStore((s) => s.quickDispose);
  const loadDone = useTaskStore((s) => s.loadDone);
  const settings = useTaskStore((s) => s.settings);
  // ONE grid, like Google Calendar: it always renders the full 24 hours, so you
  // can view and schedule at any hour (no working-vs-24h toggle). The user's
  // WORKING HOURS aren't a wall — they're a soft zone: shaded on the grid, and
  // the default window the AI planner / auto-place fills. Direct manipulation
  // (tap a slot, drag, resize) works across all 24h.
  const workStart = settings.dayStartHour ?? DAY_START_HOUR;
  const workEnd = Math.max(workStart + 1, settings.dayEndHour ?? DAY_END_HOUR);
  const capacityTarget = settings.dailyCapacityMins ?? SOFT_CAPACITY_MINS;
  const energyWindows = settings.energyWindows ?? [];
  const updateSettings = useTaskStore((s) => s.updateSettings);
  const [mode, setMode] = useState<Mode>("day");
  const [anchor, setAnchor] = useState<Date>(() => startOfDay(new Date()));
  const [settingsOpen, setSettingsOpen] = useState(false);
  // The AI planner panel: "plan" fills free slots from unscheduled next actions;
  // "replan" reorganizes the rest of today's flexible blocks from now. null = shut.
  const [planMode, setPlanMode] = useState<null | "plan" | "replan">(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  // Mobile / tap-to-schedule sheet. `at` set ⇒ a tapped slot (schedule at that
  // exact time); absent ⇒ opened from the FAB (auto-place into the first free
  // slot of `day`). The rail is desktop-only, so this is the phone path.
  const [sheet, setSheet] = useState<{ day: Date; at?: Date } | null>(null);
  // One live clock drives the now-line, the current-block ring and the Now/Next
  // countdowns so the whole surface ages in real time (30s tick).
  const now = useNow();
  // Focus Mode — the store-held session (F1): the room + its minimizable dock
  // live globally in AppShell now, so entering here survives navigation.
  const enterFocusSession = useTaskStore((s) => s.enterFocusSession);
  // Startup ritual (F0): the modal itself + the once-a-day offer banner. Both
  // resolve client-side from focusPrefs in an effect (localStorage isn't
  // available during SSR, and reading it in render would mismatch hydration).
  const [startupOpen, setStartupOpen] = useState(false);
  const [startupOffered, setStartupOffered] = useState(false);
  const [dayClosed, setDayClosed] = useState(false);
  // Today's ★ One Thing (per-day, prefs-backed). Re-read on the clock tick so
  // midnight rolls it over without a reload.
  const [oneThingId, setOneThingId] = useState<string | null>(null);
  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect -- prefs live in
       localStorage (client-only); reading them in render would mismatch the
       SSR hydration, so they resolve here on mount + the clock tick. */
    const prefs = loadFocusPrefs();
    setOneThingId(oneThingIdFor(new Date(), prefs));
    setStartupOffered(prefs.startupDoneOn !== dayKey());
    setDayClosed(prefs.dayClosedOn === dayKey());
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [now]);
  // The 24h grid is tall, so on open (and when switching to day/week) scroll to
  // ~1h before the current time — you land where the action is, not at midnight.
  const gridScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (mode === "month") return;
    const el = gridScrollRef.current;
    if (!el) return;
    const h = new Date().getHours();
    el.scrollTop = Math.max(0, (h - 1) * HOUR_PX);
  }, [mode]);
  const handleToggleOneThing = (id: string) => {
    const prev = oneThingIdFor(new Date());
    toggleOneThing(new Date(), id);
    const nextId = prev === id ? null : id;
    setOneThingId(nextId);
    // Sync to the server so the AI planner / chat agent / digest see it, and it
    // follows the user across devices. Best-effort — the local cache already
    // reflects it; a transient failure reconciles on next load.
    void apiSetDayState(dayKey(new Date()), { oneThingId: nextId ?? "" }).catch(
      () => {},
    );
  };
  // Hydrate the ★ One Thing from the server once on open (server is the source
  // of truth; reconcile the local cache so a cross-device change is reflected).
  useEffect(() => {
    let alive = true;
    void apiGetDayState(dayKey(new Date()))
      .then((s) => {
        if (!alive) return;
        const localId = oneThingIdFor(new Date());
        if (s.oneThingId && s.oneThingId !== localId) {
          saveFocusPrefs({
            oneThing: { date: dayKey(new Date()), itemId: s.oneThingId },
          });
          setOneThingId(s.oneThingId);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
    // once per mount — the clock-tick effect handles midnight rollover
  }, []);
  const oneThingItem = items.find((i) => i.id === oneThingId) ?? null;

  // project outcome lookup — the outcome ribbon on blocks (§4.7).
  const outcomeById: OutcomeById = useMemo(
    () => new Map(projects.map((p) => [p.id, p.outcome])),
    [projects],
  );

  // Persist the user's real IANA timezone so the server-side nightly roll-over
  // computes their local-day boundary correctly (defaults to UTC otherwise).
  useEffect(() => {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz && tz !== settings.timezone) void updateSettings({ timezone: tz });
  }, [settings.timezone, updateSettings]);

  // Pull DONE tasks into the store so COMPLETED time-blocks stay on the grid
  // (they're excluded from the normal hydrate). Keeps the sense of progress.
  useEffect(() => {
    void loadDone();
  }, [loadDone]);

  // Deadline radar: unscheduled next actions with a due date approaching (≤14d)
  // — the ones most likely to slip because they were never timeboxed.
  const dueSoon = useMemo(() => {
    // eslint-disable-next-line react-hooks/purity
    const nowMs = Date.now();
    const horizon = nowMs + 14 * 86400000;
    return items
      .filter(
        (i) =>
          i.isMine &&
          i.disposition === "NEXT" &&
          !i.scheduledStart &&
          !i.archivedAt &&
          i.dueAt &&
          new Date(i.dueAt).getTime() <= horizon,
      )
      .map((i) => ({
        item: i,
        days: Math.ceil((new Date(i.dueAt as string).getTime() - nowMs) / 86400000),
      }))
      .sort((a, b) => a.days - b.days);
  }, [items]);

  // Unscheduled, schedulable next actions (mine, NEXT, no block yet).
  const unscheduled = useMemo(
    () =>
      items.filter(
        (i) =>
          i.disposition === "NEXT" &&
          i.isMine &&
          !i.scheduledStart &&
          !i.archivedAt,
      ),
    [items],
  );

  // Completed blocks on the focused day — the "done today" tally (progress).
  const doneStats = useMemo(() => {
    const done = blocksForDay(items, anchor).filter(
      (b) => b.item.disposition === "DONE",
    );
    return {
      count: done.length,
      mins: done.reduce(
        (n, b) => n + (b.end.getTime() - b.start.getTime()) / 60000,
        0,
      ),
    };
  }, [items, anchor]);

  // Every scheduling mutation goes through applySchedule so it lands in the
  // undo toast — a mis-drag or stray unschedule is always one tap from safe.
  // Without `at`, the task lands in the day's FIRST FREE SLOT (from now, when
  // the day is today). An already-scheduled item keeps its block length and
  // its own old block never counts as "busy".
  const schedule = (item: GtdItem, day: Date, at?: Date, label = "Scheduled") => {
    const blockMins =
      item.scheduledStart && item.scheduledEnd
        ? Math.max(
            SNAP_MINS,
            Math.round(
              (new Date(item.scheduledEnd).getTime() -
                new Date(item.scheduledStart).getTime()) /
                60000,
            ),
          )
        : undefined;
    const mins = blockMins ?? item.timeEstimateMins ?? DEFAULT_BLOCK_MINS;
    const start =
      at ??
      firstFreeSlot(
        blocksForDay(items, day).filter((b) => b.item.id !== item.id),
        day,
        mins,
        workStart,
        workEnd,
      );
    const end = new Date(start.getTime() + mins * 60000);
    applySchedule(label, [
      {
        id: item.id,
        patch: {
          scheduledStart: start.toISOString(),
          scheduledEnd: end.toISOString(),
        },
      },
    ]);
  };
  // "Move to next free slot" — the one-gesture fix for an overdue block (and
  // the menu's home for the old Timebox-into-first-slot behavior).
  const moveToNextFree = (item: GtdItem) =>
    schedule(item, startOfDay(new Date()), undefined, "Moved to next free slot");
  const unschedule = (item: GtdItem) =>
    applySchedule("Removed from calendar", [
      { id: item.id, patch: { scheduledStart: "", scheduledEnd: "" } },
    ]);
  // Move/resize a block to an exact start+end (drag-drop + resize commit here).
  const reschedule = (
    id: string,
    start: Date,
    end: Date,
    label = "Moved block",
  ) =>
    applySchedule(label, [
      {
        id,
        patch: {
          scheduledStart: start.toISOString(),
          scheduledEnd: end.toISOString(),
        },
      },
    ]);

  // Focus timer: stamp when you actually START a block (clears any prior end so
  // it re-times cleanly). Actual work-time = actualEnd − actualStart.
  const startFocus = (item: GtdItem) =>
    updateItem(item.id, { actualStart: new Date().toISOString(), actualEnd: "" });

  // Complete/uncomplete a block. Finishing a STARTED (timed) session stamps
  // actualEnd = now so planned-vs-actual is captured; reopening clears it.
  const completeBlock = (item: GtdItem) => {
    const toDone = item.disposition !== "DONE";
    if (toDone) {
      if (item.actualStart && !item.actualEnd)
        updateItem(item.id, { actualEnd: new Date().toISOString() });
    } else if (item.actualEnd) {
      updateItem(item.id, { actualEnd: "" });
    }
    quickDispose(item.id, toDone ? "DONE" : "NEXT");
  };

  // Overdue = a past time-block whose task isn't done → offer a one-click
  // roll-over into today's open slots (the "fell behind → stale plan" failure).
  // The list itself feeds the startup ritual's carry-forward step.
  const overdueItems = useMemo(() => {
    // Real wall-clock is intentional here — the calendar is a live surface.
    // (Every task surface reads the real clock now; the frozen demo constant
    // this comment used to contrast against is gone.)
    // eslint-disable-next-line react-hooks/purity
    const nowMs = Date.now();
    return items.filter(
      (i) =>
        i.scheduledStart &&
        i.scheduledEnd &&
        i.disposition !== "DONE" &&
        new Date(i.scheduledEnd).getTime() < nowMs,
    );
  }, [items]);
  const overdueCount = overdueItems.length;

  // Leverage meter (80/20): of the focused day's booked focus-time, how much
  // sits on leveraged/important work (the ★ One Thing always counts — it IS
  // the 20%). Rendered beside the capacity meter.
  const leverageStats = useMemo(() => {
    const blocks = blocksForDay(items, anchor);
    const mins = (b: Block) => (b.end.getTime() - b.start.getTime()) / 60000;
    const total = blocks.reduce((n, b) => n + mins(b), 0);
    const lev = blocks
      .filter(
        (b) => b.item.leveraged || b.item.important || b.item.id === oneThingId,
      )
      .reduce((n, b) => n + mins(b), 0);
    return { totalMins: total, leveragedMins: lev };
  }, [items, anchor, oneThingId]);

  // Is there anything left to reorganize today? "Replan the rest of my day" only
  // makes sense with ≥1 flexible, not-done block still ahead (end ≥ now) — a
  // fixed meeting or a finished block isn't movable, so it wouldn't offer this.
  // "Fit what's left" is offered whenever there's ≥1 movable block ANYWHERE on
  // today (flexible + not done) — including ones whose time already slipped past
  // earlier today, since Fit sweeps those forward into the time remaining.
  const canReplan = useMemo(() => {
    return items.some(
      (i) =>
        i.scheduledStart &&
        i.disposition !== "DONE" &&
        (i.flexible ?? true) &&
        sameDay(new Date(i.scheduledStart), now),
    );
  }, [items, now]);

  // Anything scheduled today (done or not) → an end-of-day review has something
  // to reflect on. Independent of the browsed day (the review is about today).
  const hasTodayActivity = useMemo(
    () =>
      items.some(
        (i) => i.scheduledStart && sameDay(new Date(i.scheduledStart), now),
      ),
    [items, now],
  );

  // Roll-over now RETURNS unfinished tasks to the unscheduled list (clears their
  // schedule) instead of auto-cramming them onto today — you re-plan them
  // deliberately (drag, or Rebuild my day). Instant + undoable; they land in the
  // rail. (The nightly auto-return does the same server-side.)
  const rollOver = () => {
    if (!overdueItems.length) return;
    applySchedule(
      `Moved ${overdueItems.length} unfinished task${overdueItems.length === 1 ? "" : "s"} to your list`,
      overdueItems.map((i) => ({
        id: i.id,
        patch: { scheduledStart: "", scheduledEnd: "" },
      })),
    );
  };

  const step = (dir: 1 | -1) =>
    setAnchor((a) =>
      mode === "month"
        ? addMonths(a, dir)
        : addDays(a, dir * (mode === "week" ? 7 : 1)),
    );

  const title =
    mode === "month"
      ? anchor.toLocaleDateString(undefined, { month: "long", year: "numeric" })
      : mode === "week"
        ? `Week of ${startOfWeek(anchor).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
        : anchor.toLocaleDateString(undefined, {
            weekday: "long",
            month: "long",
            day: "numeric",
          });

  const days =
    mode === "week"
      ? Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(anchor), i))
      : [anchor];

  return (
    <div className="relative flex h-full min-h-0 flex-col bg-background">
      {/* Header: title, nav, mode toggle */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <Icon name="CalendarDays" className="h-5 w-5 shrink-0 text-primary" />
        <h1 className="text-base font-semibold text-foreground">{title}</h1>
        <div className="ml-2 flex items-center gap-0.5">
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" aria-label="Previous" onClick={() => step(-1)} className="rounded-md">
            <Icon name="ChevronLeft" className="h-4 w-4" />
          </Button>
          <Button variant="ghost" size="none" radius="keep" layout="" type="button" onClick={() => setAnchor(startOfDay(new Date()))} className="rounded-md px-2 py-1 text-xs">
            Today
          </Button>
          <Button variant="ghost" size="icon-xs" radius="keep" layout="" type="button" aria-label="Next" onClick={() => step(1)} className="rounded-md">
            <Icon name="ChevronRight" className="h-4 w-4" />
          </Button>
        </div>
        {/* Actions: share the row with the title on desktop (ml-auto), but on
            mobile take a FULL second row so the pills never get squeezed into
            wrapping against the date. */}
        <div className="relative flex w-full items-center gap-2 sm:ml-auto sm:w-auto">
          {dueSoon.length > 0 && (
            <span
              title={`${dueSoon.length} unscheduled task(s) due within 2 weeks`}
              className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning"
            >
              <Icon name="AlertTriangle" className="h-3 w-3" />
              {dueSoon.length} due soon
            </span>
          )}
          {/* (Re)start the day — the ritual is never locked out: skipped the
              morning banner, or want to re-pick the One Thing? Run it again. */}
          {!dayClosed && (
            <button
              type="button"
              onClick={() => setStartupOpen(true)}
              title="Start (or restart) your day — breathe · review · commit the One Thing"
              className="tech-transition inline-flex items-center gap-1.5 rounded-md bg-secondary px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-secondary/70 hover:text-foreground"
            >
              <Icon name="Sun" className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Start day</span>
            </button>
          )}
          {hasTodayActivity && (
            <button
              type="button"
              onClick={() => setReviewOpen(true)}
              title="End-of-day review — what got done, what carries forward, estimate accuracy"
              className="tech-transition inline-flex items-center gap-1.5 rounded-md bg-secondary px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-secondary/70 hover:text-foreground"
            >
              <Icon name="ClipboardCheck" className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Review</span>
            </button>
          )}
          {canReplan && (
            <button
              type="button"
              onClick={() => setPlanMode("replan")}
              title="Fit what's left: reshuffle today's not-done tasks into the time you have left, and move anything that no longer fits back to your list. Adds no new tasks."
              className="tech-transition inline-flex items-center gap-1.5 rounded-md bg-secondary px-2.5 py-1.5 text-xs font-medium text-muted-foreground hover:bg-secondary/70 hover:text-foreground"
            >
              <Icon name="CalendarClock" className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Fit what&apos;s left</span>
            </button>
          )}
          <button
            type="button"
            onClick={() => setPlanMode("plan")}
            title="Rebuild my day: reshuffle what's on your calendar into the time left, trim the overflow back to your list, and fill any remaining room from your Next Actions."
            className="tech-transition inline-flex items-center gap-1.5 rounded-md bg-primary/10 px-2.5 py-1.5 text-xs font-medium text-primary hover:bg-primary/20"
          >
            <Icon name="Wand2" className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Rebuild my day</span>
          </button>
          <button
            type="button"
            onClick={() => setSettingsOpen((v) => !v)}
            aria-label="Calendar settings"
            title="Day window, capacity, energy windows"
            className={[
              "tech-transition rounded-md p-1.5",
              settingsOpen
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
            ].join(" ")}
          >
            <Icon name="Settings2" className="h-4 w-4" />
          </button>
          <div className="ml-auto flex rounded-lg bg-secondary p-0.5 text-xs sm:ml-0">
            {(["day", "week", "month"] as Mode[]).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={[
                  "tech-transition rounded-md px-2.5 py-1 font-medium capitalize",
                  mode === m
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                ].join(" ")}
              >
                {m}
              </button>
            ))}
          </div>
          {settingsOpen && (
            <CalendarSettings
              settings={settings}
              onChange={(patch) => void updateSettings(patch)}
              onClose={() => setSettingsOpen(false)}
            />
          )}
        </div>
      </div>

      {/* Now / Next — always reflects the *real* current time (today), even when
          you're browsing another day/week. The antidote to time-blindness, and
          the one scheduling-aware surface that's also visible on mobile. */}
      <NowNextBar
        now={now}
        items={items}
        onOpen={openFocus}
        onComplete={completeBlock}
        onStart={(item) => {
          startFocus(item);
          enterFocusSession(item.id);
        }}
        onFillGap={() => {
          // idle gap → the Gap Filler sheet, anchored to right now
          const at = new Date(Math.ceil(Date.now() / 300000) * 300000);
          setSheet({ day: startOfDay(new Date()), at });
        }}
      />

      {/* Startup ritual offer — once per local day, dismissible. Habits form
          around rituals; the banner invites, it never ambushes. */}
      {startupOffered && !startupOpen && !dayClosed && (
        <div className="flex items-center gap-2 border-b border-border bg-warning/5 px-4 py-2 text-xs">
          <Icon name="Sun" className="h-4 w-4 shrink-0 text-warning" />
          <span className="min-w-0 flex-1 text-foreground">
            Start your day — breathe · review · commit the One Thing.
          </span>
          <button
            type="button"
            onClick={() => setStartupOpen(true)}
            className="tech-transition shrink-0 rounded-md bg-warning/20 px-2.5 py-1 font-medium text-warning hover:bg-warning/30"
          >
            Begin (5 min)
          </button>
          <Button variant="text" size="none" radius="keep" layout="" type="button" onClick={() => {
              saveFocusPrefs({ startupDoneOn: dayKey() });
              setStartupOffered(false);
            }} className="shrink-0 rounded-md px-2 py-1">
            Skip
          </Button>
        </div>
      )}

      {overdueCount > 0 && (
        <div className="flex items-center gap-2 border-b border-warning/40 bg-warning/10 px-4 py-2 text-xs">
          <Icon name="AlertTriangle" className="h-4 w-4 shrink-0 text-warning" />
          <span className="min-w-0 flex-1 text-foreground">
            {overdueCount} unfinished task{overdueCount === 1 ? "" : "s"} from
            earlier {overdueCount === 1 ? "is" : "are"} still on your calendar.
          </span>
          <button
            type="button"
            onClick={rollOver}
            title="Clear their times and move them back to your unscheduled list, so you can re-plan them (drag, or Rebuild my day)."
            className="tech-transition inline-flex shrink-0 items-center gap-1.5 rounded-md bg-warning/20 px-2.5 py-1 font-medium text-warning hover:bg-warning/30"
          >
            <Icon name="RotateCcw" className="h-3.5 w-3.5" />
            Move to my list
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div ref={gridScrollRef} className="min-w-0 flex-1 overflow-auto">
          {mode === "month" ? (
            <MonthGrid
              anchor={anchor}
              items={items}
              onPickDay={(d) => {
                setAnchor(d);
                setMode("day");
              }}
              onOpen={openFocus}
            />
          ) : (
            <TimeGrid
              days={days}
              items={items}
              now={now}
              dayStart={0}
              dayEnd={24}
              workStart={workStart}
              workEnd={workEnd}
              energyWindows={energyWindows}
              lunchStartHour={settings.lunchStartHour}
              lunchEndHour={settings.lunchEndHour}
              dayTemplates={settings.dayTemplates}
              oneThingId={oneThingId}
              outcomeById={outcomeById}
              onToggleOneThing={handleToggleOneThing}
              onFocusMode={(item) => {
                if (!item.actualStart || item.actualEnd) startFocus(item);
                enterFocusSession(item.id);
              }}
              onOpen={openFocus}
              onUnschedule={unschedule}
              onComplete={completeBlock}
              reschedule={reschedule}
              onPickSlot={(day, at) => setSheet({ day, at })}
              onSetFlexible={(item, flexible) =>
                updateItem(item.id, { flexible })
              }
              onReschedulePopup={openSchedule}
              onMoveToFree={moveToNextFree}
              onDelete={(id) => requestDelete([id])}
            />
          )}
        </div>

        {/* Unscheduled rail — click a task to timebox it into the focused day.
            Hidden in month mode. */}
        {mode !== "month" && (
          <UnscheduledRail
            tasks={unscheduled}
            capacityMins={leverageStats.totalMins}
            capacityTarget={capacityTarget}
            leveragedMins={leverageStats.leveragedMins}
            oneThingId={oneThingId}
            onToggleOneThing={handleToggleOneThing}
            dueSoon={dueSoon}
            urgentWindowHours={settings.urgentWindowHours}
            doneStats={doneStats}
            onPlan={() => setPlanMode("plan")}
            onOpen={openFocus}
            onTimebox={(t) =>
              schedule(t, mode === "week" ? startOfWeek(anchor) : anchor)
            }
            onReschedulePopup={openSchedule}
            onDelete={(id) => requestDelete([id])}
          />
        )}
      </div>

      {/* Mobile scheduling entry point — the rail is desktop-only and touch has
          no drag-and-drop, so on a phone this is how you timebox: pick a task
          (auto-placed) or Plan the whole day. Anchored to the calendar area so
          it sits above the app's bottom bar. */}
      {mode !== "month" && (
        <button
          type="button"
          onClick={() =>
            setSheet({ day: mode === "week" ? startOfWeek(anchor) : anchor })
          }
          aria-label="Schedule a task"
          className="tech-transition absolute bottom-4 right-4 z-30 inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-3 font-semibold text-primary-foreground shadow-lg hover:opacity-90 md:hidden"
        >
          <Icon name="CalendarPlus" className="h-5 w-5" />
          {unscheduled.length > 0 && (
            <span className="text-sm tabular-nums">{unscheduled.length}</span>
          )}
        </button>
      )}

      {sheet && (
        <ScheduleSheet
          day={sheet.day}
          at={sheet.at}
          tasks={unscheduled}
          items={items}
          dueSoon={dueSoon}
          urgentWindowHours={settings.urgentWindowHours}
          dayEndHour={workEnd}
          onSchedule={(t, at) => {
            schedule(t, sheet.day, at);
            setSheet(null);
            // Scheduling INTO the live moment flows straight into the Focus
            // room — the Gap Filler's zero-ceremony start (§4.5).
            if (at && Math.abs(at.getTime() - Date.now()) < 5 * 60000) {
              startFocus(t);
              enterFocusSession(t.id);
            }
          }}
          onDoNow={(t) => quickDispose(t.id, "DONE")}
          onPlan={() => {
            setSheet(null);
            setPlanMode("plan");
          }}
          onClose={() => setSheet(null)}
        />
      )}

      {reviewOpen && (
        <EndOfDayReview
          now={now}
          items={items}
          oneThingId={oneThingId}
          urgentWindowHours={settings.urgentWindowHours}
          onOpen={openFocus}
          onCloseDay={() => {
            setDayClosed(true);
            setReviewOpen(false);
          }}
          onClose={() => setReviewOpen(false)}
        />
      )}

      {planMode && (
        <PlanDayPanel
          mode={planMode}
          target={
            planMode === "replan" || mode !== "day" ? startOfDay(now) : anchor
          }
          dayStart={workStart}
          dayEnd={workEnd}
          capacityMins={capacityTarget}
          bufferMins={settings.bufferMins ?? 0}
          energyWindows={energyWindows}
          onOpenSettings={() => setSettingsOpen(true)}
          extraNote={
            oneThingItem
              ? `The user's ONE Thing for today (top priority): "${oneThingItem.title}" — place it in the first high-energy window and protect it.`
              : undefined
          }
          onClose={() => setPlanMode(null)}
        />
      )}

      {startupOpen && (
        <StartupRitual
          items={items}
          urgentWindowHours={settings.urgentWindowHours}
          carryForward={overdueItems}
          onRollover={rollOver}
          onPlan={() => setPlanMode("plan")}
          onClose={() => {
            setStartupOpen(false);
            setStartupOffered(false);
            const committed = oneThingIdFor(new Date());
            setOneThingId(committed);
            // sync the ritual's One-Thing commit to the server for the planner
            void apiSetDayState(dayKey(new Date()), {
              oneThingId: committed ?? "",
            }).catch(() => {});
          }}
        />
      )}

      {/* Focus Mode renders globally (FocusSession in AppShell) — entering a
          block here just sets the store session, so the room and its
          minimized dock survive leaving the calendar. */}
    </div>
  );
}
