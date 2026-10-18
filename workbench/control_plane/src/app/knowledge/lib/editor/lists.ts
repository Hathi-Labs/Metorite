// ── Lists of the block editor, read and written as CommonMark (KB-2b fix) ──
//
// Pure. The stock list writers indented every child by two spaces. In
// CommonMark a child of `1. ` needs three and a child of `10. ` needs four,
// so a nested list or a code block under a numbered item left the list. They
// also wrote every list tight, and changed every bullet to `-`, so two lists
// that sat next to each other merged on the second save.
//
// So each list keeps what it was written with: its bullet (`-`, `*` or
// `+`), its delimiter (`.` or `)`), and whether it is loose. A child line is
// indented by the width of its own item's marker.

import type { JSONContent, MarkdownToken } from "@tiptap/core";

export type ListFamily = "bullet" | "ordered";

export const BULLETS = ["-", "*", "+"] as const;
export const DELIMITERS = [".", ")"] as const;

/** The family of a list node, or `null` for any other node. */
export function listFamily(type: string | undefined): ListFamily | null {
  if (type === "bulletList" || type === "taskList") return "bullet";
  if (type === "orderedList") return "ordered";
  return null;
}

/** The bullet or delimiter a list token was written with. */
export function markerOf(raw: unknown, family: ListFamily): string {
  const text = typeof raw === "string" ? raw : "";
  if (family === "bullet") return /^\s*([-*+])/.exec(text)?.[1] ?? "-";
  return /^\s*\d{1,9}([.)])/.exec(text)?.[1] ?? ".";
}

const marker = (node: JSONContent, family: ListFamily): string => {
  const own = family === "bullet" ? node.attrs?.bullet : node.attrs?.delimiter;
  const set: readonly string[] = family === "bullet" ? BULLETS : DELIMITERS;
  return typeof own === "string" && set.includes(own) ? own : set[0];
};

/**
 * Give each list of one container the marker it writes with. Two lists of
 * one family that sit next to each other must differ, or CommonMark reads
 * them as one list. So the second takes the next marker.
 */
export function separateAdjacentLists(node: JSONContent): JSONContent {
  if (!node.content) return node;
  const content = node.content.map(separateAdjacentLists);
  let prev: { family: ListFamily; mark: string } | null = null;
  for (let i = 0; i < content.length; i++) {
    const family = listFamily(content[i].type);
    if (!family) {
      prev = null;
      continue;
    }
    let mark = marker(content[i], family);
    if (prev && prev.family === family && prev.mark === mark) {
      const set: readonly string[] = family === "bullet" ? BULLETS : DELIMITERS;
      mark = set[(set.indexOf(mark) + 1) % set.length];
    }
    const key = family === "bullet" ? "bullet" : "delimiter";
    content[i] = { ...content[i], attrs: { ...(content[i].attrs ?? {}), [key]: mark } };
    prev = { family, mark };
  }
  return { ...node, content };
}

/** Blocks that cannot follow a paragraph in an item without a blank line. */
const NEEDS_BLANK = new Set(["paragraph", "table", "htmlBlock"]);

/**
 * True when the list must be written loose: it was loose, or one of its
 * items holds a block after its first that needs a blank line before it.
 */
export function isLoose(list: JSONContent): boolean {
  if (list.attrs?.loose) return true;
  return (list.content ?? []).some((item) =>
    (item.content ?? []).some((child, i) => i > 0 && NEEDS_BLANK.has(child.type ?? "")),
  );
}

type Renderer = { renderChild?: (node: JSONContent, index: number) => string; renderChildren: (n: JSONContent | JSONContent[]) => string };

/** An ordered item that starts with `[ ]` or `[x]` keeps it unescaped. */
function unescapeTaskBox(first: string): string {
  return first.replace(/^\\\[( |x|X)\\\] /, "[$1] ");
}

/**
 * One list as CommonMark. Each item is its marker, then its blocks, and
 * each line after the first is indented by the width of the marker.
 */
export function renderList(list: JSONContent, h: Renderer): string {
  const family = listFamily(list.type);
  if (!family) return "";
  const loose = isLoose(list);
  const mark = marker(list, family);
  const start = Number(list.attrs?.start ?? 1) || 1;
  const items = (list.content ?? []).map((item, i) => {
    const prefix = family === "ordered" ? `${start + i}${mark} ` : `${mark} `;
    const box = item.type === "taskItem" ? `[${item.attrs?.checked ? "x" : " "}] ` : "";
    const blocks = (item.content ?? []).map((child, j) => h.renderChild?.(child, j) ?? h.renderChildren([child]));
    let body = blocks.join(loose ? "\n\n" : "\n");
    if (family === "ordered" && !box) body = unescapeTaskBox(body);
    const indent = " ".repeat(prefix.length);
    const lines = body.split("\n");
    const first = `${prefix}${box}${lines[0] ?? ""}`.replace(/[ \t]+$/, "");
    const rest = lines.slice(1).map((line) => (line === "" ? "" : indent + line));
    return [first, ...rest].join("\n");
  });
  return items.join(loose ? "\n\n" : "\n");
}

/**
 * A task box in a numbered list, kept as text. GFM reads `1. [ ] a` as a
 * task, and the editor has no numbered task list. marked takes the box off
 * into a `checkbox` token, and the stock item dropped it. So the box goes
 * back in front of the item's text, and `renderList` writes it unescaped.
 */
export function keepOrderedTaskBoxes(token: MarkdownToken): MarkdownToken {
  if (!Array.isArray(token.items)) return token;
  const items = token.items.map((item: MarkdownToken) => {
    const tokens = item.tokens ?? [];
    const at = tokens.findIndex((t) => t.type === "checkbox");
    if (at < 0) return item;
    const box = String(tokens[at].raw ?? "[ ] ");
    const next = tokens[at + 1];
    const rest = tokens.filter((_, i) => i !== at);
    if (next && (next.type === "text" || next.type === "paragraph")) {
      const merged = {
        ...next,
        raw: box + (next.raw ?? ""),
        text: box + (next.text ?? ""),
        tokens: [{ type: "text", raw: box, text: box }, ...(next.tokens ?? [])],
      };
      rest[at] = merged;
    } else {
      rest.splice(at, 0, { type: "text", raw: box, text: box, tokens: [{ type: "text", raw: box, text: box }] });
    }
    return { ...item, task: false, text: box + (item.text ?? ""), tokens: rest };
  });
  return { ...token, items };
}
