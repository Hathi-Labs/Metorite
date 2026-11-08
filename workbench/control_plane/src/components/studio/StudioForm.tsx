"use client";

/**
 * The Form block (`ai_studio.md` §5.1, slice STU-D2a).
 *
 * One form from the 16 field types of §3.1. `FormView` draws it from props,
 * and `StudioForm` holds the state. A field checks on blur, with the same
 * rules as the server (`lib/studio/fieldCheck.ts`). A submit that fails the
 * check sets `aria-invalid` and `aria-describedby` on each bad control and
 * moves focus to the first. A submit that passes goes through `door.run`
 * once (`lib/studio/formModel.ts`). A refusal of the server shows under its
 * field. Controls are the house ones in `components/ui/`.
 */
import { useEffect, useId, useRef, useState } from "react";

import Button from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import Checkbox from "@/components/ui/Checkbox";
import { Input, Textarea } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import {
  type Draft, isLines, labelOf, manyUnsupported,
} from "@/lib/studio/blocks";
import { MAX_QR_CHARS, ratingRange } from "@/lib/studio/fieldCheck";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import {
  type Drafts, afterSend, blurProblem, checkForm, initialDrafts, sendRecord,
} from "@/lib/studio/formModel";

type Parts = Record<string, string>;

interface ControlProps {
  field: FieldSpec;
  draft: Draft;
  id: string;
  /** Set when the field is bad: the id of the sentence under it. */
  errorId?: string;
  onChange: (d: Draft) => void;
  onBlur: () => void;
}

const HINTS: Partial<Record<FieldSpec["type"], string>> = {
  phone: "+ then 8 to 15 digits, as +919876543210",
  person: "The email address of a member, in lower case",
  relation: "The id of the linked record",
  file: "The id of an uploaded file",
  money: "An amount and a currency code, as 1999.50 INR",
  geo: "Latitude from -90 to 90, longitude from -180 to 180",
  richText: "Markdown: # heading, **bold**, - list. HTML tags are not allowed",
  duration: "Hours, minutes and seconds, as 1h 30m, 45m or 1:30:00",
  percent: "A percent with up to 2 decimals, as 12.5",
};

/** The hint under a field. A rating and a barcode state their own rule. */
function hintOf(f: FieldSpec): string | undefined {
  if (f.type === "rating") {
    const [lo, hi] = ratingRange(f);
    return `A whole number from ${lo} to ${hi}`;
  }
  if (f.type === "barcode") {
    return f.format === "ean13" ? "13 digits, the last one a check digit"
      : f.format === "code128" ? "Printable ASCII characters, up to 80"
      : `Any text, up to ${MAX_QR_CHARS.toLocaleString("en-US")} characters`;
  }
  return HINTS[f.type];
}

