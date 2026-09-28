/**
 * POST /api/workflows/hooks/[token] — inbound workflow webhook, verbatim.
 *
 * This route exists to be WRONG in none of the ways the generic /api/workflows
 * proxy is wrong for an external caller. That proxy does
 * `JSON.stringify(await req.json())`, which:
 *
 *   - changes the bytes, so an HMAC the sender computed over its own body can
 *     never verify against what the gateway hashes, and
 *   - throws away anything that is not JSON — form-encoded and XML webhooks
 *     arrive as `{}`.
 *
 * It also attaches the internal bearer token, which would turn a public,
 * token-addressed endpoint into a privileged one. So this route forwards the
 * raw body and the caller's own headers, and adds NO platform credentials:
 * the hook token in the path is the entire credential, exactly as the gateway
 * route (`routes/workflows/hooks.py`, listed in PUBLIC_ROUTES) expects.
 *
 * External systems should be given the GATEWAY origin directly
 * (`public_api_base_url` + /workflows/hooks/{token}) — that is what the editor
 * shows. This proxy is the safety net for a URL someone copied off the
 * control plane's address bar instead.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Headers a webhook sender legitimately needs us to preserve. */
const FORWARDED = [
  "content-type",
  "x-cc-signature",
  "x-hub-signature-256",
  "user-agent",
];

export async function POST(
  req: NextRequest,
  ctx: { params: Promise<{ token: string }> }
): Promise<NextResponse> {
  const { token } = await ctx.params;
  // Raw bytes, never parsed: signature verification is byte-exact.
  const body = await req.arrayBuffer();

  const headers: Record<string, string> = {};
  for (const name of FORWARDED) {
    const value = req.headers.get(name);
    if (value) headers[name] = value;
  }

  try {
    const upstream = await gatewayFetch(
      `${GATEWAY_URL}/workflows/hooks/${encodeURIComponent(token)}`,
      {
        method: "POST",
        headers,
        body,
        signal: AbortSignal.timeout(30_000),
      }
    );
    const payload = await upstream.json().catch(() => ({}));
    return NextResponse.json(payload, { status: upstream.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
