/**
 * GET /api/calendar/oauth/{google|microsoft}/callback: finish the calendar consent
 * (WS-CAL CE-10c).
 *
 * The owner registers `{workbench}/api/calendar/oauth/{google|microsoft}/callback` as the redirect
 * URI. The provider sends the browser here with `code` and `state` (or `error`). The gateway
 * callback (`/tasks/calendar/connections/{google|outlook}/callback`) needs the session of the
 * member who started the flow. It answers with a 302 to `/calendar?calendar_connected=<provider>`
 * or `/calendar?calendar_error=<code>`. The browser word for Outlook is `microsoft`. The gateway
 * word is `outlook`. This route maps it.
 *
 * Rules, copied from `app/api/knowledge/sources/google_drive/callback`:
 *
 * - `requireIdentity()` first, then `gatewayHeaders()`, never the service bearer.
 * - `redirect: "manual"` and ONE attempt (`{ retry: false }`). The consent code is single-use.
 * - Only three query names go upstream: `code`, `state` and `error`. The route never logs them.
 * - Every Location is RELATIVE and names `/calendar` with one parameter. A gateway reply that does
 *   not fit becomes `/calendar?calendar_error=failed`.
 *
 * Fence: `route.test.ts`.
 */
import { NextRequest, NextResponse } from "next/server";

import { gatewayProviderOf } from "@/app/calendar/lib/calendarIds";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";

export const dynamic = "force-dynamic";

const FORWARDED_PARAMS = ["code", "state", "error"] as const;
const CALLBACK_TIMEOUT_MS = 30_000;
const CODE = /^[a-z0-9_]{1,64}$/;

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

function failed(code: string): NextResponse {
  return relativeRedirect(`/calendar?calendar_error=${CODE.test(code) ? code : "failed"}`);
}

/** The Location shapes the gateway callback sends, or null. */
function allowedLocation(raw: string): string | null {
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  let target: URL;
  try {
    target = new URL(raw, "http://placeholder.invalid");
  } catch {
    return null;
  }
  if (target.host !== "placeholder.invalid" || target.pathname !== "/calendar") return null;
  const names = [...target.searchParams.keys()];
  if (names.length !== 1) return null;
  const [name] = names;
  const value = target.searchParams.get(name) ?? "";
  if (name === "calendar_connected" && (value === "google" || value === "outlook")) {
    return `/calendar?calendar_connected=${value}`;
  }
  if (name === "calendar_error" && CODE.test(value)) return `/calendar?calendar_error=${value}`;
  return null;
}

async function refusalCode(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ provider: string }> },
): Promise<NextResponse> {
  const { provider } = await params;
  const refused = refuseUnsafePath([provider]);
  if (refused) return refused;
  const gatewayWord = gatewayProviderOf(provider);
  if (!gatewayWord) return failed("failed");

  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const headers = await gatewayHeaders();

  const forwarded = new URLSearchParams();
  for (const name of FORWARDED_PARAMS) {
    const value = req.nextUrl.searchParams.get(name);
    if (value) forwarded.set(name, value);
  }
  const qs = forwarded.toString();
  const upstream =
    `${GATEWAY_URL}/tasks/calendar/connections/${gatewayWord}/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    res = await gatewayFetch(
      upstream,
      { headers, redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(CALLBACK_TIMEOUT_MS) },
      { retry: false },
    );
  } catch {
    return failed("failed");
  }

  if (res.status >= 300 && res.status < 400) {
    const location = res.headers.get("location");
    const safe = location ? allowedLocation(location) : null;
    return safe ? relativeRedirect(safe) : failed("failed");
  }
  return failed(await refusalCode(res));
}
