"use client";

/**
 * A BUTTON that opens a list of options — the filter row's dropdown.
 *
 * Owner direction, 2026-08-26 (H-94): *"Every dropdown in the row becomes a
 * button. At the default value, draw a two-headed arrow in place of the single
 * down arrow."* A native `<select>` reads as a field waiting for input. These
 * are not fields — they are a current state you can change, which is what a
 * button says.
 *
 * ⚠️ **The two-headed arrow is the whole point of the icon, and it is not
 * decoration.** A row of four controls all showing a down chevron says nothing
 * about which of them are doing anything. `ChevronsUpDown` at the default and
 * `ChevronDown` off it means the row answers *"what have I changed?"* from the
 * glyphs alone, before the colour is read — which matters because the active
 * tint is the only other cue and colour is the one people cannot all see.
 *
 * ⚠️ **NO MOTION.** Owner directive 2026-08-26, restated in H-94: a `MOTION.md`
 * landed in this tree the same day and the owner removed it. The panel appears
 * and disappears as a state change. Do not add a duration, a transition or an
 * easing curve here, and do not re-open the question without asking.
 *
 * ⚠️ **Not built on `@base-ui/react`, and that is the rule rather than a
 * shortcut.** `AGENTS.md` rule 8 / D-PM-15 make `src/components/ui/Modal.tsx`
 * the ONE file that may import the substrate. H-94 names the sanctioned answer
 * for a popover outside it: `src/lib/outsideClick.ts`. So this uses that, and
 * it does not hand-roll a second containment check — the walker is what makes
 * a portalled child not count as "outside".
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import Icon from "@/components/Icon";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

/**
 * Which arrow the trigger wears — the one decision in this file worth a test.
 *
 * ⚠️ **Two heads at the default, one off it** (owner, 2026-08-26). A row of
 * four controls all showing a down chevron says nothing about which of them
 * are doing anything. This is the only cue besides the active tint, and it is
 * the one that survives when colour does not — so it is extracted rather than
 * left inline, and `SelectButton.test.ts` pins it.
 *
 * Kept as a pure function deliberately: `vitest.config.ts` is
 * `environment: "node"`, so a JSX assertion is not available here and a
 * source-text fence would be the third one this repo has watched pass on a
 * broken value.
 */
export function arrowFor(value: string, defaultValue: string): string {
  return value === defaultValue ? "ChevronsUpDown" : "ChevronDown";
}

export interface SelectOption {
  value: string;
  label: string;
  /** Drawn after the label, muted — a count, a hint, an address. */
  hint?: string;
}

export interface SelectButtonProps {
  /** What this control is, for assistive technology. */
  label: string;
  value: string;
  options: readonly SelectOption[];
  onChange: (value: string) => void;
  /**
   * The value that counts as "not filtering". Decides the arrow, and nothing
   * else — the CALLER still owns the active tint, because only it knows
   * whether a sibling flag (`unassigned`) also counts as off-default.
   */
  defaultValue?: string;
  /** The house active pair, applied by the caller. See `FilterBar`. */
  className?: string;
  /** Widest the trigger may grow. The row is `flex-wrap`; this keeps it sane. */
  widthClass?: string;
}

export function SelectButton({
  label,
  value,
  options,
  onChange,
  defaultValue = "",
  className = "",
  widthClass = "w-[9rem]",
}: SelectButtonProps) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement | null>(null);
  const listId = useId();

  const current = options.find((o) => o.value === value);

  // ⚠️ Dismiss through the shared walker, never `root.contains(target)`. A
  // control that renders elsewhere in the DOM is, by containment, OUTSIDE the
  // popover that raised it — `outsideClick.ts`'s header carries the case.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(root.current))) {
        setOpen(false);
      }
    };
    // ⚠️ Escape closes, and focus returns to the trigger. Without the return
    // the next Tab starts from the top of the document, which on this row
    // means walking the whole filter bar again.
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        root.current?.querySelector("button")?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = useCallback(
    (next: string) => {
      onChange(next);
      setOpen(false);
      root.current?.querySelector("button")?.focus();
    },
    [onChange]
  );

  return (
    <div ref={root} className={`relative ${widthClass}`}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((was) => !was)}
        className={`cc-control flex h-7 w-full items-center gap-1 rounded-md border border-border bg-card px-2 text-left text-xs hover:bg-muted ${className}`}
      >
        <span className="min-w-0 flex-1 truncate pr-px">
          {current?.label ?? label}
        </span>
        {/* ⚠️ Two heads at the default, one off it. See the header. */}
        <Icon
          name={arrowFor(value, defaultValue)}
          size={13}
          className="shrink-0 opacity-70"
        />
      </button>

      {open ? (
        <div
          id={listId}
          role="listbox"
          aria-label={label}
          // No transition. See the header — this is a directive, not a default.
          className="absolute left-0 top-[calc(100%+2px)] z-30 max-h-64 w-max min-w-full overflow-y-auto rounded-md border border-border bg-card p-1 shadow-md"
        >
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              onClick={() => pick(option.value)}
              className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-muted ${
                option.value === value ? "bg-muted font-medium" : ""
              }`}
            >
              <span className="min-w-0 flex-1 truncate pr-px">{option.label}</span>
              {option.hint ? (
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {option.hint}
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default SelectButton;
