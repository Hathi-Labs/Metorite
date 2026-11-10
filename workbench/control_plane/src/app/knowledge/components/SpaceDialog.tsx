"use client";

/**
 * The New space form and the Space settings form (WS-61 KB-2q). One dialog
 * with two modes: `new` makes a team space, and `edit` changes the name, the
 * icon and the purpose of one that exists.
 *
 * The form has no who-can-see control: that is KB-2r. A new space starts with
 * no grants, so its manager sees it and nobody else does yet.
 *
 * ⚠️ A `me` space and the stand-in "My notes" never reach `edit`
 * (`spaceForm.canEditSpace`), so the stand-in id never goes to the gateway.
 */

import { useId, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input, { Textarea } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import { KnowledgeError, createSpace, updateSpace } from "../lib/api";
import {
  DEFAULT_SPACE_ICON,
  SPACE_ADVICE,
  SPACE_ICON_CHOICES,
  SPACE_NAME_MAX,
  SPACE_PURPOSE_MAX,
  createSpaceBody,
  patchSpaceBody,
} from "../lib/spaceForm";
import type { Space } from "../lib/types";

/** What the dialog is for. `null` closes it. */
export type SpaceDialogTarget = { mode: "new" } | { mode: "edit"; space: Space } | null;

export default function SpaceDialog({
  target,
  onClose,
  onSaved,
}: {
  target: SpaceDialogTarget;
  onClose: () => void;
  /** The gateway saved the space. The caller re-reads the spaces. */
  onSaved: (space: Space, mode: "new" | "edit") => void;
}) {
  return (
    <Modal
      open={target !== null}
      onClose={onClose}
      title={target?.mode === "edit" ? "Space settings" : "New space"}
      icon={target?.mode === "edit" ? "Settings" : "FolderPlus"}
      size="sm"
      closeLabel="Cancel"
    >
      {target ? <SpaceForm key={target.mode === "edit" ? target.space.id : "new"} target={target} onClose={onClose} onSaved={onSaved} /> : null}
    </Modal>
  );
}

function SpaceForm({
  target,
  onClose,
  onSaved,
}: {
  target: NonNullable<SpaceDialogTarget>;
  onClose: () => void;
  onSaved: (space: Space, mode: "new" | "edit") => void;
}) {
  const editing = target.mode === "edit" ? target.space : null;
  const saved = { name: editing?.name ?? "", icon: editing?.icon ?? "", purpose: editing?.purpose ?? "" };
  const [name, setName] = useState(saved.name);
  const [icon, setIcon] = useState(editing ? saved.icon : DEFAULT_SPACE_ICON);
  const [purpose, setPurpose] = useState(saved.purpose);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const purposeField = useId();

  const change = editing ? patchSpaceBody(saved, { name, icon, purpose }) : { name };
  const ready = name.trim().length > 0 && change !== null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || saving) return;
    setSaving(true);
    setError(null);
    try {
      const done = editing
        ? await updateSpace(editing.id, change ?? {})
        : await createSpace(createSpaceBody({ name, icon, purpose }));
      onSaved(done, target.mode);
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setError(`Could not ${editing ? "save the space" : "make the space"}. ${reason}`);
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="contents" data-space-form={target.mode}>
      <div className="space-y-3 p-4">
        {editing ? null : <p className="text-xs text-muted-foreground">{SPACE_ADVICE}</p>}
        <label className="block space-y-1">
          <span className="text-xs font-medium text-foreground">Name</span>
          <Input
            autoFocus
            value={name}
            maxLength={SPACE_NAME_MAX}
            onChange={(e) => setName(e.target.value)}
            placeholder="For example: Sales playbooks"
            className="w-full"
          />
        </label>
        <div className="space-y-1">
          <p className="text-xs font-medium text-foreground">Icon</p>
          <div role="radiogroup" aria-label="Space icon" className="grid max-h-36 grid-cols-8 gap-1 overflow-y-auto pr-1">
            {SPACE_ICON_CHOICES.map((choice) => (
              // A radio, not a toggle: `selected` styles it and leaves `role` alone.
              <Button
                key={choice}
                type="button"
                variant="ghost"
                size="icon"
                role="radio"
                aria-checked={choice === icon}
                aria-label={choice}
                selected={choice === icon}
                onClick={() => setIcon(choice)}
              >
                <Icon name={choice} className="h-4 w-4" />
              </Button>
            ))}
          </div>
        </div>
        <div className="space-y-1">
          <label htmlFor={purposeField} className="block text-xs font-medium text-foreground">
            Purpose (optional)
          </label>
          <Textarea
            id={purposeField}
            rows={3}
            value={purpose}
            maxLength={SPACE_PURPOSE_MAX}
            onChange={(e) => setPurpose(e.target.value)}
            placeholder="What this space is for, in a line"
            className="w-full"
          />
        </div>
        {error ? (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </div>
      <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button type="button" variant="secondary" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={saving} disabled={saving || !ready}>
          {editing ? "Save" : "Make space"}
        </Button>
      </footer>
    </form>
  );
}
