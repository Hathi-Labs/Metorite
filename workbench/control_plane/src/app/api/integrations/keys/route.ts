/**
 * GET /api/integrations/keys
 *
 * Proxies to GET /integrations/keys on the FastAPI gateway.
 * Returns which integration credentials are stored in the encrypted DB.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface IntegrationKeysResponse {
  services: Record<string, string[]>;  // service → [key_names]
  total_keys: number;
  storage: string;
}

export async function GET(_req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/keys`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(5_000),
    });
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
