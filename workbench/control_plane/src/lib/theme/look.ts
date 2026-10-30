/**
 * The look gate (D141, `project-docs/specs/glass_look.md` §4).
 *
 * Metorite carries two looks for a bounded time: **classic**, the look in
 * production, and **glass**, Aroh's design language. The gate decides
 * whether glass exists in this build at all.
 *
 *   NEXT_PUBLIC_UI_LOOK=glass   glass is the default, and Settings →
 *                               Appearance shows a Design switch
 *   unset, or any other value   classic, and nothing about glass runs
 *
 * ⚠️ Read at BUILD time. Next.js inlines `process.env.NEXT_PUBLIC_UI_LOOK`
 * only where the full name is written out, so keep it written out here.
 * Staging carries the line in `workbench/control_plane/.env.local`.
 * Production carries NO line until the owner says so (GL-9, OWNER-GATE).
 * Never write `NEXT_PUBLIC_UI_LOOK=classic` on production: the nightly
 * staging refresh copies production's NEXT_PUBLIC keys onto staging, and
 * that line would turn glass off there.
 *
 * Glass applies through ONE attribute on <html>. The server renders it when
 * the gate is on, so the default never flashes. The boot script removes it
 * before paint for a member who chose Classic. Every glass rule in
 * `globals.css` and every `glass:` class keys off it.
 *
 * Fence: `glassLook.test.ts`.
 */

export type Look = "classic" | "glass";

/** The attribute on <html> that carries the look. */
export const LOOK_ATTRIBUTE = "data-look";

/** The attribute's one value. Classic is the attribute's absence. */
export const GLASS_LOOK = "glass";

/** True when this build carries glass (staging, until GL-9). */
export function glassGateOn(): boolean {
  return process.env.NEXT_PUBLIC_UI_LOOK === GLASS_LOOK;
}

/**
 * The look in force for a member's stored choice. With the gate off this is
 * always classic, whatever the browser stored on another deploy.
 */
export function resolveLook(stored: string | null): Look {
  if (!glassGateOn()) return "classic";
  return stored === "classic" ? "classic" : "glass";
}

/** Put a look on the document. Classic is the attribute's absence. */
export function applyLook(look: Look): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (look === "glass") root.setAttribute(LOOK_ATTRIBUTE, GLASS_LOOK);
  else root.removeAttribute(LOOK_ATTRIBUTE);
}
