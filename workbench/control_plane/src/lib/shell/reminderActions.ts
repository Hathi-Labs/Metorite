/**
 * The acts of a reminder row, for every surface that draws one (RX-3): My
 * Day's Reminders band and the bell. Done and Snooze write the REMINDER row
 * through `reminderOpen.ts`, the one client of the routes. They never write
 * card state. The row leaves at once through the needs store (`removeRow`),
 * and a toast offers Undo.
 *
 * Plain functions, with the clock, the toast and the client passed in, so a
 * test runs them with no DOM. `useReminderActions` binds them to the page.
 *
 * Fence: `reminderActions.test.ts`.
 */
import { useCallback, useRef, useState } from "react";

import { useToast } from "@/components/ui/Toast";

import { removeRow, setRowError } from "./needsActs";
import { type ActResult, runReminderAct, runReminderMove } from "./reminderOpen";
import { type BandRow, type SnoozeChoice, snoozedWords, undoMove } from "./reminderBand";
import { FIRED_REMINDER_KINDS, reminderIdOf } from "./reminderKinds";
import type { NeedsItem } from "./needs";

/** What an act needs of a row: the band's row, or a bell row made with `rowOfNeeds`. */
export type ActRow = Pick<BandRow, "id" | "key" | "title" | "phase" | "at" | "atMs">;

export interface ActDeps {
  act: typeof runReminderAct;
  move: typeof runReminderMove;
  /** Show a confirmation, with an Undo. */
  say: (words: string, undo?: () => void, tone?: "error") => void;
  /** The page re-reads after a write. */
  refresh: () => void;
  now: () => Date;
}

/**
 * The row of a bell item, or null when the item is not a reminder that waits
 * on the member. The bell's Done and Snooze go for `reminder_due` and
 * `reminder_check` only: an unconfirmed reminder has no quick act.
 */
export function rowOfNeeds(item: Pick<NeedsItem, "id" | "kind" | "title" | "at">): ActRow | null {
  const id = reminderIdOf(item.id);
  if (!id || !FIRED_REMINDER_KINDS.has(item.kind)) return null;
  const atMs = item.at ? Date.parse(item.at) : NaN;
  return {
    id,
    key: item.id,
    title: item.title,
    phase: item.kind === "reminder_check" ? "check" : "fired",
    at: item.at,
    atMs: Number.isNaN(atMs) ? null : atMs,
  };
}

/** The words of a refused write go in the row's error line (the needs store). */
function fail(row: ActRow, out: ActResult): boolean {
  if (out.ok) return false;
  setRowError(row.key, out.words);
  return true;
}

/** Done: the reminder closes, the row leaves everywhere, and Undo opens it again. */
export async function doneReminder(row: ActRow, deps: ActDeps): Promise<boolean> {
  setRowError(row.key, null);
  const out = await deps.act(row.id, "done");
  if (fail(row, out)) return false;
  removeRow(row.key, true);
  deps.say("Reminder done", () => void undoDone(row, deps));
  deps.refresh();
  return true;
}

async function undoDone(row: ActRow, deps: ActDeps): Promise<void> {
  const out = await deps.act(row.id, "undo");
  if (!out.ok) {
    fail(row, out);
    deps.say(out.words, undefined, "error");
    return;
  }
  removeRow(row.key, false);
  deps.say("The reminder is open again");
  deps.refresh();
}

/** Snooze to a chosen time through `/snooze`. Undo moves it back (`undoMove`). */
export async function snoozeReminder(row: ActRow, choice: SnoozeChoice, deps: ActDeps): Promise<boolean> {
  setRowError(row.key, null);
  const nowMs = deps.now().getTime();
  const out = await deps.move(row.id, "snooze", choice.iso);
  if (fail(row, out)) return false;
  removeRow(row.key, true);
  deps.say(snoozedWords(choice, deps.now()), () => void undoSnooze(row, nowMs, deps));
  deps.refresh();
  return true;
}

async function undoSnooze(row: ActRow, wasNowMs: number, deps: ActDeps): Promise<void> {
  const out = await deps.move(row.id, "reschedule", undoMove(row, wasNowMs));
  if (!out.ok) {
    fail(row, out);
    deps.say(out.words, undefined, "error");
    return;
  }
  removeRow(row.key, false);
  deps.say("Snooze undone");
  deps.refresh();
}

/**
 * The acts bound to the page. One write at a time for each reminder: a second
 * tap on a row that is still writing does nothing.
 */
export function useReminderActions(refresh: () => void) {
  const toast = useToast();
  const busy = useRef(new Set<string>());
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());

  const guard = useCallback(
    async (row: ActRow, run: (deps: ActDeps) => Promise<boolean>) => {
      if (busy.current.has(row.key)) return;
      busy.current.add(row.key);
      setPending(new Set(busy.current));
      const deps: ActDeps = {
        act: runReminderAct,
        move: runReminderMove,
        refresh,
        now: () => new Date(),
        say: (words, undo, tone) => {
          if (!words) return;
          toast.show({
            key: "reminder-act",
            variant: tone ?? "success",
            title: words,
            action: undo ? { label: "Undo", onClick: undo } : undefined,
            timeout: 8000,
          });
        },
      };
      try {
        await run(deps);
      } finally {
        busy.current.delete(row.key);
        setPending(new Set(busy.current));
      }
    },
    [refresh, toast],
  );

  return {
    pending,
    done: (row: ActRow) => guard(row, (d) => doneReminder(row, d)),
    snooze: (row: ActRow, choice: SnoozeChoice) => guard(row, (d) => snoozeReminder(row, choice, d)),
  };
}
