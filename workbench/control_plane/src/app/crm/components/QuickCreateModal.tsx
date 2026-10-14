"use client";

/**
 * Quick-create — about six fields per entity (specs/crm_app.md §5 surface 5).
 *
 * Six, not thirty: capture in a CRM competes with a WhatsApp message, and a
 * form long enough to need scrolling is a form that gets skipped in favour of
 * one. Everything else is filled in on the record sheet, inline.
 *
 * `owner_email` is deliberately absent: the gateway defaults an ABSENT owner
 * to the person creating the record (B1), and an explicit null stays
 * unassigned. Offering a picker here would make the common case a decision.
 *
 * WS-53 CRM-C4b (`crm_platform.md` section 20.7): with the field studio on
 * (`studio` of the layout), the form also shows each visible custom field
 * through `FieldInput`, in section order, with its default filled in. A
 * required field blocks the save, except a boolean. The rules live in
 * `lib/recordForm.ts`.
 *
 * The 422 rule holds in every case: a refused save keeps the form open, with
 * the typed values. The message shows beside the field that it names, or at
 * the top of the form.
 */

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { firstField } from "../lib/focus";
import { useId, useRef, useState, type RefObject } from "react";
import type { FieldGroup } from "../lib/fields";
import { useCrmModeValue } from "../lib/modeContext";
import {
  createGroups,
  createSubmission,
  createValue,
  missingRequired,
  placeRefusal,
} from "../lib/recordForm";
import type { CrmRefusal } from "../lib/store";
import type { EntitySlug } from "../lib/types";
import { useFieldLayout } from "../lib/useCrmMode";
import FieldInput from "./FieldInput";

type FixedField = {
  key: string;
  label: string;
  placeholder?: string;
  kind?: "text" | "number" | "date";
  required?: boolean;
};

const FORMS: Record<EntitySlug, { title: string; fields: FixedField[] }> = {
  deals: {
    title: "New deal",
    fields: [
      { key: "name", label: "Deal name", required: true, placeholder: "Bosch — 2 printers" },
      { key: "amount", label: "Amount (₹)", kind: "number" },
      { key: "expected_close_date", label: "Expected close", kind: "date" },
      { key: "next_step", label: "Next step" },
      { key: "description", label: "Notes" },
    ],
  },
  leads: {
    title: "New lead",
    fields: [
      { key: "first_name", label: "First name" },
      { key: "last_name", label: "Last name" },
      { key: "organization_name", label: "Company" },
      { key: "email", label: "Email" },
      { key: "phone", label: "Phone" },
      { key: "lead_source", label: "Source", placeholder: "Referral, website, call…" },
    ],
  },
  contacts: {
    title: "New contact",
    fields: [
      { key: "first_name", label: "First name", required: true },
      { key: "last_name", label: "Last name" },
      { key: "title", label: "Title" },
      { key: "email", label: "Email" },
      { key: "phone", label: "Phone" },
      { key: "mobile", label: "Mobile" },
    ],
  },
  organizations: {
    title: "New company",
    fields: [
      { key: "name", label: "Name", required: true },
      { key: "website", label: "Website" },
      { key: "industry", label: "Industry" },
      { key: "phone", label: "Phone" },
      { key: "email", label: "Email" },
    ],
  },
};

type CreateProps = {
  entity: EntitySlug;
  saving: boolean;
  /** The refusal of the last save (CRM-C4b, the 422 rule), from the store. */
  refusal?: CrmRefusal | null;
  onClose: () => void;
  onCreate: (body: Record<string, unknown>) => void | Promise<void>;
};

export default function QuickCreateModal(props: CreateProps) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  return (
    // On `Modal` (CRM-U2): the scrim, the focus trap, Escape and the return
    // of focus to the "New" button come from the primitive. The page mounts
    // this only while it is open, so `open` is always true here.
    <Modal
      open
      onClose={props.onClose}
      title={FORMS[props.entity].title}
      icon="Plus"
      size="md"
      initialFocus={() => firstField(bodyRef.current)}
      className="max-h-[85vh]"
    >
      <QuickCreateForm {...props} bodyRef={bodyRef} />
    </Modal>
  );
}

/**
 * The body of the create form. It holds the state and the layout read, and
 * `QuickCreateModal` puts it in the `Modal`. Exported so a test draws it
 * without a DOM.
 */
