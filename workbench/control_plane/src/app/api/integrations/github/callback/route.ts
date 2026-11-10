/**
 * GET /api/integrations/github/callback — the callback URL of the Metorite
 * GitHub App (WS-54 GH-3).
 *
 * Spec: `project-docs/specs/github_integration.md` §7.2 and §7.3. GitHub
 * sends the admin's browser here after the install, with `code`,
 * `installation_id`, `setup_action` and, when GitHub keeps it, `state`. The
 * gateway callback (`/integrations/github/app/callback`) needs the session of
 * the admin who started the connect. It answers a 302 to
 * `/integrations?view=connections&github=<code>`, with `&account=<login>` for
 * the confirm step. With `GITHUB_APP_ENABLED` off it answers 404, and this
 * route sends the generic failure.
 *
 * The rules of the MCP and Zoho callbacks:
 *
 * - `requireIdentity()` first, then `gatewayHeaders()`, never the service
 *   bearer.
 * - `redirect: "manual"`, and ONE attempt, because the code is single-use.
 * - Only four query names go upstream. The code is a credential for a short
 *   time. This file never logs the query.
 * - Every Location is RELATIVE and names the Connections view with one
 *   `github` code from `GITHUB_RETURN_CODES`. Any other reply becomes the
 *   generic failure. A 403 (no `integrations:manage`) reads `forbidden`.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { githubReturnPath, parseGitHubReturn } from "@/lib/githubConnect";

export const dynamic = "force-dynamic";

/** The only query parameters that go upstream. */
const FORWARDED_PARAMS = ["code", "installation_id", "setup_action", "state"] as const;

const CALLBACK_TIMEOUT_MS = 45_000;

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

/** The Location when it is one the gateway callback can send, else null. */
function allowedLocation(raw: string): string | null {
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return null;
  let target: URL;
  try {
    target = new URL(raw, "http://placeholder.invalid");
  } catch {
    return null;
  }
  if (target.pathname !== "/integrations") return null;
  const names = [...target.searchParams.keys()].sort();
  const allowed = names.every((n) => n === "view" || n === "github" || n === "account");
  if (!allowed || target.searchParams.get("view") !== "connections") return null;
  const parsed = parseGitHubReturn(target.searchParams);
  return parsed ? githubReturnPath(parsed) : null;
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;

  const forwarded = new URLSearchParams();
  for (const name of FORWARDED_PARAMS) {
    const value = req.nextUrl.searchParams.get(name);
    // A `state` that GitHub sends goes on even when it is empty, so the
    // gateway refuses it (fix round 1, item 9). Only an ABSENT state is the
    // fallback of the pending connect.
    if (value || (name === "state" && value !== null)) forwarded.set(name, value);
  }
  const qs = forwarded.toString();
  const upstream = `${GATEWAY_URL}/integrations/github/app/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    // ⚠️ ONE attempt: the code is single-use, and the pending connect is
    // taken once. A retry would turn a good install into a failure.
    res = await gatewayFetch(
      upstream,
      {
        headers: await gatewayHeaders(),
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(CALLBACK_TIMEOUT_MS),
      },
      { retry: false },
    );
  } catch {
    return relativeRedirect(githubReturnPath({ code: "failed" }));
  }

  if (res.status >= 300 && res.status < 400) {
    const location = res.headers.get("location");
    const safe = location ? allowedLocation(location) : null;
    return relativeRedirect(safe ?? githubReturnPath({ code: "failed" }));
  }
  if (res.status === 403) return relativeRedirect(githubReturnPath({ code: "forbidden" }));
  return relativeRedirect(githubReturnPath({ code: "failed" }));
}
