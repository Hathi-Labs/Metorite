// ── The CRM filter bar's choices (WS-53 CRM-Q8) ───────────────────────────
//
// Pure, so the vitest setup (no DOM) tests every rule. `components/FilterBar.tsx`
// draws what this file decides.

import type { FieldDef } from "./fields";
import { pickable } from "./fields";
import type { EntitySlug } from "./types";
import { RANGE_KEYS, type CrmView, type RangeKey } from "./urlState";

export const ANY = "";
/** The owner option that means the caller. The gateway resolves it from the session. */
export const ME = "me";

/** The `source` values the gateway accepts (core.SOURCES). */
export const SOURCE_OPTIONS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "manual", label: "Manual" },
  { value: "import", label: "Import" },
  { value: "email", label: "Email" },
  { value: "agent", label: "Agent" },
];

export type DateFilter = { label: string; from: RangeKey; to: RangeKey };

/** The date ranges an entity offers. Only a deal has a close date. */
export function dateFilters(entity: EntitySlug, mirror = false): DateFilter[] {
  // A synced record keeps no created date, and the gateway refuses the range.
  const out: DateFilter[] = [
    ...(mirror ? [] : [{ label: "Created", from: "created_from", to: "created_to" } as DateFilter]),
    { label: "Updated", from: "updated_from", to: "updated_to" },
  ];
  if (entity === "deals") out.push({ label: "Closing", from: "close_from", to: "close_to" });
  return out;
}

/** The owner options: anyone, me, then each owner seen in the rows, then a chosen one not in them. */
export function ownerOptions(
  rows: ReadonlyArray<Record<string, unknown>>,
  chosen: string | null,
): Array<{ value: string; label: string }> {
  const seen = new Map<string, string>();
  for (const row of rows) {
    const email = row.owner_email;
    if (typeof email === "string" && email.trim()) seen.set(email.toLowerCase(), email);
  }
  if (chosen && chosen !== ME && !seen.has(chosen.toLowerCase())) {
    seen.set(chosen.toLowerCase(), chosen);
  }
  return [
    { value: ANY, label: "Anyone" },
    { value: ME, label: "Me" },
    ...[...seen.values()].sort().map((email) => ({ value: email, label: email })),
  ];
}

/** The options of a picklist definition. The sync writes `{label, value}`. */
export function optionsOf(def: FieldDef): Array<{ value: string; label: string }> {
  const out: Array<{ value: string; label: string }> = [];
  for (const raw of def.options ?? []) {
    if (typeof raw === "string") {
      out.push({ value: raw, label: raw });
    } else if (raw && typeof raw === "object") {
      const o = raw as Record<string, unknown>;
      const value =
        typeof o.value === "string" ? o.value : typeof o.label === "string" ? o.label : null;
      if (value) {
        out.push({ value, label: typeof o.label === "string" && o.label ? o.label : value });
      }
    }
  }
  return out;
}

/** The custom fields a member may filter by: picklists that carry options. */
export function picklistDefs(defs: FieldDef[] | null | undefined): FieldDef[] {
  return pickable(defs).filter(
    (d) => (d.type === "select" || d.type === "multiselect") && optionsOf(d).length > 0,
  );
}

/** Choose one option of a picklist, or none. Other fields stay as they are. */
export function setCustom(view: CrmView, key: string, value: string): CrmView {
  const custom = { ...view.custom };
  if (value) custom[key] = [value];
  else delete custom[key];
  return { ...view, custom };
}

/** Set or clear one end of a date range. An empty or invalid value clears it. */
export function setRange(view: CrmView, key: RangeKey, value: string): CrmView {
  const ranges = { ...view.ranges };
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) ranges[key] = value;
  else delete ranges[key];
  return { ...view, ranges };
}

/** How many of the bar's filters are in force. */
export function activeCount(view: CrmView): number {
  return (
    (view.owner ? 1 : 0) +
    (view.source ? 1 : 0) +
    RANGE_KEYS.filter((k) => view.ranges[k]).length +
    Object.values(view.custom).filter((items) => items.length > 0).length
  );
}

/** What a view lost before its request: stale custom fields and ranges the mode refuses. */
export type Pruned = {
  view: CrmView;
  removed: string[];
  /**
   * The definitions failed to load, so the custom filters cannot be checked.
   * They are kept (dropping them would widen the list without a word), and the
   * caller must not fetch with them.
   */
  unchecked: boolean;
};

/**
 * Drop each filter the gateway would refuse (CRM-Q8 review).
 *
 * A saved view or a `cf.X` link can name a custom field that no longer exists,
 * for example after a mode reset deleted the Zoho definitions. The gateway
 * answers 422 for an unknown key, and the whole list would fail. So a key that
 * the loaded definitions do not list is dropped before the fetch. A FAILED
 * read (`defs` is null) drops nothing and sets `unchecked`: a one-off network
 * error must not widen the list. In Mirror mode the Created range is dropped as well,
 * because a synced record keeps no created date.
 *
 * Pure. The caller shows `removed` as a short note.
 */
export function pruneFilters(
  view: CrmView,
  defs: FieldDef[] | null,
  mirror: boolean,
): Pruned {
  const removed: string[] = [];
  const unchecked = defs === null && Object.keys(view.custom).length > 0;
  const known = new Set((defs ?? []).map((d) => d.key));
  const labels = new Map((defs ?? []).map((d) => [d.key, d.label]));
  let custom = view.custom;
  for (const key of Object.keys(view.custom)) {
    if (defs === null || known.has(key)) continue;
    if (custom === view.custom) custom = { ...view.custom };
    delete custom[key];
    removed.push(labels.get(key) ?? key);
  }
  let ranges = view.ranges;
  if (mirror) {
    for (const key of ["created_from", "created_to"] as const) {
      if (!ranges[key]) continue;
      if (ranges === view.ranges) ranges = { ...view.ranges };
      delete ranges[key];
      if (!removed.includes("Created")) removed.push("Created");
    }
  }
  if (removed.length === 0) return { view, removed, unchecked };
  return { view: { ...view, custom, ranges }, removed, unchecked };
}
