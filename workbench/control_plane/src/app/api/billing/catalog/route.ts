/**
 * BFF: the priced ladder — `GET /billing/catalog` (CP-9 §9.3a, §6 item (f)).
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-4a launch done-when 1.
 *
 * ## Why this route exists at all
 *
 * Done-when 1 requires the purchase surface to render **only `plan_catalog`
 * rows that are `active` and priced — never a hard-coded ladder in
 * TypeScript**, because a price change is a database row (D23/D24's own rule)
 * and a surface that transcribes prices is a second source of truth for money.
 * Until WS-31 built `GET /billing/catalog` (2026-08-19) no route exposed the
 * priced catalog to a customer credential and the clause had no data source
 * (finding F-I). This hop is what puts it on the page.
 *
 * Prices arrive as **integer paise** and are formatted, never arithmeticked
 * (§9.2). Nothing here converts, sums or rounds.
 *
 * ## Why this one is session-gated and its three siblings are not
 *
 * It is a **read of data that is the same for every customer** — the Console's
 * handler binds its caller to `_` precisely to make "no per-org answer is
 * computable here" structural. There is no balance in it, no entitlement, no
 * order and no discount: it is our price list. So it takes the same gate the
 * shipped summary read takes — signed in — rather than `billing:purchase`,
 * which would mean a member could not see what things cost before being handed
 * the right to buy them.
 *
 * ⚠️ **That gate is weaker than this directory's other three, and the reason it
 * is acceptable here is the payload, not the pattern.** There is a recorded
 * board finding one route along — `GET /api/billing/summary` is reachable by
 * any signed-in member and returns the org's credit balance, burn and BYOK
 * status while its own header claims otherwise (`subscription_console.md`, the
 * B7 block's foot). That is a live gap in merged code and its own small ticket;
 * this route is deliberately NOT an argument that the gap is fine.
 */
import { NextResponse } from "next/server";

import { currentIdentity } from "@/lib/gateway";

import {
  consoleConfig,
  consoleHeaders,
  consoleUnavailable,
  notConfigured,
  relayConsole,
} from "../_console";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const identity = await currentIdentity();
  if (!identity) {
    return NextResponse.json({ detail: "Sign in to continue" }, { status: 401 });
  }

  const config = consoleConfig();
  if (!config) return notConfigured();

  try {
    const res = await fetch(`${config.url}/billing/catalog`, {
      headers: consoleHeaders(config.key, identity.email),
      cache: "no-store",
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
