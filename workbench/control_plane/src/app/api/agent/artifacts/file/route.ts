/**
 * GET /api/agent/artifacts/file?agent=<name>&path=<rel_path>
 * PUT /api/agent/artifacts/file?agent=<name>&path=<rel_path>
 * Proxy for the global artifact browser — read and write individual files.
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const agent = req.nextUrl.searchParams.get("agent");
    const filePath = req.nextUrl.searchParams.get("path");
    if (!agent || !filePath) {
      return NextResponse.json(
        { error: "Missing ?agent= and ?path= query parameters" },
        { status: 400 }
      );
    }

    const upstream = new URL(`${GATEWAY_URL}/agent/artifacts/file`);
    upstream.searchParams.set("agent", agent);
    upstream.searchParams.set("path", filePath);

    const res = await gatewayFetch(upstream.toString(), {
      headers: await gatewayHeaders(),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    const contentType = res.headers.get("content-type") ?? "application/octet-stream";
    const contentDisposition = res.headers.get("content-disposition") ?? "";
    const contentLength = res.headers.get("content-length");

    const headers: Record<string, string> = { "Content-Type": contentType };
    if (contentDisposition) headers["Content-Disposition"] = contentDisposition;
    if (contentLength) headers["Content-Length"] = contentLength;

    return new NextResponse(res.body, { status: 200, headers });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}

export async function PUT(req: NextRequest): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const agent = req.nextUrl.searchParams.get("agent");
    const filePath = req.nextUrl.searchParams.get("path");
    if (!agent || !filePath) {
      return NextResponse.json(
        { error: "Missing ?agent= and ?path= query parameters" },
        { status: 400 }
      );
    }

    const body = await req.json();

    const upstream = new URL(`${GATEWAY_URL}/agent/artifacts/file`);
    upstream.searchParams.set("agent", agent);
    upstream.searchParams.set("path", filePath);

    const res = await gatewayFetch(upstream.toString(), {
      method: "PUT",
      headers: {
        ...(await gatewayHeaders()),
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.text();
      return NextResponse.json({ error: err }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 503 });
  }
}
