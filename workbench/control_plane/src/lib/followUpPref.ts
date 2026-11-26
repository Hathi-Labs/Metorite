/**
 * Does the chat show the "Follow up" segment under an answer? (task 4002)
 *
 * The member's setting is `apps.chat.follow_up_suggestions` of the assistant
 * profile (`APP_PREFERENCES`), OFF by default. The gateway stores it and
 * answers it in `GET /api/auth/me/assistant-profile`, for each organization,
 * with the profile flag on or off.
 *
 * With the setting off, the chat draws no label and no pill, and it sends no
 * request to `/api/chat/suggestions`. Only a stored `true` turns it on. While
 * the first read is out, and after a failed first read, it is off too, so a
 * member who did not turn it on never sees it and never pays for a model call.
 *
 * Fence: `followUpPref.test.ts` and `e2e/chat-starter-pills.spec.ts`.
 */
import { PROFILE_URL } from "./useAssistantProfileEnabled";
import { useCachedResource } from "./useCachedResource";

/** The cache key of the read. The save in Settings drops it. */
export const FOLLOW_UPS_KEY = "assistant-profile:follow-ups";

/** The setting in a GET answer of the profile. Only a stored `true` shows. */
export function followUpsOn(raw: unknown): boolean {
  const body = (raw && typeof raw === "object" ? raw : {}) as {
    apps?: { chat?: { follow_up_suggestions?: unknown } };
  };
  return body.apps?.chat?.follow_up_suggestions === true;
}

/** Read the setting. A fault throws, so the cache keeps no guess. */
export async function fetchFollowUpsOn(fetcher: typeof fetch = fetch): Promise<boolean> {
  const res = await fetcher(PROFILE_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`assistant profile ${res.status}`);
  return followUpsOn(await res.json());
}

/** The follow-up pills that a model wrote for the last exchange, or none. */
export async function requestFollowUps(
  on: boolean,
  exchange: { userMessage: string; assistantMessage: string; agentName: string },
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<string[]> {
  if (!on) return [];
  const res = await fetcher("/api/chat/suggestions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(exchange),
    signal,
  });
  const body = (await res.json()) as { suggestions?: unknown };
  return Array.isArray(body.suggestions) ? (body.suggestions as string[]) : [];
}

/**
 * The member's setting, through the data cache. A known answer stays, also
 * over a later fault. With no answer yet, it is off.
 */
export function useFollowUps(): boolean {
  return useCachedResource<boolean>(FOLLOW_UPS_KEY, () => fetchFollowUpsOn()).data === true;
}
