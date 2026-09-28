/**
 * BFF: what this organization ran, and what it cost. **D66 (a), H-134.**
 *
 * ⚠️ **A non-admin sees only their OWN activity.** The gateway decides that
 * from the tenant plane's resolved access, and this route takes no query
 * parameter a caller could set to widen it. Credits only: no model, no
 * provider, no cost (D66).
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/usage/activity` → the Console's
 * deployment-key `billing_read` door. This route holds no Console key and
 * names no tenant; `../../_gateway.ts` says why the organization key left.
 */
import { billingRead } from "../../_gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/usage/activity");
}
