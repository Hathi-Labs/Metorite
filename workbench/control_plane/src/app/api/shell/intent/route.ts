/**
 * POST /api/shell/intent — the command bar's coordinator (NS-4b).
 *
 * Proxies the gateway's `POST /shell/intent`. It passes the words and the app
 * the member is in, and nothing else: the job list is the server's own
 * (`navigation_shell.md` §6.4 rule 1). A gateway that is down or slow answers
 * `unavailable`, and the bar shows its other groups.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export type IntentAnswer =
  | { kind: "job"; job: string; label: string; href: string; filled: Record<string, string> }
  | { kind: "handoff"; href: string }
  | { kind: "paused"; message: string }
  | { kind: "off" | "none" | "unavailable" };

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let q = "";
  let scope = "";
  try {
    const body = (await req.json()) as { q?: unknown; scope?: unknown };
    q = typeof body.q === "string" ? body.q.slice(0, 400) : "";
    scope = typeof body.scope === "string" ? body.scope.slice(0, 100) : "";
  } catch {
    return NextResponse.json({ kind: "none" });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/intent`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ q, scope: scope || null }),
      cache: "no-store",
      // The coordinator's budget is 1.5 s (§6.3), plus one fill call.
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json((await res.json()) as IntentAnswer);
  } catch {
    // Gateway unavailable: the bar shows its other groups.
  }
  return NextResponse.json({ kind: "unavailable" });
}
