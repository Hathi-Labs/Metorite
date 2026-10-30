"use client";

import Icon, { themedIcon } from "@/components/Icon";
import { ParentCrumb } from "@/components/TaskMeta";
import { useRef, useState } from "react";
import {
  type EnergyWindow,
  type DayTemplate,
} from "@/app/tasks/lib/api";
import { MyTask } from "@/app/tasks/lib/types";
import { useRemoval } from "@/app/tasks/lib/useRemoval";
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
  blockLayout,
  DRAG_TYPE,
  elapsedPct,
  snap,
  minutesInto,
  fmtClock,
  fmtLeft,
  fmtRange,
  leftText,
  deadlinesForDay,
  layoutBlocks,
  reservedWindowsForDay,
  type DragPayload,
  type OutcomeById,
} from "./shared";
import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import {
  MOVE_NOTE,
  REMINDER_DRAG_TYPE,
  clockText,
  dropTime,
  nudgeTime,
  pinGlyphTone,
  pinNeedsCompact,
  pinSegments,
  pinTone,
  pinTop,
  remindersOnDay,
  type BlockBox,
  type CalReminder,
  type ZonedTime,
} from "../lib/reminders";


// ── the card tones ───────────────────────────────────────────────────────────
// One rule for each state. A state sets the accent bar and the tint, and it
// never stacks with another. The surface is a token mixed into `--background`,
// so it is opaque: no hour line and no now line shows through a card.
type CardTone = {
  bar: string;
  border: string;
  rest: string;
  now: string;
  past: string;
  /** The elapsed layer of the current block. */
  elapsed: string;
  chip: string;
};
const CARD_TONE: Record<
  "base" | "leveraged" | "one" | "conflict" | "done",
  CardTone
