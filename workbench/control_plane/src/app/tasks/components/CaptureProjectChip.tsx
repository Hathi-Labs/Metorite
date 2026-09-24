"use client";

/**
 * My Tasks · capture straight onto a project (my_tasks_cutover.md §5 S6g).
 *
 * The chip at the right of the Inbox capture box. It reads "Inbox ▾" until the
 * member picks a destination, and then names it. Typing `#` at the start of a
 * word opens the same picker, filtered by what follows the `#`.
 *
 * The list is the Move dialog's own (`destinations`, the company tree, folders
 * shown and disabled), with my Areas first, because an Area stays private. It
 * is drawn by `WherePicker`, the list Clarify's Where step draws, so the three
 * doors show one list.
 */

import { useEffect, useMemo, useRef } from "react";

import Icon from "@/components/Icon";

import type { LensArea } from "../lib/api";
import type { DestinationRow } from "../lib/companyTree";
import type { CaptureDestination } from "../lib/quickAdd";
import type { MyTasksProject } from "../lib/types";
import { WherePicker } from "./WherePicker";

export function CaptureProjectChip({
  value,
  onChange,
  open,
  onOpenChange,
  query,
  areas,
  tree,
  projects,
  onCreateArea,
}: {
  value: CaptureDestination | null;
  onChange: (dest: CaptureDestination | null) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The `#` fragment being typed, or null when the chip opened the picker. */
  query: string | null;
  areas: LensArea[];
  tree: readonly DestinationRow[];
  projects: MyTasksProject[];
  onCreateArea: (name: string) => Promise<LensArea | undefined>;
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on a click anywhere else. The input keeps focus while `#` is typed,
  // so this listens for pointer events, not blur.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) onOpenChange(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open, onOpenChange]);

  const q = (query ?? "").trim().toLowerCase();
  const shownAreas = useMemo(
    () => (q ? areas.filter((a) => a.name.toLowerCase().includes(q)) : areas),
    [areas, q],
  );
  // Filtered, the tree loses its parents, so a match is drawn flat.
  const shownTree = useMemo(
    () =>
      q
        ? tree
            .filter((r) => r.legal && r.node.name.toLowerCase().includes(q))
            .map((r) => ({ ...r, depth: 0 }))
        : tree,
    [tree, q],
  );
  const shownProjects = useMemo(
    () => (q ? projects.filter((p) => p.outcome.toLowerCase().includes(q)) : projects),
    [projects, q],
  );

  const pick = (id: string | undefined) => {
    if (!id) {
      onChange(null);
      onOpenChange(false);
      return;
    }
    const area = areas.find((a) => a.id === id);
    const row = tree.find((r) => r.node.id === id);
    const flat = projects.find((p) => p.id === id);
    const dest: CaptureDestination | null = area
      ? { id, name: area.name, kind: "area" }
      : row
        ? { id, name: row.node.name, kind: "project" }
        : flat
          ? { id, name: flat.outcome, kind: "project" }
          : null;
    onChange(dest);
    onOpenChange(false);
  };

  const label = value ? value.name : "Inbox";
  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={
          value
            ? `Captures go to ${value.name}. Click to change. Type # in the box to pick one.`
            : "Captures go to your Inbox. Pick a project or an Area, or type # in the box."
        }
        className={[
          "tech-transition inline-flex max-w-[12rem] items-center gap-1 rounded-md border px-2 py-1 text-xs",
          value
            ? "border-primary/40 bg-primary/10 text-primary"
            : "border-border text-muted-foreground hover:border-primary/40 hover:text-foreground",
        ].join(" ")}
      >
        <Icon
          name={value ? (value.kind === "area" ? "Lock" : "FolderKanban") : "Inbox"}
          className="h-3.5 w-3.5 shrink-0"
        />
        {/* Collapses to the icon on a narrow screen. */}
        <span className="hidden truncate md:inline">{label}</span>
        <Icon name="ChevronDown" className="h-3 w-3 shrink-0" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label="Capture to"
          className="absolute right-0 top-full z-50 mt-1 max-h-[60vh] w-72 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg border border-border bg-popover p-1 shadow-xl"
        >
          <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            {q ? `Capture to “${query}”` : "Capture to"}
          </p>
          <WherePicker
            areas={shownAreas}
            projects={shownProjects}
            tree={shownTree}
            value={value?.id}
            noProjectLabel="Inbox"
            onChange={pick}
            onCreateArea={onCreateArea}
          />
        </div>
      )}
    </div>
  );
}
