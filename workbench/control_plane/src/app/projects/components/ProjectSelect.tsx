"use client";

/**
 * A dropdown that picks a project — `ProjectPicker` in a popover.
 *
 * Owner ask, 2026-10-10, on the report builder's "Which work?": *"Include
 * accordions in the reports, as decided for other sections, wherever all
 * projects are listed … Apply the same approach to any other dropdowns that
 * display all projects elsewhere in the application."*
 *
 * Closed, it is a `SelectButton`: the same trigger, the same two-headed arrow
 * at the default, so a filter row of dropdowns still reads as one row. Open,
 * it is the picker the Move dialogs draw — a search box over a tree whose
 * spaces start closed, a path on every search result, and the keyboard of a
 * tree. One picker, so a member learns it once.
 *
 * ## Why a popover here and inline in the Move dialogs
 *
 * A portalled panel falls outside a Modal's focus trap, and a search box that
 * loses the caret to the trap does not work. So the Move dialogs draw the
 * picker inline (`MoveTasksDialog`). One host here does sit in a `Modal`:
 * "Copy statuses from" when the Statuses screen opens from a row menu
 * (`ManagerFrame`). Traced through Base UI for #866: its outside press
 * ignores a panel added later, and focusout does not pull focus back. Look at
 * it again before adding another host in a dialog.
 *
 * Dismissal is `SelectButton`'s: the shared outside-click walker, Escape
 * returning focus to the trigger (`ProjectPicker`'s `onEscape`).
 */

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import { arrowFor } from "@/components/ui/SelectButton";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

import { type PickRule, pathLabel, pickerNodes } from "../lib/pickerTree";
import type { ProjectNode } from "../lib/tree";
import { type PickerLeadGroup, ProjectPicker } from "./ProjectPicker";

export interface ProjectSelectProps {
  /** What this control is, for assistive technology. */
  label: string;
  roots: readonly ProjectNode[];
  /** A node id, a lead row's value, or "" for nothing chosen. */
  value: string;
  onChange: (value: string) => void;
  /** Why a node cannot be picked. Default: every node can. */
  rule?: PickRule;
  /** Flat rows above the tree — "Whole organization". */
  lead?: readonly PickerLeadGroup[];
  /** The trigger's text when nothing is chosen. */
  prompt?: string;
  /** The value that counts as "not filtering", for the arrow. */
  defaultValue?: string;
  widthClass?: string;
  className?: string;
  disabled?: boolean;
}

export function ProjectSelect({
  label,
  roots,
  value,
  onChange,
  rule,
  lead = [],
  prompt,
  defaultValue = "",
  widthClass = "w-[9rem]",
  className = "",
  disabled = false,
}: ProjectSelectProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  // State, not a ref, so the panel re-measures once the trigger mounts.
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);

  const leadRow = lead.flatMap((g) => g.rows).find((r) => r.value === value);
  const node = leadRow ? null : pickerNodes(roots).find((n) => n.id === value) ?? null;
  const shown = leadRow?.label ?? node?.name ?? null;

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(root.current))) setOpen(false);
    };
    // Escape from anywhere in the panel, not only the search box: a click on
    // a chevron or a row moves focus off it (review of #866). As SelectButton.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      root.current?.querySelector("button")?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const close = () => {
    setOpen(false);
    trigger?.focus();
  };

  return (
    <div ref={root} className={`relative ${widthClass}`}>
      <button
        ref={setTrigger}
        type="button"
        aria-label={shown ? `${label}: ${shown}` : label}
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        // The path, so a project named like a sibling elsewhere says where.
        title={node && node.path.length ? pathLabel([...node.path, node.name]) : undefined}
        onClick={() => setOpen((was) => !was)}
        className={`cc-control flex h-7 w-full items-center gap-1 rounded-md border border-border bg-card px-2 text-left text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60 ${className}`}
      >
        <span className="min-w-0 flex-1 truncate pr-px">
          {shown ?? <span className="italic text-muted-foreground">{prompt ?? label}</span>}
        </span>
        <Icon name={arrowFor(value, defaultValue)} size={13} className="shrink-0 opacity-70" />
      </button>

      <AnchoredPanel
        anchor={trigger}
        open={open}
        maxHeight={440}
        className="w-80 max-w-[calc(100vw-2rem)] p-1.5"
        panelProps={{ role: "dialog", "aria-label": label }}
      >
        <ProjectPicker
          roots={roots}
          rule={rule ?? ANY_NODE}
          lead={lead}
          // As it is: `""` is a real value ("Whole organization").
          value={value}
          autoFocus
          label={label}
          onEscape={close}
          onPick={(next) => {
            onChange(next);
            close();
          }}
        />
      </AnchoredPanel>
    </div>
  );
}

/** Every node may be picked: a scope can be a space, a folder or a project. */
const ANY_NODE: PickRule = () => null;

export default ProjectSelect;
