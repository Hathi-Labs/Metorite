/**
 * The query of a failed mailbox connect, for the callback page.
 *
 * Spec: project-docs/specs/email_app_master_plan.md §12.3.9 (WS-17 EM-G7,
 * GM-24, scope item E-C1).
 *
 * Each bounce names its provider, so the callback page never takes Microsoft
 * for a Gmail try. Both BFF routes beside this file (`[provider]/authorize`
 * and `[provider]/callback`) build their failure query here, and the gateway
 * callback does the same in `transport/oauth.py`.
 *
 * ⚠️ The provider comes from the PATH, which is request input. Only the two
 * known values ride on the bounce. Any other segment is dropped, never
 * echoed, so a crafted URL cannot write its own text into the page.
 */

/** The providers a bounce may name. Nothing else reaches the Location. */
export const BOUNCE_PROVIDERS: ReadonlySet<string> = new Set(["gmail", "microsoft"]);

/**
 * `error=<reason>`, plus `provider=<provider>` when the provider is known.
 *
 * Every caller passes the path segment as it came. This function alone
 * decides whether it rides on the bounce.
 */
export function bounceQuery(reason: string, provider: string): string {
  const query = new URLSearchParams({ error: reason });
  if (BOUNCE_PROVIDERS.has(provider)) query.set("provider", provider);
  return query.toString();
}
