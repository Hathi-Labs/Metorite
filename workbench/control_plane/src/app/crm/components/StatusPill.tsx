"use client";

/**
 * The status pill — a lane badge that is also the way to move a record.
 *
 * One control, because a read-only badge next to a separate "change stage"
 * menu is two places to look for the same fact. Moving from here issues the
 * same PATCH the kanban drag does (lib/board.ts::moveRequest).
 *
 * The menu is an `AnchoredPanel` (CRM-U2), the shared popover of
 * `SelectButton` and `StatusMenu`. It is portalled, so the record sheet's
 * scroll does not clip it, and it paints above the sheet's `Modal`. A click
 * outside closes it through `lib/outsideClick.ts`, never a full-screen scrim.
 * Escape closes the menu only, and focus goes back to the pill.
 *
 * Not `StatusMenu`: that menu groups a task's statuses under the task stages.
 * A CRM status has a type (open, won, lost) and a probability instead.
 */

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { stageAccent } from "../lib/board";
import type { Status } from "../lib/types";

export default function StatusPill({
  status,
  statuses,
  onChange,
  disabled = false,
}: {
  status: Status | null;
  statuses: Status[];
  onChange?: (statusId: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  // State, not a ref, so the panel measures again when the trigger mounts.
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const menuId = useId();
  const sorted = useMemo(
    () => [...statuses].sort((a, b) => a.position - b.position),
    [statuses],
  );
  // The option the keyboard is on. Focus stays on the pill: the menu is
  // portalled outside the record sheet's focus trap, so moving DOM focus
  // into it would be pulled back. `aria-activedescendant` names the option
  // instead, which works inside any trap.
  const [active, setActive] = useState(0);
  const optionId = (index: number) => `${menuId}-option-${index}`;

  function toggle() {
    if (!open) {
      // The keyboard starts on the current status, set once when the menu
      // opens so it does not jump while the menu is up.
      const current = sorted.findIndex((option) => option.id === status?.id);
      setActive(current >= 0 ? current : 0);
    }
    setOpen((value) => !value);
  }

  const choose = useCallback(
    (option: Status) => {
      setOpen(false);
      root.current?.querySelector("button")?.focus();
      if (option.id !== status?.id) onChange?.(option.id);
    },
    [status?.id, onChange],
  );

  useEffect(() => {
    if (!open) return;
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(root.current))) {
        setOpen(false);
      }
    };
    // In the CAPTURE phase on `window`, and stopped there. The pill sits in
    // the record sheet, and the sheet's `Modal` hears Escape on `document`.
    // Without the stop, one Escape closed the menu AND the record. The arrow
    // keys, Home, End and Enter are stopped for the same reason.
    const onKey = (event: KeyboardEvent) => {
      const count = sorted.length;
      let handled = true;
      if (event.key === "Escape") {
        setOpen(false);
        root.current?.querySelector("button")?.focus();
      } else if (event.key === "ArrowDown") {
        setActive((index) => (index + 1) % count);
      } else if (event.key === "ArrowUp") {
        setActive((index) => (index - 1 + count) % count);
      } else if (event.key === "Home") {
        setActive(0);
      } else if (event.key === "End") {
        setActive(count - 1);
      } else if (event.key === "Enter" || event.key === " ") {
        const option = sorted[active];
        if (option) choose(option);
      } else {
        handled = false;
      }
      if (!handled) return;
      event.stopPropagation();
      event.preventDefault();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open, sorted, active, choose]);

  const readOnly = disabled || !onChange || statuses.length === 0;

  const label = status?.name ?? "No status";
  // The pill does not know the mode, so it reads the stored colour (CRM-C2).
  const tone = status ? stageAccent(status, null).dot : "bg-muted-foreground";

  if (readOnly) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-foreground">
        <span className={`w-1.5 h-1.5 rounded-full ${tone}`} />
        {label}
      </span>
    );
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={setTrigger}
        type="button"
        // The ARIA 1.2 select-only combobox: focus stays here and
        // `aria-activedescendant` names the option the keyboard is on.
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-activedescendant={open ? optionId(active) : undefined}
        onClick={toggle}
        className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary/40 px-2.5 py-1 text-xs text-foreground hover:border-primary/40 tech-transition"
      >
        <span className={`w-1.5 h-1.5 rounded-full ${tone}`} />
        {label}
        <Icon name="ChevronDown" className="w-3 h-3 opacity-60" />
      </button>
      <AnchoredPanel
        anchor={trigger}
        open={open}
        className="w-52 p-1"
        panelProps={{ id: menuId, role: "listbox", "aria-label": "Status" }}
      >
        {sorted.map((option, index) => (
            <button
              key={option.id}
              id={optionId(index)}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={option.id === status?.id}
              onClick={() => choose(option)}
              onMouseEnter={() => setActive(index)}
              className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs tech-transition ${
                option.id === status?.id
                  ? "bg-primary/10 text-foreground"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground"
              } ${index === active ? "ring-1 ring-primary/40" : ""}`}
            >
              <span
                className={`w-1.5 h-1.5 rounded-full ${stageAccent(option, null).dot}`}
              />
              <span className="flex-1 truncate">{option.name}</span>
              {/* The stage's default probability beside its name: moving a
                  deal here is what makes it inherit that number, and the
                  forecast changes the moment it does (§5.1). */}
              {option.probability !== null &&
              option.probability !== undefined ? (
                <span className="text-[10px] tabular-nums opacity-60">
                  {option.probability}%
                </span>
              ) : null}
              {option.type === "won" || option.type === "lost" ? (
                <span className="text-[10px] uppercase opacity-60">
                  {option.type}
                </span>
              ) : null}
            </button>
          ))}
      </AnchoredPanel>
    </div>
  );
}
