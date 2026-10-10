"use client";

/**
 * My Tasks · capture straight onto a project (my_tasks_cutover.md §5 S6g).
 *
 * The chip at the right of the Inbox capture box. It reads "Inbox ▾" until the
 * member picks a destination, and then names it. Typing `#` at the start of a
 * word opens the same picker, filtered by what follows the `#`.
 *
 * The list is the Move dialog's own (`ProjectPicker` over the company tree),
 * with my Areas first, because an Area stays private. It is drawn by
 * `WherePicker`, the list Clarify's Where step draws, so the three doors show
 * one list. Opened from the chip it carries its own search box. Opened by `#`
 * the capture box IS the search box, so the picker filters by the fragment.
 *
 * The list hangs from the chip through `AnchoredPanel`, the shared popover,
 * so no ancestor clips it. A click in the portalled list is not "outside",
 * because the panel carries `PREVENT_OUTSIDE_CLICK` (`shouldDismiss`).
 */

import { useEffect, useMemo, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

import type { LensArea } from "../lib/api";
import { pickerNodes } from "../../projects/lib/pickerTree";
import type { ProjectNode } from "../../projects/lib/tree";
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
  roots,
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
  roots: readonly ProjectNode[];
  projects: MyTasksProject[];
  onCreateArea: (name: string) => Promise<LensArea | undefined>;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [chip, setChip] = useState<HTMLButtonElement | null>(null);

  // Close on a click anywhere else. The input keeps focus while `#` is typed,
  // so this listens for pointer events, not blur.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (shouldDismiss(e.target as Element | null, domClickWalk(rootRef.current))) {
        onOpenChange(false);
      }
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [open, onOpenChange]);

  const nodes = useMemo(() => pickerNodes(roots), [roots]);

  const pick = (id: string | undefined) => {
    if (!id) {
      onChange(null);
      onOpenChange(false);
      return;
    }
    const area = areas.find((a) => a.id === id);
    const row = nodes.find((n) => n.id === id);
    const flat = projects.find((p) => p.id === id);
    const dest: CaptureDestination | null = area
      ? { id, name: area.name, kind: "area" }
      : row
        ? { id, name: row.name, kind: "project" }
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
        ref={setChip}
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
        <span className="hidden truncate lg:inline">{label}</span>
        <Icon name="ChevronDown" className="h-3 w-3 shrink-0" />
      </button>
      <AnchoredPanel
        anchor={chip}
        open={open}
        maxHeight={460}
        align="end"
        className="w-80 max-w-[calc(100vw-2rem)] p-1"
        panelProps={{ role: "listbox", "aria-label": "Capture to" }}
      >
          <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            {query ? `Capture to “${query}”` : "Capture to"}
          </p>
          <WherePicker
            areas={areas}
            projects={projects}
            roots={roots}
            query={query}
            autoFocus={query === null}
            onEscape={() => {
              onOpenChange(false);
              chip?.focus();
            }}
            value={value?.id}
            noProjectLabel="Inbox"
            onChange={pick}
            onCreateArea={onCreateArea}
          />
      </AnchoredPanel>
    </div>
  );
}
