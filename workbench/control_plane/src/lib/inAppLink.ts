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

/** The host of an `http(s)` link to another site, else `null`. */
export function externalHost(href: string | undefined): string | null {
  if (!href) return null;
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return url.hostname || null;
}
