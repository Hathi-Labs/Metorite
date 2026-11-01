"use client";

/**
 * ReminderReceiptCard — the receipt of a reminder that the chat set, changed or
 * moved (WS-66 RX-2, `calendar_events_reminders.md` §5.8).
 *
 * The owner rule (2026-11-01): a due date is a day, and a reminder is a
 * moment. The receipt of `set_reminder`, `update_reminder` and
 * `reschedule_reminder` is therefore not a plain result row. It is the reminder
 * itself: a Bell, its title, the exact time ("Today, 5:00 PM IST"), the
 * relative time ("in 2 hours") and the task it links, when there is one.
 *
 * Three acts, each at once and none through the model:
 *   - **Undo** puts the write back (`runUndo`): a new reminder is dropped, a
 *     move or a change goes back to the old time and words.
 *   - **Change time** opens a small picker: the presets of "Later" and a date
 *     and a time. It calls `/reschedule` (`moveReminder`).
 *   - **See in Calendar** opens `/calendar?reminder=<id>`.
 *
 * It draws in the frame and the tone of every other receipt (`toneFor`,
 * `glassToneFrame`), and in the same buttons. It adds no colour of its own.
 *
 * Fence (R7): `ReminderReceiptCard.test.ts` beside this file.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import { checkForm } from "@/app/calendar/lib/reminders";
import AppIcon from "@/components/Icon";
import { glassToneFrame } from "@/components/glassCard";
import RollupCard, { RollupBody, RollupHeader } from "@/components/RollupCard";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { exactWhen, gridZone, laterPresets, relativeWhen, wallInputs } from "@/lib/reminderClock";
import { calendarReminderHref, canUndo, receiptZoneName, runUndo, type ReminderReceipt } from "@/lib/reminderReceipt";
import { moveReminder } from "@/lib/reminderWrite";
import { knownZone } from "@/lib/shell/reminderOpen";

/** The frame of a receipt: the same tone string `toneFor("done", …)` gives. */
const TONE = "bg-card/40 border-border text-muted-foreground";

