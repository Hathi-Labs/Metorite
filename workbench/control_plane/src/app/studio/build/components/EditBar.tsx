"use client";

/**
 * The edit bar of the Studio IDE (`ai_studio.md` §6.3, slice G3c). One bar serves all three editing
 * tabs: the "Unsaved changes" mark, Save and Revert, and the one line that says how the last save
 * ended. A refusal says what happened, that nothing was saved, and that the edit is kept.
 *
 * Revert and Reload both DROP the edit, and nothing brings it back. So each one asks first, with
 * the number of changes, and offers "Copy my JSON" before it drops them. A stale save and a newer
 * draft offer Reload, which shows the stored draft.
 */
import { useState } from "react";

import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import UnsavedMark from "@/components/UnsavedMark";
import { NEWER_LINE, type EditState } from "@/lib/studio/draftEditor";

export interface EditBarProps {
  state: EditState;
  dirty: boolean;
  newer: boolean;
  onSave: () => void;
  onRevert: () => void;
  onReload: () => void;
  /** The Code tab shows the JSON fault under its editor, so the bar does not say it twice. */
  hideJson?: boolean;
  /** How many places differ from the stored draft. */
  changes: number;
  /** The text of the edit, for "Copy my JSON". */
  editText: string;
}

type Ask = "revert" | "reload" | null;

/** The words of the question. They say plainly that the edit is dropped and cannot come back. */
export function discardWords(kind: "revert" | "reload", changes: number): { title: string; body: string; confirm: string } {
  const n = changes === 1 ? "1 change" : `${changes} changes`;
  return kind === "revert"
    ? { title: "Revert your changes?", body: `Revert drops your ${n} and shows the stored draft. Nothing brings them back. Copy your JSON first if you need it.`, confirm: "Drop my changes" }
    : { title: "Reload the stored draft?", body: `Reload drops your ${n} and shows the newer stored draft. Nothing brings them back. Copy your JSON first if you need it.`, confirm: "Drop and reload" };
}

export default function EditBar({ state, dirty, newer, onSave, onRevert, onReload, hideJson = false, changes, editText }: EditBarProps) {
  const { saving } = state;
  const notice = hideJson && state.notice?.kind === "json" ? null : state.notice;
  const refused = notice && notice.kind !== "saved";
  const [ask, setAsk] = useState<Ask>(null);
  const [copied, setCopied] = useState<"yes" | "no" | null>(null);
  // With no edit open, a reload loses nothing, so it needs no question.
  const reload = () => (dirty ? setAsk("reload") : onReload());
  const words = ask ? discardWords(ask, changes) : null;
  const copy = () => {
    navigator.clipboard?.writeText(editText).then(() => setCopied("yes"), () => setCopied("no"));
  };
  const close = () => { setAsk(null); setCopied(null); };
  return (
    <div className="border-b border-border" data-edit-bar>
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5">
        {dirty ? <UnsavedMark label /> : <span className="text-xs text-muted-foreground">No unsaved changes</span>}
        <span className="flex-1" />
        <Button size="sm" variant="secondary" icon="Undo2" disabled={!dirty || saving} onClick={() => setAsk("revert")}>Revert</Button>
        <Button size="sm" icon="Save" loading={saving} disabled={!dirty} onClick={onSave} title="Save (Ctrl+S or Cmd+S in the Code tab)">Save</Button>
      </div>
      {newer && !(notice?.kind === "stale") ? (
        <div role="status" className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-1.5 text-xs text-warning">
          <span className="min-w-0 break-words">{NEWER_LINE}</span>
          <Button size="sm" variant="secondary" icon="RefreshCw" onClick={reload}>Reload</Button>
        </div>
      ) : null}
      {notice ? (
        <div role={refused ? "alert" : "status"}
          className={`flex flex-wrap items-center gap-2 border-t border-border px-3 py-1.5 text-xs ${refused ? "bg-destructive/5 text-destructive" : "text-muted-foreground"}`}>
          <span className="min-w-0 break-words">{notice.text}</span>
          {notice.kind === "stale" || (notice.kind === "failed" && dirty) ? (
            <Button size="sm" variant="secondary" icon="RefreshCw" onClick={reload}>Reload</Button>
          ) : null}
        </div>
      ) : null}
      <ConfirmDialog open={ask !== null} title={words?.title ?? ""} body={words?.body ?? ""} confirmLabel={words?.confirm ?? "Drop"}
        cancelLabel="Keep editing" confirmVariant="destructive" icon={ask === "reload" ? "RefreshCw" : "Undo2"}
        onConfirm={() => { const k = ask; close(); if (k === "revert") onRevert(); else onReload(); }} onCancel={close}>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" icon="Copy" onClick={copy}>Copy my JSON</Button>
          {copied === "yes" ? <span role="status" className="text-xs text-muted-foreground">Copied.</span> : null}
          {copied === "no" ? <span role="alert" className="text-xs text-destructive">The browser did not allow the copy. Select the text in the Code tab and copy it.</span> : null}
        </div>
      </ConfirmDialog>
    </div>
  );
}
