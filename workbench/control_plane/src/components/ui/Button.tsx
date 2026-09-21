"use client";

/**
 * Button — the shared action primitive.
 *
 * Replaces the class recipe that DESIGN_SYSTEM.md used to document as a string
 * to copy. That worked for colour and radius, which are tokens, but a design
 * system's *behaviour* has nowhere to live in a copied class list: Material's
 * hover is a translucent state layer, Fluent strokes even its solid buttons,
 * and Graphite upper-cases its labels. Those come from the control tokens and
 * need one component to apply them.
 *
 *     <Button onClick={save}>Save</Button>
 *     <Button variant="secondary" size="sm">Cancel</Button>
 *     <Button variant="destructive" icon="Trash2" loading={deleting}>Delete</Button>
 *     <Button variant="ghost" size="icon" icon="X" aria-label="Close" />
 *
 * The default variant and size reproduce the previous recipe exactly, so the
 * RapidTool theme is unchanged by adoption.
 */

import Icon from "@/components/Icon";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "destructive" | "text";

export type ButtonSize = "sm" | "md" | "lg" | "icon-xs" | "icon-sm" | "icon" | "none";

/**
 * Colour per variant. `cc-button-filled` adds the theme's stroke on solid
 * variants — no-op at 0px, a real border on Fluent.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary: "cc-button-filled bg-primary text-primary-foreground hover:opacity-90",
  secondary:
    "border border-border text-muted-foreground hover:text-foreground hover:border-primary/30",
  ghost: "text-muted-foreground hover:bg-secondary hover:text-foreground",
  destructive: "bg-destructive/10 text-destructive hover:bg-destructive/20",
  /** Bare text action — no surface, no border. Common in dense toolbars. */
  text: "text-muted-foreground hover:text-foreground",
};

/**
 * How a control says it is ON. One vocabulary for the whole product.
 *
 * **Measured 2026-09-21: 32 hand-rolled toggles across 21 files, and no
 * primitive.** Three of them sit in Projects and are the whole remaining
 * balance of H-148 — a view-mode switch, a saved-view chip and an icon
 * chooser. All three had already converged on `bg-primary/10 text-primary`
 * by hand, and `FilterBar`'s own comment records the correction that got it
 * there: it used to be `bg-accent text-accent-foreground`, which resolves
 * to a different colour per theme, so the applied view read as "selected"
 * in one theme and "highlighted" in the next.
 *
 * So the vocabulary was already agreed and simply had nowhere to live. This
 * is where it lives.
 *
 * ⚠️ **`primary` and `destructive` get a ring, not a fill.** They are
 * already filled, so a selected fill would say nothing. A toggle whose off
 * state is a solid button is unusual, and the ring is there so it does not
 * silently render identically to its own off state.
 */
const SELECTED: Record<ButtonVariant, string> = {
  primary: "ring-2 ring-ring ring-offset-1 ring-offset-background",
  secondary: "border-primary/40 bg-primary/10 text-primary",
  ghost: "bg-primary/10 text-primary",
  destructive: "ring-2 ring-destructive/40",
  text: "text-primary",
};

/**
 * Should this control carry `aria-pressed`, and with what value?
 *
 * A pure function, and exported, because `vitest.config.ts` runs in the
 * `node` environment — there is no DOM to render and assert against, so a
 * decision that only exists inside JSX is a decision no test can reach.
 * This is the same reason `SelectButton.test.ts` gives for its own shape.
 *
 * The rule:
 *
 * - An explicit `aria-pressed` from the caller always wins.
 * - A control that declares its own `role` or `aria-checked` — a radio in a
 *   `radiogroup`, a `menuitemcheckbox` — gets nothing. `aria-pressed` on a
 *   radio is two conflicting answers to "what kind of control is this", and
 *   a screen reader announces the wrong one.
 * - Otherwise `selected` drives it, so the styling and the semantics cannot
 *   disagree. That they CAN disagree today is the defect: of the 32
 *   hand-rolled toggles measured on 2026-09-21, some set `aria-pressed` and
 *   styled nothing, others styled the state and told a screen reader
 *   nothing.
 */
