// Source MIT - https://github.com/buttondown/tiptap-footnotes
// Adapted from Docmost (AGPL-3.0), packages/editor-ext/src/lib/footnotes/reference.ts,
// footnote.ts, footnotes.ts, rules.ts and utils.ts
//
// ── Footnotes as real nodes (WS-61 KB-2i, `knowledge_base.md` §21.7) ───────
//
// Pure: no React and no DOM at import, so the round-trip test runs in node.
//
// GFM writes a footnote as a mark in the text, `[^label]`, and a definition
// that starts a block, `[^label]: text`. Both keep their label byte for
// byte: a save never renumbers a footnote, and a definition stays where the
// body wrote it. Docmost moves every note to a list at the end and numbers
// the marks again. That would change the stored body, so we do not.
//
// What we keep from Docmost:
//
// - The mark and the note draw with a number, in the order of the marks.
// - A click on a mark in the read view goes to its note. In an edit, the
//   first click selects the mark and the second goes to the note.
// - `/Footnote` and `[^label]` add a mark and an empty note at the end.
// - When a member deletes the last mark of a label, its note goes too. A
//   pasted mark with no note gets an empty one.

import { InputRule, Node, mergeAttributes, type JSONContent, type MarkdownToken } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { NodeSelection, Plugin, PluginKey, TextSelection, type EditorState, type Transaction } from "@tiptap/pm/state";
import { ReplaceStep } from "@tiptap/pm/transform";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import { FOOTNOTE_DEF, FOOTNOTE_REF } from "./inline";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    footnoteReference: {
      /** Add a mark with the next free number at the caret, and its empty note at the end. */
      addFootnote: () => ReturnType;
      /** Put the caret at the end of the note of this label. */
      focusFootnote: (label: string) => ReturnType;
    };
  }
}

/** The label of a mark or a note: no space and no `]`, 1 to 100 characters. */
export function isFootnoteLabel(label: unknown): label is string {
  return typeof label === "string" && /^[^\]\s]{1,100}$/.test(label);
}

/** The label after `[^` in a mark or a note. */
function labelOf(raw: string): string {
  const m = /^\[\^([^\]\s]{1,100})\]/.exec(raw);
  return m ? m[1] : "";
}

/**
 * The text of a note: the line after `[^label]:`, and each indented line
 * after it, with its indent of four spaces or a tab taken off.
 */
export function footnoteBody(raw: string): string {
  const lines = raw.replace(/^\[\^[^\]\s]{1,100}\]:[ \t]?/, "").split("\n");
  return lines.map((line, i) => (i === 0 ? line : line.replace(/^(?: {4}|\t)/, ""))).join("\n");
}

/** A note as Markdown: the first line after `[^label]:`, and the rest indented. */
export function renderFootnote(label: string, body: string): string {
  const text = body.replace(/\s+$/, "");
  if (!text) return `[^${label}]:`;
  const [first, ...rest] = text.split("\n");
  const tail = rest.map((line) => (line.trim() === "" ? "" : `    ${line}`));
  return [`[^${label}]: ${first}`, ...tail].join("\n");
}

/** The next free number among the labels in use. */
export function nextFootnoteLabel(labels: Iterable<string>): string {
  let max = 0;
  for (const label of labels) if (/^\d{1,6}$/.test(label)) max = Math.max(max, Number(label));
  return String(max + 1);
}

/**
 * The number each label draws with: the order of its first mark. A note
 * with no mark keeps its own label.
 */
export function footnoteNumbers(doc: PMNode): Map<string, number> {
  const numbers = new Map<string, number>();
  doc.descendants((node) => {
    if (node.type.name === REF && !numbers.has(node.attrs.label)) numbers.set(node.attrs.label, numbers.size + 1);
  });
  return numbers;
}

const REF = "footnoteReference";
const DEF = "footnoteDefinition";

