// ── The URL is the view state ─────────────────────────────────────────────
//
// v1 has no saved-views table (specs/crm_app.md §1 non-goals): view state
// lives in the URL and canned views are code. The record sheet follows the
// same rule — `?deal=<id>` opens it OVER the list rather than navigating to
// `/crm/deals/<id>`, so Back closes the sheet instead of leaving the app and
// a link to a record carries the list the reader was looking at.
//
// All of it is parse/serialize over URLSearchParams, so it is pure and the
// deep-link behaviour is testable without a browser.

import { ENTITIES, type EntitySlug } from "./types";

/** The four sheet parameters, in the order a conflict resolves. */
export const SHEET_PARAMS = ["deal", "lead", "contact", "organization"] as const;
export type SheetParam = (typeof SHEET_PARAMS)[number];

/** `?deal=` → the `deals` collection its record belongs to. */
export const SHEET_ENTITY: Record<SheetParam, EntitySlug> = {
  deal: "deals",
  lead: "leads",
  contact: "contacts",
  organization: "organizations",
};

export type OpenRecord = { param: SheetParam; entity: EntitySlug; id: string };

/**
 * The two tabs that are not a collection.
 *
 * `settings` is CRM settings (WS-26f f2, CRM-Q3) and `reports` is the forecast
 * and funnel surface (WS-26g). Both are deliberately part of the SAME grammar
 * rather than routes of their own: each is one more thing this page can be
 * showing, and giving them routes would mean the record sheet (which is URL
 * state) could not stay open across them. `?tab=reports&deal=<id>` is a
 * legitimate link.
 *
 * The old `board` tab is gone (CRM-Q3, spec section 18.4). The board is the
 * `board` LAYOUT of the Deals view, and `LEGACY_TABS` keeps the old links alive.
 *
 * ⚠️ Adding one here is not the whole job: `page.tsx`'s branch chain and the
 * load effect both have to learn it, or the tab parses, the row highlights
 * and the screen keeps showing whatever was there.
 */
export const NON_ENTITY_TABS = ["settings", "reports"] as const;
export type NonEntityTab = (typeof NON_ENTITY_TABS)[number];

/** How a Deals or Leads view draws its records. */
export type CrmLayout = "list" | "board";

/** The views that have a board. Leads columns are the Lead Status values. */
export const BOARD_TABS = ["deals", "leads"] as const;

/** What a tab draws when the URL does not say. Deals opens as a board. */
export function defaultLayout(tab: CrmView["tab"]): CrmLayout {
  return tab === "deals" ? "board" : "list";
}

/** Old `?tab=` values and where they now open (CRM-Q3). */
const LEGACY_TABS: Record<string, { tab: CrmView["tab"]; layout?: CrmLayout }> = {
  board: { tab: "deals", layout: "board" },
  pipeline: { tab: "deals", layout: "board" },
};

/** The date-range filters, by the name they have in the URL and on the wire. */
export const RANGE_KEYS = [
  "created_from",
  "created_to",
  "updated_from",
  "updated_to",
  "close_from",
  "close_to",
] as const;
export type RangeKey = (typeof RANGE_KEYS)[number];

/** URL prefix of a custom picklist filter: `cf.Region=North,East`. */
export const CUSTOM_PARAM = "cf.";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export type CrmView = {
  /** Which view is behind the sheet. `deals` is the landing tab. */
  tab: NonEntityTab | EntitySlug;
  /** List or board. Only Deals and Leads have a board; the rest are lists. */
  layout: CrmLayout;
  /** The deal pipeline in view, or null for the remembered or default one. */
  pipelineId: string | null;
  /** The record the sheet is showing, or null when it is closed. */
  record: OpenRecord | null;
  /** Free-text search on the list. */
  q: string;
  /** The status lane a list is filtered to (leads and deals only). */
  statusId: string | null;
  /** Leads only: show the ones that already became deals. */
  includeConverted: boolean;
  /** Restrict to one owner's records. `owner_email` is assignment, not an
   *  ACL (D-CRM-3), so this is a filter and never a permission. */
  owner: string | null;
  /**
   * The column a list is ordered by, or null for the entity's default.
   *
   * In the URL with everything else, not in component state: a sorted list is
   * exactly the thing somebody links to ("the deals closing soonest"), and
   * holding it locally is how a header click changes the arrow and nothing
   * else — the load effect keys on the view, so a filter the view does not
   * carry cannot re-fetch.
   */
  sort: string | null;
  dir: "asc" | "desc";
  /** The source filter (CRM-Q8). */
  source: string | null;
  /** The date ranges (CRM-Q8). Only a valid ISO date is kept. */
  ranges: Partial<Record<RangeKey, string>>;
  /** Custom picklist filters (CRM-Q8): field key to the chosen options. */
  custom: Record<string, string[]>;
  /** The saved view in effect (`?view=`), or null. */
  viewId: string | null;
};

