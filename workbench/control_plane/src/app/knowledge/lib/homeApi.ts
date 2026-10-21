// ── The home lists, the favourites and the page icon (WS-61 KB-2f) ─────────
//
// The client of `gateway/routes/knowledge/home.py` and of the `icon` field
// of `PATCH /knowledge/pages/{id}`. Every request goes through `call` in
// `api.ts`, the one way to the gateway. The keys sit under
// `KNOWLEDGE_CACHE`, so one `invalidate(KNOWLEDGE_CACHE)` after a write also
// re-reads these lists.

import { call } from "./api";
import type { PageSummary } from "./types";

/** One row of a home list: a page with no body, and the name of its space. */
export interface HomePage extends PageSummary {
  space_name: string;
  /** Only on a favourite: when the member starred it. */
  starred_at?: string | null;
}

/** One page of a home list, and the cursor of the next, or `null`. */
export interface HomeList {
  pages: HomePage[];
  next_cursor: string | null;
}

/** The rows of one page of "Recently updated" and "Created by me". */
export const RECENT_PAGE = 20;

/**
 * The favourites the client reads in one request. The star of every page
 * on screen reads this set, so it asks for the most the gateway gives.
 */
export const FAVORITES_LIMIT = 500;

export const RECENT_KEY = "knowledge/recent";
export const CREATED_KEY = "knowledge/recent?mine=1";
export const FAVORITES_KEY = "knowledge/favorites";

/** The query of a home list. Pure, so the test reads it. */
export function homeQuery(opts: { cursor?: string | null; limit?: number; mine?: boolean }): string {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.mine) params.set("mine", "true");
  if (opts.cursor) params.set("cursor", opts.cursor);
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** Recently updated: the live pages the member sees, newest change first. */
export function listRecent(opts: { cursor?: string | null; mine?: boolean } = {}): Promise<HomeList> {
  return call<HomeList>(`/recent${homeQuery({ ...opts, limit: RECENT_PAGE })}`);
}

/** The member's favourites that the member still sees, newest star first. */
export function listFavorites(): Promise<HomeList> {
  return call<HomeList>(`/favorites${homeQuery({ limit: FAVORITES_LIMIT })}`);
}

/** Star a page. A second star changes nothing. */
export function starPage(id: string): Promise<{ page_id: string; favorite: boolean }> {
  return call(`/pages/${encodeURIComponent(id)}/favorite`, { method: "POST" });
}

/** Remove the star. A page with no star answers 204 too. */
export function unstarPage(id: string): Promise<void> {
  return call<void>(`/pages/${encodeURIComponent(id)}/favorite`, { method: "DELETE" });
}

/**
 * Set the icon of a page, or clear it with `null`. The gateway writes no
 * version for a change of the icon alone, and keeps `updated_at`.
 */
export function setPageIcon(id: string, icon: string | null): Promise<PageSummary> {
  return call<PageSummary>(`/pages/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ icon: icon ?? "" }),
  });
}

/** The ids of a favourites answer, for the stars on screen. */
export function favoriteIds(list: HomeList | null | undefined): Set<string> {
  return new Set((list?.pages ?? []).map((p) => p.id));
}
