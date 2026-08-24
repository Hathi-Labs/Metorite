/**
 * BFF: assign a seat to a member of the signed-in admin's own organization —
 * the Settings → Organization seat surface's write hop.
 *
 * Spec: `project-docs/specs/customer_console.md` §6 **CP-2h slice 1 —
 * D-SEAT-4** · `subscription_console.md` SC-2a (the gateway-tier transport) ·
 * `user_management_contract.md` R11.
 *
 * ## Why this WRITE goes to the GATEWAY, not the Console directly
 *
 * The Console's seat WRITE is the deployment-key `seat_admin` door, and the
 * deployment key is fenced OUT of the Next/browser tier by name
 * (`gateway.test.ts` / `customer_console.md` §6(f)). This hop therefore holds no
 * credential at all: `proxyToGateway` forwards the internal bearer plus the
 * signed-in member's `X-User-Email`, and the gateway attaches the deployment key
 * and vouches for the acting identity. A signed-out caller gets a 401 here
 * (`NoIdentityError`) and reaches nothing.
 *
 * ## R11 — the browser names the target and the plan, nothing else
 *
 * The outbound body is rebuilt from `{member_email, plan_slug, source}` ONLY, so
 * an `org` / `actor_email` / `email` a browser adds is dropped here rather than
 * forwarded. The gateway refuses those keys again (defence in depth); this door
 * simply never carries them.
 *
 * ⚠️ **`/api/billing/seats/assign` is this route's twin and stays**, because
 * `/settings/billing` still drives it. The two are one implementation of nothing
 * — each is a five-line rebuild-and-forward over the SAME gateway route and the
 * SAME Console door, which is where the seam actually lives. The billing copy
 * retires when that page's manage-seats block does (CP-2h, later slice).
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
