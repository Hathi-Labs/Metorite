// ── The "+ Filter" field list and the filter chips (WS-53 CRM-U8) ───────────
//
// Row 2 of the toolbar shows what is filtered: one chip for each filter in
// force, and a "+ Filter" button that lists every field a member can filter
// by. The list groups the fields, and a picklist carries how full it is, so the
// fields that help come first. Pure: `components/FilterBar.tsx` draws it.
// Fence: `components/crmU8.test.ts`.

import {
  ANY,
  SOURCE_OPTIONS,
  activeCount,
  dateFilters,
  optionsOf,
  setCustom,
  type CustomFilter,
  type CustomFilterKind,
  type DateFilter,
} from "./filterBar";
import { fillLabel, fillOf, type FieldDef } from "./fields";
import type { EntitySlug } from "./types";
import type { CrmView, RangeKey } from "./urlState";

export type FilterGroup = "Dates" | "Picklists" | "Numbers" | "Text" | "Other";

/** The groups, in the order of the panel. */
export const FILTER_GROUPS: readonly FilterGroup[] = [
  "Dates",
  "Picklists",
  "Numbers",
  "Text",
  "Other",
];

/** How the editor of a field is drawn. */
export type FilterFieldKind = "range" | "source" | "converted" | CustomFilterKind;

export type FilterField = {
  /** `range:created`, `source`, `converted` or `custom:<key>`. */
  id: string;
  label: string;
  group: FilterGroup;
  kind: FilterFieldKind;
  /** The share of records that fill the field, 0 to 1, or null when unknown. */
  fill: number | null;
  range?: DateFilter;
  def?: FieldDef;
};

const RANGE_ID: Record<string, string> = {
  Created: "range:created",
  Updated: "range:updated",
  Closing: "range:close",
};

const RANGE_PAIRS: ReadonlyArray<readonly [string, string, RangeKey, RangeKey]> = [
  ["Created", "range:created", "created_from", "created_to"],
  ["Updated", "range:updated", "updated_from", "updated_to"],
  ["Closing", "range:close", "close_from", "close_to"],
];

const GROUP_OF_KIND: Record<CustomFilterKind, FilterGroup> = {
  options: "Picklists",
  number: "Numbers",
  date: "Dates",
  boolean: "Other",
};

/**
 * Every field the "+ Filter" panel offers for one entity, in panel order:
 * the groups of `FILTER_GROUPS`, and in a group the fullest custom field
 * first. A field with no fill number comes after the ones that have one and
 * keeps the order of the layout. Leads add Converted. Each entity adds Source.
 * A generic module (`null`, CRM-U9) has Updated and its own fields, and no Source.
 */
export function filterFields(
  entity: EntitySlug | null,
  custom: readonly CustomFilter[],
  mirror = false,
): FilterField[] {
  const out: FilterField[] = [];
  for (const range of dateFilters(entity, mirror)) {
    out.push({
      id: RANGE_ID[range.label],
      label: range.label,
      group: "Dates",
      kind: "range",
      fill: null,
      range,
    });
  }
  const fromDefs: FilterField[] = custom
    .map((filter, index) => ({ filter, index }))
    .sort((a, b) => fillOf(b.filter.def) - fillOf(a.filter.def) || a.index - b.index)
    .map(({ filter }) => ({
      id: `custom:${filter.def.key}`,
      label: filter.def.label,
      group: GROUP_OF_KIND[filter.kind],
      kind: filter.kind,
      fill: fillOf(filter.def) >= 0 ? fillOf(filter.def) : null,
      def: filter.def,
    }));
  out.push(...fromDefs);
  if (entity !== null) {
    out.push({ id: "source", label: "Source", group: "Other", kind: "source", fill: null });
  }
  if (entity === "leads") {
    out.push({ id: "converted", label: "Converted", group: "Other", kind: "converted", fill: null });
  }
  // Stable by group. Inside a group the order above holds.
  return FILTER_GROUPS.flatMap((group) => out.filter((f) => f.group === group));
}

/** The fields of the panel by group, with the search words applied. Empty groups drop out. */
export function groupFields(
  fields: readonly FilterField[],
  words = "",
): Array<{ group: FilterGroup; items: FilterField[] }> {
  const needle = words.trim().toLowerCase();
  const kept = needle ? fields.filter((f) => f.label.toLowerCase().includes(needle)) : fields;
  return FILTER_GROUPS.map((group) => ({
    group,
    items: kept.filter((f) => f.group === group),
  })).filter((g) => g.items.length > 0);
}

