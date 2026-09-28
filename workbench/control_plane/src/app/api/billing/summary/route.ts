/**
 * BFF: the signed-in member's own organization billing summary.
 *
 * The balance, the burn over a named window, and whether the organization
 * brings its own provider key. The figures are the member's OWN
 * organization's, by derivation at the Console, never by a filter here.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/summary` → the Console's
 * deployment-key `billing_read` door. This route holds no Console key and
 * names no tenant; `_gateway.ts` says why the organization key left this tier.
 */
import { billingRead } from "../_gateway";

// Resolves the signed-in member (through `proxyToGateway`), so it can never be
// statically evaluated during `next build`.
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/summary");
}
