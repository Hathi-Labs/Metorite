// ── The colours of the Knowledge editor (WS-61 KB-2p, §21.7) ────────────────
//
// Pure. A text colour and a highlight store a colour in `data-color`. The
// pickers offer a list of NAMES, and `globals.css` gives each name its token
// under `.kb-text-color` and `.kb-highlight`. So a picked colour follows the
// colour mode and the one look, and no colour is written in code.
//
// A stored value is one of these names or a hex colour (`#2563eb`). A hex
// comes from a body that another tool wrote. The editor checks the value
// against the list or the hex pattern before it reaches a style. Any other
// value is not a stored form, and the fragment stays raw text.

/** One colour that a picker offers. */
export interface ColorChoice {
  /** The stored value, in `data-color`. */
  name: string;
  /** The words of the swatch. */
  label: string;
}

/**
 * The text colours, in the order the picker shows them. Each name maps to a
 * slot of the categorical ramp in `globals.css`, and gray to the muted ink.
 * ⚠️ Never rename one: a stored body holds the name.
 */
export const TEXT_COLORS: readonly ColorChoice[] = [
  { name: "gray", label: "Gray" },
  { name: "blue", label: "Blue" },
  { name: "green", label: "Green" },
  { name: "purple", label: "Purple" },
  { name: "red", label: "Red" },
  { name: "yellow", label: "Yellow" },
  { name: "orange", label: "Orange" },
  { name: "pink", label: "Pink" },
  { name: "teal", label: "Teal" },
];

/** The highlight colours. The same names, drawn as a tint behind the text. */
export const HIGHLIGHT_COLORS: readonly ColorChoice[] = [
  { name: "yellow", label: "Yellow" },
  { name: "green", label: "Green" },
  { name: "blue", label: "Blue" },
  { name: "purple", label: "Purple" },
  { name: "red", label: "Red" },
  { name: "orange", label: "Orange" },
  { name: "pink", label: "Pink" },
  { name: "teal", label: "Teal" },
  { name: "gray", label: "Gray" },
];

const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

/** True for `#abc` and `#aabbcc`, in either case. */
export function isHexColor(value: unknown): value is string {
  return typeof value === "string" && HEX.test(value);
}

/** True for a value that a text colour may store. */
export function isTextColor(value: unknown): value is string {
  return isHexColor(value) || TEXT_COLORS.some((c) => c.name === value);
}

/** True for a value that a highlight may store. */
export function isHighlightColor(value: unknown): value is string {
  return isHexColor(value) || HIGHLIGHT_COLORS.some((c) => c.name === value);
}

/**
 * The style of a stored hex colour, or `null` for a name. A name draws
 * through its token in `globals.css`. Only a checked hex reaches a style.
 */
export function hexStyle(property: "color" | "background-color", value: unknown): string | null {
  return isHexColor(value) ? `${property}: ${value}` : null;
}

/** The words of a stored colour, for a label: its name, or the hex value. */
export function colorLabel(choices: readonly ColorChoice[], value: string | null | undefined): string {
  if (!value) return "Default";
  return choices.find((c) => c.name === value)?.label ?? value;
}
