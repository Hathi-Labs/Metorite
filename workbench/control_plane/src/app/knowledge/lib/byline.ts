// ── The byline of a page (WS-61 KB-2d, `knowledge_base.md` §21.7) ──────────
//
// Pure. "Edited by Meera, 3m ago", and the people who wrote the page. Both
// read `versions` from `GET /knowledge/pages/{id}`. The gateway gives only
// the versions from the history floor on (`core.history_floor`), so a page
// that moved from another space names nobody from before the move.

import type { PageVersion } from "./types";

/** One person who wrote a version of the page. */
export interface Contributor {
  email: string;
  /** How many of the versions in view they wrote. */
  versions: number;
  /** When they last wrote one, or `null` when no version has a time. */
  lastAt: string | null;
}

/**
 * The people who wrote the versions in view, the most recent first. Each
 * person shows once. Versions come newest first, as the gateway sends them.
 */
export function contributorsOf(versions: readonly PageVersion[]): Contributor[] {
  const out = new Map<string, Contributor>();
  for (const v of versions) {
    const email = v.author_email?.trim();
    if (!email) continue;
    const seen = out.get(email);
    if (seen) {
      seen.versions += 1;
      if (!seen.lastAt && v.created_at) seen.lastAt = v.created_at;
    } else {
      out.set(email, { email, versions: 1, lastAt: v.created_at ?? null });
    }
  }
  return [...out.values()];
}

/** What the byline says: who wrote the newest version, and when. */
export interface Byline {
  verb: string;
  email: string;
  at: string | null;
}

/** The verb of the byline for each reason. A first version "created" the page. */
const BYLINE_VERBS: Record<string, string> = {
  create: "Created",
  publish: "Published",
  move: "Moved",
  archive: "Archived",
  restore: "Restored",
  revert: "Reverted",
  import: "Imported",
};

/** The byline of the newest version, or `null` for a page with no version in view. */
export function bylineOf(versions: readonly PageVersion[]): Byline | null {
  const last = versions[0];
  if (!last?.author_email) return null;
  return { verb: BYLINE_VERBS[last.reason] ?? "Edited", email: last.author_email, at: last.created_at ?? null };
}

/** "1 version", "3 versions". */
export function versionCount(n: number): string {
  return `${n} ${n === 1 ? "version" : "versions"}`;
}
