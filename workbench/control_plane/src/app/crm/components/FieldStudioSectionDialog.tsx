"use client";

/**
 * The name of a section in the field studio (WS-53 CRM-C4c, done-when 4).
 *
 * One `Modal` creates a section or renames one. A new section goes last, and
 * a drag moves it. The delete confirm lives in `FieldStudio.tsx`, because it
 * needs the count of the fields that move to no section.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { Input } from "@/components/ui/Input";
import type { FieldSection } from "../lib/fieldLayout";
import { LABEL_MAX, errorPart, guardClose, sectionLabelProblem } from "../lib/fieldStudio";

/** What the dialog opens on. */
export type SectionForm = { mode: "create" } | { mode: "rename"; section: FieldSection };

export default function FieldStudioSectionDialog({
  form,
  onClose,
  onSave,
}: {
  form: SectionForm | null;
  onClose: () => void;
  /** Sends the write. It throws the `CrmError` of a refusal. */
  onSave: (form: SectionForm, label: string) => Promise<void>;
}) {
  // While a save runs, Escape and the scrim do not close the dialog, so its
  // refusal keeps a place to show (review P2).
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      open={form !== null}
      onClose={guardClose(busy, onClose)}
      title={form?.mode === "rename" ? "Rename section" : "New section"}
      icon="FolderPlus"
      size="sm"
    >
      {form ? (
        <SectionFormBody
          key={form.mode === "rename" ? form.section.id : "create"}
          form={form}
          onClose={onClose}
          onSave={onSave}
          onBusy={setBusy}
        />
      ) : null}
    </Modal>
  );
}

function SectionFormBody({
  form,
  onClose,
  onSave,
  onBusy,
}: {
  form: SectionForm;
  onClose: () => void;
  onSave: (form: SectionForm, label: string) => Promise<void>;
  onBusy: (busy: boolean) => void;
}) {
  const [label, setLabel] = useState(form.mode === "rename" ? form.section.label : "");
  const [problem, setProblem] = useState<string | null>(null);
  const [saving, setSavingState] = useState(false);
  const setSaving = (busy: boolean) => {
    setSavingState(busy);
    onBusy(busy);
  };
  const id = useId();

  async function save() {
    const found = sectionLabelProblem(label);
    setProblem(found);
    if (found) return;
    setSaving(true);
    try {
      await onSave(form, label);
      onClose();
    } catch (err) {
      setProblem(errorPart(err).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="space-y-1 px-4 py-4">
        <label htmlFor={id} className="block text-xs font-medium text-foreground">
          Section name
        </label>
        <Input
          id={id}
          inputSize="sm"
          autoFocus
          maxLength={LABEL_MAX}
          value={label}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-error` : undefined}
          onChange={(e) => setLabel(e.target.value)}
        />
        {problem ? (
          <p id={`${id}-error`} role="alert" className="text-[11px] text-destructive">
            {problem}
          </p>
        ) : null}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={saving}>
          {form.mode === "rename" ? "Save" : "Add section"}
        </Button>
      </div>
    </form>
  );
}
