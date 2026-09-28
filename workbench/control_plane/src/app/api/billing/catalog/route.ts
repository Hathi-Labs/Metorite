/**
 * BFF: the priced ladder (CP-9 §9.3a, §6 item (f)).
 *
 * Done-when 1 of SC-4a: the purchase surface renders only `plan_catalog` rows
 * that are active and priced — never a ladder hard-coded in TypeScript. Prices
 * arrive as integer paise and are formatted, never computed (§9.2).
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/catalog` → the Console's
 * deployment-key `billing_read` door. The ladder is the same for every
 * customer, so the derived organization gates the read and never shapes it.
 */
import { billingRead } from "../_gateway";

// Resolves the signed-in member (through `proxyToGateway`), so it can never be
// statically evaluated during `next build`.
export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/catalog");
}
