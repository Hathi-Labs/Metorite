"use client";

import { MyTask } from "@/app/tasks/lib/types";
import {
  addDays,
  sameDay,
  blocksForDay,
} from "@/app/tasks/lib/scheduling";
import {
  DOW,
  startOfMonthGrid,
  deadlinesForDay,
} from "./shared";
import Icon from "@/components/Icon";
import { monthChips, type CalReminder } from "../lib/reminders";


// ── Month grid ───────────────────────────────────────────────────────────────
export function MonthGrid({
  anchor,
  items,
  onPickDay,
  onOpen,
  reminders = [],
  reminderZone = "UTC",
  onOpenReminder,
}: {
  anchor: Date;
  items: MyTask[];
  onPickDay: (d: Date) => void;
  onOpen: (id: string) => void;
  /** The open reminders (WS-66 RM-5): a bell, a time and a title. */
  reminders?: CalReminder[];
  reminderZone?: string;
  onOpenReminder?: (id: string) => void;
}) {
  const gridStart = startOfMonthGrid(anchor);
  const cells = Array.from({ length: 42 }, (_, i) => addDays(gridStart, i));
  const now = new Date();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="grid grid-cols-7 border-b border-border">
        {DOW.map((d) => (
          <div
            key={d}
            className="px-2 py-1 text-center text-[10px] font-medium uppercase text-muted-foreground"
          >
            {d}
          </div>
        ))}
      </div>
      <div className="grid flex-1 grid-cols-7 grid-rows-6">
        {cells.map((day) => {
          const inMonth = day.getMonth() === anchor.getMonth();
          const blocks = blocksForDay(items, day);
          const deadlines = deadlinesForDay(items, day);
          const chips = [
            ...blocks.map((b) => ({ id: b.item.id, title: b.item.title, kind: "block" as const, time: "" })),
            ...deadlines.map((d) => ({ id: d.id, title: d.title, kind: "deadline" as const, time: "" })),
            ...monthChips(reminders, day, reminderZone).map((r) => ({
              id: r.id,
              title: r.label,
              kind: "reminder" as const,
              time: r.time,
            })),
          ];
          const today = sameDay(day, now);
          return (
            <button
              key={day.toISOString()}
              type="button"
              onClick={() => onPickDay(day)}
              className={[
                "flex min-h-0 flex-col gap-0.5 border-b border-l border-border p-1 text-left align-top",
                inMonth ? "" : "bg-secondary/30",
              ].join(" ")}
            >
              <span
                className={[
                  "text-[11px] font-medium",
                  today
                    ? "flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground"
                    : inMonth
                      ? "text-foreground"
                      : "text-muted-foreground/50",
                ].join(" ")}
              >
                {day.getDate()}
              </span>
              <div className="flex flex-col gap-0.5 overflow-hidden">
                {chips.slice(0, 3).map((c) => (
                  <span
                    key={c.id}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (c.kind === "reminder") onOpenReminder?.(c.id);
                      else onOpen(c.id);
                    }}
                    className={[
                      "truncate rounded px-1 text-[9px]",
                      c.kind === "deadline"
                        ? "bg-destructive/10 text-destructive"
                        : c.kind === "reminder"
                          ? "text-muted-foreground"
                          : "bg-primary/10 text-primary",
                    ].join(" ")}
                  >
                    {c.kind === "deadline" ? "⚑ " : ""}
                    {c.kind === "reminder" && (
                      <Icon name="Bell" className="mr-0.5 inline h-2.5 w-2.5 align-[-1px]" />
                    )}
                    {c.kind === "reminder" ? `${c.time} ` : ""}
                    {c.title}
                  </span>
                ))}
                {chips.length > 3 && (
                  <span className="px-1 text-[9px] text-muted-foreground">
                    +{chips.length - 3} more
                  </span>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
