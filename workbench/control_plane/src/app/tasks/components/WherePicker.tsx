"use client";

import { useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import Input from "@/components/ui/Input";
import { categoricalAccent } from "@/lib/categorical";

import { type PickerLeadGroup, ProjectPicker } from "../../projects/components/ProjectPicker";
import type { ProjectNode } from "../../projects/lib/tree";
import type { LensArea } from "../lib/api";
import { whereGroups } from "../lib/clarify";
import type { MyTasksProject } from "../lib/types";

/** The "leave it loose" row's value. A node id is a UUID, so it cannot collide. */
const NO_PROJECT = "__no_project__";
/** One empty tree, so a host with none does not re-seed the picker each render. */
const NO_ROOTS: readonly ProjectNode[] = [];

/**
 * The Clarify "Where" picker under the lens (WS-39 S6b).
 *
 * Two groups, in this order: **My Areas** (private, mine, flat by D65) and
 * **Company projects** (the boards `GET /projects/nodes` serves). The split
 * is the point — see `whereGroups` — so the same list is never drawn flat.
 *
 * Since 2026-10-10 it is drawn by `ProjectPicker`, the Move dialog's own list:
 * a search box, then the Areas, then the company tree with its spaces closed.
 * The Areas and "No project" ride above the tree as flat groups, and the
 * search filters them too.
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
  roots,
  query,
  autoFocus = false,
  onEscape,
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
  /** The flat company list, drawn only when there is no tree to draw. */
  projects: MyTasksProject[];
  /**
   * The company TREE, the Move dialog's own. When given, the company group
   * draws it: spaces closed, folders as headers that open.
   */
  roots?: readonly ProjectNode[];
  /** The capture box's `#` fragment. The picker then draws no search box. */
  query?: string | null;
  /** Focus the search box on mount, for a list that opens on demand. */
  autoFocus?: boolean;
  /** Escape in the search box, for the capture chip's popover. */
  onEscape?: () => void;
  value?: string;
  suggestedId?: string;
  onChange: (id: string | undefined) => void;
  /** Resolves with the new Area, or `undefined` when the server refused. */
  onCreateArea: (name: string) => Promise<LensArea | undefined>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const hasTree = (roots?.length ?? 0) > 0;
  // The tree replaces the flat list when there is one, as `whereGroups` says.
  const groups = whereGroups({ areas, projects: hasTree ? [] : projects, includeAreas });
  const flatProjects = groups.find((g) => g.kind === "project")?.rows ?? [];

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

  const newArea = !allowCreateArea ? null : creating ? (
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
          // The picker's own keys (Enter picks the cursor row) stop here.
          e.stopPropagation();
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
      <Button type="button" size="sm" loading={busy} disabled={!name.trim()} onClick={() => void submit()}>
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
      className="tech-transition flex items-center gap-1.5 px-2 py-1 text-left text-xs text-primary hover:underline"
    >
      <Icon name="Plus" className="h-3 w-3" /> New Area…
    </button>
  );

  const lead: PickerLeadGroup[] = [];
  if (includeNoProject) {
    lead.push({
      key: "none",
      rows: [
        {
          value: NO_PROJECT,
          label: noProjectLabel,
          icon: <Icon name="Inbox" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />,
          muted: true,
        },
      ],
    });
  }
  for (const group of groups) {
    // An empty flat company group says so through `emptyTree` below.
    if (group.kind === "project" && group.rows.length === 0) continue;
    lead.push({
      key: group.kind,
      label: group.label,
      rows: group.rows.map((row) => ({
        value: row.id,
        label: row.name,
        icon:
          row.kind === "area" ? (
            <span
              aria-hidden
              className={`h-2 w-2 shrink-0 rounded-full ${categoricalAccent(row.name).dot}`}
            />
          ) : (
            <Icon name="FolderKanban" className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ),
      })),
      footer: group.kind === "area" ? newArea : undefined,
    });
  }

  return (
    <div className="rounded-md border border-border bg-background/40 p-1.5">
      <ProjectPicker
        roots={roots ?? NO_ROOTS}
        lead={lead}
        treeLabel={hasTree ? "Company projects" : undefined}
        emptyTree={
          hasTree || flatProjects.length > 0 ? null : "No company projects you can file into."
        }
        value={value === undefined ? NO_PROJECT : value}
        suggestedId={suggestedId}
        query={query}
        autoFocus={autoFocus}
        onEscape={onEscape}
        label="Where"
        onPick={(picked) => onChange(picked === NO_PROJECT ? undefined : picked)}
      />
    </div>
  );
}
