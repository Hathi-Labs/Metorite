"use client";

/**
 * Shared vocabulary — the organization's own tags, fields and types (WS-42 PS-3).
 *
 * Spec: `project-docs/specs/projects_settings.md` §7 row PS-3.
 *
 * An org-wide row appears in every space. Until this section, it showed only
 * as one row of a space's list, locked, with a note that pointed nowhere. This
 * is where it points: one list of what the organization shares, with the acts
 * D-PM-33 allows, which are rename (all three) and colour (tags). Merge and
 * delete stay refused on the server, so they are not offered here.
 *
 * A tag rename rewrites tasks in spaces the member may not open, so it asks
 * first with the count (`orgRenameCopy`), the same dialog the space's tag list
 * uses. A field or type rename moves a label and no task data, so it applies.
 */

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";

import { type OrgVocabulary, projectsApi } from "../lib/api";
import { FIELD_TYPE_LABELS } from "../lib/customFields";
import {
  VOCABULARY_GROUPS,
  type VocabularyKind,
  renamedNotice,
  usageLine,
  vocabularyEmpty,
  vocabularyNotes,
} from "../lib/sharedVocabulary";
import { orgRenameCopy } from "../lib/tagCopy";
import { TAG_COLORS, chipClass, normaliseTag } from "../lib/tags";

interface Props {
  /** A rename changed what every board shows: the board re-reads it. */
  onChanged: () => void;
}

/** One row of any kind, in the shape this section draws. */
interface Entry {
  id: string;
  name: string;
  color?: string | null;
  icon?: string | null;
  detail?: string | null;
  task_count?: number;
}

function entriesOf(v: OrgVocabulary, kind: VocabularyKind): Entry[] {
  if (kind === "tags") return v.tags;
  if (kind === "fields") return v.fields.map((f) => ({ ...f, detail: FIELD_TYPE_LABELS[f.field_type] ?? f.field_type }));
  return v.types;
}

type Renaming = { kind: VocabularyKind; entry: Entry; to: string; impact: { tag: string; tasks: number; projects: number } | null };

