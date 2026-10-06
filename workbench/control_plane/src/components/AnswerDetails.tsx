"use client";

/**
 * The details menu of one assistant answer (WS-45 S3, D90 §7.2, Q4).
 *
 * It names the tiers that served the answer, for example "Balanced, then
 * Powerful", for every member. The words come from `lib/tierRouting.ts`, and
 * they are tier labels, never a model (D32.7).
 *
 * `MessageBubble` draws it only when the UI flag is on and the answer holds a
 * tier. So with the flag off the action row is as it was.
 *
 * The panel is portalled through `AnchoredPanel`, because the Projects and
 * Email rails clip an absolute panel. A click outside, Escape or the trigger
 * again closes it, through `lib/outsideClick.ts`, the one dismiss rule.
 */

import { useEffect, useRef, useState } from "react";

import Icon from "@/components/Icon";
import AnchoredPanel from "@/components/ui/AnchoredPanel";
import Button from "@/components/ui/Button";
import { domClickWalk, shouldDismiss } from "@/lib/outsideClick";

/** What the menu shows. Its own component, so a node-env test can draw it. */
export function AnswerDetailsBody({ tierLabel }: { tierLabel: string }) {
  return (
    <>
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Answer details
      </p>
      <div className="flex items-baseline gap-3 text-xs">
        <span className="shrink-0 text-muted-foreground">Tier</span>
        <span className="min-w-0 text-foreground" data-tier-label="">
          {tierLabel}
        </span>
      </div>
    </>
  );
}

export default function AnswerDetails({ tierLabel }: { tierLabel: string }) {
  const [open, setOpen] = useState(false);
  // The wrapper is the anchor: `Button` does not forward a ref.
  const [anchor, setAnchor] = useState<HTMLSpanElement | null>(null);
  const rootRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (shouldDismiss(e.target as Element | null, domClickWalk(rootRef.current))) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span
      ref={(el) => {
        rootRef.current = el;
        setAnchor(el);
      }}
      className="relative inline-flex"
    >
      <Button
        variant="ghost"
        size="none"
        layout="inline-flex items-center"
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Answer details"
        title="Answer details"
        className="gap-0.5 px-1 py-0.5 text-[10px]"
      >
        <Icon name="Info" size={11} />
        Details
      </Button>
      <AnchoredPanel
        anchor={anchor}
        open={open}
        maxHeight={160}
        className="w-56 max-w-[calc(100vw-1.5rem)] p-3"
        panelProps={{ role: "dialog", "aria-label": "Answer details" }}
      >
        <AnswerDetailsBody tierLabel={tierLabel} />
      </AnchoredPanel>
    </span>
  );
}
