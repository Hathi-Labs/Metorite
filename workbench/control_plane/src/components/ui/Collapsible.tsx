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
import { createContext, useContext, useId } from "react";

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

/**
 * The frame the wrapper draws for a card that has no title row of its own
 * (`header="frame"`), while it folds. A card with its own title row keeps
 * its own frame (`header="own"`).
 */
export const CARD_FRAME_CLASS = "rounded-lg border border-border bg-card/40 px-2.5 py-1.5";

/**
 * ⚠️ **The body has no hanging indent** (owner feedback, 2026-10-10). It
 * lined up under the TITLE once, past the chevron and the icon, and that
 * left about 60 px of empty column at the left of every receipt row. Now the
 * body starts at the card's content padding, in line with the chevron's left
 * edge. There is no prop to bring the indent back: a card that needs its own
 * inner padding (a table, a generative card) sets it on its own box.
 * Fence: `src/lib/cardDenseLayout.test.ts`.
 */

/**
 * What a folding card tells its own header and body (owner feedback,
 * 2026-10-10: "one toggle, one place, inside the card").
 *
 * `RollupCard` provides it, and `CollapsibleCard` fills it in. A card whose
 * title row already exists draws `CollapsibleCardHeader` in that place, and
 * that row becomes the toggle. There is no second header above it.
 */
export interface CardFoldState {
  /** A Base UI root is above, so the body is a panel. Off outside a
   *  transcript, where a card draws as it always did. */
  rooted: boolean;
  /** The header is the toggle. Only a long card that does not wait. */
  toggle: boolean;
  open: boolean;
  /** The title, plain text. It is also the toggle's accessible name. */
  label: string;
  /** Lucide name, drawn after the chevron. */
  icon?: string;
  /** How many rows the body holds. Shown in both states. */
  count?: number | null;
  /** One line that says what the folded body holds. Shown only while shut. */
  summary?: string;
  /** Told the body's element, so the wrapper can measure it. */
  bodyRef?: (el: HTMLDivElement | null) => void;
  /** The panel's id, for the toggle's `aria-controls`. */
  panelId?: string;
}

const CardFoldContext = createContext<CardFoldState | null>(null);

/** The state of the folding card this element sits in, or null. */
export function useCardFold(): CardFoldState | null {
  return useContext(CardFoldContext);
}

/** Provide a card's fold state with no Base UI root: the static draw. */
export function CardFoldProvider({ value, children }: { value: CardFoldState; children: React.ReactNode }) {
  return <CardFoldContext.Provider value={value}>{children}</CardFoldContext.Provider>;
}

export interface CollapsibleCardHeaderProps {
  /** The visible title, when the card draws it richer than plain text (a
   *  pill, a fence). The plain `label` of the fold state is the default. */
  title?: React.ReactNode;
  /** Draw nothing while the header does not toggle. For a card that had no
   *  title row before it folded. */
  quiet?: boolean;
  /** The row's type size: the count and the summary follow it too. */
  className?: string;
  /** The title's weight and colour. The icon keeps the card's own colour. */
  titleClassName?: string;
}

/**
 * The card's title row, and its one toggle.
 *
 * While the card folds, the WHOLE row is the button: chevron, icon, title,
 * count, and the one-line summary while shut. The chevron is the first
 * thing in the row in both states, so it never moves: it points right shut
 * and down open, and one icon turns between the two. Otherwise the row is
 * plain text, as the card drew it before it could fold.
 *
 * Outside a folding card it draws nothing, so a card can call it anywhere.
 */
export function CollapsibleCardHeader({
  title,
  quiet = false,
  className = "text-[11px]",
  titleClassName = "font-medium text-foreground",
}: CollapsibleCardHeaderProps) {
  const fold = useCardFold();
  if (!fold) return null;
  const { toggle, open, label, icon, count, summary, panelId } = fold;
  const counted = typeof count === "number" && count > 0;
  const shut = toggle && !open;
  // The count sits right after the title, at a word's gap (`gap-1`), not at
  // the row's gap: "Tasks created · 5" reads as one phrase. The two share
  // one box, so a shut card's summary cuts after the count, never inside it.
  const words = (
    <>
      {icon ? <Icon name={icon} size={13} className="shrink-0" /> : null}
      <span
        data-rollup-title=""
        className={`flex min-w-0 items-baseline gap-1 ${shut && summary ? "max-w-[60%] shrink-0" : ""}`}
      >
        <span className={`min-w-0 truncate ${titleClassName}`}>{title ?? label}</span>
        {counted ? <span className="shrink-0 font-normal tabular-nums text-muted-foreground">· {count}</span> : null}
      </span>
    </>
  );
  if (!toggle) {
    if (quiet) return null;
    return <div className={`flex min-w-0 items-center gap-2 ${className}`}>{words}</div>;
  }
  return (
    <Base.Trigger
      data-rollup-toggle=""
      aria-label={`${label}${counted ? `, ${count}` : ""}`}
      // The substrate names the panel only while it is open. The panel stays
      // mounted while shut, so the toggle names it in both states.
      aria-controls={panelId}
      className={`cc-control group -mx-1 -my-0.5 flex w-[calc(100%+0.5rem)] min-w-0 items-center gap-2 rounded px-1 py-0.5 text-left ${className}`}
    >
      {/* One icon for both states, turned: a swap of glyph would jump by a
          pixel. `motion-reduce` keeps it still for a member who asked. */}
      <span
        data-rollup-chevron=""
        aria-hidden
        className={`flex shrink-0 text-muted-foreground transition-transform duration-200 ease-out group-hover:text-foreground motion-reduce:transition-none ${shut ? "-rotate-90" : ""}`}
      >
        <Icon name="ChevronDown" size={12} />
      </span>
      {words}
      {shut && summary ? (
        <span className="min-w-0 flex-1 truncate font-normal text-muted-foreground">{summary}</span>
      ) : null}
    </Base.Trigger>
  );
}