export const DEFAULT_VIEW: CrmView = {
  tab: "deals",
  layout: "board",
  pipelineId: null,
  record: null,
  q: "",
  statusId: null,
  includeConverted: false,
  owner: null,
  sort: null,
  dir: "desc",
  source: null,
  ranges: {},
  custom: {},
  viewId: null,
};

export function isEntity(value: string | null): value is EntitySlug {
  return !!value && (ENTITIES as readonly string[]).includes(value);
}

/**
 * Is this a tab at all?
 *
 * Written as its own predicate rather than folded into `parseView`'s ternary
 * because `settings` is the first tab that is neither the board nor a
 * collection — and the old `isEntity(tab) ? tab : "board"` SWALLOWED it,
 * silently landing a `?tab=settings` deep link on the kanban.
 */
export function isTab(value: string | null): value is CrmView["tab"] {
  return (
    isEntity(value) ||
    (!!value && (NON_ENTITY_TABS as readonly string[]).includes(value))
  );
}

/**
 * Read the view out of a query string.
 *
 * Tolerant on the way in and strict about what it produces: an unknown tab
 * falls back to Deals rather than rendering an empty list for a
 * collection that does not exist, because the URL is a shared artefact and
 * somebody will hand-edit it.
 */
export function parseView(search: string | URLSearchParams): CrmView {
  const params =
    typeof search === "string" ? new URLSearchParams(search) : search;
  const raw = params.get("tab");
  const legacy = raw ? LEGACY_TABS[raw] : undefined;
  const tab: CrmView["tab"] = legacy ? legacy.tab : isTab(raw) ? raw : "deals";
  return {
    tab,
    layout: parseLayout(tab, legacy?.layout, params.get("layout"), params),
    pipelineId: params.get("pipeline") || null,
    record: parseRecord(params),
    q: params.get("q") ?? "",
    statusId: params.get("status") || null,
    includeConverted: params.get("converted") === "1",
    owner: params.get("owner") || null,
    sort: params.get("sort") || null,
    // Anything that is not "asc" is descending: the gateway allows exactly
    // two directions and 422s a third, so a hand-edited `dir=sideways` should
    // render a list rather than an error.
    dir: params.get("dir") === "asc" ? "asc" : "desc",
    source: params.get("source") || null,
    ranges: parseRanges(params),
    custom: parseCustom(params),
    viewId: params.get("view") || null,
  };
}

function parseRanges(params: URLSearchParams): Partial<Record<RangeKey, string>> {
  const out: Partial<Record<RangeKey, string>> = {};
  for (const key of RANGE_KEYS) {
    const value = params.get(key);
    // A hand-edited date would be a 422 that empties the list.
    if (value && ISO_DATE.test(value)) out[key] = value;
  }
  return out;
}

function parseCustom(params: URLSearchParams): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  params.forEach((value, name) => {
    if (!name.startsWith(CUSTOM_PARAM) || name === CUSTOM_PARAM) return;
    const items = value.split(",").map((v) => v.trim()).filter(Boolean);
    if (items.length > 0) out[name.slice(CUSTOM_PARAM.length)] = items;
  });
  return out;
}

/** A tab with no board is a list, whatever the URL says. */
function parseLayout(
  tab: CrmView["tab"],
  legacy: CrmLayout | undefined,
  raw: string | null,
  params: URLSearchParams,
): CrmLayout {
  if (!hasBoard(tab)) return "list";
  if (raw === "board" || raw === "list") return raw;
  if (legacy) return legacy;
  // An old list link such as `?tab=deals&status=X` carries a filter. The board
  // would drop it without a word, so a link with one opens the list.
  if (["status", "sort", "q"].some((key) => params.get(key))) return "list";
  return defaultLayout(tab);
}

export function hasBoard(tab: CrmView["tab"]): tab is "deals" | "leads" {
  return (BOARD_TABS as readonly string[]).includes(tab);
}

/** Is this view drawing a board? */
export function isBoardView(view: CrmView): boolean {
  return hasBoard(view.tab) && view.layout === "board";
}

/**
 * Which record the sheet shows.
 *
 * SHEET_PARAMS order is the tiebreak, and it exists because a hand-edited URL
 * can name two: showing the first in a stated order beats showing whichever
 * one URLSearchParams happened to iterate first, and beats showing none (a
 * link that opens nothing looks broken to the person who sent it).
 */
