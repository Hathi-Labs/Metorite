// ── Links between imported and exported pages (WS-61 KB-2m) ──────────────────
//
// Pure. An export writes a link to another exported page as a relative file
// link, and an import turns such a link back into `/knowledge/pages/<id>`.
// Notion writes the same relative links, URL-encoded.

import { mapOutsideCode } from "./names";

/** An inline Markdown link, not an image: `[text](dest "title")`. */
const LINK = /(?<!!)\[((?:[^\]\\]|\\.)*)\]\(\s*(<[^>]*>|[^)\s]*)((?:\s+"[^"]*")?)\s*\)/g;

/** A destination that leaves the file tree: a scheme, `//`, `/` or `#`. */
function isOutside(dest: string): boolean {
  return /^([a-z][a-z0-9+.-]*:|\/|#)/i.test(dest);
}

/** Resolve `rel` against the folder of `fromPath`. Null when it leaves the root. */
export function resolveRelative(fromPath: string, rel: string): string | null {
  const parts = fromPath.split("/").slice(0, -1);
  for (const part of rel.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else parts.push(part);
  }
  return parts.join("/");
}

function decodePath(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

/** The part before `#` and `?`, and the tail from there. */
function splitTail(dest: string): [string, string] {
  const i = dest.search(/[?#]/);
  return i < 0 ? [dest, ""] : [dest.slice(0, i), dest.slice(i)];
}

/** The address of a page inside Knowledge. */
export function pagePath(id: string): string {
  return `/knowledge/pages/${encodeURIComponent(id)}`;
}

/**
 * Import: each relative link to a file of the import becomes the address of
 * the page made from it. `idOf` answers a resolved path, or `undefined` when
 * the file is not part of the import. Other links stay as written.
 */
export function rewriteLinksToIds(
  md: string,
  fromPath: string,
  idOf: (path: string) => string | undefined,
): { md: string; count: number } {
  let count = 0;
  const out = mapOutsideCode(md, (part) =>
    part.replace(LINK, (whole, text: string, rawDest: string, title: string) => {
      const dest = rawDest.replace(/^<|>$/g, "");
      if (!dest || isOutside(dest)) return whole;
      const [file, tail] = splitTail(dest);
      const target = resolveRelative(fromPath, decodePath(file));
      const id = target === null ? undefined : idOf(target);
      if (id === undefined) return whole;
      count += 1;
      return `[${text}](${pagePath(id)}${tail}${title})`;
    }),
  );
  return { md: out, count };
}

/** One path part in a link: URL-encoded, and the parentheses too. */
function encodePart(part: string): string {
  return encodeURIComponent(part).replace(/[()]/g, (c) => (c === "(" ? "%28" : "%29"));
}

/** The relative link from one file to another. Both are full paths in the zip. */
export function relativeLink(fromPath: string, toPath: string): string {
  const from = fromPath.split("/").slice(0, -1);
  const to = toPath.split("/");
  let common = 0;
  while (common < from.length && common < to.length - 1 && from[common] === to[common]) common += 1;
  const up = from.length - common;
  const parts = [...Array<string>(up).fill(".."), ...to.slice(common)];
  return parts.map(encodePart).join("/");
}

/** The id in a `/knowledge/pages/<id>` destination, with its tail. */
const PAGE_DEST = /^\/knowledge\/pages\/([^/?#]+)([?#].*)?$/;

/**
 * Export: each link to a page in the export becomes a relative file link.
 * `pathOf` answers the file path of a page id, or `undefined` for a page that
 * is not in the export. That link stays an absolute address.
 */
export function rewriteLinksToFiles(
  md: string,
  fromPath: string,
  pathOf: (id: string) => string | undefined,
): { md: string; count: number } {
  let count = 0;
  const out = mapOutsideCode(md, (part) =>
    part.replace(LINK, (whole, text: string, rawDest: string, title: string) => {
      const m = PAGE_DEST.exec(rawDest.replace(/^<|>$/g, ""));
      if (!m) return whole;
      const target = pathOf(decodePath(m[1]));
      if (target === undefined) return whole;
      count += 1;
      return `[${text}](${relativeLink(fromPath, target)}${m[2] ?? ""}${title})`;
    }),
  );
  return { md: out, count };
}
