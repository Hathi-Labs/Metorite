"use client";

/**
 * The cell editors of a Studio grid, shared by the Table (`StudioTable`, D2b) and the sheet of line
 * items (`StudioSheet`, D17a). One file holds them, so the two grids edit a cell the same way.
 *
 * A text-like cell commits on Enter or on blur, and Escape cancels. A tick commits at once. A
 * select opens its list and commits on a pick. This file builds no control: it uses `Input`,
 * `Checkbox` and `SelectButton`. It never calls `door.run`.
 */
import { useRef, type ReactNode } from "react";

import Checkbox from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { labelOf, valueToDraft, type Draft } from "@/lib/studio/blocks";
import type { FieldSpec } from "@/lib/studio/fieldCheck";

export const NO_VALUE = "";

const INPUT_TYPE: Partial<Record<FieldSpec["type"], string>> = { date: "date", email: "email", url: "url", phone: "tel" };

export function TextEditor({ field, current, onCommit, onCancel }: {
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

/**
 * The editor that replaces a cell, or null to draw the cell with its edit button. A tick shows at
 * all times. Another type shows only while `editing` is true.
 */
export function cellEditor({ field, current, busy, editing, onCommit, onCancel }: {
  field: FieldSpec; current: unknown; busy: boolean; editing: boolean;
  onCommit: (draft: Draft) => void; onCancel: () => void;
}): ReactNode | null {
  if (field.type === "checkbox") {
    return (
      <Checkbox
        size="sm" aria-label={`${labelOf(field)}`} checked={current === true} disabled={busy}
        onChange={(e) => onCommit(e.target.checked)}
      />
    );
  }
  if (!editing) return null;
  if (field.type === "select") {
    return (
      <SelectButton
        autoOpen label={`Edit ${labelOf(field)}`} value={typeof current === "string" ? current : NO_VALUE}
        widthClass="w-40"
        options={[{ value: NO_VALUE, label: "No value" }, ...(field.options ?? []).map((o) => ({ value: o, label: o }))]}
        onChange={(v) => onCommit(v)}
        onClose={onCancel}
      />
    );
  }
  return <TextEditor field={field} current={current} onCommit={onCommit} onCancel={onCancel} />;
}
