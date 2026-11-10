"use client";

/**
 * Projects · a button that opens its content as a bottom sheet on a phone and
 * as an anchored popover on a desktop (WS-64 OP-1, `one_projects_app.md` §5.3).
 *
 * Both surfaces are the house primitives: `Modal placement="sheet"` and
 * `AnchoredPanel`. This file owns only the choice between them, so the Filters
 * and View controls are one component on both screens and not two.
 */

import { useRef, useState, type ReactNode } from "react";

import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { OFF_DEFAULT } from "@/components/ui/SelectButton";
import { useDismiss } from "@/hooks/useDismiss";

export function SheetButton({
  label,
  title,
  icon,
  phone,
  highlighted = false,
  ariaLabel,
  children,
}: {
  /** The visible words of the button, and the title of the sheet. */
  label: string;
  /** The title of the sheet when it differs from the button's words. */
  title?: string;
  icon: string;
  /** True on a phone: a bottom sheet. False: a popover under the button. */
  phone: boolean;
  /** The button wears the active pair while something it holds is on. */
  highlighted?: boolean;
  ariaLabel?: string;
  /** The content, given a function that closes the sheet. */
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const root = useRef<HTMLSpanElement | null>(null);
  const close = () => setOpen(false);
  // The Modal dismisses itself. The popover needs the outside-click walk.
  useDismiss(open && !phone, close, root);

  return (
    <span ref={root} className="inline-flex shrink-0">
      <Button
        variant="secondary"
        size="sm"
        icon={icon}
        className={highlighted ? OFF_DEFAULT : undefined}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={(event) => {
          setAnchor(event.currentTarget);
          setOpen((now) => !now);
        }}
      >
        {label}
      </Button>
      {phone ? (
        <Modal
          open={open}
          onClose={close}
          title={title ?? label}
          placement="sheet"
          size="md"
          className="max-h-[85vh]"
        >
          <div className="min-h-0 overflow-y-auto p-3">{children(close)}</div>
        </Modal>
      ) : (
        <AnchoredPanel
          anchor={anchor}
          open={open}
          align="end"
          maxHeight={560}
          className="w-80 max-h-[560px]"
          panelProps={{ role: "dialog", "aria-label": title ?? label }}
        >
          <div className="p-3">{children(close)}</div>
        </AnchoredPanel>
      )}
    </span>
  );
}

export default SheetButton;
