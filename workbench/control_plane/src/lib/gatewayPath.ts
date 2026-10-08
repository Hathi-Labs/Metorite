/**
 * The path guard for every catch-all proxy to the gateway.
 *
 * A route such as `/api/projects/[...path]` builds
 * `${GATEWAY_URL}/projects/${path.join("/")}` and sends it with the internal
 * Bearer token. A segment of `..` makes the URL resolve OUTSIDE `/projects/`,
 * to any gateway route: `new URL(".../projects/../internal/drain")` is
 * `/internal/drain`. Next decodes `%2e%2e` in a segment to `..`, and the URL
 * parser also treats a literal `%2e%2e` as `..`, so both forms must be refused.
 *
 * The email and WhatsApp proxies had their own copy of this guard. Nine others
 * had none (verifier of the gateway drain, 2026-10-08). This is the one copy.
 *
 * Fence: `gatewayPath.test.ts` proves the rules, and it fails when a catch-all
 * route that joins its segments does not call `refuseUnsafePath`.
 */
import { NextResponse } from "next/server";

/** A dot segment, in plain or percent-encoded form, any case. */
const DOT_SEGMENT = /^(?:\.|%2e){1,2}$/i;

/**
 * Why `segments` could leave the prefix they are joined under, or `null` when
 * they cannot. An absent list (an optional catch-all at its root) is safe.
 */
export function unsafeGatewayPath(segments: readonly string[] | undefined): string | null {
  for (const seg of segments ?? []) {
    if (!seg) return "an empty segment";
    if (DOT_SEGMENT.test(seg)) return "a dot segment";
    if (/[/\\]/.test(seg)) return "a slash inside a segment";
    if (/%2f|%5c/i.test(seg)) return "an encoded slash inside a segment";
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
