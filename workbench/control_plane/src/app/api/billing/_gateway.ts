/**
 * The billing READS' shared hop: browser → this BFF → gateway → Console.
 *
 * Spec: `project-docs/HANDOFF.md` H-152 (the billing half) ·
 * `customer_console.md` §6 CP-2h (D-SEAT-4, the pattern) ·
 * `user_management_contract.md` R11.
 *
 * ## Why the reads left `_console.ts`
 *
 * They used to present `CUSTOMER_CONSOLE_ORG_KEY` to the Console straight from
 * here. That key IS one organization, so a SHARED box had no correct value for
 * it: unset, every billing page was dark; set, every tenant saw the key's
 * tenant's balance, roster and spend. The gateway holds the per-BOX deployment
 * key instead, and the Console derives the organization from the signed-in
 * member. `api/org/seats/route.ts` made the same move first.
 *
 * ## What this hop does, and does not do
 *
 * It holds NO Console credential, and names no tenant and no person: the
 * request carries no body and no query string, and `proxyToGateway` attaches
 * the internal bearer plus the SESSION's email. A signed-out caller gets a 401
 * from `proxyToGateway` and reaches nothing.
 *
 * It makes NO authorization decision. Which figures a member may see (a
 * non-admin reads only their own spend; the per-person table is admin-only) is
 * decided at the gateway, from the tenant plane's resolved access.
 *
 * ## 503 keeps meaning "not available here"
 *
 * The gateway answers 503 when the Console is unwired, unreachable, or the
 * box's key lacks `billing_read`. An unreachable GATEWAY is the same fact one
 * hop earlier, so it reads the same way, and the page draws its blocks absent
 * rather than as a crash. Fence: `billing/reads.test.ts`.
 */
import { proxyToGateway } from "@/lib/gateway";

/** The gateway's billing read paths. A closed set, so no route can widen it. */
export type BillingReadPath =
  | "/billing/summary"
  | "/billing/seats"
  | "/billing/members"
  | "/billing/catalog"
  | "/billing/usage/activity"
  | "/billing/usage/apps"
  | "/billing/usage/members";

const UNAVAILABLE = JSON.stringify({
  detail: "Billing is temporarily unavailable.",
});

/** Relay one billing read for the signed-in member. */
export async function billingRead(path: BillingReadPath): Promise<Response> {
  try {
    return await proxyToGateway(path);
  } catch {
    return new Response(UNAVAILABLE, {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
