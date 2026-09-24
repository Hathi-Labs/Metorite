"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { categoricalAccent } from "@/lib/categorical";

import type { LensArea } from "../lib/api";
import { whereGroups } from "../lib/clarify";
import type { MyTasksProject } from "../lib/types";

/**
 * The Clarify "Where" picker under the lens (WS-39 S6b).
 *
 * Two groups, in this order: **My Areas** (private, mine, flat by D65) and
 * **Company projects** (the boards `GET /projects/nodes` serves). The split
 * is the point — see `whereGroups` — so the same list is never drawn flat.
 *
 * Picking a row hands back its id as the task's `project_id`. An Area IS a
 * project row under one store, so the organize request does not care which
 * group the id came from; the delegate rule (`lensDelegateBlock`) does.
 *
 * "New Area…" mints one inline through the store, so the row appears in the
 * sidebar at the same moment and a refusal (a name I already have) lands on
 * the toast seam rather than in a local error nobody else sees.
 */
export function WherePicker({
  areas,
  includeAreas = true,
  includeNoProject = true,
  noProjectLabel = "No project",
  allowCreateArea = true,
  projects,
  tree,
  value,
  suggestedId,
  onChange,
  onCreateArea,
}: {
  areas: LensArea[];
  /** False for a task on a company board — see `isPersonalTask`. */
  includeAreas?: boolean;
  /** False for a task on a company board — see `whereOffersNoProject`. */
  includeNoProject?: boolean;
  /** The label of the "leave it loose" row. The capture chip says "Inbox". */
  noProjectLabel?: string;
  /** False where a new Area has no place (none today; kept for the chip). */
  allowCreateArea?: boolean;
  projects: MyTasksProject[];
  /**
   * S6g — the company TREE (`destinations`), the Move dialog's own list. When
   * given, the company group draws it: indented, folders shown and disabled.
   */
  tree?: readonly { node: { id: string; name: string }; depth: number; legal: boolean }[];
  value?: string;
  suggestedId?: string;
  onChange: (id: string | undefined) => void;
  /** Resolves with the new Area, or `undefined` when the server refused. */
  onCreateArea: (name: string) => Promise<LensArea | undefined>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const groups = whereGroups({
    areas,
    projects,
    includeAreas,
    companyRows: tree?.map((r) => ({
      id: r.node.id,
      name: r.node.name,
      depth: r.depth,
      legal: r.legal,
    })),
  });

  const submit = async () => {
    const clean = name.trim();
    if (!clean || busy) return;
    setBusy(true);
    try {
      const made = await onCreateArea(clean);
      if (made) {
        onChange(made.id);
        setCreating(false);
        setName("");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-background/40 p-2">
      {includeNoProject && (
        <Row
          selected={value === undefined}
          onClick={() => onChange(undefined)}
          icon={<Icon name="Inbox" className="h-3.5 w-3.5 text-muted-foreground" />}
          label={noProjectLabel}
          muted
        />
      )}
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-0.5">
          <p className="px-1.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            {group.label}
          </p>
          {group.rows.length === 0 && group.kind === "project" && (
            <p className="px-1.5 py-1 text-[11px] text-muted-foreground">
              No company projects you can file into.
            </p>
          )}
          {group.rows.map((row) => (
            <Row
              key={row.id}
              selected={value === row.id}
              // A suggestion is only ever a mark. The member clicks it to
              // pick it (audit 2026-09-24: never a pre-selection).
              suggested={value !== row.id && suggestedId === row.id}
              onClick={() => onChange(row.id)}
              depth={row.depth}
              disabled={row.disabled}
              icon={
                row.kind === "area" ? (
                  <span
                    aria-hidden
                    className={`h-2 w-2 shrink-0 rounded-full ${categoricalAccent(row.name).dot}`}
                  />
                ) : (
                  <Icon name="FolderKanban" className="h-3.5 w-3.5 text-muted-foreground" />
                )
              }
              label={row.name}
            />
          ))}
          {group.kind === "area" && allowCreateArea &&
            (creating ? (
              <div className="flex items-center gap-1.5 px-1.5 py-1">
                <Input
                  inputSize="sm"
                  autoFocus
                  value={name}
                  placeholder="Area name…"
                  aria-label="New area name"
                  disabled={busy}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void submit();
                    }
                    if (e.key === "Escape") {
                      setCreating(false);
                      setName("");
                    }
                  }}
                  className="flex-1"
                />
                <Button
                  type="button"
                  size="sm"
                  loading={busy}
                  disabled={!name.trim()}
                  onClick={() => void submit()}
                >
                  Add
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setCreating(false);
                    setName("");
                  }}
                >
                  Cancel
                </Button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="tech-transition flex items-center gap-1.5 px-1.5 py-1 text-left text-xs text-primary hover:underline"
              >
                <Icon name="Plus" className="h-3 w-3" /> New Area…
              </button>
            ))}
        </div>
      ))}
    </div>
  );
}

function Row({
  selected,
  suggested,
  muted,
  icon,
  label,
  onClick,
  depth = 0,
  disabled = false,
}: {
  selected: boolean;
  suggested?: boolean;
  muted?: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  /** S6g — the tree indent of a company row. */
  depth?: number;
  /** S6g — a folder holds projects, not tasks, so it cannot be picked. */
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      disabled={disabled}
      title={disabled ? "A folder holds projects, not tasks" : undefined}
      style={depth ? { paddingLeft: `${0.375 + depth * 0.875}rem` } : undefined}
      className={[
        "tech-transition flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-left text-xs",
        disabled
          ? "cursor-not-allowed text-muted-foreground/70"
          : selected
          ? "bg-primary/10 text-primary"
          : muted
            ? "text-muted-foreground hover:bg-secondary hover:text-foreground"
            : "text-foreground hover:bg-secondary",
      ].join(" ")}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {suggested && (
        <span className="rounded bg-secondary px-1.5 py-0.5 text-[9px] font-medium uppercase text-muted-foreground">
          suggested
        </span>
      )}
      {selected && <Icon name="Check" className="h-3.5 w-3.5 shrink-0" />}
    </button>
  );
}
