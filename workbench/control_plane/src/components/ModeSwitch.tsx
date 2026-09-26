"use client";

/**
 * The view switcher — which canvas you look at a set of tasks through.
 *
 * Promoted from `app/projects/page.tsx` on 2026-09-24 so My Tasks draws its
 * List / Board switch with the same control. My Tasks had a hand-rolled
 * segmented pair with a solid `bg-primary` active fill. Projects had ghost
 * `Button`s with `selected`. Two looks for one decision.
 *
 * Ghost `Button`s with `selected`, so the ON one wears the house pair
 * (`bg-primary/10 text-primary`) and sets `aria-pressed` with it
 * (`DESIGN_SYSTEM.md` §3, "A control that is ON").
 *
 * Deliberately NOT the shared `<Tabs>`: that is a page-level bar carrying its
 * own padding and bottom border, and this sits inside a header row.
 *
 * `layout="sheet"` is the phone drawer's column of larger rows.
 *
 * Fence: `ModeSwitch.test.ts`, and `sharedTaskUi.test.ts` (one declaration,
 * both apps reach it).
 */

import Button from "@/components/ui/Button";

export interface ModeOption<Id extends string> {
  id: Id;
  /** An `<Icon>` NAME. */
  icon: string;
  /** Shown text. Defaults to the id, with its first letter capitalised. */
  label?: string;
}

/**
 * "board" → "Board". In JS, not a `capitalize` class: `.cc-control` sets the
 * label's text transform from a token, which beats a utility class, so the
 * class drew "board" (measured 2026-09-24, light mode, both apps).
 */
function sentenceCase(id: string): string {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

export function ModeSwitch<Id extends string>({
  modes,
  mode,
  onPick,
  layout = "toolbar",
  label = "View mode",
}: {
  modes: readonly ModeOption<Id>[];
  mode: Id;
  onPick: (next: Id) => void;
  layout?: "toolbar" | "sheet";
  /** The group's accessible name. */
  label?: string;
}) {
  const sheet = layout === "sheet";
  return (
    <div
      className={sheet ? "flex flex-col gap-0.5" : "flex shrink-0 items-center gap-1"}
      role="group"
      aria-label={label}
    >
      {modes.map((entry) => (
        <Button
          key={entry.id}
          variant="ghost"
          size={sheet ? "lg" : "sm"}
          selected={mode === entry.id}
          icon={entry.icon}
          onClick={() => onPick(entry.id)}
        >
          {entry.label ?? sentenceCase(entry.id)}
        </Button>
      ))}
    </div>
  );
}

export default ModeSwitch;