export interface CollapsibleCardBodyProps {
  /** Classes for the body's own box (its gap, its padding). */
  className?: string;
  children: React.ReactNode;
}

/**
 * The part of a card that folds away. Inside a transcript it is the panel,
 * kept mounted while shut, so a card keeps what the member typed into it.
 * Its children see no fold state, so a card nested inside cannot claim the
 * header a second time.
 */
export function CollapsibleCardBody({ className = "", children }: CollapsibleCardBodyProps) {
  const fold = useCardFold();
  const box = `min-w-0 ${className}`.trim();
  const inner = (
    <CardFoldContext.Provider value={null}>
      <div ref={fold?.bodyRef} className={box}>
        {children}
      </div>
    </CardFoldContext.Provider>
  );
  if (!fold?.rooted) return fold ? inner : <>{children}</>;
  return <CardPanel id={fold.panelId}>{inner}</CardPanel>;
}

/** The panel, with its motion only for a fold by hand. */
function CardPanel({ id, children }: { id?: string; children: React.ReactNode }) {
  const motion = useContext(CardMotionContext);
  return (
    <Base.Panel id={id} keepMounted className={motion ? CARD_PANEL_MOTION : undefined}>
      {children}
    </Base.Panel>
  );
}

const CardMotionContext = createContext(false);

export interface CollapsibleCardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * The header toggles the body. Off, the card draws its body and its plain
   * title row, and it is always open: a short card has nothing to fold.
   * The body stays in the same place in the tree either way, so a card that
   * becomes long never remounts its content.
   */
  toggle: boolean;
  /** The header's words. Plain text, one line. */
  label: string;
  /** Lucide name, drawn after the chevron. */
  icon?: string;
  /** How many rows the body holds. Shown in both states. */
  count?: number | null;
  /** One line that says what the folded body holds. Shown only while shut. */
  summary?: string;
  /**
   * Who draws the title row.
   *
   * - `"own"`: the card has a title row, and draws `CollapsibleCardHeader`
   *   in its place and `CollapsibleCardBody` round the rest. Its title does
   *   not draw twice, and the toggle is inside its own border.
   * - `"frame"` (the default): the card has no title row. While it folds,
   *   this draws a frame with the header as its first row.
   */
  header?: "own" | "frame";
  /** Told the body's element, so the wrapper can measure it. */
  bodyRef?: (el: HTMLDivElement | null) => void;
  /** Animate the next fold (a toggle by hand). */
  animate?: boolean;
  className?: string;
  children: React.ReactNode;
}

/**
 * A chat card that folds to its own title row (owner request, 2026-10-10,
 * and the owner's feedback the same day).
 *
 * There is ONE toggle, and it is the card's title row, inside the card's
 * border, in both states. Open, the row is the first row of the card. Shut,
 * the card shrinks to that same row, and the row adds a one-line summary.
 * The chevron is at the left of the row in both states, so the pointer does
 * not travel between "open" and "close".
 *
 * The trigger is a real button with `aria-expanded` and `aria-controls`,
 * from the substrate. The body is not inside the button, so a link in the
 * body opens without a fold.
 */
export function CollapsibleCard({
  open,
  onOpenChange,
  toggle,
  label,
  icon,
  count,
  summary,
  header = "frame",
  bodyRef,
  animate = false,
  className,
  children,
}: CollapsibleCardProps) {
  const shown = toggle ? open : true;
  const panelId = useId();
  const state: CardFoldState = { rooted: true, toggle, open: shown, label, icon, count, summary, bodyRef, panelId };
  return (
    <Base.Root
      open={shown}
      onOpenChange={onOpenChange}
      render={<div className={className} data-rollup={shown ? "open" : "closed"} />}
    >
      <CardMotionContext.Provider value={toggle && animate}>
        <CardFoldContext.Provider value={state}>
          {header === "own" ? (
            children
          ) : (
            // The frame stays in the tree when the card is short (as
            // `contents`, which draws no box), so the body never remounts.
            <div data-rollup-frame="" className={toggle ? CARD_FRAME_CLASS : "contents"}>
              {toggle ? <CollapsibleCardHeader /> : null}
              <CollapsibleCardBody className={toggle ? "pt-1.5" : ""}>{children}</CollapsibleCardBody>
            </div>
          )}
        </CardFoldContext.Provider>
      </CardMotionContext.Provider>
    </Base.Root>
  );
}

export default CollapsibleSection;
