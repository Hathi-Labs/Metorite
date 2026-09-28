/**
 * POST /api/agent/workspace/[sessionId]/upload
 * Proxy multipart file upload to the gateway workspace API.
 * Accepts one or more files as multipart/form-data.
 * Returns JSON array of FileEntry objects.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;

    // Forward the multipart body to the gateway
    const upstream = `${GATEWAY_URL}/agent/workspace/${sessionId}/upload`;
    const formData = await req.formData();

    const upstreamHeaders = await gatewayHeaders();
    // Don't set Content-Type — fetch will set it with boundary for multipart

    const res = await gatewayFetch(upstream, {
      method: "POST",
      headers: upstreamHeaders,
      body: formData,
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json(
        { error: err },
        { status: res.status }
      );
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: String(err) },
      { status: 500 }
    );
  }
}
