"use client";

/**
 * A section that folds away — the Metorite wrapper over Base UI's Collapsible.
 *
 * ⚠️ **This file names `@base-ui/react`, and that is allowed only because it
 * lives in `components/ui/`** (AGENTS.md rule 8, D-PM-15: one substrate, and
 * every primitive arrives as a wrapper carrying `.cc-control`, `<Icon name>`
 * and semantic tokens). `Modal.tsx` is the worked example this follows.
 *
 * ## Why not a `<details>` element, which is free
 *
 * `<details>` cannot be animated open, ignores `.cc-control`, and styles its
 * marker differently in every engine — so the triangle would be the one
 * control in the app that does not follow the theme. The substrate gives the
 * ARIA wiring (`aria-expanded`, `aria-controls`) and leaves the chrome to us,
 * which is the split the rule exists for.
 *
 * ## The summary must survive the fold
 *
 * ⚠️ `count` is the whole point of collapsing. A section folded to its title
 * alone hides whether there is anything in it, so somebody opens all of them
 * again to find out — which is worse than never folding. "Files · 12" tells
 * you without unfolding, so `Section` takes the count rather than leaving
 * each caller to remember.
 */

import { Collapsible as Base } from "@base-ui/react/collapsible";

import Icon from "@/components/Icon";

/** Room for a focus ring inside the clipping panel; see the note at its use. */
export const PANEL_CLASS = "-mx-1.5 -mb-1.5 overflow-hidden px-1.5 pb-1.5";

export interface CollapsibleSectionProps {
  /** The heading. Kept short — it sits beside the count and the chevron. */
  label: string;
  /** Lucide name, drawn before the label; follows the active pack. */
  icon?: string;
  /**
   * How much is inside. Shown beside the label and ALWAYS visible, open or
   * shut — see the note above. `null` draws nothing, which is right for a
   * section whose content is not countable (a description).
   */
  count?: number | null;
  /** Start folded. The caller owns whether that choice is remembered. */
  defaultOpen?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Passed to the rendered `<section>` — the grid placement lives here. */
  className?: string;
  /**
   * Keep the content in the page while it is folded (WS-27bn R2b).
   *
   * ⚠️ Base UI's panel UNMOUNTS a closed panel by default. A report's table
   * sits folded under its chart, and an unmounted table is gone from the
   * page: find-in-page cannot reach it, and a render test cannot see it.
   * With this on, the folded content stays in the markup and is hidden.
   */
  keepMounted?: boolean;
  /**
   * The trigger's accessible name, when the label alone repeats (WS-27bm
   * S10). A list of sections that all read "Waits on" is a list of equal
   * buttons to a screen reader. Start the name with the visible label, so
   * a voice user who says what they see still reaches it (WCAG 2.5.3).
   */
  ariaLabel?: string;
  children: React.ReactNode;
}

export function CollapsibleSection({
  label,
  icon,
  count,
  defaultOpen = true,
  open,
  onOpenChange,
  className,
  keepMounted = false,
  ariaLabel,
  children,
}: CollapsibleSectionProps) {
  return (
    <Base.Root
      defaultOpen={defaultOpen}
      open={open}
      onOpenChange={onOpenChange}
      render={<section className={className} />}
    >
      {/* ⚠️ The trigger lives INSIDE an `h3`, which is the accordion pattern
          and not decoration: the section headings were `<h3>` before they
          folded, and a screen-reader user navigates this panel by heading.
          Turning them into bare buttons would have removed every landmark
          from the surface while looking identical.

          ⚠️ **No `uppercase` here, and that is not an omission.**
          `.cc-control` sets `text-transform` from `--control-label-transform`
          (globals.css), so a hard-coded `uppercase` on this heading is inert
          — it loses to the token. A section heading is now a BUTTON, rule 8
          says a control carries `.cc-control`, and the consequence is that
          its casing follows the theme like every other control's.

          The static `FieldCell` labels inside still hard-code `uppercase`,
          so the two disagree under a theme that sets `none`. That is a real
          inconsistency and it is the FieldCell side that is wrong — filed
          rather than patched here, because changing it touches every panel
          in the app, not this primitive. */}
      <h3 className="text-[11px] font-semibold tracking-wide">
      <Base.Trigger
        aria-label={ariaLabel}
        className="cc-control group flex w-full items-center gap-1.5 rounded px-0 py-1 text-left text-muted-foreground hover:text-foreground"
      >
        {icon ? <Icon name={icon} className="h-3 w-3 shrink-0" /> : null}
        <span>{label}</span>
        {typeof count === "number" && count > 0 ? (
          <span className="font-normal normal-case text-muted-foreground">
            · {count}
          </span>
        ) : null}
        {/* Rotates rather than swapping glyph, so the control does not jump
            by a pixel between states. `group-data-[panel-open]` is Base UI's
            own attribute — no open state is duplicated here. */}
        <Icon
          name="ChevronDown"
          className="ml-auto h-3 w-3 shrink-0 transition-transform group-data-[panel-open]:rotate-180"
        />
      </Base.Trigger>
      </h3>
      {/* ⚠️ The panel clips (`overflow-hidden`, for the fold), and a field's
          focus ring is drawn OUTSIDE the field: 2px of outline after a 2px
          offset (`.cc-control:focus-visible`, globals.css). With no room at
          the sides, the ring of a full-width field was cut off at both ends
          (owner report, 2026-10-09, the task's Description). So the panel
          reaches 1.5 units past the section on three sides and pads back by
          the same amount: the content does not move, and the ring has room.
          1.5, not 1, because compact density shrinks a unit below 4px.
          Fence: `src/components/ui/collapsible.test.ts`. */}
      <Base.Panel className={PANEL_CLASS} keepMounted={keepMounted}>
        <div className="pt-1.5">{children}</div>
      </Base.Panel>
    </Base.Root>
  );
}

