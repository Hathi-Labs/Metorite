/**
 * The inline pictures of a mail body: `<img src="cid:...">` (WS-17, the
 * inline images fix, `email_app_master_plan.md` §16.6).
 *
 * A mail names a picture of its own MIME tree with a `cid:` URL. A browser
 * cannot fetch one, so the pane writes it as the URL of the gateway route
 * `GET /email/messages/{id}/inline?cid=...`, and the frame's CSP lets that
 * one path in. Pure functions, so `inlineImages.test.ts` runs them in node.
 */

/**
 * The path of the inline pictures of one message, or null with no id.
 *
 * ⚠️ It is RELATIVE on purpose. The frame resolves it against the app's
 * origin. A value that starts with `http` counts as a remote picture: the
 * remote gate would block it, and the dark look would not re-invert it
 * (`bodyLook.BLOCKED_REMOTE_CSS`), so it would draw inverted.
 */
export function inlineImagePath(messageId: string | null | undefined): string | null {
  return messageId ? `/api/email/messages/${encodeURIComponent(messageId)}/inline` : null;
}

/** A `cid:` URL, and the Content-ID that it names. A Content-ID has no space. */
const CID_RE = /^\s*cid:\s*(\S+)\s*$/i;

/** The URL that loads the picture of a `cid:` URL. Null for any other URL. */
export function inlineImageUrl(value: string, path: string | null): string | null {
  const m = CID_RE.exec(value);
  return m && path ? `${path}?cid=${encodeURIComponent(m[1])}` : null;
}

/**
 * The `img-src` directive of the frame's CSP.
 *
 * With the remote pictures blocked, the frame loads `data:` and the ONE path
 * of the inline route of this message, and nothing else of our origin. A CSP
 * path with no trailing slash matches that path exactly, and a query does not
 * count. After "Show images", the frame loads our origin, as before.
 */
export function imgSrcDirective(showImages: boolean, origin: string, path: string | null): string {
  if (showImages) return `img-src 'self' ${origin} data:;`;
  return path && origin ? `img-src data: ${origin}${path};` : "img-src data:;";
}
