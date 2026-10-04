/**
 * Untrusted HTML — the one DOMPurify policy for HTML the app did not write.
 *
 * Two consumers:
 *  - The email reading pane (`MessageContent.tsx`) draws a mail body inside a
 *    sandboxed iframe with a CSP. It uses `UNTRUSTED_FORBID_TAGS` and
 *    `UNTRUSTED_FORBID_ATTR` and keeps `<style>`, because the frame isolates it.
 *  - The `.docx` viewer (`ArtifactViewerModal.tsx`) draws mammoth's HTML
 *    INLINE, in the app's own document, through `dangerouslySetInnerHTML`.
 *    So `sanitizeDocxHtml` takes the same base and forbids more.
 *
 * THE THREAT for the docx path. An emailed `.docx` attachment, or one an agent
 * wrote, opens in the viewer. `dangerouslySetInnerHTML` skips React's own URL
 * check, so a docx hyperlink to `javascript:…` ran in the app's origin on a
 * click. A remote image would load on open and tell the sender who read it.
 *
 * THE RULE. DOMPurify removes scripts, handlers, `javascript:` URLs, forms,
 * frames, styles and SVG. An image whose src would leave the app's origin
 * (`remoteHost`, the same rule as `lib/markdownMedia.ts`) loses its src, and
 * its alt text says which host it was not loaded from. A `data:` image stays.
 * Every link opens in a new tab with `noopener`.
 *
 * DOMPurify needs a DOM, so this runs in the browser only. Fence:
 * `e2e/untrusted-html.spec.ts`, which bundles this file and runs it in
 * Chromium, and drives the real viewer. `src/lib/htmlSinks.test.ts` keeps the
 * list of every raw-HTML sink in the app.
 */

import DOMPurify from "dompurify";
import { remoteHost } from "@/lib/markdownMedia";

/** Tags no untrusted HTML keeps, in any viewer. */
export const UNTRUSTED_FORBID_TAGS = [
  "script", "iframe", "object", "embed", "form", "base", "meta", "link",
];

/** Attributes no untrusted HTML keeps, in any viewer. */
export const UNTRUSTED_FORBID_ATTR = ["ping"];

/** More, for HTML drawn inline in the app's own document. A `style` element
 *  or attribute can restyle the app or fetch with `url()`. Form controls draw
 *  a phish with no script. SVG and media elements can fetch on render. */
const INLINE_FORBID_TAGS = [
  ...UNTRUSTED_FORBID_TAGS,
  "style", "template", "frame", "frameset", "portal", "applet",
  "input", "button", "select", "textarea", "option", "optgroup", "datalist",
  "output", "label", "fieldset", "legend",
  "svg", "math", "video", "audio", "source", "track", "picture",
];

const INLINE_FORBID_ATTR = [
  ...UNTRUSTED_FORBID_ATTR,
  "style", "srcset", "poster", "background", "action", "formaction", "autofocus",
];

export interface SanitizedDocx {
  html: string;
  /** How many remote images lost their src. */
  blockedImages: number;
}

/** Sanitise mammoth's HTML for the inline `.docx` viewer. Browser only. */
export function sanitizeDocxHtml(raw: string): SanitizedDocx {
  let blockedImages = 0;
  const hook = "afterSanitizeAttributes";
  DOMPurify.addHook(hook, (node) => {
    const el = node as Element;
    if (el.tagName === "A") {
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer nofollow");
    }
    if (el.tagName === "IMG") {
      const src = el.getAttribute("src") ?? "";
      const host = src ? remoteHost(src) : null;
      if (host) {
        blockedImages += 1;
        el.removeAttribute("src");
        const alt = el.getAttribute("alt")?.trim();
        el.setAttribute(
          "alt",
          `${alt ? `${alt}: ` : ""}image from ${host}, not loaded`,
        );
      }
    }
  });
  try {
    const html = DOMPurify.sanitize(raw, {
      FORBID_TAGS: INLINE_FORBID_TAGS,
      FORBID_ATTR: INLINE_FORBID_ATTR,
      ADD_ATTR: ["target"],
      ALLOW_DATA_ATTR: false,
      WHOLE_DOCUMENT: false,
    });
    return { html, blockedImages };
  } finally {
    DOMPurify.removeHook(hook);
  }
}
