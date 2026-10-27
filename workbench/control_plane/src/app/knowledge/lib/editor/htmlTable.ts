// ── The table that GFM cannot hold (WS-61 KB-2p, §21.7) ─────────────────────
//
// Pure. A GFM table has one header row and one line for each row. It has no
// form for a merged cell, a header column, a cell colour or an aligned
// block in a cell. A table with one of those stores as ONE block of HTML,
// one tag on each line:
//
//     <table>
//     <tr>
//     <th colspan="2" data-color="blue">
//
//     Markdown of the cell
//
//     </th>
//     </tr>
//     </table>
//
// A blank line follows the open tag of a cell and comes before its close
// tag, so the Markdown inside stays Markdown for every reader. An empty cell
// is one line, `<td></td>`. A cell carries at most these attributes, in this
// order: `colspan`, `rowspan`, `data-align` and `data-color`. A table that
// GFM can hold stays GFM (`schema.ts`). Any other HTML table stays raw text.

import type { AnyExtension, JSONContent, MarkdownRendererHelpers, MarkdownToken, RenderContext } from "@tiptap/core";
import { Extension } from "@tiptap/core";

import { hexStyle, isHighlightColor } from "./colors";
import { blockStart } from "./htmlFragment";

/** The cell alignments of a GFM table, kept in the HTML form too. */
const CELL_ALIGNS = ["left", "center", "right"];

const CELL_TYPES = ["tableCell", "tableHeader"];

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const CELL_OPEN =
  /^<(td|th)((?: colspan="[1-9]\d?")?(?: rowspan="[1-9]\d?")?(?: data-align="(?:left|center|right)")?(?: data-color="[^"<>]+")?)>(<\/\1>)?$/;
const ATTR = /([a-z-]+)="([^"]*)"/g;

const span = (value: unknown) => {
  const n = Number(value);
  return Number.isInteger(n) && n > 1 ? n : 1;
};

/** True when a paragraph or a heading in a cell has an alignment. */
function hasAlignedBlock(cell: JSONContent): boolean {
  return ((cell.content ?? []) as JSONContent[]).some(
    (b) => (b.type === "paragraph" || b.type === "heading") && Boolean(b.attrs?.align),
  );
}

/**
 * True when GFM cannot hold the table: a merged cell, a header cell outside
 * the first row, a first row that mixes header and plain cells, a cell
 * colour, or an aligned block in a cell.
 */
export function needsHtmlTable(table: JSONContent): boolean {
  const rows = (table.content ?? []) as JSONContent[];
  return rows.some((row, r) => {
    const cells = (row.content ?? []) as JSONContent[];
    if (r === 0 && new Set(cells.map((c) => c.type)).size > 1) return true;
    return cells.some(
      (c) =>
        span(c.attrs?.colspan) > 1 ||
        span(c.attrs?.rowspan) > 1 ||
        (r > 0 && c.type === "tableHeader") ||
        Boolean(c.attrs?.background) ||
        hasAlignedBlock(c),
    );
  });
}

type Helpers = Pick<MarkdownRendererHelpers, "renderChildren"> & Partial<Pick<MarkdownRendererHelpers, "renderChild">>;

/** The HTML form of a table, one tag on each line. */
export function renderHtmlTable(table: JSONContent, h: Helpers): string {
  const out = ["<table>"];
  for (const row of (table.content ?? []) as JSONContent[]) {
    out.push("<tr>");
    for (const cell of (row.content ?? []) as JSONContent[]) {
      const tag = cell.type === "tableHeader" ? "th" : "td";
      let attrs = "";
      if (span(cell.attrs?.colspan) > 1) attrs += ` colspan="${span(cell.attrs?.colspan)}"`;
      if (span(cell.attrs?.rowspan) > 1) attrs += ` rowspan="${span(cell.attrs?.rowspan)}"`;
      if (CELL_ALIGNS.includes(String(cell.attrs?.align))) attrs += ` data-align="${cell.attrs?.align}"`;
      if (isHighlightColor(cell.attrs?.background)) attrs += ` data-color="${cell.attrs?.background}"`;
      const body = ((cell.content ?? []) as JSONContent[])
        .map((block, i) => h.renderChild?.(block, i) ?? h.renderChildren([block]))
        .filter((b) => b.trim())
        .join("\n\n")
        .trim();
      out.push(body ? `<${tag}${attrs}>\n\n${body}\n\n</${tag}>` : `<${tag}${attrs}></${tag}>`);
    }
    out.push("</tr>");
  }
  out.push("</table>");
  return out.join("\n");
}

/** One cell of a read table. */
export interface HtmlCell {
  header: boolean;
  attrs: Record<string, string>;
  body: string;
}

/**
 * The HTML table at the start of `src`, or `null` when the shape is not
 * exact. Inside a cell, a line that is exactly `<table>` or `</table>`, or
 * an open or close line of a cell, counts one level, so a table in a cell
 * pairs with its own tags. A code fence is text.
 */
