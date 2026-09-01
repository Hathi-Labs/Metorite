/**
 * The categorical colour vocabulary — one ramp for every app (WS-27af).
 *
 * Its sibling is `statusAccent.ts`, and the split between them is the point:
 *
 *   • A **status** means something. "Done" is green everywhere, a blocked lane
 *     is amber, and the hue is *information*. That is `statusAccent.ts`, which
 *     resolves a stored colour / category / name / position to one of six
 *     SEMANTIC tones.
 *   • A **category** means nothing. `@errands` is not more urgent than
 *     `@calls`; the only requirement is that they do not look like each other.
 *     That is this module, which maps a name to one of eight themed slots.
 *
 * Two concepts, two mechanisms, no third (AGENTS.md rules 4 and 7). Routing a
 * category through `statusAccent` would ask `resolveHue` to resolve something
 * that has no meaning to find, and it would collapse eight distinguishable
 * things onto five tones — which was the argument against tokenising them in
 * the first place.
 *
 * The hues themselves are `--cat-1` … `--cat-8`, defined per theme in
 * `lib/theme/themes.ts` and bridged into Tailwind in `app/globals.css`. This
 * file owns only the class strings and the name → slot rule.
 *
 * ## What callers must not do
 *
 *   • **Do not pick a slot by array index.** An index shifts the moment
 *     somebody inserts an item above it, repainting everything below.
 *     `hashSlot` is stable for a given name, for every user, forever.
 *   • **Do not reorder `CATEGORICAL_ACCENTS`.** Same failure, applied to every
 *     name at once, with nothing failing to announce it.
 *   • **Do not let the hue be the only signal.** Under simulated deuteranopia
 *     the eight slots collapse to about four — no eight-hue qualitative palette
 *     survives dichromacy. Pair the colour with the label it colours
 *     (DESIGN_SYSTEM §7).
 */

import { CATEGORICAL_TOKENS } from "@/lib/theme/types";

export interface CategoricalAccent {
  /** A filled tint chip — border + background + text. */
  chip: string;
  /** The solid dot, for a leading marker or a legend. */
  dot: string;
  /**
   * The hue as FOREGROUND alone, for a glyph that is drawn rather than
   * filled — a space's icon in the Projects sidebar (migration 194).
   *
   * Added here rather than sliced out of `chip` by a caller: a substring of
   * a class string is not an API, and the split would break silently the
   * day an entry gains a second `text-` utility.
   */
  text: string;
}

/**
 * One entry per ramp slot, in slot order.
 *
 * Written out literally rather than generated from `CATEGORICAL_TOKENS`
 * because Tailwind v4 finds classes by scanning source text: a template-built
 * `` `bg-cat-${n}` `` is a class that exists in this file and in no stylesheet.
 */
export const CATEGORICAL_ACCENTS: CategoricalAccent[] = [
  { chip: "border-cat-1/30 bg-cat-1/10 text-cat-1", dot: "bg-cat-1", text: "text-cat-1" },
  { chip: "border-cat-2/30 bg-cat-2/10 text-cat-2", dot: "bg-cat-2", text: "text-cat-2" },
  { chip: "border-cat-3/30 bg-cat-3/10 text-cat-3", dot: "bg-cat-3", text: "text-cat-3" },
  { chip: "border-cat-4/30 bg-cat-4/10 text-cat-4", dot: "bg-cat-4", text: "text-cat-4" },
  { chip: "border-cat-5/30 bg-cat-5/10 text-cat-5", dot: "bg-cat-5", text: "text-cat-5" },
  { chip: "border-cat-6/30 bg-cat-6/10 text-cat-6", dot: "bg-cat-6", text: "text-cat-6" },
  { chip: "border-cat-7/30 bg-cat-7/10 text-cat-7", dot: "bg-cat-7", text: "text-cat-7" },
  { chip: "border-cat-8/30 bg-cat-8/10 text-cat-8", dot: "bg-cat-8", text: "text-cat-8" },
  { chip: "border-cat-9/30 bg-cat-9/10 text-cat-9", dot: "bg-cat-9", text: "text-cat-9" },
  { chip: "border-cat-10/30 bg-cat-10/10 text-cat-10", dot: "bg-cat-10", text: "text-cat-10" },
  { chip: "border-cat-11/30 bg-cat-11/10 text-cat-11", dot: "bg-cat-11", text: "text-cat-11" },
  { chip: "border-cat-12/30 bg-cat-12/10 text-cat-12", dot: "bg-cat-12", text: "text-cat-12" },
];

/** How many slots the ramp has — the range an explicit CHOICE may use. */
export const CATEGORICAL_SLOTS = CATEGORICAL_ACCENTS.length;

/**
 * How many slots a HASH may land on — frozen at the original eight.
 *
 * ⚠️ Never raise this. `hash % HASH_SLOTS` is what makes an auto-assignment
 * stable, so changing the modulus repaints every @context, tag and label
 * that was ever hash-coloured — the silent repaint the "never reorder the
 * slots" rule exists to prevent. Slots 9–12 (2026-08-31) are reachable only
 * through an explicit choice, e.g. a space's `icon_slot`.
 */
export const HASH_SLOTS = 8;

// The literal list above and the ramp must stay the same length: `hash %
// CATEGORICAL_SLOTS` is what makes an assignment stable, so a ninth token added
// to the ramp and not here would be unreachable, while a token removed from the
// ramp and left here would emit a class with no custom property behind it —
// which resolves to nothing and takes the whole declaration with it.
if (CATEGORICAL_ACCENTS.length !== CATEGORICAL_TOKENS.length) {
  throw new Error(
    `CATEGORICAL_ACCENTS has ${CATEGORICAL_ACCENTS.length} entries but the ` +
      `ramp declares ${CATEGORICAL_TOKENS.length}. Keep them in step.`,
  );
}

/** djb2-ish string hash → stable non-negative int. */
function hash(value: string): number {
  let h = 5381;
  for (let i = 0; i < value.length; i++) h = ((h << 5) + h + value.charCodeAt(i)) >>> 0;
  return h;
}

/**
 * The slot a name hashes to — 0-based, stable, and the same on every machine.
 *
 * Trims and lower-cases first, so the same label padded differently by two
 * surfaces does not draw two colours. Callers that carry a sigil (`@home`)
 * strip it before calling; this cannot, because `#tag` and `@tag` are not
 * necessarily the same thing to their owners.
 */
export function hashSlot(name: string): number {
  return hash(name.trim().toLowerCase()) % HASH_SLOTS;
}

/** The accent for a slot. Total: any integer lands somewhere on the ramp. */
export function accentForSlot(slot: number): CategoricalAccent {
  return CATEGORICAL_ACCENTS[((slot % CATEGORICAL_SLOTS) + CATEGORICAL_SLOTS) % CATEGORICAL_SLOTS];
}

/** The accent for a name, with no per-app overrides. The common case. */
export function categoricalAccent(name: string): CategoricalAccent {
  return accentForSlot(hashSlot(name));
}
