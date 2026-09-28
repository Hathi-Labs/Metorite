/**
 * GET /api/integrations/github/account
 *
 * Proxies to GET /integrations/github/account on the gateway.
 * Returns current GitHub account info from GITHUB_TOKEN + gh CLI status.
 *
 * Response:
 *   {
 *     token_configured: boolean,
 *     token_login: string | null,
 *     token_scopes: string[],
 *     token_has_copilot: boolean,
 *     gh_cli_available: boolean,
 *     gh_cli_authenticated: boolean,
 *     gh_cli_login: string | null,
 *     gh_cli_scopes: string[],
 *     gh_cli_has_copilot: boolean,
 *   }
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export interface GitHubAccountInfo {
  token_configured: boolean;
  token_login: string | null;
  token_scopes: string[];
  token_has_copilot: boolean;
  gh_cli_available: boolean;
  gh_cli_authenticated: boolean;
  gh_cli_login: string | null;
  gh_cli_scopes: string[];
  gh_cli_has_copilot: boolean;
}

export async function GET(_req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/github/account`, {
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(12_000),
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
