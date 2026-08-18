/**
 * BFF: the signed-in admin's own organization billing summary.
 *
 * Spec: `project-docs/specs/subscription_console.md` SC-1b/SC-5 · D32 · D35.
 *
 * ## Two things this route deliberately does not do
 *
 * **It does not hold the operator token.** The Control Plane's
 * `GET /billing/summary` is staff-only and cross-organization; a tenant
 * deployment holding that credential would be able to read every customer's
 * billing, which is the exact boundary D32/D35 exist to draw. This route
 * presents the deployment's **own organization key** to `GET /me/billing`, and
 * the key resolves the organization (CP-3) — so it can read its own billing and
 * nothing else, by construction rather than by a filter someone must remember.
 *
 * **It does not accept an organization from the caller.** There is no query
 * parameter and no body. The browser cannot name a tenant here, because the
 * tenant is a property of the credential this server holds
 * (`user_management_contract.md` R11).
 *
 * Admin-gating is enforced upstream by the Control Plane's key scope and here by
 * the session check — a member without admin never reaches billing figures.
 */
import { NextResponse } from "next/server";

import { currentIdentity } from "@/lib/gateway";

// Resolves the signed-in member, so it can never be statically evaluated:
// without this, `next build` runs the handler during page-data collection with
// no request and therefore no session.
export const dynamic = "force-dynamic";

/** Where the Control Plane lives. No default: see the note below. */
const CUSTOMER_CONSOLE_URL = process.env.CUSTOMER_CONSOLE_URL ?? "";
/** This deployment's own `cc_live_…` organization key. */
const CUSTOMER_CONSOLE_ORG_KEY = process.env.CUSTOMER_CONSOLE_ORG_KEY ?? "";

export async function GET() {
  const identity = await currentIdentity();
  if (!identity) {
    return NextResponse.json({ detail: "Sign in to continue" }, { status: 401 });
  }

  // Fail CLOSED and say what is missing. A default pointing at localhost would
  // let a misconfigured production deployment appear to work while reading
  // nothing — the same shape of defect as CP-0's fail-open auth (D33.1).
  if (!CUSTOMER_CONSOLE_URL || !CUSTOMER_CONSOLE_ORG_KEY) {
    return NextResponse.json(
      {
        detail:
          "Billing is not configured for this deployment (CUSTOMER_CONSOLE_URL / CUSTOMER_CONSOLE_ORG_KEY).",
      },
      { status: 503 },
    );
  }

  try {
    const res = await fetch(`${CUSTOMER_CONSOLE_URL.replace(/\/$/, "")}/me/billing`, {
      headers: {
        Authorization: `Bearer ${CUSTOMER_CONSOLE_ORG_KEY}`,
        // Attribution only — it refines within the organization the key already
        // pinned and can never move the read to another one.
        "X-CC-Member": identity.email,
      },
      cache: "no-store",
    });

    if (!res.ok) {
      // The upstream body is not relayed: it is written for an operator and can
      // name internal state. The status is enough for the page to branch on.
      return NextResponse.json(
        { detail: `Could not load billing (${res.status})` },
        { status: res.status === 401 || res.status === 403 ? 502 : res.status },
      );
    }

    return NextResponse.json(await res.json());
  } catch {
    // A Control Plane outage must read as "billing is unavailable", not as a
    // crash — the rest of the product is unaffected and the page says so.
    return NextResponse.json(
      { detail: "Billing is temporarily unavailable." },
      { status: 503 },
    );
  }
}
