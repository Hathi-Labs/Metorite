// ── The page tree of one space (WS-61 KB-2a) ───────────────────────────────
//
// Pure. `GET /knowledge/pages?space=` answers a flat list, and each row names
// its `parent_id`. This file turns the list into the tree the rail draws.
//
// ⚠️ A child whose parent the member cannot see goes to the ROOT. It is never
// dropped. The visibility rule is per page (`core.visible_pages_clause`), so a
// published child can sit under a draft that only its author sees. Dropping
// the child would hide a page the gateway says this member may read.

import type { PageSummary } from "./types";

export interface TreeNode<P extends PageSummary = PageSummary> {
  page: P;
  children: TreeNode<P>[];
}

/** The order of siblings: the gateway's own (`ORDER BY position, lower(title), created_at`). */
export function compareSiblings(a: PageSummary, b: PageSummary): number {
  if (a.position !== b.position) return a.position - b.position;
  const ta = a.title.toLowerCase();
  const tb = b.title.toLowerCase();
  if (ta !== tb) return ta < tb ? -1 : 1;
  const ca = a.created_at ?? "";
  const cb = b.created_at ?? "";
  if (ca !== cb) return ca < cb ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

/**
 * The tree, from `parent_id`, `position` and the title.
 *
 * A page whose parent is not in the list is a root. A loop of parents, which
 * the gateway refuses but a stale list could still hold, is broken at the
 * page the walk meets first, so each page shows exactly once.
 */
export function buildTree<P extends PageSummary>(pages: readonly P[]): TreeNode<P>[] {
  const byId = new Map<string, TreeNode<P>>();
  for (const page of pages) {
    if (!byId.has(page.id)) byId.set(page.id, { page, children: [] });
  }
  const roots: TreeNode<P>[] = [];
  for (const node of byId.values()) {
    const parentId = node.page.parent_id;
    const parent = parentId && parentId !== node.page.id ? byId.get(parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  // A loop has no root, so the walk from the roots never reaches it. Each
  // page the walk misses goes to the root, and its link to the loop is cut.
  const seen = new Set<string>();
  const walk = (node: TreeNode<P>) => {
    seen.add(node.page.id);
    for (const child of node.children) walk(child);
  };
  roots.forEach(walk);
  for (const node of byId.values()) {
    if (seen.has(node.page.id)) continue;
    const parent = byId.get(node.page.parent_id ?? "");
    if (parent) parent.children = parent.children.filter((c) => c !== node);
    roots.push(node);
    walk(node);
  }
  const sort = (nodes: TreeNode<P>[]) => {
    nodes.sort((a, b) => compareSiblings(a.page, b.page));
    nodes.forEach((n) => sort(n.children));
  };
  sort(roots);
  return roots;
}

/**
 * The ids of the pages above `pageId`, nearest first, so the rail can open
 * them. An unknown page, or a loop, stops the walk.
 */
export function ancestorIds(pages: readonly PageSummary[], pageId: string | null): string[] {
  if (!pageId) return [];
  const byId = new Map(pages.map((p) => [p.id, p]));
  const out: string[] = [];
  const seen = new Set<string>([pageId]);
  let at = byId.get(pageId)?.parent_id ?? null;
  while (at && !seen.has(at) && byId.has(at)) {
    out.push(at);
    seen.add(at);
    at = byId.get(at)?.parent_id ?? null;
  }
  return out;
}
