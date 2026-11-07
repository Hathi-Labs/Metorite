"use client";

/**
 * The change bar of the Studio IDE (`ai_studio.md` §14, slice G5). It sits above the editor tabs and
 * says what the newest change that landed in the draft did, in plain words, with names in « ».
 * "Undo" sends the earlier draft through the one save. A second press puts the change back, because
 * only one step is kept. It is NOT in the shell's dock (D89): the dock is the shell's one seam.
 */
import { useState } from "react";

import Button from "@/components/ui/Button";
import { barHeading, barLines, undoBlock, undoLabel, UNDO_NOTE, type Landed } from "@/lib/studio/landedChange";

export interface ChangeBarProps {
  landed: Landed | null;
  dirty: boolean;
  saving: boolean;
  fault: boolean;
  undoing: boolean;
  note: string | null;
  /** Why Undo would drop something that is live, or null. */
  liveBlock?: string | null;
  onUndo: () => void;
  onReload: () => void;
}

export default function ChangeBar({ landed, dirty, saving, fault, undoing, note, liveBlock = null, onUndo, onReload }: ChangeBarProps) {
  const [all, setAll] = useState(false);
  // A new change starts with the short list again.
  const [seen, setSeen] = useState(landed);
  if (landed !== seen) { setSeen(landed); setAll(false); }
  if (!landed) return null;
  const { lines, hidden } = barLines(landed, all);
  const block = undoBlock({ dirty, saving, fault, working: undoing, liveDrop: liveBlock });
  const label = undoLabel(landed.who);
  return (
    <div data-change-bar className="border-b border-border bg-secondary/40 px-3 py-1.5 text-xs">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-foreground">{barHeading(landed.who)}</p>
          <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
            {lines.map((l, i) => <li key={i} className="break-words">{l}</li>)}
          </ul>
          {hidden > 0 ? (
            <button type="button" className="mt-0.5 text-primary underline" onClick={() => setAll(true)}>
              Show all ({hidden} more)
            </button>
          ) : null}
          {all && landed.changes.length > 3 ? (
            <button type="button" className="mt-0.5 text-primary underline" onClick={() => setAll(false)}>Show fewer</button>
          ) : null}
        </div>
        <Button size="sm" variant="secondary" icon="Undo2" loading={undoing} disabled={block !== null} onClick={onUndo}
          title={block ?? `${label} the last change in the draft`}>
          {label}
        </Button>
      </div>
      <p className="mt-1 text-muted-foreground">{block ? `${block} ${UNDO_NOTE}` : UNDO_NOTE}</p>
      {note ? (
        <div role="alert" className="mt-1 flex flex-wrap items-center gap-2 text-destructive">
          <span className="min-w-0 break-words">{note}</span>
          <Button size="sm" variant="secondary" icon="RefreshCw" onClick={onReload}>Reload</Button>
        </div>
      ) : null}
    </div>
  );
}
