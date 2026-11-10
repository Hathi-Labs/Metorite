// ── Run an export (WS-61 KB-2m) ──────────────────────────────────────────────
//
// Reads through the existing reads (`listPages`, `getPage`), so a member
// exports only the pages that the server shows them. Attachments wait for
// KB-2c.

import { buildTree, type TreeNode } from "../tree";
import type { PageSummary } from "../types";
import { buildFiles, layoutExport, type ExportFormat, type ExportNode } from "./exportPages";
import type { ZipFileSpec } from "./zip";

export interface ExportReads {
  listPages: (spaceId: string) => Promise<PageSummary[]>;
  getPage: (id: string) => Promise<{ id: string; body_md: string }>;
}

export interface ExportRequest {
  spaceId: string;
  /** The page to export, or `null` for the whole space. */
  root: { id: string; title: string } | null;
  withSub: boolean;
  format: ExportFormat;
}

export interface ExportHooks {
  onProgress?: (done: number, total: number) => void;
  shouldCancel?: () => boolean;
}

export interface ExportResult {
  files: ZipFileSpec[];
  /** Pages that could not be read, and why. Their files are not in the export. */
  failed: Array<{ title: string; reason: string }>;
  cancelled: boolean;
  /** The pages in the export. */
  pages: number;
}

const PARALLEL = 4;

function toExportNode(node: TreeNode, withSub: boolean): ExportNode {
  return {
    id: node.page.id,
    title: node.page.title,
    children: withSub ? node.children.map((child) => toExportNode(child, true)) : [],
  };
}

function findNode(nodes: readonly TreeNode[], id: string): TreeNode | null {
  for (const node of nodes) {
    if (node.page.id === id) return node;
    const inner = findNode(node.children, id);
    if (inner) return inner;
  }
  return null;
}

/** Read the pages and build the files. `renderHtml` is needed for HTML only. */
export async function runExport(
  req: ExportRequest,
  reads: ExportReads,
  renderHtml: (md: string) => string,
  hooks: ExportHooks = {},
): Promise<ExportResult> {
  const needList = req.root === null || req.withSub;
  const tree = needList ? buildTree(await reads.listPages(req.spaceId)) : [];
  let roots: ExportNode[];
  if (req.root === null) roots = tree.map((n) => toExportNode(n, true));
  else {
    const node = req.withSub ? findNode(tree, req.root.id) : null;
    roots = [node ? toExportNode(node, true) : { id: req.root.id, title: req.root.title, children: [] }];
  }
  const layout = layoutExport(roots, req.format);
  const total = layout.order.length;
  const bodies = new Map<string, string>();
  const failed: ExportResult["failed"] = [];
  let done = 0;
  let cancelled = false;
  let next = 0;
  hooks.onProgress?.(0, total);
  async function worker() {
    while (next < total) {
      if (hooks.shouldCancel?.()) {
        cancelled = true;
        return;
      }
      const id = layout.order[next++];
      try {
        bodies.set(id, (await reads.getPage(id)).body_md ?? "");
      } catch (err) {
        failed.push({ title: layout.titles.get(id) ?? id, reason: err instanceof Error ? err.message : String(err) });
      }
      done += 1;
      hooks.onProgress?.(done, total);
    }
  }
  await Promise.all(Array.from({ length: Math.min(PARALLEL, total) }, worker));
  return {
    files: cancelled ? [] : buildFiles(layout, bodies, req.format, renderHtml),
    failed,
    cancelled,
    pages: bodies.size,
  };
}
