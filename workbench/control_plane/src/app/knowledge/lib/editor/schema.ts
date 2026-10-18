// ── The block editor's schema (WS-61 KB-2b, `knowledge_base.md` §21.4) ─────
//
// Pure: no React and no DOM at import, so the round-trip test runs in node.
// The component adds its node views through `KnowledgeViews`, and the
// schema and the Markdown rules stay the same with or without them.
//
// One storage format: Markdown. Each node here says how it reads from
// Markdown and how it writes back:
//
// - A callout is the GitHub alert syntax, `> [!NOTE]` and its four siblings.
// - A toggle is `<details>` with a `<summary>`, the one HTML shape we write.
// - Raw HTML that the editor cannot model stays as its own text, in an
//   `htmlBlock` or an `htmlInline` node. So do YAML front matter, a footnote
//   and a link with no text. Each draws as code and writes back byte for
//   byte, so a save never drops it in silence. ⚠️ No node here hands HTML
//   to the browser's parser (`htmlSinks.test.ts`).
// - Lists, links, code spans and entities follow `lists.ts` and `inline.ts`.
// - A heading keeps its level in storage (`#` is level 1) and draws one
//   level down (`displayHeadingLevel`), because the app bar holds the one
//   `<h1>` (AGENTS.md rule 11).
//
// ⚠️ No extension here binds `Mod-k`. The shell owns ⌘K (D87), and
// `editorMarkdown.test.ts` reads every shortcut of this list.

import {
  Extension,
  Node,
  mergeAttributes,
  type AnyExtension,
  type JSONContent,
  type MarkdownParseHelpers,
  type MarkdownParseResult,
  type MarkdownToken,
  type NodeViewRenderer,
} from "@tiptap/core";
import Code from "@tiptap/extension-code";
import CodeBlock from "@tiptap/extension-code-block";
import Heading from "@tiptap/extension-heading";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import Paragraph from "@tiptap/extension-paragraph";
import { BulletList, OrderedList, TaskItem, TaskList } from "@tiptap/extension-list";
import { Table, TableKit } from "@tiptap/extension-table";
import { Placeholder } from "@tiptap/extensions";
import { TextSelection } from "@tiptap/pm/state";
import StarterKit from "@tiptap/starter-kit";

import {
  FOOTNOTE_DEF,
  FOOTNOTE_REF,
  decodeEntities,
  entityTokenizer,
  escapeLineStart,
  linkContent,
  markdownDestination,
  markdownTitle,
  renderInline,
} from "./inline";
import { keepOrderedTaskBoxes, markerOf, renderList } from "./lists";

export { escapeLineStart, markdownDestination } from "./inline";

// ── Headings ────────────────────────────────────────────────────────────────

/** The level a stored heading draws at: one down, and never past 6. */
export function displayHeadingLevel(level: number): number {
  const n = Number.isInteger(level) && level >= 1 ? level : 1;
  return Math.min(n + 1, 6);
}

const LEVELS = [1, 2, 3, 4, 5, 6] as const;

/**
 * The heading draws at its display level, and it carries its stored level
 * in `data-md-level`. A copy and paste inside the editor reads that
 * attribute first, so a `#` heading does not come back as `##`.
 */
