"use client";

/**
 * The picker of a sum (WS-53 CRM-Q32-9b, spec crm_platform.md §18.14.10).
 *
 * Some modules keep one amount in parts. The `amount` question then offers
 * "These fields add up to the amount", and this picker opens. The admin
 * ticks 2 to 5 fields with the shared `Checkbox`. After each tick, and a
 * short wait, the sum check (`GET /crm/questions/{id}/sum-check`) says how
 * many records the fields cover, and warns when two fields hold the same
 * money. The warning never blocks the save.
 *
 * The open card and "Your answers" open the same picker. `lib/questions.ts`
 * holds every word and rule. `SumPickerBody` takes props only, so a test
 * reads its markup. The engine has no name on screen.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import { useCachedResource } from "@/lib/useCachedResource";
import { useEffect, useState } from "react";

import * as api from "../lib/api";
import {
  CANCEL_LABEL,
  CHANGES_HEADING,
  SUM_CHECKING,
  SUM_CHECK_DELAY,
  SUM_DIALOG_TITLE,
  SUM_FIELDS_LABEL,
  SUM_SAVE,
  WHY_HEADING,
  recordsInLabel,
  sumBlocked,
  sumChanges,
  sumCheckKey,
  sumCheckView,
  sumFieldView,
  sumHint,
  sumQuestion,
  sumReady,
  sumStart,
  sumWhy,
  toggleSum,
  type Question,
  type SumCheck,
  type SumCheckView,
} from "../lib/questions";

/** The body of the picker: the question, the boxes, and what the check says. No hook. */
export function SumPickerBody({
  q,
  picked,
  check,
  disabled,
  onToggle,
}: {
  q: Question;
  picked: readonly string[];
  check: SumCheckView;
  disabled: boolean;
  onToggle: (key: string) => void;
}) {
  const records = recordsInLabel(q);
  return (
    <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <div className="space-y-1">
        <h3 className="break-words text-sm font-medium text-foreground">{sumQuestion(q)}</h3>
        {records ? <p className="text-[11px] text-muted-foreground">{records}</p> : null}
      </div>
      <dl className="grid gap-2 sm:grid-cols-2">
        <div className="min-w-0 space-y-0.5">
          <dt className="text-[11px] font-medium text-muted-foreground">{WHY_HEADING}</dt>
          <dd className="break-words text-xs text-foreground">{sumWhy(q)}</dd>
        </div>
        <div className="min-w-0 space-y-0.5">
          <dt className="text-[11px] font-medium text-muted-foreground">{CHANGES_HEADING}</dt>
          <dd className="break-words text-xs text-foreground">{sumChanges(q)}</dd>
        </div>
      </dl>
      <fieldset className="space-y-1.5">
        <legend className="mb-1.5 text-[11px] font-medium text-muted-foreground">{SUM_FIELDS_LABEL}</legend>
        <ul className="space-y-1.5" aria-label={SUM_FIELDS_LABEL}>
          {(q.sums ?? []).map((o) => {
            const view = sumFieldView(q, o);
            const on = picked.includes(o.value);
            const blocked = sumBlocked(picked, o.value);
            const id = `sum-${q.id}-${o.value}`;
            return (
              <li
                key={o.value}
                className={`flex min-w-0 items-start gap-2.5 rounded-md border px-3 py-2 ${
                  on ? "border-primary/40" : "border-border"
                }`}
              >
                <Checkbox
                  id={id}
                  checked={on}
                  disabled={disabled || blocked}
                  onChange={() => onToggle(o.value)}
                  className="mt-0.5"
                />
                <label htmlFor={id} className="flex min-w-0 flex-1 cursor-pointer flex-col gap-0.5">
                  <span className="break-words text-xs font-medium text-foreground">{view.label}</span>
                  {view.lines.map((line) => (
                    <span key={line} className="text-[11px] text-muted-foreground">
                      {line}
                    </span>
                  ))}
                  {view.examples ? (
                    <span className="min-w-0 break-words text-[11px] text-muted-foreground">{view.examples}</span>
                  ) : null}
                </label>
              </li>
            );
          })}
        </ul>
        <p className="text-[11px] text-muted-foreground">{sumHint(picked.length)}</p>
      </fieldset>
      <SumCheckLines check={check} />
    </div>
  );
}

/** The coverage line, and each warning. A warning says what to look at, and never blocks. */
function SumCheckLines({ check }: { check: SumCheckView }) {
  if (check.state === "idle") return null;
  if (check.state === "loading") {
    return (
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {SUM_CHECKING}
      </p>
    );
  }
  if (check.state === "failed") {
    return (
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {check.message}
      </p>
    );
  }
  return (
    <div className="space-y-1.5 rounded-md border border-border bg-secondary/40 px-3 py-2" aria-live="polite">
      <p className="text-xs text-foreground">{check.line}</p>
      {check.warnings.map((w) => (
        <p key={w} role="alert" className="flex items-start gap-1.5 text-xs text-warning">
          <Icon name="AlertTriangle" size={14} className="mt-px shrink-0" />
          <span className="min-w-0 break-words">{w}</span>
        </p>
      ))}
    </div>
  );
}

/**
 * The picker, in a `Modal`. The sum check is a read, so it goes through the
 * one read cache (`useCachedResource`), keyed by the ticked set, and it runs
 * `SUM_CHECK_DELAY` ms after the last tick. Save sends the list.
 */
export function SumDialog({
  q,
  busy,
  onSave,
  onClose,
}: {
  q: Question;
  busy: boolean;
  onSave: (keys: string[]) => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<string[]>(() => sumStart(q));
  const [checked, setChecked] = useState<string[]>(picked);
  useEffect(() => {
    const timer = setTimeout(() => setChecked(picked), SUM_CHECK_DELAY);
    return () => clearTimeout(timer);
  }, [picked]);
  const ready = sumReady(checked) && checked.join(",") === picked.join(",");
  const res = useCachedResource<SumCheck>(sumReady(checked) ? sumCheckKey(q.id, checked) : null, () =>
    api.sumCheck(q.id, checked),
  );
  const check = sumCheckView(ready, ready ? res.data : undefined, ready ? res.error : null);
  return (
    <Modal open onClose={onClose} title={SUM_DIALOG_TITLE} icon="Sigma" size="lg" closeLabel="Close">
      <SumPickerBody
        q={q}
        picked={picked}
        check={sumReady(picked) && !ready ? { state: "loading" } : check}
        disabled={busy}
        onToggle={(key) => setPicked((now) => toggleSum(q, now, key))}
      />
      <div className="flex flex-wrap justify-end gap-2 border-t border-border p-3">
        <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
          {CANCEL_LABEL}
        </Button>
        <Button
          variant="primary"
          size="sm"
          icon="Check"
          loading={busy}
          disabled={busy || !sumReady(picked)}
          onClick={() => onSave(picked)}
        >
          {SUM_SAVE}
        </Button>
      </div>
    </Modal>
  );
}
