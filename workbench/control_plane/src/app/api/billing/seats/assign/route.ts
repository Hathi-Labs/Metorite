/**
 * BFF: assign a seat to a member of the signed-in admin's own organization.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2a (the gateway-tier
 * transport) · `customer_console.md` §6 item (h) / §9 residual 7.
 *
 * ## Why this WRITE goes to the GATEWAY, not the Console directly
 *
 * The Console's seat WRITE is the deployment-key `seat_admin` door, and the
 * deployment key is fenced OUT of the Next/browser tier by name
 * (`gateway.test.ts:232` / §6(f)). So — unlike the sibling `seats/route.ts` READ,
 * which presents this deployment's own `cc_live_` org key — this write goes
 * through the gateway (`lib/gateway.ts`), which already holds the deployment key
 * and already speaks to the Console at sign-in resolve. This hop holds NO
 * deployment key; `proxyToGateway` forwards the internal bearer + the signed-in
 * member's `X-User-Email`, and the gateway attaches the credential and the acting
 * identity. A signed-out caller gets a 401 here (`NoIdentityError`) and reaches
 * nothing.
 *
 * ## R11 — the browser names the target and the plan, nothing else
 *
 * The outbound body is rebuilt from `{member_email, plan_slug, source}` ONLY, so
 * an `org` / `actor_email` / `email` a browser adds is dropped here rather than
 * forwarded — the acting admin is the SESSION (the gateway reads it from
 * `X-User-Email`) and the org is derived Console-side. The gateway refuses those
 * keys again (defense in depth); this hop simply never sends them.
 */
import { NextRequest } from "next/server";
import { proxyToGateway } from "@/lib/gateway";

// Resolves the signed-in member (through `proxyToGateway`), so it can never be
// statically evaluated during `next build`.
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  let raw: Record<string, unknown> = {};
  try {
    raw = (await req.json()) as Record<string, unknown>;
  } catch {
    raw = {};
  }
  // R11: name only the target member, the plan and the seat source. The acting
  // admin and the org are established server-side, never from the browser body.
  const body: Record<string, unknown> = {
    member_email: raw.member_email,
    plan_slug: raw.plan_slug,
    source: raw.source,
  };
  try {
    return await proxyToGateway("/seats/assign", {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch {
    return new Response(JSON.stringify({ detail: "Gateway unreachable." }), {
      status: 502,
      headers: { "Content-Type": "application/json" },
    });
  }
}
