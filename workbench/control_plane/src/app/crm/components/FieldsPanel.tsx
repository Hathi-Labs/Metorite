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
 * - `fields`: the custom fields of `crm_field_defs`. With the field studio
 *   on (CRM-C4b, `crm_platform.md` section 20.7 item 12), it draws the
 *   layout instead: each section in order, with its system and custom
 *   fields. A visible field with no value reads "—", and a member edits it
 *   there through `FieldInput`. A Mirror record shows each field locked.
 *
 * Inline edit rather than an edit mode: a CRM record is corrected constantly
 * — a phone number, a close date, a next step — and a form that has to be
 * opened and saved is a form nobody opens.
 *
 * The fill rank (WS-53 CRM-Q17a-2, `crm_platform.md` section 18.9.6). While
 * the gateway sends fill numbers (`fillRankOn`):
 *
 * - Overview draws the filled fixed fields first, by the fill of each field.
 *   The empty ones follow.
 * - The Fields tab keeps its sections, with a filter box on top. The empty
 *   fields fold behind "Show n empty fields".
 *
 * With no fill numbers, each part draws as before. In every case the Zoho
 * system keys (`ZOHO_SYSTEM_KEYS`) and `_owner_name` never show as a row, and
 * an owner who is not a member shows by the Zoho name, with "Zoho only".
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useEffect, useId, useRef, useState } from "react";
import { isMirrored, LOCK_TIP } from "../lib/connect";
import { formValue, type FieldLayout } from "../lib/fieldLayout";
import {
  contactValue,
  fieldRows,
  fillRankOn,
  filledFirst,
  foldFields,
  foldLabel,
  hiddenOnRecordPage,
  layoutGroups,
  undefinedRows,
  zohoOwnerName,
  zohoOwnerText,
  type FieldDef,
  type FieldRow,
  type FieldValue as Value,
} from "../lib/fields";
import { money, shortDate } from "../lib/format";
import {
  editKey,
  isReference,
  layoutValue,
  referenceValue,
  saveEdit,
  storedValue,
} from "../lib/recordForm";
import type { CrmRefusal } from "../lib/store";
import FieldInput from "./FieldInput";
import FieldValue from "./FieldValue";
import RelatedLists from "./RelatedLists";
import { NO_RELATED, type RelatedLists as Related } from "../lib/related";
import type { DealContact, EntitySlug, Status } from "../lib/types";

type Row = Record<string, unknown>;

