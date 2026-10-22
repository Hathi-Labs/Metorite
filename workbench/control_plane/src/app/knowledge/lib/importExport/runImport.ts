// ── Run an import (WS-61 KB-2m) ──────────────────────────────────────────────
//
// Creates the pages of a plan, parents first, as drafts. The server checks
// the write grant on each create. A page that fails does not stop the others,
// but its children are skipped. A second pass turns each relative link into
// the address of the new page.

import { rewriteLinksToIds } from "./links";
import type { ImportPlan } from "./plan";

/** The two writes that an import needs. The client of `lib/api.ts` gives both. */
export interface ImportWrites {
  create: (body: { space_id: string; parent_id?: string; title: string; body_md?: string }) => Promise<{ id: string }>;
  patch: (id: string, body: { body_md: string }) => Promise<unknown>;
}

export interface ImportTarget {
  spaceId: string;
  /** The page that holds the imported pages, or `null` for the space root. */
  parentId: string | null;
}

export interface ImportHooks {
  onProgress?: (done: number, total: number) => void;
  /** Asked before each page. True stops the run after the current page. */
  shouldCancel?: () => boolean;
}

export interface ImportReport {
  created: number;
  /** A page that the server refused or that failed. */
  failed: Array<{ title: string; reason: string }>;
  /** A page that was not tried, because its parent failed. */
  skippedChildren: Array<{ title: string; reason: string }>;
  /** Pages not tried because the member cancelled. */
  notStarted: number;
  cancelled: boolean;
  /** Links turned into addresses of the new pages. */
  linksRewritten: number;
  /** Pages whose link write failed. */
  linkFailures: number;
  /** Files that were not pages. */
  skippedFiles: number;
  /** Images turned into text. */
  images: number;
  /** The id of the first page made at the top, to open after the run. */
  firstId: string | null;
}

function reasonOf(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Create the pages of the plan. It never throws for one failed page. */
export async function runImport(
  plan: ImportPlan,
  target: ImportTarget,
  writes: ImportWrites,
  hooks: ImportHooks = {},
): Promise<ImportReport> {
  const report: ImportReport = {
    created: 0, failed: [], skippedChildren: [], notStarted: 0, cancelled: false,
    linksRewritten: 0, linkFailures: 0, skippedFiles: plan.skipped.length, images: plan.images, firstId: null,
  };
  const total = plan.pages.length;
  const ids = new Map<number, string>();
  const dead = new Set<number>();
  let done = 0;
  hooks.onProgress?.(0, total);
  for (const page of plan.pages) {
    if (hooks.shouldCancel?.()) {
      report.cancelled = true;
      report.notStarted = total - done;
      break;
    }
    done += 1;
    if (page.parent !== null && dead.has(page.parent)) {
      dead.add(page.index);
      report.skippedChildren.push({ title: page.title, reason: "Its parent page was not made." });
      hooks.onProgress?.(done, total);
      continue;
    }
    const parentId = page.parent === null ? target.parentId : ids.get(page.parent);
    try {
      const made = await writes.create({
        space_id: target.spaceId,
        ...(parentId ? { parent_id: parentId } : {}),
        title: page.title,
        ...(page.body ? { body_md: page.body } : {}),
      });
      ids.set(page.index, made.id);
      report.created += 1;
      if (page.parent === null && report.firstId === null) report.firstId = made.id;
    } catch (err) {
      dead.add(page.index);
      report.failed.push({ title: page.title, reason: reasonOf(err) });
    }
    hooks.onProgress?.(done, total);
  }
  // The second pass: every page exists now, so a link can name its target.
  const byPath = new Map<string, string>();
  for (const page of plan.pages) {
    const id = ids.get(page.index);
    if (id && !page.folder && !byPath.has(page.path)) byPath.set(page.path, id);
  }
  for (const page of plan.pages) {
    const id = ids.get(page.index);
    if (!id || page.folder || !page.body) continue;
    const out = rewriteLinksToIds(page.body, page.path, (path) => byPath.get(path));
    if (!out.count) continue;
    try {
      await writes.patch(id, { body_md: out.md });
      report.linksRewritten += out.count;
    } catch {
      report.linkFailures += 1;
    }
  }
  return report;
}
