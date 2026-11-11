/**
 * Is agent guidance on for this member's organization? (WS-MCP SW-31b,
 * `agent_coordination.md` SW-31.)
 *
 * The gateway's router `/agent-guidance` is dark: it answers 404 while
 * `AGENT_GUIDANCE_ORGS` does not hold the org of the session, and 403 for a
 * member who cannot open Projects. So one small read of the org history says
 * yes or no. The account menu shows its "Agents" row only on a yes.
 *
 * It fails closed. While the read is out, after a fault, or on any answer
 * but a 200, the answer is false. The read goes through the data cache, so
 * the sidebar and the account foot share one request.
 *
 * Fence: `useAgentGuidanceEnabled.test.ts`.
 */
import { useCachedResource } from "./useCachedResource";

/** The cache key of the read. Not the URL, so no other read shares the entry. */
export const GUIDANCE_ENABLED_KEY = "agent-guidance:enabled";

/** True only on a 200 from the org history. A fault throws, so the cache keeps no wrong yes. */
export async function fetchGuidanceEnabled(fetcher: typeof fetch = fetch): Promise<boolean> {
  const res = await fetcher("/api/agent-guidance/history?limit=1", { cache: "no-store" });
  if (res.status >= 500) throw new Error(`agent guidance ${res.status}`);
  return res.ok;
}

/** True only when the gateway answered that guidance is on for this org. */
export function useAgentGuidanceEnabled(): boolean {
  const read = useCachedResource<boolean>(GUIDANCE_ENABLED_KEY, () => fetchGuidanceEnabled());
  return !read.error && read.data === true;
}
