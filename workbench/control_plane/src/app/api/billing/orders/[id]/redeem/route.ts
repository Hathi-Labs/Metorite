/**
 * BFF: present a discount code (SC-4g).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 8 and 9 ·
 * `project-docs/HANDOFF.md` H-152 (the checkout half).
 *
 * The second of B7's two write proxies, and the one that can move value: a
 * verified operator-issued code bringing `total_paise` to 0 fulfils through
 * the same `payments.fulfil()` the capture path calls. **The code is the
 * pre-authorization** — a customer cannot mint one — and presenting one is
 * still spending, so it is gated here and again at the gateway.
 *
 * ⚠️ **The code is a bearer secret.** It is forwarded and never logged, never
 * echoed into a response and never persisted here. Nothing in this file may
 * grow a log line that takes the body.
 *
 * ⚠️ **The refusal bodies arrive verbatim.** SC-4g's partition — three
 * distinct reasons for `expired` / `revoked` / `exhausted`, one byte-identical
 * shape for {unknown, wrong-org} — is the Console's, and the gateway relays it
 * unchanged. Idempotency is the Console's too: one code on one order redeems
 * once.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `POST /billing/orders/{id}/redeem` → the
 * Console's deployment-key `billing_purchase` door.
 */
import { NextResponse, type NextRequest } from "next/server";

import { checkoutCall, requirePurchaser } from "../../../_purchase";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const who = await requirePurchaser();
  if (who instanceof NextResponse) return who;

  const { id } = await params;
  const payload = (await req.json().catch(() => null)) as {
    code?: string;
  } | null;
  const code = (payload?.code ?? "").trim();
  if (!code) {
    return NextResponse.json({ detail: "Enter a code." }, { status: 400 });
  }

  return checkoutCall(`/billing/orders/${encodeURIComponent(id)}/redeem`, {
    method: "POST",
    // Rebuilt, not forwarded: the Console forbids extra fields.
    body: JSON.stringify({ code }),
  });
}