function Control({ field, draft, id, errorId, onChange, onBlur }: ControlProps) {
  const bad = errorId ? { "aria-invalid": true as const, "aria-describedby": errorId } : {};
  const common = { id, onBlur, "aria-required": field.required || undefined, ...bad };
  if (manyUnsupported(field)) {
    return <p className="text-xs text-muted-foreground">This list of values has no editor yet.</p>;
  }
  if (isLines(field)) {
    return (
      <Textarea {...common} rows={3} value={String(draft)} placeholder="One value on each line"
        onChange={(e) => onChange(e.target.value)} />
    );
  }
  switch (field.type) {
    case "longText":
      return <Textarea {...common} rows={4} value={String(draft)} onChange={(e) => onChange(e.target.value)} />;
    case "checkbox":
      return (
        <span className="inline-flex size-6 items-center justify-center">
          <Checkbox id={id} onBlur={onBlur} aria-required={field.required || undefined} {...bad}
            checked={draft === true} onChange={(e) => onChange(e.target.checked)} />
        </span>
      );
    case "select":
      return (
        <SelectButton id={id} label={labelOf(field)} value={String(draft)} prompt="Choose one"
          widthClass="w-full" invalid={errorId !== undefined} describedBy={errorId}
          options={[
            ...(field.required ? [] : [{ value: "", label: "No choice" }]),
            ...(field.options ?? []).map((o) => ({ value: o, label: o })),
          ]}
          onChange={onChange} />
      );
    case "multiSelect": {
      const picked = Array.isArray(draft) ? draft : [];
      return (
        <div role="group" aria-label={labelOf(field)} aria-required={field.required || undefined}
          {...bad} className="flex flex-wrap gap-x-4 gap-y-1.5">
          {(field.options ?? []).map((o, i) => (
            <label key={o} className="inline-flex min-h-6 items-center gap-1.5 text-xs text-foreground">
              <Checkbox id={i === 0 ? id : undefined} onBlur={onBlur} checked={picked.includes(o)}
                aria-invalid={errorId ? true : undefined}
                onChange={(e) => onChange(e.target.checked ? [...picked, o] : picked.filter((x) => x !== o))} />
              {o}
            </label>
          ))}
        </div>
      );
    }
    case "money": {
      const p = (draft ?? {}) as Parts;
      return (
        <div className="flex gap-2">
          <Input {...common} inputMode="decimal" value={p.amount ?? ""} placeholder="0.00"
            aria-label={`${labelOf(field)} amount`} className="min-w-0 flex-1"
            onChange={(e) => onChange({ ...p, amount: e.target.value })} />
          <Input onBlur={onBlur} {...bad} value={p.currency ?? ""} maxLength={3} className="w-16 uppercase"
            aria-label={`${labelOf(field)} currency`}
            onChange={(e) => onChange({ ...p, currency: e.target.value })} />
        </div>
      );
    }
    case "richText":
      return <Textarea {...common} rows={5} value={String(draft)} onChange={(e) => onChange(e.target.value)} />;
    case "rating":
    case "percent":
      return (
        <Input {...common} inputMode={field.type === "percent" ? "decimal" : "numeric"} value={String(draft)}
          className={field.type === "rating" ? "w-24" : "w-32"} onChange={(e) => onChange(e.target.value)} />
      );
    case "duration":
      return (
        <Input {...common} value={String(draft)} placeholder="1h 30m" className="w-40"
          onChange={(e) => onChange(e.target.value)} />
      );
    case "barcode":
      return (
        <Input {...common} inputMode={field.format === "ean13" ? "numeric" : undefined} value={String(draft)}
          className="font-mono" onChange={(e) => onChange(e.target.value)} />
      );
    case "quantity": {
      const p = (draft ?? {}) as Parts;
      return (
        <div className="flex gap-2">
          <Input {...common} inputMode="decimal" value={p.value ?? ""} placeholder="0"
            aria-label={`${labelOf(field)} value`} className="min-w-0 flex-1"
            onChange={(e) => onChange({ ...p, value: e.target.value })} />
          <SelectButton label={`${labelOf(field)} unit`} value={p.unit ?? ""} prompt="Unit"
            widthClass="w-24" invalid={errorId !== undefined}
            options={(field.units ?? []).map((u) => ({ value: u, label: u }))}
            onChange={(unit) => onChange({ ...p, unit })} />
        </div>
      );
    }
    case "geo": {
      const p = (draft ?? {}) as Parts;
      return (
        <div className="flex gap-2">
          <Input {...common} inputMode="decimal" value={p.lat ?? ""} placeholder="Latitude"
            aria-label={`${labelOf(field)} latitude`} className="min-w-0 flex-1"
            onChange={(e) => onChange({ ...p, lat: e.target.value })} />
          <Input onBlur={onBlur} {...bad} inputMode="decimal" value={p.lng ?? ""} placeholder="Longitude"
            aria-label={`${labelOf(field)} longitude`} className="min-w-0 flex-1"
            onChange={(e) => onChange({ ...p, lng: e.target.value })} />
        </div>
      );
    }
    default: {
      const type = {
        number: "text", date: "date", datetime: "datetime-local", email: "email", url: "url", phone: "tel",
      }[field.type as string] ?? "text";
      return (
        <Input {...common} type={type} inputMode={field.type === "number" ? "decimal" : undefined}
          value={String(draft)} onChange={(e) => onChange(e.target.value)} />
      );
    }
  }
}

export interface FormViewProps {
  fields: readonly FieldSpec[];
  drafts: Drafts;
  errors: Record<string, string>;
  /** Makes the control ids. Two forms on one page need two. */
  formId: string;
  title?: string;
  submitLabel?: string;
  submitting?: boolean;
  /** The line under the button: a saved note, or a refusal with no field. */
  status?: { ok: boolean; text: string } | null;
  onDraft: (key: string, d: Draft) => void;
  onBlur: (key: string) => void;
  onSubmit: () => void;
}

export const controlId = (formId: string, key: string): string => `${formId}-${key}`;
export const errorIdOf = (formId: string, key: string): string => `${formId}-${key}-err`;

