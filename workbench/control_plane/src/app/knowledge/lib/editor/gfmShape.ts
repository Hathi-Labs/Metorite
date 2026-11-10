// ── The meaning of a body, as a CommonMark and GFM reader sees it ───────────
//
// Test only: the golden test imports it, and no app code does. It reads a
// body with `remark-parse` and `remark-gfm` (micromark), which follow the
// CommonMark and GFM specs. The editor reads with marked, which is looser.
// So a body that only marked reads the right way cannot pass the golden test
// (`editorMarkdown.test.ts`).
//
// The shape keeps each block with what it means (a heading's level, a list's
// start and looseness, a cell's alignment, a code block's language and
// text) and each run of text with its marks. It leaves out what Markdown
// spells two ways for one meaning: `*` or `_`, the nesting order of two
// marks on the same text, and the amount of white space.

import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";

type MdNode = {
  type: string;
  children?: MdNode[];
  value?: string;
  depth?: number;
  ordered?: boolean | null;
  start?: number | null;
  spread?: boolean | null;
  checked?: boolean | null;
  lang?: string | null;
  url?: string;
  title?: string | null;
  alt?: string | null;
  align?: (string | null)[] | null;
  identifier?: string;
  label?: string | null;
};

/** The definitions of a tree, by their normalised label. */
type Definitions = Map<string, { url: string; title: string | null }>;

export type Shape = { t: string; a?: string; kids?: Shape[]; runs?: string[] };

const MARK_TYPES: Record<string, (n: MdNode) => string> = {
  emphasis: () => "em",
  strong: () => "strong",
  delete: () => "del",
  link: (n) => `link(${n.url ?? ""}|${n.title ?? ""})`,
};

const INLINE = new Set([
  "text", "inlineCode", "image", "break", "html", "footnoteReference", "imageReference", "linkReference",
  ...Object.keys(MARK_TYPES),
]);

function runs(nodes: MdNode[], marks: string[], out: string[], defs: Definitions): void {
  for (const n of nodes) {
    if (n.type === "linkReference") {
      // A reference link means its definition's link.
      const def = defs.get((n.identifier ?? "").toLowerCase());
      const mark = def ? `link(${def.url}|${def.title ?? ""})` : "linkref";
      runs(n.children ?? [], [...marks, mark], out, defs);
      continue;
    }
    const mark = MARK_TYPES[n.type];
    if (mark) {
      runs(n.children ?? [], [...marks, mark(n)], out, defs);
      continue;
    }
    const m = [...marks].sort().join(",");
    let piece: string;
    if (n.type === "text") piece = `text[${m}]:${(n.value ?? "").replace(/\s+/g, " ")}`;
    else if (n.type === "inlineCode") piece = `code[${m}]:${n.value}`;
    else if (n.type === "image") piece = `img[${m}]:${n.alt ?? ""}|${n.url ?? ""}|${n.title ?? ""}`;
    else if (n.type === "break") piece = "text[]: ";
    else if (n.type === "html") piece = `html[${m}]:${n.value}`;
    else piece = `${n.type}[${m}]:${n.value ?? n.identifier ?? ""}`;
    const last = out[out.length - 1];
    const prefix = `text[${m}]:`;
    if (piece.startsWith(prefix) && last?.startsWith(prefix)) {
      out[out.length - 1] = (last + piece.slice(prefix.length)).replace(/\s+/g, " ");
    } else {
      out.push(piece);
    }
  }
}

function attrsOf(n: MdNode): string | undefined {
  switch (n.type) {
    case "heading":
      return `h${n.depth}`;
    case "list": {
      // Loose as CommonMark draws it: blank lines between the items, or
      // inside one of them (mdast-util-to-hast reads it the same way).
      const loose = Boolean(n.spread) || (n.children ?? []).some((c) => Boolean(c.spread));
      return `${n.ordered ? `ol${n.start ?? 1}` : "ul"}${loose ? " loose" : ""}`;
    }
    case "listItem":
      return n.checked === null || n.checked === undefined ? undefined : `checked=${n.checked}`;
    case "code":
      return `${n.lang ?? ""}|${n.value ?? ""}`;
    case "html":
      return n.value;
    case "table":
      return (n.align ?? []).map((a) => a ?? "-").join(",");
    case "footnoteDefinition":
      return n.identifier;
    default:
      return undefined;
  }
}

function block(n: MdNode, defs: Definitions): Shape | null {
  if (n.type === "definition") return null;
  const kids = n.children ?? [];
  const shape: Shape = { t: n.type };
  const a = attrsOf(n);
  if (a !== undefined) shape.a = a;
  if (kids.length && kids.every((k) => INLINE.has(k.type))) {
    const r: string[] = [];
    runs(kids, [], r, defs);
    // A run of white space at either end means nothing.
    const trimmed = r
      .map((x, i) => (i === 0 ? x.replace(/^(text\[[^\]]*\]:) /, "$1") : x))
      .map((x, i, all) => (i === all.length - 1 ? x.replace(/ $/, "") : x))
      .filter((x) => !/^text\[[^\]]*\]:$/.test(x));
    if (trimmed.length) shape.runs = trimmed;
  } else if (kids.length) {
    const blocks = kids.map((k) => block(k, defs)).filter((k): k is Shape => k !== null);
    if (blocks.length) shape.kids = blocks;
  }
  return shape;
}

/** The meaning of `md` under CommonMark and GFM. */
export function gfmShape(md: string): Shape {
  const tree = unified().use(remarkParse).use(remarkGfm).parse(md) as unknown as MdNode;
  const defs: Definitions = new Map();
  const collect = (n: MdNode) => {
    if (n.type === "definition") defs.set((n.identifier ?? "").toLowerCase(), { url: n.url ?? "", title: n.title ?? null });
    for (const c of n.children ?? []) collect(c);
  };
  collect(tree);
  return block(tree, defs) ?? { t: "root" };
}
