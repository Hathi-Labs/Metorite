// ── The plan of an import (WS-61 KB-2m) ──────────────────────────────────────
//
// Pure. Page files in, an ordered list of pages out: each page names its
// parent, and a parent comes before its children. The rules:
//
//  - Each page file is a page. Its title is its first heading, else its name.
//  - A folder that has the base name of a page file beside it (Notion:
//    `Title <32-hex>.md` and `Title <32-hex>/`) holds the children of that
//    page.
//  - Any other folder is a parent page with an empty body.
//  - Notion's trailing 32-hex id leaves every title.

import { ImportRefusal, MAX_DEPTH, MAX_PAGES } from "./limits";
import { dirName, baseName, cleanTitle, imagesToText, pageKind, splitMarkdownTitle, stemOf, stripNotionId, titleFromPath } from "./names";
import type { RawEntry } from "./zip";

/** What the HTML converter answers. It needs a browser, so the caller gives it. */
export interface HtmlResult {
  title: string | null;
  markdown: string;
  images: number;
}
export type HtmlConverter = (html: string) => HtmlResult;

/** One file read as a page: a title and a Markdown body. */
export interface PageDraft {
  path: string;
  title: string;
  body: string;
  images: number;
}

/** A page file as a draft. Markdown keeps its text, and HTML goes through `html`. */
export function toDraft(entry: RawEntry, html: HtmlConverter): PageDraft {
  if (pageKind(entry.path) === "html") {
    const out = html(entry.text);
    return {
      path: entry.path,
      title: out.title?.trim() ? cleanTitle(out.title) : titleFromPath(entry.path),
      body: out.markdown,
      images: out.images,
    };
  }
  const split = splitMarkdownTitle(entry.text);
  const pictures = imagesToText(split.body);
  return {
    path: entry.path,
    title: split.title ? cleanTitle(split.title) : titleFromPath(entry.path),
    body: pictures.md,
    images: pictures.count,
  };
}

/** One page that the import will make. */
export interface PlanPage {
  /** The place in `ImportPlan.pages`. */
  index: number;
  /** The file path, or the folder path for a folder page. */
  path: string;
  title: string;
  body: string;
  /** The index of the parent in the plan, or `null` for the top. */
  parent: number | null;
  folder: boolean;
  depth: number;
}

export interface ImportPlan {
  /** Parents come before their children. */
  pages: PlanPage[];
  /** Files that are not pages. */
  skipped: string[];
  /** Images turned into text. */
  images: number;
}

const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

/** The plan for these drafts. Refuses over the page or depth limit. */
export function buildPlan(drafts: readonly PageDraft[], skipped: readonly string[] = []): ImportPlan {
  if (!drafts.length) {
    throw new ImportRefusal("The files hold no Markdown or HTML page. Nothing was imported.");
  }
  const ordered = [...drafts].sort((a, b) => collator.compare(a.path, b.path));
  // The first page file with a given stem owns the folder of that name.
  const owner = new Map<string, number>();
  ordered.forEach((d, i) => {
    const stem = stemOf(d.path);
    if (!owner.has(stem)) owner.set(stem, i);
  });
  // Every folder above a page file.
  const dirs = new Set<string>();
  for (const d of ordered) {
    let dir = dirName(d.path);
    while (dir && !dirs.has(dir)) {
      dirs.add(dir);
      dir = dirName(dir);
    }
  }
  const pages: PlanPage[] = ordered.map((d, i) => ({
    index: i, path: d.path, title: d.title, body: d.body, parent: null, folder: false, depth: 1,
  }));
  // The page that holds a folder: its owner, or a new folder page.
  const holder = new Map<string, number>();
  const byDepth = [...dirs].sort((a, b) => a.split("/").length - b.split("/").length || collator.compare(a, b));
  for (const dir of byDepth) {
    const own = owner.get(dir);
    if (own !== undefined) {
      holder.set(dir, own);
      continue;
    }
    const index = pages.length;
    pages.push({
      index, path: dir, title: cleanTitle(stripNotionId(baseName(dir))), body: "", parent: null, folder: true, depth: 1,
    });
    holder.set(dir, index);
  }
  for (const page of pages) {
    const dir = dirName(page.path);
    page.parent = dir ? (holder.get(dir) ?? null) : null;
  }
  if (pages.length > MAX_PAGES) {
    throw new ImportRefusal(`The import would make ${pages.length} pages. The limit is ${MAX_PAGES}. Nothing was imported.`);
  }
  const depthOf = (page: PlanPage, seen = 0): number => {
    if (page.parent === null) return 1;
    if (seen > MAX_DEPTH + 1) return seen;
    return depthOf(pages[page.parent], seen + 1) + 1;
  };
  for (const page of pages) {
    page.depth = depthOf(page);
    if (page.depth > MAX_DEPTH) {
      throw new ImportRefusal(`"${page.title}" sits ${page.depth} levels deep. The limit is ${MAX_DEPTH}. Nothing was imported.`);
    }
  }
  // Parents first. A stable sort keeps the path order among siblings.
  const sorted = [...pages].sort((a, b) => a.depth - b.depth || collator.compare(a.path, b.path));
  const remap = new Map(sorted.map((p, i) => [p.index, i]));
  const final = sorted.map((p, i) => ({
    ...p,
    index: i,
    parent: p.parent === null ? null : (remap.get(p.parent) as number),
  }));
  return { pages: final, skipped: [...skipped], images: ordered.reduce((n, d) => n + d.images, 0) };
}