/** A CSS string of a label, for the custom property that draws it. */
function cssString(text: string): string {
  return `"${text.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** The labels of every mark and every note in a document. */
function labelsIn(doc: PMNode, type: string): string[] {
  const out: string[] = [];
  doc.descendants((node) => {
    if (node.type.name === type) out.push(String(node.attrs.label));
  });
  return out;
}

/** Each note of a label, with its place, last first so a delete keeps the others. */
function notesOf(doc: PMNode, label: string): Array<{ pos: number; node: PMNode }> {
  const out: Array<{ pos: number; node: PMNode }> = [];
  doc.descendants((node, pos) => {
    if (node.type.name === DEF && node.attrs.label === label) out.push({ pos, node });
  });
  return out.reverse();
}

/** Append an empty note of `label` at the end of the document. */
function appendNote(tr: Transaction, state: EditorState, label: string): number {
  const def = state.schema.nodes[DEF];
  const paragraph = state.schema.nodes.paragraph;
  const at = tr.doc.content.size;
  tr.insert(at, def.create({ label }, paragraph.create()));
  return at;
}

const numbersKey = new PluginKey<DecorationSet>("kbFootnoteNumbers");
const rulesKey = new PluginKey("kbFootnoteRules");

/** The number of each mark and note, drawn through `--kb-fn-label`. */
function numberDecorations(doc: PMNode): DecorationSet {
  const numbers = footnoteNumbers(doc);
  const decorations: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (node.type.name !== REF && node.type.name !== DEF) return;
    const label = String(node.attrs.label);
    const shown = numbers.has(label) ? String(numbers.get(label)) : label;
    decorations.push(
      Decoration.node(pos, pos + node.nodeSize, {
        style: `--kb-fn-label: ${cssString(shown)}`,
        ...(node.type.name === REF ? { "aria-label": `Footnote ${shown}` } : {}),
      }),
    );
  });
  return DecorationSet.create(doc, decorations);
}

/** True when a step of the transaction replaced the whole document. */
function replacesAll(tr: Transaction): boolean {
  return tr.steps.some((step, i) => {
    if (!(step instanceof ReplaceStep)) return false;
    const before = tr.docs[i];
    return step.from === 0 && step.to === before.content.size;
  });
}

/**
 * A mark in the text: `[^label]`. An inline atom that draws its number.
 */
export const FootnoteReference = Node.create({
  name: REF,
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,

  addAttributes() {
    return {
      label: {
        default: "1",
        parseHTML: (el: HTMLElement) => el.getAttribute("data-footnote-ref") || "1",
        renderHTML: (attrs: Record<string, unknown>) => ({ "data-footnote-ref": attrs.label }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "sup[data-footnote-ref]" }];
  },

  renderHTML({ HTMLAttributes }) {
    return ["sup", mergeAttributes({ class: "kb-footnote-ref" }, HTMLAttributes)];
  },

  renderText({ node }) {
    return `[^${node.attrs.label}]`;
  },

  markdownTokenizer: {
    name: REF,
    level: "inline",
    start: (src: string) => src.indexOf("[^"),
    tokenize: (src: string) => {
      const m = FOOTNOTE_REF.exec(src);
      return m ? { type: REF, raw: m[0], text: m[0] } : undefined;
    },
  },

  parseMarkdown: (token: MarkdownToken, h) => h.createNode(REF, { label: labelOf(String(token.raw ?? "")) }),

  renderMarkdown: (node: JSONContent) => `[^${String(node.attrs?.label ?? "1")}]`,

  addCommands() {
    return {
      addFootnote:
        () =>
        ({ state, tr, dispatch }) => {
          const label = nextFootnoteLabel([...labelsIn(state.doc, REF), ...labelsIn(state.doc, DEF)]);
          if (!dispatch) return true;
          tr.replaceSelectionWith(this.type.create({ label }), false);
          const at = appendNote(tr, state, label);
          // The caret goes into the new note, so the member writes it next.
          tr.setSelection(TextSelection.create(tr.doc, at + 2));
          tr.scrollIntoView();
          return true;
        },
      focusFootnote:
        (label) =>
        ({ state, tr, dispatch, view }) => {
          const [note] = notesOf(state.doc, label).reverse();
          if (!note) return false;
          if (dispatch) {
            tr.setSelection(TextSelection.near(state.doc.resolve(note.pos + note.node.nodeSize - 1), -1));
            tr.scrollIntoView();
            view?.focus();
          }
          return true;
        },
    };
  },

  addInputRules() {
    // `[^note]` typed in the text becomes a mark, with a note when none exists.
    return [
      new InputRule({
        find: /\[\^([^\]\s]{1,100})\]$/,
        handler: ({ state, range, match }) => {
          const label = match[1];
          const { tr } = state;
          tr.replaceWith(range.from, range.to, this.type.create({ label }));
          if (!labelsIn(state.doc, DEF).includes(label)) appendNote(tr, state, label);
        },
      }),
    ];
  },

  addProseMirrorPlugins() {
    const editor = this.editor;
    return [
      new Plugin({
        key: numbersKey,
        state: {
          init: (_config, state) => numberDecorations(state.doc),
          apply: (tr, old) => (tr.docChanged ? numberDecorations(tr.doc) : old),
        },
        props: {
          decorations(state) {
            return numbersKey.getState(state);
          },
          // Read view: a click on a mark goes to its note. Edit: the first
          // click selects the mark, and a second goes to the note.
          handleClickOn(view, _pos, node, nodePos, event) {
            if (node.type.name !== REF) return false;
            const label = String(node.attrs.label);
            if (!view.editable) {
              event.preventDefault();
              const target = view.dom.querySelector<HTMLElement>(`[data-footnote="${CSS.escape(label)}"]`);
              target?.scrollIntoView({ block: "center" });
              return true;
            }
            const { selection } = view.state;
            if (selection instanceof NodeSelection && selection.from === nodePos) {
              return editor.commands.focusFootnote(label);
            }
            view.dispatch(view.state.tr.setSelection(NodeSelection.create(view.state.doc, nodePos)));
            return true;
          },
          handleDoubleClickOn(_view, _pos, node) {
            if (node.type.name !== REF) return false;
            return editor.commands.focusFootnote(String(node.attrs.label));
          },
        },
      }),
      new Plugin({
        key: rulesKey,
        appendTransaction(transactions, oldState, newState) {
          if (!editor.isEditable) return null;
          const edits = transactions.filter((t) => t.docChanged && !t.getMeta("preventUpdate") && !replacesAll(t));
          if (!edits.length) return null;

          // The labels of the marks that a step put in.
          const pasted = new Set<string>();
          for (const t of edits) {
            for (const step of t.steps) {
              if (!(step instanceof ReplaceStep)) continue;
              step.slice.content.descendants((node) => {
                if (node.type.name === REF) pasted.add(String(node.attrs.label));
              });
            }
          }
          const before = new Set(labelsIn(oldState.doc, REF));
          const after = new Set(labelsIn(newState.doc, REF));
          const lost = [...before].filter((label) => !after.has(label));
          const notes = new Set(labelsIn(newState.doc, DEF));
          const orphans = [...pasted].filter((label) => after.has(label) && !notes.has(label));
          if (!lost.length && !orphans.length) return null;

          const tr = newState.tr;
          const hadNote = new Set(labelsIn(oldState.doc, DEF));
          for (const label of lost) {
            if (!hadNote.has(label)) continue;
            for (const note of notesOf(tr.doc, label)) tr.delete(note.pos, note.pos + note.node.nodeSize);
          }
          for (const label of orphans) appendNote(tr, newState, label);
          return tr.docChanged ? tr : null;
        },
      }),
    ];
  },
});

/**
 * A note: `[^label]: text`, with indented lines after it. It holds blocks,
 * and it stays where the body wrote it.
 */
export const FootnoteDefinition = Node.create({
  name: DEF,
  group: "block",
  content: "block+",
  defining: true,
  isolating: true,

  addAttributes() {
    return {
      label: {
        default: "1",
        parseHTML: (el: HTMLElement) => el.getAttribute("data-footnote") || "1",
        renderHTML: (attrs: Record<string, unknown>) => ({ "data-footnote": attrs.label }),
      },
    };
  },

  parseHTML() {
    return [{ tag: "div[data-footnote]", contentElement: ".kb-footnote-body" }];
  },

  renderHTML({ HTMLAttributes }) {
    return [
      "div",
      mergeAttributes({ class: "kb-footnote" }, HTMLAttributes),
      ["span", { class: "kb-footnote-label", contenteditable: "false", "aria-hidden": "true" }],
      ["div", { class: "kb-footnote-body" }, 0],
    ];
  },

  markdownTokenizer: {
    name: DEF,
    level: "block",
    start: (src: string) => {
      const m = /\n\[\^[^\]\s]{1,100}\]:/.exec(src);
      return m ? m.index + 1 : -1;
    },
    tokenize: (src, _tokens, lexer) => {
      const m = FOOTNOTE_DEF.exec(src);
      if (!m) return undefined;
      const end = /^\n?/.exec(src.slice(m[0].length))?.[0] ?? "";
      const body = footnoteBody(m[0]);
      return {
        type: DEF,
        raw: m[0] + end,
        text: m[0],
        label: labelOf(m[0]),
        tokens: body.trim() ? lexer.blockTokens(body) : [],
      };
    },
  },

  parseMarkdown: (token, h) => {
    const children = (h.parseBlockChildren ?? h.parseChildren)(token.tokens ?? []);
    const label = isFootnoteLabel(token.label) ? token.label : "1";
    return h.createNode(DEF, { label }, children.length ? children : [h.createNode("paragraph", {}, [])]);
  },

  renderMarkdown: (node, h) => {
    const label = isFootnoteLabel(node.attrs?.label) ? node.attrs.label : "1";
    const blocks = ((node.content ?? []) as JSONContent[]).map(
      (child, i) => h.renderChild?.(child, i) ?? h.renderChildren([child]),
    );
    return renderFootnote(label, blocks.join("\n\n"));
  },
});
