/**
 * The path guard for every call from this app to the gateway.
 *
 * A route such as `/api/projects/[...path]` builds
 * `${GATEWAY_URL}/projects/${path.join("/")}` and sends it with the internal
 * Bearer token. A segment of `..` makes the URL resolve OUTSIDE `/projects/`,
 * to any gateway route: `new URL(".../projects/../internal/drain")` is
 * `/internal/drain`. Three facts make the forms many:
 *
 * - Next decodes each route segment once, so `%2e%2e` arrives as `..`, and
 *   `%3F` arrives as `?`.
 * - The URL parser also reads a literal `%2e` as a dot.
 * - The URL parser REMOVES every tab, LF and CR before it parses, so
 *   `.\t.` becomes `..` (diff review, 2026-10-08).
 *
 * Two layers, so a route that forgets the first still meets the second:
 *
 * 1. `refuseUnsafePath` in each catch-all proxy, right after it reads
 *    `params`. It answers 400.
 * 2. `gatewayUrlEscape` inside `gatewayFetch`, the one fetch to the gateway.
 *    It refuses a path that can leave its PREFIX, read twice: as the URL
 *    parser reads it, and once decoded, as uvicorn gives it to Starlette.
 *    The second read matters because Starlette's trailing-slash redirect
 *    writes its Location from the decoded path, and `fetch` follows it with
 *    the token (verifier, 2026-10-08). So `%2F..` is refused, and
 *    `openai%2Fgpt-4o`, a model id the settings page sends, is not.
 *
 * ⚠️ What this does NOT stop. A single param such as `[sessionId]` that holds
 * a decoded `?`, `#` or `/` can still move the request to another route UNDER
 * the same prefix: an ancestor when it cuts off the fixed suffix, a
 * descendant when it adds a segment. It cannot leave the prefix, because that
 * takes a dot segment. Every such route still runs as the member, so the
 * gateway's own checks apply. HANDOFF H-278 holds the per-param fix.
 *
 * Fence: `gatewayPath.test.ts` and `gatewayFetch.test.ts`.
 */
import { NextResponse } from "next/server";

/** A dot segment, in plain or percent-encoded form, any case. */
const DOT_SEGMENT = /^(?:\.|%2e){1,2}$/i;

/** A C0 control character or DEL. The parser drops some and keeps others. */
const CONTROL = /[\u0000-\u001f\u007f]/;

/**
 * Why `segments` could leave the prefix they are joined under, or `null` when
 * they cannot. An absent list (an optional catch-all at its root) is safe.
 */
export function unsafeGatewayPath(segments: readonly string[] | undefined): string | null {
  for (const seg of segments ?? []) {
    if (!seg) return "an empty segment";
    if (CONTROL.test(seg)) return "a control character";
    if (DOT_SEGMENT.test(seg)) return "a dot segment";
    if (/[/\\]/.test(seg)) return "a slash inside a segment";
    if (/%2f|%5c/i.test(seg)) return "an encoded slash inside a segment";
    if (/[?#]/.test(seg)) return "a query or fragment mark inside a segment";
  }
  return null;
}

/**
 * A 400 for a path that could leave its prefix, or `null` to go on. Call it
 * first, right after the handler reads its `params`.
 */
export function refuseUnsafePath(segments: readonly string[] | undefined): NextResponse | null {
  const why = unsafeGatewayPath(segments);
  if (why === null) return null;
  return NextResponse.json({ detail: `Invalid path: ${why}.` }, { status: 400 });
}

/**
 * Why the absolute URL string `raw` has a path that the parser will rewrite,
 * or `null` when its path is taken as written. The query and the fragment are
 * not checked: a `..` there is data, not a path.
 *
 * It takes the path, from the end of the authority to the first `?` or `#`,
 * and refuses a control character (the parser removes tab, LF and CR, which
 * can join `.\t.` into `..`), a backslash (a slash for `http:`), or a dot
 * segment. Then it decodes the path once, as uvicorn does, and refuses the
 * same three again.
 */
export function gatewayUrlEscape(raw: string): string | null {
  const authority = /^[a-z][a-z0-9+.-]*:\/\/[^/?#\\]*/i.exec(raw);
  const rest = authority ? raw.slice(authority[0].length) : raw;
  const path = rest.split(/[?#]/, 1)[0];
  const asParsed = pathEscape(path);
  if (asParsed) return `${asParsed} in the path`;
  const asDecoded = pathEscape(decodeOnce(path));
  if (asDecoded) return `${asDecoded} in the decoded path`;
  return null;
}

function pathEscape(path: string): string | null {
  if (CONTROL.test(path)) return "a control character";
  if (path.includes("\\")) return "a backslash";
  if (path.split("/").some((seg) => DOT_SEGMENT.test(seg))) return "a dot segment";
  return null;
}

/** One percent-decode of each `%XX`, as uvicorn's `unquote` does. Bytes, not UTF-8. */
function decodeOnce(path: string): string {
  return path.replace(/%([0-9a-f]{2})/gi, (_m, hex: string) => String.fromCharCode(parseInt(hex, 16)));
}