export function pressedFor(a: {
  selected?: boolean;
  role?: string;
  ariaChecked?: boolean | "true" | "false" | "mixed";
  ariaPressed?: boolean | "true" | "false" | "mixed";
}): boolean | "true" | "false" | "mixed" | undefined {
  if (a.ariaPressed !== undefined) return a.ariaPressed;
  if (a.selected === undefined) return undefined;
  if (a.role !== undefined || a.ariaChecked !== undefined) return undefined;
  return a.selected;
}

/**
 * Geometry per size. Three icon sizes rather than one because the app
 * genuinely uses three; collapsing them would resize ~100 existing controls.
 */
const SIZES: Record<ButtonSize, string> = {
  sm: "gap-1 px-2.5 py-1.5 text-[12px]",
  md: "gap-1.5 px-3 py-1.5 text-xs",
  lg: "gap-1.5 px-3 sm:px-4 py-2 text-sm",
  "icon-xs": "p-1",
  "icon-sm": "p-1.5",
  icon: "p-2",
  /** Caller supplies its own padding, gap and text size. */
  none: "",
};

/** Display and alignment a normal button wants. */
const LAYOUT_DEFAULT = "inline-flex items-center justify-center";

export type ButtonProps = Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "className"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Lucide icon name rendered before the label; follows the active pack. */
  icon?: string;
  /**
   * Swaps the icon for a spinner and disables the button. Kept as a prop
   * rather than left to call sites because "disabled while pending" is the
   * part everyone forgets, and a double-submit is a real bug.
   */
  loading?: boolean;
  /**
   * Whether the button takes the theme's button radius.
   *
   * `keep` omits it so an explicit `rounded-*` in `className` wins — for
   * controls that predate the primitive and use a radius unrelated to the
   * theme token. Adopting the primitive should give them the theme's label
   * treatment, state layer and focus ring WITHOUT resizing their corners.
   * Prefer `theme` for anything new.
   */
  radius?: "theme" | "keep";
  /**
   * Display and alignment classes, REPLACING the default.
   *
   * A prop rather than something `className` can override, because Tailwind
   * cannot: `justify-center` and `justify-start` have equal specificity, so
   * which applies depends on their order in the generated stylesheet, not the
   * class attribute. An empty string is meaningful too — a bare `<button>` is
   * `inline-block`, and forcing `inline-flex` on one moves its content.
   */
  layout?: string;
  /**
   * This control is ON — a view mode, a filter chip, a chooser cell.
   *
   * ⚠️ **It sets the STYLING and the SEMANTICS together, and that coupling
   * is the point.** Measured across the 32 hand-rolled toggles: some set
   * `aria-pressed` and styled nothing, others styled the state and told a
   * screen reader nothing. Two halves of one fact, drifting apart. Passing
   * one prop makes that impossible.
   *
   * 📌 A caller that supplies its own `role` or `aria-checked` — a radio in
   * a `radiogroup`, say — keeps them. `aria-pressed` on a radio would be
   * two conflicting answers to "what kind of control is this".
   */
  selected?: boolean;
  /** Extra classes for layout only — never for colour, radius or weight. */
  className?: string;
  children?: React.ReactNode;
};

export default function Button({
  variant = "primary",
  size = "md",
  icon,
  loading = false,
  radius = "theme",
  layout = LAYOUT_DEFAULT,
  disabled,
  selected,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const iconSize = size === "lg" ? 15 : 14;
  const pressed = pressedFor({
    selected,
    role: rest.role,
    ariaChecked: rest["aria-checked"],
    ariaPressed: rest["aria-pressed"],
  });
  return (
    <button
      {...rest}
      aria-pressed={pressed}
      disabled={disabled || loading}
      className={`cc-control ${radius === "theme" ? "cc-button" : ""} ${layout} disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${selected ? SELECTED[variant] : ""} ${SIZES[size]} ${className}`}
    >
      {loading ? (
        <Icon name="Loader2" size={iconSize} className="animate-spin" />
      ) : icon ? (
        <Icon name={icon} size={iconSize} />
      ) : null}
      {children}
    </button>
  );
}
