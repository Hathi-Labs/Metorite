"use client";

import Icon from "@/components/Icon";
import { addDays, sameDay } from "@/app/tasks/lib/scheduling";
import { DOW, startOfMonthGrid } from "@/app/calendar/components/shared";
import type { GridEvent, GridTone } from "./types";

// The date helpers are the ones the Calendar already uses. A second copy
// would drift.

const TONE: Record<Exclude<GridTone, "custom">, string> = {
  primary: "bg-primary/10 text-primary",
  destructive: "bg-destructive/10 text-destructive",
  muted: "text-muted-foreground",
};

// ── Month grid ───────────────────────────────────────────────────────────────
// Six weeks of day cells. The caller says which events fall on a day.
export function MonthGrid({
  anchor,
  eventsForDay,
  onPickDay,
  onOpenEvent,
}: {
  anchor: Date;
  /** The events to draw in one cell, in the order to draw them. */
  eventsForDay: (day: Date) => GridEvent[];
  onPickDay: (d: Date) => void;
  onOpenEvent: (e: GridEvent) => void;
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
          const chips = eventsForDay(day);
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
                      onOpenEvent(c);
                    }}
                    className={[
                      "truncate rounded px-1 text-[9px]",
                      c.tone === "custom" ? (c.look ?? "") : TONE[c.tone],
                    ].join(" ")}
                  >
                    {c.glyph === "flag" ? "⚑ " : ""}
                    {c.glyph === "bell" && (
                      <Icon name="Bell" className="mr-0.5 inline h-2.5 w-2.5 align-[-1px]" />
                    )}
                    {c.time ? `${c.time} ` : ""}
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
