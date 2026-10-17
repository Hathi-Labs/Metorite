/**
 * POST /api/integrations/github/app/confirm — the admin's click that writes the
 * org's GitHub App row (WS-54 GH-3).
 *
 * Spec: `project-docs/specs/github_integration.md` §7.3 step 10. The gateway
 * callback writes NOTHING. It keeps the installation in the pending connect,
 * and the page asks "Connect <account> to <org>?". This route sends the
 * admin's yes.
 *
 * ⚠️ **Same origin only, and that is the point of the step.** A forged
 * callback URL that a member opens would otherwise bind an installation to
 * the member's org with no act of the member. A POST that another site sends
 * carries `Sec-Fetch-Site: cross-site`, and gets 403 here before anything goes
 * upstream. No body goes upstream: the gateway reads the pending connect of
 * the session's member in the session's org.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const CONFIRM_TIMEOUT_MS = 20_000;
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
    // ⚠️ ONE attempt: the pending connect is taken once.
    res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/github/app/confirm`,
      {
        method: "POST",
        headers: await gatewayHeaders(),
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(CONFIRM_TIMEOUT_MS),
      },
      { retry: false },
    );
  } catch {
    return refuse("unreachable", 502);
  }
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string" && CODE.test(detail)) return refuse(detail, res.status);
    return refuse(res.status === 404 ? "not_available" : "failed", res.status >= 400 ? res.status : 502);
  }
  const b = (body ?? {}) as Record<string, unknown>;
  return NextResponse.json(
    {
      status: b.status === "connected" ? "connected" : "failed",
      account_login: typeof b.account_login === "string" ? b.account_login : "",
      account_type: typeof b.account_type === "string" ? b.account_type : "",
      repository_selection: typeof b.repository_selection === "string" ? b.repository_selection : "",
    },
    { headers: NO_STORE },
  );
}