export function readHtmlTable(src: string): { raw: string; rows: HtmlCell[][] } | null {
  const lines = src.split("\n");
  if (lines[0] !== "<table>") return null;
  const rows: HtmlCell[][] = [];
  let i = 1;
  for (;;) {
    if (i >= lines.length) return null;
    const line = lines[i];
    if (line === "</table>") break;
    if (line !== "<tr>") return null;
    i++;
    const cells: HtmlCell[] = [];
    for (;;) {
      if (i >= lines.length) return null;
      const cellLine = lines[i];
      if (cellLine === "</tr>") break;
      const open = CELL_OPEN.exec(cellLine);
      if (!open) return null;
      const attrs: Record<string, string> = {};
      for (const m of open[2].matchAll(ATTR)) attrs[m[1]] = m[2];
      if (attrs["data-color"] !== undefined && !isHighlightColor(attrs["data-color"])) return null;
      const header = open[1] === "th";
      if (open[3]) {
        cells.push({ header, attrs, body: "" });
        i++;
        continue;
      }
      const close = `</${open[1]}>`;
      const start = i + 1;
      let depth = 1;
      let fence: string | null = null;
      for (i = start; i < lines.length; i++) {
        const l = lines[i];
        const f = FENCE.exec(l);
        if (fence) {
          if (f && f[1][0] === fence[0] && f[1].length >= fence.length && !l.slice(f[0].length).trim()) fence = null;
          continue;
        }
        if (f) {
          fence = f[1];
          continue;
        }
        if (l === close && depth === 1) break;
        if (l === "<table>") depth++;
        else if (l === "</table>") depth--;
      }
      if (i >= lines.length) return null;
      // The exact shape: a blank line after the open tag and before the close.
      if (i - start < 2 || lines[start].trim() || lines[i - 1].trim()) return null;
      const body = lines.slice(start, i).join("\n");
      cells.push({ header, attrs, body: body.replace(/^(?:[ \t]*\n)+/, "").replace(/\s+$/, "") });
      i++;
    }
    if (!cells.length) return null;
    rows.push(cells);
    i++;
  }
  if (!rows.length) return null;
  const end = i + 1;
  return { raw: lines.slice(0, end).join("\n") + (end < lines.length ? "\n" : ""), rows };
}

type Render = (node: JSONContent, h: MarkdownRendererHelpers, ctx: RenderContext) => string;

/**
 * The table of the schema, which writes HTML when GFM cannot hold it.
 * `schema.ts` passes its table through this.
 */
export function withHtmlTable<T extends AnyExtension>(ext: T): T {
  return ext.extend({
    renderMarkdown(this: { parent?: Render | null }, node: JSONContent, h: MarkdownRendererHelpers, ctx: RenderContext) {
      return needsHtmlTable(node) ? renderHtmlTable(node, h) : (this.parent?.(node, h, ctx) ?? "");
    },
  }) as T;
}

/** The cell colour attribute, and the reader of the HTML form. */
export const KbHtmlTable = Extension.create({
  name: "kbHtmlTable",

  addGlobalAttributes() {
    return [
      {
        types: CELL_TYPES,
        attributes: {
          background: {
            default: null,
            parseHTML: (el: HTMLElement) => {
              const value = el.getAttribute("data-color");
              return isHighlightColor(value) ? value : null;
            },
            renderHTML: (attrs: Record<string, unknown>) => {
              if (!isHighlightColor(attrs.background)) return {};
              const style = hexStyle("background-color", attrs.background);
              return { "data-color": attrs.background, ...(style ? { style } : {}) };
            },
          },
        },
      },
    ];
  },

  markdownTokenName: "kbHtmlTable",

  markdownTokenizer: {
    name: "kbHtmlTable",
    level: "block",
    start: (src: string) => blockStart(src, /\n<table>\n/),
    tokenize: (src, _tokens, lexer) => {
      const found = readHtmlTable(src);
      if (!found) return undefined;
      return {
        type: "kbHtmlTable",
        raw: found.raw,
        rows: found.rows.map((cells) =>
          cells.map((c) => ({ ...c, tokens: c.body ? lexer.blockTokens(c.body) : [] })),
        ),
      };
    },
  },

  parseMarkdown: (token, h) => {
    const rows = (token.rows ?? []) as Array<Array<HtmlCell & { tokens: MarkdownToken[] }>>;
    return h.createNode(
      "table",
      {},
      rows.map((cells) =>
        h.createNode(
          "tableRow",
          {},
          cells.map((c) => {
            const children = (h.parseBlockChildren ?? h.parseChildren)(c.tokens);
            const attrs: Record<string, unknown> = {};
            if (c.attrs.colspan) attrs.colspan = Number(c.attrs.colspan);
            if (c.attrs.rowspan) attrs.rowspan = Number(c.attrs.rowspan);
            if (c.attrs["data-align"]) attrs.align = c.attrs["data-align"];
            if (c.attrs["data-color"]) attrs.background = c.attrs["data-color"];
            return h.createNode(
              c.header ? "tableHeader" : "tableCell",
              attrs,
              children.length ? children : [h.createNode("paragraph", {}, [])],
            );
          }),
        ),
      ),
    );
  },
});
