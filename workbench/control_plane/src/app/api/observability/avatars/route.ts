/**
 * GET /api/observability/avatars
 *
 * Proxies GET /observability/avatars — every stored avatar override (partial
 * config + optional custom Pixel Lab sprite), keyed by agent name. The office
 * merges these; the Avatar Studio reads them to seed its editor.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/observability/avatars`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) return NextResponse.json({ avatars: {} }, { status: 200 });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ avatars: {} }, { status: 200 });
  }
}
