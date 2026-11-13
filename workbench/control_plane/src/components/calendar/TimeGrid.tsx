"use client";

import type { ReactNode } from "react";
import { sameDay } from "@/app/tasks/lib/scheduling";
import { DOW, HOUR_PX, fmtClockShort, minutesInto } from "@/app/calendar/components/shared";
import { layoutSpans } from "./layout";
import type { GridEvent, GridTone } from "./types";

const BAR: Record<Exclude<GridTone, "custom">, string> = {
  primary: "bg-primary",
  destructive: "bg-destructive",
  muted: "bg-muted-foreground",
};

export type EventBox = { top: number; height: number; lane: number; lanes: number };

/** The events of one day that have a clock time. */
export function timedEventsForDay(events: GridEvent[], day: Date): GridEvent[] {
  return events.filter((e) => !e.allDay && sameDay(e.start, day));
}

/** The top and height of an event in a day column, in px. */
export function eventBox(e: GridEvent, dayStart: number): { top: number; height: number } {
  const mins = (e.end.getTime() - e.start.getTime()) / 60000;
  return {
    top: Math.max(0, ((minutesInto(e.start) - dayStart * 60) / 60) * HOUR_PX),
    height: Math.max(20, (mins / 60) * HOUR_PX - 2),
  };
}

// ── Day / Week hour grid ─────────────────────────────────────────────────────
// The frame: day headers, the hour gutter, hour lines, the now line and the
// events in side-by-side lanes. The caller draws an event with `renderEvent`,
// or takes the plain card.
export function TimeGrid({
  days,
  events,
  now,
  dayStart = 0,
  dayEnd = 24,
  onPickSlot,
  onOpenEvent,
  renderEvent,
}: {
  days: Date[];
  events: GridEvent[];
  now: Date;
  dayStart?: number;
  dayEnd?: number;
  onPickSlot?: (day: Date, at: Date) => void;
  onOpenEvent?: (e: GridEvent) => void;
  renderEvent?: (e: GridEvent, box: EventBox) => ReactNode;
}) {
  const hours = Array.from({ length: dayEnd - dayStart }, (_, i) => dayStart + i);
  const gridHeight = hours.length * HOUR_PX;
  return (
    <div className="min-w-fit">
      {days.length > 1 && (
        <div className="sticky top-0 z-10 bg-background">
          <div className="flex border-b border-border">
            <div className="w-14 shrink-0" />
            {days.map((d) => (
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
                    sameDay(d, now) ? "text-primary" : "text-foreground",
                  ].join(" ")}
                >
                  {d.getDate()}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="relative z-0 flex">
        <div className="relative w-14 shrink-0">
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
        {days.map((day) => {
          const laid = layoutSpans(timedEventsForDay(events, day));
          const nowTop = ((minutesInto(now) - dayStart * 60) / 60) * HOUR_PX;
          return (
            <div
              key={day.toISOString()}
              onClick={(ev) => {
                if (!onPickSlot) return;
                const rect = ev.currentTarget.getBoundingClientRect();
                const mins = Math.max(0, ((ev.clientY - rect.top) / HOUR_PX) * 60);
                const at = new Date(day);
                at.setHours(dayStart, 0, 0, 0);
                at.setMinutes(at.getMinutes() + Math.floor(mins / 15) * 15);
                onPickSlot(day, at);
              }}
              className="relative flex-1 cursor-pointer border-l border-border"
              style={{ height: gridHeight, minWidth: days.length > 1 ? 96 : undefined }}
            >
              {hours.map((h) => (
                <div key={h} style={{ height: HOUR_PX }} className="border-b border-border/50" />
              ))}
              {sameDay(day, now) && nowTop >= 0 && nowTop <= gridHeight && (
                <div
                  className="pointer-events-none absolute left-0 right-0 h-px bg-primary"
                  style={{ top: nowTop }}
                />
              )}
              {laid.map(({ block: e, lane, lanes }) => {
                const box = { ...eventBox(e, dayStart), lane, lanes };
                if (renderEvent) return <div key={e.id}>{renderEvent(e, box)}</div>;
                return (
                  <button
                    key={e.id}
                    type="button"
                    onClick={(ev) => {
                      ev.stopPropagation();
                      onOpenEvent?.(e);
                    }}
                    className="absolute flex overflow-hidden rounded-md border border-border bg-card text-left text-[11px] shadow-sm"
                    style={{
                      top: box.top,
                      height: box.height,
                      left: `${(lane / lanes) * 100}%`,
                      width: `${100 / lanes}%`,
                    }}
                  >
                    <span
                      className={["w-1 shrink-0", e.tone === "custom" ? (e.look ?? "") : BAR[e.tone]].join(" ")}
                    />
                    <span className="min-w-0 flex-1 truncate px-1.5 py-0.5 text-foreground">
                      {fmtClockShort(e.start)} {e.title}
                    </span>
                  </button>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
