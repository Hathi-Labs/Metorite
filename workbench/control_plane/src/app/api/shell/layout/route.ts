/**
 * GET and PUT /api/shell/layout — the org default of the shell layout (PKG-17b, PKG-17c-1).
 *
 * Proxies the gateway's `GET /shell/layout` (`package_host.md` §9.1). The
 * gateway answers 404 for an organization outside `PACKAGE_HOST_ORGS`, and
 * this route passes that status on. The workbench reads any non-200 as "no
 * org default".
 *
 * The PUT passes the JSON body on as it is, and passes the gateway's status and
 * body back. The gateway checks the admin right and the shape (422 with a reason).
 *
 * The identity is the session's. The request carries no tenant and no member.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: Request): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/layout`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    if (res.ok) return NextResponse.json(await res.json());
    return NextResponse.json({ error: "layout_unavailable" }, { status: res.status });
  } catch {
    return NextResponse.json({ error: "layout_unavailable" }, { status: 502 });
  }
}

export async function PUT(req: Request): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/shell/layout`, {
      method: "PUT",
      headers: { ...(await gatewayHeaders()), "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(6_000)]),
    });
    const text = await res.text();
    let data: unknown = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { error: "layout_unavailable" };
    }
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "layout_unavailable" }, { status: 502 });
  }
}
