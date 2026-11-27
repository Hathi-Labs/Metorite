/**
 * The surface of a chat turn: the app that the member is in (WS-67 OA-7d).
 *
 * The dock sends the app slug of the pane (`/email` gives `email`) as the
 * `surface` field of the chat request. The gateway reads it as a HINT. It can
 * add the core tool pack of that app and nothing else
 * (`one_assistant_context.md` §4.2.2, layer (a), input 2). It never carries a
 * tenant or an identity. The server checks the value again, so this module is
 * a courtesy and not a gate.
 *
 * Pure on purpose: the route, the hook and the dock share it, and the Vitest
 * fence reads it without a browser.
 */

/** The longest slug that goes on the wire. The server holds the same bound. */
export const SURFACE_MAX = 32;

const SLUG = /^[a-z][a-z0-9-]*$/;

/** *raw* as a surface slug, or null. A slug is lower-case letters, digits and "-". */
export function cleanSurface(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const slug = raw.trim().toLowerCase();
  if (!slug || slug.length > SURFACE_MAX || !SLUG.test(slug)) return null;
  return slug;
}

/** The surface of a pane: the first segment of its `href`, or null at "/". */
export function surfaceOfPane(here: { href: string } | null | undefined): string | null {
  if (!here) return null;
  const first = here.href.split(/[?#]/)[0].split("/").filter(Boolean)[0];
  return cleanSurface(first);
}

/** The request-body field for a surface: `{ surface }`, or nothing. */
export function surfaceField(surface: string | null | undefined): { surface?: string } {
  const slug = cleanSurface(surface);
  return slug ? { surface: slug } : {};
}
