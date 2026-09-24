/**
 * GET /api/agent/workspace/[sessionId]/file?path=<rel_path>
 * Proxy raw file bytes from the gateway workspace API.
 * The Content-Type and Content-Disposition headers from the gateway are passed through.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const filePath = req.nextUrl.searchParams.get("path");
    if (!filePath) {
      return NextResponse.json({ error: "Missing ?path= query parameter" }, { status: 400 });
    }

    const upstream = new URL(`${GATEWAY_URL}/agent/workspace/${sessionId}/file`);
    upstream.searchParams.set("path", filePath);
    // WS-27bm S8: `format=pdf` asks the gateway to convert a Markdown or HTML
    // file. The gateway owns the check, and answers any other value with 422.
    const format = req.nextUrl.searchParams.get("format");
    if (format) upstream.searchParams.set("format", format);

    const res = await fetch(upstream.toString(), {
      headers: await gatewayHeaders(),
      // No timeout here — large files can take a moment to stream
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    // Stream the response body through, preserving Content-Type + Content-Disposition
    const contentType = res.headers.get("content-type") ?? "application/octet-stream";
    const contentDisposition = res.headers.get("content-disposition") ?? "";
    const contentLength = res.headers.get("content-length");

    const headers: Record<string, string> = {
      "Content-Type": contentType,
    };
    if (contentDisposition) headers["Content-Disposition"] = contentDisposition;
    if (contentLength) headers["Content-Length"] = contentLength;

    return new NextResponse(res.body, { status: 200, headers });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const filePath = req.nextUrl.searchParams.get("path");
    if (!filePath) {
      return NextResponse.json({ error: "Missing ?path= query parameter" }, { status: 400 });
    }

    const body = await req.json();
    const upstream = new URL(`${GATEWAY_URL}/agent/workspace/${sessionId}/file`);
    upstream.searchParams.set("path", filePath);

    const res = await fetch(upstream.toString(), {
      method: "PUT",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const filePath = req.nextUrl.searchParams.get("path");
    if (!filePath) {
      return NextResponse.json({ error: "Missing ?path= query parameter" }, { status: 400 });
    }

    const upstream = new URL(`${GATEWAY_URL}/agent/workspace/${sessionId}/file`);
    upstream.searchParams.set("path", filePath);

    const res = await fetch(upstream.toString(), {
      method: "DELETE",
      headers: await gatewayHeaders(),
      signal: AbortSignal.timeout(10_000),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
