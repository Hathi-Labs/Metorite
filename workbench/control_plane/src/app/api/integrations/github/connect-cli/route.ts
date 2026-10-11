/**
 * POST /api/integrations/github/connect-cli
 *
 * Proxies to POST /integrations/github/connect-cli on the gateway.
 * Retired by WS-54 IN-0: the gateway answers 410 and reads no `gh` token,
 * because that token is the identity of the operator. IN-7 removes the route.
 * The response shape below is the one from before IN-0.
 *
 * Response:
 *   {
 *     ok: boolean,
 *     login: string,
 *     scopes: string[],
 *     has_copilot: boolean,
 *     refresh_command: string | null,  // non-null when copilot scope is missing
 *     message: string,
 *   }
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(_req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/github/connect-cli`, {
      method: "POST",
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(20_000),
    });
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(
      { error: data.detail ?? `Gateway ${res.status}` },
      { status: res.status }
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: `Gateway unreachable: ${msg}` }, { status: 502 });
  }
}
