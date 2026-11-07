/**
 * GET and PUT /api/auth/me/appearance — the member's mode, density and accent
 * (PKG-20, `package_host.md` §9.5).
 *
 * Proxies the gateway's `/auth/me/appearance`. `lib/theme/syncAppearance.ts`
 * is the one client. It reads the server copy once after the first paint, and
 * writes it only on a member's change.
 *
 * ⚠️ A failure is an error, never an all-null record. An all-null answer means
 * "no server copy", and the browser then keeps what it shows. So an
 * unreachable gateway answers 502, and the browser changes nothing.
 *
 * The identity is the session's. The body names no member and no tenant
 * (R11), and the gateway refuses a key it does not know.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** The record is under 200 bytes. The cap stops a body the gateway would refuse anyway. */
const MAX_BODY = 1_024;

async function relay(res: Response): Promise<NextResponse> {
  if (res.ok) return NextResponse.json(await res.json());
  // A refusal keeps its status, so the client can tell "bad value" from
  // "not reachable".
  return NextResponse.json({ error: "appearance_unavailable" }, { status: res.status });
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/appearance`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "appearance_unavailable" }, { status: 502 });
  }
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  if (body.length > MAX_BODY) {
    return NextResponse.json({ error: "appearance_too_large" }, { status: 413 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/appearance`, {
      method: "PUT",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: body || "null",
      cache: "no-store",
      signal: AbortSignal.timeout(6_000),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "appearance_unavailable" }, { status: 502 });
  }
}
