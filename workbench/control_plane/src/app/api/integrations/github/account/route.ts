/**
 * GET /api/integrations/github/account
 *
 * Proxies to GET /integrations/github/account on the gateway. The answer is
 * the GitHub connection of the caller's ORGANIZATION (WS-54 IN-7, GH-3): its
 * one `github` row, in the App mode or the token mode. The gateway never
 * reads the deployment GITHUB_TOKEN or the gh CLI for it, and no token is in
 * the answer.
 *
 * Response (`GitHubAccountInfo`):
 *   - connected, status, login, auth ("app" | "token" | null), account_type,
 *     repository_selection, installation_url, connected_by, last_error,
 *     organization_name, app_available. `src/lib/githubConnect.ts` reads them.
 *   - token_configured, token_login, token_scopes, token_has_copilot: the
 *     same facts under the old names of the account badge.
 *   - gh_cli_*: always empty. The CLI import is retired.
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
  // WS-54 GH-3: the two modes of the one GitHub row, and the App's reach.
  // `src/lib/githubConnect.ts` (`GitHubAccount`) reads these.
  connected?: boolean;
  status?: string;
  login?: string | null;
  auth?: "app" | "token" | null;
  account_type?: string | null;
  repository_selection?: string | null;
  installation_url?: string | null;
  connected_by?: string | null;
  organization_name?: string | null;
  app_available?: boolean;
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
