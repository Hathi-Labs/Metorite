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
 */

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import { useState } from "react";
import type { EntitySlug } from "../lib/types";

type FieldDef = {
  key: string;
  label: string;
  placeholder?: string;
  kind?: "text" | "number" | "date";
  required?: boolean;
};

const FORMS: Record<EntitySlug, { title: string; fields: FieldDef[] }> = {
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

export default function QuickCreateModal({
  entity,
  saving,
  onClose,
  onCreate,
}: {
  entity: EntitySlug;
  saving: boolean;
  onClose: () => void;
  onCreate: (body: Record<string, unknown>) => void;
}) {
  const form = FORMS[entity];
  const [values, setValues] = useState<Record<string, string>>({});

  const missing = form.fields
    .filter((f) => f.required && !(values[f.key] ?? "").trim())
    .map((f) => f.label);

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
    onCreate(body);
  }

  return (
    // On `Modal` (CRM-U2): the scrim, the focus trap, Escape and the return
    // of focus to the "New" button come from the primitive. The page mounts
    // this only while it is open, so `open` is always true here.
    <Modal
      open
      onClose={onClose}
      title={form.title}
      icon="Plus"
      size="md"
      className="max-h-[85vh]"
    >
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        {form.fields.map((field) => (
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
              onChange={(e) =>
                setValues((v) => ({ ...v, [field.key]: e.target.value }))
              }
              onKeyDown={(e) => {
                if (e.key === "Enter") submit();
              }}
              className="w-full"
            />
          </label>
        ))}
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
    </Modal>
  );
}
