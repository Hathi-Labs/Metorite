"use client";

/**
 * The Reminders panel (WS-66 RM-5, `calendar_events_reminders.md` §6.2).
 *
 * Three lists of the member's open reminders: Waiting on you (`due`),
 * Unconfirmed (`unconfirmed`) and Upcoming (`scheduled`). A tap on a row opens
 * the chat of the reminder (RM-4). Done and Drop close it, and "Move to…" opens
 * the date and time form.
 *
 * On a desktop the panel is a column beside the grid, in every mode, so month
 * mode keeps it. Day and Week put the Unscheduled rail under it, so the panel
 * shows at most `PANEL_ROWS` rows, then a "Show all (N)" toggle. The toggle
 * sits in a footer OUTSIDE the scroll box, so it always shows, collapsed and
 * expanded. When the column is too short for `PANEL_ROWS` rows, the panel
 * shows fewer (`fit`). Month has no rail, so the panel fills the column and
 * shows every row.
 *
 * On a phone `RemindersSheetBody` fills a bottom sheet that a title bar
 * button opens. Its actions are touch size (26 px, 8 px apart, §9). The
 * parent draws neither when the list is "absent".
 */
import { useLayoutEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { capGroups, splitByState, whenText, type CalReminder } from "../lib/reminders";

type Act = {
  onOpen: (id: string) => void;
  onMove: (id: string) => void;
  onResolve: (id: string, action: "done" | "drop") => void;
};

function Row({ row, zone, act, touch }: { row: CalReminder; zone: string; act: Act; touch: boolean }) {
  const when = whenText(row.fireMs, zone);
  const size = touch ? "icon-sm" : "icon-xs";
  return (
    <li className="group flex items-start gap-1 rounded-md px-1.5 py-1 hover:bg-secondary/60">
      <button
        type="button"
        onClick={() => act.onOpen(row.id)}
        className="min-w-0 flex-1 text-left focus-visible:ring-2 focus-visible:ring-ring"
        // The row cuts a long title, so the tip holds the whole of it (M11).
        title={`${row.title}, ${when}. Open it in the chat.`}
      >
        <span className="block truncate text-xs text-foreground">{row.title}</span>
        <span className="block truncate text-[10px] text-muted-foreground">{when}</span>
      </button>
      <span className={`flex shrink-0 items-center ${touch ? "gap-2" : ""}`}>
        <Button
          variant="ghost"
          size={size}
          icon="Check"
          aria-label={`Done: ${row.title}`}
          title="Done"
          onClick={() => act.onResolve(row.id, "done")}
        />
        <Button
          variant="ghost"
          size={size}
          icon="CalendarClock"
          aria-label={`Move to…: ${row.title}`}
          title="Move to…"
          onClick={() => act.onMove(row.id)}
        />
        <Button
          variant="ghost"
          size={size}
          icon="X"
          aria-label={`Drop: ${row.title}`}
          title="Drop"
          onClick={() => act.onResolve(row.id, "drop")}
        />
      </span>
    </li>
  );
}

function Group({
  label,
  rows,
  zone,
  act,
  touch,
}: {
  label: string;
  rows: CalReminder[];
  zone: string;
  act: Act;
  touch: boolean;
}) {
  if (rows.length === 0) return null;
  return (
    <section className="px-1.5 py-1.5">
      <h3 className="px-1.5 pb-0.5 text-[10px] font-medium uppercase text-muted-foreground">
        {label} · {rows.length}
      </h3>
      <ul className="flex flex-col">
        {rows.map((r) => (
          <Row key={r.id} row={r} zone={zone} act={act} touch={touch} />
        ))}
      </ul>
    </section>
  );
}

/** The three lists, in the order the panel draws them. It has no scroll box of its own. */
function Lists({
  rows,
  zone,
  limit,
  act,
  touch,
}: {
  rows: CalReminder[];
  zone: string;
  limit: number | null;
  act: Act;
  touch: boolean;
}) {
  const { groups: g } = capGroups(splitByState(rows), limit);
  return (
    <>
      {rows.length === 0 && <p className="px-3 py-3 text-xs text-muted-foreground">No open reminders.</p>}
      <Group label="Waiting on you" rows={g.waiting} zone={zone} act={act} touch={touch} />
      <Group label="Unconfirmed" rows={g.unconfirmed} zone={zone} act={act} touch={touch} />
      <Group label="Upcoming" rows={g.upcoming} zone={zone} act={act} touch={touch} />
    </>
  );
}

/** "+N more": the open reminders past the 500 rows of the one read. */
function MoreLine({ more }: { more: number }) {
  if (more <= 0) return null;
  return <p className="px-3 py-1.5 text-[10px] text-muted-foreground">+{more} more</p>;
}

type ListProps = { rows: CalReminder[]; total: number; zone: string; onNew: () => void } & Act;

/** The phone sheet: New reminder, then every row, with touch-size actions. */
export function RemindersSheetBody({ rows, total, zone, onNew, ...act }: ListProps) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto pb-2">
      <div className="px-3 pt-2">
        <Button variant="secondary" size="sm" icon="Plus" onClick={onNew}>
          New reminder
        </Button>
      </div>
      <Lists rows={rows} zone={zone} limit={null} act={act} touch />
      <MoreLine more={total - rows.length} />
    </div>
  );
}

