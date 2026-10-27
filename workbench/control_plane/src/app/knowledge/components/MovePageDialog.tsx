"use client";

/**
 * Move a page to another space or under another parent (WS-61 KB-2d,
 * `knowledge_base.md` §21.7), from the page menu.
 *
 * The form offers the spaces the member can write in, and the live pages of
 * the chosen space as the parent, with "Top of the space" first. It sends
 * one `POST /knowledge/pages/{id}/move` (`lib/movePlan.ts`). A page with
 * sub-pages stays in its space, as the gateway says, so the form says it
 * first. A refusal shows its words in the form.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import SelectButton, { type SelectOption } from "@/components/ui/SelectButton";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { useCachedResource } from "@/lib/useCachedResource";

import { KnowledgeError, listPages, movePage, pagesKey } from "../lib/api";
import { canWriteIn } from "../lib/labels";
import { TOP_LEVEL, hasChildren, moveRequest, parentChoices } from "../lib/movePlan";
import type { PageMoved, PageSummary, Space } from "../lib/types";

export default function MovePageDialog({
  open,
  page,
  spaces,
  onClose,
  onMoved,
}: {
  open: boolean;
  page: PageSummary;
  spaces: readonly Space[];
  onClose: () => void;
  onMoved: (moved: PageMoved, space: Space | null) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Move page" icon="FolderInput" size="sm" closeLabel="Cancel">
      {open ? <MoveForm page={page} spaces={spaces} onClose={onClose} onMoved={onMoved} /> : null}
    </Modal>
  );
}

function MoveForm({
  page,
  spaces,
  onClose,
  onMoved,
}: {
  page: PageSummary;
  spaces: readonly Space[];
  onClose: () => void;
  onMoved: (moved: PageMoved, space: Space | null) => void;
}) {
  const [spaceId, setSpaceId] = useState(page.space_id);
  const [parentId, setParentId] = useState(page.parent_id ?? TOP_LEVEL);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spaceField = useId();
  const parentField = useId();

  const here = useCachedResource<PageSummary[]>(pagesKey(page.space_id), () => listPages(page.space_id));
  const there = useCachedResource<PageSummary[]>(pagesKey(spaceId), () => listPages(spaceId));
  const keepsSpace = hasChildren(here.data ?? [], page.id);

  const spaceOptions: SelectOption[] = spaces
    .filter((s) => s.id === page.space_id || (canWriteIn(s.level) && !s.archived_at))
    .map((s) => ({
      value: s.id,
      label: s.name,
      disabled: keepsSpace && s.id !== page.space_id,
      hint: keepsSpace && s.id !== page.space_id ? "Has sub-pages" : undefined,
    }));
  const parentOptions: SelectOption[] = [
    { value: TOP_LEVEL, label: "Top of the space" },
    ...parentChoices(there.data ?? [], page.id).map((c) => ({ value: c.id, label: c.title, depth: c.depth })),
  ];
  const request = moveRequest(page, spaceId, parentId);
  const target = spaces.find((s) => s.id === spaceId) ?? null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!request || saving) return;
    setSaving(true);
    setError(null);
    try {
      const moved = await movePage(page.id, request);
      onMoved(moved, target);
    } catch (err) {
      const reason = err instanceof KnowledgeError ? err.message : String(err);
      setError(`Could not move the page. ${reason}`);
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="contents">
      <div className="space-y-3 p-4">
        <p className="text-xs text-muted-foreground">
          Move <span className="font-medium text-foreground">{page.title}</span> to a new place.
        </p>
        <div className="space-y-1">
          <label htmlFor={spaceField} className="block text-xs font-medium text-foreground">
            Space
          </label>
          <SelectButton
            id={spaceField}
            label="Space"
            value={spaceId}
            options={spaceOptions}
            onChange={(next) => {
              setSpaceId(next);
              setParentId(TOP_LEVEL);
            }}
            widthClass="w-full"
          />
          {keepsSpace ? (
            <p className="text-[11px] text-muted-foreground">
              This page has sub-pages, so it stays in its space. Move its sub-pages first to move it to another
              space.
            </p>
          ) : null}
        </div>
        <div className="space-y-1">
          <label htmlFor={parentField} className="block text-xs font-medium text-foreground">
            Parent
          </label>
          {there.loading ? (
            <SkeletonRows count={1} />
          ) : (
            <SelectButton
              id={parentField}
              label="Parent"
              value={parentId}
              options={parentOptions}
              onChange={setParentId}
              widthClass="w-full"
              filterAbove={8}
            />
          )}
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
        <Button type="submit" size="sm" icon="FolderInput" loading={saving} disabled={saving || !request}>
          Move page
        </Button>
      </footer>
    </form>
  );
}
