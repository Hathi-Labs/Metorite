"use client";

/**
 * A popover panel that hangs off a trigger and is clipped by nothing.
 *
 * ## Why this exists
 *
 * An absolutely positioned panel is clipped by the nearest ancestor that is
 * not `overflow: visible`. Three surfaces in this app put a picker inside
 * exactly such a box:
 *
 * * `Modal.tsx`'s body is `overflow-hidden` — the Move dialog's project
 *   picker drew SIX options in the DOM and showed two.
 * * The task panel's body scrolls — the tag picker's list spanned y486–615
 *   inside a container ending at y552, so 63px of it, including the
 *   "Create" row, was simply not there.
 * * The board's columns scroll, and a table's body scrolls.
 *
 * Every one of those reads as "the dropdown does not work", and every one is
 * invisible to a test that asks whether the options are in the DOM — they
 * all are. Both were reported by the owner rather than caught, on
 * 2026-09-20.
 *
 * A native `<select>` never had this problem, because the browser draws its
 * list outside the page entirely. That is the one thing the platform widget
 * was better at, and this is the price of replacing it.
 *
 * ## What it does
 *
 * Renders `children` into `document.body` at `position: fixed`, measured
 * from `anchor`, flipping above when there is no room below, and
 * re-measuring on scroll and resize. Scroll is watched in the CAPTURE phase
 * because the element that scrolls — a dialog body, a panel, a column — is
 * not an ancestor of the portalled node and its events never bubble to
 * `window`.
 *
 * ⚠️ **It carries `PREVENT_OUTSIDE_CLICK`, and that is not optional.** A
 * portalled child is, by containment, OUTSIDE the popover that raised it, so
 * a dismiss-on-outside-click handler walking up from a clicked option would
 * never meet the trigger and would close the panel before the click landed.
 * `lib/outsideClick.ts` defines the marker for exactly this case and said so
 * for a month before anything used it.
 *
 * ⚠️ **Re-measuring, not closing, on scroll.** These live inside dialogs and
 * panels whose bodies scroll. A list that vanishes when the member nudges
 * the wheel reads as a crash.
 */

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";

import { PREVENT_OUTSIDE_CLICK } from "@/lib/outsideClick";

export interface AnchoredPanelProps {
  /** The element the panel hangs from. */
  anchor: HTMLElement | null;
  open: boolean;
  children: React.ReactNode;
  /**
   * How tall the panel may get, in px. Used to decide whether it flips above
   * the anchor, so it should match the panel's own `max-height`.
   */
  maxHeight?: number;
  className?: string;
  /** Forwarded to the portalled element — a `role`, an `id`, a label. */
  panelProps?: React.HTMLAttributes<HTMLDivElement> & Record<string, unknown>;
  /**
   * Which edge of the anchor the panel lines up with. `start` (the default)
   * hangs it from the anchor's left edge. `end` hangs it from the right edge,
   * for a trigger near the right of the screen, where a wider panel hung from
   * the left edge runs off the window (the S6g capture chip at 768px).
   */
  align?: "start" | "end";
}

export function AnchoredPanel({
  anchor,
  open,
  children,
  maxHeight = 256,
  className = "",
  panelProps,
  align = "start",
}: AnchoredPanelProps) {
  const [box, setBox] = useState<{
    left: number;
    right: number;
    top: number;
    width: number;
  } | null>(null);

  const place = useCallback(() => {
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const below = window.innerHeight - rect.bottom;
    const up = below < maxHeight && rect.top > below;
    setBox({
      left: rect.left,
      right: window.innerWidth - rect.right,
      // Never off the top either: a flipped panel taller than the space
      // above it is the same defect upside down.
      top: up ? Math.max(8, rect.top - maxHeight - 2) : rect.bottom + 2,
      width: rect.width,
    });
  }, [anchor, maxHeight]);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, place]);

  // `open && box` rather than `open`: rendering before the first measurement
  // would flash the panel at the top-left corner of the window.
  useEffect(() => {
    if (!open) setBox(null);
  }, [open]);

  if (!open || !box) return null;

  return createPortal(
    <div
      {...panelProps}
      {...{ [PREVENT_OUTSIDE_CLICK]: "" }}
      style={{
        ...(align === "end" ? { right: box.right } : { left: box.left }),
        top: box.top,
        minWidth: box.width,
        maxHeight,
      }}
      className={`fixed z-[60] overflow-y-auto rounded-md border border-border bg-card shadow-md ${className}`}
    >
      {children}
    </div>,
    document.body,
  );
}

export default AnchoredPanel;
