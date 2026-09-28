/**
 * BFF: the signed-in member's own organization seat grid.
 *
 * The ONE seat vocabulary (`purchased · assigned · available ·
 * oversubscribed`), computed at the Console and rendered verbatim. The seat
 * WRITES are `seats/{assign,release}`, which also travel through the gateway.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/seats` → the Console's
 * deployment-key `billing_read` door. This route holds no Console key and
 * names no tenant; `_gateway.ts` says why the organization key left this tier.
 */
import { billingRead } from "../_gateway";

// Resolves the signed-in member (through `proxyToGateway`), so it can never be
// statically evaluated during `next build`.
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/seats");
}
