"use client";

/**
 * A small popover that a block of the Knowledge editor opens from itself
 * (WS-61 KB-2p): the status popover and the date picker.
 *
 * It draws through `AnchoredPanel`, the shared popover, so a scroll box
 * never clips it. A press outside closes it through `lib/outsideClick.ts`,
 * and Escape closes it and gives the focus back to the editor. Escape is
 * heard in the capture phase and stopped there, so it never also closes a
 * dialog behind the page.
 */
import { useEffect } from "react";

import AnchoredPanel from "@/components/ui/AnchoredPanel";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

export default function EditorPopover({
  anchor,
  open,
  onClose,
  label,
  children,
}: {
  /** The block's own element. A press on it does not count as outside. */
  anchor: HTMLElement | null;
  open: boolean;
  onClose: () => void;
  /** The accessible name of the popover. */
  label: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (shouldDismiss(event.target as Element | null, domClickWalk(anchor))) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey, true);
    };
  }, [open, anchor, onClose]);

  return (
    <AnchoredPanel
      anchor={anchor}
      open={open}
      maxHeight={320}
      className="w-60 p-2"
      panelProps={{ role: "dialog", "aria-label": label }}
    >
      {children}
    </AnchoredPanel>
  );
}
