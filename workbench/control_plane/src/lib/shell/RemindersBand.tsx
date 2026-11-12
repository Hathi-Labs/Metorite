"use client";

/**
 * My Day's Reminders band (RX-3, `calendar_events_reminders.md` §5.6).
 *
 * A reminder is a moment of the day, so it does not sit in the groups of
 * tasks. The band is a card of its own above the feed's groups. It holds the
 * reminders that fired and still wait on the member, in strong rows, and then
 * the reminders of the rest of today in quiet rows. It draws nothing when it
 * has no row.
 *
 * Each row is a Bell glyph (not the Calendar's icon), the title, one line with
 * the exact time and the linked object, and Done and Snooze in place
 * (`ReminderActs`). A tap on the row opens the chat card, as the push does.
 *
 * Every rule is in `reminderBand.ts`. This file draws it.
 */
import Link from "next/link";
import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { accentForHue } from "@/lib/statusAccent";

import type { Feed } from "./FeedCard";
import ReminderActs from "./ReminderActs";
import { type BandRow, bandCount, bandRows, cutBand, isWaiting, rowMeta } from "./reminderBand";

const WAITING = accentForHue("amber");
const QUIET = accentForHue("gray");
const DANGER = accentForHue("red");

export default function RemindersBand({
  feed,
  now,
  className = "",
  tabbed = false,
}: {
  feed: Pick<Feed, "answer" | "marked" | "hidden" | "errors" | "refresh">;
  /** The Reminders tab is open and names the band, so the band drops its own heading word. */
  tabbed?: boolean;
  /** The page's clock, by the minute. A render reads no clock itself. */
  now: Date | null;
  className?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  // No clock yet (the server render): draw nothing, so hydration cannot differ.
  if (!now || !feed.answer) return null;
  const nowMs = now.getTime();
  const rows = bandRows(feed.answer, feed.marked, feed.hidden, nowMs);
  if (rows.length === 0) return null;
  const { shown, hidden } = cutBand(rows, expanded);
  const waiting = bandCount(rows);
  return (
    <section
      aria-labelledby="my-day-reminders"
      data-testid="my-day-reminders"
      data-reminder-band=""
      className={`min-w-0 ${className}`}
    >
      <div className="flex items-center gap-1.5 px-1 pb-2 text-xs font-medium text-muted-foreground">
        <Icon name="Bell" size={13} className={waiting > 0 ? WAITING.text : undefined} />
        <h2 id="my-day-reminders" className={tabbed ? "sr-only" : "text-xs font-medium"}>
          Reminders
        </h2>
        {waiting > 0 ? (
          <span className={`tabular-nums ${WAITING.text}`} data-reminder-waiting="">
            {waiting} due
          </span>
        ) : null}
      </div>
      <ul className="flex flex-col divide-y divide-border/60 overflow-hidden rounded-xl border border-border bg-card glass:rounded-[16px] glass:border-transparent glass:glass-raised">
        {shown.map((row) => (
          <BandItem key={row.key} row={row} nowMs={nowMs} error={feed.errors[row.key]} refresh={feed.refresh} />
        ))}
      </ul>
      {hidden > 0 ? (
        <div className="flex justify-center pt-1">
          <Button variant="ghost" size="sm" icon="ChevronDown" onClick={() => setExpanded(true)}>
            Show all {rows.length}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function BandItem({
  row,
  nowMs,
  error,
  refresh,
}: {
  row: BandRow;
  nowMs: number;
  error?: string;
  refresh: () => void;
}) {
  const waiting = isWaiting(row);
  return (
    <li
      data-reminder-row={row.phase}
      className={`flex min-w-0 items-center gap-3 px-3 py-2.5 ${waiting ? `border-l-2 ${WAITING.bar}` : ""}`}
    >
      <span
        aria-hidden
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${waiting ? `${WAITING.soft} ${WAITING.text}` : `${QUIET.soft} ${QUIET.text}`}`}
      >
        <Icon name="Bell" size={15} />
      </span>
      <Link href={row.href} className="min-w-0 flex-1 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {/* On a phone the title and the line may wrap to two lines, so the exact
            time and the linked object stay readable at 390 px. From `sm` they truncate. */}
        <span className={`block line-clamp-2 break-words text-sm text-foreground sm:truncate ${waiting ? "font-medium" : ""}`}>{row.title}</span>
        <span className="block line-clamp-2 break-words text-xs text-muted-foreground sm:truncate" data-reminder-meta="">
          {rowMeta(row, nowMs)}
        </span>
        {error ? (
          <span role="alert" className={`block text-xs ${DANGER.text}`}>
            {error}
          </span>
        ) : null}
      </Link>
      <ReminderActs row={row} refresh={refresh} quiet={!waiting} />
    </li>
  );
}
