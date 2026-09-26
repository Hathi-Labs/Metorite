"use client";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import Icon from "@/components/Icon";
import { useMemo, useState } from "react";
import { useTaskStore } from "../lib/taskStore";
import { durationLabel } from "../lib/utils";
import {
  DEFAULT_BLOCK_MINS,
  startOfDay,
  addDays,
  sameDay,
  blocksForDay,
  firstFreeSlot,
} from "../lib/scheduling";

// The "Schedule on calendar" popup — opened from the Schedule pill, the card's
// Schedule button, or a context menu (all via store.openSchedule(id)). It picks
// an APPROPRIATE time using the same free-slot logic as the calendar grid
// (lib/scheduling), offers a couple of one-tap presets + an exact-time picker,
// and writes the block via updateItem({scheduledStart,scheduledEnd}). Global,
// mounted once in page.tsx — like DeleteConfirmModal.

/** `<input type="datetime-local">` wants a local `YYYY-MM-DDTHH:mm` string. */
function toLocalInput(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function SchedulePopup() {
  const scheduleItemId = useTaskStore((s) => s.scheduleItemId);
  const items = useTaskStore((s) => s.items);
  const updateItem = useTaskStore((s) => s.updateItem);
  const closeSchedule = useTaskStore((s) => s.closeSchedule);
  const dayStart = useTaskStore((s) => s.settings.dayStartHour ?? 7);
  const dayEnd = useTaskStore((s) => s.settings.dayEndHour ?? 22);

  const item = scheduleItemId
    ? items.find((i) => i.id === scheduleItemId)
    : undefined;

  // Captured once when the popup opens — a fixed "now" (no live ticking needed
  // for a transient dialog, and it keeps render pure of Date.now()).
  const [now] = useState(() => new Date());
  const [custom, setCustom] = useState("");

  const dur = item?.timeEstimateMins ?? DEFAULT_BLOCK_MINS;

  const presets = useMemo(() => {
    if (!item) return [];
    const today = startOfDay(now);
    const tomorrow = addDays(now, 1);
    const todaySlot = firstFreeSlot(
      blocksForDay(items, today),
      today,
      dur,
      dayStart,
      dayEnd,
    );
    const tomorrowSlot = firstFreeSlot(
      blocksForDay(items, tomorrow),
      tomorrow,
      dur,
      dayStart,
      dayEnd,
    );
    const out: { key: string; label: string; at: Date }[] = [];
    // Only offer "today" if its free slot is actually still ahead of now.
    if (todaySlot.getTime() + dur * 60000 > now.getTime()) {
      out.push({ key: "today", label: "Today — next free slot", at: todaySlot });
    }
    out.push({
      key: "tomorrow",
      label: "Tomorrow — first free slot",
      at: tomorrowSlot,
    });
    return out;
  }, [item, items, now, dur, dayStart, dayEnd]);

  if (!item) return null;

  const commit = (start: Date) => {
    const end = new Date(start.getTime() + dur * 60000);
    updateItem(item.id, {
      scheduledStart: start.toISOString(),
      scheduledEnd: end.toISOString(),
    });
    closeSchedule();
  };

  const fmt = (d: Date) =>
    d.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });

  const scheduled = item.scheduledStart ? new Date(item.scheduledStart) : null;

  // The shared `Modal` (continuity P3): one scrim, a focus trap, Escape and
  // focus return. The `alert` layer, because the Schedule nudge also opens
  // this from inside the focused task (`TaskFocusModal`, z-[80]), and the
  // dialog layer (z-50) would paint under it. See `Modal`'s `LAYERS`.
  return (
    <Modal
      open
      onClose={closeSchedule}
      title="Schedule on calendar"
      description={<span className="block truncate">{item.title}</span>}
      icon="CalendarClock"
      size="sm"
      layer="alert"
    >
        <div className="flex flex-col gap-2 px-4 py-3">
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <Icon name="Clock" className="h-3 w-3" />
            Blocks {durationLabel(dur)}
            {scheduled && (
              <span className="text-primary">
                {" "}
                · now {sameDay(scheduled, now) ? "today " : ""}
                {scheduled.toLocaleTimeString(undefined, {
                  hour: "numeric",
                  minute: "2-digit",
                })}
              </span>
            )}
          </p>

          {presets.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => commit(p.at)}
              className="tech-transition flex items-center justify-between gap-2 rounded-lg border border-border bg-background/60 px-3 py-2.5 text-left hover:border-primary/50 active:bg-primary/5"
            >
              <span className="text-sm font-medium text-foreground">
                {p.label}
              </span>
              <span className="shrink-0 text-[11px] tabular-nums text-primary">
                {fmt(p.at)}
              </span>
            </button>
          ))}

          {/* Exact time */}
          <div className="mt-1 rounded-lg border border-border bg-background/60 p-2.5">
            <label className="mb-1.5 block text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Pick an exact time
            </label>
            <div className="flex items-center gap-2">
              <input
                type="datetime-local"
                value={custom || toLocalInput(presets[0]?.at ?? now)}
                onChange={(e) => setCustom(e.target.value)}
                className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground focus:border-primary/50 focus:outline-none"
              />
              <Button size="sm" radius="keep" layout="inline-flex items-center" type="button" onClick={() => {
                  const v = custom || toLocalInput(presets[0]?.at ?? now);
                  const d = new Date(v);
                  if (!Number.isNaN(d.getTime())) commit(d);
                }} className="shrink-0 rounded-md">
                <Icon name="Check" className="h-3.5 w-3.5" />
                Set
              </Button>
            </div>
          </div>

          {scheduled && (
            <button
              type="button"
              onClick={() => {
                updateItem(item.id, { scheduledStart: "", scheduledEnd: "" });
                closeSchedule();
              }}
              className="tech-transition mt-0.5 inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-secondary hover:text-destructive"
            >
              <Icon name="CalendarX" className="h-3.5 w-3.5" />
              Unschedule
            </button>
          )}
        </div>
    </Modal>
  );
}
