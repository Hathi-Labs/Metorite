/**
 * BFF: create a checkout order — `POST /billing/orders` (CP-9 §9.3(2)).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a done-when 2 and 8 ·
 * `customer_console.md` §6 CP-9 §9.3.
 *
 * This is one of the two **write** proxies B7 gates. It creates a pending
 * intent and nothing else — no seat, no subscription, no ledger row, no
 * entitlement, none of which the organization key can write anyway. Value moves
 * only on a signature-verified webhook or an operator-issued code.
 *
 * ⚠️ **The basket carries no amount, and must not.** `CreateOrderRequest` is
 * `{ lines: [{ plan_slug, quantity }] }` with `extra: "forbid"`; every paisa
 * comes from `plan_catalog` through `payments.paise`. A checkout that trusts
 * the browser about what something costs is the oldest bug in e-commerce, so
 * this hop forwards the basket **shape** and never a price — including one the
 * page happens to be displaying.
 */
import { NextResponse, type NextRequest } from "next/server";

import {
  consoleConfig,
  consoleHeaders,
  consoleUnavailable,
  notConfigured,
  relayConsole,
  requirePurchaser,
} from "../_console";

export const dynamic = "force-dynamic";

interface BasketLine {
  plan_slug: string;
  quantity: number;
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  // 401 / 403 BEFORE anything else — including before the configuration check,
  // so a member without the capability cannot learn whether this deployment is
  // wired to a Console.
  const who = await requirePurchaser();
  if (who instanceof NextResponse) return who;

  const config = consoleConfig();
  if (!config) return notConfigured();

  const payload = (await req.json().catch(() => null)) as {
    lines?: BasketLine[];
  } | null;
  const lines = Array.isArray(payload?.lines) ? payload.lines : [];
  if (lines.length === 0) {
    return NextResponse.json(
      { detail: "Choose at least one package." },
      { status: 400 },
    );
  }

  // Rebuilt rather than forwarded: the Console forbids extra fields, and a
  // body passed through untouched is a body a future page field can 422 on.
  // It is also where "the browser never names a price" is enforceable —
  // anything but slug and quantity is dropped here.
  const body = {
    lines: lines.map((line) => ({
      plan_slug: String(line.plan_slug ?? ""),
      quantity: Number(line.quantity ?? 0),
    })),
  };

  try {
    const res = await fetch(`${config.url}/billing/orders`, {
      method: "POST",
      headers: consoleHeaders(config.key, who.email),
      body: JSON.stringify(body),
      cache: "no-store",
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
