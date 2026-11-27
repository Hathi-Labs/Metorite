"use client";

import { useEffect, useMemo, type ReactNode, type RefObject } from "react";

import { DOW, HOUR_PX, hourLabel } from "@/app/calendar/components/shared";
import { fmtClock } from "@/app/tasks/lib/utils";
import { TONE } from "./MonthGrid";
import type { GridEvent } from "./types";
import {
  buildWeek, initialScrollTop, nowDayIndex, nowTop, windowHeight, type AllDayBar, type TimedBlock, type WeekMode,
} from "./weekModel";

/** The sentence for a period with no event. */
export const EMPTY_SENTENCE = "Nothing is scheduled in this period.";

const chipLook = (e: GridEvent) => (e.tone === "custom" ? (e.look ?? "") : TONE[e.tone]);
/** A lane count at which a chip is too narrow for a time line. A week column is narrow already. */
export const compactLanes = (mode: WeekMode) => (mode === "week" ? 2 : 3);
/** A block shorter than this (in px) has room for one line only. */
export const ONE_LINE_PX = 30;
/** The room above the first hour line, so the first hour label is not clipped. About half a label. */
const BODY_PAD = "pt-2";

const range = (e: GridEvent) => (e.allDay ? "" : `${fmtClock(e.start)} – ${fmtClock(e.end)}`);

/**
 * A read-only Day or Week time grid. It draws the model of `weekModel.ts`. It has no drag, no
 * resize and no card of its own. The caller says which events to draw, and what time it is, so
 * a capture is the same every time. The Calendar app keeps its own `TimeGrid`.
 */