export default function SharedVocabulary({ onChanged }: Props) {
  const [vocab, setVocab] = useState<OrgVocabulary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState<Renaming | null>(null);

  useEffect(() => {
    let live = true;
    projectsApi
      .vocabulary()
      .then((v) => live && setVocab(v))
      .catch((err: Error) => live && setError(String(err.message)));
    return () => {
      live = false;
    };
  }, []);

  const run = async (work: () => Promise<string>) => {
    setError(null);
    setNotice(null);
    try {
      setNotice(await work());
      setEditing(null);
      setVocab(await projectsApi.vocabulary());
      onChanged();
    } catch (err) {
      setError(String((err as Error).message));
    }
  };

  const rename = (kind: VocabularyKind, entry: Entry, to: string) => {
    if (kind === "tags") {
      return run(async () => {
        const done = await projectsApi.patchTag(entry.id, { name: to });
        return renamedNotice("tag", done.name, done.retagged);
      });
    }
    if (kind === "fields") {
      return run(async () => renamedNotice("field", (await projectsApi.patchField(entry.id, { name: to })).name));
    }
    return run(async () => renamedNotice("task type", (await projectsApi.patchType(entry.id, { name: to })).name));
  };

  if (!vocab) {
    return error ? (
      <p className="rounded-lg border border-border bg-muted px-3 py-2 text-xs text-foreground">{error}</p>
    ) : (
      <p className="text-xs text-muted-foreground">Loading…</p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-1 text-xs text-muted-foreground">
        {vocabularyNotes(vocab).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {error ? (
        <p role="alert" className="rounded-lg border border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}
      {notice ? <p className="text-xs text-muted-foreground">{notice}</p> : null}

      {vocabularyEmpty(vocab) ? (
        <div className="rounded-lg border border-border bg-card p-4">
          <p className="text-sm font-medium">Nothing is shared yet</p>
          <p className="text-xs text-muted-foreground">
            Your organization has no shared tags, fields or task types. Each space keeps its own, under that
            space&apos;s settings.
          </p>
        </div>
      ) : (
        VOCABULARY_GROUPS.map((group) => {
          const rows = entriesOf(vocab, group.kind);
          return (
            <section
              key={group.kind}
              aria-label={`Shared ${group.label.toLowerCase()}`}
              className="overflow-hidden rounded-lg border border-border bg-card"
            >
              <h3 className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs font-semibold">
                <Icon name={group.icon} className="h-3.5 w-3.5 text-muted-foreground" />
                {group.label}
                <span className="font-normal text-muted-foreground">{rows.length}</span>
              </h3>
              {rows.length === 0 ? (
                <p className="px-3 py-2 text-xs text-muted-foreground">No shared {group.noun}s.</p>
              ) : (
                <ul className="divide-y divide-border">
                  {rows.map((entry) => (
                    <li key={entry.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                      {editing === entry.id ? (
                        <form
                          className="flex min-w-0 flex-1 items-center gap-1"
                          onSubmit={(e) => {
                            e.preventDefault();
                            const to = group.kind === "tags" ? normaliseTag(draft) : draft.trim() || null;
                            if (!to || to === entry.name) {
                              setEditing(null);
                              return;
                            }
                            if (group.kind === "tags") {
                              // D-PM-33: the count comes before the write. A
                              // preview that failed asks with the size unknown.
                              void projectsApi
                                .tagImpact(entry.id)
                                .catch(() => null)
                                .then((impact) => setConfirming({ kind: "tags", entry, to, impact }));
                              return;
                            }
                            void rename(group.kind, entry, to);
                          }}
                        >
                          <Input
                            autoFocus
                            inputSize="sm"
                            value={draft}
                            aria-label={`Rename ${entry.name}`}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key !== "Escape") return;
                              // The first Escape leaves the field, not the page.
                              e.stopPropagation();
                              setEditing(null);
                            }}
                          />
                          <Button type="submit" size="sm">
                            Save
                          </Button>
                          <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(null)}>
                            Cancel
                          </Button>
                        </form>
                      ) : (
                        <>
                          <span
                            className={`flex min-w-0 items-center gap-1 truncate rounded-md px-1.5 py-0.5 text-[11px] ${
                              group.kind === "fields" ? "bg-muted text-foreground" : chipClass(entry.color ?? undefined)
                            }`}
                          >
                            {group.kind === "types" ? (
                              <Icon name={entry.icon || "Circle"} className="h-3 w-3 shrink-0" />
                            ) : null}
                            {entry.name}
                          </span>
                          {entry.detail ? <Badge>{entry.detail}</Badge> : null}
                          {usageLine(entry.task_count) ? (
                            <span className="text-[11px] text-muted-foreground">{usageLine(entry.task_count)}</span>
                          ) : null}
                          <span className="flex-1" />
                          {group.kind === "tags" && vocab.can_edit ? (
                            <SelectButton
                              label={`Colour for ${entry.name}`}
                              widthClass="w-[7rem]"
                              value={TAG_COLORS.includes(entry.color as never) ? (entry.color as string) : "gray"}
                              onChange={(next) =>
                                void run(async () => {
                                  await projectsApi.patchTag(entry.id, { color: next });
                                  return `Recoloured the shared tag “${entry.name}”.`;
                                })
                              }
                              options={TAG_COLORS.map((c) => ({ value: c, label: c }))}
                            />
                          ) : null}
                          {vocab.can_edit ? (
                            <Button
                              variant="ghost"
                              size="icon-sm"
                              icon="Pencil"
                              aria-label={`Rename ${entry.name}`}
                              title="Renames it in every space"
                              onClick={() => {
                                setEditing(entry.id);
                                setDraft(entry.name);
                              }}
                            />
                          ) : null}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })
      )}

      <ConfirmDialog
        open={Boolean(confirming)}
        {...orgRenameCopy(confirming?.to ?? "", confirming?.impact ?? null)}
        icon="Pencil"
        onCancel={() => {
          setConfirming(null);
          setEditing(null);
        }}
        onConfirm={() => {
          const act = confirming;
          setConfirming(null);
          if (act) void rename(act.kind, act.entry, act.to);
        }}
      />
    </div>
  );
}
