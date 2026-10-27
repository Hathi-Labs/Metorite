// ── Columns (WS-61 KB-2p, §21.7) ────────────────────────────────────────────
//
// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/columns/columns.ts
// and packages/editor-ext/src/lib/columns/column.ts
//
// Pure. Two to five columns side by side, each one holding blocks. Stored as
//
//     <div data-type="columns" data-count="2">
//
//     <div data-type="column">
//
//     Markdown of the first column
//
//     </div>
//
//     <div data-type="column">
//
//     Markdown of the second column
//
//     </div>
//
//     </div>
//
// A blank line follows each open tag and comes before each close tag, so the
// Markdown inside stays Markdown for every reader. `data-count` must match
// the number of columns, or the fragment is not a stored form and stays raw.
//
// What changed from Docmost: the stored form and its Markdown reader and
// writer are ours, there are no width layouts (the count is the layout), and
// Tab at the first or the last column leaves the editor as it does in a
// table, so a keyboard user is never held in the block.

import { Node, findParentNode, type JSONContent, type MarkdownToken, type NodeViewRenderer } from "@tiptap/core";
import { Fragment, type Node as PMNode } from "@tiptap/pm/model";
import { TextSelection } from "@tiptap/pm/state";

import { blockStart } from "./htmlFragment";

export const MIN_COLUMNS = 2;
export const MAX_COLUMNS = 5;

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    columns: {
      /** Insert columns, each with one empty paragraph. The caret goes in the first. */
      insertColumns: (count?: number) => ReturnType;
      /** Change the count of the columns around the caret. A cut column's blocks go to the last one kept. */
      setColumnCount: (count: number) => ReturnType;
    };
  }
}

const OPEN = /^ {0,3}<div data-type="columns" data-count="([2-5])">[ \t]*$/;
const COLUMN_OPEN = /^ {0,3}<div data-type="column">[ \t]*$/;
const CLOSE = /^ {0,3}<\/div>[ \t]*$/;
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const DIV_TAG = /<(\/?)div\b[^<>]*>/gi;

/**
 * The columns at the start of `src`: the count, the Markdown of each column,
 * and the raw text they span. `null` when the shape is not exact.
 *
 * A line that starts with a `<div` or a `</div` tag outside a code fence
 * opens or closes one level, so a raw `<div>` in a column pairs with its own
 * close tag and does not end the column.
 */
export function readColumns(src: string): { raw: string; count: number; bodies: string[] } | null {
  const lines = src.split("\n");
  const open = OPEN.exec(lines[0] ?? "");
  if (!open) return null;
  const count = Number(open[1]);
  const bodies: string[] = [];
  let i = 1;
  for (;;) {
    while (i < lines.length && !lines[i].trim()) i++;
    if (i >= lines.length) return null;
    if (CLOSE.test(lines[i])) break;
    if (!COLUMN_OPEN.test(lines[i])) return null;
    const start = i + 1;
    let depth = 1;
    let fence: string | null = null;
    for (i = start; i < lines.length; i++) {
      const line = lines[i];
      const f = FENCE.exec(line);
      if (fence) {
        if (f && f[1][0] === fence[0] && f[1].length >= fence.length && !line.slice(f[0].length).trim()) fence = null;
        continue;
      }
      if (f) {
        fence = f[1];
        continue;
      }
      const lead = line.trimStart().toLowerCase();
      if (!lead.startsWith("<div") && !lead.startsWith("</div")) continue;
      if (CLOSE.test(line)) {
        depth--;
        if (depth === 0) break;
        continue;
      }
      for (const tag of line.matchAll(DIV_TAG)) depth += tag[1] ? -1 : 1;
      if (depth <= 0) return null;
    }
    if (i >= lines.length) return null;
    bodies.push(lines.slice(start, i).join("\n").replace(/^(?:[ \t]*\n)+/, "").replace(/\s+$/, ""));
    i++;
  }
  if (bodies.length !== count) return null;
  const end = i + 1;
  const raw = lines.slice(0, end).join("\n") + (end < lines.length ? "\n" : "");
  return { raw, count, bodies };
}

/** The stored form of columns, from the Markdown of each column. */
export function writeColumns(bodies: readonly string[]): string {
  const cols = bodies.map((body) => `<div data-type="column">\n\n${body ? `${body}\n\n` : ""}</div>`);
  return `<div data-type="columns" data-count="${bodies.length}">\n\n${cols.join("\n\n")}\n\n</div>`;
}

const clampCount = (n: unknown) => Math.min(MAX_COLUMNS, Math.max(MIN_COLUMNS, Math.round(Number(n) || MIN_COLUMNS)));

