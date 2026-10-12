"use client";

/**
 * The fields of the record page, with inline edit
 * (specs/crm_app.md §5 surface 3).
 *
 * Since WS-53 CRM-X4 (`crm_platform.md` section 19.8) the page draws it in
 * three parts, one for each tab:
 *
 * - `overview`: the lead's Convert and the fixed fields. The status pill
 *   moved to the record header, because it is one control (`StatusPill`).
 * - `related`: the deal's people with their roles, and the related lists.
 * - `fields`: the custom fields of `crm_field_defs`.
 *
 * Inline edit rather than an edit mode: a CRM record is corrected constantly
 * — a phone number, a close date, a next step — and a form that has to be
 * opened and saved is a form nobody opens.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useState } from "react";
import { isMirrored, LOCK_TIP } from "../lib/connect";
import { contactValue, fieldRows, type FieldDef } from "../lib/fields";
import { money, shortDate } from "../lib/format";
import FieldValue from "./FieldValue";
import RelatedLists from "./RelatedLists";
import { NO_RELATED, type RelatedLists as Related } from "../lib/related";
import type { DealContact, EntitySlug, Status } from "../lib/types";

type Row = Record<string, unknown>;

type Field = {
  key: string;
  label: string;
  kind?: "text" | "number" | "date";
  /**
   * How it reads when it is not being edited.
   *
   * Takes the record's own STATUS as well as the row, because one field —
   * `probability` — means something different depending on it: a NULL is not
   * "no forecast", it is "this deal has not stated one and inherits its
   * stage's" (§5.1). Rendering that as "—" hides the number the whole
   * weighted pipeline is computed from.
   */
  show?: (row: Row, status: Status | null) => string;
  /**
   * The value is an email or a phone, so it reads as a `mailto:` or a `tel:`
   * link (CRM-X4 item 6). The rules are those of `contactValue`.
   */
  link?: "email" | "phone";
};

const FIELDS: Record<EntitySlug, Field[]> = {
  deals: [
    { key: "name", label: "Name" },
    {
      key: "amount",
      label: "Amount",
      kind: "number",
      show: (r) => money(r.amount as number | null, (r.currency as string) || "INR"),
    },
    {
      key: "probability",
      label: "Probability",
      kind: "number",
      show: (r, status) => {
        const stated = r.probability as number | null | undefined;
        if (stated !== null && stated !== undefined) return `${stated}%`;
        const inherited = status?.probability;
        return inherited === null || inherited === undefined
          ? "—"
          : `${inherited}% (from ${status?.name ?? "the stage"})`;
      },
    },
    {
      key: "expected_close_date",
      label: "Expected close",
      kind: "date",
      show: (r) => shortDate(r.expected_close_date as string | null),
    },
    { key: "next_step", label: "Next step" },
    { key: "owner_email", label: "Owner" },
    { key: "description", label: "Notes" },
  ],
  leads: [
    { key: "lead_name", label: "Name" },
    { key: "first_name", label: "First name" },
    { key: "last_name", label: "Last name" },
    { key: "organization_name", label: "Company" },
    { key: "email", label: "Email", link: "email" },
    { key: "phone", label: "Phone", link: "phone" },
    { key: "lead_source", label: "Source" },
    { key: "owner_email", label: "Owner" },
    { key: "description", label: "Notes" },
  ],
  contacts: [
    { key: "first_name", label: "First name" },
    { key: "last_name", label: "Last name" },
    { key: "title", label: "Title" },
    { key: "email", label: "Email", link: "email" },
    { key: "phone", label: "Phone", link: "phone" },
    { key: "mobile", label: "Mobile", link: "phone" },
    { key: "owner_email", label: "Owner" },
    { key: "description", label: "Notes" },
  ],
  organizations: [
    { key: "name", label: "Name" },
    { key: "website", label: "Website" },
    { key: "industry", label: "Industry" },
    { key: "phone", label: "Phone", link: "phone" },
    { key: "email", label: "Email", link: "email" },
    { key: "owner_email", label: "Owner" },
    { key: "description", label: "Notes" },
  ],
};

