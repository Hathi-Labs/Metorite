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

/**
 * The gateway word for the provider word of the browser path. Microsoft's word in the browser
 * path is `microsoft`, and the gateway word is `outlook`. Anything else gives null.
 */
export function gatewayProviderOf(browserWord: unknown): Provider | null {
  if (browserWord === "google") return "google";
  if (browserWord === "microsoft") return "outlook";
  return null;
}
