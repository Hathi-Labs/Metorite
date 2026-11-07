"use client";

/**
 * Done and Snooze of one reminder row (RX-3). My Day's Reminders band and the
 * bell both draw it, so a reminder acts the same in both. The acts write the
 * reminder row (`reminderActions.ts`). They never write card state.
 *
 * Snooze opens `ContextMenu`, the one menu, with the choices of
 * `snoozeChoices`: In 1 hour, This evening, Tomorrow morning.
 */
import { useRef, useState } from "react";

import { ContextMenu, type CtxItem } from "@/components/ContextMenu";
import { themedIcon } from "@/components/Icon";
import Button from "@/components/ui/Button";

import { useMemberZone } from "./memberZone";
import { snoozeChoices, snoozeZone } from "./reminderBand";
import { type ActRow, useReminderActions } from "./reminderActions";

const CHOICE_ICON = { hour: "Clock", evening: "Sunset", tomorrow: "Sunrise" } as const;

export default function ReminderActs({
  row,
  refresh,
  quiet = false,
}: {
  row: ActRow;
  /** The page reads again after a write. */
  refresh: () => void;
  /** Quiet rows (an upcoming reminder) draw both acts as icons, so the row stays calm. */
  quiet?: boolean;
}) {
  const acts = useReminderActions(refresh);
  // The member's saved zone names "This evening" and "Tomorrow morning" (RX-7).
  const saved = useMemberZone();
  const busy = acts.pending.has(row.key);
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  // The choices are read when the menu opens, so "This evening" is true for that moment.
  const items = (): CtxItem[] => [
    { kind: "label", label: "Snooze" },
    ...snoozeChoices(new Date(), snoozeZone(saved)).map(
      (c): CtxItem => ({
        kind: "item",
        label: c.label,
        icon: themedIcon(CHOICE_ICON[c.id]),
        onSelect: () => void acts.snooze(row, c),
      }),
    ),
  ];
  const closeMenu = () => {
    setMenuAt(null);
    const from = opener.current;
    requestAnimationFrame(() => {
      if (from?.isConnected) from.focus();
    });
  };
  return (
    <div className="flex shrink-0 items-center gap-1" data-reminder-acts="">
      <Button
        variant={quiet ? "ghost" : "secondary"}
        size="sm"
        icon="Check"
        loading={busy}
        data-reminder-done=""
        aria-label={`Mark ${row.title} done`}
        onClick={() => void acts.done(row)}
        className="min-h-9"
      >
        {/* Below `sm` the word goes, as Snooze's does, so the row's text
            keeps the width (task 1898). A due row keeps its filled look,
            and the label stays in `aria-label`. */}
        <span className="hidden sm:inline">Done</span>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        icon="Clock"
        disabled={busy}
        data-reminder-snooze=""
        aria-label={`Snooze ${row.title}`}
        aria-haspopup="menu"
        aria-expanded={menuAt !== null}
        onClick={(e) => {
          opener.current = e.currentTarget;
          const r = e.currentTarget.getBoundingClientRect();
          setMenuAt({ x: Math.max(4, r.right - 190), y: r.bottom + 4 });
        }}
        className="min-h-9"
      >
        <span className="hidden sm:inline">Snooze</span>
      </Button>
      {menuAt ? <ContextMenu x={menuAt.x} y={menuAt.y} autoFocus items={items()} onClose={closeMenu} /> : null}
    </div>
  );
}