/**
 * The motion of a card's fold. It runs only on a toggle by hand
 * (`animate`): an automatic roll-up snaps, so the transcript under it does
 * not slide while the member reads. `motion-reduce` drops it for a member
 * who asked the system for less motion. The height variable is Base UI's
 * own, and it returns to `auto` once the panel is open, so a card that
 * grows while open is never clipped.
 */
export const CARD_PANEL_MOTION =
  "h-[var(--collapsible-panel-height)] overflow-hidden transition-[height] duration-200 ease-out data-[starting-style]:h-0 data-[ending-style]:h-0 motion-reduce:transition-none";

export interface CollapsibleCardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Draw the header that toggles the body. Off, the card draws its body and
   * nothing else, and it is always open: a short card has nothing to fold.
   * The body stays in the same place in the tree either way, so a card that
   * becomes long never remounts its content.
   */
  toggle: boolean;
  /** The header's words. Plain text, one line. */
  label: string;
  /** Lucide name, drawn before the label. */
  icon?: string;
  /** How many rows the body holds. Shown in both states. */
  count?: number | null;
  /** One line that says what the folded body holds. Shown only while shut. */
  summary?: string;
  /** Animate the next fold (a toggle by hand). */
  animate?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * A chat card that folds to one header row (owner request, 2026-10-10).
 *
 * Shut, the header is the card: chevron, icon, label, count and a one-line
 * summary, inside the card's own border. Open, the header is a quiet "Roll
 * up" control above the card, so the card keeps its own chrome and its title
 * does not draw twice.
 * The trigger is a real button with `aria-expanded` and `aria-controls`,
 * from the substrate. The body stays mounted while shut (`keepMounted`), so
 * a card keeps what the member typed into it.
 */
export function CollapsibleCard({
  open,
  onOpenChange,
  toggle,
  label,
  icon,
  count,
  summary,
  animate = false,
  className,
  children,
}: CollapsibleCardProps) {
  const shown = toggle ? open : true;
  return (
    <Base.Root
      open={shown}
      onOpenChange={onOpenChange}
      render={<div className={className} data-rollup={shown ? "open" : "closed"} />}
    >
      {toggle ? (
        <Base.Trigger
          data-rollup-toggle=""
          aria-label={`${label}${typeof count === "number" && count > 0 ? `, ${count}` : ""}. ${shown ? "Roll up" : "Show all"}`}
          className={
            shown
              ? "cc-control group mb-1 ml-auto flex w-fit items-center gap-1 rounded px-1 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
              : "cc-control group flex w-full min-w-0 items-center gap-1.5 rounded-lg border border-border bg-card/40 px-2.5 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-secondary/60 hover:text-foreground"
          }
        >
          {/* Open, the card below already wears its title, so the control
              says only what it does. Shut, the header stands in for the
              card: its icon, its label, its count and one line of it. */}
          <Icon
            name="ChevronDown"
            className={`h-3 w-3 shrink-0 ${animate ? "transition-transform motion-reduce:transition-none" : ""} ${shown ? "rotate-180" : "-rotate-90"}`}
          />
          {shown ? (
            <span>Roll up</span>
          ) : (
            <>
              {icon ? <Icon name={icon} className="h-3 w-3 shrink-0" /> : null}
              <span className="max-w-[60%] shrink-0 truncate font-medium text-foreground">{label}</span>
              {typeof count === "number" && count > 0 ? (
                <span className="shrink-0 tabular-nums">· {count}</span>
              ) : null}
              {summary ? <span className="min-w-0 flex-1 truncate">{summary}</span> : null}
            </>
          )}
        </Base.Trigger>
      ) : null}
      <Base.Panel keepMounted className={toggle && animate ? CARD_PANEL_MOTION : undefined}>
        {children}
      </Base.Panel>
    </Base.Root>
  );
}

export default CollapsibleSection;