export function parseRecord(params: URLSearchParams): OpenRecord | null {
  for (const param of SHEET_PARAMS) {
    const id = params.get(param);
    if (id) return { param, entity: SHEET_ENTITY[param], id };
  }
  return null;
}

/**
 * The view as a query string — omitting every default.
 *
 * A URL that spells out its defaults is one nobody can read, and it makes two
 * identical views produce two different history entries.
 */
export function serializeView(view: CrmView): string {
  const params = new URLSearchParams();
  if (view.tab !== "deals") params.set("tab", view.tab);
  // A board that carries a filter says so, or the link would reopen as a list
  // (parseLayout): the filter is what the reader sent.
  const filtered = Boolean(view.q.trim() || view.statusId || view.sort);
  if (
    hasBoard(view.tab) &&
    (view.layout !== defaultLayout(view.tab) || (filtered && view.layout === "board"))
  ) {
    params.set("layout", view.layout);
  }
  if (view.pipelineId && view.tab === "deals") params.set("pipeline", view.pipelineId);
  if (view.q.trim()) params.set("q", view.q.trim());
  if (view.statusId) params.set("status", view.statusId);
  if (view.includeConverted) params.set("converted", "1");
  if (view.owner) params.set("owner", view.owner);
  if (view.sort) {
    params.set("sort", view.sort);
    // `dir` only travels with a stated `sort`: on its own it means nothing,
    // and writing it would put a parameter in every URL that changes nothing.
    if (view.dir === "asc") params.set("dir", "asc");
  }
  if (view.source) params.set("source", view.source);
  for (const key of RANGE_KEYS) {
    const value = view.ranges[key];
    if (value) params.set(key, value);
  }
  for (const [key, items] of Object.entries(view.custom)) {
    if (items.length > 0) params.set(`${CUSTOM_PARAM}${key}`, items.join(","));
  }
  if (view.viewId) params.set("view", view.viewId);
  if (view.record) params.set(view.record.param, view.record.id);
  return params.toString();
}

/** `/crm?tab=leads&lead=…` — what the address bar shows. */
export function viewHref(view: CrmView, base = "/crm"): string {
  const qs = serializeView(view);
  return qs ? `${base}?${qs}` : base;
}

/** Open a record over whatever list is showing. The list state is kept, which
 *  is the whole reason the sheet is URL state and not a route. */
export function openRecord(
  view: CrmView,
  param: SheetParam,
  id: string
): CrmView {
  return { ...view, record: { param, entity: SHEET_ENTITY[param], id } };
}

export function closeRecord(view: CrmView): CrmView {
  return { ...view, record: null };
}

/**
 * Switching tabs closes the sheet and drops the filters that do not travel: a
 * status lane from the deal pipeline means nothing on the leads list.
 *
 * ⚠️ `sort` is one of them, and for a harder reason than the others: each
 * entity's sort keys are a server-side ALLOWLIST and an unknown key is a 422,
 * never a silent fall back. Carrying `amount` from the deals list onto leads
 * would not merely sort oddly — it would empty the list with a refusal.
 */
export function selectTab(view: CrmView, tab: CrmView["tab"]): CrmView {
  if (tab === view.tab) return view;
  return {
    ...view,
    tab,
    layout: defaultLayout(tab),
    record: null,
    statusId: null,
    includeConverted: false,
    sort: null,
    dir: "desc",
    // The new filters name fields of one entity, so none travels. `owner` does.
    source: null,
    ranges: {},
    custom: {},
    viewId: null,
  };
}

/**
 * A header click: the same column flips direction, a new column starts
 * descending.
 *
 * Descending first because every default this app has is "newest / biggest /
 * most recently touched" — ascending on the first click would open the list
 * on the oldest and smallest rows.
 */
export function applySort(view: CrmView, key: string): CrmView {
  if (view.sort === key) {
    return { ...view, dir: view.dir === "asc" ? "desc" : "asc" };
  }
  return { ...view, sort: key, dir: "desc" };
}

/** The list or board switch. A tab with no board stays a list. */
export function selectLayout(view: CrmView, layout: CrmLayout): CrmView {
  if (!hasBoard(view.tab) || layout === view.layout) return view;
  // A status lane is a list filter and a board column at once. Keep it off the
  // board, where the columns already are the statuses.
  return { ...view, layout, record: view.record, statusId: layout === "board" ? null : view.statusId };
}

/** The pipeline picker. It changes the lanes, so it closes no record. */
export function selectPipeline(view: CrmView, pipelineId: string | null): CrmView {
  return { ...view, pipelineId, statusId: null };
}
