"use client";

/**
 * The import's tree step — every ClickUp Space, Folder and List, with a
 * checkbox to leave it out and a rename (WS-41 I-8).
 *
 * Spec: `project-docs/specs/project_import.md` §9 row I-8.
 *
 * The rows come from `importFlow.importTreeRows`, which mirrors the gateway's rule:
 * leaving a Space or Folder out leaves out everything under it, so a row under
 * one is shown and cannot be ticked. The server plans again on save, and its
 * answer is what the Import button acts on.
 */

import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import Input from "@/components/ui/Input";

import type { TreeRow } from "../lib/importFlow";

const KIND_ICON: Record<TreeRow["kind"], string> = { space: "LayoutGrid", folder: "Folder", project: "List" };
const KIND_LABEL: Record<TreeRow["kind"], string> = { space: "space", folder: "folder", project: "list" };

interface Props {
  rows: readonly TreeRow[];
  onSkip: (ref: string, skip: boolean) => void;
  /** `null` takes the ClickUp name back. */
  onRename: (ref: string, name: string | null) => void;
}

export default function ImportTree({ rows, onSkip, onRename }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <ul className="divide-y divide-border rounded-md border border-border" aria-label="Spaces and lists in the file">
      {rows.map((row) => {
        const out = row.skipSelf || row.skipInherited;
        const renamed = row.shownName !== row.name;
        return (
          <li
            key={row.ref}
            className="flex flex-wrap items-center gap-2 py-1.5 pr-2"
            style={{ paddingLeft: `${0.5 + row.depth * 1.25}rem` }}
          >
            <Checkbox
              size="sm"
              checked={!out}
              disabled={row.skipInherited}
              aria-label={`Import ${KIND_LABEL[row.kind]} ${row.shownName}`}
              title={row.skipInherited ? "Left out with the space or folder above it" : undefined}
              onChange={(e) => onSkip(row.ref, !e.target.checked)}
            />
            <Icon name={KIND_ICON[row.kind]} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            {editing === row.ref ? (
              <form
                className="flex min-w-0 flex-1 items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  const next = draft.trim();
                  onRename(row.ref, next && next !== row.name ? next : null);
                  setEditing(null);
                }}
              >
                <Input
                  autoFocus
                  inputSize="sm"
                  value={draft}
                  maxLength={120}
                  aria-label={`New name for ${row.name}`}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key !== "Escape") return;
                    // The first Escape leaves the field, not the wizard.
                    e.stopPropagation();
                    setEditing(null);
                  }}
                />
                <Button type="submit" size="sm">
                  Save
                </Button>
              </form>
            ) : (
              <div className={`min-w-0 flex-1 ${out ? "text-muted-foreground line-through" : ""}`}>
                <p className="truncate text-xs font-medium">{row.shownName}</p>
                {renamed ? <p className="truncate text-[11px] text-muted-foreground">In ClickUp: {row.name}</p> : null}
              </div>
            )}
            {row.kind === "project" ? (
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {row.tasks} {row.tasks === 1 ? "task" : "tasks"}
              </span>
            ) : null}
            {editing !== row.ref ? (
              <Button
                variant="ghost"
                size="icon-sm"
                icon="Pencil"
                aria-label={`Rename ${row.shownName}`}
                disabled={out}
                onClick={() => {
                  setEditing(row.ref);
                  setDraft(row.shownName);
                }}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
