/**
 * BFF: what each person in this organization spent. **D66 (b), H-134.**
 *
 * 🔴 **ADMIN ONLY.** The gateway refuses a non-admin with a 403 before the
 * Console is asked, from the tenant plane's resolved access. The page's own
 * admin gate is a courtesy, not the boundary.
 *
 * ⚠️ **This must never grow a cap column.** `member_ai_cap` is not enforced
 * while the member identity comes from an inbound header (H-73). A number
 * that looks like a control and is not one is worse than an absent number.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/usage/members` → the Console's
 * deployment-key `billing_read` door. This route holds no Console key.
 */
import { billingRead } from "../../_gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<Response> {
  return billingRead("/billing/usage/members");
}
