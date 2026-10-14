"use client";

/**
 * The field studio of a Native CRM (WS-53 CRM-C4c).
 *
 * Spec: project-docs/specs/crm_platform.md section 20.7, "CRM-C4c: the studio
 * UI". The owner asked for "a good system for creating and setting up the
 * fields like it is there on ZOHO CRM".
 *
 * `FieldsCard` draws this in the `fields` settings section when the layout
 * says `studio_admin`: the flag `CRM_FIELD_STUDIO_ORGS` allows the org, the
 * caller is an admin, and the mode is Native. In each other case the admin
 * sees the read-only card of today.
 *
 * - One tab for each record type: leads, contacts, companies and deals.
 * - The fields in no section come first, then each section in order, then the
 *   retired fields. A field or a section moves by HTML5 drag, as in the stage
 *   grid (`PipelineSettings.tsx`). A drop sends the whole order in one
 *   `PUT /crm/field-layout/{entity}`.
 * - Each write goes through `lib/fieldStudio.ts`, and then the studio calls
 *   `refresh` of `useFieldLayout`. A write re-reads what is on screen, also
 *   after a refusal (`workbench/AGENTS.md`, CRM rule 2).
 * - A retire and a section delete ask first, in a `ConfirmDialog`.
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import Tabs from "@/components/Tabs";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import ConfirmDialog from "@/components/ui/ConfirmDialog";
import { SkeletonRows } from "@/components/ui/Skeleton";
import { invalidate } from "@/lib/dataCache";
import type { FieldDef } from "../lib/fields";
import type { FieldLayout, FieldSection } from "../lib/fieldLayout";
import {
  RETIRE_BODY,
  createField,
  createSection,
  deleteSection,
  errorPart,
  layoutBody,
  moveField,
  moveSection,
  patchField,
  putFieldLayout,
  renameSection,
  restoreField,
  retireField,
  sectionDeleteBody,
  studioLayout,
  typeLabel,
  type StudioLayout,
} from "../lib/fieldStudio";
import type { EntitySlug } from "../lib/types";
import { useFieldLayout } from "../lib/useCrmMode";
import FieldStudioFieldDialog, { type FieldForm } from "./FieldStudioFieldDialog";
import FieldStudioSectionDialog, { type SectionForm } from "./FieldStudioSectionDialog";

export const STUDIO_TITLE = "Fields";
export const STUDIO_NOTE =
  "Add, rename, hide and order the fields of each record type. Drag a field or a section to move it.";
export const NO_SECTION_LABEL = "No section";
export const RETIRED_LABEL = "Retired fields";
export const EMPTY_GROUP = "Drag a field here.";

export type StudioEntity = { slug: EntitySlug; label: string };

/** What the view asks the studio to do. */
export type StudioHandlers = {
  onEntity: (entity: EntitySlug) => void;
  onNewField: (section: string | null) => void;
  onEdit: (def: FieldDef) => void;
  onRestore: (def: FieldDef) => void;
  onNewSection: () => void;
  onRenameSection: (section: FieldSection) => void;
  onDeleteSection: (section: FieldSection, count: number) => void;
  /** A drop gave a new order. The studio sends it. */
  onMove: (next: StudioLayout) => void;
  onRetry: () => void;
};

type Drag = { kind: "field"; key: string } | { kind: "section"; id: string } | null;