export function QuickCreateForm({
  entity,
  saving,
  refusal = null,
  onClose,
  onCreate,
  bodyRef,
}: CreateProps & { bodyRef?: RefObject<HTMLDivElement | null> }) {
  const form = FORMS[entity];
  const { layout } = useFieldLayout(entity);
  // Mirror mode: Zoho owns the fields, so a custom field reads only until
  // CRM-C5 (the C4p lock).
  const locked = useCrmModeValue() === "mirror";
  const groups: FieldGroup[] = layout?.studio === true ? createGroups(layout) : [];
  const customDefs = groups.flatMap((g) => g.fields);

  const [values, setValues] = useState<Record<string, string>>({});
  /** The custom values that the member changed. The rest show their default. */
  const [edited, setEdited] = useState<Record<string, unknown>>({});
  /** The client check of the last save, by key. */
  const [checked, setChecked] = useState<Record<string, string>>({});
  /** A save went out, so a refusal of the store belongs to this form. */
  const [sent, setSent] = useState(false);
  /** The keys that the member changed after the refusal. Its message leaves them. */
  const [changedSince, setChangedSince] = useState<string[]>([]);

  const valueOf = (def: (typeof customDefs)[number]) => createValue(def, edited);
  const missing = [
    ...form.fields.filter((f) => f.required && !(values[f.key] ?? "").trim()).map((f) => f.label),
    ...(locked ? [] : missingRequired(customDefs, valueOf)),
  ];
  const keys = [...form.fields.map((f) => f.key), ...customDefs.map((d) => d.key)];
  const placed = sent && !saving ? placeRefusal(refusal, keys) : placeRefusal(null, keys);
  const serverError = (key: string) =>
    placed.field === key && !changedSince.includes(key) ? placed.message : null;

  function changed(key: string) {
    setChangedSince((list) => (list.includes(key) ? list : [...list, key]));
    setChecked((errors) => {
      if (!(key in errors)) return errors;
      const next = { ...errors };
      delete next[key];
      return next;
    });
  }

  function submit() {
    if (missing.length) return;
    const body: Record<string, unknown> = {};
    for (const field of form.fields) {
      const value = (values[field.key] ?? "").trim();
      // An untouched field is OMITTED rather than sent as null: omitting asks
      // for the column's default, and `null` on a defaulted NOT NULL column
      // is a 422 by design.
      if (!value) continue;
      body[field.key] =
        field.kind === "number" ? Number.parseFloat(value) : value;
    }
    const { custom, errors } = createSubmission(customDefs, edited, locked);
    setChecked(errors);
    if (Object.keys(errors).length > 0) return;
    if (custom) body.custom = custom;
    setSent(true);
    setChangedSince([]);
    void onCreate(body);
  }

  return (
    <div ref={bodyRef} className="contents">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {placed.top && (
          <p role="alert" className="text-[11px] text-destructive">
            {placed.top}
          </p>
        )}
        {form.fields.map((field) => {
          const error = serverError(field.key);
          return (
            <label key={field.key} className="block">
              <span className="mb-1 block text-[10px] uppercase tracking-wide text-muted-foreground">
                {field.label}
                {field.required && <span className="text-destructive"> *</span>}
              </span>
              <Input
                inputSize="sm"
                type={
                  field.kind === "date"
                    ? "date"
                    : field.kind === "number"
                      ? "number"
                      : "text"
                }
                value={values[field.key] ?? ""}
                placeholder={field.placeholder}
                aria-invalid={error ? true : undefined}
                onChange={(e) => {
                  setValues((v) => ({ ...v, [field.key]: e.target.value }));
                  if (sent) changed(field.key);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") submit();
                }}
                className="w-full"
              />
              {error && (
                <span role="alert" className="mt-1 block text-[11px] text-destructive">
                  {error}
                </span>
              )}
            </label>
          );
        })}
        {groups.length > 0 && (
          <CustomFieldGroups
            groups={groups}
            valueOf={valueOf}
            errorOf={(key) => checked[key] ?? serverError(key)}
            locked={locked}
            onChange={(key, next) => {
              setEdited((v) => ({ ...v, [key]: next }));
              changed(key);
            }}
          />
        )}
        {entity === "leads" && (
          <p className="text-[10px] text-muted-foreground">
            A lead needs nothing at all — its display name is derived from
            whatever you give it, falling back through company and email.
          </p>
        )}
      </div>

      <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button variant="secondary" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          loading={saving}
          disabled={saving || missing.length > 0}
          onClick={submit}
        >
          Create
        </Button>
      </footer>
    </div>
  );
}

/**
 * The custom fields of the create form, by section (CRM-C4b done-when 1).
 * Props only, so a test draws it. A section draws its label as a heading,
 * and the fields of no section come first with no heading.
 */
export function CustomFieldGroups({
  groups,
  valueOf,
  errorOf,
  locked = false,
  onChange,
}: {
  groups: FieldGroup[];
  valueOf: (def: FieldGroup["fields"][number]) => unknown;
  errorOf: (key: string) => string | null;
  locked?: boolean;
  onChange: (key: string, next: unknown) => void;
}) {
  return (
    <>
      {groups.map((group) => (
        <fieldset
          key={group.section?.id ?? "none"}
          className="space-y-3 border-t border-border pt-3"
        >
          {group.section && (
            <legend className="pr-2 text-[11px] font-medium text-foreground">
              {group.section.label}
            </legend>
          )}
          {group.fields.map((def) => (
            <CustomField
              key={def.key}
              def={def}
              value={valueOf(def)}
              error={errorOf(def.key)}
              locked={locked}
              onChange={(next) => onChange(def.key, next)}
            />
          ))}
        </fieldset>
      ))}
    </>
  );
}

function CustomField({
  def,
  value,
  error,
  locked,
  onChange,
}: {
  def: FieldGroup["fields"][number];
  value: unknown;
  error: string | null;
  locked: boolean;
  onChange: (next: unknown) => void;
}) {
  const id = useId();
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1 block text-[10px] uppercase tracking-wide text-muted-foreground"
      >
        {def.label}
        {def.required && def.type !== "boolean" && <span className="text-destructive"> *</span>}
      </label>
      <FieldInput id={id} def={def} value={value} onChange={onChange} error={error} locked={locked} />
    </div>
  );
}
