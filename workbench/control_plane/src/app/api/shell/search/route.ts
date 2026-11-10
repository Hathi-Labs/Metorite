/**
 * GET /api/shell/search?q=&scope= — the command bar's "Find" (NS-4a).
 *
 * Proxies the gateway's `GET /shell/search`, which asks each app the member
 * holds for its records (`navigation_shell.md` §6.3). The bar is an
 * accelerant, never a requirement, so a gateway that is down or slow answers
 * an empty list, and the bar still offers Do, Go to and Ask.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface FindItem {
  kind: "task" | "email" | "person";
  title: string;
  hint: string;
  href: string;
}

export interface FindGroup {
  app: string;
  label: string;
  items: FindItem[];
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const q = (req.nextUrl.searchParams.get("q") ?? "").slice(0, 200);
  const scope = (req.nextUrl.searchParams.get("scope") ?? "").slice(0, 100);
  const params = new URLSearchParams({ q });
  if (scope) params.set("scope", scope);
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/search?${params}`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      // The gateway's whole budget is 1.5 s (TOTAL_BUDGET_S), plus the trip.
      // ⚠️ The browser's own abort too: the bar drops a request whose words
      // the member has changed, and the gateway call must stop with it.
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(2_500)]),
    });
    if (res.ok) {
      const body = (await res.json()) as { groups?: FindGroup[] };
      return NextResponse.json({ groups: body.groups ?? [] });
    }
  } catch {
    // Gateway unavailable: the bar shows its other groups.
  }
  return NextResponse.json({ groups: [] });
}
