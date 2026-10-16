/**
 * GET /api/agent/workspace/[sessionId]/file?path=<rel_path>
 * Proxy raw file bytes from the gateway workspace API.
 * The Content-Type and Content-Disposition headers from the gateway are passed through.
 *
 * One exception (owner report, 2026-10-16). A member who opens the address of
 * a Markdown file in a tab gets a 303 to its rendered view in the Artifacts
 * app, never the raw text. `rendersAsPage` in `lib/artifactKind.ts` decides.
 * A viewer's fetch, an image and a Download button (`&download=1`) still get
 * the raw bytes. An HTML file keeps its old answer (HANDOFF H-238).
 */
import { NextRequest, NextResponse } from "next/server";
import { GATEWAY_URL, gatewayHeaders, requireIdentity, gatewayFetch } from "@/lib/gateway";
import { refuseUnsafePath } from "@/lib/gatewayPath";
import { artifactViewUrl, rendersAsPage } from "@/lib/artifactKind";

export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const me = await requireIdentity();
  if (me instanceof NextResponse) return me;
  try {
    const { sessionId } = await params;
    const refused = refuseUnsafePath([sessionId]);
    if (refused) return refused;
    const filePath = req.nextUrl.searchParams.get("path");
    if (!filePath) {
      return NextResponse.json({ error: "Missing ?path= query parameter" }, { status: 400 });
    }

    // WS-27bm S8: `format=pdf` asks the gateway to convert a Markdown or HTML
    // file. The gateway owns the check, and answers any other value with 422.
    const format = req.nextUrl.searchParams.get("format");
    const page = rendersAsPage(filePath, {
      dest: req.headers.get("sec-fetch-dest"),
      accept: req.headers.get("accept"),
      download: req.nextUrl.searchParams.has("download"),
      format,
    });
    if (page) {
      // A relative Location: the app may sit behind a proxy, so the origin
      // this server sees is not the one the member typed. The view fetches
      // the file through this route again, so every check still runs.
      return new NextResponse(null, {
        status: 303,
        headers: { Location: artifactViewUrl(sessionId, filePath), "Cache-Control": "no-store" },
      });
    }

    const upstream = new URL(`${GATEWAY_URL}/agent/workspace/${sessionId}/file`);
    upstream.searchParams.set("path", filePath);
    if (format) upstream.searchParams.set("format", format);

    const res = await gatewayFetch(upstream.toString(), {
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
      // The same address answers a tab with the rendered view, so a cache
      // must not give a tab these raw bytes (`rendersAsPage`).
      Vary: "Sec-Fetch-Dest, Accept",
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
    const refused = refuseUnsafePath([sessionId]);
    if (refused) return refused;
    const filePath = req.nextUrl.searchParams.get("path");
    if (!filePath) {
      return NextResponse.json({ error: "Missing ?path= query parameter" }, { status: 400 });
    }

    const body = await req.json();
    const upstream = new URL(`${GATEWAY_URL}/agent/workspace/${sessionId}/file`);
    upstream.searchParams.set("path", filePath);

    const res = await gatewayFetch(upstream.toString(), {
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
    const refused = refuseUnsafePath([sessionId]);
    if (refused) return refused;
    const filePath = req.nextUrl.searchParams.get("path");
    if (!filePath) {
      return NextResponse.json({ error: "Missing ?path= query parameter" }, { status: 400 });
    }

    const upstream = new URL(`${GATEWAY_URL}/agent/workspace/${sessionId}/file`);
    upstream.searchParams.set("path", filePath);

    const res = await gatewayFetch(upstream.toString(), {
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
