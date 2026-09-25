"use client";

/**
 * The empty state — one shape for every list surface (S4).
 *
 * A surface with nothing on it has to answer one question: *is this empty, or
 * did I hide it?* `/tasks` answers it with two distinct states — `NoMatchState`
 * ("No tasks match your filters", plus the way out) and `EmptyState` (an icon
 * and copy for the view). `/projects` answered it with one string that asked
 * the reader to guess: *"Nothing to show. Clear a filter, or this project has
 * no statuses yet."* Convergence runs both ways, and here Tasks was right.
 *
 * So this is the box, promoted so both apps can draw one. WHICH of the two
 * states a surface is in, and what each says, stays the app's own business —
 * `/projects` decides it in `app/projects/lib/emptyState.ts`, where the copy is
 * unit-tested rather than eyeballed.
 *
 * **The action is the whole point of the filtered variant.** A dead end that
 * says "nothing matches" and offers no way back reads as a broken screen; the
 * one control that undoes the cause belongs in the state that names it.
 *
 * WS-27am adds the **third** state to that pair — no-permission — as a
 * capability of the action rather than a new component: `disabled` +
 * `disabledReason`, so a CTA the reader may not use is drawn greyed instead of
 * being hidden. All three props are optional and nothing existing changed
 * shape, which is why that slice wired no call site: an additive prop needs no
 * edit at a surface that does not use it, and `TaskBoard`/`TaskList`/
 * `TableView` were held open by other slices.
 *
 * Both apps draw it now. Continuity P3 retired `/tasks`' own
 * `NoMatchState`/`EmptyState` pair, and the Inbox's pair, onto this box. The
 * copy stays in `app/tasks/lib/emptyState.ts`. Fence: `sharedTaskUi.test.ts`.
 */

import { useId } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

export interface EmptyStateAction {
  label: string;
  /** Lucide name; the active theme picks the pack. */
  icon?: string;
  /**
   * Omitted only for a disabled action — there is nothing to run. Optional
   * rather than required so the no-permission arm below does not have to invent
   * a no-op callback whose only purpose is to satisfy the type.
   */
  onClick?: () => void;
  /**
   * **Disabled, not hidden** (WS-27am, the third arm of the triad).
   *
   * A surface the caller may not write to still shows its CTA, greyed. Hiding
   * it teaches the reader that the action does not exist here — so they go
   * looking for it, or file the absence as a bug. Showing it disabled teaches
   * them two true things at once: the action exists, and it is not theirs. The
   * `Button` primitive already draws `disabled:opacity-50` and
   * `disabled:cursor-not-allowed`, so this is one prop and no new chrome.
   */
  disabled?: boolean;
  /**
   * Why it is disabled, as a sentence. A disabled control with no explanation
   * is only half the message — the reader learns they cannot, never why or who
   * to ask. Rendered as the native tooltip AND as the button's accessible
   * description, because `title` alone is unreachable from a keyboard.
   */
  disabledReason?: string;
}

export function EmptyState({
  icon,
  message,
  hint,
  action,
  /**
   * `success` is the celebratory tick /tasks draws on "Inbox zero. Mind like
   * water." — an empty inbox is an achievement, an empty board is not. Kept as
   * a named tone rather than a class so the caller never writes a colour.
   */
  tone = "muted",
  className = "",
}: {
  /** Lucide name for the glyph above the copy. */
  icon: string;
  message: string;
  /** One line of what to do about it. Omitted when there is nothing to say. */
  hint?: string;
  action?: EmptyStateAction;
  tone?: "muted" | "success";
  className?: string;
}) {
  const reasonId = useId();
  const reasonShown = Boolean(action?.disabled && action.disabledReason);
  return (
    <div
      className={`flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center ${className}`}
    >
      <Icon
        name={icon}
        className={`h-8 w-8 ${tone === "success" ? "text-success/70" : "text-muted-foreground/60"}`}
        aria-hidden
      />
      <p className="text-sm text-muted-foreground">{message}</p>
      {hint ? (
        <p className="max-w-xs text-xs text-muted-foreground/80">{hint}</p>
      ) : null}
      {action ? (
        <Button
          variant="secondary"
          size="sm"
          icon={action.icon}
          disabled={action.disabled}
          title={action.disabled ? action.disabledReason : undefined}
          aria-describedby={reasonShown ? reasonId : undefined}
          onClick={action.onClick}
        >
          {action.label}
        </Button>
      ) : null}
      {/* The reason is rendered, not only tooltipped: a disabled button is not
          focusable, so `title` alone is unreachable from a keyboard and never
          appears on a touch screen — the two ways most people would meet it. */}
      {reasonShown ? (
        <p id={reasonId} className="max-w-xs text-xs text-muted-foreground/80">
          {action?.disabledReason}
        </p>
      ) : null}
    </div>
  );
}
