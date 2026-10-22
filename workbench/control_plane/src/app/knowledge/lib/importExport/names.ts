// ── Titles, file names and the shape of a Markdown file (WS-61 KB-2m) ────────
//
// Pure. The import names a page from its file and the export names a file
// from its page. Notion adds a 32-character id to each name, and the import
// takes it off.

import { MAX_TITLE } from "./limits";

/** The page file kinds that an import reads. */
export const MARKDOWN_EXT = [".md", ".markdown"] as const;
export const HTML_EXT = [".html", ".htm"] as const;

export type PageKind = "md" | "html";

/** The last part of a path. */
export function baseName(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? path : path.slice(i + 1);
}

/** The folder part of a path, with no trailing `/`. The top is "". */
export function dirName(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

/** The kind of page file that a name is, or `null` for any other file. */
export function pageKind(name: string): PageKind | null {
  const lower = name.toLowerCase();
  if (MARKDOWN_EXT.some((ext) => lower.endsWith(ext))) return "md";
  if (HTML_EXT.some((ext) => lower.endsWith(ext))) return "html";
  return null;
}

/** A file path with its page extension off. Another extension stays. */
export function stemOf(path: string): string {
  if (pageKind(path) === null) return path;
  return path.slice(0, path.lastIndexOf("."));
}

/** Notion's trailing id: a space and 32 hex characters. */
const NOTION_ID = /\s+[0-9a-f]{32}$/i;

/** A name with Notion's trailing id off. A name that is only an id stays. */
export function stripNotionId(name: string): string {
  const stripped = name.replace(NOTION_ID, "").trim();
  return stripped || name;
}

/** A title for a page: no edge space, at most `MAX_TITLE` characters, never empty. */
export function cleanTitle(title: string): string {
  const one = title.replace(/\s+/g, " ").trim().slice(0, MAX_TITLE).trim();
  return one || "Untitled";
}

/** The title that a file name gives: no folder, no extension, no Notion id. */
export function titleFromPath(path: string): string {
  return cleanTitle(stripNotionId(stemOf(baseName(path))));
}

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;

/**
 * Apply `fn` to each line outside a fenced code block, and to each part of the
 * line outside an inline code span. Returns the new text.
 */
export function mapOutsideCode(md: string, fn: (text: string) => string): string {
  const lines = md.split("\n");
  let fence: string | null = null;
  const out = lines.map((line) => {
    const m = FENCE.exec(line);
    if (fence) {
      if (m && m[1][0] === fence[0] && m[1].length >= fence.length && line.trim() === m[1]) fence = null;
      return line;
    }
    if (m) {
      fence = m[1];
      return line;
    }
    return line
      .split(/(`+[^`]*`+)/)
      .map((part, i) => (i % 2 === 1 ? part : fn(part)))
      .join("");
  });
  return out.join("\n");
}

/**
 * The first `# heading` of a Markdown file, and the file without it. The
 * search skips fenced code and a front matter block at the top. A file with
 * no heading gives `title: null` and the same text.
 */
export function splitMarkdownTitle(md: string): { title: string | null; body: string } {
  const lines = md.split("\n");
  let i = 0;
  if (/^---[ \t]*$/.test(lines[0] ?? "")) {
    for (let j = 1; j < lines.length; j++) {
      if (/^(---|\.\.\.)[ \t]*$/.test(lines[j])) {
        i = j + 1;
        break;
      }
    }
  }
  let fence: string | null = null;
  for (; i < lines.length; i++) {
    const line = lines[i];
    const m = FENCE.exec(line);
    if (fence) {
      if (m && m[1][0] === fence[0] && m[1].length >= fence.length) fence = null;
      continue;
    }
    if (m) {
      fence = m[1];
      continue;
    }
    const h = /^ {0,3}#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/.exec(line);
    if (h) {
      const rest = [...lines.slice(0, i), ...lines.slice(i + 1)];
      // The blank line that followed the heading goes with it.
      if (rest[i] !== undefined && rest[i].trim() === "" && (i === 0 || rest[i - 1].trim() === "")) rest.splice(i, 1);
      return { title: h[1].trim(), body: rest.join("\n").replace(/^\s*\n/, "").replace(/\s+$/, "") };
    }
  }
  return { title: null, body: md.replace(/\s+$/, "") };
}

/** The image pattern of Markdown: `![alt](src "title")`. */
const IMAGE = /!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+"[^"]*")?\s*\)/g;

/** The name of an image from its source, for `*[image: name]*`. */
export function imageName(src: string): string {
  const raw = src.replace(/^<|>$/g, "");
  if (/^data:/i.test(raw)) return "image";
  const last = baseName(raw.split(/[?#]/)[0]);
  let name = last;
  try {
    name = decodeURIComponent(last);
  } catch {
    // A bad escape: keep the name as written.
  }
  return name || "image";
}

/** Escape the marks that would end an italic early. */
function plain(text: string): string {
  return text.replace(/[*_\\[\]`]/g, (c) => `\\${c}`);
}

/**
 * Each image becomes its alt text in italics, or `*[image: name]*`.
 * Images wait for KB-2c. Returns the new text and the number of images.
 */
export function imagesToText(md: string): { md: string; count: number } {
  let count = 0;
  const out = mapOutsideCode(md, (part) =>
    part.replace(IMAGE, (_m, alt: string, src: string) => {
      count += 1;
      const words = alt.replace(/\s+/g, " ").trim();
      return words ? `*${plain(words)}*` : `*[image: ${plain(imageName(src))}]*`;
    }),
  );
  return { md: out, count };
}
