"use client";

/**
 * Projects · the "New project" picker (WS-39 TPL-1b).
 *
 * The "New from template…" entry of a row opens this. The choice is a blank
 * project or one template of the organization. Each template shows its counts.
 * A template asks for the start date, because every starter date counts from
 * it. A blank project asks for a name only. A refusal of the route shows in
 * place, with its next step, and the choices stay.
 *
 * Drawn from the house primitives: `Modal`, `Button`, `Input`, `Select`.
 */

import { useId, useMemo, useState } from "react";

import Button from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import type { ProjectRow } from "../lib/api";
import {
  BLANK_CHOICE,
  countsLine,
  pickerReady,
  statusesNote,
  type TemplateRow,
  templateOptionLabel,
  todayLocal,
} from "../lib/templates";

interface Props {
  /** The row to make the project under. `null` closes the picker. */
  parent: ProjectRow | null;
  /** `null` while the list loads. */
  templates: readonly TemplateRow[] | null;
  /** The word for the level: "project" or "subproject". */
  noun: string;
  error?: string | null;
  busy?: boolean;
  onClose: () => void;
  onBlank: (name: string) => void;
  onTemplate: (template: TemplateRow, name: string, startDate: string) => void;
}

export function NewFromTemplateDialog(props: Props) {
  if (!props.parent) return null;
  return <PickerForm key={props.parent.id} {...props} parent={props.parent} />;
}

function PickerForm({
  parent,
  templates,
  noun,
  error,
  busy,
  onClose,
  onBlank,
  onTemplate,
}: Props & { parent: ProjectRow }) {
  const ids = useId();
  const [choice, setChoice] = useState(BLANK_CHOICE);
  const [name, setName] = useState("");
  const [startDate, setStartDate] = useState(todayLocal());
  const chosen = useMemo(
    () => templates?.find((t) => t.id === choice) ?? null,
    [templates, choice],
  );
  const ready = pickerReady({ templateId: choice, name, startDate }) && !busy;
  const submit = () => {
    if (!ready) return;
    if (chosen) onTemplate(chosen, name.trim(), startDate);
    else onBlank(name.trim());
  };
  return (
    <Modal
      open
      onClose={onClose}
      title={`New ${noun}`}
      icon="LayoutTemplate"
      size="md"
      description={`Under ${parent.name}. Start blank, or from a template of your organization.`}
    >
      <form
        className="space-y-3 p-3 text-xs"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-from`} className="font-medium text-foreground">
            Start from
          </label>
          <Select
            id={`${ids}-from`}
            inputSize="md"
            autoFocus
            value={choice}
            disabled={templates === null}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value={BLANK_CHOICE}>Blank {noun}</option>
            {(templates ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {templateOptionLabel(t)}
              </option>
            ))}
          </Select>
          {templates === null ? (
            <p className="text-muted-foreground">Loading templates…</p>
          ) : templates.length === 0 ? (
            <p className="text-muted-foreground">
              Your organization has no templates yet. Open the menu of a project and choose
              “Save as template” to make one.
            </p>
          ) : null}
        </div>

        {chosen ? (
          <div
            className="rounded border border-border bg-muted/40 p-2 text-muted-foreground"
            data-testid="template-detail"
          >
            {chosen.description ? <p className="text-foreground">{chosen.description}</p> : null}
            {chosen.work_style ? <p>Work style: {chosen.work_style}</p> : null}
            <p>{countsLine(chosen.counts)}</p>
          </div>
        ) : null}

        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-name`} className="font-medium text-foreground">
            Name{" "}
            {chosen ? (
              <span className="font-normal text-muted-foreground">(optional)</span>
            ) : null}
          </label>
          <Input
            id={`${ids}-name`}
            inputSize="md"
            value={name}
            maxLength={200}
            placeholder={chosen ? chosen.name : `New ${noun}`}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        {chosen ? (
          <div className="flex flex-col gap-1">
            <label htmlFor={`${ids}-start`} className="font-medium text-foreground">
              Start date
            </label>
            <Input
              id={`${ids}-start`}
              inputSize="md"
              type="date"
              value={startDate}
              min="1900-01-01"
              max="2999-12-31"
              onChange={(e) => setStartDate(e.target.value)}
              aria-describedby={`${ids}-start-help`}
            />
            <p id={`${ids}-start-help`} className="text-muted-foreground">
              The starter tasks keep their distance from this day.
            </p>
            <p className="text-muted-foreground">{statusesNote(parent.name)}</p>
          </div>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="rounded border border-destructive/40 bg-destructive/10 p-2 text-destructive"
          >
            {error}
          </p>
        ) : null}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" size="sm" icon="Plus" loading={busy} disabled={!ready}>
            {chosen ? `Make ${noun} from template` : `Make ${noun}`}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default NewFromTemplateDialog;
