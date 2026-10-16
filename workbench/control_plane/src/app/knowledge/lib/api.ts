// ── The /knowledge client (WS-61 KB-2a) ─────────────────────────────────────
//
// Every request goes through the BFF at /api/knowledge/* and never to the
// gateway directly, which carries no session (workbench/AGENTS.md,
// "Identity").
//
// A refusal keeps its HTTP status. The status IS the message here: a 404 on
// `GET /knowledge/spaces` means the app is off for the organization, a 403
// means the member has no `feature:knowledge`, and a 404 on one page means
// only that the page is not there for this member (`phase.ts`).

import type {
  PageCreateBody,
  PageDetail,
  PagePatchBody,
  PageRevertBody,
  PageSummary,
  PageWritten,
  SearchAnswer,
  Space,
} from "./types";

export class KnowledgeError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "KnowledgeError";
    this.status = status;
  }
}

/** The BFF prefix. Exported for the e2e spec and the tests. */
export const KNOWLEDGE_API = "/api/knowledge";

/**
 * FastAPI's `detail` is a string for an HTTPException and a list of objects
 * for a validation failure. Draw the words, never "[object Object]".
 */
export function detailOf(body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) =>
        typeof item === "object" && item && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : String(item),
      )
      .join("; ");
  }
  return "";
}

/** The one way to call the gateway. A second `fetch` path is a defect. */
export async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${KNOWLEDGE_API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new KnowledgeError(detailOf(body) || `Knowledge answered ${res.status}`, res.status);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** The spaces the member can open, each with its count of visible pages. */
export async function listSpaces(): Promise<Space[]> {
  const out = await call<{ spaces: Space[] }>("/spaces");
  return out?.spaces ?? [];
}

/** The live pages of one space, with no body. */
export async function listPages(spaceId: string): Promise<PageSummary[]> {
  const out = await call<{ pages: PageSummary[] }>(`/pages?space=${encodeURIComponent(spaceId)}`);
  return out?.pages ?? [];
}

/** One page with its body, its versions and `can_write`. */
export function getPage(id: string): Promise<PageDetail> {
  return call<PageDetail>(`/pages/${encodeURIComponent(id)}`);
}

/** Make a draft. With no `space_id`, the gateway puts it in `me`. */
export function createPage(body: PageCreateBody): Promise<PageWritten> {
  return call<PageWritten>("/pages", { method: "POST", body: JSON.stringify(body) });
}

/** Edit the title or the body. The gateway writes one version. */
export function updatePage(id: string, body: PagePatchBody): Promise<PageWritten> {
  return call<PageWritten>(`/pages/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Publish a draft. */
export function publishPage(id: string): Promise<PageWritten> {
  return call<PageWritten>(`/pages/${encodeURIComponent(id)}/publish`, { method: "POST" });
}

/** The body of a revert: the number of the version to write again. */
export function revertBody(version: number): PageRevertBody {
  if (!Number.isInteger(version) || version < 1) {
    throw new RangeError(`A version is a whole number from 1. Got ${version}.`);
  }
  return { version };
}

/** Write an old version's title and body as a new version. */
export function revertPage(id: string, version: number): Promise<PageWritten> {
  return call<PageWritten>(`/pages/${encodeURIComponent(id)}/revert`, {
    method: "POST",
    body: JSON.stringify(revertBody(version)),
  });
}

/** The hits of one search. The view asks for the most the gateway gives. */
export const SEARCH_K = 20;

/**
 * Search the published pages the member can see (KB-3b).
 *
 * It sends `q` and `k` only. `hybrid` stays at the gateway's default, and the
 * view adds no space filter yet. `signal` reaches `fetch`, because `call`
 * spreads `init`, so a newer keystroke cancels this request.
 */
export function searchKnowledge(q: string, opts: { signal?: AbortSignal } = {}): Promise<SearchAnswer> {
  return call<SearchAnswer>(`/search?q=${encodeURIComponent(q)}&k=${SEARCH_K}`, { signal: opts.signal });
}

// ── The read-cache keys (`src/lib/dataCache.ts`) ────────────────────────────
//
// One prefix for the app, so that one `invalidate(KNOWLEDGE_CACHE)` after a
// write re-reads the spaces, the trees and the open page together.

export const KNOWLEDGE_CACHE = "knowledge/";
export const SPACES_KEY = "knowledge/spaces";

export function pagesKey(spaceId: string): string {
  return `knowledge/pages?space=${spaceId}`;
}

export function pageKey(pageId: string): string {
  return `knowledge/page/${pageId}`;
}
