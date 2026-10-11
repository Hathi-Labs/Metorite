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

import Icon from "@/components/Icon";
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
      />
      <div className="relative flex max-h-[85vh] w-full max-w-md flex-col rounded-xl border border-border bg-card shadow-xl">
        <header className="flex shrink-0 items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-base font-bold text-foreground">{form.title}</h2>
          <button
            onClick={onClose}
            className="rounded-lg border border-border p-2 text-muted-foreground hover:bg-secondary tech-transition"
          >
            <Icon name="X" className="w-4 h-4" />
          </button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto p-4">
          {form.fields.map((field) => (
            <label key={field.key} className="block">
              <span className="mb-1 block text-[10px] uppercase tracking-wide text-muted-foreground">
                {field.label}
                {field.required && <span className="text-destructive"> *</span>}
              </span>
              <input
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
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary/40 focus:outline-none"
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
          <button
            onClick={onClose}
            className="rounded-lg border border-border px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:border-primary/30 tech-transition"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={saving || missing.length > 0}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-40 tech-transition"
          >
            Create
          </button>
        </footer>
      </div>
    </div>
  );
}
