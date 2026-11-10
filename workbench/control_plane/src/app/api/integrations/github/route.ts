/**
 * DELETE /api/integrations/github — disconnect GitHub for the org (WS-54 GH-3).
 *
 * Spec: `project-docs/specs/github_integration.md` §7.5. The gateway route
 * (`DELETE /integrations/github`) needs `integrations:manage`. For a Metorite
 * App row it does NOT uninstall the App at GitHub (GH-E10). It answers the
 * link of the installation page, and this route passes that link on only when
 * it is an installation page on `https://github.com`.
 *
 * Same origin only: a DELETE that another site sends gets 403. No body goes
 * upstream. Fence: `../github.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { sameOrigin } from "@/lib/accountsServer";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { safeInstallationUrl } from "@/lib/githubConnect";

export const dynamic = "force-dynamic";

const NO_STORE = { "Cache-Control": "no-store" };

export async function DELETE(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  if (!sameOrigin(req)) {
    return NextResponse.json({ detail: "cross_origin" }, { status: 403, headers: NO_STORE });
  }
  let res: Response;
  try {
    res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/github`,
      {
        method: "DELETE",
        headers: await gatewayHeaders(),
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(15_000),
      },
      { retry: false },
    );
  } catch {
    return NextResponse.json({ detail: "unreachable" }, { status: 502, headers: NO_STORE });
  }
  const body = ((await res.json().catch(() => null)) ?? {}) as Record<string, unknown>;
  if (!res.ok) {
    return NextResponse.json({ detail: "failed" }, { status: res.status >= 400 ? res.status : 502, headers: NO_STORE });
  }
  return NextResponse.json(
    {
      disconnected: body.disconnected === true,
      installation_url: safeInstallationUrl(body.installation_url),
    },
    { headers: NO_STORE },
  );
}
