/**
 * The guard for one dynamic path segment that a route puts into a gateway URL.
 *
 * `encodeURIComponent` leaves `.` alone. Next decodes `%2e%2e` to `..`, and
 * the gateway URL then resolves to a sibling path. So a segment must match
 * `^[\w.:@-]+$`, must not be `.` or `..`, and must hold no `..` run. It
 * answers 400 before any fetch. Fence: `safeSegment.test.ts`.
 */
import { NextResponse } from "next/server";

const SAFE = /^[\w.:@-]+$/;

export function unsafeSegment(seg: string | undefined): boolean {
  if (!seg || !SAFE.test(seg)) return true;
  return seg === "." || seg === ".." || seg.includes("..");
}

/** A 400 for an unsafe segment, or `null` to go on. */
export function refuseBadSegment(seg: string | undefined): NextResponse | null {
  if (!unsafeSegment(seg)) return null;
  return NextResponse.json({ detail: "Invalid path segment." }, { status: 400 });
}
