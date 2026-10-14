"use client";

/**
 * The form of one field in the field studio (WS-53 CRM-C4c).
 *
 * Spec: project-docs/specs/crm_platform.md section 20.7, "CRM-C4c: the studio
 * UI", done-whens 1, 2, 5 to 10. One `Modal` creates a field or edits one.
 *
 * - **Create** offers each of the 15 types. A lookup picks one of the four
 *   entity slugs as its target.
 * - **Edit** sends only the parts that changed (`patchBody`). The type menu
 *   offers the matrix changes only (`typeChoices`).
 * - **A system field** offers its label and a hide only. A system field that
 *   the CRM needs offers no hide (`editableParts`).
 * - **The default** is `FieldInput` of CRM-C4p, so it takes the client check
 *   of its type before the save. A lookup has no default.
 * - **A refusal** shows beside the control it names (`errorPart`), and the
 *   form stays open with what the admin typed.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Modal from "@/components/ui/Modal";
import { Input, Textarea } from "@/components/ui/Input";
import { SelectButton, type SelectOption } from "@/components/ui/SelectButton";
import type { FieldDef } from "../lib/fields";
import type { FieldSection } from "../lib/fieldLayout";
import { LOOKUP_TARGETS } from "../lib/fieldLayout";
import {
  HELP_MAX,
  LABEL_MAX,
  TARGET_LABELS,
  checkDraft,
  createBody,
  draftOf,
  editableParts,
  errorPart,
  hasOptions,
  newDraft,
  patchBody,
  probeDef,
  retype,
  typeChoices,
  typeLabel,
  type DraftProblems,
  type FieldDraft,
  type FormPart,
  type LookupTarget,
} from "../lib/fieldStudio";
import FieldInput from "./FieldInput";
import FieldStudioOptions from "./FieldStudioOptions";

/** What the form opens on: a new field in a section, or a field that exists. */
export type FieldForm = { mode: "create"; section: string | null } | { mode: "edit"; def: FieldDef };

/** The value that puts a field in no section. `SelectButton` needs a string. */
const NO_SECTION = "";

/** The section choices of a field. */
export function sectionOptions(sections: readonly FieldSection[]): SelectOption[] {
  return [
    { value: NO_SECTION, label: "No section" },
    ...sections.map((s) => ({ value: s.id, label: s.label })),
  ];
}

/** The target choices of a lookup: the four entity slugs, never a Zoho module. */
export const TARGET_OPTIONS: SelectOption[] = LOOKUP_TARGETS.map((t) => ({
  value: t,
  label: TARGET_LABELS[t],
}));

/** The line under a control: its error, else nothing. */
function Problem({ id, text }: { id: string; text?: string }) {
  if (!text) return null;
  return (
    <p id={id} role="alert" className="mt-1 text-[11px] text-destructive">
      {text}
    </p>
  );
}

