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
 * mode keeps it. On a phone `RemindersList` fills a bottom sheet that a title
 * bar button opens. The parent draws neither when the list is "absent".
 */
import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { splitByState, whenText, type CalReminder } from "../lib/reminders";

type Act = {
  onOpen: (id: string) => void;
  onMove: (id: string) => void;
  onResolve: (id: string, action: "done" | "drop") => void;
};

function Row({ row, zone, act }: { row: CalReminder; zone: string; act: Act }) {
  return (
    <li className="group flex items-start gap-1 rounded-md px-1.5 py-1 hover:bg-secondary/60">
      <button
        type="button"
        onClick={() => act.onOpen(row.id)}
        className="min-w-0 flex-1 text-left focus-visible:ring-2 focus-visible:ring-ring"
        title="Open this reminder in the chat"
      >
        <span className="block truncate text-xs text-foreground">{row.title}</span>
        <span className="block truncate text-[10px] text-muted-foreground">{whenText(row.fireMs, zone)}</span>
      </button>
      <span className="flex shrink-0 items-center">
        <Button
          variant="ghost"
          size="icon-xs"
          icon="Check"
          aria-label={`Done: ${row.title}`}
          title="Done"
          onClick={() => act.onResolve(row.id, "done")}
        />
        <Button
          variant="ghost"
          size="icon-xs"
          icon="CalendarClock"
          aria-label={`Move to…: ${row.title}`}
          title="Move to…"
          onClick={() => act.onMove(row.id)}
        />
        <Button
          variant="ghost"
          size="icon-xs"
          icon="X"
          aria-label={`Drop: ${row.title}`}
          title="Drop"
          onClick={() => act.onResolve(row.id, "drop")}
        />
      </span>
    </li>
  );
}

function Group({ label, rows, zone, act }: { label: string; rows: CalReminder[]; zone: string; act: Act }) {
  if (rows.length === 0) return null;
  return (
    <section className="px-1.5 py-1.5">
      <h3 className="px-1.5 pb-0.5 text-[10px] font-medium uppercase text-muted-foreground">
        {label} · {rows.length}
      </h3>
      <ul className="flex flex-col">
        {rows.map((r) => (
          <Row key={r.id} row={r} zone={zone} act={act} />
        ))}
      </ul>
    </section>
  );
}

/** The three lists. The panel and the phone sheet both draw it. */
export function RemindersList({
  rows,
  total,
  zone,
  ...act
}: { rows: CalReminder[]; total: number; zone: string } & Act) {
  const g = splitByState(rows);
  const more = Math.max(0, total - rows.length);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {rows.length === 0 && (
        <p className="px-3 py-3 text-xs text-muted-foreground">No open reminders.</p>
      )}
      <Group label="Waiting on you" rows={g.waiting} zone={zone} act={act} />
      <Group label="Unconfirmed" rows={g.unconfirmed} zone={zone} act={act} />
      <Group label="Upcoming" rows={g.upcoming} zone={zone} act={act} />
      {more > 0 && <p className="px-3 py-1.5 text-[10px] text-muted-foreground">+{more} more</p>}
    </div>
  );
}

/** The desktop column. */
export function RemindersPanel({
  onNew,
  ...list
}: { rows: CalReminder[]; total: number; zone: string; onNew: () => void } & Act) {
  return (
    <aside
      aria-label="Reminders"
      className="hidden max-h-[45%] w-64 shrink-0 flex-col border-b border-l border-border bg-card md:flex"
    >
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        <Icon name="Bell" className="h-3.5 w-3.5 text-primary" />
        <span className="flex-1 text-xs font-semibold text-foreground">Reminders</span>
        <Button variant="secondary" size="sm" icon="Plus" onClick={onNew}>
          New reminder
        </Button>
      </div>
      <RemindersList {...list} />
    </aside>
  );
}