export function createColumns(view?: NodeViewRenderer) {
  return Node.create({
    name: "columns",
    group: "block",
    content: `column{${MIN_COLUMNS},${MAX_COLUMNS}}`,
    defining: true,
    isolating: true,

    parseHTML() {
      return [{ tag: 'div[data-type="columns"]' }];
    },

    renderHTML({ node }) {
      return ["div", { "data-type": "columns", "data-count": String(node.childCount), class: "kb-columns" }, ["div", { class: "kb-columns-grid" }, 0]];
    },

    ...(view ? { addNodeView: () => view } : {}),

    addCommands() {
      return {
        insertColumns:
          (count = MIN_COLUMNS) =>
          ({ tr, state, dispatch }) => {
            const n = clampCount(count);
            const columnType = state.schema.nodes.column;
            const paragraph = state.schema.nodes.paragraph;
            const node = this.type.create(
              null,
              Fragment.from(Array.from({ length: n }, () => columnType.create(null, paragraph.create()))),
            );
            const before = tr.steps.length;
            tr.replaceSelectionWith(node);
            if (tr.steps.length > before) {
              let at = 0;
              tr.steps[tr.steps.length - 1].getMap().forEach((_from, _to, newFrom) => {
                at = newFrom;
              });
              tr.setSelection(TextSelection.near(tr.doc.resolve(at + 1), 1));
            }
            if (dispatch) dispatch(tr);
            return true;
          },

        setColumnCount:
          (count: number) =>
          ({ tr, state, dispatch }) => {
            const parent = findParentNode((node: PMNode) => node.type.name === "columns")(state.selection);
            if (!parent) return false;
            const n = clampCount(count);
            const { node: columns, pos } = parent;
            const current = columns.childCount;
            if (n === current) return false;
            const columnType = state.schema.nodes.column;
            const paragraph = state.schema.nodes.paragraph;
            const kept: PMNode[] = [];
            if (n > current) {
              columns.forEach((child) => kept.push(child));
              for (let i = current; i < n; i++) kept.push(columnType.create(null, paragraph.create()));
            } else {
              for (let i = 0; i < n - 1; i++) kept.push(columns.child(i));
              // The blocks of each cut column move to the last column kept.
              // An empty paragraph adds nothing, so it does not move.
              let merged = columns.child(n - 1).content;
              for (let j = n; j < current; j++) {
                const moved: PMNode[] = [];
                columns.child(j).content.forEach((block) => {
                  if (block.type.name !== "paragraph" || block.content.size > 0) moved.push(block);
                });
                if (moved.length) merged = merged.append(Fragment.from(moved));
              }
              kept.push(columnType.create(null, merged));
            }
            tr.replaceWith(pos, pos + columns.nodeSize, columns.type.create(columns.attrs, Fragment.from(kept)));
            tr.setSelection(TextSelection.near(tr.doc.resolve(pos + 1), 1));
            if (dispatch) dispatch(tr);
            return true;
          },
      };
    },

    markdownTokenizer: {
      name: "columns",
      level: "block",
      start: (src: string) => blockStart(src, /\n {0,3}<div data-type="columns"/),
      tokenize: (src, _tokens, lexer) => {
        const found = readColumns(src);
        if (!found) return undefined;
        return {
          type: "columns",
          raw: found.raw,
          columns: found.bodies.map((body) => (body ? lexer.blockTokens(body) : [])),
        };
      },
    },

    parseMarkdown: (token, h) => {
      const columns = ((token.columns ?? []) as MarkdownToken[][]).map((tokens) => {
        const children = (h.parseBlockChildren ?? h.parseChildren)(tokens);
        return h.createNode("column", {}, children.length ? children : [h.createNode("paragraph", {}, [])]);
      });
      return h.createNode("columns", {}, columns);
    },

    renderMarkdown: (node: JSONContent, h) => {
      const bodies = ((node.content ?? []) as JSONContent[]).map((column) =>
        ((column.content ?? []) as JSONContent[])
          .map((child, i) => h.renderChild?.(child, i) ?? h.renderChildren([child]))
          .filter((block) => block.trim())
          .join("\n\n")
          .trim(),
      );
      return writeColumns(bodies);
    },
  });
}

export const Column = Node.create({
  name: "column",
  content: "block+",
  defining: true,
  isolating: true,
  selectable: false,

  parseHTML() {
    return [{ tag: 'div[data-type="column"]' }];
  },

  renderHTML() {
    return ["div", { "data-type": "column", class: "kb-column" }, 0];
  },

  addKeyboardShortcuts() {
    // Tab moves to the next column and Shift-Tab to the one before. At either
    // end the key goes on, so Tab leaves the block. A list or a table inside
    // a column keeps its own Tab.
    const jump = (step: 1 | -1) => () => {
      const editor = this.editor;
      if (editor.isActive("listItem") || editor.isActive("taskItem") || editor.isActive("table")) return false;
      const { state, view } = editor;
      const columns = findParentNode((n: PMNode) => n.type.name === "columns")(state.selection);
      const column = findParentNode((n: PMNode) => n.type.name === "column")(state.selection);
      if (!columns || !column) return false;
      let index = -1;
      columns.node.forEach((_child, offset, i) => {
        if (columns.pos + 1 + offset === column.pos) index = i;
      });
      const target = index + step;
      if (index < 0 || target < 0 || target >= columns.node.childCount) return false;
      let offset = 0;
      for (let j = 0; j < target; j++) offset += columns.node.child(j).nodeSize;
      const at = columns.pos + 1 + offset + 1;
      view.dispatch(state.tr.setSelection(TextSelection.near(state.doc.resolve(at), 1)));
      return true;
    };
    return { Tab: jump(1), "Shift-Tab": jump(-1) };
  },
});
