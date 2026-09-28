/**
 * GET /api/chat/directory — people and groups you can share a room with
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const q = req.nextUrl.searchParams.get("q") ?? "";
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/directory?q=${encodeURIComponent(q)}`,
      {
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(5_000),
      },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