export type Field = {
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

/**
 * The owner as Overview reads it: the member's email, else the Zoho name of
 * an owner who is not a member, with "Zoho only" (CRM-Q17a-2 item 4).
 */
function ownerShow(row: Row): string {
  const zoho = zohoOwnerName(row);
  if (zoho) return zohoOwnerText(zoho);
  const email = row.owner_email;
  return email === null || email === undefined || email === "" ? "—" : String(email);
}

/** The owner of the layout's Fields tab, when Zoho names one who is not a member. */
function zohoOwnerValue(def: FieldDef, record: Row): Value | null {
  if (!def.is_system || def.key !== "owner_email") return null;
  const zoho = zohoOwnerName(record);
  return zoho ? { kind: "text", text: zohoOwnerText(zoho) } : null;
}

/** A fixed field holds a value on this record. */
function fixedFilled(field: Field, row: Row): boolean {
  const raw = row[field.key];
  if (raw !== null && raw !== undefined && raw !== "") return true;
  return field.key === "owner_email" && zohoOwnerName(row) !== null;
}

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
    { key: "owner_email", label: "Owner", show: ownerShow },
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
    { key: "owner_email", label: "Owner", show: ownerShow },
    { key: "description", label: "Notes" },
  ],
  contacts: [
    { key: "first_name", label: "First name" },
    { key: "last_name", label: "Last name" },
    { key: "title", label: "Title" },
    { key: "email", label: "Email", link: "email" },
    { key: "phone", label: "Phone", link: "phone" },
    { key: "mobile", label: "Mobile", link: "phone" },
    { key: "owner_email", label: "Owner", show: ownerShow },
    { key: "description", label: "Notes" },
  ],
  organizations: [
    { key: "name", label: "Name" },
    { key: "website", label: "Website" },
    { key: "industry", label: "Industry" },
    { key: "phone", label: "Phone", link: "phone" },
    { key: "email", label: "Email", link: "email" },
    { key: "owner_email", label: "Owner", show: ownerShow },
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
  layout = null,
  refusal = null,
  companyName = null,
}: {
  /** Which tab draws the panel. Overview when it is not given. */
  part?: FieldsPart;
  entity: EntitySlug;
  record: Row;
  /** The record's own status reads the inherited probability of a deal. */
  statuses: Status[];
  dealContacts: DealContact[];
  saving: boolean;
  /**
   * Save an edit. The page gives the result of `store.patchRecord`: `false`
   * for a refusal, and then the edit of the Fields tab stays open (CRM-C4b).
   */
  onPatch: (body: Record<string, unknown>) => void | Promise<boolean>;
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
  /**
   * The layout of the entity (CRM-C4b). With `studio` true, the Fields tab
   * draws it by section. `null` or `studio` false draws the tab of today.
   */
  layout?: FieldLayout | null;
  /** The refusal of the last edit, from the store (the 422 rule). */
  refusal?: CrmRefusal | null;
  /** The name of a contact's company, which the contact row does not carry. */
  companyName?: string | null;
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

  if (part === "fields" && layout?.studio === true) {
    return (
      <LayoutFields
        tools={fillRankOn(layout.fields)}
        layout={layout}
        record={record}
        statuses={statuses}
        companyName={companyName}
        saving={saving}
        refusal={refusal}
        locked={mirrored}
        onPatch={onPatch}
      />
    );
  }

  if (part === "fields") {
    const custom = fieldRows(record, fieldDefs);
    // CRM-Q17a-2 item 5: the filter box and the fold, while the fill rank is on.
    if (fillRankOn(fieldDefs) || fillRankOn(layout?.fields)) {
      return (
        <FoldedFields
          rows={custom}
          emptyText="No custom fields on this record."
          renderRows={(rows) => (
            <dl className="divide-y divide-border/60">
              {rows.map((row) => (
                <div key={row.key} className="flex items-start gap-3 px-4 py-2">
                  <dt className={LABEL}>{row.label}</dt>
                  <dd className="min-w-0 flex-1 break-words text-xs text-foreground">
                    <FieldValue value={row.value} />
                  </dd>
                </div>
              ))}
            </dl>
          )}
        />
      );
    }
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
        {overviewFields(entity, record, layout).map((field) => (
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

/**
 * The fixed fields of Overview, in the order they draw (CRM-Q17a-2 item 5).
 * While the layout carries fill numbers, the filled fields come first, by
 * the fill of each field, and the empty ones follow. Else the order of today.
 */
export function overviewFields(
  entity: EntitySlug,
  record: Row,
  layout: FieldLayout | null,
): Field[] {
  const defs = layout?.fields ?? null;
  if (!fillRankOn(defs)) return FIELDS[entity];
  const system = new Map((defs ?? []).filter((d) => d.is_system).map((d) => [d.key, d]));
  return filledFirst(
    FIELDS[entity],
    (field) => fixedFilled(field, record),
    (field) => system.get(field.key),
  );
}

// ── The filter box and the fold of the Fields tab (CRM-Q17a-2 item 5) ──────

/** The filter box of the Fields tab. */
export function FieldFilterBox({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="border-b border-border/60 px-4 py-2">
      <Input
        icon="Search"
        inputSize="sm"
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Filter fields"
        aria-label="Filter fields"
      />
    </div>
  );
}

/** "Show n empty fields", and "Hide empty fields" while they show. */
export function EmptyFold({
  empty,
  open,
  onToggle,
}: {
  empty: number;
  open: boolean;
  onToggle: () => void;
}) {
  if (empty === 0) return null;
  return (
    <div className="border-t border-border/60 px-4 py-2">
      <Button
        variant="ghost"
        size="sm"
        icon={open ? "ChevronUp" : "ChevronDown"}
        aria-expanded={open}
        onClick={onToggle}
      >
        {foldLabel(empty, open)}
      </Button>
    </div>
  );
}

/** The line in place of the rows when the filter or the fold leaves none. */
function noRowsText(query: string, folded: number, emptyText: string): string {
  if (query.trim()) return "No field matches the filter.";
  if (folded > 0) return "Each field of this record is empty.";
  return emptyText;
}

/**
 * The rows of a Fields tab with the filter box and the fold. The record page
 * of a generic module draws its Fields tab with it too, so both tabs act
 * the same. `renderRows` draws the rows that show.
 */
export function FoldedFields<R extends FieldRow>({
  rows,
  emptyText,
  renderRows,
}: {
  rows: R[];
  emptyText: string;
  renderRows: (rows: R[]) => React.ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [showEmpty, setShowEmpty] = useState(false);
  const fold = foldFields(rows, query, showEmpty, (row) => row.value.kind === "empty");
  return (
    <div className="flex flex-col">
      <FieldFilterBox value={query} onChange={setQuery} />
      {fold.shown.length > 0 ? (
        renderRows(fold.shown)
      ) : (
        <p className="px-4 py-8 text-center text-xs text-muted-foreground">
          {noRowsText(query, fold.folded, emptyText)}
        </p>
      )}
      <EmptyFold empty={fold.empty} open={showEmpty} onToggle={() => setShowEmpty((v) => !v)} />
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

// ── The Fields tab by layout (WS-53 CRM-C4b) ────────────────────────────────

/** A label cell of the Fields tab. The same look as the rows of today. */
const LABEL = "w-28 shrink-0 pt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground";

/**
 * The Fields tab when the layout says `studio` (section 20.7 item 12). Each
 * section in order, with the system and the custom fields in it. A custom
 * key that no definition describes follows, read only, as before.
 */
export function LayoutFields({
  tools = false,
  layout,
  record,
  statuses,
  companyName,
  saving,
  refusal,
  locked,
  onPatch,
}: {
  /**
   * CRM-Q17a-2 item 5: the filter box and the fold. The page turns it on
   * while the layout carries fill numbers. Off, the tab draws as before.
   */
  tools?: boolean;
  layout: FieldLayout;
  record: Row;
  statuses: Status[];
  companyName: string | null;
  saving: boolean;
  refusal: CrmRefusal | null;
  /** A Mirror record: each field reads only, with the lock (CRM-C5 opens it). */
  locked: boolean;
  onPatch: (body: Record<string, unknown>) => void | Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const [showEmpty, setShowEmpty] = useState(false);
  // CRM-Q17a-2 item 6: no Zoho system key is a row.
  const all = layoutGroups(layout, (d) => !hiddenOnRecordPage(d.key));
  /** What a field shows: a reference by name, the Zoho owner, or its value. */
  const valueOf = (def: FieldDef): Value | null =>
    isReference(def)
      ? referenceValue(def, record, statuses, companyName)
      : (zohoOwnerValue(def, record) ?? layoutValue(def, record, statuses));
  // With `tools` off, nothing folds and nothing filters: the tab of before.
  const folds = all.map((group) => {
    const rows = group.fields
      .map((def) => ({ key: def.key, label: def.label, def, value: valueOf(def) }))
      .filter((row) => !tools || row.value !== null);
    return {
      section: group.section,
      fold: foldFields(rows, tools ? query : "", !tools || showEmpty, (row) => row.value?.kind === "empty"),
    };
  });
  const groups = folds
    .map(({ section, fold }) => ({ section, fields: fold.shown.map((row) => row.def) }))
    .filter((group) => !tools || group.fields.length > 0);
  const undefinedFold = foldFields(
    undefinedRows(record, layout.fields),
    tools ? query : "",
    !tools || showEmpty,
    (row) => row.value.kind === "empty",
  );
  const extra = undefinedFold.shown;
  const emptyCount = folds.reduce((sum, { fold }) => sum + fold.empty, undefinedFold.empty);
  const foldedCount = folds.reduce((sum, { fold }) => sum + fold.folded, undefinedFold.folded);
  const nothing = tools && groups.length === 0 && extra.length === 0;
  return (
    <div className="flex flex-col">
      {tools && <FieldFilterBox value={query} onChange={setQuery} />}
      {nothing && (
        <p className="px-4 py-8 text-center text-xs text-muted-foreground">
          {noRowsText(query, foldedCount, "No fields on this record.")}
        </p>
      )}
      {groups.map((group) => (
        <section
          key={group.section?.id ?? "none"}
          aria-label={group.section?.label}
          className="border-b border-border/60 last:border-b-0"
        >
          {group.section && (
            <h3 className="px-4 pb-1 pt-3 text-[11px] font-medium text-foreground">
              {group.section.label}
            </h3>
          )}
          <dl className="divide-y divide-border/60">
            {group.fields.map((def) => {
              if (isReference(def)) {
                const value = referenceValue(def, record, statuses, companyName);
                return value === null ? null : (
                  <div key={def.key} className="flex items-start gap-3 px-4 py-2">
                    <dt className={LABEL}>{def.label}</dt>
                    <dd className="min-w-0 flex-1 break-words text-xs text-foreground">
                      <FieldValue value={value} />
                    </dd>
                  </div>
                );
              }
              return (
                <LayoutFieldRow
                  key={def.key}
                  def={def}
                  record={record}
                  statuses={statuses}
                  saving={saving}
                  refusal={refusal}
                  locked={locked}
                  onPatch={onPatch}
                />
              );
            })}
          </dl>
        </section>
      ))}
      {extra.length > 0 && (
        <dl className="divide-y divide-border/60 border-t border-border/60">
          {extra.map((row) => (
            <div key={row.key} className="flex items-start gap-3 px-4 py-2">
              <dt className={LABEL}>{row.label}</dt>
              <dd className="min-w-0 flex-1 break-words text-xs text-foreground">
                <FieldValue value={row.value} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {tools && (
        <EmptyFold empty={emptyCount} open={showEmpty} onToggle={() => setShowEmpty((v) => !v)} />
      )}
    </div>
  );
}

/** Focus the control of an inline edit: the input, or the first one in a group. */
function focusControl(id: string): void {
  const el = document.getElementById(id);
  const target = el?.matches("input, textarea, button")
    ? el
    : el?.querySelector<HTMLElement>("input, textarea, button");
  target?.focus();
}

/**
 * One field of the Fields tab: its value, and an inline edit through
 * `FieldInput`, as the Overview edits a fixed field.
 *
 * - A save sends only this key (`fieldPatch`). So a lookup that reads
 *   `"missing": true` in another row never reaches the save.
 * - The client check runs first. A refusal of the gateway keeps the edit
 *   open with the typed value, and its message shows under the input.
 * - The keyboard works as in `EditableField`: the edit takes the focus,
 *   Enter saves, Escape cancels (`editKey`). After a save or a cancel, the
 *   focus goes back to the Pencil button.
 * - The value reads by `layoutValue`, so an amount keeps the currency of
 *   the record (review P1). The Mirror lock reads the same value.
 */
function LayoutFieldRow({
  def,
  record,
  statuses,
  saving,
  refusal,
  locked,
  onPatch,
}: {
  def: FieldDef;
  record: Row;
  statuses: Status[];
  saving: boolean;
  refusal: CrmRefusal | null;
  locked: boolean;
  onPatch: (body: Record<string, unknown>) => void | Promise<boolean>;
}) {
  const id = useId();
  const editId = `${id}-edit`;
  const stored = storedValue(def, record);
  // CRM-Q17a-2 item 4: an owner who is not a member, by the Zoho name.
  const shown = zohoOwnerValue(def, record) ?? layoutValue(def, record, statuses);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<unknown>(null);
  /** The message of the client check. */
  const [problem, setProblem] = useState<string | null>(null);
  /** The gateway refused the last save of this row. */
  const [refused, setRefused] = useState(false);
  /** Where the focus goes after the next render: the control, or the Pencil. */
  const focusTo = useRef<"control" | "pencil" | null>(null);
  const error = problem ?? (refused && !saving ? (refusal?.message ?? null) : null);

  useEffect(() => {
    const to = focusTo.current;
    if (to === null) return;
    focusTo.current = null;
    if (to === "control") focusControl(id);
    else document.getElementById(editId)?.focus();
  });

  function begin() {
    setDraft(formValue(def, stored));
    setProblem(null);
    setRefused(false);
    setEditing(true);
    focusTo.current = "control";
  }

  function close() {
    setEditing(false);
    focusTo.current = "pencil";
  }

  async function commit() {
    setRefused(false);
    const outcome = await saveEdit(def, stored, draft, onPatch);
    if (outcome.done) {
      close();
    } else if (outcome.problem) {
      setProblem(outcome.problem);
    } else {
      setRefused(true);
    }
  }

  return (
    <div className="flex items-start gap-3 px-4 py-2">
      <dt className={LABEL}>
        {editing ? <label htmlFor={id}>{def.label}</label> : def.label}
      </dt>
      <dd className="min-w-0 flex-1">
        {locked ? (
          // The C4p lock, with the value of `layoutValue`.
          <span className="flex items-start gap-1.5 text-xs text-foreground" title={LOCK_TIP}>
            <span className="min-w-0 flex-1 break-words">
              <FieldValue value={shown} />
            </span>
            <Icon
              name="Lock"
              className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground"
              aria-label={LOCK_TIP}
            />
          </span>
        ) : editing ? (
          <div
            className="flex items-start gap-1"
            onKeyDown={(e) => {
              const act = editKey(e.key, (e.target as HTMLElement).tagName);
              if (act === "cancel") {
                e.stopPropagation();
                close();
              } else if (act === "commit") {
                e.preventDefault();
                void commit();
              }
            }}
          >
            <div className="min-w-0 flex-1">
              <FieldInput
                id={id}
                def={def}
                value={draft}
                held={stored}
                error={error}
                onChange={(next) => {
                  setDraft(next);
                  setProblem(null);
                  setRefused(false);
                }}
              />
            </div>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="Check"
              aria-label={`Save ${def.label.toLowerCase()}`}
              disabled={saving}
              onClick={() => void commit()}
            />
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label="Cancel"
              onClick={close}
            />
          </div>
        ) : (
          <span className="flex items-start gap-1.5 text-xs text-foreground">
            <span className="min-w-0 flex-1 break-words">
              <FieldValue value={shown} />
            </span>
            <Button
              id={editId}
              variant="ghost"
              size="icon-xs"
              icon="Pencil"
              aria-label={`Edit ${def.label.toLowerCase()}`}
              onClick={begin}
            />
          </span>
        )}
      </dd>
    </div>
  );
}
