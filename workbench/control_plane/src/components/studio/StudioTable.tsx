"use client";

/**
 * The Table block with inline edit (`ai_studio.md` §14 D2b-1, slice STU-D2b).
 *
 * A Table whose node says `data.editable` draws here. It is the one `dataGrid`
 * template, with a `GridEdit` supplied (`genUITemplates.tsx`). A click on a
 * cell of a scalar field opens its editor. A text-like cell commits on Enter
 * or on blur, and Escape cancels. A tick commits at once. A select opens its
 * list and commits on a pick. The store is `lib/studio/inlineEdit.ts`: the
 * cell shows the new value, one update follows, and a refusal puts the old
 * value back and says why in the line above the grid.
 *
 * ⚠️ This file never calls `door.run`. Only the edit store writes, and the
 * Table reads only through `door.read` in `GenerativeUINode`.
 */
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";

import { DataGrid, type GridEdit } from "@/components/genUITemplates";
import Checkbox from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { labelOf, parseFields, valueToDraft } from "@/lib/studio/blocks";
import type { Row, StudioDataDoor, StudioSource } from "@/lib/studio/dataDoor";
import { gridData } from "@/lib/studio/dataDoor";
import type { FieldSpec } from "@/lib/studio/fieldCheck";
import { cellId, computedKeys, createEditor, type EditState } from "@/lib/studio/inlineEdit";

const NO_UNSUBSCRIBE = () => {};
const IDLE: EditState = { editing: null, shown: {}, pending: new Set(), alert: null, live: "" };
const NO_VALUE = "";

/** The update verb that pairs with a list source, or null. */
export function updateOf(source: StudioSource | null): string | null {
  const m = source ? /^(object:[a-z][a-z0-9_]*)\.list$/.exec(source.action) : null;
  return m ? `${m[1]}.update` : null;
}

const INPUT_TYPE: Partial<Record<FieldSpec["type"], string>> = { date: "date", email: "email", url: "url", phone: "tel" };

function TextEditor({ field, current, onCommit, onCancel }: {
  field: FieldSpec; current: unknown; onCommit: (draft: string) => void; onCancel: () => void;
}) {
  const start = String(valueToDraft(field, current));
  const cancelled = useRef(false);
  return (
    <Input
      autoFocus
      inputSize="sm"
      type={INPUT_TYPE[field.type] ?? "text"}
      inputMode={field.type === "number" ? "decimal" : undefined}
      aria-label={`Edit ${labelOf(field)}`}
      defaultValue={start}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit((e.target as HTMLInputElement).value);
        } else if (e.key === "Escape") {
          cancelled.current = true;
          onCancel();
        }
      }}
      onBlur={(e) => {
        if (cancelled.current) return;
        // Only a real change commits. Tabbing through a cell writes nothing.
        if (e.target.value !== start) onCommit(e.target.value);
        else onCancel();
      }}
    />
  );
}

export interface StudioTableProps {
  /** The `data` of the node: `fields`, `title`, `filterable`, `editable`, `computed`. */
  data: Record<string, unknown>;
  rows: Row[];
  door: StudioDataDoor;
  source: StudioSource;
}

export default function StudioTable({ data, rows, door, source }: StudioTableProps) {
  const fieldsKey = JSON.stringify(data.fields ?? null);
  const computedList = Array.isArray(data.computed) ? data.computed.filter((x): x is string => typeof x === "string") : [];
  const computedKey = computedList.join(",");
  const fields = useMemo(() => parseFields(data.fields), [fieldsKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const computed = useMemo(() => computedKeys(computedList.map((target) => ({ kind: "formula", target }))), [computedKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const update = data.editable === true ? updateOf(source) : null;
  const editor = useMemo(
    () => (update ? createEditor(door, update, fields, computed) : null),
    [door, update, fields, computed],
  );
  const subscribe = useCallback((fn: () => void) => (editor ? editor.subscribe(fn) : NO_UNSUBSCRIBE), [editor]);
  const st = useSyncExternalStore(subscribe, () => editor?.getState() ?? IDLE, () => editor?.getState() ?? IDLE);
  useEffect(() => editor?.settle(), [editor, rows]);

  // The values that the member set and the door has not confirmed sit over the stored rows.
  const shownRows = useMemo(
    () => rows.map((r) => (typeof r.id === "string" && st.shown[r.id] ? { ...r, ...st.shown[r.id] } : r)),
    [rows, st.shown],
  );
  const byId = useMemo(() => new Map(shownRows.map((r) => [String(r.id), r] as const)), [shownRows]);

  const edit = useMemo<GridEdit | undefined>(() => {
    if (!editor) return undefined;
    const keyAt = (index: number): string | undefined => fields[index]?.key;
    const can = (index: number) => {
      const k = keyAt(index);
      return k !== undefined && editor.canEdit(k);
    };
    return {
      can,
      open: (id, index) => { const k = keyAt(index); if (k) editor.open(id, k); },
      pending: (id, index) => st.pending.has(cellId(id, keyAt(index) ?? "")),
      editor: (id, index) => {
        const f = fields[index];
        if (!f) return null;
        const current = byId.get(id)?.[f.key];
        const busy = st.pending.has(cellId(id, f.key));
        if (f.type === "checkbox") {
          return (
            <Checkbox
              size="sm" aria-label={`${labelOf(f)}`} checked={current === true} disabled={busy}
              onChange={(e) => { void editor.commit(id, f.key, e.target.checked, current); }}
            />
          );
        }
        if (st.editing?.id !== id || st.editing.key !== f.key) return null;
        if (f.type === "select") {
          return (
            <SelectButton
              autoOpen label={`Edit ${labelOf(f)}`} value={typeof current === "string" ? current : NO_VALUE}
              widthClass="w-40"
              options={[{ value: NO_VALUE, label: "No value" }, ...(f.options ?? []).map((o) => ({ value: o, label: o }))]}
              onChange={(v) => { void editor.commit(id, f.key, v, current); }}
              onClose={() => editor.cancel()}
            />
          );
        }
        return (
          <TextEditor
            field={f} current={current}
            onCommit={(draft) => { void editor.commit(id, f.key, draft, current); }}
            onCancel={() => editor.cancel()}
          />
        );
      },
    };
  }, [editor, fields, st.editing, st.pending, byId]);

  const grid = useMemo(() => gridData(data, shownRows), [data, shownRows]);

  return (
    <div className="space-y-2">
      {/* The slot keeps its height, so a refusal does not push the grid down. */}
      <div data-refusal-slot="" className="min-h-7">
        {st.alert ? (
          <p role="alert" className="rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive">{st.alert}</p>
        ) : null}
      </div>
      <p className="sr-only" aria-live="polite">{st.live}</p>
      <DataGrid data={grid} edit={edit} />
    </div>
  );
}
