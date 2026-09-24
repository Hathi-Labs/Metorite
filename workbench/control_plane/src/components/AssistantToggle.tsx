"use client";

/**
 * AssistantToggle — the one "Assistant" button in an app's top bar.
 *
 * My Tasks and Projects both put it at the right end of the slim `h-10` bar
 * at the top of the app (owner ask, 2026-09-24). Before this file, My Tasks
 * drew a hand-rolled button element there, and Projects drew a `Button` in the
 * project header one row lower. Two looks, two places, for one control.
 *
 * The button owns only its LOOK. What a press does stays with the app: My
 * Tasks swaps its panes for the assistant, and Projects docks the chat beside
 * the board (`app/projects/lib/chatDock.ts`).
 *
 * `secondary` + `selected` is the recipe My Tasks had written by hand
 * (a bordered chip that turns `bg-primary/10 text-primary` when on), so the
 * primitive now carries it, with the focus ring and `aria-pressed` it lacked.
 *
 * Fence: `AssistantToggle.test.ts` — both apps mount this component inside
 * their top bar, and neither draws a second assistant button anywhere else.
 */

import Button from "@/components/ui/Button";

export interface AssistantToggleProps {
  /** The assistant is on screen. Drives the pressed look and `aria-pressed`. */
  open: boolean;
  onToggle: () => void;
  /** The tooltip. Say what a press will do, when it is not obvious. */
  title?: string;
  /** Layout only — for example `ml-auto` to push it to the right end. */
  className?: string;
}

export default function AssistantToggle({
  open,
  onToggle,
  title = "Assistant",
  className,
}: AssistantToggleProps) {
  return (
    <Button
      variant="secondary"
      size="sm"
      icon="Sparkles"
      selected={open}
      title={title}
      onClick={onToggle}
      className={className}
    >
      Assistant
    </Button>
  );
}
