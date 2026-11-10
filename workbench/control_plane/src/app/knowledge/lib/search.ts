// ── The Search view's rules (WS-61 KB-3b, `knowledge_base.md` §6.3, §11.1) ──
//
// Pure, so that each rule has a fence with no DOM: which words go to the
// gateway, which answer is still current, how the hits group, and which parts
// of a text the view marks. The view (`components/KnowledgeSearch.tsx`) only
// draws what these return.

import type { SearchHit } from "./types";

/** The words of a search, in characters. The gateway's `MIN_Q` and `MAX_Q`. */
export const SEARCH_MIN = 2;
export const SEARCH_MAX = 300;

/** The wait after the last keystroke before the view asks. */
export const SEARCH_DEBOUNCE_MS = 300;

/** The id of the box, so the bar's Search tool and the rail can focus it. */
export const SEARCH_INPUT_ID = "knowledge-search-input";

/** The snippets one page row shows at most. */
export const SNIPPETS_PER_PAGE = 2;

/**
 * The words as the gateway reads them: runs of white space become one space,
 * and the ends are trimmed. This is `" ".join(q.split())` in `search.py`.
 */
export function normalizeQuery(raw: string): string {
  return raw.split(/\s+/).filter(Boolean).join(" ");
}

/**
 * What the view does with these words.
 *
 * - `empty`: nothing typed. The view shows the first hint.
 * - `short`: fewer than 2 characters. No request, and a short hint.
 * - `long`: more than 300 characters. No request, because the gateway
 *   answers 422.
 * - `ok`: the view asks.
 *
 * The length counts code points, as Python's `len` does.
 */
export type QueryState = "empty" | "short" | "long" | "ok";

export function queryState(raw: string): QueryState {
  const words = normalizeQuery(raw);
  const n = Array.from(words).length;
  if (n === 0) return "empty";
  if (n < SEARCH_MIN) return "short";
  if (n > SEARCH_MAX) return "long";
  return "ok";
}

/**
 * Is an answer still for the words in the box? Two requests have no order,
 * so a slow answer for "ref" can land after the answer for "refund". The
 * view drops an answer whose `query` is not the live query. This is the
 * `isCurrent` rule of the Projects palette, with the gateway's white space.
 */
export function isCurrent(answerQuery: string, liveQuery: string): boolean {
  return normalizeQuery(answerQuery) === normalizeQuery(liveQuery);
}

/** True for the rejection of a request that a newer keystroke cancelled. */
export function isAbort(err: unknown): boolean {
  return typeof err === "object" && err !== null && (err as { name?: unknown }).name === "AbortError";
}

// ── Grouping ────────────────────────────────────────────────────────────────

/** One snippet of a page: where it sits, and the matched text. */
export interface Snippet {
  headingPath: string;
  text: string;
}

/** One row of the view: a page, with its best score and up to two snippets. */
export interface PageGroup {
  pageId: string;
  title: string;
  /** The page icon (KB-2f), or `null`. */
  icon: string | null;
  best: number;
  snippets: Snippet[];
}

/** One space of the view, with its pages in the order of their best hit. */
export interface SpaceGroup {
  spaceId: string;
  spaceName: string;
  best: number;
  pages: PageGroup[];
}

/**
 * Group the hits by space, then by page.
 *
 * A space comes before another when its best hit scores higher. Inside a
 * space, a page comes before another by the same rule. A page shows once,
 * with at most two snippets, and the same snippet twice shows once. A tie
 * keeps the gateway's order, which is already by score.
 */
export function groupHits(hits: readonly SearchHit[]): SpaceGroup[] {
  const spaces = new Map<string, SpaceGroup>();
  const pages = new Map<string, PageGroup>();
  for (const hit of hits) {
    let space = spaces.get(hit.space_id);
    if (!space) {
      space = { spaceId: hit.space_id, spaceName: hit.space_name, best: hit.score, pages: [] };
      spaces.set(hit.space_id, space);
    }
    space.best = Math.max(space.best, hit.score);
    let page = pages.get(hit.page_id);
    if (!page) {
      page = { pageId: hit.page_id, title: hit.title, icon: hit.icon ?? null, best: hit.score, snippets: [] };
      pages.set(hit.page_id, page);
      space.pages.push(page);
    }
    page.best = Math.max(page.best, hit.score);
    const snippet = { headingPath: hit.heading_path ?? "", text: hit.text ?? "" };
    const seen = page.snippets.some((s) => s.headingPath === snippet.headingPath && s.text === snippet.text);
    if (!seen && page.snippets.length < SNIPPETS_PER_PAGE) page.snippets.push(snippet);
  }
  // `Array.prototype.sort` is stable, so a tie keeps the first-seen order.
  for (const space of spaces.values()) space.pages.sort((a, b) => b.best - a.best);
  return [...spaces.values()].sort((a, b) => b.best - a.best);
}

/** "1 page in 1 space", "4 pages in 2 spaces". */
export function resultCount(groups: readonly SpaceGroup[]): string {
  const pages = groups.reduce((n, g) => n + g.pages.length, 0);
  const spaces = groups.length;
  return `${pages} ${pages === 1 ? "page" : "pages"} in ${spaces} ${spaces === 1 ? "space" : "spaces"}`;
}

// ── Highlighting ────────────────────────────────────────────────────────────

/** One part of a text. The view draws a `match` part in a `<mark>`. */
export interface Segment {
  text: string;
  match: boolean;
}

/** The query syntax of `websearch_to_tsquery` that is not a word to find. */
const OPERATOR_WORDS = new Set(["or", "and"]);

/**
 * The words of a query that the view marks in a text.
 *
 * Quotes go, a word with a leading "-" goes (the search excludes it), and
 * "or" goes. A word of one character goes too, or every "a" would be marked.
 * The longer word comes first, so "refund" wins over "re" in one match.
 */
export function highlightWords(query: string): string[] {
  const words = normalizeQuery(query.replace(/["“”]/g, " "))
    .split(" ")
    .filter((w) => w && !w.startsWith("-"))
    .filter((w) => !OPERATOR_WORDS.has(w.toLowerCase()))
    .filter((w) => Array.from(w).length >= 2);
  const unique = [...new Map(words.map((w) => [w.toLowerCase(), w])).values()];
  return unique.sort((a, b) => b.length - a.length);
}

/**
 * The parts of `text` around every word of `query`, for the view to draw.
 *
 * It returns text and never HTML, so no part of a page body reaches the DOM
 * as markup. Each word is escaped before it becomes a pattern, so a query of
 * `a+b` or `(draft)` matches itself. The match has no case. Modelled on
 * `highlight()` in `app/projects/lib/search.ts`, which takes the whole query
 * as one needle. This one takes each word.
 */
export function highlight(text: string, query: string): Segment[] {
  const words = highlightWords(query);
  if (!text) return [];
  if (!words.length) return [{ text, match: false }];
  const pattern = new RegExp(`(${words.map(escapeRegExp).join("|")})`, "gi");
  // With one capture group, `split` puts each match at an odd index.
  return text
    .split(pattern)
    .map((part, i) => ({ text: part, match: i % 2 === 1 }))
    .filter((part) => part.text !== "");
}

function escapeRegExp(word: string): string {
  return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
