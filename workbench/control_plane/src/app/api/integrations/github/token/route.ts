/**
 * PUT /api/integrations/github/token — the Advanced path: paste a personal
 * access token (WS-54 GH-7).
 *
 * Spec: `project-docs/specs/github_integration.md` §7.6. The gateway route
 * (`PUT /integrations/github/token`) is the ONE writer of a pasted token. It
 * asks GitHub `GET /user` with the token, refuses a token that GitHub refuses
 * with 422, and stores it as the org's `token` row. It has no BYOK coupling.
 *
 * The rules, each with a case in `route.test.ts`:
 *
 * 1. **Same origin only.** A PUT that another site sends gets 403, and the
 *    token goes nowhere.
 * 2. **Only `{token}` goes upstream**, and `replace_app: true` when the body
 *    holds exactly that (the admin's confirm that the token replaces the App,
 *    fix round 1, item 3). No other field of the body is read, so a body
 *    cannot name an org or a member (R11).
 * 3. **The token never comes back and is never logged.** The answer carries
 *    the status, the login and the scopes only.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };
const MAX_TOKEN_CHARS = 255;

function refuse(detail: string, status: number): NextResponse {
  return NextResponse.json({ detail }, { status, headers: NO_STORE });
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  if (!sameOrigin(req)) return refuse("cross_origin", 403);

  let token: unknown;
  let replaceApp = false;
  try {
    const parsed = (await req.json()) as { token?: unknown; replace_app?: unknown } | null;
    token = parsed?.token;
    replaceApp = parsed?.replace_app === true;
  } catch {
    return refuse("bad_body", 400);
  }
  if (typeof token !== "string" || !token.trim() || token.length > MAX_TOKEN_CHARS) {
    return refuse("That is not a GitHub token.", 422);
  }

  let res: Response;
  try {
    res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/github/token`,
      {
        method: "PUT",
        headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
        body: JSON.stringify(replaceApp ? { token: token.trim(), replace_app: true } : { token: token.trim() }),
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(20_000),
      },
      { retry: false },
    );
  } catch {
    return refuse("GitHub did not answer.", 502);
  }
  const body = ((await res.json().catch(() => null)) ?? {}) as Record<string, unknown>;
  if (!res.ok) {
    const detail = typeof body.detail === "string" && body.detail.length <= 120 ? body.detail : "failed";
    return refuse(detail, res.status >= 400 ? res.status : 502);
  }
  return NextResponse.json(
    {
      status: body.status === "connected" ? "connected" : "failed",
      login: typeof body.login === "string" ? body.login : "",
      scopes: Array.isArray(body.scopes) ? body.scopes.filter((s) => typeof s === "string") : [],
    },
    { headers: NO_STORE },
  );
}
