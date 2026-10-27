// ── The files of an export (WS-61 KB-2m) ─────────────────────────────────────
//
// Pure. A page is one file. A page with children also has a folder of the
// same name beside it, which is the layout that Notion writes and that the
// import reads, so an export imports back with the same tree.

import { rewriteLinksToFiles } from "./links";
import { MAX_TITLE } from "./limits";
import type { ZipFileSpec } from "./zip";

export type ExportFormat = "md" | "html";

/** A page of the export with its children. */
export interface ExportNode {
  id: string;
  title: string;
  children: ExportNode[];
}

/** Names that Windows keeps for devices. */
const RESERVED = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

/**
 * A title as a file name, with no extension. The characters that a file
 * system refuses, and control characters, become "-". An empty result is
 * "Untitled".
 */
export function fileStem(title: string): string {
  // eslint-disable-next-line no-control-regex
  let stem = title.replace(/[\u0000-\u001f\u007f/\\:*?"<>|]/g, "-").replace(/\s+/g, " ").trim();
  stem = stem.replace(/^\.+/, "").replace(/[. ]+$/, "").slice(0, Math.min(100, MAX_TITLE)).trim();
  if (!stem) return "Untitled";
  return RESERVED.test(stem) ? `${stem}_` : stem;
}

/** The file path of every page in the export, by page id. */
export interface ExportLayout {
  /** Page ids in the order of the walk, parents first. */
  order: string[];
  paths: Map<string, string>;
  titles: Map<string, string>;
}

/** The layout of these roots. A duplicate name in one folder gets a number. */
export function layoutExport(roots: readonly ExportNode[], format: ExportFormat): ExportLayout {
  const layout: ExportLayout = { order: [], paths: new Map(), titles: new Map() };
  const place = (nodes: readonly ExportNode[], dir: string) => {
    const used = new Set<string>();
    for (const node of nodes) {
      const base = fileStem(node.title);
      let stem = base;
      for (let n = 2; used.has(stem.toLowerCase()); n++) stem = `${base} ${n}`;
      used.add(stem.toLowerCase());
      layout.order.push(node.id);
      layout.paths.set(node.id, `${dir}${stem}.${format}`);
      layout.titles.set(node.id, node.title);
      if (node.children.length) place(node.children, `${dir}${stem}/`);
    }
  };
  place(roots, "");
  return layout;
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** A minimal HTML document: a title, the heading, the body, and no script. */
export function htmlDocument(title: string, bodyHtml: string): string {
  return (
    `<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>${escapeHtml(title)}</title>\n</head>\n` +
    `<body>\n<h1>${escapeHtml(title)}</h1>\n${bodyHtml}\n</body>\n</html>\n`
  );
}

/** A Markdown file: the title as a heading, then the body. */
export function markdownDocument(title: string, body: string): string {
  const text = body.replace(/\s+$/, "");
  return text ? `# ${title}\n\n${text}\n` : `# ${title}\n`;
}

/**
 * The file of each page. `bodies` holds the Markdown body of each page, and a
 * page with no entry is left out. `renderHtml` turns a Markdown body into
 * HTML, and it needs a browser, so the caller gives it.
 */
export function buildFiles(
  layout: ExportLayout,
  bodies: ReadonlyMap<string, string>,
  format: ExportFormat,
  renderHtml: (md: string) => string,
): ZipFileSpec[] {
  const files: ZipFileSpec[] = [];
  const pathOf = (id: string) => layout.paths.get(id);
  for (const id of layout.order) {
    const body = bodies.get(id);
    const path = layout.paths.get(id);
    if (body === undefined || path === undefined) continue;
    const title = layout.titles.get(id) ?? "Untitled";
    const linked = rewriteLinksToFiles(body, path, pathOf).md;
    files.push({
      path,
      text: format === "md" ? markdownDocument(title, linked) : htmlDocument(title, renderHtml(linked)),
    });
  }
  return files;
}
