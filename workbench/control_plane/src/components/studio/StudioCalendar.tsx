"use client";

/**
 * The Calendar block (`ai_studio.md` §14 D5, slice D5-W2).
 *
 * The records of an object, placed by a date or datetime field, in a Month, Week or Day grid. The
 * mode and the date are the viewer's choice and live in the state of the block. The block reads
 * only the visible period: the `list` of the view with a `gte` and an `lt` on the start field.
 * Month uses `MonthGrid`, and Week and Day use `WeekGrid`. Both are read only. A record opens
 * through its link, so the frame shows it in the panel. A calendar with no get action has plain
 * chips with no link.
 */
import { useMemo, useState } from "react";

import { MonthGrid } from "@/components/calendar/MonthGrid";
import { rowsToEvents, type ObjectRow } from "@/components/calendar/objectEvents";
import type { GridEvent } from "@/components/calendar/types";
import { EMPTY_SENTENCE, WeekGrid } from "@/components/calendar/WeekGrid";
import RecordsStatus from "@/components/studio/RecordsStatus";
import { PANEL } from "@/components/studio/StudioPage";
import Tabs from "@/components/Tabs";
import Button from "@/components/ui/Button";
import {
  DEFAULT_MODE, capSentence, eventsOnDay, periodSource, periodTitle, stepPeriod, switchMode, type CalendarMode,
} from "@/lib/studio/calendarWindow";
import { moreLine, sourceKey, type Row, type StudioDataDoor } from "@/lib/studio/dataDoor";
import type { ViewCalendar } from "@/lib/studio/viewBlocks";
import { useCachedResource } from "@/lib/useCachedResource";

const MODES = [
  { id: "month", label: "Month" },
  { id: "week", label: "Week" },
  { id: "day", label: "Day" },
];

export interface StudioCalendarProps {
  model: ViewCalendar;
  door: StudioDataDoor;
  /** Opens a record in the panel. The month grid has no link, so it uses this. */
  onOpenRecord?: (id: string) => void;
  /** The clock. A test or a capture sets it. The default is the time at the first draw. */
  now?: Date;
}

export default function StudioCalendar({ model, door, onOpenRecord, now: nowProp }: StudioCalendarProps) {
  const [now] = useState(() => nowProp ?? new Date());
  const [mode, setMode] = useState<CalendarMode>(DEFAULT_MODE);
  const [anchor, setAnchor] = useState<Date>(now);
  const source = useMemo(
    () => periodSource(model.source, model.start, model.startType, mode, anchor),
    [model.source, model.start, model.startType, mode, anchor]);
  const rows = useCachedResource<Row[]>(sourceKey(door, source), () => door.read(source));
  const events = useMemo<GridEvent[]>(() => {
    const list: ObjectRow[] = (rows.data ?? []).filter((r) => r.id != null).map((r) => ({ id: String(r.id), values: r }));
    return rowsToEvents(list, {
      dateField: model.start, titleField: model.titleField, ...(model.end ? { endField: model.end } : {}),
    });
  }, [rows.data, model.start, model.end, model.titleField]);

  const base = model.openBase;
  const hrefOf = base ? (e: GridEvent) => `${base}${encodeURIComponent(e.id)}` : undefined;
  const limit = typeof model.source.args.limit === "number" ? model.source.args.limit : 100;
  const cap = rows.data ? capSentence(rows.data.length, limit, moreLine(rows.data) !== null) : null;
  const failed = rows.error !== null && rows.data === undefined;
  const ready = rows.data !== undefined;

  return (
    <section className={PANEL} aria-label={model.title}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-sm font-semibold text-foreground">{model.title}</h2>
        <Button variant="ghost" size="icon-sm" icon="ChevronLeft" aria-label="Previous period"
          onClick={() => setAnchor((a) => stepPeriod(mode, a, -1))} />
        <Button variant="secondary" size="sm" onClick={() => setAnchor(now)}>Today</Button>
        <Button variant="ghost" size="icon-sm" icon="ChevronRight" aria-label="Next period"
          onClick={() => setAnchor((a) => stepPeriod(mode, a, 1))} />
        <span className="text-xs font-medium text-foreground">{periodTitle(mode, anchor)}</span>
        <Tabs tabs={MODES} activeTab={mode} variant="segmented" chrome="inline"
          onTabChange={(m) => { setAnchor((a) => switchMode(mode, m as CalendarMode, a)); setMode(m as CalendarMode); }} />
      </div>
      {failed ? <RecordsStatus loading={false} error={rows.error} onRetry={rows.refresh} /> : null}
      {!ready && !failed ? <p role="status" className="text-xs text-muted-foreground">Loading…</p> : null}
      {ready ? (
        <>
          <div className={mode === "month" ? "min-h-[28rem]" : "max-h-[70vh] overflow-auto"}>
            {mode === "month" ? (
              <MonthGrid anchor={anchor} eventsForDay={(day) => eventsOnDay(events, day)}
                onPickDay={(d) => { setAnchor(d); setMode("day"); }}
                onOpenEvent={(e) => { if (base && onOpenRecord) onOpenRecord(e.id); }} />
            ) : (
              <WeekGrid events={events} mode={mode} anchor={anchor} now={now} hrefOf={hrefOf} />
            )}
          </div>
          {mode === "month" && events.length === 0 ? (
            <p role="status" className="px-1 pt-2 text-xs text-muted-foreground">{EMPTY_SENTENCE}</p>
          ) : null}
          {cap ? <p role="status" className="mt-2 text-xs text-muted-foreground">{cap}</p> : null}
        </>
      ) : null}
    </section>
  );
}
