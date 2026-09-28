/**
 * GET /api/integrations/test?service={name}
 *
 * Proxies to GET /integrations/test?service={name} on the FastAPI gateway.
 * Returns { service, ok, detail }.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const service = req.nextUrl.searchParams.get("service") ?? "";
  if (!service) {
    return NextResponse.json({ error: "service param required" }, { status: 400 });
  }

  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/test?service=${encodeURIComponent(service)}`,
      {
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(15_000),
      }
    );
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    const text = await res.text().catch(() => "");
    return NextResponse.json(
      { error: `Gateway ${res.status}: ${text}` },
      { status: res.status }
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
