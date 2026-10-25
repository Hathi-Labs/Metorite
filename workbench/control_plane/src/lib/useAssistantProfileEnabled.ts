/**
 * Is the assistant profile on for this member's organization? (WS-63 PZ-25,
 * `personalization.md` §14.2, D113.)
 *
 * Two switches must both say yes:
 *
 * 1. The build switch, `NEXT_PUBLIC_ASSISTANT_PROFILE` (`assistantProfileFlag.ts`).
 * 2. The gateway's answer for the member's organization: `enabled` in the
 *    body of `GET /api/auth/me/assistant-profile`. The gateway reads the
 *    master switch and `ASSISTANT_PROFILE_ORGS` for the org of the session.
 *
 * It fails closed. While the read is out, after a fault, or with any value
 * but `true`, the answer is false, so the page, the account row and the My
 * Day name show nothing. The read goes through the data cache, so the three
 * places that ask share one request.
 *
 * Fence: `useAssistantProfileEnabled.test.ts`.
 */
import { useSyncExternalStore } from "react";

import { assistantProfileOn } from "./assistantProfileFlag";
import { useCachedResource } from "./useCachedResource";

/** The BFF route of the profile. Its GET answer carries `enabled`. */
export const PROFILE_URL = "/api/auth/me/assistant-profile";
/** The cache key of the read. Not the URL, so no other read shares the entry. */
export const PROFILE_ENABLED_KEY = "assistant-profile:enabled";

/** `enabled` of the answer. A fault throws, so the cache keeps no wrong yes. */
export async function fetchProfileEnabled(fetcher: typeof fetch = fetch): Promise<boolean> {
  const res = await fetcher(PROFILE_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`assistant profile ${res.status}`);
  const body: unknown = await res.json();
  return !!body && typeof body === "object" && (body as { enabled?: unknown }).enabled === true;
}

/** What the web app knows: on, off, still asking, or the read failed. */
export type ProfileState = "on" | "off" | "pending" | "error";

/**
 * The pure rule. "on" needs the build switch AND a `true` from the gateway.
 * A fault is "error", also over an earlier yes, so no caller keeps a stale
 * yes. "pending" is a read that is still out with no answer yet. A caller
 * that only hides treats each state but "on" as hidden
 * (`useAssistantProfileEnabled`). The page shows "error" with Retry.
 */
export function profileState(
  built: boolean,
  read: { data: boolean | undefined; loading: boolean; error: string | null },
): ProfileState {
  if (built !== true) return "off";
  if (read.error) return "error";
  if (read.data === true) return "on";
  return read.data === undefined && read.loading ? "pending" : "off";
}

const noSubscribe = () => () => {};

/** The state of the profile for this build and this organization, and a retry. */
export function useAssistantProfileRead(): { state: ProfileState; refresh: () => void } {
  // The server render reads the build value only. A dev override takes
  // effect after hydration, with no mismatch (as `MyDayPage` reads it).
  const built = useSyncExternalStore(noSubscribe, assistantProfileOn, () => false);
  const read = useCachedResource<boolean>(built ? PROFILE_ENABLED_KEY : null, () => fetchProfileEnabled());
  return { state: profileState(built, read), refresh: read.refresh };
}

/** The state alone, for a caller that does not retry. */
export function useAssistantProfileState(): ProfileState {
  return useAssistantProfileRead().state;
}

/** True only when the profile is on for this build AND this organization. */
export function useAssistantProfileEnabled(): boolean {
  return useAssistantProfileState() === "on";
}
