/**
 * BFF: the signed-in member's OWN organization seats — `GET /me/seats`.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-1a (the launch SEATS
 * BLOCK) · `customer_console.md` §6 item (g) / done-when 19.
 *
 * ## The same read hop as `catalog/route.ts`, for the same reasons
 *
 * These routes do NOT go to the gateway. They present this deployment's own
 * `cc_live_…` organization key to the Customer Console, and the key resolves the
 * organization (CP-3) — so the route reads its own org's seats and nothing else,
 * **by construction rather than by a filter someone must remember**. The browser
 * cannot name a tenant here: there is no query parameter, no body and no org on
 * the wire (`user_management_contract.md` R11). This is the surface's half of
 * SC-1a's "org A its own rows and never org B's" — the Console pins the org to
 * the credential, and here the credential is the server's, not the caller's.
 * `X-CC-Member` is attribution only and can never move the read to another org.
 *
 * ## Session-gated, like `catalog`, not `billing:purchase`-gated
 *
 * This is a read of the caller's own seat counts — it mints nothing and moves no
 * money — so it takes the same gate the shipped `summary`/`catalog` reads take:
 * signed in. Requiring `billing:purchase` would stop a member seeing how many
 * seats they have before being handed the right to buy more. The Console's own
 * `my_seats` sits on the `can_pay` door (a suspended org must still see its
 * seats); the per-member surface is admin-gated at the page. This inherits the
 * summary read's known-open "any signed-in member" posture (the B7 block's foot)
 * — not made worse here, and its own ticket to fix.
 *
 * ## No new environment variables
 *
 * The same `CUSTOMER_CONSOLE_URL` / `CUSTOMER_CONSOLE_ORG_KEY` the read and
 * checkout proxies already use, and the same fail-closed 503.
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

// Resolves the signed-in member, so it can never be statically evaluated during
// `next build` — the same reason `summary`/`catalog` carry it.
export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const identity = await currentIdentity();
  if (!identity) {
    return NextResponse.json({ detail: "Sign in to continue" }, { status: 401 });
  }

  const config = consoleConfig();
  if (!config) return notConfigured();

  try {
    const res = await fetch(`${config.url}/me/seats`, {
      headers: consoleHeaders(config.key, identity.email),
      cache: "no-store",
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
