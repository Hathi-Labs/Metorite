/**
 * BFF: read one order back (CP-9 §9.3a).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 5a and 9 ·
 * `project-docs/HANDOFF.md` H-152 (the checkout half).
 *
 * ## Why the READ carries the same gate as the two writes
 *
 * It exposes an order's state, its amounts, its GST split and the prefix of a
 * discount code somebody was issued — the record of a purchase in progress,
 * readable by order id. A gate on the write and not on the read of the same
 * object is a gate with a door beside it. So it takes `billing:purchase` here
 * and again at the gateway.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `GET /billing/orders/{id}` → the Console's
 * deployment-key `billing_purchase` door. The id names an ORDER, never a
 * tenant: the Console looks it up inside the member's own organization, and a
 * foreign order answers the same 404 as an unknown one.
 */
import { NextResponse } from "next/server";

import { checkoutCall, requirePurchaser } from "../../_purchase";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const who = await requirePurchaser();
  if (who instanceof NextResponse) return who;

  const { id } = await params;
  return checkoutCall(`/billing/orders/${encodeURIComponent(id)}`);
}
