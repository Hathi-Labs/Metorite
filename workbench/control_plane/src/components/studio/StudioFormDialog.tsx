"use client";

/**
 * The Form block in a `Modal` (STU-AF6, `ai_studio.md` §5.5): the dialog of "New <Object>" and of
 * Edit. The Modal is a sheet on a phone. It closes on Esc, and the focus goes back to the control
 * that opened it (`Modal` does that). The form sends through `door.run` once (`StudioForm`), shows a
 * refusal under its field, and calls `onSaved` after a save that worked. This file holds no control.
 */
import { useSyncExternalStore } from "react";

import Modal from "@/components/ui/Modal";
import StudioForm, { type StudioFormProps } from "@/components/studio/StudioForm";

const PHONE = "(max-width: 639.98px)";
const subscribe = (onChange: () => void) => {
  const q = window.matchMedia(PHONE);
  q.addEventListener("change", onChange);
  return () => q.removeEventListener("change", onChange);
};
/** True below `sm`: the dialog is a sheet there. The server and the first render say false. */
function useBelowSm(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(PHONE).matches, () => false);
}

export interface StudioFormDialogProps extends Omit<StudioFormProps, "title"> {
  open: boolean;
  onClose: () => void;
  title: string;
}

export default function StudioFormDialog({ open, onClose, title, onSaved, ...form }: StudioFormDialogProps) {
  const phone = useBelowSm();
  return (
    <Modal open={open} onClose={onClose} title={title} placement={phone ? "sheet" : "center"} size="md">
      <div className="overflow-y-auto p-4">
        {/* The Modal draws the title, so the form draws none. */}
        <StudioForm {...form} onSaved={() => { onSaved?.(); onClose(); }} />
      </div>
    </Modal>
  );
}