/** The rows that the desktop panel shows before "Show all", at most. It leaves room for the Unscheduled rail. */
export const PANEL_ROWS = 4;

/**
 * The desktop column. `fill` is month mode: no rail under it, so the panel
 * takes the whole column and shows every row.
 */
export function RemindersPanel({ rows, total, zone, onNew, fill = false, ...act }: ListProps & { fill?: boolean }) {
  const [all, setAll] = useState(false);
  // The rows that fit in the box, at most PANEL_ROWS.
  const [fit, setFit] = useState(PANEL_ROWS);
  const [count, setCount] = useState(rows.length);
  if (count !== rows.length) {
    // A new count of rows fits again from the top.
    setCount(rows.length);
    setFit(PANEL_ROWS);
  }
  const listRef = useRef<HTMLDivElement>(null);
  const limit = fill || all ? null : fit;
  const { hidden } = capGroups(splitByState(rows), limit);
  const toggle = !fill && (all ? rows.length > fit : hidden > 0);
  const more = total - rows.length;

  // Fit the collapsed list to its box: while the list must scroll, show one
  // row less, down to one. The toggle in the footer still says how many.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || limit === null || fit <= 1) return;
    if (el.scrollHeight > el.clientHeight + 1) setFit((n) => Math.max(1, n - 1));
  });

  // A new height of the column (a window resize, a banner, a density
  // change) fits again from PANEL_ROWS.
  useLayoutEffect(() => {
    const column = listRef.current?.closest("aside")?.parentElement;
    if (!column || typeof ResizeObserver === "undefined") return;
    let height = column.clientHeight;
    const watch = new ResizeObserver(() => {
      if (column.clientHeight === height) return;
      height = column.clientHeight;
      setFit(PANEL_ROWS);
    });
    watch.observe(column);
    return () => watch.disconnect();
  }, []);

  return (
    <aside
      aria-label="Reminders"
      className={[
        "flex w-64 min-h-0 flex-col border-l border-border bg-card",
        fill ? "flex-1" : `shrink-0 border-b ${all ? "max-h-[70%]" : "max-h-[50%]"}`,
      ].join(" ")}
    >
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border px-3 py-2">
        <Icon name="Bell" className="h-3.5 w-3.5 text-primary" />
        <span className="flex-1 text-xs font-semibold text-foreground">Reminders</span>
        <Button variant="secondary" size="sm" icon="Plus" onClick={onNew}>
          New reminder
        </Button>
      </div>
      <div ref={listRef} className="min-h-0 shrink overflow-y-auto">
        <Lists rows={rows} zone={zone} limit={limit} act={act} touch={false} />
        {fill && <MoreLine more={more} />}
      </div>
      {!fill && (toggle || more > 0) && (
        // The footer sits outside the scroll box, so the toggle always shows.
        <div className="shrink-0 border-t border-border">
          {toggle && (
            <button
              type="button"
              aria-expanded={all}
              onClick={() => setAll((v) => !v)}
              className={[
                "tech-transition w-full px-3 py-1.5 text-left text-xs hover:bg-secondary/60 focus-visible:ring-2 focus-visible:ring-ring",
                all ? "text-muted-foreground" : "font-medium text-primary",
              ].join(" ")}
            >
              {all ? "Show fewer" : `Show all (${rows.length})`}
            </button>
          )}
          <MoreLine more={more} />
        </div>
      )}
    </aside>
  );
}
