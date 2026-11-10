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

/** A backslash, or a control mark such as a tab, CR or LF. */
const OFF_PATH_MARKS = /[\\\u0000-\u001f\u007f]/;

/**
 * True for a path inside this app: `/projects?task=…`. Never `//host`, which
 * a browser reads as another site, and never `/api/…`, which is a file or a
 * JSON answer and not a page. The chat, the entity pill and the My Day feed
 * share this one rule (moved here from `EntityPill.tsx` in MD-2c fix round 1).
 *
 * The prefix test alone is not enough (S9 fix round 1). A URL parser strips a
 * tab or a newline and reads `\` as `/`, so `/\evil.com` and `/<tab>/evil.com`
 * both open another site. So the path must also resolve, against a fixed base,
 * to that same base: the check the browser itself will make, run here first.
 *
 * A backslash and each control mark are refused outright too (MD-2c fix
 * round 1). No page of this app has one, and each parser reads them in its
 * own way. Fences: `EntityPill.test.ts`, `inAppLink.test.ts` and
 * `feedHandled.test.ts`.
 *
 * A dot segment is refused too (review of #1197, P0): `router.push` resolves
 * `/projects/../crm` to `/crm`, so a prefix check on the raw path would let a
 * path walk out of its pane. Each segment is decoded once, and must not be
 * `.`, `..`, empty, or hold a slash, a backslash, a control mark or another
 * percent ({@link pathSegments}). The server's `is_in_app_path` is the twin,
 * and `inAppPaths.cases.json` is the one table both read.
 */
export function isInAppPath(href: string | undefined): boolean {
  return inAppPathname(href) !== null;
}

/** First segments that are never a page, on either side of the twin. */
const NEVER_A_PAGE = new Set(["api", "_next"]);

/**
 * The decoded segments of a raw path, or `null` when one is not a page's.
 * The root gives `[]`. The same rule as `path_segments` in
 * `orchestrator/in_app_path.py`.
 */
export function pathSegments(pathname: string): string[] | null {
  if (pathname === "/") return [];
  const out: string[] = [];
  for (const raw of pathname.split("/").slice(1)) {
    if (raw === "") return null;
    let seg: string;
    try {
      seg = decodeURIComponent(raw);
    } catch {
      return null;
    }
    if (seg.includes("%") || seg.includes("/") || OFF_PATH_MARKS.test(seg)) return null;
    if (seg === "." || seg === "..") return null;
    out.push(seg);
  }
  return out;
}

/**
 * The decoded pathname of an in-app href (`/projects` for
 * `/projects?task=42`), or `null` when {@link isInAppPath} would refuse it.
 * The one assistant matches it against the member's panes (`navigateAct`).
 */
export function inAppPathname(href: string | undefined): string | null {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return null;
  if (OFF_PATH_MARKS.test(href)) return null;
  let url: URL;
  try {
    url = new URL(href, SERVER_ORIGIN);
  } catch {
    return null;
  }
  if (url.origin !== SERVER_ORIGIN) return null;
  // The RAW path part, before the parser folds a dot segment away.
  const segments = pathSegments(href.split(/[?#]/)[0]);
  if (segments === null) return null;
  if (segments.length && NEVER_A_PAGE.has(segments[0])) return null;
  return "/" + segments.join("/");
}
