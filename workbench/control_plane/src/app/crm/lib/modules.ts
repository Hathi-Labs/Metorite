// ── The generic Zoho modules (WS-53 CRM-Q6b) ──────────────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 18.7 "CRM-Q6b: the views".
//
// The mirror keeps each Zoho module that has no table of its own (Products,
// Quotes, each custom module) in one generic store. The gateway serves them at
// `GET /crm/modules` and `GET /crm/modules/{module}/records[/{id}]`.
//
// Pure, so the vitest setup (no DOM) can test every rule: the rail rows, the
// list columns and the request, the record header and the lookup links. The
// components draw what this file decides, with the parts that the four known
// entities use (`RecordList`, the record page shell, `FieldValue`).
//
// ⚠️ Read only, in every mode. A generic record has no native writer: the sync
// is the only writer (CRM-Q6a), so no control here ever edits one.

import { fieldRows, type FieldDef, type FieldRow, type FieldValue } from "./fields";
import { LIST_PAGE_SIZE, type ListQuery } from "./filters";
import { dateTime, shortDate } from "./format";
import type { CrmColumn } from "./columns";
import type { EntitySlug } from "./types";
import { MODULE_NAME, isModuleName } from "./urlState";

type Row = Record<string, unknown>;

/** One row of `GET /crm/modules`, in the profile order. */
export type CrmModule = {
  api_name: string;
  /** The plural label, as the rail shows it. */
  label: string;
  /** The singular label, as the record page names one record. */
  singular_label: string | null;
  /** The records that the mirror holds. */
  count: number;
};

/** One record of a generic module. `fields` holds each value by API name. */
export type ModuleRecord = {
  id: string;
  module: string;
  name: string | null;
  owner_email: string | null;
  fields: Row;
  ext_source: string | null;
  ext_id: string | null;
  ext_url: string | null;
  ext_created_at: string | null;
  ext_modified_at: string | null;
};

/** A field definition of a generic module. A lookup names its target. */
export type ModuleFieldDef = FieldDef & { lookup_module: string | null };

/**
 * Where a lookup opens: the record page of a known record (`entity`), or the
 * generic record page of a mirrored module (`module`). The gateway leaves a
 * lookup with no local target out, and the page draws its name as text.
 */
export type ModuleLink =
  | { kind: "record"; entity: EntitySlug; module?: null; id: string; name: string | null }
  | { kind: "module"; module: string; entity?: null; id: string; name: string | null };

/** `GET /crm/modules/{module}/records`: the list shape, and the field definitions. */
export type ModuleListResponse = {
  rows: ModuleRecord[];
  total: number;
  field_defs?: ModuleFieldDef[];
};

/** `GET /crm/modules/{module}/records/{id}`. */
export type ModuleRecordPage = {
  module: CrmModule;
  record: ModuleRecord;
  field_defs: ModuleFieldDef[];
  links: Record<string, ModuleLink>;
};

// The name check lives in the URL grammar (`urlState.ts`), which reads it
// first. It is re-exported here for the callers of this file.
export { MODULE_NAME, isModuleName };

// ── The rail ───────────────────────────────────────────────────────────────

/** The icon of each standard module. A custom module gets `Boxes`. */
const MODULE_ICONS: Record<string, string> = {
  Products: "Package",
  Quotes: "FileText",
  Sales_Orders: "ShoppingCart",
  Purchase_Orders: "ClipboardList",
  Invoices: "Receipt",
  Vendors: "Truck",
  Price_Books: "BookOpen",
  Cases: "LifeBuoy",
  Solutions: "Lightbulb",
  Campaigns: "Megaphone",
};

export function moduleIcon(apiName: string): string {
  return MODULE_ICONS[apiName] ?? "Boxes";
}

/** One rail row of a generic module. */
export type ModuleRailRow = { module: string; label: string; icon: string; count: number };

/**
 * The rail rows of the generic modules (section 18.4): one for each module, in
 * the order of `GET /crm/modules`, which is the profile order. The gateway
 * already leaves out a module that is not mirrored. An empty list gives no
 * row, so the group does not show. A failed read (`null`) gives no row either.
 */
export function moduleRailRows(modules: readonly CrmModule[] | null | undefined): ModuleRailRow[] {
  return (modules ?? [])
    .filter((m) => isModuleName(m.api_name))
    .map((m) => ({
      module: m.api_name,
      label: m.label || m.api_name,
      icon: moduleIcon(m.api_name),
      count: m.count,
    }));
}

/** The label of one module, for the title bar. */
export function moduleLabel(
  modules: readonly CrmModule[] | null | undefined,
  apiName: string | null | undefined,
): string {
  return modules?.find((m) => m.api_name === apiName)?.label || apiName || "Module";
}

// ── The list ───────────────────────────────────────────────────────────────

/** The sort keys of `GET /crm/modules/{module}/records` (the gateway allowlist). */
export const MODULE_SORTS = ["name", "owner_email", "ext_modified_at", "ext_created_at"] as const;

/**
 * The fixed columns of a module list. The member may add up to five fields
 * with the Columns control, by the rules of `fields.ts`.
 */
