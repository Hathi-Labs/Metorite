/**
 * POST /api/signup — the one door from the browser to the gateway's self-serve
 * provision route (CP-2c slice 4; the client half of done-when 4).
 *
 * ## Why this hop exists at all
 *
 * The browser CANNOT reach the gateway's `POST /signup/provision` directly: it
 * is BFF-internal-bearer and session-email-only (`routes/signup.py` — not in
 * `PUBLIC_ROUTES`, authenticated by construction). So this route is the hop that
 * attaches the internal bearer AND the acting identity, through the single door
 * (`lib/gateway.ts`, the only module that may mint that token — `gateway.test.ts`
 * fences a second reader). It mirrors every other `/api/**` gateway proxy
 * (`api/agent/register/route.ts` is the worked shape) rather than inventing a
 * parallel one.
 *
 * ## What it forwards, and what it refuses to (R11)
 *
 * The owner is the SESSION email — resolved server-side by `gatewayHeaders()`,
 * attached as `X-User-Email`, and the gateway takes the owner from THAT, never
 * from the body. The deployment is the box's own. So this hop forwards ONLY the
 * four signup fields (`slug`, `display_name`, `registered_state`, `gstin`) and
 * relays no `email` / `org` / `deployment_label` — the tenant/identity claims
 * the gateway 400s as `InvalidBody` (`signup.py:111`). The gateway is the fence;
 * this door simply never carries them.
 *
 * The gateway's own responses pass straight through: 200 `{admit, code, …}` for
 * an outcome, 400 `{code, detail}` for a shape violation. The client renders the
 * `code` through the one `errorCopy` seam.
 */
import { NextRequest, NextResponse } from "next/server";

import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

// Resolves the signed-in member, so it can never be statically evaluated:
// without this, `next build` runs the handler during page-data collection with
// no request and therefore no session.
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  // Fail closed when nobody is signed in: the gateway's provision route needs a
  // session email to own the new org, so a hop with no identity is a 401 here,
  // not a bearer travelling alone.
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return NextResponse.json(
      { code: "InvalidBody", detail: "Invalid JSON body" },
      { status: 400 },
    );
  }
  const body =
    raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const str = (v: unknown) => (typeof v === "string" ? v : "");

  // ONLY the four signup fields. Identity comes from the session (R11), so no
  // email / org / deployment_label is ever placed here — a caller that smuggles
  // one is simply not relayed it.
  const forward = {
    slug: str(body.slug),
    display_name: str(body.display_name),
    registered_state: str(body.registered_state),
    gstin: str(body.gstin),
  };

  try {
    const res = await fetch(`${GATEWAY_URL}/signup/provision`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(forward),
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch {
    // The gateway being unreachable reads to the person as "temporarily
    // unavailable" (the reused ConsoleUnavailable copy), not a crash.
    return NextResponse.json(
      { admit: false, code: "ConsoleUnavailable" },
      { status: 502 },
    );
  }
}
