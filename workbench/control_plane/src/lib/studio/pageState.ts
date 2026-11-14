/**
 * Page state of a Studio page: the selected record, the filters and the tab.
 * Pure encode and decode. The URL is the only source of truth for it.
 * STU-D11a bounds it. A hostile URL gives a safe state. STU-D11b wires it to the URL.
 */

export interface PageState {
  /** The id of the selected record, or `null`. */
  selected: string | null;
  /** Filter name to value. An empty value is not kept. */
  filters: Record<string, string>;
  /** The active tab, or `null`. */
  tab: string | null;
  /**
   * The id of the page of the app, or absent (STU-AF1). The key is `page`. The state keeps any plain id.
   * A page id that the manifest does not declare is not an error here: `buildPage` falls back to the first page.
   */
  page?: string;
  /**
   * The list bar (STU-AF4): the search text, the sort (`field` or `-field`), the layout and the group.
   * Each key is absent when it is not set. The filter chips of the bar live in `filters`.
   */
  q?: string;
  sort?: string;
  layout?: "table" | "board";
  group?: string;
}

export const EMPTY_PAGE_STATE: PageState = { selected: null, filters: {}, tab: null };

/** The bounds of a page state. `ai_studio.md` section 14 (D11a) holds the numbers. */
export const PAGE_STATE_LIMITS = {
  sel: 64,
  filters: 8,
  filterName: 48,
  filterValue: 200,
  encoded: 2000,
} as const;

const FILTER_PREFIX = "f.";
const TAB_ID = /^[a-z][a-z0-9_-]{0,47}$/;
const SORT_VALUE = /^-?[a-z][a-z0-9_]{0,47}$/;
const FILTER_NAME = /^[a-z][a-z0-9_]{0,47}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
// eslint-disable-next-line no-control-regex
const CONTROLS = /[\u0000-\u001f\u007f-\u009f]/g;
const PROTOTYPE_KEYS = new Set(["__proto__", "constructor", "prototype"]);

/** What `boundPageState` needs to know of a page: its declared tab ids. */
export interface PageTabs {
  tabs?: readonly { id: string }[];
}

function emptyState(): PageState {
  return { selected: null, filters: Object.create(null), tab: null };
}

/** Encode the state as query parameters. Keys sort, so one state has one string. */
export function encodePageState(state: PageState): string {
  const p = new URLSearchParams();
  if (state.selected) p.set("sel", state.selected);
  if (state.tab) p.set("tab", state.tab);
  if (state.page) p.set("page", state.page);
  if (state.q) p.set("q", state.q);
  if (state.sort) p.set("sort", state.sort);
  if (state.layout) p.set("layout", state.layout);
  if (state.group) p.set("group", state.group);
  for (const k of Object.keys(state.filters).sort()) {
    const v = state.filters[k];
    if (k && v) p.set(FILTER_PREFIX + k, v);
  }
  p.sort();
  return p.toString();
}

/**
 * Cut a state to its bounds. Never throws, and returns a new state.
 * `sel` is at most 64 characters with no control character. `tab` is a declared tab id of
 * `page`, or a plain id when no page is given. A filter name is a short lower-case id and
 * never a prototype key. A value is at most 200 characters, and control characters leave it.
 * At most 8 filters stay, in sorted order. The whole encoded string is at most 2,000
 * characters, or only `tab` and `sel` stay.
 */
export function boundPageState(state: PageState, page?: PageTabs): PageState {
  const out = emptyState();
  try {
    const sel = state.selected;
    if (typeof sel === "string" && sel && sel.length <= PAGE_STATE_LIMITS.sel && !CONTROL.test(sel)) {
      out.selected = sel;
    }
    if (typeof state.page === "string" && TAB_ID.test(state.page)) out.page = state.page;
    const q = typeof state.q === "string" ? state.q.replace(CONTROLS, "") : "";
    if (q && q.length <= PAGE_STATE_LIMITS.filterValue) out.q = q;
    if (typeof state.sort === "string" && SORT_VALUE.test(state.sort)) out.sort = state.sort;
    if (state.layout === "table" || state.layout === "board") out.layout = state.layout;
    if (typeof state.group === "string" && FILTER_NAME.test(state.group)) out.group = state.group;
    const tab = state.tab;
    if (typeof tab === "string" && TAB_ID.test(tab)) {
      const declared = page ? (page.tabs ?? []).some((t) => t.id === tab) : true;
      if (declared) out.tab = tab;
    }
    const names: string[] = [];
    for (const k of Object.keys(state.filters ?? {})) {
      if (k.length > PAGE_STATE_LIMITS.filterName) continue;
      if (PROTOTYPE_KEYS.has(k) || !FILTER_NAME.test(k)) continue;
      const raw = (state.filters as Record<string, unknown>)[k];
      if (typeof raw !== "string" || raw.length > PAGE_STATE_LIMITS.filterValue) continue;
      const v = raw.replace(CONTROLS, "");
      if (v) {
        out.filters[k] = v;
        names.push(k);
      }
    }
    names.sort();
    for (const k of names.slice(PAGE_STATE_LIMITS.filters)) delete out.filters[k];
    if (encodePageState(out).length > PAGE_STATE_LIMITS.encoded) out.filters = Object.create(null);
    // The filters left, and the encoded string is still too long: the bar keys leave too.
    if (encodePageState(out).length > PAGE_STATE_LIMITS.encoded) { delete out.q; delete out.sort; delete out.layout; delete out.group; }
  } catch {
    return emptyState();
  }
  return out;
}

/** Decode query parameters, then bound them. Unknown keys are ignored. Never throws. */
export function decodePageState(
  query: string | URLSearchParams,
  page?: PageTabs,
): PageState {
  try {
    const p = typeof query === "string" ? new URLSearchParams(query) : query;
    const filters: Record<string, string> = Object.create(null);
    for (const [k, v] of p.entries()) {
      if (k.startsWith(FILTER_PREFIX) && k.length > FILTER_PREFIX.length && v) {
        filters[k.slice(FILTER_PREFIX.length)] = v;
      }
    }
    return boundPageState({ selected: p.get("sel") || null, filters, tab: p.get("tab") || null, page: p.get("page") || undefined,
      q: p.get("q") || undefined, sort: p.get("sort") || undefined,
      layout: (p.get("layout") || undefined) as PageState["layout"], group: p.get("group") || undefined }, page);
  } catch {
    return emptyState();
  }
}

/** A filter edit that the URL has not caught up with. `base` is the URL value when the member typed. */
export interface FilterDraft {
  base: string;
  text: string;
}

/**
 * The text for a filter input. The draft shows only while the URL still holds the value it
 * was typed over. When the URL value changes (Back, a shared link), the URL wins.
 */
export function filterInputValue(draft: FilterDraft | null, urlValue: string): string {
  return draft && draft.base === urlValue ? draft.text : urlValue;
}
