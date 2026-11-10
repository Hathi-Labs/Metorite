"use client";

/**
 * The one New page form (WS-61 KB-2a, `knowledge_base.md` §4.1 and §5.3).
 *
 * A title and a space. The form offers only the spaces the member can write
 * in, and "My notes" too: a page made with no space lands in the member's
 * `me` space, which the gateway makes on the first write. A sub-page takes
 * its parent's space, so the form shows the parent and no space choice.
 *
 * On save the page opens in the editor, because a new page is empty.
 *
 * KB-9a: an ask may carry a title and the gap the page answers. The form
 * starts with that title, and names the question. The layout marks the gap
 * answered after the page is made.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";
import SelectButton from "@/components/ui/SelectButton";

import { KnowledgeError, createPage } from "../lib/api";
import { createBody, defaultSpaceChoice, writableSpaces } from "../lib/labels";
import type { PageWritten, Space } from "../lib/types";
import type { NewPageAsk } from "./KnowledgeContext";

export default function NewPageDialog({
  ask,
  spaces,
  onClose,
  onCreated,
}: {
  /** The open ask, or `null` when the form is closed. */
  ask: NewPageAsk | null;
  spaces: readonly Space[];
  onClose: () => void;
  onCreated: (page: PageWritten) => void;
}) {
  return (
    <Modal
      open={ask !== null}
      onClose={onClose}
      title={ask?.parent ? "New sub-page" : "New page"}
      icon="FilePlus"
      size="sm"
      closeLabel="Cancel"
    >
      {ask ? (
        <NewPageForm ask={ask} spaces={spaces} onClose={onClose} onCreated={onCreated} />
      ) : null}
    </Modal>
  );
}

function NewPageForm({
  ask,
  spaces,
  onClose,
  onCreated,
}: {
  ask: NewPageAsk;
  spaces: readonly Space[];
  onClose: () => void;
  onCreated: (page: PageWritten) => void;
}) {
  const choices = writableSpaces(spaces);
  const parent = ask.parent ?? null;
  const [title, setTitle] = useState(ask.title ?? "");
  const [space, setSpace] = useState(() =>
    parent ? parent.spaceId : defaultSpaceChoice(spaces, ask.spaceId ?? null),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parentSpace = parent ? spaces.find((s) => s.id === parent.spaceId) : null;
  const spaceField = useId();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      const page = await createPage(createBody({ space, parentId: parent?.id, title }));
      onCreated(page);
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setError(`Could not make the page. ${reason}`);
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="contents">
      <div className="space-y-3 p-4">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-foreground">Title</span>
          <Input
            autoFocus
            value={title}
            maxLength={300}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="For example: Refund policy"
            className="w-full"
          />
        </label>
        {parent ? (
          <p className="text-xs text-muted-foreground">
            Under <span className="font-medium text-foreground">{parent.title}</span>
            {parentSpace ? <> in {parentSpace.name}</> : null}
          </p>
        ) : (
          <div className="space-y-1">
            <label htmlFor={spaceField} className="block text-xs font-medium text-foreground">
              Space
            </label>
            <SelectButton
              id={spaceField}
              label="Space"
              value={space}
              options={choices}
              onChange={setSpace}
              widthClass="w-full"
            />
          </div>
        )}
        {ask.gap ? (
          <p className="text-xs text-muted-foreground" data-new-page-gap={ask.gap.id}>
            This page answers the question{" "}
            <span className="font-medium text-foreground">{ask.gap.question}</span>
          </p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          The page starts as a draft. Only you see it until you publish it.
        </p>
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
        <Button type="submit" size="sm" loading={saving} disabled={saving || !title.trim()}>
          Create page
        </Button>
      </footer>
    </form>
  );
}
