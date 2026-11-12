/**
 * GET /api/agent-kit/<client>.zip — the zip of one client (KIT-4b).
 *
 * Relays the gateway's `GET /agent-kit/<client>.zip` as bytes, with its
 * `Content-Disposition`. The client id must be one of the six, so no other
 * path reaches the gateway. The session cookie rides along on the download.
 */
import { NextResponse, type NextRequest } from "next/server";
import { refuseUnsafePath } from "@/lib/gatewayPath";
import { GATEWAY_URL, gatewayFetch, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const FILE = /^(claude|codex|copilot|gemini|antigravity|generic)\.zip$/;

export async function GET(
  req: NextRequest,
  ctx: { params: Promise<{ file: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  const { file } = await ctx.params;
  const refused = refuseUnsafePath([file]);
  if (refused) return refused;
  if (!FILE.test(file)) return NextResponse.json({ error: "agent_kit_unavailable" }, { status: 404 });
  try {
    const res = await gatewayFetch(`${GATEWAY_URL}/agent-kit/${file}`, {
      headers: await gatewayHeaders(),
      cache: "no-store",
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(15_000)]),
    });
    if (!res.ok) return NextResponse.json({ error: "agent_kit_unavailable" }, { status: res.status });
    return new NextResponse(await res.arrayBuffer(), {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": res.headers.get("content-disposition") ?? `attachment; filename="${file}"`,
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json({ error: "agent_kit_unavailable" }, { status: 502 });
  }
}
