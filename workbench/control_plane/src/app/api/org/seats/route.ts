/**
 * BFF: the organization's seat counts + roster, for the Settings → Organization
 * Seat-assignments tab.
 *
 * Spec: `project-docs/specs/customer_console.md` §6 **CP-2h slice 1 —
 * D-SEAT-4** · `subscription_console.md` SC-2a (the gateway-tier transport) ·
 * `user_management_contract.md` R11.
 *
 * ## Why this read moved off `/api/billing/*` (the whole point of the slice)
 *
 * The Seats tab used to compose its picture from two ORGANIZATION-key reads —
 * `/api/billing/seats` (`GET /me/seats`) and `/api/billing/members`
 * (`GET /me/members`) — which present this deployment's own `cc_live_…` key from
 * `api/billing/_console.ts`. **On a shared multi-tenant box there is no single
 * correct org key**: `CUSTOMER_CONSOLE_ORG_KEY` names one tenant, the deployment
 * hosts many, so the variable is unset and every seat read 503s. The tab then
 * renders "not configured for this deployment" — permanently, and for a
 * STRUCTURAL reason no flag flip fixes.
 *
 * So this hop goes to the GATEWAY (`GET /seats/overview`), which holds the
 * per-BOX deployment key and asks the Console to derive the organization from
 * the acting admin's membership. Same door as the seat WRITES already use: one
 * plane, one credential, every tenant on the box served correctly.
 *
 * The org-key path is deliberately **untouched** — `/settings/billing` is a
 * per-org surface and its routes keep their own credential.
 *
 * ## R11 — nothing here names a tenant, because there is nothing to name
 *
 * No body, no query parameter, no path segment. `proxyToGateway` attaches the
 * internal bearer plus the signed-in member's `X-User-Email`, the gateway reads
 * the acting admin from that, and the Console derives the org. A signed-out
 * caller gets a 401 from `proxyToGateway` and reaches nothing.
 *
 * ## 503 has to keep meaning "unconfigured"
 *
 * `SeatsTab`'s `PlaneState` distinguishes an unreachable seat plane from an
 * empty one, and 503 is the signal it keys on. The gateway answers 503 when the
 * Console is unwired or unreachable; an unreachable GATEWAY is the same fact one
 * hop earlier, so it is reported the same way rather than as a 502 the surface
 * would draw as a red error banner.
 */
import { proxyToGateway } from "@/lib/gateway";

// Resolves the signed-in member (through `proxyToGateway`), so it can never be
// statically evaluated during `next build`.
export const dynamic = "force-dynamic";

/** The unconfigured/unreachable answer — the Console's own wording, relayed. */
const UNAVAILABLE = JSON.stringify({
  detail: "Seat management is temporarily unavailable.",
});

export async function GET(): Promise<Response> {
  try {
    return await proxyToGateway("/seats/overview");
  } catch {
    return new Response(UNAVAILABLE, {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