function Row({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      {htmlFor ? (
        <label htmlFor={htmlFor} className="block text-xs font-medium text-foreground">
          {label}
        </label>
      ) : (
        <span className="block text-xs font-medium text-foreground">{label}</span>
      )}
      {children}
      {hint ? <p className="text-[11px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

export default function FieldStudioFieldDialog({
  form,
  sections,
  onClose,
  onSave,
  onRetire,
}: {
  /** null: the dialog is closed. */
  form: FieldForm | null;
  sections: readonly FieldSection[];
  onClose: () => void;
  /**
   * Sends the write. It resolves when the gateway takes it, and it throws
   * the `CrmError` of a refusal.
   */
  onSave: (form: FieldForm, body: Record<string, unknown>) => Promise<void>;
  /** Asks to retire the field. The studio confirms first. */
  onRetire: (def: FieldDef) => void;
}) {
  return (
    <Modal
      open={form !== null}
      onClose={onClose}
      title={form?.mode === "edit" ? `Edit ${form.def.label}` : "New field"}
      icon="TextCursorInput"
      size="lg"
    >
      {form ? (
        // A key for each field, so the form starts again from the stored values.
        <FieldFormBody
          key={form.mode === "edit" ? `edit:${form.def.key}` : `create:${form.section ?? ""}`}
          form={form}
          sections={sections}
          onClose={onClose}
          onSave={onSave}
          onRetire={onRetire}
        />
      ) : null}
    </Modal>
  );
}

/** The form inside the dialog. Exported for `crmC4.test.ts`, which has no DOM for a portal. */
export function FieldFormBody({
  form,
  sections,
  onClose,
  onSave,
  onRetire,
}: {
  form: FieldForm;
  sections: readonly FieldSection[];
  onClose: () => void;
  onSave: (form: FieldForm, body: Record<string, unknown>) => Promise<void>;
  onRetire: (def: FieldDef) => void;
}) {
  const def = form.mode === "edit" ? form.def : null;
  const [draft, setDraft] = useState<FieldDraft>(() =>
    def ? draftOf(def) : newDraft(form.mode === "create" ? form.section : null),
  );
  const [problems, setProblems] = useState<DraftProblems>({});
  const [saving, setSaving] = useState(false);
  const base = useId();
  const id = (part: FormPart) => `${base}-${part}`;
  const errId = (part: FormPart) => `${base}-${part}-error`;

  const parts = def
    ? editableParts(def)
    : editableParts({ ...probeDef(draft, ""), is_system: false });
  const system = def?.is_system === true;
  const choices = typeChoices(def);

  const set = (next: Partial<FieldDraft>) => setDraft((d) => ({ ...d, ...next }));

  async function save() {
    const found = checkDraft(draft, def);
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    const body = def ? patchBody(def, draft) : createBody(draft);
    if (def && Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    try {
      await onSave(form, body);
      onClose();
    } catch (err) {
      const { part, message } = errorPart(err);
      setProblems({ [part]: message });
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {problems.form ? (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {problems.form}
          </p>
        ) : null}

        {system ? (
          <p className="text-[11px] text-muted-foreground">
            A system field is a fixed part of each record. You can change its label, hide it and
            drag it to a new place. Its type stays the same.
          </p>
        ) : null}

        <Row label="Label" htmlFor={id("label")}>
          <Input
            id={id("label")}
            inputSize="sm"
            autoFocus
            maxLength={LABEL_MAX}
            value={draft.label}
            aria-invalid={problems.label ? true : undefined}
            aria-describedby={problems.label ? errId("label") : undefined}
            onChange={(e) => set({ label: e.target.value })}
          />
          <Problem id={errId("label")} text={problems.label} />
        </Row>

        {!system ? (
          <Row
            label="Type"
            hint={
              def && choices.length === 1
                ? "The type of this field cannot change, because each value it holds must stay valid."
                : def
                  ? "Only the changes that keep each stored value valid are offered."
                  : undefined
            }
          >
            <SelectButton
              id={id("type")}
              label="Type"
              value={draft.type}
              options={choices.map((t) => ({ value: t, label: typeLabel(t) }))}
              onChange={(type) => setDraft((d) => retype(d, type))}
              widthClass="w-full sm:w-56"
              disabled={choices.length === 1}
              invalid={!!problems.type}
              describedBy={problems.type ? errId("type") : undefined}
            />
            <Problem id={errId("type")} text={problems.type} />
          </Row>
        ) : null}

        {!system && draft.type === "lookup" ? (
          <Row
            label="Finds a record in"
            hint={def ? "The target stays while a record holds a value of this field." : undefined}
          >
            <SelectButton
              id={id("target")}
              label="Lookup target"
              value={draft.lookup_target ?? ""}
              options={TARGET_OPTIONS}
              prompt="Choose a record type"
              onChange={(target) => set({ lookup_target: target as LookupTarget })}
              widthClass="w-full sm:w-56"
              invalid={!!problems.target}
              describedBy={problems.target ? errId("target") : undefined}
            />
            <Problem id={errId("target")} text={problems.target} />
          </Row>
        ) : null}

        {!system && hasOptions(draft.type) ? (
          <Row label="Options">
            <FieldStudioOptions
              options={draft.options}
              onChange={(options) => set({ options })}
              error={!!problems.options}
              describedBy={problems.options ? errId("options") : undefined}
            />
            <Problem id={errId("options")} text={problems.options} />
          </Row>
        ) : null}

        {parts.section ? (
          <Row label="Section">
            <SelectButton
              id={id("section")}
              label="Section"
              value={draft.section_id ?? NO_SECTION}
              options={sectionOptions(sections)}
              onChange={(value) => set({ section_id: value === NO_SECTION ? null : value })}
              widthClass="w-full sm:w-56"
              invalid={!!problems.section}
              describedBy={problems.section ? errId("section") : undefined}
            />
            <Problem id={errId("section")} text={problems.section} />
          </Row>
        ) : null}

        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {parts.required ? (
            <label className="flex items-center gap-1.5 text-xs text-foreground">
              <Checkbox
                size="sm"
                checked={draft.required}
                onChange={(e) => set({ required: e.target.checked })}
              />
              Required
            </label>
          ) : null}
          {parts.visible ? (
            <label className="flex items-center gap-1.5 text-xs text-foreground">
              <Checkbox
                size="sm"
                checked={!draft.visible}
                onChange={(e) => set({ visible: !e.target.checked })}
              />
              Hidden
            </label>
          ) : (
            <p className="text-[11px] text-muted-foreground">
              The CRM needs this field, so it stays visible.
            </p>
          )}
        </div>
        {parts.required ? (
          <p className="-mt-2 text-[11px] text-muted-foreground">
            A member must give a value to a required field. No form or list shows a hidden field,
            and each record keeps its value.
          </p>
        ) : null}
        <Problem id={errId("visible")} text={problems.visible} />

        {parts.help ? (
          <Row label="Help text" htmlFor={id("help")} hint="A short line under the input on each form.">
            <Textarea
              id={id("help")}
              rows={2}
              maxLength={HELP_MAX}
              value={draft.help_text}
              aria-invalid={problems.help ? true : undefined}
              aria-describedby={problems.help ? errId("help") : undefined}
              onChange={(e) => set({ help_text: e.target.value })}
            />
            <Problem id={errId("help")} text={problems.help} />
          </Row>
        ) : null}

        {!system ? (
          draft.type === "lookup" ? (
            <p className="text-[11px] text-muted-foreground">A lookup has no default value.</p>
          ) : (
            <Row label="Default value" htmlFor={id("default")} hint="A new record gets this value.">
              <FieldInput
                // A new type draws a new input, with its own draft text.
                key={draft.type}
                id={id("default")}
                def={probeDef(draft)}
                value={draft.default_value}
                held={def?.default_value}
                onChange={(next) => set({ default_value: next })}
                error={problems.default}
              />
            </Row>
          )
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3">
        {def && parts.retire ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            icon="Archive"
            disabled={saving}
            onClick={() => onRetire(def)}
          >
            Retire
          </Button>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" size="sm" loading={saving}>
            {def ? "Save" : "Create field"}
          </Button>
        </div>
      </div>
    </form>
  );
}
