// ── The title flow of a page in an edit (WS-61 KB-2i, §21.7) ───────────────
//
// Pure: no React and no DOM at import. Docmost's title editor
// (apps/client/src/features/editor/title-editor.tsx) gives the model, and
// this file is written from it, not copied:
//
// - Enter in the title moves the text after the caret into a new first
//   paragraph of the body, and the caret goes to its start.
// - ArrowDown in the title, or ArrowRight at its end, moves the caret to
//   the start of the body.
// - ArrowUp at the very start of the body moves the caret back to the end
//   of the title. Docmost has no way back, and the spec asks for one.
//
// The title stays a plain field (`TitleField.tsx`), and this file holds
// the rules that both sides use.

import { Extension } from "@tiptap/core";
import { Selection, type EditorState } from "@tiptap/pm/state";

/** The title and the text that Enter moves into the body. */
export function splitTitle(value: string, start: number, end: number = start): { title: string; moved: string } {
  const from = Math.max(0, Math.min(start, end, value.length));
  const to = Math.max(0, Math.min(Math.max(start, end), value.length));
  return { title: value.slice(0, from), moved: value.slice(to).replace(/^\s+/, "") };
}

/** True when an empty caret sits at the first place of the body. */
export function isAtBodyStart(state: EditorState): boolean {
  const { selection, doc } = state;
  return selection.empty && selection.from === Selection.atStart(doc).from;
}

/** True when the document is one empty paragraph: an empty body. */
export function isEmptyBody(state: EditorState): boolean {
  const { doc } = state;
  return doc.childCount === 1 && doc.firstChild?.type.name === "paragraph" && doc.firstChild.content.size === 0;
}

export interface TitleHandoffStorage {
  /** The way up to the title. It returns true when it took the key. */
  exitTop: (() => boolean) | null;
}

declare module "@tiptap/core" {
  interface Storage {
    kbTitleHandoff: TitleHandoffStorage;
  }
}

/** ArrowUp at the start of the body goes to the title, when a page has one. */
export const TitleHandoff = Extension.create<object, TitleHandoffStorage>({
  name: "kbTitleHandoff",
  addStorage() {
    return { exitTop: null };
  },
  addKeyboardShortcuts() {
    return {
      ArrowUp: ({ editor }) => {
        const exit = this.storage.exitTop;
        if (!exit || !isAtBodyStart(editor.state)) return false;
        return exit();
      },
    };
  },
});
