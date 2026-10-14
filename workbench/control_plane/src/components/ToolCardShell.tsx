"use client";

import Button from "@/components/ui/Button";
import {
  CARD_FRAME_CLASS,
  CollapsibleCard,
  CollapsibleCardBody,
  CollapsibleCardHeader,
} from "@/components/ui/Collapsible";
import Icon from "@/components/Icon";
import { createContext, useContext, useState } from "react";

/**
 * True inside a step of the working trail (`ThinkingContainer`). A read's
 * card draws there since the placement rule of 2026-10-08
 * (`lib/chatPlacement.ts`), and the step is the thing a member closes. So a
 * card in a step draws no dismiss control: its X would hide nothing.
 */
export const InStepContext = createContext(false);

/**
 * Shared chrome for an AG-UI tool card: a title row with a fold toggle, an
 * optional icon and title, and a dismiss (X) control. Used to make chat tool
 * cards tidy-able — fold the ones you've seen, close the ones you're done
 * with — across the email assistant, the task manager and the Projects reads.
 *
 * ⚠️ **One toggle look (owner report, 2026-10-13).** This shell drew its own
 * header: a text button with a chevron, in its own size, beside the
 * receipts' `RollupHeader`. Now its title row IS the shared header
 * (`CollapsibleCardHeader` inside a `CollapsibleCard`), in the shared frame
 * (`CARD_FRAME_CLASS`), so a read and a receipt draw one toggle, one size and
 * one chevron. The icon is the caller's node, in the header's icon slot.
 *
 * It keeps its own open state and its own lazy body: a closed card mounts no
 * children, so an email read fetches only when it opens. It does not roll
 * up by itself (`RollupCard` does that for the flow cards).
 *
 * `onDismiss` is supplied by the caller (so a folded group can dismiss several
 * tool ids at once); omit it to render a non-dismissable shell.
 */
export function ToolCardShell({
  title,
  icon,
  defaultCollapsed = false,
  onDismiss,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  defaultCollapsed?: boolean;
  onDismiss?: () => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!defaultCollapsed);
  const [animate, setAnimate] = useState(false);
  const dismiss = useContext(InStepContext) ? undefined : onDismiss;
  return (
    <CollapsibleCard
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        setAnimate(true);
      }}
      toggle
      label={title}
      header="own"
      animate={animate}
    >
      <div data-tool-card="" className={CARD_FRAME_CLASS}>
        <div className="flex min-w-0 items-center gap-1.5">
          <div className="min-w-0 flex-1">
            <CollapsibleCardHeader lead={icon} />
          </div>
          {dismiss && (
            <Button variant="ghost" size="icon-xs" icon="X" onClick={dismiss} title="Dismiss" aria-label="Dismiss" className="shrink-0" />
          )}
        </div>
        <CollapsibleCardBody className="pt-1.5">{open ? children : null}</CollapsibleCardBody>
      </div>
    </CollapsibleCard>
  );
}

/**
 * Thin wrapper that overlays a dismiss (X) on a card that brings its own chrome
 * (draft card, rule card, …), so it becomes closable without altering the card's
 * internals. The X stays visible (faint by default, solid on hover) — a
 * hover-only control read as "no close button" on these cards.
 */
export function DismissableCard({
  onDismiss,
  children,
}: {
  onDismiss: () => void;
  children: React.ReactNode;
}) {
  if (useContext(InStepContext)) return <>{children}</>;
  return (
    <div className="relative group/dismiss">
      {/* The shared Button, as the shell's own X: the dismiss of a card that
          brings its own chrome was the one hand-made control left on it.
          ⚠️ The box places it, not the Button: `.cc-control` sets
          `position: relative`, which beats an `absolute` class on it. */}
      <div className="absolute top-1.5 right-1.5 z-10">
        <Button
          variant="ghost"
          size="none"
          onClick={onDismiss}
          title="Dismiss"
          aria-label="Dismiss"
          className="p-0.5 bg-card/80 opacity-60 hover:opacity-100"
        >
          <Icon name="X" size={12} />
        </Button>
      </div>
      {children}
    </div>
  );
}
