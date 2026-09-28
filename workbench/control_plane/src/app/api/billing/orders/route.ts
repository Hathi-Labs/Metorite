/**
 * BFF: create a checkout order (CP-9 §9.3(2)).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 2 and 8 ·
 * `project-docs/HANDOFF.md` H-152 (the checkout half).
 *
 * One of the two **write** proxies B7 gates. It creates a pending intent and
 * nothing else — no seat, no subscription, no ledger row. Value moves only on
 * a signature-verified webhook or an operator-issued code.
 *
 * ⚠️ **The basket carries no amount, and must not.** This hop forwards the
 * basket SHAPE (`plan_slug`, `quantity`) and never a price — including one the
 * page happens to display. The gateway rebuilds it again, and the Console
 * forbids any other field.
 *
 * ## The hop (H-152)
 *
 * browser → this route → gateway `POST /billing/orders` → the Console's
 * deployment-key `billing_purchase` door, which derives the organization from
 * the signed-in member. No Console key lives here; see `_purchase.ts`.
 */
import { NextResponse, type NextRequest } from "next/server";

import { checkoutCall, requirePurchaser } from "../_purchase";

export const dynamic = "force-dynamic";

interface BasketLine {
  plan_slug: string;
  quantity: number;
}

export async function POST(req: NextRequest): Promise<Response> {
  // 401 / 403 BEFORE anything else, so a refused member never produces a
  // request to the money route.
  const who = await requirePurchaser();
  if (who instanceof NextResponse) return who;

  const payload = (await req.json().catch(() => null)) as {
    lines?: BasketLine[];
  } | null;
  const lines = Array.isArray(payload?.lines) ? payload.lines : [];
  if (lines.length === 0) {
    return NextResponse.json({ detail: "Choose at least one package." }, { status: 400 });
  }

  // Rebuilt rather than forwarded: anything but slug and quantity is dropped
  // here, which is where "the browser never names a price" is enforceable.
  const body = {
    lines: lines.map((line) => ({
      plan_slug: String(line.plan_slug ?? ""),
      quantity: Number(line.quantity ?? 0),
    })),
  };

  return checkoutCall("/billing/orders", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
