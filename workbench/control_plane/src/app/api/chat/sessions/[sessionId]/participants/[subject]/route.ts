/**
 * PATCH  /api/chat/sessions/[sessionId]/participants/[subject] — change a room role
 * DELETE /api/chat/sessions/[sessionId]/participants/[subject] — remove someone, or leave
 *
 * `subject` is an email or `group:<slug>`, so it is re-encoded on the way
 * upstream rather than interpolated raw.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string; subject: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId, subject } = await params;
    const body = await req.json();
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${sessionId}/participants/${encodeURIComponent(subject)}`,
      {
        method: "PATCH",
        headers: await gatewayHeaders(),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(5_000),
      },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ sessionId: string; subject: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId, subject } = await params;
    const res = await gatewayFetch(
      `${GATEWAY_URL}/chat/sessions/${sessionId}/participants/${encodeURIComponent(subject)}`,
      {
        method: "DELETE",
        headers: await gatewayHeaders(),
        signal: AbortSignal.timeout(5_000),
      },
    );
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
