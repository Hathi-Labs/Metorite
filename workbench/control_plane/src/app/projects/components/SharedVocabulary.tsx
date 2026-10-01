"use client";

/**
 * Shared vocabulary — the organization's own tags, fields and types (WS-42
 * PS-3 and PS-3b).
 *
 * Spec: `project-docs/specs/projects_settings.md` §7 rows PS-3 and PS-3b.
 *
 * An org-wide row appears in every space. This is the one place to see the
 * organization's rows and change them:
 *
 * - **Rename** all three, and **recolour** a tag (D-PM-33).
 * - **Delete** any of them, and **merge** a tag into another shared tag
 *   (H-205, owner 2026-10-01). Each asks first with the count of tasks and
 *   spaces it reaches, read from `GET /vocabulary/{kind}/{id}/impact`.
 * - **Add** a shared entry, only when `PROJECTS_ORG_VOCABULARIES` is on
 *   (`can_create`, H-5). The create goes through the ONE create route of each
 *   kind with `scope: "org"`, anchored on a space the admin can see.
 *
 * A tag rename rewrites tasks in spaces the member may not open, so it asks
 * first too (`orgRenameCopy`). A field or type rename moves a label only.
 */

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";

import { type OrgVocabulary, projectsApi } from "../lib/api";
import { FIELD_TYPES, FIELD_TYPE_LABELS, type FieldType, needsOptions } from "../lib/customFields";
import type { DeleteCopy } from "../lib/deleteCopy";
import {
  type Impact,
  VOCABULARY_GROUPS,
  type VocabularyKind,
  renamedNotice,
  sharedDeleteCopy,
  sharedMergeCopy,
  usageLine,
  vocabularyEmpty,
  vocabularyNotes,
} from "../lib/sharedVocabulary";
import { orgRenameCopy } from "../lib/tagCopy";
import { TAG_COLORS, chipClass, normaliseTag } from "../lib/tags";

interface Props {
  /** A change moved what every board shows: the board re-reads it. */
  onChanged: () => void;
  /** A space the admin can see, which the create routes anchor on (H-5). */
  anchorSpaceId?: string | null;
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

/** The act waiting for the shared ConfirmDialog, with the words it shows. */
type Asking =
  | { act: "rename"; kind: VocabularyKind; entry: Entry; to: string; copy: DeleteCopy }
  | { act: "delete"; kind: VocabularyKind; entry: Entry; copy: DeleteCopy }
  | { act: "merge"; entry: Entry; into: Entry; copy: DeleteCopy };

const NOUN: Record<VocabularyKind, string> = { tags: "tag", fields: "field", types: "task type" };

export default function SharedVocabulary({ onChanged, anchorSpaceId = null }: Props) {
  const [vocab, setVocab] = useState<OrgVocabulary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [asking, setAsking] = useState<Asking | null>(null);

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

  /** The count first, then the question. A failed count asks anyway, with
   * the size unknown, never as zero. */
  const impactOf = (kind: VocabularyKind, id: string): Promise<Impact> =>
    projectsApi.vocabularyImpact(kind, id).catch(() => null);

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

  const remove = (kind: VocabularyKind, entry: Entry) =>
    run(async () => {
      if (kind === "tags") {
        const n = (await projectsApi.deleteTag(entry.id)).cascaded.tasks_untagged;
        return `Deleted the shared tag “${entry.name}”. It came off ${n} task${n === 1 ? "" : "s"}.`;
      }
      if (kind === "fields") {
        const n = (await projectsApi.deleteField(entry.id)).cascaded.values_cleared;
        return `Deleted the shared field “${entry.name}”. ${n} task${n === 1 ? "" : "s"} lost its value.`;
      }
      const n = (await projectsApi.deleteType(entry.id)).tasks_untyped;
      return `Deleted the shared task type “${entry.name}”. ${n} task${n === 1 ? " has" : "s have"} no type now.`;
    });

  if (!vocab) {
    return error ? (
      <p className="rounded-lg border border-border bg-muted px-3 py-2 text-xs text-foreground">{error}</p>
    ) : (
      <p className="text-xs text-muted-foreground">Loading…</p>
    );
  }

  const canAdd = vocab.can_create && Boolean(anchorSpaceId);

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

      {vocabularyEmpty(vocab) && !canAdd ? (
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
                              // D-PM-33: the count comes before the write.
                              void projectsApi
                                .tagImpact(entry.id)
                                .catch(() => null)
                                .then((impact) =>
                                  setAsking({
                                    act: "rename",
                                    kind: "tags",
                                    entry,
                                    to,
                                    copy: orgRenameCopy(to, impact),
                                  }),
                                );
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
                          {group.kind === "tags" && vocab.can_edit && rows.length > 1 ? (
                            <SelectButton
                              label={`Merge ${entry.name} into`}
                              widthClass="w-[8rem]"
                              value=""
                              onChange={(intoId) => {
                                const into = rows.find((r) => r.id === intoId);
                                if (!into) return;
                                void impactOf("tags", entry.id).then((impact) =>
                                  setAsking({
                                    act: "merge",
                                    entry,
                                    into,
                                    copy: sharedMergeCopy(entry.name, into.name, impact),
                                  }),
                                );
                              }}
                              options={[
                                { value: "", label: "Merge into…" },
                                ...rows.filter((r) => r.id !== entry.id).map((r) => ({ value: r.id, label: r.name })),
                              ]}
                            />
                          ) : null}
                          {vocab.can_edit ? (
                            <>
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
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                icon="Trash2"
                                aria-label={`Delete ${entry.name}`}
                                title="Deletes it in every space, after showing what it reaches"
                                onClick={() =>
                                  void impactOf(group.kind, entry.id).then((impact) =>
                                    setAsking({
                                      act: "delete",
                                      kind: group.kind,
                                      entry,
                                      copy: sharedDeleteCopy(group.kind, entry.name, impact),
                                    }),
                                  )
                                }
                              />
                            </>
                          ) : null}
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {canAdd ? (
                <AddShared
                  kind={group.kind}
                  onAdd={(name, fieldType, options) =>
                    run(async () => {
                      const anchor = anchorSpaceId as string;
                      if (group.kind === "tags") await projectsApi.createTag(anchor, { name, scope: "org" });
                      else if (group.kind === "types")
                        await projectsApi.createType(anchor, { name, scope: "org", icon: "Circle", color: "gray" });
                      else
                        await projectsApi.createField(anchor, { name, field_type: fieldType, options, scope: "org" });
                      return `Added the shared ${NOUN[group.kind]} “${name}”. Every space has it now.`;
                    })
                  }
                />
              ) : null}
            </section>
          );
        })
      )}

      <ConfirmDialog
        open={Boolean(asking)}
        {...(asking?.copy ?? sharedDeleteCopy("tags", "", null))}
        confirmVariant={asking?.act === "rename" ? "primary" : "destructive"}
        defaultFocus="cancel"
        icon={asking?.act === "rename" ? "Pencil" : asking?.act === "merge" ? "Merge" : "Trash2"}
        onCancel={() => {
          setAsking(null);
          setEditing(null);
        }}
        onConfirm={() => {
          const act = asking;
          setAsking(null);
          if (!act) return;
          if (act.act === "rename") void rename(act.kind, act.entry, act.to);
          else if (act.act === "delete") void remove(act.kind, act.entry);
          else
            void run(async () => {
              const done = await projectsApi.mergeTag(act.entry.id, act.into.id);
              return done.retagged
                ? `Merged “${done.merged}” into “${done.into}” on ${done.retagged} task${done.retagged === 1 ? "" : "s"}.`
                : `Merged “${done.merged}” into “${done.into}”. No task wore it.`;
            });
        }}
      />
    </div>
  );
}

