"use client";

/**
 * The all-day row (CE-5a, `calendar_events_reminders.md` §6.4). A strip above
 * the time grid. An all-day entry that spans days is one bar. More than two
 * in a day fold to "+N", and "+N" opens the rest in place.
 */
import { useState } from "react";

import { allDayRow, type ExternalView } from "../lib/externalEntries";
import { externalLook } from "./externalLook";

export function ExternalAllDayRow({
  days,
  externals,
  onOpen,
}: {
  days: Date[];
  externals: ExternalView[];
  onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const row = allDayRow(externals, days, open);
  const folded = (open ? allDayRow(externals, days, false) : row).more.some((n) => n > 0);
  const lanes = Math.max(1, row.laneCount);
  return (
    <div
      className={`grid min-h-6 gap-y-0.5 border-b border-border py-0.5 ${open ? "max-h-24 overflow-y-auto" : ""}`}
      style={{ gridTemplateColumns: `3.5rem repeat(${days.length}, minmax(96px, 1fr))` }}
    >
      <span className="row-start-1 self-center pr-1 text-right text-[9px] uppercase text-muted-foreground" style={{ gridColumn: 1, gridRow: 1 }}>
        All day
      </span>
      {row.bars.map((b) => (
        <button
          key={`${b.entry.id}-${b.from}`}
          type="button"
          onClick={() => onOpen(b.entry.id)}
          title={b.entry.title}
          className={`mx-0.5 truncate rounded px-1.5 py-0.5 text-left text-[11px] leading-tight focus-visible:ring-2 focus-visible:ring-ring ${externalLook(b.entry)}`}
          style={{ gridColumn: `${b.from + 2} / ${b.to + 3}`, gridRow: b.lane + 1 }}
        >
          {b.entry.title}
        </button>
      ))}
      {!open &&
        row.more.map((n, i) =>
          n > 0 ? (
            <button
              key={`more-${i}`}
              type="button"
              onClick={() => setOpen(true)}
              aria-label={`Show ${n} more all-day ${n === 1 ? "event" : "events"}`}
              className="mx-0.5 rounded px-1.5 text-left text-[10px] text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              style={{ gridColumn: i + 2, gridRow: lanes + 1 }}
            >
              +{n}
            </button>
          ) : null,
        )}
      {open && folded && (
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="mx-0.5 rounded px-1.5 text-left text-[10px] text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          style={{ gridColumn: 1, gridRow: lanes + 1 }}
        >
          Show less
        </button>
      )}
    </div>
  );
}
