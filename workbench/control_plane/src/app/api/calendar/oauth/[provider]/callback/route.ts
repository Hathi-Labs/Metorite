/**
 * GET /api/calendar/oauth/{google|microsoft}/callback — finish the calendar
 * consent (WS-CAL CE-10, `calendar_events_reminders.md` §8.1).
 *
 * The owner registers this URL in the Google and Microsoft consoles (task
 * OWN-CAL). The provider sends the browser here with `code` and `state`. The
 * gateway callback (`/tasks/calendar/connections/{google|outlook}/callback`)
 * needs the session of the member who started the flow, and it answers with a
 * 302 to `/calendar?calendar_connected=<provider>` or
 * `/calendar?calendar_error=<code>`.
 *
 * The generic proxy at `/api/tasks/[...path]` cannot serve this URL: it
 * follows redirects, and its GET may retry the single-use code. This file is a
 * clone of `app/api/knowledge/sources/google_drive/callback/route.ts`:
 *
 * - `requireIdentity()` first, then `gatewayHeaders()`, never the service
 *   bearer. The gateway refuses unless the member in the state is the member
 *   of the session.
 * - `redirect: "manual"`, ONE attempt, so the code is never sent twice.
 * - Only three query names go upstream, and this file never logs the query.
 * - Every Location is RELATIVE and names `/calendar`.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const FORWARDED_PARAMS = ["code", "state", "error"] as const;

/** The path word of the provider console -> the word of the gateway route. */
const PROVIDERS: Record<string, string> = { google: "google", microsoft: "outlook" };

/** The JSON refusal codes of the gateway callback that the page may name. */
const KNOWN_ERRORS = new Set([
  "invalid_state",
  "google_not_configured",
  "outlook_not_configured",
]);

const CALLBACK_TIMEOUT_MS = 30_000;

function relativeRedirect(location: string): NextResponse {
  return new NextResponse(null, { status: 302, headers: { location } });
}

function failed(code: string): NextResponse {
  const safe = KNOWN_ERRORS.has(code) ? code : "failed";
  return relativeRedirect(`/calendar?calendar_error=${safe}`);
}

/** Is this one of the two Location shapes the gateway callback sends? */
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
  if (name === "calendar_error" && /^[a-z0-9_]{1,64}$/.test(value)) {
    return `/calendar?calendar_error=${value}`;
  }
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
  ctx: { params: Promise<{ provider: string }> },
): Promise<NextResponse> {
  const { provider } = await ctx.params;
  const word = Object.hasOwn(PROVIDERS, provider) ? PROVIDERS[provider] : null;
  if (!word) return new NextResponse(null, { status: 404 });

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
    `${GATEWAY_URL}/tasks/calendar/connections/${word}/callback` + (qs ? `?${qs}` : "");

  let res: Response;
  try {
    // ONE attempt: the consent code is single-use.
    res = await gatewayFetch(
      upstream,
      {
        headers,
        redirect: "manual",
        cache: "no-store",
        signal: AbortSignal.timeout(CALLBACK_TIMEOUT_MS),
      },
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
