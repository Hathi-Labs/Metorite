/**
 * The glass card language for the domain tool cards (GL-10a, D143,
 * `glass_look.md` GL-4). One place holds the class strings, so an email
 * card, a task card and a project card draw one card.
 *
 * Every token sits behind `glass:`, so a classic page matches none of them
 * and its markup stays byte for byte what it was. Copied from
 * `ToolCardShell` and `ConfirmationCard`. The literals are whole strings,
 * because Tailwind reads class names from the source text.
 *
 * ⚠️ Never the chrome or menu material on a card: a card may hold a
 * non-portalled `position: fixed` child. A card takes the surface utility.
 */

/** A settled card: a 22 px surface with a hairline and no border. */
export const G_SETTLED = "glass:rounded-[16px] glass:border-transparent glass:glass-raised glass:p-4";

/**
 * A card that failed, or that wants the member's care. It keeps the tint
 * its classic tone gives it, and takes the glass corner and padding.
 */
export const G_TINTED = "glass:rounded-[16px] glass:p-4";

/** An inner group on a card: a rounded block on the secondary fill. */
export const G_INNER = "glass:rounded-[10px] glass:border-transparent glass:bg-secondary";

/**
 * The glass frame for a tone string such as `toneFor()` returns: a failed
 * or a guarded tone is tinted, and every other tone is settled.
 */
export function glassToneFrame(tone: string): string {
  return /destructive|warning/.test(tone) ? G_TINTED : G_SETTLED;
}
