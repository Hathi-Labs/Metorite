/**
 * BFF: release a member's seat in the signed-in admin's own organization —
 * the Settings → Organization seat surface's release hop.
 *
 * Spec: `project-docs/specs/customer_console.md` §6 **CP-2h slice 1 —
 * D-SEAT-4** · `subscription_console.md` SC-2a · `user_management_contract.md`
 * R11.
 *
 * The write twin of `assign/route.ts` — see its header for why a seat write goes
 * through the gateway rather than presenting the deployment key from the browser
 * tier, and why the outbound body is rebuilt so the browser names no org and no
 * actor. The release arm takes no `source` (freeing a seat needs no billing
 * category); everything else is identical.
 *
 * ⚠️ **Release is not removal** (D-SEAT-7): the seat frees immediately, the
 * `seat_assignment` row is kept with `released_at` set (seat history is billing
 * evidence), the member keeps their membership and their roster row, and their
 * DATA is untouched — it is organization data on the tenant plane. Barring a
 * person is the Members tab's act. The copy that says so lives on the surface;
 * this hop only carries the call.
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
