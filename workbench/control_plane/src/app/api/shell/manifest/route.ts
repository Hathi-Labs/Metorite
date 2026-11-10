/**
 * GET /api/shell/manifest — the resolved package manifest (PKG-6).
 *
 * Proxies the gateway's `GET /shell/manifest` (`package_host.md` §5.2). The
 * gateway answers 404 for an organization outside `PACKAGE_HOST_ORGS`, and
 * this route passes that status on. The workbench reads a 404 as "off".
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
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/manifest`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    return NextResponse.json({ error: "manifest_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "manifest_unavailable" }, { status: 502 });
  }
}
