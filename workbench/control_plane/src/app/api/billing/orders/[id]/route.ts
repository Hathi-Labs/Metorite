/**
 * BFF: read one order back — `GET /billing/orders/{id}` (CP-9 §9.3a).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 5a and 9.
 *
 * ## Why the READ carries the same gate as the two writes
 *
 * §9.3a calls this *"a read on a credential that already reads"*, and on the
 * Console that is exactly right — it mints no scheme there. On **this** side it
 * is different, and the difference is what it exposes: an order's state, its
 * amounts, its GST split and the prefix of a discount code somebody was issued.
 * That is not the summary's data (a balance and a burn rate) and it is not
 * public to the org in the way the catalog is — it is the record of a purchase
 * in progress, readable by order id.
 *
 * So it is gated on `billing:purchase` like its two siblings. The alternative
 * — session-only, like `summary/route.ts` — would mean any signed-in member who
 * can guess or overhear an order id reads what the company bought and what
 * discount it was given, on a route that exists because **done-when 5a demands
 * a re-read before every act**. A gate on the write and not on the read of the
 * same object is a gate with a door beside it.
 *
 * The narrow cost is stated rather than hidden: an admin without
 * `billing:purchase` cannot inspect an order. They also cannot create or redeem
 * one, so there is no order of theirs to inspect.
 */
import { NextResponse } from "next/server";

import {
  consoleConfig,
  consoleHeaders,
  consoleUnavailable,
  notConfigured,
  relayConsole,
  requirePurchaser,
} from "../../_console";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const who = await requirePurchaser();
  if (who instanceof NextResponse) return who;

  const config = consoleConfig();
  if (!config) return notConfigured();

  const { id } = await params;

  try {
    const res = await fetch(
      `${config.url}/billing/orders/${encodeURIComponent(id)}`,
      {
        headers: consoleHeaders(config.key, who.email),
        cache: "no-store",
      },
    );
    // A foreign order and an unknown one answer one byte-identical 404 upstream
    // (§9.3(7)); relaying it unchanged is what keeps that non-oracle property
    // true at the browser rather than only at the Console.
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
