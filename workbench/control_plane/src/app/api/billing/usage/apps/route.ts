/**
 * BFF: what each app spent, and which agents inside it. **Usage slice 3.**
 *
 * ⚠️ **A non-admin sees only their OWN apps.** The gateway decides that from
 * the tenant plane's resolved access. There is no query parameter here a
 * browser could set to widen it. Credits only (D66).
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/usage/apps` → the Console's
 * deployment-key `billing_read` door. This route holds no Console key and
 * names no tenant; `../../_gateway.ts` says why the organization key left.
 */
import { billingRead } from "../../_gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/usage/apps");
}
