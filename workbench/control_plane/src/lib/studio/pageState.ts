/**
 * Page state of a Studio page: the selected record, the filters and the tab.
 * Pure encode and decode, so a later slice can put it in the URL.
 * STU-D1 does not wire it to the URL. That waits for STU-D11.
 */

export interface PageState {
  /** The id of the selected record, or `null`. */
  selected: string | null;
  /** Filter name to value. An empty value is not kept. */
  filters: Record<string, string>;
  /** The active tab, or `null`. */
  tab: string | null;
}

export const EMPTY_PAGE_STATE: PageState = { selected: null, filters: {}, tab: null };

const FILTER_PREFIX = "f.";

/** Encode the state as query parameters. Keys sort, so one state has one string. */
export function encodePageState(state: PageState): string {
  const p = new URLSearchParams();
  if (state.selected) p.set("sel", state.selected);
  if (state.tab) p.set("tab", state.tab);
  for (const k of Object.keys(state.filters).sort()) {
    const v = state.filters[k];
    if (k && v) p.set(FILTER_PREFIX + k, v);
  }
  p.sort();
  return p.toString();
}

/** Decode query parameters. Unknown keys are ignored. Never throws. */
export function decodePageState(query: string | URLSearchParams): PageState {
  const p = typeof query === "string" ? new URLSearchParams(query) : query;
  const filters: Record<string, string> = Object.create(null);
  for (const [k, v] of p.entries()) {
    if (k.startsWith(FILTER_PREFIX) && k.length > FILTER_PREFIX.length && v) {
      filters[k.slice(FILTER_PREFIX.length)] = v;
    }
  }
  return { selected: p.get("sel") || null, filters, tab: p.get("tab") || null };
}
