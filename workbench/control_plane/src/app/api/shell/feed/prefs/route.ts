/**
 * GET and PUT /api/shell/feed/prefs — My Day's feed preferences (WS-58 MD-2d).
 *
 * Proxies the gateway's `/shell/feed/prefs` (`my_day_feed.md` §13.3). The
 * stored object holds three keys: `mutes`, `open_in_app` and `corrections`.
 * A PUT sends `{key, value}`, and the gateway merges that one key. No surface
 * calls these routes yet. MD-2b, MD-6b, MD-8 and MD-9 are the readers.
 *
 * ⚠️ A failure is an error, never an empty object. `{}` means "the member
 * set no preference", so a database fault must not read as "no mutes". An
 * unreachable gateway answers 502.
 *
 * The identity is the session's. The body names no member and no tenant
 * (R11), and the gateway refuses a key it does not know.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/**
 * Ten correction lines of 300 characters are the largest value, about 3 KB.
 * The cap stops a body the gateway would refuse anyway.
 */
const MAX_BODY = 8_192;

async function relay(res: Response): Promise<NextResponse> {
  if (res.ok) return NextResponse.json(await res.json());
  // A refusal keeps its status, so the client can tell "bad value" from
  // "not reachable".
  return NextResponse.json({ error: "prefs_unavailable" }, { status: res.status });
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/feed/prefs`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "prefs_unavailable" }, { status: 502 });
  }
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  if (body.length > MAX_BODY) {
    return NextResponse.json({ error: "prefs_too_large" }, { status: 413 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/feed/prefs`, {
      method: "PUT",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      // An empty body goes on as null, and the gateway refuses it with 422.
      body: body || "null",
      cache: "no-store",
      signal: AbortSignal.timeout(6_000),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "prefs_unavailable" }, { status: 502 });
  }
}
