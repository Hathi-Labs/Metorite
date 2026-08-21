/**
 * BFF: release a member's seat in the signed-in admin's own organization.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2a (the gateway-tier
 * transport) · `customer_console.md` §6 item (h) / §9 residual 7.
 *
 * The write twin of `assign/route.ts` — see its header for why a seat WRITE goes
 * through the gateway rather than presenting the deployment key from the browser
 * tier, and why the outbound body is rebuilt so the browser names no org and no
 * actor (R11). The release arm takes no `source` (freeing a seat needs no billing
 * category); everything else is identical, `proxyToGateway` attaching the internal
 * bearer + the signed-in member's `X-User-Email`.
 */
import { NextRequest } from "next/server";
import { proxyToGateway } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<Response> {
  let raw: Record<string, unknown> = {};
  try {
    raw = (await req.json()) as Record<string, unknown>;
  } catch {
    raw = {};
  }
  // R11: name only the target member and the plan. The acting admin and the org
  // are established server-side, never from the browser body.
  const body: Record<string, unknown> = {
    member_email: raw.member_email,
    plan_slug: raw.plan_slug,
  };
  try {
    return await proxyToGateway("/seats/release", {
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