export const MODULE_COLUMNS: CrmColumn[] = [
  { key: "name", label: "Name", sort: "name", className: "font-medium text-foreground" },
  { key: "owner_email", label: "Owner", sort: "owner_email" },
  { key: "modified", label: "Modified", sort: "ext_modified_at" },
];

/** The rows per page: the page of the four lists (CRM-X1). */
export const MODULE_PAGE_SIZE = LIST_PAGE_SIZE;

/** The request of one page of a module list. */
export function moduleListQuery(
  view: { q: string; sort: string | null; dir: "asc" | "desc" },
  page = 1,
): ListQuery {
  const query: ListQuery = { page, page_size: MODULE_PAGE_SIZE };
  if (view.q.trim()) query.q = view.q.trim();
  if (view.sort && (MODULE_SORTS as readonly string[]).includes(view.sort)) {
    query.sort = view.sort;
    query.dir = view.dir;
  }
  return query;
}

/**
 * A module record as a list row. The fields go under `custom`, so the Columns
 * control and `listCell` read them as they read a known record's fields.
 * `modified` is the date as the Modified column reads it.
 */
export function listRow(record: ModuleRecord): Row {
  return {
    ...record,
    name: record.name || "Untitled",
    modified: shortDate(record.ext_modified_at),
    custom: record.fields,
  };
}


// ── The record page ────────────────────────────────────────────────────────

/** The tabs of a generic record page. No timeline: a generic record has none. */
export type ModuleTab = "overview" | "fields" | "related";

/** Each field key whose definition is a lookup and whose value is set. */
export function lookupKeys(page: Pick<ModuleRecordPage, "record" | "field_defs">): string[] {
  return page.field_defs
    .filter((d) => d.type === "lookup" && !isBlank(page.record.fields[d.key]))
    .map((d) => d.key);
}

/** Overview and Fields, and Related only when a lookup holds a value. */
export function moduleTabs(page: Pick<ModuleRecordPage, "record" | "field_defs">): ModuleTab[] {
  return lookupKeys(page).length > 0 ? ["overview", "fields", "related"] : ["overview", "fields"];
}

/** The tab the page draws: the one asked for, or Overview. */
export function shownModuleTab(
  page: Pick<ModuleRecordPage, "record" | "field_defs">,
  tab: string | null | undefined,
): ModuleTab {
  return (moduleTabs(page) as string[]).includes(tab ?? "") ? (tab as ModuleTab) : "overview";
}

/** The facts of the header: the owner and the modified date (section 18.7). */
export function moduleFacts(record: ModuleRecord): FieldRow[] {
  const facts: FieldRow[] = [
    {
      key: "owner_email",
      label: "Owner",
      value: record.owner_email ? { kind: "text", text: record.owner_email } : EMPTY,
    },
    {
      key: "ext_modified_at",
      label: "Modified",
      value: record.ext_modified_at ? { kind: "text", text: dateTime(record.ext_modified_at) } : EMPTY,
    },
  ];
  return facts.filter((f) => f.value.kind !== "empty");
}

/** One field row, with the link of a lookup when the gateway found one. */
export type ModuleFieldRow = FieldRow & { link: ModuleLink | null };

/**
 * The fields of a generic record, by the CRM-Q4 rules of `fields.ts`: the
 * order of `position`, the label, and only the visible fields. A lookup with
 * a link opens that record, and any other lookup stays text.
 */
export function moduleFieldRows(page: Pick<ModuleRecordPage, "record" | "field_defs" | "links">): ModuleFieldRow[] {
  return fieldRows({ custom: page.record.fields }, page.field_defs).map((row) => ({
    ...row,
    link: linkOf(page.links, row.key),
  }));
}

/** The lookups of a record, for the Related tab, in field order. */
export function moduleRelated(page: Pick<ModuleRecordPage, "record" | "field_defs" | "links">): ModuleFieldRow[] {
  const keys = new Set(lookupKeys(page));
  return moduleFieldRows(page).filter((row) => keys.has(row.key));
}

/** The most fields the Overview tab shows. The Fields tab shows them all. */
export const OVERVIEW_FIELDS = 8;

/** A link from the gateway, checked: an unknown kind or a bad id is no link. */
export function linkOf(links: Record<string, ModuleLink> | null | undefined, key: string): ModuleLink | null {
  const link = links?.[key];
  if (!link || typeof link.id !== "string" || !link.id) return null;
  if (link.kind === "record" && ["deals", "leads", "contacts", "organizations"].includes(String(link.entity))) {
    return link;
  }
  if (link.kind === "module" && isModuleName(link.module)) return link;
  return null;
}

/** The text of a link: the name Zoho sent, or the value as text. */
export function linkText(row: ModuleFieldRow): string {
  if (row.link?.name) return row.link.name;
  return row.value.kind === "empty" ? "—" : row.value.kind === "chips" ? row.value.items.join(", ") : row.value.text;
}

/** The title of the record page. */
export function moduleTitle(record: ModuleRecord | null, singular: string | null): string {
  if (!record) return "Loading…";
  return record.name || `Untitled ${(singular || "record").toLowerCase()}`;
}

const EMPTY: FieldValue = { kind: "empty" };

function isBlank(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}