/** The form, drawn from props. No state, so a test can draw each case. */
export function FormView({
  fields, drafts, errors, formId, title, submitLabel = "Save", submitting = false, status,
  onDraft, onBlur, onSubmit,
}: FormViewProps) {
  return (
    <form noValidate aria-label={title ?? "Record form"} className="space-y-3"
      onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
      {title && <h2 className="text-sm font-semibold text-foreground">{title}</h2>}
      {fields.map((f) => {
        const id = controlId(formId, f.key);
        const message = errors[f.key];
        const errorId = message ? errorIdOf(formId, f.key) : undefined;
        const hint = hintOf(f);
        return (
          <div key={f.key} className="space-y-1" data-field={f.key} data-type={f.type}>
            <label htmlFor={id} className="block text-xs font-medium text-foreground">
              {labelOf(f)}
              {f.required && <span className="font-normal text-muted-foreground"> (required)</span>}
            </label>
            <Control field={f} draft={drafts[f.key]} id={id} errorId={errorId}
              onChange={(d) => onDraft(f.key, d)} onBlur={() => onBlur(f.key)} />
            {hint && !message && <p className="text-xs text-muted-foreground">{hint}</p>}
            {message && (
              <p id={errorId} role="alert" className="text-xs text-destructive">{message}</p>
            )}
          </div>
        );
      })}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" size="md" loading={submitting}>{submitLabel}</Button>
        {status && (
          <span role={status.ok ? "status" : "alert"}
            className={`text-xs ${status.ok ? "text-success" : "text-destructive"}`}>
            {status.text}
          </span>
        )}
      </div>
    </form>
  );
}

export interface StudioFormProps {
  fields: readonly FieldSpec[];
  door: StudioDataDoor;
  /** The typed action that saves: `object:<name>.create` or `.update`. */
  action: string;
  /** The stored record of an edit. Omit it for a new record. */
  initial?: Record<string, unknown>;
  /** Sent with the record, as `{ id }` for an update. */
  extraArgs?: Record<string, unknown>;
  title?: string;
  submitLabel?: string;
  /** The toast sentence after a save that worked. */
  savedMessage?: string;
  /** Called after the door says ok. */
  onSaved?: () => void;
}

export function StudioForm({
  fields, door, action, initial, extraArgs, title, submitLabel, savedMessage = "Saved", onSaved,
}: StudioFormProps) {
  const toast = useToast();
  const formId = useId().replace(/:/g, "");
  const [drafts, setDrafts] = useState<Drafts>(() => initialDrafts(fields, initial));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  // The ref blocks a second press before the state commits (M3).
  const busy = useRef(false);
  // The key to focus once the errors are on screen.
  const [focusKey, setFocusKey] = useState<string | null>(null);

  useEffect(() => {
    if (!focusKey) return;
    document.getElementById(controlId(formId, focusKey))?.focus();
    setFocusKey(null);
  }, [focusKey, formId]);

  const setError = (key: string, message: string | null) =>
    setErrors((prev) => {
      const next = { ...prev };
      if (message) next[key] = message;
      else delete next[key];
      return next;
    });

  const submit = async () => {
    const check = checkForm(fields, drafts, initial);
    if (!check.ok) {
      setErrors(check.errors);
      setStatus(null);
      setFocusKey(check.first);
      return;
    }
    setErrors({});
    setStatus(null);
    setSubmitting(true);
    const out = await sendRecord(door, action, check.value, busy, extraArgs);
    setSubmitting(false);
    if (out.kind === "busy") return;
    const next = afterSend(fields, drafts, out, { savedMessage, reset: initial === undefined });
    setDrafts(next.drafts);
    setErrors(next.errors);
    setStatus(next.status);
    setFocusKey(next.focus);
    if (next.toast) {
      toast.show({ variant: "success", title: next.toast });
      onSaved?.();
    }
  };

  return (
    <FormView fields={fields} drafts={drafts} errors={errors} formId={formId} title={title}
      submitLabel={submitLabel} submitting={submitting} status={status}
      onDraft={(key, d) => {
        setDrafts((prev) => ({ ...prev, [key]: d }));
        // A field that shows an error checks again as the member fixes it.
        if (errors[key]) setError(key, blurProblem(fields, { ...drafts, [key]: d }, key));
      }}
      onBlur={(key) => setError(key, blurProblem(fields, drafts, key))}
      onSubmit={() => { void submit(); }} />
  );
}

export default StudioForm;
