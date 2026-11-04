"use client";

/**
 * The Delete button of a record, with its confirm dialog (`ai_studio.md`
 * §14 D2b-1, slice STU-D2b). It sits in the Detail sheet, and never in a Table row.
 *
 * The button opens `ConfirmDialog`. The dialog names the record and says that
 * the delete cannot be undone. Cancel sends nothing. Confirm sends one delete
 * (`lib/studio/deleteRecord.ts`). A refusal shows its words in the dialog and
 * keeps the record. Fence: `DeleteRecordButton.test.ts`.
 */
import { useMemo, useSyncExternalStore } from "react";

import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";
import { createDeleteFlow, deleteCopy } from "@/lib/studio/deleteRecord";

export interface DeleteRecordButtonProps {
  door: StudioDataDoor;
  /** The object name, as in `object:<name>.delete`. */
  object: string;
  id: string;
  /** The words that name the record in the dialog. */
  title?: string | null;
  /** The record is gone. The page picks another record. */
  onDeleted?: () => void;
}

export default function DeleteRecordButton({ door, object, id, title, onDeleted }: DeleteRecordButtonProps) {
  const flow = useMemo(() => createDeleteFlow(door, `object:${object}.delete`, id), [door, object, id]);
  const st = useSyncExternalStore(flow.subscribe, flow.getState, flow.getState);
  const copy = deleteCopy(title);
  return (
    <>
      <Button type="button" variant="secondary" size="sm" icon="Trash2" onClick={flow.ask}>
        Delete
      </Button>
      <ConfirmDialog
        open={st.open}
        title={copy.heading}
        subject={copy.subject}
        body={copy.body}
        confirmLabel={copy.confirmLabel}
        busy={st.busy}
        onCancel={flow.cancel}
        onConfirm={() => { void flow.confirm().then((gone) => { if (gone) onDeleted?.(); }); }}
      >
        {st.error ? (
          <p role="alert" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{st.error}</p>
        ) : null}
      </ConfirmDialog>
    </>
  );
}
