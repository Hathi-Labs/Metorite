"use client";

/**
 * Switch — the on/off control, one component for the product.
 *
 *     <Switch checked={on} onChange={setOn} label="Show Status column" />
 *
 * ## Why this exists (D142, `glass_look.md` GL-2)
 *
 * Three local copies drew a switch each: the email automation `Toggle`, the
 * Tasks settings modal (two of them) and the Meeting copilot toggle in Notes.
 * They agreed on the semantics (`role="switch"`, `aria-checked`) and on
 * nothing else, so the glass look had three places to restyle and no place
 * to say "green when on". Now they render through this component.
 *
 * ## Two looks, one DOM
 *
 * **Glass** is Aroh's switch: 38 by 22 px (51 by 31 on a touch screen),
 * `--system-green` when on, `bg-muted` when off, and a white knob with a
 * shadow. Every glass class sits behind `glass:`.
 *
 * **Classic** is what each copy drew before. The copies differ, so the
 * `classic` prop picks the shape and each call site keeps its exact output:
 *
 * - `default`: 36 by 20 px, accent fill when on (the Tasks settings modal).
 * - `compact`: 32 by 18 px, the email automation `Toggle`.
 * - `outlined`: 36 by 20 px with a border and a tinted fill (Meeting copilot).
 *
 * A `<button>` toggles on Space and Enter by itself, so the keyboard needs no
 * handler here. Pass `label` unless a `<label>` around the switch already
 * names it: `aria-label` would override that name (GL-2 review, the email
 * onboarding switches).
 */

export type SwitchClassic = "default" | "compact" | "outlined";

interface Shape {
  track: string;
  on: string;
  off: string;
  knob: string;
  knobOn: string;
  knobOff: string;
}

const SHAPES: Record<SwitchClassic, Shape> = {
  default: {
    track: "tech-transition relative inline-flex h-5 w-9 shrink-0 items-center rounded-full",
    on: "bg-primary",
    off: "bg-secondary",
    knob: "inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform",
    knobOn: "translate-x-[18px]",
    knobOff: "translate-x-[2px]",
  },
  compact: {
    // OFF is `bg-muted-foreground/30`, not `bg-secondary`. In light mode
    // `--secondary` is about 96% light, so on a white card the OFF track and
    // its white knob vanished (EM-T6d visual review, 2026-10-03).
    track: "relative h-[18px] w-[32px] flex-shrink-0 rounded-full transition-colors",
    on: "bg-primary",
    off: "bg-muted-foreground/30",
    knob: "absolute top-0.5 h-3.5 w-3.5 rounded-full bg-white transition-all",
    knobOn: "left-[15px]",
    knobOff: "left-0.5",
  },
  outlined: {
    track: "relative h-5 w-9 shrink-0 rounded-full border transition-colors",
    on: "border-primary bg-primary/30",
    off: "border-border bg-secondary",
    knob: "absolute top-0.5 h-3.5 w-3.5 rounded-full transition-all",
    knobOn: "left-[calc(100%-1.125rem)] bg-primary",
    knobOff: "left-0.5 bg-muted-foreground",
  },
};

/**
 * The glass classes of the track and the knob, the same for every classic
 * shape. They reset what a classic shape sets (an `absolute` knob, a border,
 * a size) so one glass switch comes out of three classic ones.
 */
const GLASS_TRACK =
  "glass:relative glass:inline-flex glass:h-[22px] glass:w-[38px] glass:items-center glass:rounded-full glass:border-0 " +
  "glass:duration-200 glass:pointer-coarse:h-[31px] glass:pointer-coarse:w-[51px]";
const GLASS_KNOB =
  "glass:static glass:h-[18px] glass:w-[18px] glass:rounded-full glass:bg-white glass:shadow-md " +
  "glass:duration-200 glass:pointer-coarse:h-[27px] glass:pointer-coarse:w-[27px]";

/** The class strings of one switch. Exported for the markup test. */
export function switchClasses(classic: SwitchClassic, checked: boolean) {
  const shape = SHAPES[classic];
  return {
    track: `${shape.track} ${checked ? shape.on : shape.off} ${GLASS_TRACK} ${
      checked ? "glass:bg-system-green" : "glass:bg-muted"
    } disabled:opacity-50`,
    knob: `${shape.knob} ${checked ? shape.knobOn : shape.knobOff} ${GLASS_KNOB} ${
      checked
        ? "glass:translate-x-[18px] glass:pointer-coarse:translate-x-[22px]"
        : "glass:translate-x-[2px]"
    }`,
  };
}

export interface SwitchProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /**
   * The accessible name. Leave it out ONLY when a `<label>` around the switch
   * names it, because `aria-label` overrides the label's text.
   */
  label?: string;
  disabled?: boolean;
  /** Native tooltip. */
  title?: string;
  /** Which classic shape to draw. Glass draws one shape. */
  classic?: SwitchClassic;
  /** Layout only, for example a margin. Never colour or size. */
  className?: string;
}

export function Switch({
  checked,
  onChange,
  label,
  disabled,
  title,
  classic = "default",
  className = "",
}: SwitchProps) {
  const cls = switchClasses(classic, checked);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label || undefined}
      title={title}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`${cls.track}${className ? ` ${className}` : ""}`}
    >
      <span className={cls.knob} />
    </button>
  );
}

export default Switch;
