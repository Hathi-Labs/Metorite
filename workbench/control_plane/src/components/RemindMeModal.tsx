"use client";

/**
 * "Remind me" on a task (WS-66 RX-6, `calendar_events_reminders.md` §5.8).
 *
 * A due date is a day. A reminder is a moment. The task keeps its due date,
 * and this form sets a SEPARATE reminder at a date and a time, linked to the
 * task (`subject_kind: "task"`). It shows on the Calendar like any reminder,
 * and it makes no new task.
 *
 * One `Modal`, as the Calendar's New reminder form: a title (the task's title
 * by default), a date and a time (the next half hour), and the presets of
 * "Later" (1 hour, This evening, Tomorrow morning). The time is read in the
 * browser zone, as the Calendar reads it, and goes out as ISO with the offset
 * beside `tz`. A time at or before now is refused under its field. The save
 * calls `createReminder`, the one client of the create route. A refusal of the
 * server stays in the form, in words.
 *
 * Both hosts of the task body mount it (Projects and My Tasks), so it lives in
 * `components/`. It imports no Calendar app code: Tasks may not (D54).
 *
 * Fence (R7): `RemindMeModal.test.ts` beside this file.
 */
import { useId, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useToast } from "@/components/ui/Toast";
import {
  PAST_FIELD_WORDS,
  defaultWhen,
  exactWhen,
  gridZone,
  laterPresets,
  wallInputs,
  zonedToIso,
  type ZonedTime,
} from "@/lib/reminderClock";
import { REMINDER_TITLE_MAX, createReminder } from "@/lib/reminderWrite";

/** The checked time of the form, or the words under its field. Pure. */
export function checkRemindMe(a: {
  title: string;
  date: string;
  time: string;
  zone: string;
  nowMs: number;
}): { title: string; when?: ZonedTime; titleError?: string; whenError?: string } {
  const title = a.title.trim();
  const out: { title: string; when?: ZonedTime; titleError?: string; whenError?: string } = { title };
  if (!title) out.titleError = "Add a title for the reminder.";
  else if (title.length > REMINDER_TITLE_MAX) out.titleError = `Keep the title to ${REMINDER_TITLE_MAX} characters.`;
  const when = zonedToIso(a.date, a.time, a.zone);
  if (!a.date || !a.time) out.whenError = "Pick a date and a time.";
  else if (!when) out.whenError = "This date or time is not valid.";
  else if (when.ms <= a.nowMs) out.whenError = PAST_FIELD_WORDS;
  else out.when = when;
  return out;
}

export function RemindMeModal({
  taskId,
  taskTitle,
  onClose,
}: {
  taskId: string;
  taskTitle: string;
  onClose: () => void;
}) {
  const ids = useId();
  const toast = useToast();
  const zone = gridZone(Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [start] = useState(() => defaultWhen(Date.now(), zone));
  const [title, setTitle] = useState(() => taskTitle.slice(0, REMINDER_TITLE_MAX));
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [errors, setErrors] = useState<{ title?: string; when?: string }>({});
  const [refused, setRefused] = useState("");
  const [saving, setSaving] = useState(false);
  const lock = useRef(false);
  const titleRef = useRef<HTMLInputElement>(null);

  const pick = (t: ZonedTime) => {
    const w = wallInputs(t.ms, zone);
    setDate(w.date);
    setTime(w.time);
    setErrors({});
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (lock.current) return;
    const check = checkRemindMe({ title, date, time, zone, nowMs: Date.now() });
    setErrors({ title: check.titleError, when: check.whenError });
    setRefused("");
    if (check.titleError || !check.when) return;
    lock.current = true;
    setSaving(true);
    try {
      const got = await createReminder({
        title: check.title,
        kind: "action",
        fireAt: check.when.iso,
        tz: zone,
        subject: { kind: "task", id: taskId },
      });
      if (!got.ok) {
        setRefused(got.words);
        return;
      }
      toast.show({
        key: "task-reminder",
        variant: "success",
        title: `Reminder set for ${exactWhen(check.when.ms, zone)}`,
      });
      onClose();
    } finally {
      lock.current = false;
      setSaving(false);
    }
  };

  return (
    <Modal open onClose={onClose} size="sm" icon="Bell" title="Remind me" description={taskTitle} initialFocus={titleRef}>
      <form onSubmit={submit} noValidate className="flex min-h-0 flex-col gap-3 overflow-y-auto p-3">
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-title`} className="text-xs font-medium text-foreground">
            Title
          </label>
          <Input
            ref={titleRef}
            id={`${ids}-title`}
            inputSize="sm"
            value={title}
            maxLength={REMINDER_TITLE_MAX}
            onChange={(e) => setTitle(e.target.value)}
            aria-invalid={errors.title ? true : undefined}
            aria-describedby={errors.title ? `${ids}-title-err` : undefined}
          />
          {errors.title && (
            <p id={`${ids}-title-err`} className="text-xs text-destructive">
              {errors.title}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <span id={`${ids}-when`} className="text-xs font-medium text-foreground">
            When
          </span>
          <div className="flex flex-wrap gap-1.5">
            {laterPresets(Date.now(), zone).map((p) => (
              <Button key={p.label} type="button" variant="secondary" size="sm" onClick={() => pick(p.time)}>
                {p.label}
              </Button>
            ))}
          </div>
          <div role="group" aria-labelledby={`${ids}-when`} className="flex gap-2">
            <Input
              inputSize="sm"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              aria-label="Date"
              aria-invalid={errors.when ? true : undefined}
              aria-describedby={errors.when ? `${ids}-when-err` : undefined}
              className="min-w-0 flex-1"
            />
            <Input
              inputSize="sm"
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              aria-label="Time"
              aria-invalid={errors.when ? true : undefined}
              aria-describedby={errors.when ? `${ids}-when-err` : undefined}
              className="min-w-0 flex-1"
            />
          </div>
          {errors.when ? (
            <p id={`${ids}-when-err`} className="text-xs text-destructive">
              {errors.when}
            </p>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              Time zone: {zone.replace(/_/g, " ")}. The task keeps its due date. This is a separate reminder, linked to the task.
            </p>
          )}
        </div>

        {refused && (
          <p role="alert" className="text-xs text-destructive">
            {refused}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" size="sm" loading={saving}>
            Set reminder
          </Button>
        </div>
      </form>
    </Modal>
  );
}
