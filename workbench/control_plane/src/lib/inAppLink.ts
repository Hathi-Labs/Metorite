/**
 * The two decisions of a link in chat text (review round 1, P2-a and P2-b).
 *
 * 1. **An in-app link announces each click.** `router.push` of the URL the
 *    page already shows changes nothing, so the second click on the same
 *    email link in the email chat did nothing at all (P2-b). The chat link
 *    sends {@link IN_APP_LINK_EVENT} on EACH activation, before it pushes,
 *    and a page that owns the path acts on it (`EmailDeepLink.tsx`).
 * 2. **A link to another site says so.** A mail can carry a subject such as
 *    `[Open in inbox](https://evil.example)`, and a model that writes the
 *    subject into its answer plants that link (P2-a). So the chat draws an
 *    external link with an external-link icon and its host
 *    ({@link externalHost}), and the member sees where it goes.
 *
 * Pure, so node-env vitest can hold it. Fence: `inAppLink.test.ts`.
 */

/** The event each activation of an in-app chat link sends. */
export const IN_APP_LINK_EVENT = "cc-in-app-link";

/** Send {@link IN_APP_LINK_EVENT} for one activation of an in-app link. */
export function announceInAppLink(href: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(IN_APP_LINK_EVENT, { detail: href }));
}

/** The href that an {@link IN_APP_LINK_EVENT} carries, or `null`. */
export function announcedHref(event: Event): string | null {
  const detail = (event as CustomEvent<unknown>).detail;
  return typeof detail === "string" ? detail : null;
}

/** The origin a link is read against on the server, where no page exists. */
const SERVER_ORIGIN = "https://in-app.invalid";

/**
 * The host of a link that leaves the app, else `null`.
 *
 * The href is resolved against the app's own origin, as the browser will
 * resolve it on the click. So a scheme-less form leaves the app too:
 * `//evil.example`, `/\evil.example` and `\\evil.example` all reach
 * `evil.example`. The first build parsed with `new URL(href)` alone, which
 * throws on each of them, so they drew as plain links and went off-site
 * (review round 2, P2). A link that resolves to the app's origin, such as
 * `foo`, is not external. Only `http(s)` counts.
 *
 * `origin` is the app's origin. It defaults to the page's own, and on the
 * server to a fixed origin no link can name.
 */
export function externalHost(href: string | undefined, origin?: string): string | null {
  if (!href) return null;
  const base = origin
    ?? (typeof window !== "undefined" ? window.location.origin : SERVER_ORIGIN);
  let url: URL;
  try {
    url = new URL(href, base);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.origin === new URL(base).origin) return null;
  return url.hostname || null;
}
