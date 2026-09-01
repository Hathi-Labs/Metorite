// Categorical hues — identity, not measurement.
//
// ⚠️ **This completes `tone.ts`, it does not compete with it.** A tone answers
// "how broken" and its hue carries information. A category answers "which one"
// and its hue carries only identity: a provider is not better or worse than
// another provider, it is merely a different one.
//
// ⚠️ **This is `control_plane/src/lib/categorical.ts`'s rule, re-expressed.**
// It is not an import — `customer_console.md` §2.4 measures the cross-import
// count from the customer workbench as ZERO.
//
// 🔴 **What was NOT ported: the old `PROVIDER_COLOURS` table.** The deleted
// customer-side model picker carried a hand-written map of raw Tailwind palette
// classes (`bg-blue-500/10 text-blue-600 border-blue-500/30`). Two reasons it
// could not come across:
//
//   1. `AGENTS.md` rule 7 forbids raw palette classes by name, and deleting
//      that file is what let the conformance baseline drop in PR #145. Copying
//      it here would re-import debt the customer app just paid off.
//   2. This app has no Tailwind at all, so the strings would resolve to
//      nothing and every provider would render unstyled.
//
// Hashing the NAME rather than reading a table also means a provider nobody
// anticipated still gets a colour, and always the same one.

/** Eight slots, defined in `globals.css` for both themes. */
export const CAT_SLOTS = 8;

/** Stable across processes and platforms, unlike anything built on hashCode.
 *
 * ⚠️ **Hash the name, never the array index.** Index assignment repaints every
 * existing item the moment somebody inserts one, and the repaint is silent —
 * yesterday's screenshot and today's page disagree with no code change in
 * between. */
export function slotFor(name: string): number {
  const s = (name || "").trim().toLowerCase();
  if (!s) return 0;
  // FNV-1a, 32-bit. Chosen because it is short enough to read and has no
  // pathological clustering on the short lowercase strings we key on.
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return Math.abs(h) % CAT_SLOTS;
}

/** The class a categorical chip wears. `globals.css` draws `.cat-0`…`.cat-7`. */
export function categoricalChip(name: string): string {
  return `chip cat-${slotFor(name)}`;
}

/** A single glyph standing in for a provider.
 *
 * ⚠️ Deliberately a LETTER, not an emoji. The deleted table used 🐋 for
 * deepseek and 🦙 for ollama, and emoji render at different sizes on Windows,
 * macOS and Linux — in a table of chips that produces visibly ragged rows. A
 * letter inherits the font and the colour comes from the slot. */
export function providerGlyph(name: string): string {
  const s = (name || "").trim();
  return s ? s[0].toUpperCase() : "?";
}

/** A solid monogram box for an ORGANIZATION (mockup adoption, 2026-08-30).
 *
 * Same slot, same stability rule as the chip — one name, one colour,
 * everywhere. The box form reads at roster density where a full chip
 * would shout; the letter comes from `providerGlyph`, which is a
 * first-letter rule and not provider-specific despite its name. */
export function categoricalBox(name: string): string {
  return `orgglyph cat-${slotFor(name)}`;
}
