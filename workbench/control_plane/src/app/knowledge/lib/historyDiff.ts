// ── The compare of two versions (WS-61 KB-2g, `knowledge_base.md` §21.7) ────
//
// The history modal draws the newer version through the page's own read-only
// editor, and marks what changed since the older one in place. This file
// holds the pure half: the words of a document, the word diff of two
// documents, and the decorations that mark it. The idea of marks in place
// comes from the page history of Docmost. No code of Docmost is copied here.
//
// The diff runs on the words of the two bodies AFTER the Markdown parse, so a
// `#`, a `**` or a `|` is not a word. A word keeps its marks in its key, so a
// word made bold or given a link counts as a change. An image, a raw HTML
// span and a divider count as one word each. The start of each block is a
// break, so two words in two paragraphs never join. A change of a block's
// type alone (a paragraph made a heading) is not marked.
//
// A deleted word draws as a widget with its text only, never as HTML and
// never as an image, so a compare makes no HTML sink (`htmlSinks.test.ts`)
// and loads no image outside `MarkdownImage`.

import type { Node as PMNode } from "@tiptap/pm/model";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import { diffKeys, MAX_EDITS } from "./wordDiff";

/** The key of the start of a block. It separates words and is never drawn. */
const BREAK = "\u0000break";

/** One word of a document, with its place. */
export interface Word {
  key: string;
  /** What a deletion shows for it. */
  text: string;
  from: number;
  to: number;
  /** An image, a raw HTML span or a divider: marked as a whole node. */
  atom: boolean;
}

/** The marks of a text node as a key, so a bold word differs from a plain one. */
function marksKey(node: PMNode): string {
  if (!node.marks.length) return "";
  return (
    node.marks
      .map((m) => (m.type.name === "link" ? `link(${String(m.attrs.href ?? "")})` : m.type.name))
      .sort()
      .join(",") + "|"
  );
}

/** What a deleted atom says, in words. Text only, never markup to parse. */
function atomText(node: PMNode): string {
  const name = node.type.name;
  if (name === "image") return `[Image: ${String(node.attrs.alt || node.attrs.src || "")}]`;
  if (name === "htmlInline") return String(node.attrs.raw ?? "");
  if (name === "horizontalRule") return "[Divider]";
  // A node with no words of its own. Its type name is not text for a member.
  return "[Item]";
}

/**
 * A short hash of a removed run (FNV-1a, 32 bits), for the key of its widget.
 * ProseMirror keeps the DOM of a widget whose key is equal, so the key must
 * change when the words change.
 */
function textHash(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}

/** The words of a document, in order, with a break at the start of each block. */
export function wordsOf(doc: PMNode): Word[] {
  const out: Word[] = [];
  doc.descendants((node, pos) => {
    if (node.isTextblock) {
      out.push({ key: BREAK, text: "", from: pos + 1, to: pos + 1, atom: false });
      return true;
    }
    if (node.isText) {
      const text = node.text ?? "";
      const marks = marksKey(node);
      for (const m of text.matchAll(/\S+/g)) {
        const from = pos + (m.index ?? 0);
        out.push({ key: marks + m[0], text: m[0], from, to: from + m[0].length, atom: false });
      }
      return false;
    }
    if (node.type.name === "hardBreak") {
      out.push({ key: BREAK, text: "", from: pos, to: pos, atom: false });
      return false;
    }
    if (node.isLeaf || node.isAtom) {
      out.push({
        key: `\u0001${node.type.name}:${JSON.stringify(node.attrs)}|${marksKey(node)}`,
        text: atomText(node),
        from: pos,
        to: pos + node.nodeSize,
        atom: true,
      });
      return false;
    }
    return true;
  });
  return out;
}

/** One change: the words that went out and came in at one place. */
export interface Change {
  /** From 1, in the order of the document. */
  index: number;
  /** The text of the words that went out, joined by a space. */
  removed: string;
  removedWords: number;
  /** Where the removed words draw in the newer document. */
  removedAt: number;
  /** The ranges of the words that came in, in the newer document. */
  added: Array<{ from: number; to: number; atom: boolean }>;
  addedWords: number;
}

export interface Compare {
  changes: Change[];
  addedWords: number;
  removedWords: number;
  /** True when the versions differ too much to mark each word. */
  coarse: boolean;
}

