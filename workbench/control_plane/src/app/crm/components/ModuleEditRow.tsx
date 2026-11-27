"use client";

/**
 * One editable row of a generic module record (CRM-Z10d), drawn in the look
 * of the Fields tab of a core record (`LayoutFieldRow`): the value and a
 * Pencil, then `FieldInput` with Save and Cancel.
 *
 * - The client check (`checkValue`) runs first. A refused save keeps the
 *   edit open with the typed value, and the message shows under the input.
 * - A save sends only this key (`modulePatch`).
 * - Enter saves and Escape cancels (`editKey`).
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import { checkValue, formValue } from "../lib/fieldLayout";
import { formatField, type FieldDef, type FieldValue as Value } from "../lib/fields";
import { modulePatch, moduleStored, type ModuleRecord } from "../lib/modules";
import { editKey } from "../lib/recordForm";
import type { CrmRefusal } from "../lib/store";
import FieldInput from "./FieldInput";
import FieldValue from "./FieldValue";

/**
 * The message beside a field: the client check first, else the refusal of the
 * gateway when it names this key (or none) and the save is over.
 */
export function rowError(
  key: string,
  problem: string | null,
  refused: boolean,
  saving: boolean,
  refusal: CrmRefusal | null,
): string | null {
  const mine = refusal && (refusal.field === null || refusal.field === key) ? refusal.message : null;
  return problem ?? (refused && !saving ? mine : null);
}

export const LABEL = "w-28 shrink-0 pt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground";

export default function ModuleEditRow({
  def,
  record,
  shown,
  saving,
  refusal,
  onPatch,
}: {
  def: FieldDef;
  record: ModuleRecord;
  /** What the row shows while it is not in edit. Absent: the formatted value. */
  shown?: Value;
  saving: boolean;
  refusal: CrmRefusal | null;
  onPatch: (body: Record<string, unknown>) => Promise<boolean>;
}) {
  const id = useId();
  const stored = moduleStored(record, def);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<unknown>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [refused, setRefused] = useState(false);
  const error = rowError(def.key, problem, refused, saving, refusal);

  function begin() {
    setDraft(formValue(def, stored));
    setProblem(null);
    setRefused(false);
    setEditing(true);
  }

  async function commit() {
    setRefused(false);
    const body = modulePatch(def, record, draft);
    if (!body) {
      setEditing(false);
      return;
    }
    const check = checkValue(def, formValue(def, draft), stored);
    if (check) {
      setProblem(check);
      return;
    }
    if (await onPatch(body)) setEditing(false);
    else setRefused(true);
  }

  return (
    <div className="flex items-start gap-3 px-4 py-2">
      <dt className={LABEL}>{editing ? <label htmlFor={id}>{def.label}</label> : def.label}</dt>
      <dd className="min-w-0 flex-1">
        {editing ? (
          <div
            className="flex items-start gap-1"
            onKeyDown={(e) => {
              const act = editKey(e.key, (e.target as HTMLElement).tagName);
              if (act === "cancel") {
                e.stopPropagation();
                setEditing(false);
              } else if (act === "commit") {
                e.preventDefault();
                void commit();
              }
            }}
          >
            <div className="min-w-0 flex-1">
              <FieldInput
                id={id}
                def={def}
                value={draft}
                held={stored}
                error={error}
                onChange={(next) => {
                  setDraft(next);
                  setProblem(null);
                  setRefused(false);
                }}
              />
            </div>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="Check"
              aria-label={`Save ${def.label.toLowerCase()}`}
              disabled={saving}
              onClick={() => void commit()}
            />
            <Button
              variant="ghost"
              size="icon-xs"
              icon="X"
              aria-label="Cancel"
              onClick={() => setEditing(false)}
            />
          </div>
        ) : (
          <span className="flex items-start gap-1.5 text-xs text-foreground">
            <span className="min-w-0 flex-1 break-words">
              <FieldValue value={shown ?? formatField(def, stored)} />
            </span>
            <Button
              variant="ghost"
              size="icon-xs"
              icon="Pencil"
              aria-label={`Edit ${def.label.toLowerCase()}`}
              onClick={begin}
            />
          </span>
        )}
      </dd>
    </div>
  );
}
