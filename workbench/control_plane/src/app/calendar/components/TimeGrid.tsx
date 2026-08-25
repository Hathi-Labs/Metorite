"use client";

import Icon, { themedIcon } from "@/components/Icon";
import { useRef, useState } from "react";
import {
  type EnergyWindow,
  type DayTemplate,
} from "@/app/tasks/lib/api";
import { GtdItem } from "@/app/tasks/lib/types";
import {
  sameDay,
  blocksForDay,
  type Block,
} from "@/app/tasks/lib/scheduling";
import {
} from "@/app/tasks/lib/focusPrefs";
import {
  HOUR_PX,
  DOW,
  SNAP_MINS,
  DRAG_TYPE,
  snap,
  minutesInto,
  fmtClock,
  fmtLeft,
  deadlinesForDay,
  layoutBlocks,
  reservedWindowsForDay,
  type DragPayload,
  type OutcomeById,
} from "./shared";
import { ContextMenu, type CtxItem } from "@/components/ContextMenu";


// ── Day / Week hour grid ─────────────────────────────────────────────────────
export function TimeGrid({
  days,
  items,
  now,
  dayStart,
  dayEnd,
  workStart,
  workEnd,
  energyWindows,
  lunchStartHour,
  lunchEndHour,
  dayTemplates,
  oneThingId,
  outcomeById,
  onToggleOneThing,
  onFocusMode,
  onOpen,
  onUnschedule,
  onComplete,
  reschedule,
  onPickSlot,
  onSetFlexible,
  onReschedulePopup,
  onMoveToFree,
  onDelete,
}: {
  days: Date[];
  items: GtdItem[];
  now: Date;
  /** Grid extent — always the full day (0…24), Google-Calendar style. */
  dayStart: number;
  dayEnd: number;
  /** The user's working window — not a wall, just a soft zone: the off-hours
   *  outside it are shaded, and it's the default the AI planner / auto-place
   *  fills. You can still drag / tap / resize anywhere in the 24h grid. */
  workStart: number;
  workEnd: number;
  energyWindows: EnergyWindow[];
  /** Protected lunch + recurring windows — drawn as labeled bands so reserved
   *  time (lunch, hobbies, themed focus) is visible and clearly not filled. */
  lunchStartHour?: number | null;
  lunchEndHour?: number | null;
  dayTemplates?: DayTemplate[];
  /** today's ★ One Thing (gold, protected-feeling) — null when unset. */
  oneThingId: string | null;
  outcomeById: OutcomeById;
  onToggleOneThing: (id: string) => void;
  /** enter the full-screen Focus room for a block. */
  onFocusMode: (item: GtdItem) => void;
  onOpen: (id: string) => void;
  onUnschedule: (item: GtdItem) => void;
  onComplete: (item: GtdItem) => void;
  reschedule: (id: string, start: Date, end: Date, label?: string) => void;
  /** Tap an empty grid slot → schedule a task at that snapped time. */
  onPickSlot: (day: Date, at: Date) => void;
  /** Pin (false) / unpin (true) a block so the auto-mover skips / includes it. */
  onSetFlexible: (item: GtdItem, flexible: boolean) => void;
  /** "Reschedule…" → the global Schedule popup (date/time picker + Unschedule). */
  onReschedulePopup: (id: string) => void;
  /** "Move to next free slot" — re-timebox into today's first opening (the
   *  one-gesture fix for an overdue block). */
  onMoveToFree: (item: GtdItem) => void;
  /** "Delete task…" → the store's confirm-first delete flow. */
  onDelete: (id: string) => void;
}) {
  const hours = Array.from({ length: dayEnd - dayStart }, (_, i) => dayStart + i);
  const gridHeight = hours.length * HOUR_PX;
  // Live resize (transient end while dragging the handle) + drop-target column.
  const [resizing, setResizing] = useState<{ id: string; endMs: number } | null>(null);
  const resizingRef = useRef(false); // suppress the native block-drag while resizing
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
  // Block context menu — right-click on desktop, LONG-PRESS on touch (the
  // hover micro-buttons don't exist on a phone, so this menu IS the mobile
  // path to unschedule / pin / star / focus / reschedule / delete).
  const [ctx, setCtx] = useState<{
    x: number;
    y: number;
    item: GtdItem;
    day: Date;
  } | null>(null);
  const lpTimer = useRef<number | null>(null);
  const lpFired = useRef(false);
  const startLongPress = (e: React.PointerEvent, item: GtdItem, day: Date) => {
    if (e.pointerType === "mouse") return; // mouse has real right-click
    const { clientX, clientY } = e;
    lpFired.current = false;
    lpTimer.current = window.setTimeout(() => {
      lpFired.current = true;
      setCtx({ x: clientX, y: clientY, item, day });
    }, 450);
  };
  const cancelLongPress = () => {
    if (lpTimer.current != null) {
      clearTimeout(lpTimer.current);
      lpTimer.current = null;
    }
  };

  // Drop a dragged task/block onto `day` at the cursor's Y → snapped start+end.
  const handleDrop = (e: React.DragEvent, day: Date) => {
    e.preventDefault();
    setDragOverKey(null);
    const raw = e.dataTransfer.getData(DRAG_TYPE);
    if (!raw) return;
    let p: DragPayload;
    try {
      p = JSON.parse(raw) as DragPayload;
    } catch {
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const windowMins = (dayEnd - dayStart) * 60;
    let mins = snap(((e.clientY - rect.top) / HOUR_PX) * 60 - p.grabOffsetMins);
    mins = Math.max(0, Math.min(mins, windowMins - p.durationMins));
    const start = new Date(day);
    start.setHours(dayStart, 0, 0, 0);
    start.setMinutes(start.getMinutes() + mins);
    // Honest undo label: a rail card lands as "Scheduled", an existing block
    // as "Moved block".
    const wasScheduled = !!items.find((i) => i.id === p.id)?.scheduledStart;
    reschedule(
      p.id,
      start,
      new Date(start.getTime() + p.durationMins * 60000),
      wasScheduled ? "Moved block" : "Scheduled",
    );
  };

  const onBlockDragStart = (e: React.DragEvent, b: Block) => {
    if (resizingRef.current) {
      e.preventDefault(); // grabbing the resize handle, not moving the block
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const grabOffsetMins = ((e.clientY - rect.top) / HOUR_PX) * 60;
    const durationMins = (b.end.getTime() - b.start.getTime()) / 60000;
    e.dataTransfer.setData(
      DRAG_TYPE,
      JSON.stringify({ id: b.item.id, durationMins, grabOffsetMins }),
    );
    e.dataTransfer.effectAllowed = "move";
  };

  // Resize via pointer events (native DnD is poor for edge-drag). Window
  // listeners track the drag; commit on pointer-up.
  const startResize = (e: React.PointerEvent, b: Block) => {
    e.stopPropagation();
    e.preventDefault();
    resizingRef.current = true;
    const startY = e.clientY;
    const startMs = b.start.getTime();
    const originEndMs = b.end.getTime();
    const dayEndAt = new Date(b.start);
    dayEndAt.setHours(dayEnd, 0, 0, 0);
    const clampEnd = (clientY: number) => {
      const dyMins = ((clientY - startY) / HOUR_PX) * 60;
      const endMs = originEndMs + snap(dyMins) * 60000;
      return Math.max(
        startMs + SNAP_MINS * 60000,
        Math.min(endMs, dayEndAt.getTime()),
      );
    };
    const onMove = (ev: PointerEvent) =>
      setResizing({ id: b.item.id, endMs: clampEnd(ev.clientY) });
    const onUp = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      reschedule(
        b.item.id,
        new Date(startMs),
        new Date(clampEnd(ev.clientY)),
        "Resized block",
      );
      setResizing(null);
      // Release AFTER the trailing click event, so finishing a resize doesn't
      // also open the task card (the block's click handler checks this ref).
      window.setTimeout(() => {
        resizingRef.current = false;
      }, 0);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  return (
    <div className="min-w-fit">
      {/* Column headers (week) */}
      {days.length > 1 && (
        <div className="sticky top-0 z-10 flex border-b border-border bg-background">
          <div className="w-14 shrink-0" />
          {days.map((d) => {
            const today = sameDay(d, now);
            return (
              <div
                key={d.toISOString()}
                style={{ minWidth: 96 }}
                className="flex-1 border-l border-border px-2 py-1.5 text-center"
              >
                <div className="text-[10px] font-medium uppercase text-muted-foreground">
                  {DOW[(d.getDay() + 6) % 7]}
                </div>
                <div
                  className={[
                    "text-sm font-semibold",
                    today ? "text-primary" : "text-foreground",
                  ].join(" ")}
                >
                  {d.getDate()}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="flex">
        {/* Hour gutter — each row is EXACTLY HOUR_PX so the labels stay in
            lockstep with the day-column hour lines and the now-line (a per-row
            negative margin here used to compound, drifting the labels ~3h up by
            late evening). The label is centred on the hour line at the row top. */}
        <div className="w-14 shrink-0">
          {hours.map((h) => (
            <div
              key={h}
              style={{ height: HOUR_PX }}
              className="relative pr-2 text-right text-[10px] text-muted-foreground"
            >
              <span className="absolute right-2 top-0 -translate-y-1/2">
                {h % 12 === 0 ? 12 : h % 12}
                {h < 12 ? "am" : "pm"}
              </span>
            </div>
          ))}
        </div>

        {/* Day columns */}
        {days.map((day) => {
          const blocks = blocksForDay(items, day);
          const deadlines = deadlinesForDay(items, day);
          const today = sameDay(day, now);
          const dayKey = day.toISOString();
          const nowTop =
            ((minutesInto(now) - dayStart * 60) / 60) * HOUR_PX;
          return (
            <div
              key={dayKey}
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
                if (dragOverKey !== dayKey) setDragOverKey(dayKey);
              }}
              onDragLeave={(e) => {
                if (e.currentTarget === e.target) setDragOverKey(null);
              }}
              onDrop={(e) => handleDrop(e, day)}
              onClick={(e) => {
                // Empty-slot tap → schedule-here sheet. Blocks and deadline
                // markers stopPropagation, so this only fires on bare grid.
                const rect = e.currentTarget.getBoundingClientRect();
                const rawMins = ((e.clientY - rect.top) / HOUR_PX) * 60;
                const at = new Date(day);
                at.setHours(dayStart, 0, 0, 0);
                at.setMinutes(at.getMinutes() + Math.max(0, snap(rawMins)));
                onPickSlot(day, at);
              }}
              className={[
                "relative flex-1 cursor-pointer border-l border-border",
                dragOverKey === dayKey ? "bg-primary/5" : "",
              ].join(" ")}
              // Week view on a phone: 7 columns keep a readable minimum width
              // and the grid scrolls horizontally instead of crushing blocks.
              style={{
                height: gridHeight,
                minWidth: days.length > 1 ? 96 : undefined,
              }}
            >
              {/* hour lines */}
              {hours.map((h) => (
                <div
                  key={h}
                  style={{ height: HOUR_PX }}
                  className="border-b border-border/60"
                />
              ))}

              {/* off-hours shade — the hours OUTSIDE your working window read
                  dimmer so the day has a natural shape (Google-Calendar style),
                  but they're still live: tap / drag / resize a block into the
                  early morning or late night whenever you want to. */}
              {workStart > dayStart && (
                <div
                  aria-hidden
                  className="pointer-events-none absolute inset-x-0 top-0 bg-muted/25"
                  style={{ height: (workStart - dayStart) * HOUR_PX }}
                />
              )}
              {workEnd < dayEnd && (
                <div
                  aria-hidden
                  className="pointer-events-none absolute inset-x-0 bg-muted/25"
                  style={{
                    top: (workEnd - dayStart) * HOUR_PX,
                    height: (dayEnd - workEnd) * HOUR_PX,
                  }}
                />
              )}

              {/* energy windows — peak/trough tint the planner places work into */}
              {energyWindows.map((w, wi) => {
                const bandTop = (w.start_hour - dayStart) * HOUR_PX;
                const bandH = (w.end_hour - w.start_hour) * HOUR_PX;
                if (bandH <= 0) return null;
                const tone =
                  w.energy === "high"
                    ? "bg-success/10"
                    : w.energy === "low"
                      ? "bg-muted/40"
                      : "bg-warning/10";
                return (
                  <div
                    key={`ew-${wi}`}
                    title={`${w.energy} energy ${w.start_hour}:00–${w.end_hour}:00`}
                    className={`pointer-events-none absolute inset-x-0 ${tone}`}
                    style={{ top: Math.max(0, bandTop), height: bandH }}
                  />
                );
              })}

              {/* reserved windows — protected lunch + recurring Block/Focus
                  windows, drawn as labeled bands so the user SEES the time the
                  planner won't fill (block) or reserves for a theme (focus).
                  Non-blocking: you can still tap to schedule here manually. */}
              {reservedWindowsForDay(
                day,
                lunchStartHour,
                lunchEndHour,
                dayTemplates,
              ).map((b, bi) => {
                const top = (b.startHour - dayStart) * HOUR_PX;
                const h = (b.endHour - b.startHour) * HOUR_PX;
                if (h <= 0) return null;
                const isBlock = b.kind === "block";
                return (
                  <div
                    key={`rb-${bi}`}
                    aria-hidden
                    title={
                      isBlock
                        ? `${b.label} — protected time, the planner keeps this free of tasks`
                        : `${b.label} — reserved for this kind of work`
                    }
                    className={[
                      "pointer-events-none absolute inset-x-0 overflow-hidden border-y",
                      isBlock
                        ? "border-muted-foreground/20 bg-muted/50"
                        : "border-primary/20 bg-primary/[0.06]",
                    ].join(" ")}
                    style={{
                      top: Math.max(0, top),
                      height: h,
                      ...(isBlock
                        ? {
                            backgroundImage:
                              "repeating-linear-gradient(45deg, transparent, transparent 5px, rgba(128,128,128,0.10) 5px, rgba(128,128,128,0.10) 10px)",
                          }
                        : {}),
                    }}
                  >
                    <span
                      className={[
                        "absolute left-1 top-0.5 rounded px-1 text-[9px] font-medium",
                        isBlock
                          ? "bg-muted text-muted-foreground"
                          : "bg-primary/15 text-primary",
                      ].join(" ")}
                    >
                      {isBlock ? "🔒 " : "◆ "}
                      {b.label}
                    </span>
                  </div>
                );
              })}

              {/* deadline markers (all-day, pinned top) */}
              {deadlines.length > 0 && (
                <div className="absolute inset-x-1 top-0 flex flex-col gap-0.5">
                  {deadlines.map((d) => (
                    <button
                      key={d.id}
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpen(d.id);
                      }}
                      title={`Due today: ${d.title}`}
                      className="tech-transition truncate rounded border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 text-left text-[10px] font-medium text-destructive hover:bg-destructive/20"
                    >
                      ⚑ {d.title}
                    </button>
                  ))}
                </div>
              )}

              {/* now line — pulses so "where am I in the day" is glanceable; a
                  live time pill (day view) makes the exact time readable. */}
              {today && nowTop >= 0 && nowTop <= gridHeight && (
                <div
                  className="pointer-events-none absolute inset-x-0 z-20 border-t-2 border-primary"
                  style={{ top: nowTop }}
                >
                  <span className="absolute -left-1 -top-1 h-2 w-2 animate-pulse rounded-full bg-primary" />
                  {days.length === 1 && (
                    <span className="absolute left-1 -top-2 rounded bg-primary px-1 text-[9px] font-semibold leading-tight text-primary-foreground shadow-sm">
                      {fmtClock(now)}
                    </span>
                  )}
                </div>
              )}

              {/* scheduled blocks — overlaps split into side-by-side lanes */}
              {layoutBlocks(blocks).map(({ block: b, lane, lanes }) => {
                const end =
                  resizing?.id === b.item.id ? new Date(resizing.endMs) : b.end;
                const top =
                  ((minutesInto(b.start) - dayStart * 60) / 60) * HOUR_PX;
                const mins = (end.getTime() - b.start.getTime()) / 60000;
                const height = Math.max(20, (mins / 60) * HOUR_PX - 2);
                const conflict = lanes > 1;
                const isDone = b.item.disposition === "DONE";
                // The block that contains "now" — the one you should be in.
                const isNow =
                  !isDone &&
                  b.start.getTime() <= now.getTime() &&
                  b.end.getTime() > now.getTime();
                // Fixed (meeting) block — roll-over/replan leave it put.
                const isFixed = b.item.flexible === false;
                // Leverage lens (80/20): leveraged work reads gold at a glance;
                // the ★ One Thing gets the strongest treatment on the grid.
                const isOneThing = b.item.id === oneThingId && !isDone;
                const isLeveraged =
                  !isDone && !conflict && (b.item.leveraged || isOneThing);
                const outcome = b.item.projectId
                  ? outcomeById.get(b.item.projectId)
                  : undefined;
                // Planned-vs-actual: for a completed, timed block, how far off
                // the plan the real work-time was (the estimate-accuracy signal).
                const plannedMins = Math.round(
                  (b.end.getTime() - b.start.getTime()) / 60000,
                );
                const actualMins =
                  isDone && b.item.actualStart && b.item.actualEnd
                    ? Math.max(
                        1,
                        Math.round(
                          (new Date(b.item.actualEnd).getTime() -
                            new Date(b.item.actualStart).getTime()) /
                            60000,
                        ),
                      )
                    : null;
                const leftPct = (lane / lanes) * 100;
                const widthPct = 100 / lanes;
                return (
                  <div
                    key={b.item.id}
                    draggable
                    onDragStart={(e) => onBlockDragStart(e, b)}
                    onClick={(e) => {
                      // Click anywhere on the block → open the task card
                      // (controls stopPropagation; drag/resize never emit a
                      // plain click). stopPropagation keeps the day column's
                      // empty-slot tap from also firing.
                      e.stopPropagation();
                      if (resizingRef.current) return; // trailing resize click
                      onOpen(b.item.id);
                    }}
                    onClickCapture={(e) => {
                      // a long-press already opened the menu — swallow the
                      // click that follows so it doesn't also open the task
                      if (lpFired.current) {
                        e.preventDefault();
                        e.stopPropagation();
                        lpFired.current = false;
                      }
                    }}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setCtx({ x: e.clientX, y: e.clientY, item: b.item, day });
                    }}
                    onPointerDown={(e) => startLongPress(e, b.item, day)}
                    onPointerMove={cancelLongPress}
                    onPointerUp={cancelLongPress}
                    onPointerCancel={cancelLongPress}
                    style={{
                      top: Math.max(0, top),
                      height,
                      left: `calc(${leftPct}% + 2px)`,
                      width: `calc(${widthPct}% - 4px)`,
                    }}
                    title={
                      conflict ? "Overlaps another block — double-booked" : undefined
                    }
                    className={[
                      "group absolute cursor-grab overflow-hidden rounded-md border px-1.5 py-0.5 text-left active:cursor-grabbing",
                      isDone
                        ? "border-success/40 bg-success/10"
                        : conflict
                          ? "border-destructive/60 bg-destructive/10"
                          : isOneThing
                            ? "border-amber-400/80 bg-amber-500/15 shadow-[0_0_12px_rgba(245,158,11,0.18)]"
                            : isLeveraged
                              ? "border-amber-500/50 bg-amber-500/10"
                              : isNow
                                ? "border-primary bg-primary/20"
                                : "border-primary/40 bg-primary/10",
                      isNow
                        ? "z-10 ring-2 ring-primary ring-offset-1 ring-offset-background shadow-md"
                        : "",
                      // A pinned block gets a solid left accent so "this won't
                      // move" is glanceable, not only on the lock icon.
                      isFixed && !isDone ? "border-l-2 border-l-primary" : "",
                      // Leveraged work carries a gold left edge even when other
                      // states (now/fixed) win the fill.
                      isLeveraged && !isFixed
                        ? "border-l-2 border-l-amber-400"
                        : "",
                    ].join(" ")}
                  >
                    <div className="flex items-start gap-1">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onComplete(b.item);
                        }}
                        aria-label={isDone ? "Mark not done" : "Mark done"}
                        title={isDone ? "Mark as not done" : "Mark done"}
                        className={[
                          "mt-[1px] flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border",
                          isDone
                            ? "border-success bg-success text-white"
                            : "border-muted-foreground/50 text-transparent hover:border-success hover:text-success/70",
                        ].join(" ")}
                      >
                        <Icon name="Check" className="h-2.5 w-2.5" strokeWidth={3} />
                      </button>
                      <button
                        type="button"
                        onClick={() => onOpen(b.item.id)}
                        className={[
                          "min-w-0 flex-1 truncate text-left text-[11px] font-medium",
                          isDone
                            ? "text-muted-foreground line-through"
                            : "text-foreground",
                        ].join(" ")}
                      >
                        {isOneThing && (
                          <span className="text-amber-400">★ </span>
                        )}
                        {b.item.title}
                      </button>
                    </div>
                    {/* outcome ribbon — why this block matters (tall blocks) */}
                    {outcome && mins >= 45 && (
                      <div className="truncate pr-3 text-[9px] font-medium text-amber-500/90">
                        → {outcome}
                      </div>
                    )}
                    <div className="flex items-center gap-1 text-[9px] text-muted-foreground">
                      {fmtClock(b.start)}
                      {b.item.energy && (
                        <span className="capitalize">· {b.item.energy}</span>
                      )}
                      {isNow && (
                        <span className="ml-auto inline-flex items-center gap-0.5 pr-3 font-semibold text-primary">
                          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
                          now
                        </span>
                      )}
                      {actualMins != null && (
                        <span
                          title={`Planned ${plannedMins}m · actually took ${actualMins}m`}
                          className={[
                            "ml-auto inline-flex shrink-0 items-center pr-3 font-medium tabular-nums",
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
                    </div>
                    {/* ▶ enter the Focus room. Always visible on the current
                        block (the one you should be in), hover elsewhere. */}
                    {!isDone && (
                      <button
                        type="button"
                        aria-label="Focus on this block"
                        title="Focus — full-screen room + timer"
                        onClick={(e) => {
                          e.stopPropagation();
                          onFocusMode(b.item);
                        }}
                        className={[
                          "tech-transition absolute right-[54px] top-0.5 rounded p-0.5",
                          isNow
                            ? "text-primary hover:bg-black/10"
                            : "text-muted-foreground opacity-0 hover:bg-black/10 hover:text-primary group-hover:opacity-100",
                        ].join(" ")}
                      >
                        <Icon name="Play" className="h-3 w-3" fill="currentColor" />
                      </button>
                    )}
                    {/* ★ commit / uncommit the One Thing (today only). */}
                    {!isDone && sameDay(day, now) && (
                      <button
                        type="button"
                        aria-label={
                          isOneThing ? "Unset the One Thing" : "Make this the One Thing"
                        }
                        title={
                          isOneThing
                            ? "Your One Thing today. Click to unset."
                            : "If only one thing gets done today… make it this."
                        }
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleOneThing(b.item.id);
                        }}
                        className={[
                          "tech-transition absolute right-[36px] top-0.5 rounded p-0.5",
                          isOneThing
                            ? "text-amber-400 hover:bg-black/10"
                            : "text-muted-foreground opacity-0 hover:bg-black/10 hover:text-amber-400 group-hover:opacity-100",
                        ].join(" ")}
                      >
                        <Icon name="Star"
                          className="h-3 w-3"
                          fill={isOneThing ? "currentColor" : "none"}
                        />
                      </button>
                    )}
                    {/* Fixed ↔ flexible. A fixed (meeting) block is skipped by
                        roll-over/replan; the lock is persistent when fixed and
                        hover-to-pin when flexible. */}
                    <button
                      type="button"
                      aria-label={isFixed ? "Make flexible" : "Pin as fixed"}
                      title={
                        isFixed
                          ? "Fixed — the planner won't move this. Click to make flexible."
                          : "Flexible — auto-moves when you replan. Click to pin as fixed."
                      }
                      onClick={(e) => {
                        e.stopPropagation();
                        onSetFlexible(b.item, isFixed);
                      }}
                      className={[
                        "tech-transition absolute right-[18px] top-0.5 rounded p-0.5",
                        isFixed
                          ? "text-primary hover:bg-black/10"
                          : "text-muted-foreground opacity-0 hover:bg-black/10 hover:text-foreground group-hover:opacity-100",
                      ].join(" ")}
                    >
                      {isFixed ? (
                        <Icon name="Lock" className="h-3 w-3" />
                      ) : (
                        <Icon name="Unlock" className="h-3 w-3" />
                      )}
                    </button>
                    <button
                      type="button"
                      aria-label="Unschedule"
                      title="Remove from calendar"
                      onClick={(e) => {
                        e.stopPropagation();
                        onUnschedule(b.item);
                      }}
                      className="tech-transition absolute right-0.5 top-0.5 rounded p-0.5 text-muted-foreground opacity-0 hover:bg-black/10 hover:text-destructive group-hover:opacity-100"
                    >
                      <Icon name="X" className="h-3 w-3" />
                    </button>
                    {/* drag the bottom edge to change duration */}
                    <div
                      onPointerDown={(e) => startResize(e, b)}
                      title="Drag to change duration"
                      className="absolute inset-x-0 bottom-0 flex h-2 cursor-ns-resize items-end justify-center pb-0.5 opacity-0 group-hover:opacity-100"
                    >
                      <div className="h-0.5 w-6 rounded-full bg-primary/60" />
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {/* block context menu (right-click / long-press) */}
      {ctx &&
        (() => {
          const it = ctx.item;
          const isDone = it.disposition === "DONE";
          const isFixed = it.flexible === false;
          const isOT = it.id === oneThingId;
          const today = sameDay(ctx.day, now);
          const menu: CtxItem[] = [
            {
              kind: "item",
              label: "Open task",
              icon: themedIcon("ExternalLink"),
              onSelect: () => onOpen(it.id),
            },
            ...(!isDone
              ? [
                  {
                    kind: "item",
                    label: "Focus on this",
                    icon: themedIcon("Play"),
                    onSelect: () => onFocusMode(it),
                  } as CtxItem,
                ]
              : []),
            {
              kind: "item",
              label: isDone ? "Mark not done" : "Mark done",
              icon: themedIcon("Check"),
              onSelect: () => onComplete(it),
            },
            { kind: "sep" },
            ...(!isDone && today
              ? [
                  {
                    kind: "item",
                    label: isOT ? "Unset One Thing" : "Make it the One Thing",
                    icon: themedIcon("Star"),
                    checked: isOT,
                    onSelect: () => onToggleOneThing(it.id),
                  } as CtxItem,
                ]
              : []),
            ...(!isDone
              ? [
                  {
                    kind: "item",
                    label: isFixed
                      ? "Make flexible (auto-moves)"
                      : "Pin as fixed (won't move)",
                    icon: isFixed ? themedIcon("Unlock") : themedIcon("Lock"),
                    checked: isFixed,
                    onSelect: () => onSetFlexible(it, isFixed),
                  } as CtxItem,
                ]
              : []),
            { kind: "sep" },
            ...(!isDone
              ? [
                  {
                    kind: "item",
                    label: "Move to next free slot",
                    icon: themedIcon("CalendarPlus"),
                    onSelect: () => onMoveToFree(it),
                  } as CtxItem,
                ]
              : []),
            {
              kind: "item",
              label: "Reschedule…",
              icon: themedIcon("CalendarClock"),
              onSelect: () => onReschedulePopup(it.id),
            },
            {
              kind: "item",
              label: "Remove from calendar",
              icon: themedIcon("X"),
              onSelect: () => onUnschedule(it),
            },
            {
              kind: "item",
              label: "Delete task…",
              icon: themedIcon("Trash2"),
              danger: true,
              onSelect: () => onDelete(it.id),
            },
          ];
          return (
            <ContextMenu
              x={ctx.x}
              y={ctx.y}
              items={menu}
              onClose={() => setCtx(null)}
            />
          );
        })()}
    </div>
  );
}
