/**
 * The checks that run before a value goes into a gateway path (WS-CAL CE-10c, task 66).
 *
 * Pure and free of React and of the data cache, so the BFF routes and the client share ONE copy.
 * A connection id is a UUID. A provider is one of two words. Nothing else enters a gateway URL.
 *
 * Fence: `calendarConnections.test.ts`.
 */
export type Provider = "google" | "outlook";


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** A connection id is a UUID. Nothing else goes into a gateway path. */
export function isConnectionId(v: unknown): v is string {
  return typeof v === "string" && UUID.test(v);
}

/** One of the two provider words of the gateway. */
export function isProvider(v: unknown): v is Provider {
  return v === "google" || v === "outlook";
}

/** The one host each provider may send a browser to for consent. Nothing else. */
export const CONSENT_HOST: Record<Provider, string> = {
  google: "accounts.google.com",
  outlook: "login.microsoftonline.com",
};

/**
 * The consent URL when it is safe to send a browser there, else null. It must be an `https` URL
 * on the exact host of the provider, with no user, no password and no port.
 */
export function safeConsentUrl(provider: Provider, raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 4096) return null;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" || url.hostname !== CONSENT_HOST[provider]) return null;
  if (url.username || url.password || url.port) return null;
  return url.toString();
}
