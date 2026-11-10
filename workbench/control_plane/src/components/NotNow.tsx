"use client";

/**
 * NotNow — the one "Not now" control of a card that parks a run (owner
 * request, 2026-10-18). `lib/declineAnswer.ts` holds the envelope and the
 * words.
 *
 * Three parts, and every card family uses the same three, never its own:
 *
 * - {@link NotNowButton}: the quiet `Button` beside Submit.
 * - {@link NotNowClose}: the X in the card's title row, named "Not now".
 * - {@link DeclinedNote}: the muted line a declined card shows in place of
 *   its footer. It is a status, never an error.
 *
 * The question card (`ElicitationCard`), the form card and the option picker
 * (`genUITemplates.tsx`) draw them. The plan card keeps its Cancel, which
 * sends the same decline.
 *
 * Fence: `src/components/notNow.test.ts`.
 */

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";
import { DECLINED_LINE, NOT_NOW } from "@/lib/declineAnswer";

/** The quiet control beside Submit. */
export function NotNowButton({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick} disabled={disabled} data-not-now="">
      {NOT_NOW}
    </Button>
  );
}

/** The X in the title row. Its accessible name is "Not now", not "Close". */
export function NotNowClose({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  return (
    <Button
      variant="ghost"
      size="icon-xs"
      icon="X"
      onClick={onClick}
      disabled={disabled}
      aria-label={NOT_NOW}
      title={NOT_NOW}
      data-not-now-close=""
      className="shrink-0"
    />
  );
}

/** The muted line of a declined card: "Not answered · You can ask again later." */
export function DeclinedNote({ className = "" }: { className?: string }) {
  return (
    <div
      role="status"
      data-declined=""
      className={`flex items-center gap-1.5 text-[11px] text-muted-foreground ${className}`}
    >
      <Icon name="CircleMinus" size={12} className="shrink-0" />
      <span>{DECLINED_LINE}</span>
    </div>
  );
}
