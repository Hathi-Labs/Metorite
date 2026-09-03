"use client";

/**
 * Checkbox — the house tick, on the house tokens.
 *
 * ## Why this exists
 *
 * `src/components/ui/` held Badge, Button, Input, Modal, Skeleton and Toast,
 * and no Checkbox. So all 37 checkboxes in the app were a raw
 * `<input type="checkbox">`, which takes its paint from the PLATFORM and not
 * from the design system.
 *
 * That is invisible in dark mode, where the native control recedes into a
 * faint outline and looks deliberate. In light mode the same element is a
 * solid black square, and a board of twelve cards is twelve black squares
 * outweighing the task titles beside them. Measured 2026-09-03 with the
 * visual-review rig; `DESIGN_SYSTEM.md` §3 already said it — the primitives,
 * not a class string.
 *
 *     <Checkbox checked={picked} onChange={…} aria-label="Select task" />
 *     <Checkbox checked={all} indeterminate={some} onChange={…} />
 *
 * ## How it is drawn
 *
 * A real `<input type="checkbox">` stays underneath, so the keyboard, the
 * form, the label association and the accessibility tree all keep working.
 * `appearance-none` removes the platform paint, and the box is drawn with the
 * same tokens every other control uses. This is the cheapest correct shape: a
 * hand-rolled `div` with `role="checkbox"` would owe us focus, space-to-toggle
 * and label clicks, and would get one of them wrong.
 *
 * ⚠️ **`--primary` is right here, and it is not right on a status.** A checked
 * box is a SELECTION, which is what the member's accent means. A lane colour
 * is a fact about the work, which is why `statusAccent.ts` uses `--info` and
 * `--success` instead.
 */

import { useEffect, useRef } from "react";

export type CheckboxSize = "sm" | "md";

const SIZES: Record<CheckboxSize, string> = {
  // Sized in `rem` rather than `px`, so both follow the member's density.
  // A box pinned in px stops matching the text beside it at compact.
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
};

const BASE =
  "appearance-none shrink-0 cursor-pointer rounded border border-border bg-background " +
  "outline-none transition-none " +
  // Checked and indeterminate share one look: the accent fill. The tick and
  // the dash are drawn by `.cc-checkbox::after` in globals.css, masked so they
  // take `--primary-foreground` — the ink that pairs with this fill.
  "checked:border-primary checked:bg-primary " +
  "indeterminate:border-primary indeterminate:bg-primary " +
  "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 " +
  "focus-visible:ring-offset-background " +
  "disabled:cursor-not-allowed disabled:opacity-60";

export type CheckboxProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "type" | "size" | "className"
> & {
  size?: CheckboxSize;
  /**
   * Some children are picked and some are not — the parent's box shows a dash.
   * This is a DOM property and not an attribute, so React cannot set it from
   * JSX; the effect below is the only way to reach it.
   */
  indeterminate?: boolean;
  className?: string;
  ref?: React.Ref<HTMLInputElement>;
};

export function Checkbox({
  size = "md",
  indeterminate = false,
  className = "",
  ref,
  ...rest
}: CheckboxProps) {
  const own = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (own.current) own.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      {...rest}
      type="checkbox"
      ref={(node) => {
        own.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) (ref as React.MutableRefObject<HTMLInputElement | null>).current = node;
      }}
      className={`cc-checkbox ${BASE} ${SIZES[size]} ${className}`}
    />
  );
}

export default Checkbox;