export function WeekGrid({
  events, mode, anchor, now, hourPx = HOUR_PX, weekdayLabels = DOW, hrefOf, renderEvent, onOpenEvent, scrollRef,
}: {
  events: GridEvent[];
  mode: WeekMode;
  anchor: Date;
  now: Date;
  hourPx?: number;
  /** Seven short names, Monday first. */
  weekdayLabels?: readonly string[];
  /** The link of an event, or null. A link makes the whole chip an anchor. */
  hrefOf?: (event: GridEvent) => string | null;
  /** The inside of a chip. The default is the title and the time range. */
  renderEvent?: (event: GridEvent) => ReactNode;
  /** Runs when a member clicks the link of a chip. A chip with no link does nothing. */
  onOpenEvent?: (event: GridEvent) => void;
  /** The scroll box that holds the grid. The grid scrolls it to the first useful hour. */
  scrollRef?: RefObject<HTMLElement | null>;
}) {
  const model = useMemo(() => buildWeek(events, { anchor, mode, hourPx }), [events, anchor, mode, hourPx]);
  const byId = useMemo(() => new Map(events.map((e) => [e.id, e])), [events]);
  const { days, window } = model;
  const week = days.length > 1;
  const hours = Array.from({ length: window.endHour - window.startHour }, (_, i) => window.startHour + i);
  const gridHeight = windowHeight(window, hourPx);
  const todayIndex = nowDayIndex(days, now);
  const nowPx = nowTop(now, window, hourPx, days);
  const hasAllDay = model.allDay.length > 0;
  const scrollTop = useMemo(
    () => initialScrollTop(model.timed.flatMap((t) => t.blocks), nowPx, hourPx, window.startHour),
    [model.timed, nowPx, hourPx, window.startHour]);
  const scrollKey = `${mode}:${days[0]?.key ?? ""}`;
  // Open at one hour before the first block. This runs when the range or the mode changes.
  useEffect(() => {
    const box = scrollRef?.current;
    if (box && typeof box.scrollTo === "function") box.scrollTo({ top: scrollTop });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollKey]);
  const colStyle = { minWidth: week ? 96 : undefined } as const;

  const chip = (
    e: GridEvent, cls: string, style?: React.CSSProperties, opts: { compact?: boolean; oneLine?: boolean } = {},
  ) => {
    const href = hrefOf?.(e) ?? null;
    const hideTime = opts.compact || opts.oneLine;
    const body = renderEvent ? renderEvent(e) : (
      <>
        <span className="block truncate font-medium">{e.title}</span>
        {range(e) && !hideTime ? <span className="block truncate text-[10px] text-muted-foreground">{range(e)}</span> : null}
      </>
    );
    // A chip with no time line keeps the time on the title attribute.
    const hint = hideTime && range(e) ? `${e.title}, ${range(e)}` : e.title;
    const all = `${cls} ${chipLook(e)} block overflow-hidden rounded px-1 text-left text-[11px] leading-tight`;
    if (href) {
      return (
        <a key={e.id} href={href} title={hint} className={all} style={style}
          onClick={onOpenEvent ? () => onOpenEvent(e) : undefined}>{body}</a>
      );
    }
    return <div key={e.id} title={hint} className={all} style={style}>{body}</div>;
  };

  const timed = (b: TimedBlock) => {
    const e = byId.get(b.id);
    if (!e) return null;
    const left = (b.lane / b.lanes) * 100;
    // The tint is translucent. The wrapper is opaque, so an hour line does not show through a title.
    return (
      <div key={e.id} data-block={e.id} className="absolute rounded bg-card"
        style={{ top: b.topPx, height: b.heightPx, left: `calc(${left}% + 1px)`, width: `calc(${100 / b.lanes}% - 2px)` }}>
        {chip(e, "h-full", undefined, { compact: b.lanes >= compactLanes(mode), oneLine: b.heightPx < ONE_LINE_PX })}
      </div>
    );
  };

  const bar = (b: AllDayBar) => {
    const e = byId.get(b.id);
    if (!e) return null;
    const mark = `${b.clippedLeft ? "rounded-l-none " : ""}${b.clippedRight ? "rounded-r-none " : ""}`;
    return chip(e, `${mark}my-0.5 mx-0.5 truncate`, { gridColumn: `${b.startCol + 1} / span ${b.span}` });
  };

  return (
    <div className="min-w-fit" data-week-grid={mode}>
      <div className="sticky top-0 z-10 bg-background">
        <div className="flex border-b border-border">
          <div className="w-14 shrink-0" />
          {days.map((d, i) => {
            const today = i === todayIndex;
            return (
              <div key={d.key} data-day={d.key} style={colStyle}
                className="flex-1 border-l border-border px-2 py-1.5 text-center">
                <div className="text-[10px] font-medium uppercase text-muted-foreground">
                  {weekdayLabels[(d.date.getDay() + 6) % 7]}
                </div>
                <div aria-current={today ? "date" : undefined}
                  className={`text-sm font-semibold ${today ? "text-primary" : "text-foreground"}`}>
                  {d.date.getDate()}
                </div>
              </div>
            );
          })}
        </div>
        {hasAllDay ? (
          <div className="flex border-b border-border" data-all-day>
            <div className="w-14 shrink-0 pr-2 pt-1 text-right text-[10px] text-muted-foreground">All day</div>
            <div className="grid flex-1 border-l border-border"
              style={{ gridTemplateColumns: `repeat(${days.length}, minmax(0, 1fr))`, minWidth: week ? 96 * days.length : undefined }}>
              {model.allDay.map(bar)}
            </div>
          </div>
        ) : null}
      </div>

      <div className={BODY_PAD}>
      <div className="relative z-0 flex">
        <div className="relative w-14 shrink-0">
          {hours.map((h) => (
            <div key={h} style={{ height: hourPx }} className="relative pr-2 text-right text-[10px] text-muted-foreground">
              <span className="absolute right-2 top-0 -translate-y-1/2">{hourLabel(h)}</span>
            </div>
          ))}
        </div>
        {model.timed.map(({ dayIndex, blocks }) => (
          <div key={days[dayIndex].key} data-column={days[dayIndex].key} style={{ ...colStyle, height: gridHeight }}
            className="relative flex-1 border-l border-border">
            {hours.map((h) => (
              <div key={h} style={{ height: hourPx }} className="border-b border-border/60" />
            ))}
            {dayIndex === todayIndex && nowPx !== null ? (
              <div data-now-line className="pointer-events-none absolute inset-x-0 border-t-2 border-primary"
                style={{ top: nowPx }}>
                <span className="absolute -left-1 -top-1 h-2 w-2 rounded-full bg-primary" />
              </div>
            ) : null}
            {blocks.map(timed)}
          </div>
        ))}
      </div>
      </div>
    </div>
  );
}

export default WeekGrid;