> = {
  base: {
    bar: "bg-primary",
    border: "border-primary/25 hover:border-primary/50",
    rest: "bg-[color-mix(in_srgb,var(--primary)_10%,var(--background))]",
    now: "bg-[color-mix(in_srgb,var(--primary)_14%,var(--background))]",
    past: "bg-[color-mix(in_srgb,var(--primary)_5%,var(--background))]",
    elapsed: "bg-primary/15",
    chip: "bg-primary/15 text-primary",
  },
  leveraged: {
    bar: "bg-warning",
    border: "border-primary/25 hover:border-primary/50",
    rest: "bg-[color-mix(in_srgb,var(--primary)_10%,var(--background))]",
    now: "bg-[color-mix(in_srgb,var(--primary)_14%,var(--background))]",
    past: "bg-[color-mix(in_srgb,var(--primary)_5%,var(--background))]",
    elapsed: "bg-primary/15",
    chip: "bg-primary/15 text-primary",
  },
  one: {
    bar: "bg-warning",
    border: "border-warning/40 hover:border-warning/70",
    rest: "bg-[color-mix(in_srgb,var(--warning)_14%,var(--background))]",
    now: "bg-[color-mix(in_srgb,var(--warning)_18%,var(--background))]",
    past: "bg-[color-mix(in_srgb,var(--warning)_7%,var(--background))]",
    elapsed: "bg-warning/15",
    chip: "bg-warning/20 text-foreground",
  },
  conflict: {
    bar: "bg-destructive",
    border: "border-destructive/35 hover:border-destructive/60",
    rest: "bg-[color-mix(in_srgb,var(--destructive)_10%,var(--background))]",
    now: "bg-[color-mix(in_srgb,var(--destructive)_14%,var(--background))]",
    past: "bg-[color-mix(in_srgb,var(--destructive)_5%,var(--background))]",
    elapsed: "bg-destructive/15",
    chip: "bg-primary/15 text-primary",
  },
  done: {
    bar: "bg-success",
    border: "border-success/25 hover:border-success/50",
    rest: "bg-[color-mix(in_srgb,var(--success)_8%,var(--background))]",
    now: "bg-[color-mix(in_srgb,var(--success)_8%,var(--background))]",
    past: "bg-[color-mix(in_srgb,var(--success)_8%,var(--background))]",
    elapsed: "bg-success/15",
    chip: "bg-success/15 text-success",
  },
};

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
  reminders = [],
  reminderZone = "UTC",
  onOpenReminder,
  onMoveReminder,
  onPickReminderMove,
  onResolveReminder,
}: {
  days: Date[];
  items: MyTask[];
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
  onFocusMode: (item: MyTask) => void;
  onOpen: (id: string) => void;
  onUnschedule: (item: MyTask) => void;
  onComplete: (item: MyTask) => void;
  reschedule: (id: string, start: Date, end: Date, label?: string) => void;
  /** Tap an empty grid slot → schedule a task at that snapped time. */
  onPickSlot: (day: Date, at: Date) => void;
  /** Pin (false) / unpin (true) a block so the auto-mover skips / includes it. */
  onSetFlexible: (item: MyTask, flexible: boolean) => void;
  /** "Reschedule…" → the global Schedule popup (date/time picker + Unschedule). */
  onReschedulePopup: (id: string) => void;
  /** "Move to next free slot" — re-timebox into today's first opening (the
   *  one-gesture fix for an overdue block). */
  onMoveToFree: (item: MyTask) => void;
  /** "Delete task…" → the store's confirm-first delete flow. */
  onDelete: (id: string) => void;
  /** The open reminders (WS-66 RM-5). Each draws as a line with no height. It
   *  takes no lane and no capacity, and it never joins `blocksForDay`. */
  reminders?: CalReminder[];
  /** The zone whose wall clock places a pin. */
  reminderZone?: string;
  /** A tap on a pin opens the chat of the reminder (RM-4). */
  onOpenReminder?: (id: string) => void;
  /** A drop or an Alt+Arrow moves a reminder to a named time. */
  onMoveReminder?: (id: string, to: ZonedTime) => void;
  /** "Move to…" opens the date and time form. */
  onPickReminderMove?: (id: string) => void;
  onResolveReminder?: (id: string, action: "done" | "drop") => void;
}) {
  const hours = Array.from({ length: dayEnd - dayStart }, (_, i) => dayStart + i);
  const gridHeight = hours.length * HOUR_PX;
  // Where the time pill sits in the gutter: day view, today, inside the grid.
  const pillRaw = ((minutesInto(now) - dayStart * 60) / 60) * HOUR_PX;
  const pillTop =
    days.length === 1 && sameDay(days[0], now) && pillRaw >= 0 && pillRaw <= gridHeight
      ? pillRaw
      : null;
  // Live resize (transient end while dragging the handle) + drop-target column.
  const removal = useRemoval();
  const [resizing, setResizing] = useState<{ id: string; endMs: number } | null>(null);
  const resizingRef = useRef(false); // suppress the native block-drag while resizing
  const [dragOverKey, setDragOverKey] = useState<string | null>(null);
  // Block context menu — right-click on desktop, LONG-PRESS on touch (the
  // hover micro-buttons don't exist on a phone, so this menu IS the mobile
  // path to unschedule / pin / star / focus / reschedule / delete).
  const [ctx, setCtx] = useState<{
    x: number;
    y: number;
    item: MyTask;
    day: Date;
  } | null>(null);
  const [rctx, setRctx] = useState<{ x: number; y: number; row: CalReminder } | null>(null);
  const lpTimer = useRef<number | null>(null);
  const lpFired = useRef(false);
  const startLongPress = (e: React.PointerEvent, item: MyTask, day: Date) => {
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
    // A reminder pin has its own drag type. It moves a reminder and never
    // reaches the task `reschedule` below.
    const pin = e.dataTransfer.getData(REMINDER_DRAG_TYPE);
    if (pin) {
      dropReminder(e, day, pin);
      return;
    }
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

  const dropReminder = (e: React.DragEvent, day: Date, raw: string) => {
    let p: { id: string; grabOffsetMins: number };
    try {
      p = JSON.parse(raw) as { id: string; grabOffsetMins: number };
    } catch {
      return;
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const mins =
      dayStart * 60 + snap(((e.clientY - rect.top) / HOUR_PX) * 60 - p.grabOffsetMins);
    const to = dropTime(day, mins, reminderZone);
    if (to) onMoveReminder?.(p.id, to);
  };

  const onPinDragStart = (e: React.DragEvent, row: CalReminder) => {
    const rect = e.currentTarget.getBoundingClientRect();
    // The line sits at the middle of the button.
    const grabOffsetMins = ((e.clientY - (rect.top + rect.height / 2)) / HOUR_PX) * 60;
    e.dataTransfer.setData(REMINDER_DRAG_TYPE, JSON.stringify({ id: row.id, grabOffsetMins }));
    e.dataTransfer.effectAllowed = "move";
  };

  const onPinKeyDown = (e: React.KeyboardEvent, row: CalReminder) => {
    if (!e.altKey || (e.key !== "ArrowUp" && e.key !== "ArrowDown")) return;
    e.preventDefault();
    e.stopPropagation();
    const to = nudgeTime(row, e.key === "ArrowDown" ? 1 : -1, reminderZone);
    if (to) onMoveReminder?.(row.id, to);
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
      {/* Column headers (week). The grid body below is its own stacking
          context (`relative z-0`), so no pin, now line or block in it can
          draw over this header when the grid scrolls under it. */}
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

      {/* `relative z-0`: one stacking context for the whole body. The z-20 of
          a pin stays inside it, under the sticky week header (z-10) above.
          The now line and the cards have no z-index, so they stay there too. */}
      <div className="relative z-0 flex">
        {/* Hour gutter — each row is EXACTLY HOUR_PX so the labels stay in
            lockstep with the day-column hour lines and the now-line (a per-row
            negative margin here used to compound, drifting the labels ~3h up by
            late evening). The label is centred on the hour line at the row top. */}
        <div className="relative w-14 shrink-0">
          {hours.map((h) => (
            <div
              key={h}
              style={{ height: HOUR_PX }}
              className="relative pr-2 text-right text-[10px] text-muted-foreground"
            >
              {/* the pill below hides the label it would sit on */}
              {!(
                pillTop != null && Math.abs(pillTop - (h - dayStart) * HOUR_PX) < 9
              ) && (
                <span className="absolute right-2 top-0 -translate-y-1/2">
                  {h % 12 === 0 ? 12 : h % 12}
                  {h < 12 ? "am" : "pm"}
                </span>
              )}
            </div>
          ))}
          {/* The live time (day view), in the gutter and level with the now
              line, so it never covers a card. */}
          {pillTop != null && (
            <span
              className="pointer-events-none absolute right-1 -translate-y-1/2 rounded bg-primary px-1 text-[11px] font-semibold leading-4 tabular-nums text-primary-foreground shadow-sm"
              style={{ top: pillTop }}
            >
              {fmtClock(now)}
            </span>
          )}
        </div>

        {/* Day columns */}
        {days.map((day) => {
          const blocks = blocksForDay(items, day);
          const deadlines = deadlinesForDay(items, day);
          const today = sameDay(day, now);
          const pins = remindersOnDay(reminders, day, reminderZone);
          // The task blocks of this column as spans of minutes. A pin whose
          // label would sit on one draws compact (`pinNeedsCompact`). The
          // block height is at least 20 px, as drawn below.
          const spans = blocks.map((b) => {
            const start = minutesInto(b.start);
            const mins = (b.end.getTime() - b.start.getTime()) / 60000;
            return { start, end: start + Math.max(mins, (20 / HOUR_PX) * 60) };
          });
          // The blocks in their lanes, drawn below. A pin line stops at the
          // edge of each lane a block holds at its height (`pinSegments`).
          const laid = layoutBlocks(blocks);
          const boxes: BlockBox[] = laid.map(({ block: b, lane, lanes }) => {
            const end = resizing?.id === b.item.id ? new Date(resizing.endMs) : b.end;
            const mins = (end.getTime() - b.start.getTime()) / 60000;
            return {
              top: Math.max(0, ((minutesInto(b.start) - dayStart * 60) / 60) * HOUR_PX),
              height: Math.max(20, (mins / 60) * HOUR_PX - 2),
              lane,
              lanes,
            };
          });
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

              {/* now line. It has no z-index and it comes before the cards, so
                  every card draws over it: the line never strikes through a
                  title. The time pill is in the gutter. The current card shows
                  the elapsed part as a layer that ends at this line. */}
              {today && nowTop >= 0 && nowTop <= gridHeight && (
                <div
                  className="pointer-events-none absolute inset-x-0 border-t-2 border-primary"
                  style={{ top: nowTop }}
                >
                  <span className="absolute -left-1 -top-1 h-2 w-2 rounded-full bg-primary motion-safe:animate-pulse" />
                </div>
              )}

              {/* reminder pins (WS-66 RM-5) — a line with no height at the
                  minute of the reminder. Not a block: no lane, no capacity.
                  The line is pointer-events-none. The button is the handle.
                  The line stops at the edge of a block, so it never strikes
                  through the block's title (`pinSegments`). */}
              {pins.map(({ row, minutes }) => {
                const top = pinTop(minutes, dayStart, HOUR_PX);
                if (top < 0 || top > gridHeight) return null;
                const clock = clockText(row.fireMs, reminderZone);
                const compact = pinNeedsCompact(minutes, spans, HOUR_PX);
                return (
                  <div
                    key={`rm-${row.id}`}
                    className="pointer-events-none absolute inset-x-0 z-20"
                    style={{ top }}
                  >
                    {pinSegments(top, boxes).map((s) => (
                      <div
                        key={s.from}
                        aria-hidden
                        className={`absolute top-0 border-t-2 ${pinTone(row.state)}`}
                        style={{ left: `${s.from * 100}%`, width: `${(s.to - s.from) * 100}%` }}
                      />
                    ))}
                    <button
                      type="button"
                      draggable
                      onDragStart={(e) => onPinDragStart(e, row)}
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenReminder?.(row.id);
                      }}
                      onKeyDown={(e) => onPinKeyDown(e, row)}
                      onContextMenu={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        setRctx({ x: e.clientX, y: e.clientY, row });
                      }}
                      aria-label={`Reminder, ${clock}: ${row.title}`}
                      aria-keyshortcuts="Alt+ArrowUp Alt+ArrowDown"
                      title={`${clock} · ${row.title}. Drag to move it, or press Alt+Arrow. ${MOVE_NOTE}`}
                      className={[
                        compact
                          ? // Over a block: a small bell on the left edge of
                            // the column, half out in the gutter. It has no
                            // text, so it covers no block title.
                            "-left-2 -top-2 h-4 w-4 justify-center rounded-full"
                          : "left-0 -top-2.5 max-w-full gap-0.5 rounded-sm px-1 text-[10px] leading-4",
                        // A finger needs 24 px. On a touch screen an empty
                        // layer grows the target to 24 px, and the look stays.
                        "pointer-coarse:before:absolute pointer-coarse:before:-inset-1",
                        `pointer-events-auto absolute inline-flex cursor-grab items-center bg-background focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing ${pinGlyphTone(row.state)}`,
                      ].join(" ")}
                    >
                      <Icon name="Bell" className="h-3 w-3 shrink-0" />
                      {!compact && <span className="shrink-0 tabular-nums">{clock}</span>}
                      {!compact && days.length === 1 && (
                        <span className="min-w-0 truncate text-foreground">{row.title}</span>
                      )}
                    </button>
                  </div>
                );
              })}

              {/* scheduled blocks — overlaps split into side-by-side lanes.
                  One card anatomy: a 3 px accent bar carries the state colour,
                  the surface is opaque (so no hour line and no now line shows
                  through), and each state sets its own bar and tint without
                  stacking (task 1124). */}
              {laid.map(({ block: b, lane, lanes }) => {
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
                // Past and not done: muted, so the eye goes to now and next.
                const isPast = !isDone && b.end.getTime() <= now.getTime();
                // Fixed (meeting) block — roll-over/replan leave it put.
                const isFixed = b.item.flexible === false;
                // ★ One Thing: the warning bar and tint. Leveraged work
                // (80/20) takes the warning bar and keeps the default tint.
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
                // A week column, or three lanes, is narrow.
                const narrow = days.length > 1 || lanes >= 3;
                const veryNarrow = days.length > 1 && lanes > 1;
                const layout = blockLayout(mins, narrow, Boolean(b.item.parent));
                const leftPct = (lane / lanes) * 100;
                const widthPct = 100 / lanes;
                const cardTop = Math.max(0, top) + 1;
                // The elapsed layer runs from the card's top down to "now", so
                // its lower edge meets the now line at both edges of the card.
                const elapsed = isNow ? elapsedPct(b.start, end, now) : 0;
                const tone = isDone
                  ? CARD_TONE.done
                  : conflict
                    ? CARD_TONE.conflict
                    : isOneThing
                      ? CARD_TONE.one
                      : isLeveraged
                        ? CARD_TONE.leveraged
                        : CARD_TONE.base;
                const surface = isPast ? tone.past : isNow ? tone.now : tone.rest;
                const range = fmtRange(b.start, end, { compact: narrow });
                const planned = fmtLeft(Math.max(1, Math.round(mins)));
                const titleCls = [
                  "min-w-0 flex-1 text-left text-xs font-medium",
                  layout.wrapTitle ? "line-clamp-2" : "truncate",
                  isDone
                    ? "text-muted-foreground line-through"
                    : isPast
                      ? "text-muted-foreground"
                      : "text-foreground",
                ].join(" ");
                const nowChip = isNow ? (
                  <span
                    className={`inline-flex shrink-0 items-center rounded px-1 text-[11px] font-medium leading-4 ${tone.chip}`}
                  >
                    Now · {leftText(end, now)}
                  </span>
                ) : null;
                const overlapChip = conflict && !veryNarrow ? (
                  <span className="inline-flex shrink-0 items-center rounded bg-destructive/15 px-1 text-[11px] font-medium leading-4 text-destructive">
                    Overlaps
                  </span>
                ) : null;
                const actualChip =
                  actualMins != null ? (
                    <span
                      title={`Planned ${plannedMins}m · actually took ${actualMins}m`}
                      className={[
                        "shrink-0 text-[11px] font-medium leading-4 tabular-nums",
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
                  ) : null;
                const lockMark = isFixed ? (
                  <span title="Fixed — the planner does not move this block" className="inline-flex shrink-0">
                    <Icon name="Lock" className="h-3 w-3" aria-label="Fixed" role="img" />
                  </span>
                ) : null;
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
                      top: cardTop,
                      height,
                      left: `calc(${leftPct}% + 2px)`,
                      width: `calc(${widthPct}% - 4px)`,
                    }}
                    title={
                      conflict ? "Overlaps another block — double-booked" : undefined
                    }
                    className={[
                      "group absolute cursor-grab overflow-hidden rounded-md border text-left active:cursor-grabbing",
                      "transition-[box-shadow,border-color] duration-[var(--motion-duration)] hover:shadow-md",
                      "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
                      surface,
                      tone.border,
                      isNow ? "shadow-sm" : "",
                    ].join(" ")}
                  >
                    {/* the one place a state shows its colour */}
                    <span
                      aria-hidden
                      className={`absolute inset-y-0 left-0 w-[3px] ${tone.bar} ${isPast ? "opacity-50" : ""}`}
                    />
                    {/* elapsed part of the current block */}
                    {elapsed > 0 && (
                      <span
                        aria-hidden
                        className={`pointer-events-none absolute inset-x-0 top-0 ${tone.elapsed}`}
                        style={{ height: `${elapsed}%` }}
                      />
                    )}
                    <div
                      className={[
                        "relative flex h-full min-w-0 flex-col pl-2 pr-1",
                        layout.rows === "one" ? "justify-center" : "pt-0.5",
                      ].join(" ")}
                    >
                      <div
                        className={[
                          "flex min-w-0 gap-1.5",
                          layout.wrapTitle ? "items-start" : "items-center",
                          // keep the labelled Focus button clear of the title
                          isNow ? "pr-[62px]" : "",
                        ].join(" ")}
                      >
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onComplete(b.item);
                          }}
                          aria-label={isDone ? "Mark not done" : "Mark done"}
                          title={isDone ? "Mark as not done" : "Mark done"}
                          className={[
                            "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                            layout.wrapTitle ? "mt-px" : "",
                            isDone
                              ? "border-success bg-success text-success-foreground"
                              : "border-muted-foreground/60 text-transparent hover:border-success hover:text-success/70",
                          ].join(" ")}
                        >
                          <Icon name="Check" className="h-3 w-3" strokeWidth={3} />
                        </button>
                        {/* the title is a real button, so Tab reaches it. A click
                            bubbles to the card, which opens the task. */}
                        <button type="button" className={titleCls}>
                          {isOneThing && <span className="text-warning">★ </span>}
                          {b.item.title}
                        </button>
                        {layout.rows === "one" && (
                          <>
                            {layout.chips && nowChip}
                            {layout.chips && overlapChip}
                            {layout.chips && actualChip}
                            {lockMark}
                            {layout.rangeOnTitle && !isNow && (
                              <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                                {range}
                              </span>
                            )}
                          </>
                        )}
                      </div>
                      {/* D-PM-38 S4 — a scheduled step names its parent with
                          the shared crumb. Under 90 minutes it shares the
                          meta row, from 90 it has its own line
                          (`blockLayout`). */}
                      {layout.crumb === "own" && (
                        <ParentCrumb
                          parent={b.item.parent}
                          className="pl-[22px] text-[11px]! leading-[14px]!"
                        />
                      )}
                      {/* outcome ribbon — why this block matters (tall blocks) */}
                      {outcome && layout.outcome && (
                        <div className="truncate pl-[22px] text-[11px] font-medium leading-[14px] text-warning">
                          → {outcome}
                        </div>
                      )}
                      {layout.rows !== "one" && (
                        <div className="flex min-w-0 items-center gap-1.5 pl-[22px] text-[11px] leading-[14px] text-muted-foreground">
                          {nowChip}
                          <span className={narrow ? "min-w-0 truncate tabular-nums" : "shrink-0 tabular-nums"}>
                            {range}
                            {!narrow && ` · ${planned}`}
                          </span>
                          {layout.crumb === "inline" && (
                            <ParentCrumb
                              parent={b.item.parent}
                              className="min-w-0 text-[11px]!"
                            />
                          )}
                          {!narrow && b.item.energy && (
                            <span className="capitalize">· {b.item.energy}</span>
                          )}
                          {lockMark}
                          {overlapChip}
                          {actualChip}
                        </div>
                      )}
                    </div>
                    {/* The toolbar: on hover and on keyboard focus, on a solid
                        background so it never overlaps text. On the current
                        block, Focus is a labelled button that is always there.
                        Pin, unschedule and the rest are in the menu. */}
                    <div className="absolute right-1 top-0.5 z-[1] flex items-center gap-1">
                      <div className="reveal-on-hover flex items-center rounded border border-border bg-background shadow-sm">
                        {!isDone && !isNow && (
                          <button
                            type="button"
                            aria-label="Focus on this block"
                            title="Focus — full-screen room + timer"
                            onClick={(e) => {
                              e.stopPropagation();
                              onFocusMode(b.item);
                            }}
                            className="tech-transition rounded p-0.5 text-muted-foreground hover:text-primary focus-visible:ring-2 focus-visible:ring-ring"
                          >
                            <Icon name="Play" className="h-3.5 w-3.5" fill="currentColor" />
                          </button>
                        )}
                        {/* ★ commit / uncommit the One Thing (today only). */}
                        {!isDone && sameDay(day, now) && !veryNarrow && (
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
                              "tech-transition rounded p-0.5 focus-visible:ring-2 focus-visible:ring-ring",
                              isOneThing
                                ? "text-warning"
                                : "text-muted-foreground hover:text-warning",
                            ].join(" ")}
                          >
                            <Icon
                              name="Star"
                              className="h-3.5 w-3.5"
                              fill={isOneThing ? "currentColor" : "none"}
                            />
                          </button>
                        )}
                        <button
                          type="button"
                          aria-label="More actions"
                          title="More actions — pin, reschedule, remove"
                          onClick={(e) => {
                            e.stopPropagation();
                            setCtx({ x: e.clientX, y: e.clientY, item: b.item, day });
                          }}
                          className="tech-transition rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <Icon name="MoreHorizontal" className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      {isNow && (
                        <button
                          type="button"
                          aria-label="Focus on this block"
                          title="Focus — full-screen room + timer"
                          onClick={(e) => {
                            e.stopPropagation();
                            onFocusMode(b.item);
                          }}
                          className="tech-transition inline-flex items-center gap-1 rounded bg-primary px-1.5 text-[11px] font-medium leading-4 text-primary-foreground hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <Icon name="Play" className="h-3 w-3" fill="currentColor" />
                          Focus
                        </button>
                      )}
                    </div>
                    {/* drag the bottom edge to change duration */}
                    <div
                      onPointerDown={(e) => startResize(e, b)}
                      title="Drag to change duration"
                      className="absolute inset-x-0 bottom-0 flex h-2 cursor-ns-resize items-end justify-center pb-0.5 reveal-on-hover"
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
              // S6g, P0 — a board task leaves my lists, and says so.
              label: removal.canPurge(it) ? "Delete task…" : "Remove from my lists…",
              icon: themedIcon(removal.canPurge(it) ? "Trash2" : "UserX"),
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

      {rctx && (
        <ContextMenu
          x={rctx.x}
          y={rctx.y}
          onClose={() => setRctx(null)}
          items={[
            {
              kind: "item",
              label: "Open in chat",
              icon: themedIcon("ExternalLink"),
              onSelect: () => onOpenReminder?.(rctx.row.id),
            },
            {
              kind: "item",
              label: "Move to…",
              icon: themedIcon("CalendarClock"),
              onSelect: () => onPickReminderMove?.(rctx.row.id),
            },
            {
              kind: "item",
              label: "Done",
              icon: themedIcon("Check"),
              onSelect: () => onResolveReminder?.(rctx.row.id, "done"),
            },
            {
              kind: "item",
              label: "Drop",
              icon: themedIcon("X"),
              danger: true,
              onSelect: () => onResolveReminder?.(rctx.row.id, "drop"),
            },
          ]}
        />
      )}
    </div>
  );
}
