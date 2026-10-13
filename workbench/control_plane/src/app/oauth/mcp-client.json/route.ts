/**
 * GET /oauth/mcp-client.json — Metorite's OAuth client metadata document
 * (CIMD) for MCP servers (WS-54 IN-8a, D98).
 *
 * Spec: `project-docs/specs/integrations_console.md` §3.6.3. An auth server
 * that supports CIMD (Notion, Linear) fetches this URL with no session, and
 * the URL itself is the `client_id`. So the path is PUBLIC (`proxy.ts`
 * PUBLIC_PAGES), exactly this one path.
 *
 * The gateway builds the document (`/integrations/mcp/oauth/client-metadata`)
 * from its own public origin, so staging serves a document of its own. The
 * document is the same for every caller and names nobody, so this route asks
 * with the service bearer. With `MCP_OAUTH_ENABLED` off the gateway answers
 * 404, and so does this route.
 *
 * Only a JSON object goes out. Any other answer becomes a 404, so a gateway
 * fault never shows an auth server a page that is not the document.
 *
 * Fence: `route.test.ts` and `tests/unit/test_mcp_oauth_cimd.py`.
 */
import { NextResponse } from "next/server";
import { GATEWAY_URL, gatewayFetch, serviceHeaders } from "@/lib/gateway";

export const dynamic = "force-dynamic";

const TIMEOUT_MS = 5_000;

function notFound(): NextResponse {
  return NextResponse.json({ error: "Not Found" }, { status: 404 });
}

export async function GET(): Promise<NextResponse> {
  let res: Response;
  try {
    res = await gatewayFetch(
      `${GATEWAY_URL}/integrations/mcp/oauth/client-metadata`,
      {
        headers: serviceHeaders(
          "the CIMD document is the same public JSON for every caller and names nobody",
        ),
        cache: "no-store",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      },
      { retry: false },
    );
  } catch {
    return notFound();
  }
  if (res.status !== 200) return notFound();
  let doc: unknown;
  try {
    doc = await res.json();
  } catch {
    return notFound();
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return notFound();
  return NextResponse.json(doc, {
    headers: { "Cache-Control": "public, max-age=300" },
  });
}