/** The add form of one group. A field also takes its type, and its options
 * when the type has them. */
function AddShared({
  kind,
  onAdd,
}: {
  kind: VocabularyKind;
  onAdd: (name: string, fieldType: FieldType, options: string[]) => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [fieldType, setFieldType] = useState<FieldType>("text");
  const [options, setOptions] = useState("");
  const noun = NOUN[kind];
  return (
    <form
      className="flex flex-wrap items-center gap-2 border-t border-border p-3"
      onSubmit={(e) => {
        e.preventDefault();
        const clean = kind === "tags" ? normaliseTag(name) : name.trim();
        if (!clean) return;
        const list = needsOptions(fieldType)
          ? options
              .split(",")
              .map((o) => o.trim())
              .filter(Boolean)
          : [];
        void onAdd(clean, fieldType, list).then(() => {
          setName("");
          setOptions("");
        });
      }}
    >
      <Input
        inputSize="sm"
        value={name}
        aria-label={`New shared ${noun}`}
        placeholder={`New shared ${noun}`}
        onChange={(e) => setName(e.target.value)}
        className="min-w-[10rem] flex-1"
      />
      {kind === "fields" ? (
        <SelectButton
          label="Type of the new shared field"
          widthClass="w-[8rem]"
          value={fieldType}
          onChange={(next) => setFieldType(next as FieldType)}
          options={FIELD_TYPES.map((t) => ({ value: t, label: FIELD_TYPE_LABELS[t] }))}
        />
      ) : null}
      {kind === "fields" && needsOptions(fieldType) ? (
        <Input
          inputSize="sm"
          value={options}
          aria-label="Options, separated by commas"
          placeholder="Options, separated by commas"
          onChange={(e) => setOptions(e.target.value)}
          className="min-w-[10rem] flex-1"
        />
      ) : null}
      <Button type="submit" size="sm" disabled={!name.trim()}>
        Add
      </Button>
    </form>
  );
}
