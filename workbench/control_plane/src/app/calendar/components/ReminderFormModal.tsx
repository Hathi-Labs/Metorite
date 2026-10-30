"use client";

/**
 * The "New reminder" form, and the "Move to…" form of a pin (WS-66 RM-5,
 * `calendar_events_reminders.md` §6.1, the reminder arm).
 *
 * One `Modal`, two modes. `new` asks for a title, a date, a time and a kind.
 * `move` asks for a date and a time only, and keeps the title. The form makes a
 * standalone reminder: it sends no subject, because the link picker waits for
 * CE-3. The full Task, Event and Reminder composer is CE-3 too.
 *
 * - **M8.** A refusal sits under its field, linked by `aria-describedby`.
 *   A time at or before now is refused.
 * - **M7.** The save shows its result. The parent toasts it, and a refusal of
 *   the server stays in the form.
 * - **M3.** One save at a time. The button waits for the answer.
 * - Esc and the close button save nothing.
 *
 * The parent mounts this form when it opens, so each open starts from the
 * props. The time is read on the wall of `zone`, and `checkForm` turns it into
 * an ISO string with the offset.
 */
import { useId, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import {
  TITLE_MAX,
  checkForm,
  defaultWhen,
  type ReminderKind,
  type ZonedTime,
} from "../lib/reminders";

const KINDS: { value: ReminderKind; label: string; hint: string }[] = [
  { value: "action", label: "Action", hint: "Asks you later if it is done." },
  { value: "notice", label: "Notice", hint: "Tells you once." },
];

export interface ReminderFormValue {
  title: string;
  kind: ReminderKind;
  when: ZonedTime;
}

export function ReminderFormModal({
  mode,
  zone,
  initial,
  heading,
  onSubmit,
  onClose,
}: {
  mode: "new" | "move";
  /** The wall clock of the date and the time fields. */
  zone: string;
  /** For `move`, the current date and time of the reminder. */
  initial?: { date: string; time: string };
  /** For `move`, the title that stays. */
  heading?: string;
  /** Resolves to the words of a refusal, or null when the save worked. */
  onSubmit: (value: ReminderFormValue) => Promise<string | null>;
  onClose: () => void;
}) {
  const ids = useId();
  const [start] = useState(() => initial ?? defaultWhen(Date.now(), zone));
  const [title, setTitle] = useState("");
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [kind, setKind] = useState<ReminderKind>("action");
  const [errors, setErrors] = useState<{ title?: string; when?: string }>({});
  const [refused, setRefused] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const dateRef = useRef<HTMLInputElement>(null);

  const isNew = mode === "new";
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saveLock.current) return;
    const check = checkForm({ title, date, time, zone, nowMs: Date.now(), needTitle: isNew });
    setErrors({ title: check.titleError, when: check.whenError });
    setRefused(null);
    if (check.titleError) {
      titleRef.current?.focus();
      return;
    }
    if (!check.when) {
      dateRef.current?.focus();
      return;
    }
    saveLock.current = true;
    setSaving(true);
    try {
      const words = await onSubmit({ title: check.title, kind, when: check.when });
      if (words) setRefused(words);
    } finally {
      saveLock.current = false;
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      size="sm"
      icon="Bell"
      title={isNew ? "New reminder" : "Move reminder"}
      description={isNew ? undefined : heading}
      initialFocus={isNew ? titleRef : dateRef}
    >
      <form onSubmit={submit} noValidate className="flex min-h-0 flex-col gap-3 overflow-y-auto p-3">
        {isNew && (
          <div className="flex flex-col gap-1">
            <label htmlFor={`${ids}-title`} className="text-xs font-medium text-foreground">
              Title
            </label>
            <Input
              ref={titleRef}
              id={`${ids}-title`}
              inputSize="sm"
              value={title}
              maxLength={TITLE_MAX}
              placeholder="What should I remind you of?"
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
        )}

        <div className="flex flex-col gap-1">
          <span id={`${ids}-when`} className="text-xs font-medium text-foreground">
            When
          </span>
          <div role="group" aria-labelledby={`${ids}-when`} className="flex gap-2">
            <Input
              ref={dateRef}
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
            <p className="text-[11px] text-muted-foreground">Time zone: {zone.replace(/_/g, " ")}</p>
          )}
        </div>

        {isNew && (
          <div className="flex flex-col gap-1">
            <span id={`${ids}-kind`} className="text-xs font-medium text-foreground">
              Kind
            </span>
            <div role="radiogroup" aria-labelledby={`${ids}-kind`} className="flex gap-2">
              {KINDS.map((k) => (
                <Button
                  key={k.value}
                  type="button"
                  role="radio"
                  aria-checked={kind === k.value}
                  variant="secondary"
                  size="sm"
                  selected={kind === k.value}
                  title={k.hint}
                  onClick={() => setKind(k.value)}
                >
                  {k.label}
                </Button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">
              {KINDS.find((k) => k.value === kind)?.hint}
            </p>
          </div>
        )}

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
            {isNew ? "Add reminder" : "Move"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
