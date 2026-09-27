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
      <Base.Panel className="overflow-hidden" keepMounted={keepMounted}>
        <div className="pt-1.5">{children}</div>
      </Base.Panel>
    </Base.Root>
  );
}

export default CollapsibleSection;
