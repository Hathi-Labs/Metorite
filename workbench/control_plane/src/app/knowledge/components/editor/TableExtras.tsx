"use client";

/**
 * The KB-2p table controls (WS-61, `knowledge_base.md` §21.7): merge and
 * split cells, a header column, and a cell colour. A table with one of these
 * stores as HTML, because GFM has no form for them (`lib/editor/htmlTable.ts`).
 *
 * Two mounts, one set of acts:
 *
 * - `TableExtras` sits in the row of the table tools (`TableTools` in
 *   `menus.tsx`) while the caret is in a cell: split, header column, colour.
 * - `TableCellTools` is its own bubble over a selection of cells, as
 *   Docmost's cell menu is: merge, split, header column, colour. The table
 *   tools hide while cells are selected, so merge needs this bubble.
 *
 * Every swatch is a colour name, drawn through its `.kb-cell-swatch` token.
 */
import { useState } from "react";
import type { Editor } from "@tiptap/core";
import { CellSelection } from "@tiptap/pm/tables";
import { BubbleMenu } from "@tiptap/react/menus";
import { useEditorState } from "@tiptap/react";

import Button from "@/components/ui/Button";

import { HIGHLIGHT_COLORS } from "../../lib/editor/colors";

function CellActs({ editor, merge }: { editor: Editor; merge: boolean }) {
  const [colours, setColours] = useState(false);
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      canMerge: e.can().mergeCells(),
      canSplit: e.can().splitCell(),
      background:
        ((e.getAttributes("tableCell").background ?? e.getAttributes("tableHeader").background) as string | null) ?? null,
    }),
  });
  return (
    <>
      <span className="mx-1 h-5 w-px bg-border" aria-hidden />
      {merge ? (
        <Button
          variant="ghost"
          size="icon-sm"
          icon="TableCellsMerge"
          aria-label="Merge cells"
          title="Merge cells"
          disabled={!state?.canMerge}
          onClick={() => editor.chain().focus().mergeCells().run()}
        />
      ) : null}
      <Button
        variant="ghost"
        size="icon-sm"
        icon="TableCellsSplit"
        aria-label="Split cell"
        title="Split cell"
        disabled={!state?.canSplit}
        onClick={() => editor.chain().focus().splitCell().run()}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        icon="PanelLeft"
        aria-label="Turn the header column on or off"
        title="Turn the header column on or off"
        onClick={() => editor.chain().focus().toggleHeaderColumn().run()}
      />
      <span className="kb-panel-anchor relative inline-flex">
        <Button
          variant="ghost"
          size="icon-sm"
          icon="PaintBucket"
          aria-label="Cell colour"
          title="Cell colour"
          aria-haspopup="true"
          aria-expanded={colours}
          selected={colours || Boolean(state?.background)}
          onClick={() => setColours((o) => !o)}
        />
        {colours ? (
          <span role="group" aria-label="Cell colour" className="kb-bubble-panel kb-bubble-panel-end kb-color-grid">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="No cell colour"
              title="No cell colour"
              selected={!state?.background}
              onClick={() => {
                editor.chain().focus().setCellAttribute("background", null).run();
                setColours(false);
              }}
            >
              <span className="kb-cell-swatch" aria-hidden />
            </Button>
            {HIGHLIGHT_COLORS.map((c) => (
              <Button
                key={c.name}
                variant="ghost"
                size="icon-sm"
                aria-label={`${c.label} cell`}
                title={`${c.label} cell`}
                selected={state?.background === c.name}
                onClick={() => {
                  editor.chain().focus().setCellAttribute("background", c.name).run();
                  setColours(false);
                }}
              >
                <span className="kb-cell-swatch" data-color={c.name} aria-hidden />
              </Button>
            ))}
          </span>
        ) : null}
      </span>
    </>
  );
}

/** The cell acts in the row of the table tools. */
export default function TableExtras({ editor }: { editor: Editor }) {
  return <CellActs editor={editor} merge={false} />;
}

/** The bubble over a selection of cells. */
export function TableCellTools({ editor, scrollTarget }: { editor: Editor; scrollTarget?: HTMLElement | Window }) {
  return (
    <BubbleMenu
      editor={editor}
      pluginKey="kbTableCells"
      shouldShow={({ editor: e, state }) => e.isEditable && state.selection instanceof CellSelection}
      options={{ placement: "top", hide: true, shift: { padding: 8 }, scrollTarget }}
      className="kb-bubble shadow-md"
      role="toolbar"
      aria-label="Selected cells"
    >
      <div className="flex items-center gap-0.5" onMouseDown={(e) => e.preventDefault()}>
        <CellActs editor={editor} merge />
      </div>
    </BubbleMenu>
  );
}
