/**
 * The checkout proxies' shared half: the purchase gate and the gateway hop.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 8 and its
 * B7 block · `project-docs/HANDOFF.md` H-152 (the checkout half).
 *
 * ## Where the money goes now (H-152)
 *
 * The checkout used to call the Customer Console from HERE with
 * `CUSTOMER_CONSOLE_ORG_KEY`. That key names ONE tenant, so on a shared box
 * every tenant's purchases were billed to the key's tenant, or refused. The
 * three routes now relay through the gateway's `/billing/orders*`, which
 * holds the per-box deployment key; the Console derives the organization
 * from the signed-in member. This file holds NO Console key and never did
 * the org-key comparison `_console.ts` needed: the Console answers for the
 * member's own organization by construction.
 *
 * ## Two gates, the same rule
 *
 * The gateway checks the tenant plane's `billing:purchase` itself, before it
 * reaches the Console. This hop checks it too, BEFORE the gateway's money
 * route is touched, so a refused member never produces a request to it.
 * `checkout.test.ts` asserts the refusal happens first, by running the
 * handlers.
 */
import { NextResponse } from "next/server";

import { GATEWAY_URL, currentIdentity, headersActingAs, proxyToGateway, gatewayFetch } from "@/lib/gateway";
import { hasCapability, type Access } from "@/lib/access";

/**
 * The slug minted for this surface. Mirrored in
 * `acb_auth.permissions.CAPABILITIES` and checked again at the gateway
 * (`routes/billing.py`), whose fence is `test_billing_proxy_route.py`.
 */
export const PURCHASE_CAPABILITY = "billing:purchase";

export interface Purchaser {
  email: string;
}

/**
 * The signed-in member, IF they may spend the company's money.
 *
 * Returns a `NextResponse` to hand straight back on refusal:
 *
 *     const who = await requirePurchaser();
 *     if (who instanceof NextResponse) return who;
 *
 * 1. **401 for a signed-out caller**, 403 for a signed-in one without the
 *    capability. Two facts, two statuses, two sentences on the page.
 * 2. **The capability comes from the gateway's `/auth/me`**, resolved
 *    server-side over the session. Never from a header, a body or a
 *    client-supplied claim (R3/R11). An owner holding `*` passes.
 * 3. **Every failure to RESOLVE is a 403.** A gateway that is down, a non-JSON
 *    answer, a payload with no `capabilities` array — all mean *we could not
 *    establish that this person may buy*, and the safe answer is no.
 */
export async function requirePurchaser(): Promise<Purchaser | NextResponse> {
  const identity = await currentIdentity();
  if (!identity) {
    return NextResponse.json({ detail: "Sign in to continue" }, { status: 401 });
  }

  let access: Partial<Access> | null = null;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me`, {
      headers: headersActingAs(identity.email),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (res.ok) access = (await res.json()) as Partial<Access>;
  } catch {
    // An unreachable gateway is not permission to buy.
    access = null;
  }

  const capabilities = Array.isArray(access?.capabilities) ? access.capabilities : [];
  if (!hasCapability({ capabilities } as Access, PURCHASE_CAPABILITY)) {
    return NextResponse.json(
      {
        detail: "You do not have permission to make purchases for this organization.",
      },
      { status: 403 },
    );
  }
  return { email: identity.email };
}

/** The gateway's checkout paths. */
export type CheckoutPath = "/billing/orders" | `/billing/orders/${string}`;

const UNAVAILABLE = JSON.stringify({ detail: "Billing is temporarily unavailable." });

/**
 * Relay one checkout call through the gateway, as the signed-in member.
 *
 * The gateway relays the Console's refusal partition verbatim (400, 404,
 * 409), turns an outage or a missing capability into a 503, and never
 * passes a Console 401 on as a sign-in prompt. An unreachable gateway is the
 * same 503 one hop earlier.
 */
export async function checkoutCall(
  path: CheckoutPath,
  init: RequestInit = {},
): Promise<Response> {
  try {
    return await proxyToGateway(path, init);
  } catch {
    return new Response(UNAVAILABLE, {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
