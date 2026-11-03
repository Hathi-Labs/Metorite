"use client";

/**
 * The member's saved time zone, on the page (WS-66 RX-7).
 *
 * The gateway reads a member's day from `user_settings.timezone`. This file is
 * the one client of `GET` and `PUT /api/auth/me/zone`, the route that needs a
 * signed-in member and no app feature. A member without `feature:tasks` cannot
 * reach `/tasks/settings`, and this route is how their zone is saved and read.
 *
 * The page keeps the zone in memory, so the snooze menu, the chat "Later"
 * chips and the shell's one save read it without a request each. A change of
 * member or org clears it (`onClear`). A read that fails leaves it unknown,
 * and the callers fall back to the browser's zone.
 *
 * Fence: `memberZone.test.ts`.
 */
import { useEffect, useSyncExternalStore } from "react";

import { onClear } from "@/lib/dataCache";

export const ZONE_URL = "/api/auth/me/zone";

/** undefined: not read yet. null: the read failed. A string: the saved zone. */
let saved: string | null | undefined;
let inflight: Promise<string> | null = null;
const listeners = new Set<() => void>();

function set(next: string | null | undefined): void {
  saved = next;
  for (const l of listeners) l();
}

/** The saved zone as the page knows it, or null when it is not known. */
export function knownMemberZone(): string | null {
  return saved ?? null;
}

/** The page learns a zone that was saved elsewhere (the Calendar settings). */
export function rememberMemberZone(zone: string): void {
  set(zone);
}

/** Read the saved zone. It throws when the read fails, so a caller can try again later. */
export async function readMemberZone(fetchImpl: typeof fetch = fetch): Promise<string> {
  const res = await fetchImpl(ZONE_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`zone read ${res.status}`);
  const body = (await res.json()) as { timezone?: unknown };
  if (typeof body.timezone !== "string" || !body.timezone) throw new Error("zone read: no zone");
  set(body.timezone);
  return body.timezone;
}

/** Save the zone. It throws when the save fails. */
export async function writeMemberZone(zone: string, fetchImpl: typeof fetch = fetch): Promise<void> {
  const res = await fetchImpl(ZONE_URL, {
    method: "PUT",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ timezone: zone }),
  });
  if (!res.ok) throw new Error(`zone write ${res.status}`);
  set(zone);
}

/** One read at a time. A failure marks the zone unknown and is not retried until a clear. */
function load(): Promise<string> {
  inflight ??= readMemberZone().catch((e: unknown) => {
    if (saved === undefined) set(null);
    throw e;
  });
  return inflight;
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

/**
 * The member's saved zone, live, or null while it is unknown. The first user
 * on the page starts the one read. Callers use `snoozeZone` to turn the answer
 * into a zone to draw in.
 */
export function useMemberZone(): string | null {
  const zone = useSyncExternalStore(subscribe, knownMemberZone, () => null);
  useEffect(() => {
    // After a clear the zone turns null again, and this runs again.
    if (zone === null && saved === undefined) void load().catch(() => undefined);
  }, [zone]);
  return zone;
}

/** For tests: forget the zone. */
export function __resetMemberZoneForTests(): void {
  saved = undefined;
  inflight = null;
  listeners.clear();
}

onClear(() => {
  saved = undefined;
  inflight = null;
  for (const l of listeners) l();
});