/** "82% filled" for a field that carries a fill number, else null. */
export function fieldFillText(field: FilterField): string | null {
  return field.def ? fillLabel(field.def) : null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dayText(iso: string, withYear: boolean): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[(m ?? 1) - 1] ?? ""}${withYear ? ` ${y}` : ""}`;
}

/**
 * A date range in words: `1 Oct–31 Dec`. The year shows when the two ends
 * are in different years, or when an end is not in `thisYear`.
 */
export function rangeText(
  from: string | undefined,
  to: string | undefined,
  thisYear: number,
): string {
  const year = (iso: string) => Number(iso.slice(0, 4));
  const ends = [from, to].filter((x): x is string => Boolean(x));
  const withYear =
    ends.some((iso) => year(iso) !== thisYear) || (!!from && !!to && year(from) !== year(to));
  if (from && to) return `${dayText(from, withYear)}–${dayText(to, withYear)}`;
  if (from) return `from ${dayText(from, withYear)}`;
  if (to) return `to ${dayText(to, withYear)}`;
  return "";
}

/** One chip of row 2: a filter in force that the member can reopen or remove. */
export type FilterChip = { id: string; label: string };

/**
 * The chips for the filters in force, in panel order: the date ranges, the
 * source, Converted, then the custom fields. Owner and stage have their own
 * controls and the search has its box, so they make no chip.
 *
 * ⚠️ A range shows even when the entity does not offer it (a link can carry
 * `close_from` on leads), so a narrowed list never hides why.
 */
export function activeFilterChips(
  view: CrmView,
  fields: readonly FilterField[],
  thisYear = new Date().getFullYear(),
): FilterChip[] {
  const chips: FilterChip[] = [];
  for (const [label, id, from, to] of RANGE_PAIRS) {
    if (view.ranges[from] || view.ranges[to]) {
      chips.push({
        id,
        label: `${label}: ${rangeText(view.ranges[from], view.ranges[to], thisYear)}`,
      });
    }
  }
  if (view.source) {
    const name = SOURCE_OPTIONS.find((o) => o.value === view.source)?.label ?? view.source;
    chips.push({ id: "source", label: `Source: ${name}` });
  }
  if (view.includeConverted) chips.push({ id: "converted", label: "Converted" });
  for (const [key, items] of Object.entries(view.custom)) {
    if (items.length === 0) continue;
    const field = fields.find((f) => f.id === `custom:${key}`);
    const names = items.map((item) => {
      if (field?.kind === "boolean") return item === "true" ? "Yes" : item === "false" ? "No" : item;
      return field?.def ? (optionsOf(field.def).find((o) => o.value === item)?.label ?? item) : item;
    });
    chips.push({ id: `custom:${key}`, label: `${field?.label ?? key}: ${names.join(", ")}` });
  }
  return chips;
}

/** The view without the filter that a chip names. */
export function removeFilter(view: CrmView, chipId: string): CrmView {
  const pair = RANGE_PAIRS.find(([, id]) => id === chipId);
  if (pair) {
    const ranges = { ...view.ranges };
    delete ranges[pair[2]];
    delete ranges[pair[3]];
    return { ...view, ranges };
  }
  if (chipId === "source") return { ...view, source: null };
  if (chipId === "converted") return { ...view, includeConverted: false };
  if (chipId.startsWith("custom:")) return setCustom(view, chipId.slice("custom:".length), ANY);
  return view;
}

/** Is anything filtered? Drives "Clear". It counts the search, the stage and Converted too. */
export function anyFilter(view: CrmView): boolean {
  return (
    activeCount(view) > 0 ||
    Boolean(view.statusId) ||
    Boolean(view.q.trim()) ||
    view.includeConverted
  );
}

/** The filters in force on a phone: the chips, the owner and the stage. */
export function filtersInForce(view: CrmView, chips: readonly FilterChip[]): number {
  return chips.length + (view.owner ? 1 : 0) + (view.statusId ? 1 : 0);
}

/** The label of the phone button: "Filters", or "Filters (2)" with two in force. */
export function filtersButtonLabel(active: number): string {
  return active > 0 ? `Filters (${active})` : "Filters";
}

/**
 * The options of the Stage control. They come from the stage chips of the
 * list (`chipsFor`), less Converted, which has its own chip. "All" is the
 * empty choice. A hidden stage stays out unless the view is filtered to it.
 */
export function stageOptions(
  chips: ReadonlyArray<{ id: string; label: string; count?: number }>,
  noun: string,
): Array<{ value: string; label: string }> {
  return chips
    .filter((c) => c.id !== "converted")
    .map((c) => ({
      value: c.id === "all" ? ANY : c.id,
      label:
        c.id === "all"
          ? `Any ${noun.toLowerCase()}`
          : c.count !== undefined
            ? `${c.label} (${c.count})`
            : c.label,
    }));
}
