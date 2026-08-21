/**
 * BFF: the signed-in member's OWN organization roster — `GET /me/members`.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-2b (the manage-seats
 * roster) · `customer_console.md` §6 item (i) (`MembersView` {email, role,
 * status}).
 *
 * ## The same read hop as `seats/route.ts` / `catalog/route.ts`, verbatim
 *
 * This route does NOT go to the gateway. It presents this deployment's own
 * `cc_live_…` organization key to the Customer Console, and the key resolves the
 * organization (CP-3) — so the route reads its own org's members and nothing
 * else, **by construction rather than by a filter someone must remember**. The
 * browser cannot name a tenant here: there is no query parameter, no body and no
 * org on the wire (`user_management_contract.md` R11). The Console pins the org
 * to the credential, and here the credential is the server's, not the caller's.
 * `X-CC-Member` is attribution only and can never move the read to another org.
 *
 * ## Session-gated, like `seats`/`catalog`, not `billing:purchase`-gated
 *
 * This is a read of the caller's own roster — it mints nothing and moves no
 * money — so it takes the same gate the shipped `summary`/`catalog`/`seats`
 * reads take: signed in. The per-member WRITE controls that consume this roster
 * are `billing:purchase`-gated at the surface AND admin-gated by the page; the
 * roster read itself is the seats read's known-open "any signed-in member"
 * posture (the B7 block's foot) — not made worse here, and its own ticket to fix.
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
// `next build` — the same reason `summary`/`catalog`/`seats` carry it.
export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const identity = await currentIdentity();
  if (!identity) {
    return NextResponse.json({ detail: "Sign in to continue" }, { status: 401 });
  }

  const config = consoleConfig();
  if (!config) return notConfigured();

  try {
    const res = await fetch(`${config.url}/me/members`, {
      headers: consoleHeaders(config.key, identity.email),
      cache: "no-store",
    });
    return await relayConsole(res);
  } catch {
    return consoleUnavailable();
  }
}
