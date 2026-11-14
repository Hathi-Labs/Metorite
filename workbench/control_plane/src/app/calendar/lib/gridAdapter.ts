/**
 * The Calendar's door to the shared grids in `components/calendar/`.
 * The grids draw `GridEvent`. This file turns tasks, deadlines, reminders and
 * external events into that shape, and sends a tap back to the right handler.
 * It holds no markup.
 */
import type { MyTask } from "@/app/tasks/lib/types";
import { blocksForDay } from "@/app/tasks/lib/scheduling";
import type { GridEvent } from "@/components/calendar/types";
import { deadlinesForDay } from "../components/shared";
import { externalLook } from "../components/externalLook";
import { monthChips, type CalReminder } from "./reminders";
import { externalChips, type ExternalView } from "./externalEntries";

/** What a month chip stands for. The grid never reads it. */
export type MonthChipKind = "block" | "deadline" | "reminder" | "external";

const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const endOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);

/**
 * The chips of one Month cell, in the order the Calendar has always drawn
 * them: external events, task blocks, deadlines, then reminders.
 * A chip with no clock span of its own takes the whole day.
 */
export function monthEventsForDay(
  day: Date,
  items: MyTask[],
  reminders: CalReminder[],
  reminderZone: string,
  externals: ExternalView[],
): GridEvent[] {
  const blocks = blocksForDay(items, day);
  const deadlines = deadlinesForDay(items, day);
  const kind = (k: MonthChipKind) => ({ kind: k });
  return [
    ...externalChips(externals, day, reminderZone).map(
      (x): GridEvent => ({
        id: x.id,
        title: x.title,
        start: startOf(day),
        end: endOf(day),
        allDay: false,
        tone: "custom",
        time: x.time,
        look: externalLook(x),
        data: kind("external"),
      }),
    ),
    ...blocks.map(
      (b): GridEvent => ({
        id: b.item.id,
        title: b.item.title,
        start: b.start,
        end: b.end,
        allDay: false,
        tone: "primary",
        data: kind("block"),
      }),
    ),
    ...deadlines.map(
      (d): GridEvent => ({
        id: d.id,
        title: d.title,
        start: startOf(day),
        end: endOf(day),
        allDay: false,
        tone: "destructive",
        glyph: "flag",
        data: kind("deadline"),
      }),
    ),
    ...monthChips(reminders, day, reminderZone).map(
      (r): GridEvent => ({
        id: r.id,
        title: r.label,
        start: startOf(day),
        end: endOf(day),
        allDay: false,
        tone: "muted",
        glyph: "bell",
        time: r.time,
        data: kind("reminder"),
      }),
    ),
  ];
}

/** A tap on a Month chip. A reminder and an external event have their own handler. */
export function openMonthEvent(
  e: GridEvent,
  on: {
    onOpen: (id: string) => void;
    onOpenReminder?: (id: string) => void;
    onOpenExternal?: (id: string) => void;
  },
): void {
  const k = (e.data as { kind?: MonthChipKind } | undefined)?.kind;
  if (k === "reminder") on.onOpenReminder?.(e.id);
  else if (k === "external") on.onOpenExternal?.(e.id);
  else on.onOpen(e.id);
}
