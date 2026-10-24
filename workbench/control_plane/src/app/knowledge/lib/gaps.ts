// ── Knowledge gaps (WS-61 KB-9a, `knowledge_base.md` §8.4 door 3) ──────────
//
// The client of `gateway/routes/knowledge/gaps.py`. A gap is a question that
// the assistant asked Knowledge for a member and found no page. The gateway
// writes it; a person closes it here: "Write a page" links the new page, and
// a manager may Dismiss it. Every request goes through `call` in `api.ts`,
// the one way to the gateway. The key sits under `KNOWLEDGE_CACHE`, so one
// `invalidate(KNOWLEDGE_CACHE)` after a write also re-reads the list.

import { KnowledgeError, call } from "./api";
import { KNOWLEDGE_HOME } from "./links";

/** The capability that sees every gap of the org and may dismiss one. */
export const MANAGE_CAPABILITY = "knowledge:manage";

/** The Gaps view. */
export const KNOWLEDGE_GAPS = `${KNOWLEDGE_HOME}/gaps`;

/** The parameter that opens the New page form for one gap (the My Day card). */
export const WRITE_PARAM = "write";

export const GAPS_KEY = "knowledge/gaps";

/** The rows the view asks for: the gateway's maximum. */
export const GAPS_LIMIT = 200;

export type GapStatus = "open" | "answered" | "dismissed";

export interface Gap {
  id: string;
  /** The first wording a member typed. */
  question: string;
  /** The different askers of the current 30-day window. */
  ask_count: number;
  first_asked_at: string | null;
  last_asked_at: string | null;
  status: GapStatus;
  answered_page: { id: string; title: string } | null;
  asked_by_me: boolean;
  dismissed_at: string | null;
}

export interface GapList {
  gaps: Gap[];
  /** `org` for a manager, who sees every gap. `mine` for a member. */
  scope: "org" | "mine";
  /** True when gaps are off for the org: the gateway answered 404. */
  off: boolean;
}

/**
 * The open gaps the member may see. A 404 means gaps are off for the org,
 * and it is a state, not an error: the read cache keeps an error as words
 * only, so the status would be lost there.
 */
export async function listGaps(): Promise<GapList> {
  let out: Omit<GapList, "off"> | undefined;
  try {
    out = await call<Omit<GapList, "off">>(`/gaps?status=open&limit=${GAPS_LIMIT}`);
  } catch (err) {
    if (err instanceof KnowledgeError && err.status === 404) return { gaps: [], scope: "mine", off: true };
    throw err;
  }
  if (!out || !Array.isArray(out.gaps)) throw new Error("The gaps answer has no list.");
  return { gaps: out.gaps, scope: out.scope === "org" ? "org" : "mine", off: false };
}

/** Close a gap with no page. The gateway asks for `knowledge:manage`. */
export function dismissGap(id: string): Promise<Gap> {
  return call<Gap>(`/gaps/${encodeURIComponent(id)}/dismiss`, { method: "POST" });
}

/** Close a gap with the page that answers it: "I wrote it". */
export function markGapAnswered(id: string, pageId: string): Promise<Gap> {
  return call<Gap>(`/gaps/${encodeURIComponent(id)}/answered`, {
    method: "POST",
    body: JSON.stringify({ page_id: pageId }),
  });
}

/** The Gaps view, and with `write` the New page form for that gap. */
export function gapsHref(write?: string | null): string {
  return write ? `${KNOWLEDGE_GAPS}?${WRITE_PARAM}=${encodeURIComponent(write)}` : KNOWLEDGE_GAPS;
}

/** The question as a page title: one line, at most 300 characters, no end mark. */
export function titleFromQuestion(question: string): string {
  const flat = question.replace(/\s+/g, " ").trim();
  const bare = flat.replace(/[?!.。？！]+$/u, "").trim() || flat;
  const cased = bare.charAt(0).toUpperCase() + bare.slice(1);
  return cased.slice(0, 300);
}

/** "Asked by 3 people" or "Asked by 1 person". */
export function askersLabel(count: number): string {
  const n = Math.max(0, Math.floor(count));
  return `Asked by ${n} ${n === 1 ? "person" : "people"}`;
}

/** The count a rail row shows: the open gaps, "99+" past 99, and none at 0. */
export function gapCount(list: GapList | null | undefined): string | null {
  const n = list?.gaps.length ?? 0;
  if (n <= 0) return null;
  return n > 99 ? "99+" : String(n);
}
