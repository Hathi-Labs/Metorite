"use client";

/**
 * The undo/redo buttons — one pair, used by every app.
 *
 * A keyboard shortcut nobody is told about is a feature only its author has.
 * These exist so undo is DISCOVERABLE, and their tooltips carry both the
 * shortcut and what the next step would actually revert — "Undo moved Landing
 * page A/B (Ctrl+Z)" rather than "Undo".
 *
 * Disabled rather than hidden when there is nothing to undo: a control that
 * appears and disappears as you work is one you cannot aim at, and its absence
 * reads as "this app has no undo".
 */

import Button from "@/components/ui/Button";
import { useUndo } from "@/components/UndoProvider";

export function UndoControls({ className = "" }: { className?: string }) {
  const { undo, redo, canUndo, canRedo, undoLabel, redoLabel, busy } = useUndo();

  return (
    <div className={`flex items-center gap-0.5 ${className}`}>
      <Button
        variant="ghost"
        size="icon-sm"
        icon="Undo2"
        aria-label={undoLabel ? `Undo ${undoLabel}` : "Undo"}
        title={
          undoLabel ? `Undo ${undoLabel}  ·  Ctrl+Z` : "Nothing to undo  ·  Ctrl+Z"
        }
        disabled={!canUndo || busy}
        onClick={undo}
      />
      <Button
        variant="ghost"
        size="icon-sm"
        icon="Redo2"
        aria-label={redoLabel ? `Redo ${redoLabel}` : "Redo"}
        title={
          redoLabel
            ? `Redo ${redoLabel}  ·  Ctrl+Shift+Z`
            : "Nothing to redo  ·  Ctrl+Shift+Z"
        }
        disabled={!canRedo || busy}
        onClick={redo}
      />
    </div>
  );
}

export default UndoControls;
