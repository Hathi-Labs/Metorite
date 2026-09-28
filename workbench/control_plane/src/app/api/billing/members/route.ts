/**
 * BFF: the signed-in member's own organization roster.
 *
 * `email · role · status · seats` per membership row, for the manage-seats
 * panel. The seat WRITES are `seats/{assign,release}`, via the gateway too.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/members` → the Console's
 * deployment-key `billing_read` door. This route holds no Console key and
 * names no tenant; `_gateway.ts` says why the organization key left this tier.
 */
import { billingRead } from "../_gateway";

// Resolves the signed-in member (through `proxyToGateway`), so it can never be
// statically evaluated during `next build`.
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/members");
}