const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export default function ReminderReceiptCard({
  id,
  label,
  receipt,
  openTask,
}: {
  /** The tool event id, stable across a replay. */
  id: string;
  /** "Reminder set", "Reminder changed", "Reminder moved" or "Reminder already set". */
  label: string;
  receipt: ReminderReceipt;
  /** Opens the linked task in Projects. */
  openTask: (taskId: string) => void;
}) {
  const router = useRouter();
  // The zone of the reminder, else the browser's. The time reads in it.
  const zone = knownZone(receipt.zone) ?? gridZone(browserZone());
  const [fireMs, setFireMs] = useState(receipt.fireMs);
  const [title, setTitle] = useState(receipt.title);
  const [undone, setUndone] = useState(false);
  const [dropped, setDropped] = useState(false);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const start = wallInputs(fireMs, zone);
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [refused, setRefused] = useState("");

  const now = Date.now();
  const undoable = !undone && canUndo(receipt, now);

  const undo = async () => {
    if (busy) return;
    setBusy(true);
    const got = await runUndo(receipt);
    setBusy(false);
    if (!got.ok) {
      setSaid(got.words);
      return;
    }
    setUndone(true);
    setPicking(false);
    if (receipt.undo === "drop") {
      setDropped(true);
      setSaid("Undone. The reminder is dropped.");
      return;
    }
    if (receipt.was.fireAt) setFireMs(Date.parse(receipt.was.fireAt));
    if (receipt.was.title) setTitle(receipt.was.title);
    setSaid("Undone. The reminder is back as it was.");
  };

  const moveTo = async (iso: string, ms: number) => {
    if (busy) return;
    setBusy(true);
    setRefused("");
    const got = await moveReminder(receipt.id, { fireAt: iso, tz: zone });
    setBusy(false);
    if (!got.ok) {
      setRefused(got.words);
      return;
    }
    setFireMs(ms);
    setPicking(false);
    setUndone(true); // The old Undo no longer fits the new time.
    setSaid(`Moved to ${exactWhen(ms, zone, Date.now(), receiptZoneName(receipt, ms))}.`);
  };

  const saveTime = () => {
    const check = checkForm({ title: "", date, time, zone, nowMs: Date.now(), needTitle: false });
    if (!check.when) {
      setRefused(check.whenError ?? "This date or time is not valid.");
      return;
    }
    void moveTo(check.when.iso, check.when.ms);
  };

  const exact = exactWhen(fireMs, zone, now, receiptZoneName(receipt, fireMs));
  const summary = `${title} · ${exact}`;

  return (
    <RollupCard id={`tool:${id}`} title={label} icon="Bell" summary={summary} header="own">
      <div className={`rounded-lg border px-2.5 py-2 ${TONE} ${glassToneFrame(TONE)}`} data-reminder-receipt="">
        <RollupHeader />
        <RollupBody>
          <div className="mt-1 flex items-start gap-2">
            <span
              className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
              aria-hidden
            >
              <AppIcon name="Bell" size={14} />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-foreground" title={title} data-reminder-title="">
                {title || "Reminder"}
              </div>
              <div className={`text-xs ${dropped ? "text-muted-foreground line-through" : "text-foreground"}`} data-reminder-exact="">
                {exact}
              </div>
              <div className="text-[10px] text-muted-foreground" data-reminder-relative="">
                {dropped ? "Dropped" : relativeWhen(fireMs, now)} · private to its owner
              </div>
              {receipt.taskId && (
                <div className="mt-1">
                  <Button
                    variant="text"
                    size="none"
                    icon="ExternalLink"
                    onClick={() => openTask(receipt.taskId!)}
                    className="gap-1 text-[10px]"
                  >
                    Linked task
                  </Button>
                </div>
              )}
            </div>
          </div>

          {said && (
            <div role="status" className="mt-1.5 text-[11px] text-foreground" data-reminder-said="">
              {said}
            </div>
          )}

          {!dropped && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {undoable && (
                <Button variant="secondary" size="sm" icon="Undo2" onClick={() => void undo()} disabled={busy}>
                  Undo
                </Button>
              )}
              <Button
                variant="secondary"
                size="sm"
                icon="CalendarClock"
                aria-expanded={picking}
                onClick={() => setPicking((v) => !v)}
                disabled={busy}
              >
                Change time
              </Button>
              <Button
                variant="text"
                size="none"
                icon="ExternalLink"
                onClick={() => router.push(calendarReminderHref(receipt.id))}
                className="gap-1 text-[11px]"
              >
                See in Calendar
              </Button>
            </div>
          )}
          {dropped && (
            <div className="mt-2">
              <Button
                variant="text"
                size="none"
                icon="ExternalLink"
                onClick={() => router.push("/calendar")}
                className="gap-1 text-[11px]"
              >
                See in Calendar
              </Button>
            </div>
          )}

          {picking && !dropped && (
            <div className="mt-2 flex flex-col gap-2 rounded-lg border border-border bg-secondary/40 p-2" data-reminder-picker="">
              <div className="flex flex-wrap gap-1.5">
                {laterPresets(Date.now(), zone).map((p) => (
                  <Button key={p.label} variant="secondary" size="sm" disabled={busy} onClick={() => void moveTo(p.time.iso, p.time.ms)}>
                    {p.label}
                  </Button>
                ))}
              </div>
              <div role="group" aria-label="Pick a time" className="flex flex-wrap items-center gap-1.5">
                <Input
                  inputSize="sm"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  aria-label="Date"
                  className="min-w-0 flex-1"
                />
                <Input
                  inputSize="sm"
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  aria-label="Time"
                  className="min-w-0 flex-1"
                />
                <Button variant="primary" size="sm" onClick={saveTime} disabled={busy}>
                  Save
                </Button>
              </div>
              {refused && (
                <p role="alert" className="text-xs text-destructive">
                  {refused}
                </p>
              )}
              <p className="text-[10px] text-muted-foreground">Time zone: {zone.replace(/_/g, " ")}</p>
            </div>
          )}
        </RollupBody>
      </div>
    </RollupCard>
  );
}
