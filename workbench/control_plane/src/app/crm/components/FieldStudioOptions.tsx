"use client";

/**
 * The option editor of a picklist or a multi-select (WS-53 CRM-C4c,
 * done-when 5).
 *
 * Spec: project-docs/specs/crm_platform.md section 20.7 item 6. An admin adds,
 * renames, reorders and retires an option here, and the field form saves the
 * list in one write (`optionsBody` in `lib/fieldStudio.ts`).
 *
 * - A rename changes the label only. The `value` of a saved option never
 *   changes, so each record that holds it still finds it.
 * - A saved option never leaves the list. Retire keeps it with
 *   `retired: true`, and the gateway refuses a list that leaves one out
 *   (422 `option_removed`). Only a new option, which has no `value` yet, can be
 *   removed before the save.
 * - The order changes by HTML5 drag, as in the stage grid
 *   (`PipelineSettings.tsx`).
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { OPTION_LABEL_MAX, moveOption, type OptionDraft } from "../lib/fieldStudio";

export default function FieldStudioOptions({
  options,
  onChange,
  error,
  describedBy,
}: {
  options: OptionDraft[];
  onChange: (next: OptionDraft[]) => void;
  /** The client check or the 422 of the options. The form draws it. */
  error?: boolean;
  /** The id of the error line under the editor. */
  describedBy?: string;
}) {
  const [dragging, setDragging] = useState<number | null>(null);
  const [adding, setAdding] = useState("");

  const edit = (index: number, next: Partial<OptionDraft>) =>
    onChange(options.map((o, i) => (i === index ? { ...o, ...next } : o)));

  function add() {
    const label = adding.trim();
    if (!label) return;
    onChange([...options, { value: null, label, retired: false }]);
    setAdding("");
  }

  return (
    <div
      role="group"
      aria-label="Options"
      aria-describedby={describedBy}
      aria-invalid={error || undefined}
      className="space-y-1.5"
    >
      {options.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">No options yet. Add the first one below.</p>
      ) : (
        <ul className="space-y-1">
          {options.map((option, index) => (
            <li
              key={option.value ?? `new-${index}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (dragging !== null) onChange(moveOption(options, dragging, index));
                setDragging(null);
              }}
              className="flex items-center gap-1.5"
            >
              <span
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData("text/plain", option.value ?? option.label);
                  e.dataTransfer.effectAllowed = "move";
                  setDragging(index);
                }}
                onDragEnd={() => setDragging(null)}
                className="cursor-grab text-muted-foreground hover:text-foreground"
                title="Drag to reorder"
              >
                <Icon name="GripVertical" className="h-4 w-4" />
              </span>
              <Input
                inputSize="sm"
                aria-label={`Option ${index + 1}`}
                maxLength={OPTION_LABEL_MAX}
                value={option.label}
                onChange={(e) => edit(index, { label: e.target.value })}
                className="min-w-0 flex-1"
              />
              {option.retired && <Badge size="xs">Retired</Badge>}
              {option.value === null ? (
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  icon="X"
                  aria-label={`Remove ${option.label || "the new option"}`}
                  onClick={() => onChange(options.filter((_, i) => i !== index))}
                />
              ) : (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  icon={option.retired ? "RotateCcw" : "Archive"}
                  aria-label={`${option.retired ? "Restore" : "Retire"} ${option.label}`}
                  onClick={() => edit(index, { retired: !option.retired })}
                >
                  {option.retired ? "Restore" : "Retire"}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-1.5">
        <Input
          inputSize="sm"
          aria-label="New option"
          placeholder="New option"
          maxLength={OPTION_LABEL_MAX}
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          className="min-w-0 flex-1"
        />
        <Button type="button" size="sm" variant="secondary" icon="Plus" disabled={!adding.trim()} onClick={add}>
          Add
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        A retired option stays on each record that holds it. No picker offers it.
      </p>
    </div>
  );
}
