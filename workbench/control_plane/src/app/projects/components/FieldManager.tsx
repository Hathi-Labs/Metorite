"use client";

/**
 * Projects · managing custom field definitions (WS-27l).
 *
 * Definitions are root-scoped, exactly as statuses and types are, so this
 * manages the whole tree's fields from whichever node is selected.
 *
 * **Two things this dialog says out loud, because the API refuses them and a
 * refusal nobody predicted reads as a bug:**
 *
 * * the key is shown while the name is still being typed, and marked permanent
 *   — it cannot be renamed later, since every stored value is filed under it;
 * * deleting a field says it will remove the values too, before the click, and
 *   reports how many it removed after.
 */

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import SelectButton from "@/components/ui/SelectButton";
import Modal from "@/components/ui/Modal";

/** See `TagManager` — the same marker, the same reason. */
const orgWide = (row: { project_id?: string | null }): boolean =>
  row.project_id === null;

/**
 * ⚠️ Narrowed on 2026-09-20 (D-PM-33), because the old wording is now wrong.
 *
 * It said "edit it in organization settings". An org-wide field CAN be
 * renamed since that ruling, by somebody holding `admin:settings:manage`, and
 * the rename touches no task at all — `field_key` is never editable, so only
 * the label moves. What is still refused is the DELETE, which clears the
 * key off every task in the organization.
 *
 * This list has no rename control to open up. Adding one belongs with the
 * admin surface (H-4), where the organization's own fields are listed.
 */
const ORG_WIDE_NOTE =
  "Shared by the whole organization — it cannot be deleted from inside " +
  "one project.";
import { useEffect, useState } from "react";

import { type FieldRow, projectsApi } from "../lib/api";
import {
  FIELD_TYPES,
  FIELD_TYPE_LABELS,
  type FieldType,
  keyPreview,
  needsOptions,
  ordered,
} from "../lib/customFields";

interface Props {
  projectId: string;
  projectName: string;
  onClose: () => void;
  onChanged: (fields: FieldRow[]) => void;
}

export function FieldManager({ projectId, projectName, onClose, onChanged }: Props) {
  const [fields, setFields] = useState<FieldRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [type, setType] = useState<FieldType>("text");
  const [options, setOptions] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      const res = await projectsApi.fields(projectId);
      setFields(res.rows);
      onChanged(res.rows);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await projectsApi.createField(projectId, {
        name: name.trim(),
        field_type: type,
        options: needsOptions(type)
          ? options
              .split(",")
              .map((o) => o.trim())
              .filter(Boolean)
          : [],
      });
      setName("");
      setOptions("");
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function remove(field: FieldRow) {
    setError(null);
    setNotice(null);
    try {
      const gone = await projectsApi.deleteField(field.id);
      const cleared = gone.cascaded.values_cleared;
      // Reported rather than assumed: losing data silently is what makes people
      // stop trusting a delete button.
      setNotice(
        cleared
          ? `Removed “${field.name}” and cleared it from ${cleared} task${
              cleared === 1 ? "" : "s"
            }.`
          : `Removed “${field.name}”. No task was using it.`
      );
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    // WS-27ak — this dialog had NO Escape and NO outside-press dismissal at
    // all, and nothing focusable was reachable from the keyboard once it was
    // up. Both arrive with the primitive.
    <Modal
      open
      onClose={onClose}
      title="Custom fields"
      description={`Shared by ${projectName} and everything under it`}
      size="lg"
    >
      {error ? (
        <p className="border-b border-border bg-muted px-3 py-2 text-xs text-foreground">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="border-b border-border px-3 py-2 text-xs text-muted-foreground">
          {notice}
        </p>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : fields.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No custom fields yet. Add one below — it appears on every task in
            this project and everything under it.
          </p>
        ) : (
          <ul className="space-y-1">
            {ordered(fields as never).map((field) => (
              <li
                key={field.id}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1.5"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-foreground">
                    {field.name}
                  </span>
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {field.field_key}
                    {field.options.length ? ` · ${field.options.join(", ")}` : ""}
                  </span>
                </span>
                {/* ⚠️ Same marker `TagManager` now reads: the wire sends
                    `project_id: null` for an org-wide row and the gateway
                    refuses every per-project write against one. Drawn and
                    disabled rather than hidden — the field really is on this
                    project's tasks, so leaving it off the list would make
                    this screen disagree with the task panel. */}
                {orgWide(field) ? (
                  <Badge
                    tone="primary"
                    title="Shared by every project in this organization"
                  >
                    Organization
                  </Badge>
                ) : null}
                <Badge>{FIELD_TYPE_LABELS[field.field_type as FieldType]}</Badge>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  icon="Trash2"
                  aria-label={`Delete ${field.name}`}
                  disabled={orgWide(field)}
                  title={
                    orgWide(field)
                      ? ORG_WIDE_NOTE
                      : `Delete “${field.name}” and clear it from every task`
                  }
                  onClick={() => void remove(field as FieldRow)}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      <form onSubmit={create} className="border-t border-border p-3">
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[8rem] flex-1 text-[11px] text-muted-foreground">
            Name
            <Input
              inputSize="sm"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Customer PO"
              aria-label="Field name"
            />
          </label>
          {/* A `div`, not a `label`: the control is a BUTTON now, and a
              label cannot forward a click to one. Leaving the element as a
              label would promise an association the DOM does not make. */}
          <div className="text-[11px] text-muted-foreground">
            Type
            <SelectButton
              label="Field type"
              widthClass="mt-0.5 w-[10rem]"
              value={type}
              onChange={(next) => setType(next as FieldType)}
              options={FIELD_TYPES.map((option) => ({
                value: option,
                label: FIELD_TYPE_LABELS[option],
              }))}
            />
          </div>
          <Button type="submit" size="sm" loading={busy} disabled={!name.trim()}>
            Add
          </Button>
        </div>

        {needsOptions(type) ? (
          <label className="mt-2 block text-[11px] text-muted-foreground">
            Choices, comma separated
            <Input
              inputSize="sm"
              value={options}
              onChange={(e) => setOptions(e.target.value)}
              placeholder="EU, IN, US"
              aria-label="Choices"
            />
          </label>
        ) : null}

        {name.trim() ? (
          // Shown BEFORE the field exists, because this is the last moment
          // anybody can change it: the key is what every stored value is
          // filed under and the API refuses to rename it.
          <p className="mt-2 flex items-center gap-1 text-[11px] text-muted-foreground">
            <Icon name="Key" size={11} />
            Key <code className="text-foreground">{keyPreview(name)}</code> —
            permanent once created.
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
