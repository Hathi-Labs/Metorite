"use client";

/**
 * Projects · "Save as template" (WS-39 TPL-1b).
 *
 * The project menu opens this. It asks for a name and a description, and it
 * says what the template keeps and what it leaves out BEFORE the click. The
 * work style of a template is not shown: no project has a work style yet
 * (SP-3), so the template holds none. A refusal of the route
 * (`source_not_org_wide`, `name_taken`) shows in place, with its next step,
 * and the typing stays.
 *
 * Drawn from the house primitives: `Modal`, `Button`, `Input`, `Textarea`.
 */

import { useId, useState } from "react";

import Button from "@/components/ui/Button";
import { Input, Textarea } from "@/components/ui/Input";
import Modal from "@/components/ui/Modal";

import type { ProjectRow } from "../lib/api";
import { TEMPLATE_KEEPS, TEMPLATE_LEAVES_OUT } from "../lib/templates";

interface Props {
  /** The project to save. `null` closes the dialog. */
  project: ProjectRow | null;
  /** The route's refusal, in words, shown IN the dialog. */
  error?: string | null;
  busy?: boolean;
  onClose: () => void;
  onSave: (project: ProjectRow, name: string, description: string) => void;
}

export function SaveAsTemplateDialog({ project, error, busy, onClose, onSave }: Props) {
  if (!project) return null;
  // Keyed on the project, so each open starts from that project's name.
  return (
    <SaveForm
      key={project.id}
      project={project}
      error={error}
      busy={busy}
      onClose={onClose}
      onSave={onSave}
    />
  );
}

function SaveForm({
  project,
  error,
  busy,
  onClose,
  onSave,
}: Omit<Props, "project"> & { project: ProjectRow }) {
  const ids = useId();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState("");
  const ready = name.trim().length > 0 && !busy;
  return (
    <Modal
      open
      onClose={onClose}
      title="Save as template"
      icon="LayoutTemplate"
      size="md"
      description={`Everyone in your organization can then start a project from ${project.name}.`}
    >
      <form
        className="space-y-3 p-3 text-xs"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) onSave(project, name.trim(), description.trim());
        }}
      >
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-name`} className="font-medium text-foreground">
            Template name
          </label>
          <Input
            id={`${ids}-name`}
            inputSize="md"
            autoFocus
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={`${ids}-desc`} className="font-medium text-foreground">
            Description <span className="font-normal text-muted-foreground">(optional)</span>
          </label>
          <Textarea
            id={`${ids}-desc`}
            inputSize="md"
            rows={3}
            value={description}
            maxLength={2000}
            placeholder="When to use this template"
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" data-testid="template-keeps">
          <div>
            <p className="mb-1 font-medium text-foreground">The template keeps</p>
            <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
              {TEMPLATE_KEEPS.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
          <div>
            <p className="mb-1 font-medium text-foreground">The template leaves out</p>
            <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
              {TEMPLATE_LEAVES_OUT.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          </div>
        </div>
        <p className="text-muted-foreground">
          Archived tasks and tasks in triage are not copied. A template is a copy made now, so
          later changes to this project do not reach it.
        </p>

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
          <Button type="submit" size="sm" icon="LayoutTemplate" loading={busy} disabled={!ready}>
            Save template
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default SaveAsTemplateDialog;
