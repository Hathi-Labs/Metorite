"use client";

/**
 * Projects · the tag registry screen (WS-27m).
 *
 * The reason the registry exists, in one dialog: **rename** and **merge**.
 * Free tagging without them is how a tag set rots into forty near-duplicates
 * nobody can fix, which is precisely what `paca_pm_research_2026-08.md` row 13
 * refused about Paca's bare array.
 *
 * Ordered by usage, because "which of these two do I merge into the other" is
 * the question this screen is opened to answer, and the counts are the answer.
 */

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import Modal from "@/components/ui/Modal";
import { useEffect, useState } from "react";

import { projectsApi } from "../lib/api";
import { TAG_COLORS, type TagRow, byUsage, chipClass, normaliseTag } from "../lib/tags";

interface Props {
  projectId: string;
  projectName: string;
  onClose: () => void;
  onChanged: (tags: TagRow[]) => void;
  /** Fired when a rename, merge or delete rewrote tasks, so the board reloads. */
  onTasksTouched: () => void;
}

export function TagManager({
  projectId,
  projectName,
  onClose,
  onChanged,
  onTasksTouched,
}: Props) {
  const [tags, setTags] = useState<TagRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [mergeSource, setMergeSource] = useState<TagRow | null>(null);

  const load = async () => {
    try {
      const res = await projectsApi.tags(projectId);
      setTags(res.rows);
      onChanged(res.rows);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const run = async (work: () => Promise<string | null>) => {
    setError(null);
    setNotice(null);
    try {
      const said = await work();
      if (said) setNotice(said);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  };

  const touched = (n: number) =>
    n ? ` ${n} task${n === 1 ? "" : "s"} updated.` : "";

  return (
    // WS-27ak — same as FieldManager: no Escape, no outside press and no focus
    // trap before the primitive.
    <Modal
      open
      onClose={onClose}
      title="Tags"
      description={`Shared by ${projectName} and everything under it`}
      size="lg"
    >
      {error ? (
        <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
          {notice}
        </p>
      ) : null}

      {mergeSource ? (
        <div className="border-b border-border px-3 py-2">
          <p className="text-xs text-foreground">
            Merge <strong>{mergeSource.name}</strong> into which tag? It will be
            deleted, and every task wearing it gets the other one.
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {byUsage(tags)
              .filter((t) => t.id !== mergeSource.id)
              .map((t) => (
                <Button
                  key={t.id}
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    void run(async () => {
                      const done = await projectsApi.mergeTag(mergeSource.id, t.id);
                      setMergeSource(null);
                      onTasksTouched();
                      return `Merged “${done.merged}” into “${done.into}”.${touched(done.retagged)}`;
                    })
                  }
                >
                  {t.name}
                </Button>
              ))}
            <Button variant="ghost" size="sm" onClick={() => setMergeSource(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : tags.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No tags yet. They appear here the moment somebody puts one on a
            task — you do not have to create them first.
          </p>
        ) : (
          <ul className="space-y-1">
            {byUsage(tags).map((t) => (
              <li
                key={t.id}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5"
              >
                {editing === t.id ? (
                  <form
                    className="flex min-w-0 flex-1 items-center gap-1"
                    onSubmit={(e) => {
                      e.preventDefault();
                      const next = normaliseTag(draft);
                      if (!next || next === t.name) {
                        setEditing(null);
                        return;
                      }
                      void run(async () => {
                        const done = await projectsApi.patchTag(t.id, { name: next });
                        setEditing(null);
                        onTasksTouched();
                        return `Renamed to “${done.name}”.${touched(done.retagged ?? 0)}`;
                      });
                    }}
                  >
                    <Input
                      autoFocus
                      inputSize="sm"
                      value={draft}
                      aria-label={`Rename ${t.name}`}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key !== "Escape") return;
                        // WS-27ak — the first Escape leaves the FIELD, it does
                        // not close the dialog. `stopPropagation` is what makes
                        // that true: the substrate binds Escape on `document`,
                        // which sits above React's root container, so an
                        // unstopped key cancels the rename and dismisses the
                        // dialog on one press. Same rule the task panel already
                        // holds (`page.tsx:1004-1010`).
                        e.stopPropagation();
                        setEditing(null);
                      }}
                    />
                    <Button type="submit" size="sm">
                      Save
                    </Button>
                  </form>
                ) : (
                  <>
                    <span
                      className={`min-w-0 truncate rounded-md px-1.5 py-0.5 text-[11px] ${chipClass(t.color)}`}
                    >
                      {t.name}
                    </span>
                    <span className="flex-1" />
                    {/* The number this screen is opened for: which of two
                        near-duplicates should absorb the other. */}
                    <Badge>{t.task_count ?? 0}</Badge>
                    <SelectButton
                      label={`Colour for ${t.name}`}
                      widthClass="w-[7rem]"
                      value={TAG_COLORS.includes(t.color as never) ? t.color : "gray"}
                      onChange={(next) =>
                        void run(async () => {
                          await projectsApi.patchTag(t.id, { color: next });
                          return null;
                        })
                      }
                      options={TAG_COLORS.map((c) => ({ value: c, label: c }))}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Pencil"
                      aria-label={`Rename ${t.name}`}
                      onClick={() => {
                        setEditing(t.id);
                        setDraft(t.name);
                      }}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Merge"
                      aria-label={`Merge ${t.name}`}
                      title="Fold this tag into another"
                      disabled={tags.length < 2}
                      onClick={() => setMergeSource(t)}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Trash2"
                      aria-label={`Delete ${t.name}`}
                      title="Deletes it and takes it off every task"
                      onClick={() =>
                        void run(async () => {
                          const done = await projectsApi.deleteTag(t.id);
                          onTasksTouched();
                          const n = done.cascaded.tasks_untagged;
                          return `Deleted “${done.name}”.${
                            n ? ` Removed from ${n} task${n === 1 ? "" : "s"}.` : ""
                          }`;
                        })
                      }
                    />
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <form
        className="flex items-center gap-2 border-t border-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          const next = normaliseTag(name);
          if (!next) return;
          void run(async () => {
            await projectsApi.createTag(projectId, { name: next });
            setName("");
            return null;
          });
        }}
      >
        <Input
          inputSize="sm"
          value={name}
          aria-label="New tag"
          placeholder="New tag"
          onChange={(e) => setName(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={!normaliseTag(name)}>
          Add
        </Button>
      </form>
    </Modal>
  );
}
