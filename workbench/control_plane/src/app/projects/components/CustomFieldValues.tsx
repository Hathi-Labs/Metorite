"use client";

/**
 * Projects · the custom-field block inside the task panel (WS-27l).
 *
 * One control per definition, in the project's own order. Saving sends **only
 * what moved** (`changedValues`) — a PATCH carrying every field would make a
 * single edit read as eight on the timeline, burying the one that mattered.
 *
 * The gateway is the authority on what a value may be, so a refusal is shown as
 * it came back rather than pre-empted here: two validators for one value are
 * two differently-worded messages for the same mistake, and only one of them
 * can be right.
 */

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import { Checkbox } from "@/components/ui/Checkbox";
import { useEffect, useState } from "react";

import type { FieldRow, TaskRow } from "../lib/api";
import { projectsApi } from "../lib/api";
import {
  type FieldDef,
  changedValues,
  needsOptions,
  ordered,
  toInput,
} from "../lib/customFields";

interface Props {
  task: TaskRow;
  fields: FieldRow[];
  onChanged: (task: TaskRow) => void;
}

export function CustomFieldValues({ task, fields, onChanged }: Props) {
  const defs = ordered(fields as FieldDef[]);
  const stored = task.custom_fields ?? {};

  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seeded whenever the task changes identity OR its stored values do, so a
  // value written by somebody else (or by an automation) replaces the draft
  // instead of being overwritten by a stale one on the next save.
  useEffect(() => {
    const next: Record<string, unknown> = {};
    for (const def of defs) {
      next[def.field_key] = toInput(def.field_type, stored[def.field_key]);
    }
    setDraft(next);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, JSON.stringify(stored), fields.length]);

  if (defs.length === 0) return null;

  const patch = changedValues(defs, stored, draft);
  const dirty = Object.keys(patch).length > 0;

  async function save() {
    if (!dirty) return;
    setSaving(true);
    setError(null);
    try {
      onChanged(await projectsApi.patchTask(task.id, { custom_fields: patch }));
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setSaving(false);
    }
  }

  const set = (key: string, value: unknown) =>
    setDraft((current) => ({ ...current, [key]: value }));

  return (
    <section className="border-t border-border px-3 py-2">
      <div className="mb-1 flex items-center justify-between">
        <h4 className="text-xs font-medium text-foreground">Fields</h4>
        {dirty ? (
          <Button size="sm" loading={saving} onClick={() => void save()}>
            Save
          </Button>
        ) : null}
      </div>

      {error ? (
        <p className="mb-2 rounded-md bg-muted px-2 py-1 text-xs text-foreground">
          {error}
        </p>
      ) : null}

      <dl className="space-y-2">
        {defs.map((def) => (
          <div key={def.id}>
            <dt className="text-[11px] text-muted-foreground">
              {def.name}
              {def.description ? (
                <span className="ml-1 opacity-70">— {def.description}</span>
              ) : null}
            </dt>
            <dd className="mt-0.5">
              <FieldControl
                def={def}
                value={draft[def.field_key]}
                onChange={(next) => set(def.field_key, next)}
                onEnter={() => void save()}
              />
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/**
 * ONE control per field type, for every surface that edits a custom field.
 *
 * Lifted out of the task panel's block (S6c) so the move dialog's
 * required-field step draws the same checkbox, the same picker and the same
 * input as the panel — a second set of controls for the same definitions is
 * the drift `DESIGN_SYSTEM.md` rule 4 names. `value` is what the control
 * holds (see `toInput`), and `onChange` hands back the raw control value;
 * `toWire` is the caller's job, at save time.
 */
export function FieldControl({
  def,
  value,
  onChange,
  onEnter,
  disabled,
}: {
  def: FieldDef;
  value: unknown;
  onChange: (next: unknown) => void;
  onEnter?: () => void;
  disabled?: boolean;
}) {
  if (def.field_type === "boolean") {
    return (
      <label className="flex items-center gap-2 text-xs text-foreground">
        <Checkbox
          checked={value === true}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          aria-label={def.name}
        />
        {value === true ? "Yes" : "No"}
      </label>
    );
  }
  if (def.field_type === "select") {
    return (
      <SelectButton
        label={def.name}
        widthClass="w-full"
        disabled={disabled}
        value={String(value ?? "")}
        onChange={(next) => onChange(next)}
        options={[
          // An explicit "not set" row: without it the field can
          // never be emptied once somebody has chosen something.
          { value: "", label: "— not set —" },
          ...def.options.map((option) => ({
            value: option,
            label: option,
          })),
        ]}
      />
    );
  }
  if (def.field_type === "multi_select") {
    const chosen = Array.isArray(value) ? (value as string[]) : [];
    return (
      <div className="flex flex-wrap gap-1">
        {def.options.map((option) => {
          const on = chosen.includes(option);
          return (
            <Button
              key={option}
              size="sm"
              disabled={disabled}
              variant={on ? "primary" : "secondary"}
              aria-pressed={on}
              onClick={() =>
                onChange(
                  on ? chosen.filter((v) => v !== option) : [...chosen, option]
                )
              }
            >
              {option}
            </Button>
          );
        })}
      </div>
    );
  }
  return (
    <Input
      inputSize="sm"
      aria-label={def.name}
      disabled={disabled}
      type={
        def.field_type === "number"
          ? "number"
          : def.field_type === "date"
            ? "date"
            : def.field_type === "url"
              ? "url"
              : "text"
      }
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") onEnter?.();
      }}
    />
  );
}