const KnowledgeHeading = Heading.extend({
  parseHTML() {
    return [
      ...LEVELS.map((level) => ({
        tag: `h${level}[data-md-level]`,
        priority: 60,
        getAttrs: (el: HTMLElement) => {
          const stored = Number(el.getAttribute("data-md-level"));
          return { level: LEVELS.includes(stored as (typeof LEVELS)[number]) ? stored : level };
        },
      })),
      ...this.options.levels.map((level) => ({ tag: `h${level}`, attrs: { level } })),
    ];
  },
  renderHTML({ node, HTMLAttributes }) {
    const stored = Number(node.attrs.level) || 1;
    return [
      `h${displayHeadingLevel(stored)}`,
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, { "data-md-level": String(stored) }),
      0,
    ];
  },
  // An ATX heading is one line. A setext heading of two lines reads as one
  // heading with a soft break, so the break writes as a space. A closing
  // `#` that is text keeps its backslash, or CommonMark drops it.
  renderMarkdown: (node, h) => {
    const level = Math.min(Math.max(Number(node.attrs?.level) || 1, 1), 6);
    const text = renderInline((node.content ?? []) as JSONContent[], h)
      .replace(/(?: {2,}|\\)?\n[ \t]*/g, " ")
      .trim()
      .replace(/(^|\s)(#+)$/, "$1\\$2");
    return text ? `${"#".repeat(level)} ${text}` : "";
  },
});

// ── Paragraphs ──────────────────────────────────────────────────────────────

const stockParagraph = Paragraph.config;

const KnowledgeParagraph = Paragraph.extend({
  // The stock rule lifts a paragraph of one image out to the block level.
  // Our images are inline, so the image stays in its paragraph.
  parseMarkdown: (token: MarkdownToken, h: MarkdownParseHelpers): MarkdownParseResult => {
    const tokens = token.tokens ?? [];
    if (tokens.length === 1 && tokens[0].type === "image") {
      return h.createNode("paragraph", {}, h.parseInline(tokens));
    }
    return stockParagraph.parseMarkdown
      ? stockParagraph.parseMarkdown(token, h)
      : h.createNode("paragraph", {}, h.parseInline(tokens));
  },
  renderMarkdown: (node, h, ctx) => {
    const content = (node.content ?? []) as JSONContent[];
    const out = content.length
      ? renderInline(content, h)
      : stockParagraph.renderMarkdown
        ? stockParagraph.renderMarkdown(node, h, ctx)
        : "";
    // A line after a hard break starts a line too, so each line gets the
    // same check. Spaces at the end of a paragraph mean nothing in Markdown,
    // and a page link leaves one, so they go.
    return out
      .split("\n")
      .map((line) => escapeLineStart(line))
      .join("\n")
      .replace(/[ \t]+$/, "");
  },
});

// ── Code blocks ─────────────────────────────────────────────────────────────

/**
 * The fence of a code block: three backticks, or one more than the longest
 * run of backticks in the code. A fixed fence breaks on code that holds a
 * fence of its own, and the round trip then loses the block.
 */
export function codeFence(code: string): string {
  let longest = 0;
  for (const run of code.match(/`+/g) ?? []) longest = Math.max(longest, run.length);
  return "`".repeat(Math.max(3, longest + 1));
}

const KnowledgeCodeBlock = CodeBlock.extend({
  renderMarkdown: (node: JSONContent) => {
    const code = (node.content ?? []).map((c) => c.text ?? "").join("");
    const fence = codeFence(code);
    const language = (node.attrs?.language as string | null) || "";
    return `${fence}${language}\n${code}\n${fence}`;
  },
});

// ── Code spans and links ────────────────────────────────────────────────────

/**
 * A code span writes with the fence that `prepareCodeSpans` put on its
 * mark (`inline.ts`), so a backtick inside it does not end it.
 */
const KnowledgeCode = Code.extend({
  // The stock code mark shuts out every other mark, so `[`code`](u)` lost
  // its link in the editor. Markdown allows a code span inside a link.
  excludes: "",
  renderMarkdown: (node: JSONContent, h: { renderChildren: (n: JSONContent[]) => string }) => {
    const fence = typeof node.attrs?.fence === "string" ? node.attrs.fence : "`";
    const pad = node.attrs?.pad ? " " : "";
    return `${fence}${pad}${h.renderChildren((node.content ?? []) as JSONContent[])}${pad}${fence}`;
  },
});

/**
 * A link marks every inline node it holds, an image too. A link with no
 * text stays as its own raw text. `renderInline` writes links: this
 * `renderMarkdown` covers a caller that renders a link mark alone.
 */
const KnowledgeLink = Link.extend({
  parseMarkdown: (token: MarkdownToken, h: MarkdownParseHelpers): MarkdownParseResult => {
    const content = h.parseInline(token.tokens ?? []);
    if (!content.length) return h.createNode("htmlInline", { raw: String(token.raw ?? "") });
    return linkContent(content, token);
  },
  renderMarkdown: (node: JSONContent, h: { renderChildren: (n: JSONContent) => string }) => {
    const href = String(node.attrs?.href ?? "");
    return `[${h.renderChildren(node)}](${markdownDestination(href)}${markdownTitle(node.attrs?.title)})`;
  },
});

// ── Images ──────────────────────────────────────────────────────────────────

/** The alt text of an image, with the brackets that would end it escaped. */
function imageAlt(alt: string): string {
  return alt.replace(/\\/g, "\\\\").replace(/([[\]])/g, "\\$1");
}

/**
 * Images are inline, as in Markdown, so `![a](b)` stays in its paragraph.
 * The node view draws each one through `MarkdownImage`, which gates a
 * remote image behind a click (`src/lib/markdownMedia.ts`).
 */
const KnowledgeImage = Image.extend({
  renderMarkdown: (node: JSONContent) => {
    const src = String(node.attrs?.src ?? "");
    const alt = imageAlt(String(node.attrs?.alt ?? ""));
    return `![${alt}](${markdownDestination(src)}${markdownTitle(node.attrs?.title)})`;
  },
}).configure({ inline: true, allowBase64: false });

// ── Tables ──────────────────────────────────────────────────────────────────

/** A `|` that no backslash escapes, escaped, so it cannot split a cell. */
export function escapeCellPipes(text: string): string {
  let out = "";
  let slashes = 0;
  for (const ch of text) {
    if (ch === "|" && slashes % 2 === 0) out += "\\";
    out += ch;
    slashes = ch === "\\" ? slashes + 1 : 0;
  }
  return out;
}

const ALIGN_RULE: Record<string, string> = { left: ":---", right: "---:", center: ":---:" };

/**
 * A table as GFM, one line for each row, with no padding. A cell holds one
 * line: its blocks and its line breaks join with `<br>`, which reads back as
 * a break (`htmlInline`). Each bare `|` is escaped. Padding is left out, so
 * an edit of one cell changes one line of the stored body.
 */
function renderTable(node: JSONContent, h: { renderChildren: (n: JSONContent | JSONContent[]) => string }): string {
  const rows = (node.content ?? []) as JSONContent[];
  if (!rows.length) return "";
  const cellText = (cell: JSONContent): string =>
    ((cell.content ?? []) as JSONContent[])
      .map((block) => h.renderChildren([block]))
      .join("\n")
      .split(/[ \t]*\r?\n[ \t]*/)
      .map((s) => s.trim())
      .filter(Boolean)
      .join("<br>");
  const grid = rows.map((row) => ((row.content ?? []) as JSONContent[]).map((cell) => ({
    text: escapeCellPipes(cellText(cell)),
    header: cell.type === "tableHeader",
    align: typeof cell.attrs?.align === "string" ? (cell.attrs.align as string) : null,
  })));
  const width = Math.max(...grid.map((r) => r.length));
  if (!width) return "";
  const hasHeader = grid[0].some((c) => c.header);
  const line = (cells: string[]) => `| ${cells.join(" | ")} |`;
  const pick = (r: typeof grid[number]) => Array.from({ length: width }, (_, i) => r[i]?.text ?? "");
  const aligns = Array.from({ length: width }, (_, i) => grid.find((r) => r[i]?.align)?.[i]?.align ?? null);
  const out = [
    line(hasHeader ? pick(grid[0]) : Array.from({ length: width }, () => "")),
    line(aligns.map((a) => (a && ALIGN_RULE[a]) || "---")),
    ...(hasHeader ? grid.slice(1) : grid).map((r) => line(pick(r))),
  ];
  return out.join("\n");
}

const KnowledgeTable = Table.extend({
  renderMarkdown: (node: JSONContent, h: { renderChildren: (n: JSONContent | JSONContent[]) => string }) =>
    renderTable(node, h),
  // Tab moves to the next cell. In the last cell it moves on out of the
  // editor, as Tab does on any page. The stock key added a row each time,
  // so a keyboard user could never leave the table.
  addKeyboardShortcuts() {
    return {
      ...this.parent?.(),
      Tab: () => this.editor.commands.goToNextCell(),
      "Shift-Tab": () => this.editor.commands.goToPreviousCell(),
    };
  },
});

// ── Lists ───────────────────────────────────────────────────────────────────

/** The attributes that keep how a list was written (`lists.ts`). */
const listAttrs = (key: "bullet" | "delimiter", fallback: string) => ({
  [key]: { default: fallback, rendered: false, keepOnSplit: true },
  loose: { default: false, rendered: false, keepOnSplit: true },
});

/** The stock parse of a list, with its marker and its looseness kept. */
function keepListShape(
  stock: ((t: MarkdownToken, h: MarkdownParseHelpers) => MarkdownParseResult) | undefined,
  key: "bullet" | "delimiter",
) {
  return (token: MarkdownToken, h: MarkdownParseHelpers): MarkdownParseResult => {
    const out = stock?.(token, h);
    if (!out || Array.isArray(out) || "mark" in out) return out ?? [];
    const family = key === "bullet" ? "bullet" : "ordered";
    return { ...out, attrs: { ...(out.attrs ?? {}), [key]: markerOf(token.raw, family), loose: Boolean(token.loose) } };
  };
}

const KnowledgeBulletList = BulletList.extend({
  addAttributes() {
    return { ...this.parent?.(), ...listAttrs("bullet", "-") };
  },
  parseMarkdown: keepListShape(BulletList.config.parseMarkdown, "bullet"),
  renderMarkdown: (node: JSONContent, h) => renderList(node, h),
});

/**
 * The stock ordered list brings its own tokenizer, which does not follow
 * CommonMark: it knows no loose list and no child indent. This one never
 * matches, so marked's own list rule reads the list.
 */
const KnowledgeOrderedList = OrderedList.extend({
  addAttributes() {
    return { ...this.parent?.(), ...listAttrs("delimiter", ".") };
  },
  markdownTokenizer: { name: "orderedList", level: "block", start: () => -1, tokenize: () => undefined },
  parseMarkdown: (token: MarkdownToken, h: MarkdownParseHelpers) =>
    keepListShape(OrderedList.config.parseMarkdown, "delimiter")(keepOrderedTaskBoxes(token), h),
  renderMarkdown: (node: JSONContent, h) => renderList(node, h),
});

const KnowledgeTaskList = TaskList.extend({
  addAttributes() {
    return { ...this.parent?.(), ...listAttrs("bullet", "-") };
  },
  parseMarkdown: keepListShape(TaskList.config.parseMarkdown, "bullet"),
  renderMarkdown: (node: JSONContent, h) => renderList(node, h),
});



// ── Callouts ────────────────────────────────────────────────────────────────

/** The five GitHub alert tones, in the order the menus offer them. */
export const CALLOUT_TONES = ["note", "tip", "important", "warning", "caution"] as const;
export type CalloutTone = (typeof CALLOUT_TONES)[number];

export function isCalloutTone(value: unknown): value is CalloutTone {
  return typeof value === "string" && (CALLOUT_TONES as readonly string[]).includes(value);
}

/**
 * Where a block of ours may start inside `src`, for marked's paragraph cut.
 *
 * ⚠️ marked hands a block `start` the source with its FIRST character cut
 * off, so index 0 is the middle of a line. A match there split
 * `\\<details>` into a stray backslash and a block. So a block starts only
 * right after a newline. `pattern` matches that newline.
 */
function lineStartIndex(src: string, pattern: RegExp): number {
  const m = pattern.exec(src);
  return m ? m.index + 1 : -1;
}

const CALLOUT_OPEN = /^ {0,3}> ?\[!(note|tip|important|warning|caution)\][ \t]*(?:\n|$)/i;
const QUOTE_LINE = /^ {0,3}>[^\n]*(?:\n|$)/;

/** Prefix each line of a rendered child with `> `, and an empty one with `>`. */
function quoteLines(text: string): string {
  return text
    .split("\n")
    .map((line) => (line.trim() === "" ? ">" : `> ${line}`))
    .join("\n");
}

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    callout: {
      /** Wrap the selected blocks in a callout of this tone. */
      setCallout: (tone?: CalloutTone) => ReturnType;
      /** Change the tone of the callout around the selection. */
      setCalloutTone: (tone: CalloutTone) => ReturnType;
    };
    details: {
      /** Insert a toggle with an empty summary and one paragraph. */
      insertToggle: () => ReturnType;
    };
  }
}

const Callout = Node.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,

  addAttributes() {
    return {
      tone: {
        default: "note",
        parseHTML: (el: HTMLElement) => {
          const tone = el.getAttribute("data-callout");
          return isCalloutTone(tone) ? tone : "note";
        },
        renderHTML: (attrs: Record<string, unknown>) => ({ "data-callout": attrs.tone }),
      },
      // The marker as the body wrote it (`[!note]` or `[!NOTE]`), so a save
      // keeps its case. A change of tone writes the tone in capitals.
      marker: { default: null, rendered: false },
    };
  },

  parseHTML() {
    return [{ tag: "div[data-callout]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes({ class: "kb-callout" }, HTMLAttributes), 0];
  },

  addCommands() {
    return {
      setCallout:
        (tone = "note") =>
        ({ commands }) =>
          commands.wrapIn(this.name, { tone }),
      setCalloutTone:
        (tone) =>
        ({ commands }) =>
          commands.updateAttributes(this.name, { tone, marker: null }),
    };
  },

  markdownTokenizer: {
    name: "callout",
    level: "block",
    start: (src: string) => lineStartIndex(src, /\n {0,3}> ?\[!(?:note|tip|important|warning|caution)\]/i),
    tokenize: (src, _tokens, lexer) => {
      const open = CALLOUT_OPEN.exec(src);
      if (!open) return undefined;
      let raw = open[0];
      const body: string[] = [];
      let rest = src.slice(raw.length);
      for (let line = QUOTE_LINE.exec(rest); line; line = QUOTE_LINE.exec(rest)) {
        raw += line[0];
        body.push(line[0].replace(/\n$/, "").replace(/^ {0,3}> ?/, ""));
        rest = rest.slice(line[0].length);
      }
      return {
        type: "callout",
        raw,
        tone: open[1].toLowerCase(),
        marker: open[1],
        tokens: lexer.blockTokens(body.join("\n")),
      };
    },
  },

  parseMarkdown: (token, h) => {
    const children = (h.parseBlockChildren ?? h.parseChildren)(token.tokens ?? []);
    const content = children.length ? children : [h.createNode("paragraph", {}, [])];
    const tone = isCalloutTone(token.tone) ? token.tone : "note";
    return h.createNode("callout", { tone, marker: token.marker ?? null }, content);
  },

  renderMarkdown: (node, h) => {
    const tone = isCalloutTone(node.attrs?.tone) ? node.attrs.tone : "note";
    const children = (node.content ?? []) as JSONContent[];
    const parts = children.map((child, i) => quoteLines(h.renderChild?.(child, i) ?? h.renderChildren([child])));
    const body = parts.length ? parts.join("\n>\n") : ">";
    const kept = typeof node.attrs?.marker === "string" && node.attrs.marker.toLowerCase() === tone;
    return `> [!${kept ? node.attrs?.marker : tone.toUpperCase()}]\n${body}`;
  },
});

// ── Toggles ─────────────────────────────────────────────────────────────────

/**
 * A summary made safe as HTML text, with the least change: `<` before a
 * tag, and `&` before an entity. So "5 < 6 & more" writes back as it was,
 * and a summary still cannot end its own tag.
 */
export function escapeSummary(text: string): string {
  return text
    .replace(/&(?=#?[A-Za-z0-9]+;)/g, "&amp;")
    .replace(/<(?=[A-Za-z/!?])/g, "&lt;");
}

/** The text of a summary: each entity as its character. */
export function unescapeSummary(text: string): string {
  return decodeEntities(text);
}

const DETAILS_OPEN = /^ {0,3}<details(\s+open)?\s*>[ \t]*\n?[ \t]*<summary>([^\n]*?)<\/summary>[ \t]*(?:\n|$)/i;

const Details = Node.create({
  name: "details",
  group: "block",
  content: "detailsSummary detailsContent",
  defining: true,
  isolating: true,

  addAttributes() {
    // `<details open>`: the toggle starts open on the read view, and a save
    // keeps the word.
    return { open: { default: false, rendered: false } };
  },

  parseHTML() {
    return [{ tag: "div[data-toggle]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes({ "data-toggle": "", class: "kb-toggle" }, HTMLAttributes), 0];
  },

  addCommands() {
    return {
      // The caret lands in the summary, so a member names the toggle first.
      insertToggle:
        () =>
        ({ chain }) =>
          chain()
            .insertContent({
              type: this.name,
              content: [
                { type: "detailsSummary" },
                { type: "detailsContent", content: [{ type: "paragraph" }] },
              ],
            })
            .command(({ tr }) => {
              const $at = tr.selection.$from;
              for (let d = $at.depth; d > 0; d--) {
                if ($at.node(d).type.name === this.name) {
                  tr.setSelection(TextSelection.create(tr.doc, $at.start(d) + 1));
                  break;
                }
              }
              return true;
            })
            .run(),
    };
  },

  markdownTokenizer: {
    name: "details",
    level: "block",
    start: (src: string) => lineStartIndex(src, /\n {0,3}<details/i),
    tokenize: (src, _tokens, lexer) => {
      const open = DETAILS_OPEN.exec(src);
      if (!open) return undefined;
      // Find the `</details>` that closes this one, past any nested toggle.
      const tags = /<details\b[^>]*>|<\/details\s*>/gi;
      tags.lastIndex = open[0].length;
      let depth = 1;
      let close: RegExpExecArray | null = null;
      for (let m = tags.exec(src); m; m = tags.exec(src)) {
        depth += m[0][1] === "/" ? -1 : 1;
        if (depth === 0) {
          close = m;
          break;
        }
      }
      if (!close) return undefined;
      const after = /^[ \t]*(?:\n|$)/.exec(src.slice(close.index + close[0].length));
      if (!after) return undefined;
      const end = close.index + close[0].length + after[0].length;
      const body = src.slice(open[0].length, close.index).replace(/^\s*\n/, "").replace(/\s+$/, "");
      return {
        type: "details",
        raw: src.slice(0, end),
        summary: unescapeSummary(open[2].trim()),
        open: Boolean(open[1]),
        tokens: body ? lexer.blockTokens(body) : [],
      };
    },
  },

  parseMarkdown: (token, h) => {
    const summary = String(token.summary ?? "");
    const children = (h.parseBlockChildren ?? h.parseChildren)(token.tokens ?? []);
    return h.createNode("details", { open: Boolean(token.open) }, [
      h.createNode("detailsSummary", {}, summary ? [h.createTextNode(summary)] : []),
      h.createNode("detailsContent", {}, children.length ? children : [h.createNode("paragraph", {}, [])]),
    ]);
  },

  renderMarkdown: (node, h) => {
    const [summaryNode, contentNode] = (node.content ?? []) as JSONContent[];
    const summary = (summaryNode?.content ?? []).map((c) => c.text ?? "").join("");
    const blocks = ((contentNode?.content ?? []) as JSONContent[]).map(
      (child, i) => h.renderChild?.(child, i) ?? h.renderChildren([child]),
    );
    const body = blocks.join("\n\n").trim();
    const tag = node.attrs?.open ? "<details open>" : "<details>";
    return `${tag}\n<summary>${escapeSummary(summary)}</summary>\n\n${body}${body ? "\n\n" : ""}</details>`;
  },
});

const DetailsSummary = Node.create({
  name: "detailsSummary",
  content: "text*",
  marks: "",
  defining: true,
  selectable: false,

  parseHTML() {
    return [{ tag: "div[data-toggle-summary]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes({ "data-toggle-summary": "", class: "kb-toggle-summary" }, HTMLAttributes), 0];
  },

  addKeyboardShortcuts() {
    return {
      // Enter in the summary moves into the body, and never splits it.
      Enter: ({ editor }) => {
        const { $from } = editor.state.selection;
        if ($from.parent.type.name !== this.name) return false;
        const after = $from.after();
        return editor.commands.setTextSelection(after + 2);
      },
    };
  },
});

const DetailsContent = Node.create({
  name: "detailsContent",
  content: "block+",
  defining: true,
  selectable: false,

  parseHTML() {
    return [{ tag: "div[data-toggle-content]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes({ "data-toggle-content": "", class: "kb-toggle-content" }, HTMLAttributes), 0];
  },
});

// ── Raw HTML, kept as text ──────────────────────────────────────────────────

/**
 * One piece of inline raw HTML, as CommonMark defines it: an open tag, a
 * close tag, a comment, a processing instruction, a declaration or a CDATA
 * section. Anchored, so the tokenizer only matches at its own position.
 */
const INLINE_HTML = new RegExp(
  "^(?:" +
    [
      String.raw`<[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s"'=<>\x60]+|'[^']*'|"[^"]*"))?)*\s*\/?>`,
      String.raw`<\/[A-Za-z][A-Za-z0-9-]*\s*>`,
      String.raw`<!--(?:-?>|[\s\S]*?-->)`,
      String.raw`<\?[\s\S]*?\?>`,
      String.raw`<![A-Za-z]+[^>]*>`,
      String.raw`<!\[CDATA\[[\s\S]*?\]\]>`,
    ].join("|") +
    ")",
);

/** True for `<br>`, `<br/>` and `<br />`. */
export function isLineBreakHtml(raw: string): boolean {
  return /^<br\s*\/?>$/i.test(raw);
}

/** The first `<` that no backslash escapes, or -1. */
function firstOpenAngle(src: string): number {
  for (let i = src.indexOf("<"); i !== -1; i = src.indexOf("<", i + 1)) {
    let slashes = 0;
    for (let j = i - 1; j >= 0 && src[j] === "\\"; j--) slashes++;
    if (slashes % 2 === 0) return i;
  }
  return -1;
}

const HtmlBlock = Node.create({
  name: "htmlBlock",
  group: "block",
  content: "text*",
  marks: "",
  code: true,
  defining: true,

  markdownTokenName: "html",

  parseHTML() {
    return [{ tag: "pre[data-raw-html]", preserveWhitespace: "full" as const }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["pre", mergeAttributes({ "data-raw-html": "", class: "kb-raw-html" }, HTMLAttributes), ["code", 0]];
  },

  // A block of raw HTML stays as its own text. Inline HTML never reaches
  // here: `htmlInline` takes it first, so `null` hands back nothing.
  parseMarkdown: (token, h) => {
    if (!token.block) return [];
    const raw = String(token.raw ?? token.text ?? "").replace(/\s+$/, "");
    return h.createNode("htmlBlock", {}, raw ? [h.createTextNode(raw)] : []);
  },

  renderMarkdown: (node) => ((node.content ?? []) as JSONContent[]).map((c) => c.text ?? "").join(""),
});

const HtmlInline = Node.create({
  name: "htmlInline",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,

  addAttributes() {
    return { raw: { default: "", rendered: false } };
  },

  parseHTML() {
    return [
      {
        tag: "code[data-raw-html]",
        priority: 60,
        getAttrs: (el: HTMLElement) => ({ raw: el.textContent ?? "" }),
      },
      {
        tag: "br[data-raw-html]",
        priority: 60,
        getAttrs: (el: HTMLElement) => ({ raw: el.getAttribute("data-raw-html") || "<br>" }),
      },
    ];
  },

  // A `<br>` draws as the break it means, and keeps its spelling in an
  // attribute. Every other piece draws as code. Neither is parsed as HTML.
  renderHTML({ node, HTMLAttributes }) {
    const raw = String(node.attrs.raw);
    if (isLineBreakHtml(raw)) return ["br", { "data-raw-html": raw }];
    return ["code", mergeAttributes({ "data-raw-html": "", class: "kb-raw-html" }, HTMLAttributes), raw];
  },

  renderText({ node }) {
    return String(node.attrs.raw);
  },

  markdownTokenizer: {
    name: "htmlInline",
    level: "inline",
    start: firstOpenAngle,
    tokenize: (src) => {
      const m = INLINE_HTML.exec(src);
      return m ? { type: "htmlInline", raw: m[0], text: m[0] } : undefined;
    },
  },

  parseMarkdown: (token, h) => h.createNode("htmlInline", { raw: String(token.raw ?? "") }),

  renderMarkdown: (node) => String(node.attrs?.raw ?? ""),
});

// ── Front matter, footnotes and entities ────────────────────────────────────

/**
 * YAML front matter at the top of a body: `---`, a first line with a key
 * (`title:`), more lines, then `---` or `...`. CommonMark reads it as a rule and a heading, so a save turned
 * `title: x` into `## title: x`. `parseBody` takes it off first, and this
 * node keeps it as text, byte for byte.
 */
export const FRONT_MATTER = /^---[ \t]*\n(?=[A-Za-z0-9_"'-][^\n]*:)(?:[^\n]*\n)*?(?:---|\.\.\.)[ \t]*(?:\n|$)/;

const FrontMatter = Node.create({
  name: "frontMatter",
  group: "block",
  content: "text*",
  marks: "",
  code: true,
  defining: true,

  parseHTML() {
    return [{ tag: "pre[data-front-matter]", preserveWhitespace: "full" as const }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["pre", mergeAttributes({ "data-front-matter": "", class: "kb-raw-html" }, HTMLAttributes), ["code", 0]];
  },

  renderMarkdown: (node) => ((node.content ?? []) as JSONContent[]).map((c) => c.text ?? "").join(""),
});

/**
 * Footnotes, kept as raw text. GFM draws `[^1]` and `[^1]: …`, and marked
 * does not know them, so the stock round trip escaped them into plain text.
 */
const FootnoteRef = Extension.create({
  name: "kbFootnoteRef",
  markdownTokenizer: {
    name: "kbFootnoteRef",
    level: "inline",
    start: (src: string) => src.indexOf("[^"),
    tokenize: (src: string) => {
      const m = FOOTNOTE_REF.exec(src);
      return m ? { type: "kbFootnoteRef", raw: m[0], text: m[0] } : undefined;
    },
  },
  parseMarkdown: (token, h) => h.createNode("htmlInline", { raw: String(token.raw ?? "") }),
});

const FootnoteDef = Extension.create({
  name: "kbFootnoteDef",
  markdownTokenizer: {
    name: "kbFootnoteDef",
    level: "block",
    start: (src: string) => {
      const m = /\n\[\^[^\]\s]{1,100}\]:/.exec(src);
      return m ? m.index + 1 : -1;
    },
    tokenize: (src: string) => {
      const m = FOOTNOTE_DEF.exec(src);
      if (!m) return undefined;
      const end = /^\n?/.exec(src.slice(m[0].length))?.[0] ?? "";
      return { type: "kbFootnoteDef", raw: m[0] + end, text: m[0] };
    },
  },
  parseMarkdown: (token, h) => h.createNode("htmlBlock", {}, [h.createTextNode(String(token.text ?? ""))]),
});

/** An entity reads as its character (`inline.ts`). */
const Entities = Extension.create({
  name: "kbEntity",
  markdownTokenizer: entityTokenizer,
  parseMarkdown: (token, h) => h.createTextNode(String(token.text ?? "")),
});

// ── The list ────────────────────────────────────────────────────────────────

/** The node views the component plugs in. Each one is optional. */
export interface KnowledgeViews {
  image?: NodeViewRenderer;
  codeBlock?: NodeViewRenderer;
  callout?: NodeViewRenderer;
  details?: NodeViewRenderer;
}

export interface KnowledgeSchemaOptions {
  /** The node views of the component. The test passes none. */
  views?: KnowledgeViews;
  /** The empty-body hint, in edit mode only. */
  placeholder?: string;
}

/** Wire a node view into a node, when the component gives one. */
function withView<T extends AnyExtension>(ext: T, view: NodeViewRenderer | undefined): T {
  if (!view) return ext;
  return ext.extend({ addNodeView: () => view }) as T;
}

/**
 * The extensions of the Knowledge editor, in one list for the editor, the
 * read view and the round-trip test.
 *
 * - Underline is off: Markdown has no underline, so a save would write HTML.
 * - Table columns do not resize: Markdown keeps no width, so a save would
 *   drop it, and a member would see the width go.
 * - Links open on no click while a member edits. The read view decides
 *   where a click goes (`KnowledgeEditor`'s click handler).
 */
export function knowledgeExtensions(opts: KnowledgeSchemaOptions = {}): AnyExtension[] {
  const views = opts.views ?? {};
  const list: AnyExtension[] = [
    StarterKit.configure({
      heading: false,
      paragraph: false,
      codeBlock: false,
      code: false,
      link: false,
      bulletList: false,
      orderedList: false,
      underline: false,
    }),
    KnowledgeCode,
    KnowledgeLink.configure({
      openOnClick: false,
      autolink: true,
      linkOnPaste: true,
      defaultProtocol: "https",
      HTMLAttributes: { rel: "noopener noreferrer nofollow", target: null },
    }),
    KnowledgeBulletList,
    KnowledgeOrderedList,
    KnowledgeParagraph,
    KnowledgeHeading.configure({ levels: [...LEVELS] }),
    withView(KnowledgeCodeBlock, views.codeBlock),
    withView(KnowledgeImage, views.image),
    TableKit.configure({ table: false }),
    KnowledgeTable.configure({ resizable: false }),
    KnowledgeTaskList,
    TaskItem.configure({ nested: true }),
    withView(Callout, views.callout),
    withView(Details, views.details),
    DetailsSummary,
    DetailsContent,
    HtmlBlock,
    HtmlInline,
    FrontMatter,
    FootnoteRef,
    FootnoteDef,
    Entities,
  ];
  if (opts.placeholder) {
    list.push(
      Placeholder.configure({
        placeholder: ({ node }) => (node.type.name === "paragraph" ? opts.placeholder ?? "" : ""),
        showOnlyCurrent: true,
      }),
    );
  }
  return list;
}
