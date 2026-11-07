"use client";

/**
 * The edit bar of the Studio IDE (`ai_studio.md` §6.3, slice G3c). One bar serves all three editing
 * tabs: the "Unsaved changes" mark, Save and Revert, and the one line that says how the last save
 * ended. A refusal says what happened, that nothing was saved, and that the edit is kept. A stale
 * save and a newer draft offer Reload, which drops the edit and reads the stored draft.
 */
import Button from "@/components/ui/Button";
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
}

export default function EditBar({ state, dirty, newer, onSave, onRevert, onReload, hideJson = false }: EditBarProps) {
  const { saving } = state;
  const notice = hideJson && state.notice?.kind === "json" ? null : state.notice;
  const refused = notice && notice.kind !== "saved";
  return (
    <div className="border-b border-border" data-edit-bar>
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5">
        {dirty ? <UnsavedMark label /> : <span className="text-xs text-muted-foreground">No unsaved changes</span>}
        <span className="flex-1" />
        <Button size="sm" variant="secondary" icon="Undo2" disabled={!dirty || saving} onClick={onRevert}>Revert</Button>
        <Button size="sm" icon="Save" loading={saving} disabled={!dirty} onClick={onSave} title="Save (Ctrl+S or Cmd+S in the Code tab)">Save</Button>
      </div>
      {newer && !(notice?.kind === "stale") ? (
        <div role="status" className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-1.5 text-xs text-warning">
          <span className="min-w-0 break-words">{NEWER_LINE}</span>
          <Button size="sm" variant="secondary" icon="RefreshCw" onClick={onReload}>Reload</Button>
        </div>
      ) : null}
      {notice ? (
        <div role={refused ? "alert" : "status"}
          className={`flex flex-wrap items-center gap-2 border-t border-border px-3 py-1.5 text-xs ${refused ? "bg-destructive/5 text-destructive" : "text-muted-foreground"}`}>
          <span className="min-w-0 break-words">{notice.text}</span>
          {notice.kind === "stale" ? <Button size="sm" variant="secondary" icon="RefreshCw" onClick={onReload}>Reload</Button> : null}
        </div>
      ) : null}
    </div>
  );
}
