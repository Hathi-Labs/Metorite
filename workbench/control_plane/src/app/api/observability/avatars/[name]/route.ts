/**
 * PUT/DELETE /api/observability/avatars/[name]
 *
 * Proxies the avatar-override write endpoints — pin (PUT) or clear (DELETE) an
 * agent's look/sprite. The Pixel Lab API key never touches the browser; it lives
 * on the gateway (see the generate route).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ name: string }> };

export async function PUT(req: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await ctx.params;
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    /* empty body ok */
  }
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/observability/avatars/${encodeURIComponent(name)}`,
      {
        method: "PUT",
        headers: await gatewayHeaders(),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(8_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "gateway unreachable" }, { status: 502 });
  }
}

export async function DELETE(_req: NextRequest, ctx: Ctx): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { name } = await ctx.params;
  try {
    const res = await gatewayFetch(
      `${GATEWAY_URL}/observability/avatars/${encodeURIComponent(name)}`,
      {
        method: "DELETE",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(8_000),
      },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "gateway unreachable" }, { status: 502 });
  }
}
