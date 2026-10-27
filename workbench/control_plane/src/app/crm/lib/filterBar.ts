// ── The CRM filter bar's choices (WS-53 CRM-Q8) ───────────────────────────
//
// Pure, so the vitest setup (no DOM) tests every rule. `components/FilterBar.tsx`
// draws what this file decides.

import { coalesce } from "@/lib/useCachedResource";

import { isDateValue, toWire } from "./fieldLayout";
import type { FieldDef } from "./fields";
import { fieldOptions, pickable } from "./fields";
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

/**
 * The date ranges an entity offers. Only a deal has a close date. A generic
 * module (`null`) offers Updated only: its record keeps no created date.
 */
export function dateFilters(entity: EntitySlug | null, mirror = false): DateFilter[] {
  // A synced record keeps no created date, and the gateway refuses the range.
  const out: DateFilter[] = [
    ...(mirror || entity === null ? [] : [{ label: "Created", from: "created_from", to: "created_to" } as DateFilter]),
    { label: "Updated", from: "updated_from", to: "updated_to" },
  ];
  if (entity === "deals") out.push({ label: "Closing", from: "close_from", to: "close_to" });
  return out;
}

/** One owner that `GET /crm/owners` names (CRM-X1). */
export type CrmOwner = {
  email: string;
  /** The display name of a member. Null for an owner who is not a member. */
  name: string | null;
  /** True for an active member of the organization. */
  member: boolean;
};

/**
 * The owner options: anyone, me, each owner of `GET /crm/owners` in its order,
 * then a chosen owner that the list does not hold (CRM-X1).
 *
 * The list is the members and the record owners of the whole organization,
 * not the rows on screen. So an owner with no row on page 1 is still offered.
 * `null` means the list is not read yet, or the read failed.
 */
export function ownerOptions(
  owners: ReadonlyArray<CrmOwner> | null,
  chosen: string | null,
): Array<{ value: string; label: string }> {
  const seen = new Set<string>();
  const out: Array<{ value: string; label: string }> = [];
  for (const owner of owners ?? []) {
    const key = owner.email.toLowerCase();
    if (!owner.email.trim() || seen.has(key)) continue;
    seen.add(key);
    out.push({ value: owner.email, label: owner.name?.trim() || owner.email });
  }
  if (chosen && chosen !== ME && !seen.has(chosen.toLowerCase())) {
    out.push({ value: chosen, label: chosen });
  }
  return [{ value: ANY, label: "Anyone" }, { value: ME, label: "Me" }, ...out];
}

/**
 * The search box's text, and what it knows about the URL (CRM-X1).
 *
 * `seen` is the last `?q=` the box reconciled. `mine` is the last query the
 * box wrote itself, as the URL holds it, or null.
 */
export type SearchBoxState = { draft: string; seen: string; mine: string | null };

export function searchBoxStart(value: string): SearchBoxState {
  return { draft: value, seen: value, mine: null };
}

/**
 * The URL's `?q=` is now `value`. A value that the box did not write (Back,
 * Clear, a saved view) replaces the text. The box's own write does not, so a
 * key typed while the URL catches up is never lost.
 */
export function searchBoxSync(state: SearchBoxState, value: string): SearchBoxState {
  if (value === state.seen) return state;
  return { draft: value === state.mine ? state.draft : value, seen: value, mine: null };
}

/**
 * The box wrote `q`. It remembers the write trimmed, because the URL holds it
 * trimmed (`serializeView`). Untrimmed, a pause after a space read as a write
 * from outside and ate the space: "printer order" became "printerorder".
 */
export function searchBoxWrote(state: SearchBoxState, q: string): SearchBoxState {
  return { ...state, mine: q.trim() };
}

/** Does writing `q` change the URL, which holds `current`? */
export function searchChanges(q: string, current: string): boolean {
  return q.trim() !== current.trim();
}

/** How long the search box waits after the last key before it writes `?q=`. */
export const SEARCH_DELAY_MS = 300;

/**
 * The search box's timer (CRM-X1). Each key calls `type`, and `commit` runs
 * once, with the last text, `ms` after the last key. It is the shared
 * trailing coalescer, so the box adds no second debounce.
 */
