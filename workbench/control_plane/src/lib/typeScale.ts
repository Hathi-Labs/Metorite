/**
 * The type scale, as tokens for an inline style.
 *
 * Every step is in `rem`, so it follows the member's density. `--ui-scale`
 * sets the root font size (`globals.css`), and a `rem` reads it. A `px` value
 * does not. The generative-UI templates drew their text at `fontSize: 11`,
 * `12`, `13` and `14`, so at compact density they kept their size while the
 * chat around them got smaller (follow-up of #716 and #735).
 *
 * The steps are the Tailwind scale that the classes use: `xs` is `text-xs`,
 * `sm` is `text-sm`, and so on. `caption` and `micro` are the two small steps
 * of `AGENTS.md` (`text-[11px]` and `text-[10px]`), written in `rem`.
 *
 * Use a class where the element takes one. Use this map in a `style` object,
 * where a class cannot reach. Fence: `src/lib/typeScale.test.ts`, which also
 * fails on a `px` font size in a template file.
 */

export const TYPE = {
  /** 10px at the default density. */
  micro: "0.625rem",
  /** 11px at the default density. */
  caption: "0.6875rem",
  /** `text-xs`, 12px. */
  xs: "0.75rem",
  /** `text-sm`, 14px. */
  sm: "0.875rem",
  /** `text-base`, 16px. */
  base: "1rem",
  /** `text-xl`, 20px. */
  xl: "1.25rem",
  /** `text-2xl`, 24px. */
  "2xl": "1.5rem",
  /** `text-4xl`, 36px. */
  "4xl": "2.25rem",
} as const;

export type TypeStep = keyof typeof TYPE;