/** Join the added words of one change into ranges: one per run of text, one per atom. */
function addedRanges(words: Word[]): Change["added"] {
  const out: Change["added"] = [];
  let run: { from: number; to: number } | null = null;
  const close = () => {
    if (run) out.push({ ...run, atom: false });
    run = null;
  };
  for (const w of words) {
    if (w.key === BREAK) {
      close();
    } else if (w.atom) {
      close();
      out.push({ from: w.from, to: w.to, atom: true });
    } else if (run) {
      run.to = w.to;
    } else {
      run = { from: w.from, to: w.to };
    }
  }
  close();
  return out;
}

/**
 * The compare of `older` and `newer`. Both documents come from one schema.
 * A change holds only breaks when a block was split or joined with no word
 * changed, and then it is left out.
 */
export function compareDocs(older: PMNode, newer: PMNode, maxEdits = MAX_EDITS): Compare {
  const a = wordsOf(older);
  const b = wordsOf(newer);
  const { steps, coarse } = diffKeys(
    a.map((w) => w.key),
    b.map((w) => w.key),
    maxEdits,
  );
  const changes: Change[] = [];
  let addedWords = 0;
  let removedWords = 0;
  // The end of the last word of the newer document before the change.
  let lastB = 0;
  let out: Word[] = [];
  let into: Word[] = [];
  let startB = 0;

  const flush = () => {
    const gone = out.filter((w) => w.key !== BREAK);
    const come = into.filter((w) => w.key !== BREAK);
    if (gone.length || come.length) {
      const removedAt = come.length ? come[0].from : startB;
      changes.push({
        index: changes.length + 1,
        removed: gone.map((w) => w.text).join(" "),
        removedWords: gone.length,
        removedAt,
        added: addedRanges(into),
        addedWords: come.length,
      });
      addedWords += come.length;
      removedWords += gone.length;
    }
    out = [];
    into = [];
  };

  for (const s of steps) {
    if (s.kind === "equal") {
      if (out.length || into.length) flush();
      lastB = b[s.b].to;
      continue;
    }
    if (!out.length && !into.length) startB = lastB;
    if (s.kind === "delete") out.push(a[s.a]);
    else {
      into.push(b[s.b]);
      lastB = b[s.b].to;
    }
  }
  if (out.length || into.length) flush();
  return { changes, addedWords, removedWords, coarse };
}

/** The class of an added run, an added atom and a removed run (`globals.css`). */
export const DIFF_CLASSES = {
  added: "kb-diff-added",
  addedNode: "kb-diff-node-added",
  removed: "kb-diff-removed",
  current: "kb-diff-current",
} as const;

/**
 * The decorations of a compare on the newer document. The change at
 * `current` (from 1) carries one more class, so the member sees which change
 * the arrows moved to.
 */
export function compareDecorations(doc: PMNode, compare: Compare, current = 0): DecorationSet {
  const decorations: Decoration[] = [];
  const size = doc.content.size;
  for (const change of compare.changes) {
    const index = String(change.index);
    const now = change.index === current ? ` ${DIFF_CLASSES.current}` : "";
    for (const r of change.added) {
      if (r.from < 0 || r.to > size || r.from >= r.to) continue;
      decorations.push(
        r.atom
          ? Decoration.node(r.from, r.to, { class: DIFF_CLASSES.addedNode + now, "data-diff-index": index })
          : Decoration.inline(r.from, r.to, { class: DIFF_CLASSES.added + now, "data-diff-index": index }),
      );
    }
    if (change.removedWords) {
      const at = Math.max(0, Math.min(change.removedAt, size));
      const text = change.removed;
      decorations.push(
        Decoration.widget(
          at,
          () => {
            const del = document.createElement("del");
            del.className = DIFF_CLASSES.removed + now;
            del.setAttribute("data-diff-index", index);
            // Text only: a deleted word is never parsed as HTML.
            del.textContent = text;
            return del;
          },
          { side: -1, key: `kb-diff-${index}-${now ? "on" : "off"}-${text.length}-${textHash(text)}` },
        ),
      );
    }
  }
  return DecorationSet.create(doc, decorations);
}

/** "3 changes", "1 change". */
export function changeCount(n: number): string {
  return `${n} ${n === 1 ? "change" : "changes"}`;
}

/** "+12 words, −4 words" for the counts of a compare. */
export function wordCounts(compare: Pick<Compare, "addedWords" | "removedWords">): string {
  const w = (n: number) => `${n} ${n === 1 ? "word" : "words"}`;
  return `${w(compare.addedWords)} added, ${w(compare.removedWords)} removed`;
}
