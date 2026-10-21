"use client";

/**
 * Columns in the Knowledge editor (WS-61 KB-2p, §21.7).
 *
 * The columns sit side by side on a wide screen and stack on a phone
 * (`globals.css`). While a member edits, a small bar above the columns
 * switches the count between 2 and 5, as Docmost's columns menu does. A
 * lower count moves the blocks of each cut column into the last column it
 * keeps, so no text is lost (`setColumnCount` in `lib/editor/columns.ts`).
 */
import { NodeViewContent, NodeViewWrapper, type NodeViewProps } from "@tiptap/react";

import Button from "@/components/ui/Button";

import { MAX_COLUMNS, MIN_COLUMNS } from "../../lib/editor/columns";

const COUNTS = Array.from({ length: MAX_COLUMNS - MIN_COLUMNS + 1 }, (_, i) => MIN_COLUMNS + i);

export default function ColumnsView({ node, editor, getPos }: NodeViewProps) {
  const count = node.childCount;
  const setCount = (n: number) => {
    const pos = typeof getPos === "function" ? getPos() : undefined;
    if (typeof pos !== "number") return;
    // The caret goes into the block first, so the command finds it.
    editor
      .chain()
      .focus()
      .setTextSelection(pos + 2)
      .setColumnCount(n)
      .run();
  };

  return (
    <NodeViewWrapper className="kb-columns" data-count={String(count)}>
      {editor.isEditable ? (
        <div className="kb-columns-bar" contentEditable={false} role="group" aria-label="Columns">
          <span className="kb-columns-label">Columns</span>
          {COUNTS.map((n) => (
            <Button
              key={n}
              variant="ghost"
              size="icon-xs"
              aria-label={`${n} columns`}
              title={`${n} columns`}
              selected={n === count}
              onClick={() => setCount(n)}
            >
              {n}
            </Button>
          ))}
        </div>
      ) : null}
      <NodeViewContent className="kb-columns-grid" />
    </NodeViewWrapper>
  );
}
