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
import ConfirmDialog from "@/components/ui/ConfirmDialog";

/**
 * An org-wide row belongs to the organization, not to this space.
 *
 * `project_id: null` is the wire's marker for it (WS-27bj / D-PM-16).
 *
 * **What may be done to one changed on 2026-09-20 (D-PM-33).** A RENAME and a
 * recolour are allowed, for somebody holding `admin:settings:manage`, and the
 * rename is confirmed against a count first — see `orgRenameCopy`. A MERGE
 * and a DELETE are still refused by the gateway, so those two stay drawn
 * disabled with the reason rather than letting the member press a 409.
 *
 * ⚠️ Disabled, NOT hidden. The row still belongs on this list — it is a
 * tag this project's tasks really can wear, and hiding it would make the
 * screen disagree with the picker that offers it.
 */
const orgWide = (row: { project_id?: string | null }): boolean =>
  row.project_id === null;

const ORG_WIDE_NOTE =
  "Shared by the whole organization — it can be renamed, but not merged " +
  "away or deleted from inside one project.";

import { useEffect, useState } from "react";

import { projectsApi } from "../lib/api";
import { TAG_COLORS, type TagRow, byUsage, chipClass, normaliseTag } from "../lib/tags";
import { orgRenameCopy, tagDeleteCopy } from "../lib/tagCopy";

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
  // The act waiting for the shared ConfirmDialog. A DELETE strips the tag
  // from every task and cannot be undone. An org-wide RENAME rewrites tasks
  // in projects the member may not see, so the count is asked first
  // (D-PM-33). Both asked through `window.confirm`, or not at all, until
  // 2026-09-24. The words are `lib/tagCopy.ts`'s.
  const [confirming, setConfirming] = useState<
    | { kind: "delete"; tag: TagRow }
    | {
        kind: "rename";
        tag: TagRow;
        to: string;
        impact: { tag: string; tasks: number; projects: number } | null;
      }
    | null
  >(null);

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
                      if (orgWide(t)) {
                        // D-PM-33: the count comes before the write, never
                        // after. A preview that failed must not become a
                        // silent yes, so the dialog asks with the size unknown.
                        void projectsApi
                          .tagImpact(t.id)
                          .catch(() => null)
                          .then((impact) =>
                            setConfirming({ kind: "rename", tag: t, to: next, impact }),
                          );
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
                    {/* ⚠️ The gateway sends `project_id: null` for an org-wide
                        row and says in `tags._row` that it does so because
                        "a client reads it to know whether the row is
                        editable here". Neither manager read it. So the day
                        the first org-wide tag exists, this screen would list
                        it beside the local ones with a full set of Rename,
                        Merge and Delete buttons, every one of which answers
                        409 from `refuse_org_wide_write`.

                        Nothing is broken today only because
                        `PROJECTS_ORG_VOCABULARIES` is off and no such row
                        exists. That makes this latent rather than harmless:
                        the flag is one env write away, and the failure it
                        uncovers is three dead buttons. */}
                    {orgWide(t) ? (
                      <Badge tone="primary" title="Shared by every project in this organization">
                        Organization
                      </Badge>
                    ) : null}
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
                      title={
                        orgWide(t)
                          ? "Renames it for the whole organization — you are shown how many tasks first"
                          : undefined
                      }
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
                      title={orgWide(t) ? ORG_WIDE_NOTE : "Fold this tag into another"}
                      disabled={orgWide(t) || tags.length < 2}
                      onClick={() => setMergeSource(t)}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon="Trash2"
                      aria-label={`Delete ${t.name}`}
                      disabled={orgWide(t)}
                      title={
                        orgWide(t)
                          ? ORG_WIDE_NOTE
                          : "Deletes it and takes it off every task"
                      }
                      onClick={() => setConfirming({ kind: "delete", tag: t })}
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

      {/* Inside the Modal on purpose: Base UI nests a dialog opened from
          within another, so focus returns to the tag manager on close. */}
      <ConfirmDialog
        open={Boolean(confirming)}
        {...(confirming?.kind === "rename"
          ? orgRenameCopy(confirming.to, confirming.impact)
          : tagDeleteCopy(confirming?.tag ?? { name: "" }))}
        icon={confirming?.kind === "rename" ? "Pencil" : "Trash2"}
        onCancel={() => {
          if (confirming?.kind === "rename") setEditing(null);
          setConfirming(null);
        }}
        onConfirm={() => {
          const act = confirming;
          setConfirming(null);
          if (!act) return;
          if (act.kind === "delete") {
            void run(async () => {
              const done = await projectsApi.deleteTag(act.tag.id);
              onTasksTouched();
              const n = done.cascaded.tasks_untagged;
              return `Deleted “${done.name}”.${
                n ? ` Removed from ${n} task${n === 1 ? "" : "s"}.` : ""
              }`;
            });
            return;
          }
          void run(async () => {
            const done = await projectsApi.patchTag(act.tag.id, { name: act.to });
            setEditing(null);
            onTasksTouched();
            return `Renamed to “${done.name}”.${touched(done.retagged ?? 0)}`;
          });
        }}
      />
    </Modal>
  );
}