export function searchCommitter(
  commit: (q: string) => void,
  ms = SEARCH_DELAY_MS,
): { type: (q: string) => void; cancel: () => void } {
  let latest = "";
  const timer = coalesce(() => commit(latest), ms);
  return {
    type(q: string) {
      latest = q;
      timer.trigger();
    },
    cancel: timer.cancel,
  };
}

/**
 * The active options of a picklist definition. `fieldOptions` is the one
 * reader of the options (CRM-C4p). A retired option is left out, because no
 * picker offers it.
 */
export function optionsOf(def: FieldDef): Array<{ value: string; label: string }> {
  return fieldOptions(def)
    .filter((o) => !o.retired)
    .map(({ value, label }) => ({ value, label }));
}

/** The custom fields a member may filter by: picklists that carry options. */
export function picklistDefs(defs: FieldDef[] | null | undefined): FieldDef[] {
  return pickable(defs).filter(
    (d) => (d.type === "select" || d.type === "multiselect") && optionsOf(d).length > 0,
  );
}

// ── The custom filters of the field studio (WS-53 CRM-C4b) ──────────────────
//
// Spec section 20.7, CRM-C4b Scope item 5, with the audit fix B: each filter
// sends `custom.<key>.in` with one value, through `extraQuery`. The gateway
// matches it the same way as `custom.<key>=` (`core.py`, the custom filter):
// the text of the stored value, or an item of a stored array. So each filter
// is equality only. A range needs a new gateway operator, and it is a
// follow-up.

/** How the bar draws the filter of one custom field. */
export type CustomFilterKind = "options" | "boolean" | "number" | "date";

/** The types that the studio adds to the bar, and the control of each. */
const STUDIO_FILTERS: Readonly<Record<string, CustomFilterKind>> = {
  boolean: "boolean",
  number: "number",
  decimal: "number",
  currency: "number",
  date: "date",
};

export type CustomFilter = { def: FieldDef; kind: CustomFilterKind };

/**
 * The custom filters of the bar. Without the studio: the picklists, as
 * before. With it: also a checkbox, a number, a decimal, a currency and a
 * date. Each one reads `pickable`, so a retired field and a system row are
 * never offered (done-when 6).
 */
export function customFilters(defs: FieldDef[] | null | undefined, studio: boolean): CustomFilter[] {
  const out: CustomFilter[] = [];
  for (const def of pickable(defs)) {
    if (def.type === "select" || def.type === "multiselect") {
      if (optionsOf(def).length > 0) out.push({ def, kind: "options" });
    } else if (studio && Object.hasOwn(STUDIO_FILTERS, def.type)) {
      out.push({ def, kind: STUDIO_FILTERS[def.type] });
    }
  }
  return out;
}

/**
 * The hint of the "No" choice. The gateway compares the stored text, so a
 * record with no value for the key is not "No".
 */
export const NO_HINT = "A record with no value is not found.";

/** The choices of a checkbox filter: any, Yes (`true`) or No (`false`). */
export function booleanFilterOptions(
  def: FieldDef,
): Array<{ value: string; label: string; description?: string }> {
  return [
    { value: ANY, label: `Any ${def.label.toLowerCase()}` },
    { value: "true", label: "Yes" },
    { value: "false", label: "No", description: NO_HINT },
  ];
}

/**
 * The value of a number filter for the text that the member typed. `""`
 * clears the filter, and `null` means the text is not a number.
 *
 * `toWire` reads the text as the record form does, so `1,20,000` is
 * 120000. The value is `String(Number(text))`, the text that Postgres gives
 * for a JSON number that the client wrote.
 */
export function numberFilterValue(def: FieldDef, text: string): string | null {
  const value = toWire(def, text);
  if (value === null) return ANY;
  return typeof value === "number" && Number.isFinite(value) ? String(value) : null;
}

/** The value of a date filter: `YYYY-MM-DD`, `""` to clear, or null when not a day. */
export function dateFilterValue(text: string): string | null {
  const day = text.trim();
  if (day === "") return ANY;
  return isDateValue(day) ? day : null;
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