/** The part of the panel that one tab of the record page draws. */
export type FieldsPart = "overview" | "related" | "fields";

export default function FieldsPanel({
  part = "overview",
  entity,
  record,
  statuses,
  dealContacts,
  saving,
  onPatch,
  onConvert,
  onSetPrimary,
  onDetach,
  onOpenContact,
  related = NO_RELATED,
  onOpenRelated,
  onRetryRelated,
  fieldDefs = null,
}: {
  /** Which tab draws the panel. Overview when it is not given. */
  part?: FieldsPart;
  entity: EntitySlug;
  record: Row;
  /** The record's own status reads the inherited probability of a deal. */
  statuses: Status[];
  dealContacts: DealContact[];
  saving: boolean;
  onPatch: (body: Record<string, unknown>) => void;
  onConvert: () => void;
  onSetPrimary: (contactId: string) => void;
  onDetach: (contactId: string) => void;
  onOpenContact: (contactId: string) => void;
  /** The related lists of a company or a contact (CRM-Q3). */
  related?: Related;
  onOpenRelated?: (entity: EntitySlug, id: string) => void;
  onRetryRelated?: () => void;
  /** What each custom field means (CRM-Q4). `null` falls back to the humanised keys. */
  fieldDefs?: FieldDef[] | null;
}) {
  const status =
    statuses.find((s) => s.id === record.status_id) ?? null;
  const converted = Boolean(record.converted_deal_id);
  // A record that Zoho owns (CRM-Z7, spec §3.3): its fields, its stage and its
  // people are read-only here. Notes and calls stay open, in the timeline.
  const mirrored = isMirrored(record);

  if (part === "related") {
    return (
      <div className="flex flex-col">
        {entity === "deals" && (
          <section className="border-b border-border px-4 py-3">
            <h3 className="mb-2 text-[10px] uppercase tracking-wide text-muted-foreground">
              Contacts
            </h3>
            {dealContacts.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                No contacts on this deal.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {dealContacts.map(({ contact, role, is_primary }) => (
                  <li key={contact.id} className="flex items-center gap-2">
                    <button
                      onClick={() => onOpenContact(contact.id)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <span className="block truncate text-xs text-foreground">
                        {contact.first_name} {contact.last_name ?? ""}
                      </span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {role || contact.title || contact.email || "—"}
                      </span>
                    </button>
                    {!mirrored && (
                    <>
                    <button
                      onClick={() => onSetPrimary(contact.id)}
                      title={
                        is_primary
                          ? "Primary contact"
                          : "Make primary — the current primary is demoted"
                      }
                      className={`p-1 rounded tech-transition ${
                        is_primary
                          ? "text-warning"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      <Icon name="Star"
                        className="w-3.5 h-3.5"
                        fill={is_primary ? "currentColor" : "none"}
                      />
                    </button>
                    <button
                      onClick={() => onDetach(contact.id)}
                      title="Remove from this deal (the contact record is kept)"
                      className="p-1 rounded text-muted-foreground hover:text-destructive tech-transition"
                    >
                      <Icon name="UserMinus" className="w-3.5 h-3.5" />
                    </button>
                    </>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {onOpenRelated && (
          <RelatedLists
            entity={entity}
            related={related}
            onOpen={onOpenRelated}
            onRetry={onRetryRelated}
          />
        )}
      </div>
    );
  }

  if (part === "fields") {
    const custom = fieldRows(record, fieldDefs);
    return custom.length === 0 ? (
      <p className="px-4 py-8 text-center text-xs text-muted-foreground">
        No custom fields on this record.
      </p>
    ) : (
      <dl className="divide-y divide-border/60">
        {custom.map((row) => (
          <div key={row.key} className="flex items-start gap-3 px-4 py-2">
            <dt className="w-28 shrink-0 pt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
              {row.label}
            </dt>
            <dd className="min-w-0 flex-1 break-words text-xs text-foreground">
              <FieldValue value={row.value} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return (
    <div className="flex flex-col">
      {entity === "leads" && !mirrored && (
        <div className="flex items-center justify-end gap-2 border-b border-border px-4 py-3">
          {converted ? (
            <span className="rounded-full bg-success/10 px-2 py-0.5 text-[10px] text-success">
              converted
            </span>
          ) : (
            <Button variant="primary" size="sm" onClick={onConvert}>
              Convert
            </Button>
          )}
        </div>
      )}

      <dl className="divide-y divide-border/60">
        {FIELDS[entity].map((field) => (
          <EditableField
            key={field.key}
            field={field}
            row={record}
            status={status}
            saving={saving}
            locked={mirrored}
            onPatch={onPatch}
          />
        ))}
      </dl>
    </div>
  );
}

function EditableField({
  field,
  row,
  status,
  saving,
  locked,
  onPatch,
}: {
  field: Field;
  row: Row;
  status: Status | null;
  saving: boolean;
  /** A mirrored record: show the value, with a lock and no edit. */
  locked: boolean;
  onPatch: (body: Record<string, unknown>) => void;
}) {
  const raw = row[field.key];
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");

  function begin() {
    setValue(raw === null || raw === undefined ? "" : String(raw));
    setEditing(true);
  }

  function commit() {
    setEditing(false);
    const next = value.trim();
    const before = raw === null || raw === undefined ? "" : String(raw);
    if (next === before) return;
    // An emptied field clears to null — the gateway keeps an explicit null as
    // "clear this" and refuses it only where the column is NOT NULL with a
    // default, which is a 422 the panel surfaces rather than swallows.
    onPatch({
      [field.key]:
        next === ""
          ? null
          : field.kind === "number"
            ? Number.parseFloat(next)
            : next,
    });
  }

  // An email or a phone that passes its guard reads as its link. Every other
  // value reads as text, as before.
  const linked = field.link ? contactValue(field.link, raw) : null;
  const link = linked && (linked.kind === "mailto" || linked.kind === "tel") ? linked : null;
  const shown = link ? (
    <FieldValue value={link} />
  ) : field.show ? (
    field.show(row, status)
  ) : raw === null || raw === undefined || raw === "" ? (
    "—"
  ) : (
    String(raw)
  );

  return (
    <div className="flex items-start gap-3 px-4 py-2">
      <dt className="w-28 shrink-0 pt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
        {field.label}
      </dt>
      <dd className="min-w-0 flex-1">
        {locked ? (
          <span className="flex items-start gap-1.5 text-xs text-foreground" title={LOCK_TIP}>
            <span className="min-w-0 flex-1 break-words">{shown}</span>
            <Icon
              name="Lock"
              className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground"
              aria-label={LOCK_TIP}
            />
          </span>
        ) : editing ? (
          <div className="flex items-center gap-1">
            {/* The shared primitive, never a raw field (design rule 3, CRM-X4). */}
            <Input
              autoFocus
              inputSize="sm"
              aria-label={field.label}
              type={field.kind === "date" ? "date" : field.kind === "number" ? "number" : "text"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") commit();
                if (e.key === "Escape") setEditing(false);
              }}
              className="min-w-0 flex-1"
            />
            <button
              onClick={commit}
              disabled={saving}
              className="p-1 rounded text-muted-foreground hover:text-success tech-transition"
              aria-label="Save"
            >
              <Icon name="Check" className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setEditing(false)}
              className="p-1 rounded text-muted-foreground hover:text-foreground tech-transition"
              aria-label="Cancel"
            >
              <Icon name="X" className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : link ? (
          // The link and the edit button sit side by side. A link inside the
          // button that starts the edit would be two controls in one.
          <span className="flex items-start gap-1.5 text-xs text-foreground">
            <span className="min-w-0 flex-1 break-words">{shown}</span>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="Pencil"
              aria-label={`Edit ${field.label.toLowerCase()}`}
              onClick={begin}
            />
          </span>
        ) : (
          <button
            onClick={begin}
            className="w-full break-words text-left text-xs text-foreground hover:text-primary tech-transition"
          >
            {shown}
          </button>
        )}
      </dd>
    </div>
  );
}
