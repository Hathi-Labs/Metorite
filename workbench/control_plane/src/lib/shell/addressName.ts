/**
 * The name My Day greets (WS-63 PZ-4, `personalization.md` §5.2).
 *
 * With `NEXT_PUBLIC_ASSISTANT_PROFILE` on, My Day asks the gateway for the
 * form of address: the profile name, then the People preferred name, then
 * the first word of the display name. The assistant uses the same order, so
 * the page and the chat call the member the same name. While the read is
 * out, after a fault, with the flag off, or when the answer is no name, the
 * page keeps the session name and `firstName`, as before PZ-4.
 *
 * Fence: `addressName.test.ts`.
 */
import { greeting, greetingLine } from "./myDay";

/** The cache key of the read. */
export const ADDRESS_KEY = "/api/auth/me/assistant-profile/address";

/** The name of the answer, or `""`. A fault throws, so the cache keeps no wrong name. */
export async function fetchAddressName(fetcher: typeof fetch = fetch): Promise<string> {
  const res = await fetcher(ADDRESS_KEY, { cache: "no-store" });
  if (!res.ok) throw new Error(`address ${res.status}`);
  const body: unknown = await res.json();
  const name = body && typeof body === "object" ? (body as { name?: unknown }).name : undefined;
  return typeof name === "string" ? name.trim() : "";
}

/**
 * "Good morning, VJ" with a fetched name. With none, `greetingLine` on the
 * session name. A fetched name is whole, because the member chose it.
 */
export function addressedGreetingLine(
  now: Date,
  fetched: string | null | undefined,
  sessionName: string | null | undefined,
): string {
  const name = (fetched ?? "").trim();
  return name ? `${greeting(now.getHours())}, ${name}` : greetingLine(now, sessionName);
}
