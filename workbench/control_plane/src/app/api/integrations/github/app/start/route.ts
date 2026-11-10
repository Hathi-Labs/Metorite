/**
 * POST /api/integrations/github/app/start — start the install of the Metorite
 * GitHub App (WS-54 GH-3).
 *
 * Spec: `project-docs/specs/github_integration.md` §7.1. The gateway route
 * (`POST /integrations/github/app/start`) needs `integrations:manage`, signs
 * the state, writes the pending connect in Redis and answers `{url}`, the
 * install link of the App. With `GITHUB_APP_ENABLED` off it answers 404, and
 * so does this route.
 *
 * The rules, each with a case in `route.test.ts`:
 *
 * 1. **No body goes upstream.** The org and the member come from the session
 *    at the gateway (R5, R11).
 * 2. **Same origin only.** A start that another site sends gets 403.
 * 3. **The URL comes from the gateway, whole**, and it must be an install
 *    link on `https://github.com/apps/`. Any other answer is a 502, so this
 *    route can never become an open redirect.
 * 4. **A refusal carries a short code.**
 * 5. **Nothing logs the answer.** The URL holds the state.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { isGitHubInstallUrl } from "@/lib/githubConnect";

export const dynamic = "force-dynamic";

const START_TIMEOUT_MS = 20_000;
const NO_STORE = { "Cache-Control": "no-store" };
const CODE = /^[a-z0-9_]{1,40}$/;

function refuse(detail: string, status: number): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  if (!sameOrigin(req)) return refuse("cross_origin", 403);

  let res: Response;
  try {
    res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/github/app/start`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(START_TIMEOUT_MS),
      },
      { retry: false },
    );
  } catch {
    return refuse("unreachable", 502);
  }
  if (res.type === "opaqueredirect" || res.status === 0 || (res.status >= 300 && res.status < 400)) {
    return refuse("failed", 502);
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string" && CODE.test(detail)) return refuse(detail, res.status);
    return refuse(res.status === 404 ? "not_available" : "failed", res.status);
  }
  const url = (body as { url?: unknown } | null)?.url;
  if (!isGitHubInstallUrl(url)) return refuse("failed", 502);
  return NextResponse.json({ url }, { headers: NO_STORE });
}