/** The studio, drawn from a layout that the caller holds. */
export function FieldStudioView({
  entities,
  entity,
  layout,
  loading,
  readError,
  busy,
  notice,
  handlers,
}: {
  entities: readonly StudioEntity[];
  entity: EntitySlug;
  /** null: no layout to show yet. */
  layout: StudioLayout | null;
  loading: boolean;
  readError: string | null;
  busy: boolean;
  /** The refusal of the last write, at the top of the card. */
  notice: string | null;
  handlers: StudioHandlers;
}) {
  const [drag, setDrag] = useState<Drag>(null);

  const dropField = (section: string | null, index: number) => {
    if (drag?.kind !== "field" || !layout) return;
    const next = moveField(layout, drag.key, { section, index });
    setDrag(null);
    if (next !== layout) handlers.onMove(next);
  };
  const dropSection = (index: number) => {
    if (drag?.kind !== "section" || !layout) return;
    const next = moveSection(layout, drag.id, index);
    setDrag(null);
    if (next !== layout) handlers.onMove(next);
  };

  const sectioned = (layout?.groups.length ?? 0) > 1;

  return (
    <section className="rounded-xl border border-border bg-card/40" aria-label={STUDIO_TITLE}>
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-foreground">{STUDIO_TITLE}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{STUDIO_NOTE}</p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          icon="FolderPlus"
          disabled={busy || !layout}
          onClick={handlers.onNewSection}
        >
          New section
        </Button>
        <Button
          variant="secondary"
          size="sm"
          icon="Plus"
          disabled={busy || !layout}
          onClick={() => handlers.onNewField(null)}
        >
          New field
        </Button>
      </header>

      <div className="border-b border-border px-4 py-2">
        <Tabs
          chrome="inline"
          tabs={entities.map((e) => ({ id: e.slug, label: e.label }))}
          activeTab={entity}
          onTabChange={(id) => handlers.onEntity(id as EntitySlug)}
        />
      </div>

      {notice ? (
        <p role="alert" className="border-b border-border px-4 py-2 text-xs text-destructive">
          {notice}
        </p>
      ) : null}

      {!layout ? (
        loading ? (
          <SkeletonRows count={5} className="p-4" />
        ) : (
          <div className="flex flex-wrap items-center gap-2 px-4 py-4 text-xs text-muted-foreground">
            <span>The fields could not be read{readError ? `: ${readError}` : "."}</span>
            <Button variant="secondary" size="sm" icon="RefreshCw" onClick={handlers.onRetry}>
              Try again
            </Button>
          </div>
        )
      ) : (
        <div className="divide-y divide-border/60">
          {layout.groups.map((group, groupIndex) => {
            const section = group.section;
            const sectionId = section?.id ?? null;
            const sectionIndex = groupIndex - 1;
            return (
              <div key={sectionId ?? "none"} aria-label={section?.label ?? NO_SECTION_LABEL}>
                {section || sectioned ? (
                  <div
                    onDragOver={(e) => {
                      if (drag?.kind === "field" || (drag?.kind === "section" && section)) {
                        e.preventDefault();
                      }
                    }}
                    onDrop={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (drag?.kind === "section" && section) dropSection(sectionIndex);
                      else dropField(sectionId, 0);
                    }}
                    className="flex flex-wrap items-center gap-2 bg-muted/30 px-4 py-2"
                  >
                    {section ? (
                      <span
                        draggable={!busy}
                        onDragStart={(e) => {
                          e.dataTransfer.setData("text/plain", section.id);
                          e.dataTransfer.effectAllowed = "move";
                          setDrag({ kind: "section", id: section.id });
                        }}
                        onDragEnd={() => setDrag(null)}
                        className="cursor-grab text-muted-foreground hover:text-foreground"
                        title="Drag to move the section"
                      >
                        <Icon name="GripVertical" className="h-4 w-4" />
                      </span>
                    ) : null}
                    <h3 className="min-w-0 flex-1 truncate text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      {section?.label ?? NO_SECTION_LABEL}
                    </h3>
                    <span className="text-[10px] text-muted-foreground">{group.fields.length}</span>
                    {section ? (
                      <>
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          icon="Plus"
                          aria-label={`Add a field to ${section.label}`}
                          disabled={busy}
                          onClick={() => handlers.onNewField(section.id)}
                        />
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          icon="Pencil"
                          aria-label={`Rename ${section.label}`}
                          disabled={busy}
                          onClick={() => handlers.onRenameSection(section)}
                        />
                        <Button
                          size="icon-sm"
                          variant="ghost"
                          icon="Trash2"
                          aria-label={`Delete ${section.label}`}
                          disabled={busy}
                          onClick={() => handlers.onDeleteSection(section, group.fields.length)}
                        />
                      </>
                    ) : null}
                  </div>
                ) : null}
                <ul
                  onDragOver={(e) => {
                    if (drag?.kind === "field") e.preventDefault();
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    dropField(sectionId, group.fields.length);
                  }}
                >
                  {group.fields.map((def, index) => (
                    <FieldRow
                      key={def.key}
                      def={def}
                      busy={busy}
                      onDragStart={() => setDrag({ kind: "field", key: def.key })}
                      onDragEnd={() => setDrag(null)}
                      canDrop={drag?.kind === "field"}
                      onDrop={() => dropField(sectionId, index)}
                      onEdit={() => handlers.onEdit(def)}
                    />
                  ))}
                  {group.fields.length === 0 ? (
                    <li className="px-4 py-3 text-[11px] text-muted-foreground">{EMPTY_GROUP}</li>
                  ) : null}
                </ul>
              </div>
            );
          })}
          {layout.retired.length > 0 ? (
            <div aria-label={RETIRED_LABEL}>
              <div className="bg-muted/30 px-4 py-2">
                <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {RETIRED_LABEL}
                </h3>
              </div>
              <ul>
                {layout.retired.map((def) => (
                  <li key={def.key} className="flex flex-wrap items-center gap-2 px-4 py-2">
                    <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground line-through">
                      {def.label}
                    </span>
                    <Badge size="xs">{typeLabel(def.type)}</Badge>
                    <Badge size="xs" icon="Archive">
                      Retired
                    </Badge>
                    <Button
                      variant="secondary"
                      size="sm"
                      icon="RotateCcw"
                      aria-label={`Restore ${def.label}`}
                      disabled={busy}
                      onClick={() => handlers.onRestore(def)}
                    >
                      Restore
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

/** One field of the list: its handle, label, type, flags and the Edit button. */
function FieldRow({
  def,
  busy,
  canDrop,
  onDragStart,
  onDragEnd,
  onDrop,
  onEdit,
}: {
  def: FieldDef;
  busy: boolean;
  canDrop: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
  onEdit: () => void;
}) {
  return (
    <li
      onDragOver={(e) => {
        if (canDrop) e.preventDefault();
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onDrop();
      }}
      className="flex flex-wrap items-center gap-2 px-4 py-2"
    >
      <span
        draggable={!busy}
        onDragStart={(e) => {
          e.dataTransfer.setData("text/plain", def.key);
          e.dataTransfer.effectAllowed = "move";
          onDragStart();
        }}
        onDragEnd={onDragEnd}
        className="cursor-grab text-muted-foreground hover:text-foreground"
        title="Drag to move the field"
      >
        <Icon name="GripVertical" className="h-4 w-4" />
      </span>
      <span
        className={`min-w-0 flex-1 truncate text-xs font-medium ${
          def.visible ? "text-foreground" : "text-muted-foreground"
        }`}
      >
        {def.label}
      </span>
      <Badge size="xs">{typeLabel(def.type)}</Badge>
      {def.is_system ? (
        <Badge size="xs" icon="Lock" title="A fixed part of each record">
          System
        </Badge>
      ) : null}
      {def.required ? (
        <Badge size="xs" tone="primary">
          Required
        </Badge>
      ) : null}
      {!def.visible ? (
        <Badge size="xs" tone="warning" icon="EyeOff">
          Hidden
        </Badge>
      ) : null}
      <Button
        size="icon-sm"
        variant="ghost"
        icon="Pencil"
        aria-label={`Edit ${def.label}`}
        disabled={busy}
        onClick={onEdit}
      />
    </li>
  );
}

type Confirm =
  | { kind: "retire"; def: FieldDef }
  | { kind: "delete-section"; section: FieldSection; count: number }
  | null;

/** The studio with its read and its writes. */
export default function FieldStudio({ entities }: { entities: readonly StudioEntity[] }) {
  const [entity, setEntity] = useState<EntitySlug>(entities[0]?.slug ?? "leads");
  const { layout, error, loading, refresh } = useFieldLayout(entity);

  // The server's answer is the truth. The local order exists only between a
  // drop and the re-read that it starts, as in the stage grid. Set during
  // render, so no frame shows the old order after a new read.
  const [served, setServed] = useState<FieldLayout | null>(layout);
  const [shown, setShown] = useState<StudioLayout | null>(layout ? studioLayout(layout) : null);
  if (served !== layout) {
    setServed(layout);
    setShown(layout ? studioLayout(layout) : null);
  }

  const [fieldForm, setFieldForm] = useState<FieldForm | null>(null);
  const [sectionForm, setSectionForm] = useState<SectionForm | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  /** After each write: the layout, and the field definitions that the records read. */
  const reread = () => {
    refresh();
    invalidate(`crm:fields:${entity}`);
  };

  /** A write from the list. A refusal shows at the top of the card. */
  async function run(act: () => Promise<unknown>): Promise<boolean> {
    setBusy(true);
    setNotice(null);
    try {
      await act();
      return true;
    } catch (err) {
      setNotice(errorPart(err).message);
      return false;
    } finally {
      setBusy(false);
      reread();
    }
  }

  /** A write from a dialog. A refusal goes back to the dialog, beside its control. */
  async function write(act: () => Promise<unknown>): Promise<void> {
    setNotice(null);
    try {
      await act();
    } finally {
      reread();
    }
  }

  const handlers: StudioHandlers = {
    onEntity: (next) => {
      setNotice(null);
      setEntity(next);
    },
    onNewField: (section) => setFieldForm({ mode: "create", section }),
    onEdit: (def) => setFieldForm({ mode: "edit", def }),
    onRestore: (def) => void run(() => restoreField(entity, def.key)),
    onNewSection: () => setSectionForm({ mode: "create" }),
    onRenameSection: (section) => setSectionForm({ mode: "rename", section }),
    onDeleteSection: (section, count) => setConfirm({ kind: "delete-section", section, count }),
    onMove: (next) => {
      setShown(next);
      void run(() => putFieldLayout(entity, layoutBody(next))).then((ok) => {
        if (!ok) setShown(served ? studioLayout(served) : null);
      });
    },
    onRetry: refresh,
  };

  return (
    <>
      <FieldStudioView
        entities={entities}
        entity={entity}
        layout={shown}
        loading={loading}
        readError={error}
        busy={busy}
        notice={notice}
        handlers={handlers}
      />
      <FieldStudioFieldDialog
        form={fieldForm}
        sections={layout?.sections ?? []}
        onClose={() => setFieldForm(null)}
        onSave={(form, body) =>
          write(() =>
            form.mode === "create"
              ? createField(entity, body)
              : patchField(entity, form.def.key, body),
          )
        }
        onRetire={(def) => setConfirm({ kind: "retire", def })}
      />
      <FieldStudioSectionDialog
        form={sectionForm}
        onClose={() => setSectionForm(null)}
        onSave={(form, label) =>
          write(() =>
            form.mode === "create"
              ? createSection(entity, label)
              : renameSection(entity, form.section.id, label),
          )
        }
      />
      <ConfirmDialog
        open={confirm?.kind === "retire"}
        title="Retire this field?"
        subject={confirm?.kind === "retire" ? confirm.def.label : null}
        body={RETIRE_BODY}
        confirmLabel="Retire"
        icon="Archive"
        busy={busy}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm?.kind !== "retire") return;
          const def = confirm.def;
          setConfirm(null);
          setFieldForm(null);
          void run(() => retireField(entity, def.key));
        }}
      />
      <ConfirmDialog
        open={confirm?.kind === "delete-section"}
        title="Delete this section?"
        subject={confirm?.kind === "delete-section" ? confirm.section.label : null}
        body={sectionDeleteBody(confirm?.kind === "delete-section" ? confirm.count : 0)}
        confirmLabel="Delete section"
        busy={busy}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm?.kind !== "delete-section") return;
          const section = confirm.section;
          setConfirm(null);
          void run(() => deleteSection(entity, section.id));
        }}
      />
    </>
  );
}
