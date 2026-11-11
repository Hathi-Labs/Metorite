/**
 * GET /api/shell/layout — the org default of the shell layout (PKG-17b).
 *
 * Proxies the gateway's `GET /shell/layout` (`package_host.md` §9.1). The
 * gateway answers 404 for an organization outside `PACKAGE_HOST_ORGS`, and
 * this route passes that status on. The workbench reads any non-200 as "no
 * org default".
 *
 * The identity is the session's. The request carries no tenant and no member.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/layout`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    return NextResponse.json({ error: "layout_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "layout_unavailable" }, { status: 502 });
  }
}
