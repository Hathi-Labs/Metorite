"use client";

/**
 * Record names — the card in CRM settings → Fields (WS-53 CRM-Z10g, D133.1).
 *
 * An admin of a Native CRM renames the four record types here: "Existing
 * Sites" in place of "Contacts", for example. Each type has two boxes, the
 * name of the list and the name of one record, with the default name as the
 * placeholder. The name of the list alone is enough, and the record page then
 * uses it. One Save writes the whole map (`PUT /crm/settings/labels`).
 * "Use the default" empties the boxes of one type, and the next Save stores
 * that. The rail, the list headings and the record pages read the names from
 * `GET /crm/mode` (`useSourceLabels`), and the write wakes that read.
 *
 * In Mirror mode Zoho owns the names, so the card shows one line and no box.
 * A member who is not an admin sees nothing. With no mode, nothing shows.
 *
 * `RecordNamesView` only draws, so the test renders it without a DOM.
 * `RecordNamesCard` reads, writes and owns the boxes. The rules live in
 * `lib/recordNames.ts`.
 */

import { useState } from "react";

import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toast";

import { CrmError, saveRecordNames } from "../lib/api";
import type { CrmMode } from "../lib/connect";
import { useCrmModeValue } from "../lib/modeContext";
import {
  RECORD_NAME_MAX,
  RECORD_NAMES_MIRROR_NOTE,
  RECORD_NAMES_NOTE,
  RECORD_NAMES_TITLE,
  RECORD_TYPES,
  SAVE_NAMES_LABEL,
  USE_DEFAULT_LABEL,
  labelsBody,
  namesChanged,
  problemsOf,
  rowIsSet,
  rowsOf,
  singularPlaceholder,
  type NameRow,
  type NameRows,
} from "../lib/recordNames";
import type { EntitySlug } from "../lib/types";
import { useRecordNames } from "../lib/useCrmMode";
import type { SourceLabels } from "../lib/views";
import { Card } from "./SettingsCards";

/** The boxes, the stored names they started from, and the last saved answer. */
type Draft = { base: string; rows: NameRows; saved?: SourceLabels | null };

export type RecordNamesViewProps = {
  mode: CrmMode;
  isAdmin: boolean;
  rows: NameRows;
  /** The boxes differ from the stored names. */
  changed: boolean;
  saving: boolean;
  onChange: (slug: EntitySlug, row: NameRow) => void;
  onSave: () => void;
};

/** The card, drawn from props only. */
export function RecordNamesView({
  mode,
  isAdmin,
  rows,
  changed,
  saving,
  onChange,
  onSave,
}: RecordNamesViewProps) {
  if (!isAdmin) return null;
  if (mode === "mirror") {
    // The note is the body, not the header note. A header with no body draws
    // its bottom border on the border of the card, so the two lines doubled.
    return (
      <Card title={RECORD_NAMES_TITLE}>
        <p className="px-4 py-3 text-xs text-muted-foreground">{RECORD_NAMES_MIRROR_NOTE}</p>
      </Card>
    );
  }
  if (mode !== "native") return null;
  const problems = problemsOf(rows);
  const blocked = Object.keys(problems).length > 0;
  return (
    <Card title={RECORD_NAMES_TITLE} note={RECORD_NAMES_NOTE}>
      <div className="divide-y divide-border/60">
        {RECORD_TYPES.map((type) => {
          const row = rows[type.slug];
          const problem = problems[type.slug];
          const problemId = `crm-record-name-${type.slug}-problem`;
          const field = (part: keyof NameRow, label: string, placeholder: string) => (
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              {label}
              <Input
                value={row[part]}
                placeholder={placeholder}
                maxLength={RECORD_NAME_MAX}
                disabled={saving}
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? problemId : undefined}
                onChange={(e) => onChange(type.slug, { ...row, [part]: e.target.value })}
              />
            </label>
          );
          return (
            <div key={type.slug} role="group" aria-label={type.plural} className="px-4 py-3">
              <div className="flex min-h-7 items-center justify-between gap-2">
                <h3 className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {type.plural}
                </h3>
                {rowIsSet(row) && (
                  <Button
                    variant="text"
                    size="sm"
                    disabled={saving}
                    onClick={() => onChange(type.slug, { plural: "", singular: "" })}
                  >
                    {USE_DEFAULT_LABEL}
                  </Button>
                )}
              </div>
              <div className="mt-1 grid gap-2 sm:grid-cols-2">
                {field("plural", "Name of the list", type.plural)}
                {field("singular", "Name of one record", singularPlaceholder(type, row))}
              </div>
              {problem && (
                <p id={problemId} className="mt-1 text-xs text-destructive">
                  {problem}
                </p>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex justify-end border-t border-border px-4 py-3">
        <Button size="sm" loading={saving} disabled={!changed || blocked} onClick={onSave}>
          {SAVE_NAMES_LABEL}
        </Button>
      </div>
    </Card>
  );
}

/** The card with its read and its write. The page shows it to an admin only. */
export default function RecordNamesCard({ isAdmin }: { isAdmin: boolean }) {
  const toast = useToast();
  const mode = useCrmModeValue();
  const names = useRecordNames(isAdmin && mode === "native");
  const stored = names.labels;
  // The boxes the admin typed, over the stored names they started from. A
  // re-read that changes the stored names drops the boxes, as the other
  // settings drafts do. After a Save, `saved` holds the answer until that
  // re-read lands, so the boxes never flash back to the old names.
  const base = JSON.stringify(stored ?? {});
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const live = draft && draft.base === base ? draft : null;
  const rows = live ? live.rows : rowsOf(stored);
  const against = live?.saved !== undefined ? live.saved : stored;

  if (!isAdmin) return null;
  if (mode === "native" && names.loading) {
    return (
      <div
        className="space-y-2 rounded-xl border border-border bg-card/40 px-4 py-3"
        aria-label="Loading record names"
      >
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }
  if (mode === "native" && !names.known) {
    // A failed first read must not offer empty boxes: a Save would clear the
    // names that an admin stored.
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card/40 px-4 py-3">
        <p className="text-xs text-destructive">Could not load the record names. Try again.</p>
        <Button size="sm" variant="secondary" onClick={() => names.refresh()}>
          Try again
        </Button>
      </div>
    );
  }

  async function save() {
    setSaving(true);
    try {
      const answer = await saveRecordNames(labelsBody(rows));
      setDraft({ base, rows: rowsOf(answer.labels), saved: answer.labels });
      toast.show({ key: "crm-record-names", variant: "success", title: "Record names saved" });
    } catch (err) {
      toast.show({
        key: "crm-record-names",
        variant: "error",
        title: err instanceof CrmError ? err.message : "The record names did not save. Try again.",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <RecordNamesView
      mode={mode}
      isAdmin={isAdmin}
      rows={rows}
      changed={namesChanged(rows, against)}
      saving={saving}
      onChange={(slug, row) =>
        setDraft({ base, rows: { ...rows, [slug]: row }, saved: live?.saved })
      }
      onSave={() => void save()}
    />
  );
}
