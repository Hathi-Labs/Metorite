// ── The history of a page (WS-61 KB-2g, `knowledge_base.md` §21.7) ──────────
//
// Pure. The shape of one old version, and the rules of the history modal:
// which version a view compares with, how two picked versions make a
// compare, and the words of a row.

import type { PageVersion } from "./types";

/**
 * `GET /knowledge/pages/{id}/versions/{n}` (`routes/knowledge/history.py`):
 * one version with its title and its body. The gateway answers 404 for a
 * page the member cannot see, and for a version from before the last move to
 * another space (the history floor).
 */
export interface PageVersionBody {
  page_id: string;
  version: number;
  title: string;
  body_md: string;
  author_email: string;
  reason: string;
  via: string;
  created_at: string | null;
}

/** Two versions to compare. `newer` draws, and `older` is what it differs from. */
export interface ComparePair {
  newer: number;
  older: number;
}

/**
 * The pair that two picked versions make. A higher number is newer. Null
 * unless exactly two different versions are picked.
 */
export function comparePair(picked: readonly number[]): ComparePair | null {
  if (picked.length !== 2 || picked[0] === picked[1]) return null;
  const [x, y] = picked;
  return x > y ? { newer: x, older: y } : { newer: y, older: x };
}

/**
 * The version a single view compares with: the one before it in the list.
 * The list is newest first, as the gateway sends it. Null for the oldest
 * version the member can read.
 */
export function previousVersion(versions: readonly PageVersion[], n: number): number | null {
  const at = versions.findIndex((v) => v.version === n);
  if (at < 0) return null;
  return versions[at + 1]?.version ?? null;
}

/** Pick or unpick a version for a compare. A third pick is refused. */
export function togglePick(picked: readonly number[], n: number): number[] {
  if (picked.includes(n)) return picked.filter((p) => p !== n);
  if (picked.length >= 2) return [...picked];
  return [...picked, n];
}

/**
 * The date and time of a version, the same for a reader in India and in the
 * US (DESIGN_SYSTEM.md §10): "19 Oct 2026, 14:05". Empty for no date.
 */
export function versionDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const day = at.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day}, ${time}`;
}

/** The name part of an author's email, for a row: "asha.rao" from "asha.rao@x.com". */
export function authorName(email: string): string {
  const name = email.split("@")[0]?.trim();
  return name || email;
}

/** The cache key of one version (`src/lib/dataCache.ts`). Under the app's prefix. */
export function versionKey(pageId: string, n: number): string {
  return `knowledge/page/${pageId}/version/${n}`;
}
