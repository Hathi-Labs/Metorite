/**
 * GET /api/accounts — the accounts this browser is signed in to (MT-1k A2).
 *
 * `{ enabled: false }` while the flag is off, and the shell then draws the old
 * footer. With `?orgs=1`, each other account carries its organization's name,
 * read from the gateway AS that account.
 *
 * ⚠️ That is the one place this tier acts as an email that is not the active
 * session's. The token in the slot is the proof: it is a valid Auth.js session
 * for that email, held by this browser, exactly as strong as the active one.
 * The read is `/auth/me` only, and only the organization's name leaves it.
 */
import { NextResponse, type NextRequest } from "next/server";
import { loadState } from "@/lib/accountsServer";
import { otherAccounts } from "@/lib/accountSlots";
import { GATEWAY_URL, currentIdentity, gatewayFetch, headersActingAs } from "@/lib/gateway";

export const dynamic = "force-dynamic";

async function organizationOf(email: string): Promise<string | null> {
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me`, {
      headers: headersActingAs(email),
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return null;
    const me = (await res.json()) as { organization?: { display_name?: string; slug?: string } | null };
    return me.organization?.display_name || me.organization?.slug || null;
  } catch {
    return null;
  }
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const s = await loadState(req, { write: false });
  if (s instanceof NextResponse) return s;
  // A second lock: Auth.js's own reading of the session must name the same
  // account as the cookie this route decoded.
  const me = await currentIdentity();
  if (!me || me.email.toLowerCase() !== s.account.email.toLowerCase()) {
    return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  }
  const others = otherAccounts(s.account, s.slots);
  const withOrgs = req.nextUrl.searchParams.get("orgs") === "1";
  const orgs = withOrgs
    ? await Promise.all(others.map((o) => organizationOf(o.account.email)))
    : others.map(() => null);
  return NextResponse.json(
    {
      enabled: true,
      active: { email: s.account.email, name: s.account.name },
      others: others.map((o, i) => ({
        slot: o.index,
        email: o.account.email,
        name: o.account.name,
        organization: orgs[i],
      })),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}
