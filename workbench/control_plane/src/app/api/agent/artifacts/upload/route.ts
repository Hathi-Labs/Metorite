/**
 * POST /api/agent/artifacts/upload?agent=&category=
 * Proxy multipart file upload to the gateway /agent/artifacts/upload — uploads
 * file(s) into an agent's workspace folder (used by the email rule editor to
 * attach files to draft actions). Returns JSON array of FileEntry objects.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const upstream = new URL(`${GATEWAY_URL}/agent/artifacts/upload`);
    const agent = req.nextUrl.searchParams.get("agent");
    const category = req.nextUrl.searchParams.get("category");
    if (agent) upstream.searchParams.set("agent", agent);
    if (category) upstream.searchParams.set("category", category);

    const formData = await req.formData();
    // Don't set Content-Type — fetch sets it with the multipart boundary.
    const res = await gatewayFetch(upstream.toString(), {
      method: "POST",
      headers: await gatewayHeaders(),
      body: formData,
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }
    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
