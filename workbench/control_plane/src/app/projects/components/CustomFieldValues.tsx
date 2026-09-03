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

const SELECT =
  "cc-control w-full rounded-lg border border-border bg-background px-2 py-1.5 " +
  "text-xs text-foreground outline-none focus:border-primary/50";

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
              {def.field_type === "boolean" ? (
                <label className="flex items-center gap-2 text-xs text-foreground">
                  <Checkbox
                    checked={draft[def.field_key] === true}
                    onChange={(e) => set(def.field_key, e.target.checked)}
                    aria-label={def.name}
                  />
                  {draft[def.field_key] === true ? "Yes" : "No"}
                </label>
              ) : def.field_type === "select" ? (
                <select
                  aria-label={def.name}
                  className={SELECT}
                  value={String(draft[def.field_key] ?? "")}
                  onChange={(e) => set(def.field_key, e.target.value)}
                >
                  {/* An explicit "not set" row: without it a select can never be
                      emptied once somebody has chosen something. */}
                  <option value="">— not set —</option>
                  {def.options.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              ) : def.field_type === "multi_select" ? (
                <div className="flex flex-wrap gap-1">
                  {def.options.map((option) => {
                    const chosen = Array.isArray(draft[def.field_key])
                      ? (draft[def.field_key] as string[])
                      : [];
                    const on = chosen.includes(option);
                    return (
                      <Button
                        key={option}
                        size="sm"
                        variant={on ? "primary" : "secondary"}
                        aria-pressed={on}
                        onClick={() =>
                          set(
                            def.field_key,
                            on
                              ? chosen.filter((v) => v !== option)
                              : [...chosen, option]
                          )
                        }
                      >
                        {option}
                      </Button>
                    );
                  })}
                </div>
              ) : (
                <Input
                  inputSize="sm"
                  aria-label={def.name}
                  type={
                    def.field_type === "number"
                      ? "number"
                      : def.field_type === "date"
                        ? "date"
                        : def.field_type === "url"
                          ? "url"
                          : "text"
                  }
                  value={String(draft[def.field_key] ?? "")}
                  onChange={(e) => set(def.field_key, e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void save();
                  }}
                />
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
