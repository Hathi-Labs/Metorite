/**
 * POST /api/integrations/discover
 *
 * Proxies to POST /integrations/discover on the FastAPI gateway.
 * Body: { query: string }
 * Returns: { ok: true, results: DiscoveredDef[], query: string, web_enhanced: boolean }
 * On failure: { error: string }, plain copy for the member.
 *
 * ⚠️ The raw gateway body is never shown (task 3007). It once carried a
 * vendor's 401 JSON into the Add API dialog. The gateway's `detail` is
 * written for the member, so the route passes that through.
 */

import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { describeFailure, detailText, readJsonBody } from "@/lib/apiError";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/integrations/discover`, {
      method: "POST",
      headers: await gatewayHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(45_000), // LLM can take up to 30s
    });
    if (res.ok) {
      return NextResponse.json(await res.json());
    }
    const text = await res.text().catch(() => "");
    const { value } = readJsonBody(text);
    return NextResponse.json(
      { error: detailText(value?.detail) || describeFailure(res.status, text) },
      { status: res.status }
    );
  } catch (err) {
    const timedOut = err instanceof Error && err.name === "TimeoutError";
    return NextResponse.json(
      {
        error: timedOut
          ? "AI discovery took too long. Try again in a minute."
          : describeFailure(502),
      },
      { status: timedOut ? 504 : 502 }
    );
  }
}
