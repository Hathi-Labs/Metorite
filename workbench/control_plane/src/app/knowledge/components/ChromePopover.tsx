"use client";

/**
 * A small popover of the page chrome (WS-61 KB-2d): the folded breadcrumbs
 * and the contributors of the byline.
 *
 * It hangs from its trigger through `AnchoredPanel`, so nothing clips it.
 * A press outside closes it through the shared walker (`lib/outsideClick.ts`),
 * Escape closes it, and the focus goes back to the trigger. The caller owns
 * the trigger and the open state.
 */

import { useEffect, type ReactNode } from "react";

import { AnchoredPanel } from "@/components/ui/AnchoredPanel";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

export default function ChromePopover({
  trigger,
  open,
  onClose,
  label,
  id,
  align = "start",
  children,
}: {
  /** The button that opens the popover. */
  trigger: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  /** The accessible name of the panel. */
  label: string;
  id?: string;
  align?: "start" | "end";
  children: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(trigger))) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      onClose();
      trigger?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, trigger]);

  return (
    <AnchoredPanel
      anchor={trigger}
      open={open}
      align={align}
      maxHeight={320}
      className="w-64 max-w-[calc(100vw-1rem)] p-1"
      panelProps={{ id, role: "dialog", "aria-label": label }}
    >
      {children}
    </AnchoredPanel>
  );
}
