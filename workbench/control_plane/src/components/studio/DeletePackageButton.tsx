"use client";

/**
 * The "Delete app" action of the app page (`ai_studio.md` §14 BETA-1, task 1809).
 *
 * The button reads the delete preview, and the dialog shows the counts and asks
 * for a confirm. Confirm sends the DELETE with the token and follows each 202 with
 * its new token (`lib/studio/packageDelete.ts`). The dialog shows the progress. A
 * 409 swaps in the new counts and asks again. A 503 says to try again. A 200 calls
 * `onDeleted`, and the page returns to the library. Fence: `packageDelete.test.ts`.
 */
import { useState } from "react";

import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import {
  DELETE_LABEL, type DeletePreview, previewCopy, progressLine, readPreview, runDelete,
} from "@/lib/studio/packageDelete";

export interface DeletePackageButtonProps {
  id: string;
  /** The package is gone, or was already gone. */
  onDeleted: () => void;
}

export default function DeletePackageButton({ id, onDeleted }: DeletePackageButtonProps) {
  const [preview, setPreview] = useState<DeletePreview | null>(null);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // Outside the dialog: the preview failed, so no dialog opens.
  const [readProblem, setReadProblem] = useState<string | null>(null);

  async function ask() {
    setReading(true);
    setReadProblem(null);
    const found = await readPreview(id);
    setReading(false);
    if (found.ok) {
      setProblem(null);
      setProgress(null);
      setPreview(found.preview);
    } else if (found.gone) {
      onDeleted();
    } else {
      setReadProblem(found.message);
    }
  }

  async function confirm() {
    if (!preview || busy) return;
    setBusy(true);
    setProblem(null);
    setProgress(null);
    const end = await runDelete(id, preview.confirm, {
      onProgress: (remaining, capped) => setProgress(progressLine(remaining, capped)),
    });
    setBusy(false);
    setProgress(null);
    if (end.kind === "done" || end.kind === "gone") {
      setPreview(null);
      onDeleted();
    } else if (end.kind === "changed") {
      if (end.preview) setPreview(end.preview);
      setProblem(end.message);
    } else {
      // Busy or failed: keep the token of the last 202, so Confirm goes on from there.
      setPreview((p) => (p ? { ...p, confirm: end.confirm } : p));
      setProblem(end.message);
    }
  }

  const copy = preview ? previewCopy(preview) : null;
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="secondary" size="sm" icon="Trash2" loading={reading} onClick={() => { void ask(); }}>
          {DELETE_LABEL}
        </Button>
        {readProblem ? <span role="alert" className="text-xs text-destructive">{readProblem}</span> : null}
      </div>
      <ConfirmDialog
        open={preview !== null}
        title={copy?.heading ?? "Delete this app?"}
        subject={copy?.subject}
        body={copy?.body ?? ""}
        note={copy?.note}
        confirmLabel={problem && !busy ? "Confirm delete" : DELETE_LABEL}
        busy={busy}
        onCancel={() => { if (!busy) { setPreview(null); setProblem(null); } }}
        onConfirm={() => { void confirm(); }}
      >
        {busy ? <p role="status" className="text-xs text-muted-foreground">{progress ?? "Deleting. This can take a few seconds."}</p> : null}
        {problem ? (
          <p role="alert" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{problem}</p>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
