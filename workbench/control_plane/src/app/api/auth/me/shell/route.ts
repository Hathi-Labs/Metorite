/**
 * GET and PUT /api/auth/me/shell — the member's shell layout (NS-7).
 *
 * Proxies the gateway's `/auth/me/shell` (`navigation_shell.md` §8.2). The
 * sidebar's "My apps", the star in All apps, My Day's card order, the command
 * bar's job order and the first sign-in question all read it.
 *
 * ⚠️ A failure is an error, never an empty layout. An all-null answer means
 * "never asked", and the shell then asks the first sign-in question. So an
 * unreachable gateway answers 502, and the shell keeps the role's preset and
 * asks nothing.
 *
 * The identity is the session's. The body names no member and no tenant
 * (R11), and the gateway refuses a key it does not know.
 */
import { NextResponse, type NextRequest } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

/** A layout is a few hundred bytes. The cap stops a body the gateway would refuse anyway. */
const MAX_BODY = 8_192;

async function relay(res: Response): Promise<NextResponse> {
  if (res.ok) return NextResponse.json(await res.json());
  // A refusal keeps its status, so the client can tell "bad layout" from
  // "not reachable".
  return NextResponse.json({ error: "shell_unavailable" }, { status: res.status });
}

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/shell`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "shell_unavailable" }, { status: 502 });
  }
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const body = await req.text();
  if (body.length > MAX_BODY) {
    return NextResponse.json({ error: "layout_too_large" }, { status: 413 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/auth/me/shell`, {
      method: "PUT",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: body || "null",
      cache: "no-store",
      signal: AbortSignal.timeout(6_000),
    });
    return await relay(res);
  } catch {
    return NextResponse.json({ error: "shell_unavailable" }, { status: 502 });
  }
}
