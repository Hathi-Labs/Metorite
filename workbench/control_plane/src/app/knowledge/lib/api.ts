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
  PageMoveBody,
  PageMoved,
  PagePatchBody,
  PageRevertBody,
  PageState,
  PageSummary,
  PageWritten,
  SearchAnswer,
  Space,
} from "./types";
import type { PageVersionBody } from "./history";
import type { Proposal } from "./review";
import { updatedAfter, type SearchFilters } from "./searchFilters";

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

/**
 * The pages of one space, with no body. With no `state`, the live pages.
 * `state: "archived"` gives the archived pages that the member can see
 * (KB-2d): the gateway shows them to a manager of the space, and a draft
 * to its author.
 */
export async function listPages(spaceId: string, opts: { state?: PageState } = {}): Promise<PageSummary[]> {
  const state = opts.state ? `&state=${encodeURIComponent(opts.state)}` : "";
  const out = await call<{ pages: PageSummary[] }>(`/pages?space=${encodeURIComponent(spaceId)}${state}`);
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

/** The body of a move. A place is a whole number from 0 (KB-2e). */
export function moveBody(body: PageMoveBody): PageMoveBody {
  const { position } = body;
  if (position !== undefined && (!Number.isInteger(position) || position < 0)) {
    throw new RangeError(`A place is a whole number from 0. Got ${position}.`);
  }
  return body;
}

/** Move a page to another parent, or to another place among its siblings. */
export function movePage(id: string, body: PageMoveBody): Promise<PageMoved> {
  return call<PageMoved>(`/pages/${encodeURIComponent(id)}/move`, {
    method: "POST",
    body: JSON.stringify(moveBody(body)),
  });
}

/** Archive a page. The gateway asks for manage on the page's space. */
export function archivePage(id: string): Promise<PageWritten> {
  return call<PageWritten>(`/pages/${encodeURIComponent(id)}/archive`, { method: "POST" });
}

/** Restore an archived page to its state before the archive. It needs manage too. */
export function restorePage(id: string): Promise<PageWritten> {
  return call<PageWritten>(`/pages/${encodeURIComponent(id)}/restore`, { method: "POST" });
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

/**
 * One version of a page, with its title and body (KB-2g). The gateway
 * answers 404 for a page the member cannot see and under the history floor.
 */
export function getVersion(pageId: string, n: number): Promise<PageVersionBody> {
  if (!Number.isInteger(n) || n < 1) {
    return Promise.reject(new RangeError(`A version is a whole number from 1. Got ${n}.`));
  }
  return call<PageVersionBody>(`/pages/${encodeURIComponent(pageId)}/versions/${n}`);
}

/** One page that links to the open page (KB-2h). */
export interface Backlink {
  id: string;
  title: string;
  icon: string | null;
  space_name: string;
}

/** The published pages the member can see that link to this page (KB-2h). */
export async function getBacklinks(pageId: string): Promise<Backlink[]> {
  const out = await call<{ backlinks: Backlink[] }>(`/pages/${encodeURIComponent(pageId)}/backlinks`);
  return out.backlinks;
}

/** The hits of one search. The view asks for the most the gateway gives. */
export const SEARCH_K = 20;

/**
 * Search the published pages the member can see (KB-3b).
 *
 * It sends `q` and `k`, and one parameter for each filter that is set
 * (`space`, `author`, `updated_after`, `title_only`). `hybrid` stays at the
 * gateway's default. `signal` reaches `fetch`, because `call`
 * spreads `init`, so a newer keystroke cancels this request.
 */
export function searchKnowledge(
  q: string,
  opts: { signal?: AbortSignal; filters?: SearchFilters; now?: Date } = {},
): Promise<SearchAnswer> {
  let url = `/search?q=${encodeURIComponent(q)}&k=${SEARCH_K}`;
  // KB-3d: each filter adds its parameter, and an unset filter adds none.
  const f = opts.filters;
  if (f?.space) url += `&space=${encodeURIComponent(f.space)}`;
  if (f?.author) url += `&author=${encodeURIComponent(f.author)}`;
  const after = updatedAfter(f?.date ?? null, opts.now);
  if (after) url += `&updated_after=${encodeURIComponent(after)}`;
  if (f?.titleOnly) url += "&title_only=true";
  return call<SearchAnswer>(url, { signal: opts.signal });
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

/** The archived pages of one space (KB-2d). */
export function archivedPagesKey(spaceId: string): string {
  return `knowledge/pages?space=${spaceId}&state=archived`;
}

export function pageKey(pageId: string): string {
  return `knowledge/page/${pageId}`;
}

/** The pages that link to one page (KB-2h). Under the prefix, so a write re-reads it. */
export function backlinksKey(pageId: string): string {
  return `knowledge/page/${pageId}/backlinks`;
}

// ── The proposals of the assistant (KB-5b) ──────────────────────────────────

/** The proposals that wait for the member. Page proposals first, then edits. */
export async function listProposals(): Promise<Proposal[]> {
  const out = await call<{ proposals: Proposal[] }>("/proposals?status=pending");
  return out?.proposals ?? [];
}

/** Accept a proposal: a page publishes, and an edit applies. Web only (F2). */
export function acceptProposal(id: string): Promise<unknown> {
  return call<unknown>(`/proposals/${encodeURIComponent(id)}/accept`, { method: "POST" });
}

/** Reject a proposal. A page archives as a draft, and an edit is marked. */
export function rejectProposal(id: string): Promise<unknown> {
  return call<unknown>(`/proposals/${encodeURIComponent(id)}/reject`, { method: "POST" });
}
